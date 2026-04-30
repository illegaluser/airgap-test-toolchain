#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# File: dify_sonar_issue_analyzer.py
# Version: 1.2
#
# [Overview]
# This script handles **stage 2 (AI-driven auto diagnosis)** of the quality
# analysis pipeline (Phase 3). Takes the static-analysis issue JSON produced
# by sonar_issue_exporter.py as input, sends each issue to a Dify AI workflow,
# and saves the LLM-generated root-cause analysis / risk assessment / suggested
# fix to JSONL.
#
# [Position in the pipeline]
# sonar_issue_exporter.py (issue collection)
#       ↓ sonar_issues.json
# >>> dify_sonar_issue_analyzer.py (AI analysis) <<<
#       ↓ llm_analysis.jsonl
# gitlab_issue_creator.py (issue registration)
#
# [Core flow]
# 1. Read the issue list from sonar_issues.json.
# 2. For each issue, combine code snippet + rule description + metadata into the
#    Dify workflow input.
# 3. Call the Dify /v1/workflows/run API in blocking mode and receive the LLM
#    analysis result.
# 4. On failure, retry up to 3 times. Successful results are written one line
#    at a time to the JSONL file.
#
# [Run example]
# python3 dify_sonar_issue_analyzer.py \
#   --dify-api-base http://api:5001 \
#   --dify-api-key app-xxxxxxxx \
#   --input sonar_issues.json \
#   --output llm_analysis.jsonl
# ==================================================================================

import argparse
import json
import os
import sys
import time
import uuid
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Line-buffer stdout/stderr so the Jenkins console shows real-time progress.
# Without -u (PYTHONUNBUFFERED), progress lines like
# `[DEBUG] >>> Sending Issue ...` would appear in chunks because Python's
# default is block-buffered in pipe mode — leading to confusion ("seems
# stuck for a long time"). Line buffering reflects each line to the
# console immediately.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def truncate_text(text, max_chars=1000):
    """
    Truncate text to the specified maximum number of characters.

    If the rule description sent to the Dify workflow is too long, it pushes
    the code snippet out of the context window and the LLM cannot reference
    the code. We apply the length cap to rule descriptions only.

    Note: a previous HTML cleanup helper was removed because it caused data loss.

    Args:
        text: original text
        max_chars: max characters allowed (default 1000)

    Returns:
        Truncated text (with "... (Rule Truncated)" suffix when truncated)
    """
    if not text: return ""
    if len(text) <= max_chars: return text
    return text[:max_chars] + "... (Rule Truncated)"

def hyde_expand(row, ollama_base_url: str, ollama_model: str, timeout: int = 60) -> str:
    """P2 R-3 — Hypothetical Document Embedding (lite).

    Convert the Sonar issue into a one-sentence natural-language search
    query and add it as an auxiliary line to kb_query. This boosts retrieval
    recall in cases where dense matching in the embedding space was weak
    (rules where code identifiers alone barely express semantics).

    Because of the call cost (~30-60s for gemma4:e4b), we do not call it on
    every general case — only on the analyzer's final retry (attempt=2).
    On call failure it returns an empty string and kb_query keeps working
    as usual.
    """
    if not ollama_base_url:
        return ""
    rule_detail = row.get("rule_detail", {}) or {}
    prompt = (
        "Convert the Sonar static-analysis issue into a one-sentence natural-language search query. "
        "Keep code identifiers as-is, focus on behavior/intent, within 50 chars. No other explanation.\n\n"
        f"Rule: {row.get('sonar_rule_key','')} - {rule_detail.get('name','')}\n"
        f"Function: {row.get('enclosing_function','')}\n"
        f"File: {row.get('relative_path','')}\n"
        f"Message: {row.get('sonar_message','')}\n\n"
        "Answer:"
    )
    try:
        import urllib.request
        body = json.dumps({
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 80},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{ollama_base_url.rstrip('/')}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            text = (data.get("response") or "").strip()
            # First line + 300 char cap
            return text.splitlines()[0].strip()[:300] if text else ""
    except Exception as e:
        print(f"   [HyDE WARN] Ollama call failed: {e}", file=sys.stderr)
        return ""


def build_kb_query(row, attempt: int = 0, hyde_text: str = ""):
    """P1 + Phase B F1 — build a structured multi-query kb_query.

    P2 R-4: per-attempt variation — to make the existing 3-time retry loop
    (which used to send the same query) more effective, send a differently
    shaped query each time. Diversifying the search signal against the same
    KB raises cumulative recall above what a single query achieves, in line
    with the multi-query retrieval intuition.

    Phase B F1: append the issue function's tree-sitter metadata
    (endpoint / decorators / doc_params) to the attempt=0 and attempt=2
    queries. This widens the search surface so other handlers on the same
    route, common decorator patterns like @require_role, and caller chunks
    sharing parameter names match in both BM25 and dense.

    attempt=0 (default — full structured):
      1) Code window near the issue line (3-4 lines around the `>>` marker)
      2) function: enclosing_function
      3) callees: enclosing_function   — guides toward caller definition chunks
      4) test_for: enclosing_function  — guides toward test chunks
      5) is_test: true                 — generic match for test chunks
      6) path: relative_path
      7) rule name
      8) [F1] endpoint: <method path>  — other handlers on the same route
      9) [F1] decorators: ...          — same decorator pattern
      10) [F1] params: ...             — callers sharing the same parameter names

    attempt=1 (natural-language oriented):
      1) rule name + short sonar_message — emphasizes semantic match
      2) function: enclosing_function
      3) path: relative_path

    attempt=2 (identifier oriented):
      1) enclosing_function only — exact-symbol BM25
      2) callees: enclosing_function
      3) callers: enclosing_function
      4) [F1] endpoint: <method path>  — fallback route match for symbol matching
    """
    snippet = row.get("code_snippet", "") or ""
    lines = snippet.splitlines()
    marker_idx = 0
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(">>"):
            marker_idx = i
            break
    window = "\n".join(lines[max(0, marker_idx - 3): marker_idx + 4]) if lines else ""

    enclosing = row.get("enclosing_function", "") or ""
    rel_path = row.get("relative_path", "") or ""
    rule_detail = row.get("rule_detail", {}) or {}
    rule_name = rule_detail.get("name") or row.get("sonar_rule_key", "") or ""
    sonar_msg = row.get("sonar_message", "") or ""

    # Phase B F1 — extract tree-sitter metadata (enclosing_* fields populated by the exporter)
    endpoint = (row.get("enclosing_endpoint") or "").strip()
    decorators = row.get("enclosing_decorators") or []
    doc_params = row.get("enclosing_doc_params") or []
    # Identifier only (`@app.route('/x')` → `app.route`); short tokens improve BM25 matching
    dec_tokens = []
    for d in decorators[:5]:
        if not isinstance(d, str):
            continue
        # `@module.func(args)` → `module.func`
        body = d.lstrip("@").split("(", 1)[0].strip()
        if body:
            dec_tokens.append(body)
    # Param names only (drop type/desc) — matches actual call-site argument names in caller code
    param_names = []
    for p in doc_params[:8]:
        if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1]:
            param_names.append(str(p[1]).strip())

    if attempt == 1:
        parts = []
        if rule_name:
            parts.append(rule_name)
        if sonar_msg:
            parts.append(sonar_msg[:300])
        if enclosing:
            parts.append(f"function: {enclosing}")
        if rel_path:
            parts.append(f"path: {rel_path}")
        return "\n".join([p for p in parts if p])

    if attempt == 2:
        parts = []
        if enclosing:
            parts.append(enclosing)
            parts.append(f"callees: {enclosing}")
            parts.append(f"callers: {enclosing}")
        elif rule_name:
            parts.append(rule_name)
        # F1 — endpoint match (keep the route signal alive even in identifier-oriented mode)
        if endpoint:
            parts.append(f"endpoint: {endpoint}")
        # P2 R-3 — HyDE natural-language augmentation (analyzer injects host Ollama call result)
        if hyde_text:
            parts.append(hyde_text)
        return "\n".join([p for p in parts if p])

    # Default (attempt=0) — full structured + P1 natural-language hint + F1 tree-sitter meta
    parts = [window]
    if enclosing:
        parts.append(f"function: {enclosing}")
        parts.append(f"callees: {enclosing}")
        parts.append(f"test_for: {enclosing}")
    parts.append("is_test: true")
    if rel_path:
        parts.append(f"path: {rel_path}")
    if rule_name:
        parts.append(rule_name)
    # F1 — endpoint / decorators / params lines (only when values are present).
    # The KB footer serializes the same key format, so direct BM25 + dense matching both apply.
    if endpoint:
        parts.append(f"endpoint: {endpoint}")
    if dec_tokens:
        parts.append(f"decorators: {' '.join(dec_tokens)}")
    if param_names:
        parts.append(f"params: {' '.join(param_names)}")
    # P1 — bge-m3 dense retrieval has been observed to weakly capture the
    # semantics of metadata-style lines (`callees: X`) (callers bucket fill
    # 10%, tests 0%). Add a natural-language line to widen the embedding
    # match surface for the caller/test categories.
    if enclosing:
        parts.append(
            f"caller route handler controller that calls this function {enclosing}, "
            f"related test spec e2e cypress scenarios"
        )
    return "\n".join([p for p in parts if p])


def format_dependency_tree(item) -> str:
    """Phase E E1 — render the depth-2 caller graph as LLM-friendly text.

    Phase E' (a): include the header (`## Dependency Graph`) too. When empty,
    return an empty string → the corresponding line in the user prompt
    becomes a blank line. Reduces LLM noise.
    """
    direct = item.get("direct_callers") or []
    depth2 = item.get("depth2_callers") or []
    if not direct and not depth2:
        return ""
    lines = ["## Dependency Graph (depth-2)"]
    if direct:
        lines.append("Direct callers (depth 1):")
        for c in direct[:8]:
            lines.append(f"  - {c}")
    if depth2:
        lines.append("Callers of those callers (depth 2):")
        for c in depth2[:5]:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def format_git_history(item) -> str:
    """Phase E E3 — render git history as LLM-friendly text. Includes the header; empty → entire block omitted."""
    parts = []
    git_ctx = (item.get("git_context") or "").strip()
    similar = item.get("git_history_similar") or []
    if not git_ctx and not similar:
        return ""
    parts.append("## Git History")
    if git_ctx:
        parts.append(git_ctx)
    if similar:
        parts.append("Past fix history for the same rule:")
        for s in similar[:3]:
            parts.append(f"  - {s}")
    return "\n".join(parts)


def format_similar_locations(item) -> str:
    """Phase E E5 — other locations of the same rule. Includes header; empty → entire block omitted."""
    locs = item.get("similar_rule_locations") or []
    if not locs:
        return ""
    lines = [
        f"## Other locations of the same rule ({len(locs)} sites with the same pattern)",
    ]
    for loc in locs[:5]:
        lines.append(f"  - {loc.get('relative_path', '?')}:{loc.get('line', '?')}")
    return "\n".join(lines)


def format_project_overview(text: str) -> str:
    """Phase E E2-lite — wrap project_overview with a header. Empty → entire block omitted."""
    if not text or not text.strip():
        return ""
    return "## Project overview (README + dependencies + CONTRIBUTING)\n" + text.strip()


def format_enclosing_meta(item) -> str:
    """Phase B F2b — render the enclosing_* metadata populated by exporter as LLM-friendly multi-line text.

    Goes into the Dify start.enclosing_meta paragraph variable, which is
    rendered as the 'issue function static metadata' section of the LLM
    user prompt. When empty, returns an empty string — the workflow
    template automatically blanks out the corresponding line (jinja
    handles empty paragraphs).

    Format (only lines with values are emitted):
      - decorators: @app.post('/login'), @require_role('user')
      - HTTP route: POST /login
      - parameters: email (str), password (str)
      - returns: User
      - raises: AuthError
      - callees: hash_password, verify_session
      - leading doc: Authenticate user against the local DB.
    """
    lines = []
    decorators = item.get("enclosing_decorators") or []
    if decorators:
        lines.append("- decorators: " + ", ".join(d for d in decorators[:5] if d))
    endpoint = (item.get("enclosing_endpoint") or "").strip()
    if endpoint:
        lines.append(f"- HTTP route: {endpoint}")
    doc_params = item.get("enclosing_doc_params") or []
    if doc_params:
        param_strs = []
        for p in doc_params[:10]:
            if not isinstance(p, (list, tuple)):
                continue
            t = (p[0] or "").strip() if len(p) >= 1 else ""
            n = (p[1] or "").strip() if len(p) >= 2 else ""
            if not n:
                continue
            param_strs.append(f"{n} ({t})" if t else n)
        if param_strs:
            lines.append("- parameters: " + ", ".join(param_strs))
    doc_returns = item.get("enclosing_doc_returns")
    if isinstance(doc_returns, (list, tuple)) and len(doc_returns) >= 2:
        rt, rd = (doc_returns[0] or "").strip(), (doc_returns[1] or "").strip()
        if rt or rd:
            lines.append("- returns: " + (f"{rt} — {rd}" if rt and rd else (rt or rd)))
    doc_throws = item.get("enclosing_doc_throws") or []
    if doc_throws:
        thr_strs = []
        for t in doc_throws[:5]:
            if not isinstance(t, (list, tuple)):
                continue
            ex = (t[1] or t[0] or "").strip() if len(t) >= 2 else ""
            if ex:
                thr_strs.append(ex)
        if thr_strs:
            lines.append("- raises: " + ", ".join(thr_strs))
    callees = item.get("enclosing_callees") or []
    if callees:
        lines.append("- internal callees: " + ", ".join(callees[:8]))
    doc = (item.get("enclosing_doc") or "").strip()
    if doc:
        # 200-char cap already applied for oneline. Helps the LLM understand intent in natural language.
        lines.append(f"- leading doc: {doc}")
    return "\n".join(lines)


# Step C — generate a templated response for skip_llm issues (MINOR/INFO).
# Without calling Dify, the analyzer constructs outputs directly and writes
# them to llm_analysis.jsonl.
def build_skip_llm_outputs(severity: str, msg: str) -> dict:
    return {
        "title": f"[{severity}] {msg}",
        "labels": [
            f"severity:{severity}",
            "classification:true_positive",
            "confidence:low",
            "auto_template:true",
        ],
        "impact_analysis_markdown": (
            "(Auto-template — LLM call skipped due to MINOR/INFO severity. "
            "Manual review recommended.)"
        ),
        "suggested_fix_markdown": "",
        "classification": "true_positive",
        "fp_reason": "",
        "confidence": "low",
        "suggested_diff": "",
    }


# Step C — inject safe defaults for the outputs schema.
# Even when Dify's parameter-extractor fails to pick up new fields, the
# creator side keeps running without KeyError. Empty `classification`
# is treated as true_positive.
def normalize_outputs(outputs: dict) -> dict:
    out = dict(outputs or {})
    defaults = {
        "title": "",
        "labels": [],
        "impact_analysis_markdown": "",
        "suggested_fix_markdown": "",
        "classification": "true_positive",
        "fp_reason": "",
        "confidence": "medium",
        "suggested_diff": "",
    }
    for k, v in defaults.items():
        if k not in out or out[k] is None or out[k] == "":
            # classification only: empty → force-default to true_positive
            if k == "classification":
                out[k] = "true_positive"
            else:
                out[k] = v
    return out


def _compute_tree_sitter_hits(impact_md: str, item: dict, used_items: list) -> dict:
    """Phase C F4 — measure whether the LLM answer actually cites tree-sitter
    static metadata.

    Citation rate (path/symbol matching) alone cannot prove the causality
    "did the AI use the pre-training results". This function counts the
    occurrence of the following 4 signal types in the answer body:

    1. The enclosing function's endpoint URL (`POST /login` → `/login`, `POST` token)
    2. The enclosing function's decorator (identifiers like `@require_role`)
    3. The enclosing function's docstring param names (`email`, `password`, ...)
    4. used_items' endpoint/decorator (i.e. metadata of other RAG-received chunks)

    Returns:
      {
        "endpoint_hits": int,       # endpoint URL or method token matches
        "decorator_hits": int,      # decorator identifier matches
        "param_hits": int,          # parameter name matches
        "rag_meta_hits": int,       # endpoint/decorator matches inside used_items
        "total_hits": int,          # sum of the 4 above (single dashboard metric)
      }

    PM perspective: total_hits=0 means "pre-training signal did not reach
    the answer"; >0 means "static metadata was actually reflected". The
    core input for Stage 4 of the 4-stage diagnostic report.
    """
    impact = impact_md or ""
    if not impact:
        return {"endpoint_hits": 0, "decorator_hits": 0, "param_hits": 0,
                "rag_meta_hits": 0, "total_hits": 0}

    # 1. enclosing endpoint
    endpoint_hits = 0
    enc_ep = (item.get("enclosing_endpoint") or "").strip()
    if enc_ep:
        # "POST /login" → check both tokens. URL path → 1 point, +1 if method present.
        parts = enc_ep.split(maxsplit=1)
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""
        if path and path in impact:
            endpoint_hits += 1
        if method and method in ("GET", "POST", "PUT", "PATCH", "DELETE") and method in impact:
            # Method-only matching is noisy (English word GET is common) → only count when path is also present.
            if path and path in impact:
                endpoint_hits += 1

    # 2. enclosing decorators
    decorator_hits = 0
    enc_decs = item.get("enclosing_decorators") or []
    seen_dec_idents = set()
    for d in enc_decs[:5]:
        if not isinstance(d, str):
            continue
        body = d.lstrip("@").split("(", 1)[0].strip()
        if not body or len(body) < 3:
            continue
        if body in seen_dec_idents:
            continue
        seen_dec_idents.add(body)
        if body in impact:
            decorator_hits += 1

    # 3. enclosing doc_params
    param_hits = 0
    enc_params = item.get("enclosing_doc_params") or []
    seen_pnames = set()
    for p in enc_params[:10]:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        name = (p[1] or "").strip()
        if not name or len(name) < 3 or name in seen_pnames:
            continue
        seen_pnames.add(name)
        # Match only when wrapped in backticks or separated by word boundaries — blocks common-word false positives
        if re.search(rf"[`\b]{re.escape(name)}[`\b]", impact) or f"`{name}`" in impact:
            param_hits += 1

    # 4. used_items' endpoint/decorator — whether the static metadata of other
    #    RAG chunks made it into the answer. context_filter forwards
    #    has_endpoint/decorators_raw.
    rag_meta_hits = 0
    seen_rag = set()
    for it in used_items or []:
        ep = (it.get("endpoint_raw") or "").strip()
        if ep:
            parts = ep.split(maxsplit=1)
            path = parts[1] if len(parts) > 1 else ""
            if path and path in impact and path not in seen_rag:
                rag_meta_hits += 1
                seen_rag.add(path)
        # decorators_raw is a single-line footer string ("@app.route('/x') @auth")
        dec_raw = (it.get("decorators_raw") or "").strip()
        if dec_raw:
            # Take only the first decorator identifier (`@module.func(args)` → `module.func`)
            for token in dec_raw.split():
                token = token.lstrip("@").split("(", 1)[0].strip()
                if len(token) >= 3 and token in impact and token not in seen_rag:
                    rag_meta_hits += 1
                    seen_rag.add(token)
                    break

    total = endpoint_hits + decorator_hits + param_hits + rag_meta_hits
    return {
        "endpoint_hits": endpoint_hits,
        "decorator_hits": decorator_hits,
        "param_hits": param_hits,
        "rag_meta_hits": rag_meta_hits,
        "total_hits": total,
    }


def _compute_citation(impact_md: str, used_items: list) -> dict:
    """P1.5 M-2 — compute which used_items chunks the LLM's
    impact_analysis_markdown actually cites.

    Heuristic (Fix C broadens to 3 tiers + Fix 4 dedup):
      tier 1: full path string match (`src/auth/login.py`)
      tier 2: symbol (function name) match
      tier 3: path basename match (`login.py` alone counts)
              — captures the real-world pattern of LLMs dropping long paths
              and using only the file name.
    `?` or empty values are excluded from matching — blocks false-positive
    citations where bad chunk metadata happens to substring-match real
    content.

    Fix 4 (dedup): with Dify segmentation, multiple segments of the same
    document are retrieved separately, so the same (path, symbol) appears
    repeatedly in used_items. Dedup by (path, symbol) for both cited_count
    and total_used — prevents one citation of the same file from being
    inflated to "two", which would falsely report 100%.

    Returns: {"cited_count": int, "cited_items": [{...}, ...], "total_used": int}
    """
    impact = impact_md or ""

    # Fix 4 — dedup used_items by (path, symbol). Preserves the first occurrence.
    seen = set()
    deduped = []
    for it in (used_items or []):
        path = (it.get("path") or "").strip()
        symbol = (it.get("symbol") or "").strip()
        key = (path, symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    cited = []
    for it in deduped:
        path = (it.get("path") or "").strip()
        symbol = (it.get("symbol") or "").strip()
        # Chunks without a metadata footer (`?::?`) are not match candidates
        if path in ("", "?") and symbol in ("", "?"):
            continue
        matched = False
        if path and path not in ("?",) and path in impact:
            matched = True
        elif symbol and symbol not in ("?",) and symbol in impact:
            matched = True
        elif path and path not in ("?",):
            base = path.rsplit("/", 1)[-1]
            if len(base) >= 5 and base in impact:
                matched = True
        if matched:
            cited.append({
                "bucket": it.get("bucket"),
                "path": path,
                "symbol": symbol,
                "score": it.get("score"),
            })
    # +T2 — citation_depth: count of distinct backtick identifiers in impact_md.
    # We treat backtick-wrapped code-like tokens as a "specificity signal"
    # from the LLM. An answer that backticks several distinct identifiers
    # is judged to have used the RAG context more deeply (heuristic) than
    # one that merely cites once or twice.
    backtick_idents = set(re.findall(r"`([^`\s]+)`", impact))
    # Single-word code-like only — exclude labels like `[MAJOR]`, single non-Latin words, etc.
    backtick_idents = {
        b for b in backtick_idents
        if 2 <= len(b) <= 80 and any(c.isalpha() for c in b) and "[" not in b and "]" not in b
    }

    return {
        "cited_count": len(cited),
        "cited_items": cited,
        "total_used": len(deduped),
        # +T2 — measurement only (no gating). Indexed in the report.
        "citation_depth": len(backtick_idents),
        # P7 — partial citation signal. Analyzer reads this and downgrades confidence.
        "is_partial_citation": (
            len(deduped) >= 2 and (len(cited) / len(deduped)) < 0.5
        ),
    }


def _build_out_row(*, item, key, severity, msg, line, enclosing_fn, enclosing_ln,
                   commit_sha, rule, rule_detail, final_code, outputs, llm_skipped: bool,
                   context_stats: dict = None):
    """Step C — common builder for out_row. Same format for both Dify-success and skip_llm paths.

    Passes through the facts the creator expects + outputs (the normalized
    8 fields) + the clustering/routing fields exporter already injected in
    Step B.

    P1.5 M-1/M-2: includes context_stats (a dict aggregated by
    context_filter and passed up from the workflow) and the citation
    analysis of the LLM answer in out_row. Subsequent
    diagnostic_report_builder.py reads these fields to render the
    per-issue diagnostic report.
    """
    normalized = normalize_outputs(outputs)
    diagnostic = None
    if context_stats is not None:
        used = context_stats.get("used_items") or []
        # Phase E' (b) — when the LLM ignores the E4 review-scope obligation
        # (previous cycle measured 0/10), the analyzer auto-appends it to
        # the end of the answer. Guarantees 100% deterministic coverage.
        # Skip if the LLM answer already has the '🔍 Review' pattern.
        impact_md = normalized.get("impact_analysis_markdown", "") or ""
        if impact_md.strip() and "🔍 Review" not in impact_md:
            buckets = context_stats.get("used_per_bucket") or {}
            cn = buckets.get("callers", 0) or 0
            tn = buckets.get("tests", 0) or 0
            on = buckets.get("others", 0) or 0
            git_hist_n = len(item.get("git_history_similar") or [])
            sim_n = len(item.get("similar_rule_locations") or [])
            depth2_n = len(item.get("depth2_callers") or [])
            scope_line = (
                f"\n\n🔍 Review: callers {cn} · tests {tn} · others {on} · "
                f"depth-2 {depth2_n} · git history {git_hist_n} · similar locations {sim_n}"
            )
            impact_md = impact_md.rstrip() + scope_line
            normalized["impact_analysis_markdown"] = impact_md
        citation = _compute_citation(impact_md, used)
        # Phase C F4 — measure whether tree-sitter metadata is actually reflected in the answer.
        ts_hits = _compute_tree_sitter_hits(impact_md, item, used)
        # P7 — confidence calibration: partial citation downgrades high → medium + adds label.
        # is_partial_citation is set by _compute_citation when (cited/total < 0.5).
        if citation.get("is_partial_citation") and (normalized.get("confidence") or "").lower() == "high":
            normalized["confidence"] = "medium"
            labels = list(normalized.get("labels") or [])
            if "partial_citation" not in labels:
                labels.append("partial_citation")
            normalized["labels"] = labels
        diagnostic = {
            "retrieved_total": context_stats.get("retrieved_total", 0),
            "excluded_self": context_stats.get("excluded_self", 0),
            "kept_total": context_stats.get("kept_total", 0),
            "used_total": context_stats.get("used_total", 0),
            "buckets": context_stats.get("buckets", {}),
            "used_per_bucket": context_stats.get("used_per_bucket", {}),
            "used_items": used,
            "citation": citation,
            # Phase C F4 — core input for the Stage 4 diagnostic report.
            "tree_sitter_hits": ts_hits,
        }
    return {
        "sonar_issue_key": key,
        "severity": severity,
        "sonar_message": msg,
        "sonar_issue_url": item.get("sonar_issue_url", ""),
        # Location info (used by the creator's header renderer)
        "relative_path": item.get("relative_path", "") or "",
        "line": line,
        "enclosing_function": enclosing_fn,
        "enclosing_lines": enclosing_ln,
        # Phase B F2a passthrough — used by the GitLab issue creator
        # (PM-friendly body) for the 'AI judgment basis' section and
        # static metadata display.
        "enclosing_kind": item.get("enclosing_kind", "") or "",
        "enclosing_lang": item.get("enclosing_lang", "") or "",
        "enclosing_decorators": item.get("enclosing_decorators", []) or [],
        "enclosing_endpoint": item.get("enclosing_endpoint", "") or "",
        "enclosing_doc_params": item.get("enclosing_doc_params", []) or [],
        "enclosing_doc_returns": item.get("enclosing_doc_returns"),
        "enclosing_doc_throws": item.get("enclosing_doc_throws", []) or [],
        "enclosing_doc": item.get("enclosing_doc", "") or "",
        "enclosing_callees": item.get("enclosing_callees", []) or [],
        "commit_sha": commit_sha,
        # Rule info (used by the creator's '📖 Rule detail' section)
        "rule_key": rule,
        "rule_name": rule_detail.get("name", ""),
        "rule_description": rule_detail.get("description", ""),
        # Problem code block (used by the creator's '🔴 Problem code' section)
        "code_snippet": final_code,
        # Step B passthrough — creator's Affected Locations section + FP transition routing
        "cluster_key": item.get("cluster_key", ""),
        "affected_locations": item.get("affected_locations", []) or [],
        "direct_callers": item.get("direct_callers", []) or [],
        "git_context": item.get("git_context", "") or "",
        "judge_model": item.get("judge_model", ""),
        "llm_skipped": llm_skipped,
        # LLM-generated — the normalized 8-field outputs
        "outputs": normalized,
        # P1.5 M-1/M-2 — read by diagnostic_report_builder.py to render HTML.
        # On the skip_llm path this is None (template response without a Dify call).
        "rag_diagnostic": diagnostic,
        "generated_at": int(time.time()),
    }


def send_dify_request(url, api_key, payload):
    """
    Send an HTTP POST request to the Dify Workflow API.

    Talks directly from the Jenkins container to the Dify API container; the
    timeout is 5 minutes (300s). LLM inference can be slow, so we need a
    generous timeout.

    Args:
        url: Dify workflow run endpoint (e.g. http://api:5001/v1/workflows/run)
        api_key: Dify app API key (Bearer token)
        payload: workflow input data (dict)

    Returns:
        tuple: (HTTP status code, response body string)
               On a network error returns status 0 and the error message.
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, data=data)
    try:
        with urlopen(req, timeout=300) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def main():
    """
    Main entry point: parse CLI args, walk through SonarQube issues, and request
    analysis via the Dify workflow.

    [Overall flow]
    1. Parse CLI args (Dify connection info, input/output paths, ...)
    2. Load the issue list from sonar_issues.json
    3. For each issue:
       a. Extract code snippet, rule info, metadata and shape them into the
          Dify input format
       b. Call the Dify workflow API (blocking mode, up to 3 retries)
       c. Write the analysis result line by line to the JSONL file on success
    4. Close the result file (llm_analysis.jsonl)
    """
    # ---------------------------------------------------------------
    # [Step 1] Parse CLI args
    # ---------------------------------------------------------------
    parser = argparse.ArgumentParser()
    parser.add_argument("--dify-api-base", required=True)   # Dify API base URL
    parser.add_argument("--dify-api-key", required=True)     # Dify app API key
    parser.add_argument("--input", required=True)            # path to sonar_issues.json
    parser.add_argument("--output", default="llm_analysis.jsonl")  # analysis result output file
    parser.add_argument("--max-issues", type=int, default=0) # max issues to analyze (0 = all)
    parser.add_argument("--user", default="")                # Dify user identifier
    parser.add_argument("--response-mode", default="")       # response mode (unused, kept for compat)
    parser.add_argument("--timeout", type=int, default=0)    # timeout (unused, kept for compat)
    parser.add_argument("--print-first-errors", type=int, default=0)  # cap for error dumps
    # P2 R-3 — HyDE (lite). Only on attempt=2, calls host Ollama for one-line
    # natural-language conversion and adds it to kb_query. When empty,
    # disabled. Zero impact on the general (1st-success) case — only the
    # last retry pays the cost.
    parser.add_argument("--hyde-ollama-base-url", default="",
                        help="Ollama base URL (e.g. http://host.docker.internal:11434). Empty = HyDE off")
    parser.add_argument("--hyde-ollama-model", default="gemma4:e4b",
                        help="Ollama model used for HyDE conversion")
    # Step R new — used to forward commit info that the creator uses for
    # deterministic body rendering. When empty, out_row's commit_sha is also
    # empty (the creator omits the commit section).
    parser.add_argument("--commit-sha", default="")
    args, _ = parser.parse_known_args()

    # ---------------------------------------------------------------
    # [Step 2] Load the input file (sonar_issues.json)
    # Reads the issue list produced by sonar_issue_exporter.py.
    # ---------------------------------------------------------------
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Cannot read input file: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract the issue list and apply the count limit
    issues = data.get("issues", [])
    if args.max_issues > 0: issues = issues[:args.max_issues]

    # Phase E E2-lite — extract the metadata section (project_overview, etc.)
    # written once by the exporter. Attached identically to every LLM call
    # (not per-issue info).
    metadata = data.get("metadata", {}) or {}
    project_overview_text = metadata.get("project_overview", "") or ""
    if project_overview_text:
        print(f"[INFO] project_overview applied: {len(project_overview_text)} chars", file=sys.stderr)

    # Open the JSONL file for results
    # buffering=1 = line-buffered. Each out_fp.write reflects to disk one
    # line at a time without an explicit flush → during long runs Jenkins
    # can read JSONL in real time to track progress. Line buffering is
    # legal in Python text mode.
    out_fp = open(args.output, "w", encoding="utf-8", buffering=1)

    # Build the Dify API endpoint
    # Auto-correct if the user omitted the /v1 suffix.
    base_url = args.dify_api_base.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    target_api_url = f"{base_url}/workflows/run"

    print(f"[INFO] Analyzing {len(issues)} issues...", file=sys.stderr)

    # --print-first-errors: cap detailed empty-output dumps to N at most.
    # 0 = unlimited. On every empty outputs, prints parse_status + llm_text_preview.
    empty_debug_budget = [args.print_first_errors]  # wrapped in a list so the inner scope can mutate

    # ---------------------------------------------------------------
    # [Step 3] Walk through each issue and send it to the Dify workflow
    # ---------------------------------------------------------------
    for item in issues:
        # --- 3-a. Extract issue metadata ---
        key = item.get("sonar_issue_key")           # SonarQube issue unique key
        rule = item.get("sonar_rule_key", "")       # violated rule id (e.g. java:S1192)
        project = item.get("sonar_project_key", "") # SonarQube project key

        # issue_search_item: the original SonarQube /api/issues/search response item
        issue_item = item.get("issue_search_item", {})
        msg = issue_item.get("message", "")          # issue description
        severity = issue_item.get("severity", "")    # severity (BLOCKER, CRITICAL, ...)
        component = item.get("component", "")        # file path (project_key:src/...)
        line = issue_item.get("line") or issue_item.get("textRange", {}).get("startLine", 0)

        # --- 3-b. Extract the code snippet ---
        # Try several key names to grab the code.
        # sonar_issue_exporter.py stores it under code_snippet, but data from
        # other sources is supported too.
        raw_code = item.get("code_snippet", "")
        if not raw_code:
            raw_code = item.get("source", "") or item.get("code", "")

        # Use the original code as-is — no HTML cleanup or other processing.
        # A previous version's HTML stripping corrupted code content.
        final_code = raw_code if raw_code else "(NO CODE CONTENT)"

        # --- 3-c. Process rule info ---
        # Limit only the description length; keep the contents intact.
        rule_detail = item.get("rule_detail", {})
        raw_desc = rule_detail.get("description", "")
        safe_desc = truncate_text(raw_desc, max_chars=800)

        # Dify workflow's Jinja2 template uses curly braces ({}) as variable
        # delimiters, so replace curlies inside the description with parens
        # to avoid parse errors.
        safe_rule_json = json.dumps({
            "key": rule_detail.get("key"),
            "name": rule_detail.get("name"),
            "description": safe_desc.replace("{", "(").replace("}", ")")
        }, ensure_ascii=False)

        # Serialize the issue metadata as a JSON string included in the Dify input.
        safe_issue_json = json.dumps({
            "key": key, "rule": rule, "message": msg, "severity": severity,
            "project": project, "component": component, "line": line
        }, ensure_ascii=False)

        # Generate a unique user id per issue request.
        # Keeps Dify sessions separate so prior conversations don't influence the call.
        session_user = f"jenkins-{uuid.uuid4()}"

        print(f"\n[DEBUG] >>> Sending Issue {key}")

        # Common Step R metadata is used by both the skip_llm and llm-call paths.
        # Previously initialized inside the skip_llm branch, which raised
        # UnboundLocalError when MINOR/INFO issues were routed.
        enclosing_fn = item.get("enclosing_function", "") or ""
        enclosing_ln = item.get("enclosing_lines", "") or ""
        commit_sha = item.get("commit_sha", "") or args.commit_sha or ""

        # Step C — issues tagged skip_llm=True by the exporter's severity routing
        # bypass the Dify call and build out_row from a template response. Saves
        # LLM cost on issues at MINOR/INFO and below.
        skip_llm = bool(item.get("skip_llm"))
        if skip_llm:
            print(f"[SKIP_LLM] {key} — template response generated")
            rd = item.get("rule_detail", {}) or {}
            templated = build_skip_llm_outputs(severity, msg)
            out_row = _build_out_row(
                item=item, key=key, severity=severity, msg=msg,
                line=line, enclosing_fn=enclosing_fn, enclosing_ln=enclosing_ln,
                commit_sha=commit_sha, rule=rule, rule_detail=rd,
                final_code=final_code, outputs=templated, llm_skipped=True,
            )
            out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            continue

        # --- 3-d. Build the Dify workflow input data ---
        # Step C: kb_query is expanded into a multi-query (the previous 1-line
        # `rule + msg` becomes 4 lines: code near issue line + enclosing
        # function + path + rule name). This way RAG pulls "similar chunks
        # in this function/file context" beyond the rule name alone.
        # Step R: pass enclosing_function / enclosing_lines / commit_sha
        # extracted by the exporter to the LLM as additional hints (the
        # prompt explicitly states "location facts are rendered separately,
        # do not repeat in the body").
        # Initial kb_query is attempt=0 (full structured). On retry, switch
        # shape via build_kb_query(item, attempt=i) — P2 R-4.
        # Phase B F2b — serialize the tree-sitter metadata populated by the
        # exporter as text for the LLM prompt. When empty, the
        # corresponding section in the workflow user prompt is rendered as
        # blank.
        enclosing_meta_text = format_enclosing_meta(item)
        # Phase E — graph / git history / similar locations text serialization.
        # Phase E' (a): format_*() includes the header (## ...) so when
        # values are empty, the entire string is empty → the corresponding
        # workflow user-prompt line ends up blank.
        dependency_tree_text = format_dependency_tree(item)
        git_history_text = format_git_history(item)
        similar_locations_text = format_similar_locations(item)
        project_overview_block = format_project_overview(project_overview_text)

        inputs = {
            "sonar_issue_key": key,
            "sonar_project_key": project,
            "code_snippet": final_code,
            "sonar_issue_url": item.get("sonar_issue_url", ""),
            "kb_query": build_kb_query(item, attempt=0),
            "sonar_issue_json": safe_issue_json,
            "sonar_rule_json": safe_rule_json,
            # Step R new inputs
            "enclosing_function": enclosing_fn,
            "enclosing_lines": enclosing_ln,
            "commit_sha": commit_sha,
            # Phase B F2b — tree-sitter metadata of the enclosing function
            # (decorators / endpoint / doc_params/returns/throws / callees / leading doc).
            # Empty when nothing applies.
            "enclosing_meta": enclosing_meta_text,
            # Phase E — 4 inputs strengthening real cause analysis (header included; empty → blank string)
            "dependency_tree": dependency_tree_text,        # E1 — depth-2 caller graph
            "git_history": git_history_text,                # E3 — function change history + same-rule fix history
            "similar_locations": similar_locations_text,    # E5 — other locations of the same rule
            "project_overview": project_overview_block,     # E2-lite — project overview (same for every issue)
            # P1: self-exclusion — workflow's context_filter Code node uses
            # this path to drop matching RAG chunks, eliminating the
            # degenerate case of "getting back our own file".
            "issue_file_path": item.get("relative_path", "") or "",
            # P5 — pass the line number too so context_filter can do precise
            # self-exclusion. context_filter judges self by "does the chunk's
            # lines contain issue_line" → in cases like ProfileDAO with the
            # same method name appearing multiple times, sibling code can
            # still be used.
            "issue_line": str(line) if line else "",
            # retry_hint is updated below in the retry loop per attempt.
            # Inserted into the LLM user prompt's tail as {{#start.retry_hint#}}.
        }

        # Debug aid: confirm the actual code being sent.
        print(f"   [DATA CHECK] Code Length: {len(final_code)}")
        print(f"   [DATA CHECK] Preview: {final_code[:100].replace(chr(10), ' ')}...")

        # Dify workflow run payload
        # response_mode="blocking": wait until the workflow completes, then return the result
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": session_user
        }

        # --- 3-e. API call + retry logic ---
        # Up to 3 attempts, with a 2-second wait on failure.
        # Provides resilience against LLM inference overload and transient network issues.
        # Even when Dify returns `succeeded`, the LLM body may emit an
        # empty/non-JSON response that falls into the Code node's default
        # fallback, leaving core fields blank. Treat empty
        # impact_analysis_markdown as a retry trigger.
        success = False
        last_outputs = None
        # Retry hints — escalating per attempt index.
        # 1st: just the default system prompt. 2nd: re-emphasize strict JSON.
        # 3rd: copy the minimum skeleton.
        # When the 4B model corrupts the schema on the first try, repeating
        # the same prompt has low effect.
        retry_hints = [
            "",
            (
                "**[Retry 1]** The previous response did not satisfy the JSON schema. "
                "This time output **a single JSON object only**. No code fences / no prose. "
                "At minimum the `title`, `labels`, and `impact_analysis_markdown` fields "
                "must not be empty."
            ),
            (
                "**[Retry 2 — final]** Copy the skeleton below verbatim, but "
                "**replace the `...` placeholders inside the values with actual issue content**. "
                "Leaving `...` in place is not allowed. No field deletions/additions. No code fences:\n"
                "{\n"
                '  "title": "<actual one-line summary>",\n'
                '  "labels": ["<severity>", "<type>"],\n'
                '  "impact_analysis_markdown": "<3-6 line impact analysis>",\n'
                '  "suggested_fix_markdown": "",\n'
                '  "classification": "true_positive",\n'
                '  "fp_reason": "",\n'
                '  "confidence": "medium",\n'
                '  "suggested_diff": ""\n'
                "}"
            ),
        ]
        for i in range(3):
            inputs["retry_hint"] = retry_hints[i]
            # P2 R-3 — call the HyDE natural-language conversion only on attempt=2 (the last retry).
            # Signal: every prior attempt returned empty → recall must be boosted.
            hyde_text = ""
            if i == 2 and args.hyde_ollama_base_url:
                hyde_text = hyde_expand(item, args.hyde_ollama_base_url, args.hyde_ollama_model)
                if hyde_text:
                    print(f"   [HyDE] attempt=2 augment query: {hyde_text[:80]}...", file=sys.stderr)
            # P2 R-4: differently shaped kb_query per attempt.
            inputs["kb_query"] = build_kb_query(item, attempt=i, hyde_text=hyde_text)
            status, body = send_dify_request(target_api_url, args.dify_api_key, payload)

            if status == 200:
                try:
                    res = json.loads(body)
                    # Verify the workflow's internal execution succeeded.
                    if res.get("data", {}).get("status") == "succeeded":
                        outputs = res["data"].get("outputs", {}) or {}
                        if (outputs.get("impact_analysis_markdown") or "").strip():
                            rd = item.get("rule_detail", {}) or {}
                            # P1.5 M-1 — parse the stats JSON uploaded by context_filter.
                            # On failure → None; the diagnostic field is recorded as None.
                            ctx_stats = None
                            raw_stats = outputs.get("context_stats_json") or ""
                            if raw_stats:
                                try:
                                    ctx_stats = json.loads(raw_stats)
                                except Exception:
                                    ctx_stats = None
                            # F1 — one-line stderr trace of retrieve. Shows
                            # which chunk landed in which bucket so we can
                            # post-mortem the real cause of callers/tests
                            # bucket=0 (score cutoff vs missing meta vs weak
                            # query match). Active only when env var
                            # RAG_TRACE=1 is set — default OFF (avoids log
                            # bloat).
                            if os.environ.get("RAG_TRACE") and ctx_stats:
                                used_items = ctx_stats.get("used_items") or []
                                ret_total = ctx_stats.get("retrieved_total", 0)
                                excl = ctx_stats.get("excluded_self", 0)
                                kept = ctx_stats.get("kept_total", 0)
                                used_brief = ", ".join(
                                    f"[{u.get('bucket','?')[:3]}]{u.get('symbol','?')}"
                                    for u in used_items[:6]
                                )
                                print(
                                    f"   [RAG-TRACE] {key[:8]} retr={ret_total} "
                                    f"excl={excl} kept={kept} used={len(used_items)} "
                                    f"items=[{used_brief}]",
                                    file=sys.stderr,
                                )
                            out_row = _build_out_row(
                                item=item, key=key, severity=severity, msg=msg,
                                line=line, enclosing_fn=enclosing_fn, enclosing_ln=enclosing_ln,
                                commit_sha=commit_sha, rule=rule, rule_detail=rd,
                                final_code=final_code,
                                outputs=outputs,
                                llm_skipped=False,
                                context_stats=ctx_stats,
                            )
                            out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                            success = True
                            print(f"   -> Success.")
                            break
                        else:
                            last_outputs = outputs
                            parse_status = outputs.get("parse_status") or "(workflow not updated? parse_status missing)"
                            print(
                                f"   -> Dify succeeded but outputs empty "
                                f"(impact_analysis_markdown missing) [parse_status={parse_status}]",
                                file=sys.stderr,
                            )
                            # Detailed dump: print the full llm_text_preview only when budget remains
                            if empty_debug_budget[0] != 0:
                                preview = outputs.get("llm_text_preview") or ""
                                parse_error = outputs.get("parse_error_msg") or ""
                                ctx_raw = outputs.get("context_stats_json") or ""
                                ctx_summary = ""
                                if ctx_raw:
                                    try:
                                        cs = json.loads(ctx_raw)
                                        ctx_summary = (
                                            f"retrieved={cs.get('retrieved_total')}, "
                                            f"kept={cs.get('kept_total')}, "
                                            f"used={cs.get('used_total')}, "
                                            f"buckets={cs.get('buckets')}"
                                        )
                                    except Exception:
                                        ctx_summary = "(context_stats_json parse failed)"
                                print(
                                    f"      [EMPTY-DEBUG] key={key} attempt={i} "
                                    f"parse_status={parse_status}\n"
                                    f"      [EMPTY-DEBUG] parse_error: {parse_error}\n"
                                    f"      [EMPTY-DEBUG] context: {ctx_summary}\n"
                                    f"      [EMPTY-DEBUG] llm_text_preview ({len(preview)} chars):\n"
                                    f"------------------ LLM RAW ------------------\n"
                                    f"{preview}\n"
                                    f"---------------------------------------------",
                                    file=sys.stderr,
                                )
                                if empty_debug_budget[0] > 0:
                                    empty_debug_budget[0] -= 1
                    else:
                        # HTTP 200 but workflow internal failure
                        print(f"   -> Dify Internal Fail: {res}", file=sys.stderr)
                except: pass

            print(f"   -> Retry {i+1}/3 due to Status {status} | Error: {body}")
            time.sleep(2)

        if not success:
            if last_outputs is not None:
                # All 3 retries returned empty outputs — instead of dropping the
                # issue, write the last empty response anyway so the GitLab
                # issue still gets created. The creator renders a fallback
                # text "LLM did not provide impact analysis".
                rd = item.get("rule_detail", {}) or {}
                # P1.5 M-1/M-4 — context_filter stats are still valid on the retry-failure path.
                ctx_stats = None
                raw_stats = last_outputs.get("context_stats_json") or ""
                if raw_stats:
                    try:
                        ctx_stats = json.loads(raw_stats)
                    except Exception:
                        ctx_stats = None
                out_row = _build_out_row(
                    item=item, key=key, severity=severity, msg=msg,
                    line=line, enclosing_fn=enclosing_fn, enclosing_ln=enclosing_ln,
                    commit_sha=commit_sha, rule=rule, rule_detail=rd,
                    final_code=final_code, outputs=last_outputs, llm_skipped=False,
                    context_stats=ctx_stats,
                )
                out_row["retry_exhausted"] = True  # M-4 aggregation key
                out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                print(f"[FAIL-EMPTY] {key} — 3 retries exhausted with empty outputs; row written anyway", file=sys.stderr)
            else:
                print(f"[FAIL] Failed to analyze {key}", file=sys.stderr)

    # ---------------------------------------------------------------
    # [Step 4] Close the result file
    # ---------------------------------------------------------------
    out_fp.close()

if __name__ == "__main__":
    main()
