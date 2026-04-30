#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# File: sonar_issue_exporter.py
# Version: 1.2
#
# [Overview]
# This script handles **stage 1 (static-analysis result collection)** of the
# quality analysis pipeline (Phase 3). It pulls the open-issue list via the
# SonarQube REST API with full pagination, then enriches each issue with the
# related source-code lines and rule details, and finally consolidates
# everything into a single JSON file.
#
# [Position in the pipeline]
# SonarQube (static analysis result)
#       ↓ REST API
# >>> sonar_issue_exporter.py (collect issues + enrich code/rule) <<<
#       ↓ sonar_issues.json
# dify_sonar_issue_analyzer.py (AI analysis)
#
# [Core flow]
# 1. /api/issues/search: paginate the open-issue list 100 at a time.
# 2. /api/rules/show: fetch the violated-rule detail for each issue (cached
#    to avoid duplicate calls).
# 3. /api/sources/lines: fetch ~100 lines around the issue location.
# 4. Save the consolidated information to sonar_issues.json.
#
# [Run example]
# python3 sonar_issue_exporter.py \
#   --sonar-host-url http://sonarqube:9000 \
#   --sonar-token squ_xxxxx \
#   --project-key myproject \
#   --output sonar_issues.json
# ==================================================================================

import argparse
import base64
import glob
import hashlib
import json
import subprocess
import sys
import html
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

# Step R reuses repo_context_builder's chunking logic to extract the
# enclosing_function. The sibling module lives in the same
# /opt/pipeline-scripts/ tree (entrypoint.sh symlinks it as `scripts`),
# so direct import is fine. On failure we degrade gracefully — only the
# enclosing_function extraction is skipped, the rest of the pipeline runs.
try:
    from repo_context_builder import extract_chunks_from_file, LANG_CONFIG  # type: ignore
    _TS_AVAILABLE = True
except Exception as _e:  # noqa: BLE001
    _TS_AVAILABLE = False
    _TS_IMPORT_ERR = str(_e)


def _clean_html_tags(text: str) -> str:
    """
    Strip HTML tags and decode HTML entities.

    SonarQube API responses include HTML tags inside the code and rule
    descriptions (e.g. <span class="k">public</span>, &lt;String&gt;).
    The LLM needs plain text to analyze the code accurately, so we strip
    the tags and decode the entities back to literal characters.

    Args:
        text: original text containing HTML

    Returns:
        Plain text with HTML tags removed and entities decoded
    """
    if not text: return ""
    # Step 1: drop HTML tags (<span ...>, </div>, etc.)
    text = re.sub(r'<[^>]+>', '', text)
    # Step 2: decode HTML entities (&lt; → <, &amp; → &, ...)
    text = html.unescape(text)
    return text


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    Send an HTTP GET request and return the parsed JSON response.

    A common helper used by every SonarQube API call.
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _build_basic_auth(token: str) -> str:
    """
    Convert a SonarQube token into an HTTP Basic Authentication header value.

    SonarQube uses Basic Auth with the token as the username and an empty
    password (token: format).
    """
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")


def _api_url(host: str, path: str, params: dict = None) -> str:
    """
    Build the full URL for a SonarQube API endpoint.

    Args:
        host: SonarQube host URL (e.g. http://sonarqube:9000)
        path: API path (e.g. /api/issues/search)
        params: query parameter dictionary

    Returns:
        Complete URL string (with query string)
    """
    base = host.rstrip("/") + "/"
    url = urljoin(base, path.lstrip("/"))
    if params:
        url += "?" + urlencode(params, doseq=True)
    return url

def _get_rule_details(host: str, headers: dict, rule_key: str) -> dict:
    """
    Fetch the detail of a SonarQube violation rule.

    Pulls name, description, severity, and language from /api/rules/show.
    The LLM uses this information to understand "why this is a problem".

    SonarQube rule descriptions can be split into several
    descriptionSections, each containing HTML tags. Tags are stripped
    before returning.

    Args:
        host: SonarQube host URL
        headers: Basic Auth header
        rule_key: rule key (e.g. "java:S1192")

    Returns:
        dict: rule detail (key, name, description, severity, lang).
              On API failure returns defaults so the whole process keeps
              running.
    """
    if not rule_key:
        return {"key": "UNKNOWN", "name": "Unknown", "description": "No rule key."}

    url = _api_url(host, "/api/rules/show", {"key": rule_key})

    # Defaults used when the API call fails.
    fallback = {
        "key": rule_key,
        "name": f"Rule {rule_key}",
        "description": "No detailed description available.",
        "lang": "code"
    }

    try:
        resp = _http_get_json(url, headers)
        rule = resp.get("rule", {})
        if not rule: return fallback

        # Walk the structured description sections (e.g. ROOT_CAUSE,
        # HOW_TO_FIX) and collect their text.
        desc_parts = []
        sections = rule.get("descriptionSections", [])
        for sec in sections:
            k = sec.get("key", "").upper().replace("_", " ")  # normalize section name to upper case
            c = sec.get("content", "")
            if c:
                # Strip HTML tags so the LLM sees plain text.
                desc_parts.append(f"[{k}]\n{_clean_html_tags(c)}")

        full_desc = "\n\n".join(desc_parts)
        # If no structured sections, fall back to the legacy fields (mdDesc, htmlDesc).
        if not full_desc:
            raw_desc = rule.get("mdDesc") or rule.get("htmlDesc") or rule.get("description") or ""
            full_desc = _clean_html_tags(raw_desc)

        return {
            "key": rule.get("key", rule_key),
            "name": rule.get("name", fallback["name"]),
            "description": full_desc if full_desc else fallback["description"],
            "severity": rule.get("severity", "UNKNOWN"),
            "lang": rule.get("lang", "code")
        }
    except:
        return fallback

def _relative_path_from_component(component: str, project_key: str) -> str:
    """Strip the project prefix from a Sonar component (e.g.
    'dscore-ttc-sample:src/auth.py') to obtain the repo-relative path.
    Keeps the original text if it does not match the expected pattern.
    """
    if not component:
        return ""
    prefix = f"{project_key}:"
    if component.startswith(prefix):
        return component[len(prefix):]
    # If the project key is missing and only 'src/...' is given, return as-is
    return component


def _enclosing_function(repo_root: str, rel_path: str, target_line: int) -> tuple:
    """Given the repo root, relative path, and issue line, return the
    (symbol, lines_str) tuple for the function/method containing that line.
    Returns ("", "") on failure.

    Backward-compat. New code should use _enclosing_meta() — it returns the
    full tree-sitter extracted metadata (decorators / endpoint / doc_struct).
    """
    meta = _enclosing_meta(repo_root, rel_path, target_line)
    return (meta.get("symbol", ""), meta.get("lines", ""))


def _enclosing_meta(repo_root: str, rel_path: str, target_line: int) -> dict:
    """Return the full chunk metadata for the chunk that contains the issue line.

    Phase B F2a: the older _enclosing_function only returned (symbol, lines),
    so the LLM prompt could not see the function's decorators / endpoint /
    docstring structure. Now we hand the matched chunk's tree-sitter metadata
    to the analyzer wholesale, allowing it to inject structured hints into
    the LLM prompt.

    Returned dict keys (all optional, empty on failure):
      symbol, lines, kind, lang, decorators, endpoint,
      doc_params, doc_returns, doc_throws, doc, callees
    """
    empty = {
        "symbol": "", "lines": "", "kind": "", "lang": "",
        "decorators": [], "endpoint": "", "doc_params": [],
        "doc_returns": None, "doc_throws": [], "doc": "", "callees": [],
    }
    if not _TS_AVAILABLE or not repo_root or not rel_path or target_line <= 0:
        return empty
    abs_path = Path(repo_root) / rel_path
    if not abs_path.is_file():
        return empty
    if abs_path.suffix.lower() not in LANG_CONFIG:
        return empty
    try:
        chunks = extract_chunks_from_file(abs_path, Path(repo_root), commit_sha="")
    except Exception:
        return empty
    best = None
    best_key = None
    for ch in chunks:
        lines = ch.get("lines", "")
        try:
            s, e = map(int, lines.split("-", 1))
        except Exception:
            continue
        if s <= target_line <= e:
            span = e - s
            kind = ch.get("kind", "")
            pref = 0 if kind in ("function", "method") else 1
            key = (pref, span)
            if best is None or key < best_key:
                best = ch
                best_key = key
    if best is None:
        return empty
    return {
        "symbol": best.get("symbol", ""),
        "lines": best.get("lines", ""),
        "kind": best.get("kind", ""),
        "lang": best.get("lang", ""),
        "decorators": list(best.get("decorators") or []),
        "endpoint": (best.get("endpoint") or "").strip(),
        "doc_params": list(best.get("doc_params") or []),
        "doc_returns": best.get("doc_returns"),
        "doc_throws": list(best.get("doc_throws") or []),
        "doc": (best.get("doc") or "").strip(),
        "callees": list(best.get("callees") or []),
    }


def _git_context(repo_root: str, rel_path: str, line: int) -> str:
    """Step B — return a 3-line text summarizing git blame for the issue line
    plus the file's recent log.

    Returns "" on failure (the pipeline keeps going). Provides the LLM with
    the "who put this code in, when, and why" context.
    """
    if not repo_root or not rel_path or line <= 0:
        return ""
    if not Path(repo_root).is_dir():
        return ""
    try:
        blame = subprocess.run(
            ["git", "-C", repo_root, "blame", "-L", f"{line},{line}", "--porcelain", rel_path],
            capture_output=True, text=True, timeout=15
        )
        author = ""
        committed = ""
        sha = ""
        if blame.returncode == 0:
            for ln in blame.stdout.splitlines():
                if ln.startswith("author "):
                    author = ln[len("author "):]
                elif ln.startswith("author-time "):
                    # unix epoch — skip human-readable formatting (avoid extra complexity)
                    committed = ln[len("author-time "):]
                elif not sha and re.match(r"^[0-9a-f]{40}", ln):
                    sha = ln.split()[0][:12]

        log_line = ""
        log_run = subprocess.run(
            ["git", "-C", repo_root, "log", "-1", "--format=%an|%ar|%s", "--", rel_path],
            capture_output=True, text=True, timeout=15
        )
        if log_run.returncode == 0 and log_run.stdout.strip():
            log_line = log_run.stdout.strip()

        parts = []
        if author or sha:
            parts.append(f"blame L{line}: {author} ({sha})")
        if committed:
            parts.append(f"committed_at(epoch)={committed}")
        if log_line:
            parts.append(f"last_commit: {log_line}")

        # Phase E E3 — change history of the function's line range (up to 5)
        # plus the file's 5 most recent commits. `git log -L` follows
        # function-level changes and gives clues about "why this code was
        # written this way".
        try:
            log_l = subprocess.run(
                ["git", "-C", repo_root, "log", "-L",
                 f"{line},+1:{rel_path}",
                 "--no-patch", "--pretty=%h|%ar|%an|%s", "-5"],
                capture_output=True, text=True, timeout=20,
            )
            if log_l.returncode == 0 and log_l.stdout.strip():
                history_lines = [
                    ln.strip() for ln in log_l.stdout.splitlines()
                    if ln.strip() and "|" in ln
                ][:5]
                if history_lines:
                    parts.append("function_history:")
                    for h in history_lines:
                        parts.append(f"  - {h}")
        except Exception:
            pass
        return "\n".join(parts)
    except Exception:
        return ""


def _similar_rule_history(repo_root: str, rule_key: str, limit: int = 3) -> list:
    """Phase E E3 — find prior commit messages where the same rule_key was fixed.

    Matches GitLab issue bodies / commit messages that contain the Sonar rule
    key. Example pattern: "fix: javascript:S6606 ...".

    Returns: ["abc1234 2 weeks ago alice fix Sonar S6606 in profile-dao", ...]
    """
    if not repo_root or not rule_key or not Path(repo_root).is_dir():
        return []
    try:
        # Search only on the short form (`S1234`) — the colon in
        # `javascript:S1234` would have to be escaped for git log --grep
        # regex, and the short form matches better anyway.
        short = rule_key.split(":")[-1] if ":" in rule_key else rule_key
        if not short or len(short) < 3:
            return []
        run = subprocess.run(
            ["git", "-C", repo_root, "log",
             f"--grep={short}", "--pretty=%h|%ar|%an|%s", f"-{limit}"],
            capture_output=True, text=True, timeout=15,
        )
        if run.returncode == 0 and run.stdout.strip():
            return [
                ln.strip() for ln in run.stdout.splitlines()
                if ln.strip() and "|" in ln
            ][:limit]
    except Exception:
        pass
    return []


def _load_callgraph_index(callgraph_dir: str) -> dict:
    """Step B — load the *.jsonl files under callgraph_dir once and build a
    `callee_symbol → [caller path::symbol, ...]` reverse index. Runs only
    once per exporter invocation; later `_direct_callers` only does dict
    lookups against this index.
    """
    idx: dict = {}
    if not callgraph_dir or not Path(callgraph_dir).is_dir():
        return idx
    for jp in sorted(glob.glob(os.path.join(callgraph_dir, "*.jsonl"))):
        try:
            with open(jp, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        ch = json.loads(ln)
                    except Exception:
                        continue
                    path = ch.get("path") or ""
                    symbol = ch.get("symbol") or ""
                    callees = ch.get("callees") or []
                    caller_ref = f"{path}::{symbol}" if path and symbol else (path or symbol)
                    if not caller_ref:
                        continue
                    for cal in callees:
                        if not cal:
                            continue
                        idx.setdefault(cal, []).append(caller_ref)
        except Exception:
            continue
    return idx


def _direct_callers(cg_index: dict, symbol: str, limit: int = 10) -> list:
    """Return the callers of `symbol` (up to limit) from cg_index."""
    if not symbol or not cg_index:
        return []
    refs = cg_index.get(symbol, [])
    # Same caller may appear multiple times — dedup while preserving order.
    seen = set()
    out = []
    for r in refs:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _depth2_callers(cg_index: dict, symbol: str, max_d1: int = 5, max_d2: int = 5) -> dict:
    """Phase E E1 — depth-2 caller graph for impact-scope tracing.

    enclosing function → direct caller (depth 1) → caller's caller (depth 2).
    Lets the LLM trace "what is affected if we change this function" more
    accurately when the analyzer injects this as structured text into the
    prompt.

    Returns: {
      "depth_1": [...],  # direct callers (path::symbol)
      "depth_2": [...],  # callers of those callers (depth 2)
    }
    """
    if not symbol or not cg_index:
        return {"depth_1": [], "depth_2": []}
    d1 = _direct_callers(cg_index, symbol, limit=max_d1)
    seen_d2 = set(d1) | {symbol}  # block self/d1 duplicates
    d2 = []
    for ref in d1:
        # ref = "path::caller_symbol" — extract caller symbol only
        caller_sym = ref.rsplit("::", 1)[-1] if "::" in ref else ref
        if not caller_sym or caller_sym == symbol:
            continue
        for r in cg_index.get(caller_sym, []):
            if r in seen_d2:
                continue
            seen_d2.add(r)
            d2.append(r)
            if len(d2) >= max_d2:
                break
        if len(d2) >= max_d2:
            break
    return {"depth_1": d1, "depth_2": d2}


def _build_project_overview(repo_root: str, max_chars: int = 2400) -> str:
    """Phase E E2-lite — bundle project metadata into a single text block to
    attach to every LLM prompt. Pulls only the essentials from
    README / CONTRIBUTING / package.json — no separate KB.

    Collection priority:
      1. package.json — name + description + key dependencies (10)
      2. README.md — first 1500 chars (title + overview)
      3. CONTRIBUTING.md — first 500 chars

    Cap: 2400 chars. LLM prompt token overhead < 1KB.
    """
    if not repo_root or not Path(repo_root).is_dir():
        return ""
    parts = []
    root = Path(repo_root)

    # 1) package.json — dependencies + description
    pkg_path = root / "package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            line = []
            if pkg.get("name"):
                line.append(f"name: {pkg['name']}")
            if pkg.get("description"):
                line.append(f"desc: {pkg['description'][:200]}")
            deps = list((pkg.get("dependencies") or {}).keys())[:10]
            if deps:
                line.append(f"deps: {', '.join(deps)}")
            if line:
                parts.append("[package.json] " + " · ".join(line))
        except Exception:
            pass

    # Python pyproject.toml fallback
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and not any("package.json" in p for p in parts):
        try:
            txt = pyproject.read_text(encoding="utf-8")[:600]
            parts.append(f"[pyproject.toml]\n{txt}")
        except Exception:
            pass

    # 2) README.md
    for name in ("README.md", "README.rst", "README.txt", "Readme.md"):
        p = root / name
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore").strip()
                # First 1500 non-empty chars
                if len(txt) > 1500:
                    txt = txt[:1500] + "\n... (truncated)"
                parts.append(f"[README]\n{txt}")
            except Exception:
                pass
            break

    # 3) CONTRIBUTING.md
    for name in ("CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt"):
        p = root / name
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore").strip()
                if len(txt) > 500:
                    txt = txt[:500] + "..."
                parts.append(f"[CONTRIBUTING]\n{txt}")
            except Exception:
                pass
            break

    overview = "\n\n".join(parts)
    if len(overview) > max_chars:
        overview = overview[:max_chars] + "\n... (truncated)"
    return overview


def _classify_severity(severity: str) -> tuple:
    """Step B — analyze every severity with gemma4:e4b.

    Always run Dify/LLM analysis regardless of severity; the skip_llm branch
    is no longer used.
    """
    _ = (severity or "").upper()
    return ("gemma4:e4b", False)


def _cluster_key(rule_key: str, enclosing_function: str, component: str) -> str:
    """Step B — cluster issues that share the same rule, same function, and
    same directory.

    Only the representative is emitted; the rest go into affected_locations
    to cut down on P3 LLM calls.
    """
    base_dir = os.path.dirname(component or "")
    raw = f"{rule_key or ''}|{enclosing_function or ''}|{base_dir}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _severity_rank(sev: str) -> int:
    """Used when picking the cluster representative — more severe = smaller rank value."""
    order = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}
    return order.get((sev or "").upper(), 9)


def _apply_clustering(records: list) -> list:
    """Group by cluster_key, keep one representative, fold the rest into affected_locations.

    Representative selection: most severe → smallest line number.
    To avoid non-deterministic ordering, preserve the original order within a
    cluster_key and only `pick` by the sort key.
    """
    groups: dict = {}
    for r in records:
        k = r.get("cluster_key") or r.get("sonar_issue_key")
        groups.setdefault(k, []).append(r)
    out: list = []
    for k, items in groups.items():
        if len(items) == 1:
            items[0]["affected_locations"] = []
            out.append(items[0])
            continue
        items.sort(key=lambda r: (_severity_rank(r.get("issue_search_item", {}).get("severity", "")), r.get("line") or 0))
        leader = items[0]
        followers = items[1:]
        leader["affected_locations"] = [
            {
                "component": f.get("component"),
                "line": f.get("line"),
                "sonar_issue_key": f.get("sonar_issue_key"),
                "relative_path": f.get("relative_path"),
            }
            for f in followers
        ]
        out.append(leader)
    return out


def _diff_mode_filter(records: list, state_dir: str, mode: str) -> tuple:
    """Step B — diff-mode.

    `mode=incremental` → compare against the issue-key set in
    {state_dir}/last_scan.json and drop existing keys.
    `mode=full` → no filtering + overwrite last_scan.
    Returns: (filtered_records, skipped_count).
    """
    state_path = Path(state_dir) / "last_scan.json" if state_dir else None
    prev_keys: set = set()
    if mode == "incremental" and state_path and state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            prev_keys = set(data.get("issue_keys", []))
        except Exception:
            prev_keys = set()

    filtered = records
    skipped = 0
    if mode == "incremental" and prev_keys:
        filtered = [r for r in records if r.get("sonar_issue_key") not in prev_keys]
        skipped = len(records) - len(filtered)

    # Write the new last_scan (regardless of full/incremental — the next
    # incremental run uses this snapshot as its baseline)
    if state_path:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "issue_keys": sorted({r.get("sonar_issue_key") for r in records if r.get("sonar_issue_key")}),
                "snapshot_size": len(records),
            }
            state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[WARN] last_scan.json write failed: {e}", file=sys.stderr)

    return (filtered, skipped)


def _get_code_lines(host: str, headers: dict, component: str, target_line: int) -> str:
    """
    Extract the 50 lines before and after the issue line (101 total) as text.

    Pulls the code from SonarQube /api/sources/lines and prefixes the issue
    line with a ">>" marker so the LLM can spot it easily.

    SonarQube returns code with syntax-highlight HTML tags, so they are
    stripped via _clean_html_tags().

    Args:
        host: SonarQube host URL
        headers: Basic Auth header
        component: file component key (e.g. "myproject:src/main/App.java")
        target_line: issue line number

    Returns:
        str: code text with line numbers (issue line marked with ">>").
             Returns an empty string on lookup failure.
    """
    if target_line <= 0 or not component: return ""

    # Request 50 lines before and after the issue line.
    start = max(1, target_line - 50)
    end = target_line + 50

    url = _api_url(host, "/api/sources/lines", {"key": component, "from": start, "to": end})
    try:
        resp = _http_get_json(url, headers)
        sources = resp.get("sources", [])
        if not sources: return ""

        out = []
        for src in sources:
            ln = src.get("line", 0)
            raw_code = src.get("code", "")

            # Strip the syntax-highlight HTML tags SonarQube embeds.
            # (e.g. <span class="k">public</span> → public)
            code = _clean_html_tags(raw_code)

            # Mark the issue line with ">>" for visual distinction.
            marker = ">> " if ln == target_line else "   "
            # Truncate very long lines (saves LLM tokens).
            if len(code) > 400: code = code[:400] + " ...[TRUNCATED]"
            out.append(f"{marker}{ln:>5} | {code}")
        return "\n".join(out)
    except:
        return ""

def main():
    """
    Main entry point: enumerate open issues from SonarQube and enrich with code/rule info.

    [Overall flow]
    1. Parse CLI args (SonarQube connection info, project key, etc.)
    2. Page through /api/issues/search to gather all open issues.
    3. For each issue:
       a. Fetch the violation rule detail (cached to avoid duplicate calls
          for the same rule).
       b. Fetch the 50-line window around the issue location.
       c. Combine everything into a single enriched object.
    4. Save the entire result as sonar_issues.json.
    """
    # ---------------------------------------------------------------
    # [Step 1] Parse CLI args
    # ---------------------------------------------------------------
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)   # SonarQube host URL
    ap.add_argument("--sonar-token", required=True)       # SonarQube auth token
    ap.add_argument("--project-key", required=True)       # target project key
    ap.add_argument("--output", default="sonar_issues.json")  # output file path
    ap.add_argument("--severities", default="")           # severity filter (unused, kept for compat)
    ap.add_argument("--statuses", default="")             # status filter (unused, kept for compat)
    ap.add_argument("--sonar-public-url", default="")     # external-facing URL (unused, kept for compat)
    # Step R new — used by the GitLab issue body renderer and to pin
    # the snapshot for RAG search. The 03 Jenkinsfile resolves this via
    # `git ls-remote` and forwards it (temporary until Phase 1.5 ships).
    ap.add_argument("--commit-sha", default="")
    # Step R new — repo root used to extract enclosing_function. Usually
    # /var/knowledges/codes/<repo>. When empty, enclosing_function is
    # skipped (the pipeline still runs).
    ap.add_argument("--repo-root", default="")
    # Step B new — diff-mode, state, callgraph paths.
    ap.add_argument("--mode", choices=["full", "incremental"], default="full",
                    help="full: reset last_scan and emit everything. incremental: diff against the previous snapshot.")
    ap.add_argument("--state-dir", default="/var/knowledges/state",
                    help="Path for scanner state files such as last_scan.json.")
    ap.add_argument("--callgraph-dir", default="/var/knowledges/docs/result",
                    help="Directory of JSONL chunks left by P1 — source for the direct_callers reverse index.")
    ap.add_argument("--disable-clustering", action="store_true",
                    help="Emit each issue separately instead of folding same rule+function+dir into one representative.")
    args, _ = ap.parse_known_args()

    # SonarQube API auth header (Basic Auth)
    headers = {"Authorization": _build_basic_auth(args.sonar_token)}

    # ---------------------------------------------------------------
    # [Step 2] Pull all open issues with pagination.
    # SonarQube returns at most 100 per call, so loop while incrementing
    # the page index.
    # ---------------------------------------------------------------
    issues = []
    p = 1
    while True:
        query = {
            "componentKeys": args.project_key,  # target project
            "resolved": "false",                # open issues only
            "p": p, "ps": 100,                  # page index / page size
            "additionalFields": "_all"          # include all extras
        }
        if args.severities.strip():
            query["severities"] = args.severities.strip()
        if args.statuses.strip():
            query["statuses"] = args.statuses.strip()
        url = _api_url(args.sonar_host_url, "/api/issues/search", query)
        try:
            res = _http_get_json(url, headers)
            items = res.get("issues", [])
            issues.extend(items)
            # Stop the loop once nothing more is left or we hit the total.
            if not items or p * 100 >= res.get("paging", {}).get("total", 0): break
            p += 1
        except: break

    print(f"[INFO] Processing {len(issues)} issues...", file=sys.stderr)

    # Step B — load the callgraph reverse index once (symbol → callers list)
    cg_index = _load_callgraph_index(args.callgraph_dir)
    if cg_index:
        print(f"[INFO] callgraph index loaded: {len(cg_index)} callees", file=sys.stderr)

    # Phase E E2-lite — load the project overview once (attached identically
    # to every issue analysis). Goes into the metadata section of the
    # exporter output so the analyzer reuses it across all calls.
    project_overview = _build_project_overview(args.repo_root)
    if project_overview:
        print(f"[INFO] project_overview loaded: {len(project_overview)} chars", file=sys.stderr)

    # Phase E E5 — pre-aggregate other locations of the same rule_key (within
    # the current scan). When the same rule fires in N places, the LLM can
    # treat it as a "project-wide pattern".
    rule_to_locations: dict = {}
    for issue in issues:
        rk = issue.get("rule", "")
        if not rk:
            continue
        comp = issue.get("component", "")
        ln = issue.get("line") or (issue.get("textRange") or {}).get("startLine") or 0
        rule_to_locations.setdefault(rk, []).append({
            "key": issue.get("key"),
            "component": comp,
            "relative_path": _relative_path_from_component(comp, args.project_key),
            "line": int(ln) if ln else 0,
        })

    # ---------------------------------------------------------------
    # [Step 3] Enrich each issue with rule info + source code.
    # ---------------------------------------------------------------
    enriched = []
    # Cache to avoid duplicate API calls for the same rule key.
    # A single project may have dozens-to-hundreds of identical violations.
    rule_cache = {}
    # Phase E E3 — cache of commit history for the same rule_key (one git log call per rule).
    similar_fix_cache: dict = {}

    for issue in issues:
        key = issue.get("key")              # SonarQube issue unique key
        rule_key = issue.get("rule")        # violated rule id
        component = issue.get("component")  # file component key

        # Extract the issue line number (supports both location formats).
        line = issue.get("line")
        if not line and "textRange" in issue:
            line = issue["textRange"].get("startLine")
        line = int(line) if line else 0

        # --- 3-a. Fetch rule detail (cached) ---
        if rule_key not in rule_cache:
            rule_cache[rule_key] = _get_rule_details(args.sonar_host_url, headers, rule_key)

        # --- 3-b. Fetch the source code at the issue location ---
        snippet = _get_code_lines(args.sonar_host_url, headers, component, line)
        if not snippet: snippet = "(Code not found in SonarQube)"

        # --- 3-c. Step R + Phase B F2a: location meta + tree-sitter enrichment ---
        # _enclosing_meta returns symbol/lines plus
        # decorators/endpoint/doc_struct in one shot. The analyzer uses these
        # for kb_query and the LLM prompt.
        rel_path = _relative_path_from_component(component, args.project_key)
        enc_meta = _enclosing_meta(args.repo_root, rel_path, line)
        enclosing_symbol = enc_meta["symbol"]
        enclosing_lines = enc_meta["lines"]

        # --- 3-d. Step B + Phase E: git context + callers + severity + cluster ---
        git_ctx = _git_context(args.repo_root, rel_path, line)
        callers = _direct_callers(cg_index, enclosing_symbol)
        # Phase E E1 — depth-2 caller graph (impact-scope expansion)
        graph = _depth2_callers(cg_index, enclosing_symbol)
        # Phase E E3 — past fix commits for the same rule (per-rule cache)
        if rule_key not in similar_fix_cache:
            similar_fix_cache[rule_key] = _similar_rule_history(args.repo_root, rule_key)
        similar_fixes = similar_fix_cache[rule_key]
        # Phase E E5 — other locations of the same rule (excluding self, within current scan)
        similar_locations = [
            loc for loc in rule_to_locations.get(rule_key, [])
            if loc.get("key") != key
        ][:5]
        severity = (issue.get("severity") or rule_cache[rule_key].get("severity", "") or "").upper()
        judge_model, skip_llm = _classify_severity(severity)
        cluster_k = _cluster_key(rule_key, enclosing_symbol, component)

        # --- 3-e. Build the unified object ---
        # This object becomes the input for dify_sonar_issue_analyzer.py.
        enriched.append({
            "sonar_issue_key": key,           # issue unique key
            "sonar_rule_key": rule_key,       # violated rule id
            "sonar_project_key": args.project_key,  # project key
            "sonar_issue_url": f"{args.sonar_host_url}/project/issues?id={args.project_key}&issues={key}&open={key}",  # SonarQube direct link
            "issue_search_item": issue,       # original /api/issues/search response item
            "rule_detail": rule_cache[rule_key],  # rule detail (name, description, severity)
            "code_snippet": snippet,          # code around issue (with ">>" marker)
            "component": component,           # file component key
            # Step R new fields — used by the creator's deterministic renderer
            "relative_path": rel_path,        # e.g. "src/auth.py"
            "line": line,                     # integer line number
            "enclosing_function": enclosing_symbol,   # e.g. "login" (tree-sitter, "" on failure)
            "enclosing_lines": enclosing_lines,       # e.g. "22-27"
            # Phase B F2a — tree-sitter metadata of the enclosing chunk.
            # Used by analyzer's build_kb_query (F1) and enclosing_meta inputs (F2b).
            # When empty the LLM prompt automatically omits the corresponding line.
            "enclosing_kind": enc_meta["kind"],
            "enclosing_lang": enc_meta["lang"],
            "enclosing_decorators": enc_meta["decorators"],
            "enclosing_endpoint": enc_meta["endpoint"],
            "enclosing_doc_params": enc_meta["doc_params"],
            "enclosing_doc_returns": enc_meta["doc_returns"],
            "enclosing_doc_throws": enc_meta["doc_throws"],
            "enclosing_doc": enc_meta["doc"],
            "enclosing_callees": enc_meta["callees"],
            "commit_sha": args.commit_sha,            # empty → creator omits the commit section
            # Step B new fields — used by the P3 LLM prompt + clustering + skip_llm branch
            "git_context": git_ctx,                   # 3-line text like "blame L24: alice (abc123)"
            "direct_callers": callers,                # up to 10. fs-based callgraph.
            "cluster_key": cluster_k,                 # first 16 chars of sha1
            "judge_model": judge_model,               # "qwen3-coder:30b" / "gemma4:e4b" / "skip_llm"
            "skip_llm": skip_llm,                     # if True analyzer skips the Dify call
            "severity": severity,                     # top-level copy used by clustering/creator
            # affected_locations is filled in _apply_clustering (only the representative is non-empty)
            "affected_locations": [],
            # Phase E new fields
            "depth2_callers": graph["depth_2"],        # E1 — callers' callers (impact scope depth 2)
            "git_history_similar": similar_fixes,      # E3 — past commit fix history for the same rule
            "similar_rule_locations": similar_locations,  # E5 — other locations of the same rule (current scan)
        })

    # ---------------------------------------------------------------
    # [Step 4] Step B — Clustering + diff-mode
    # ---------------------------------------------------------------
    # (a) Clustering: same rule+function+dir issues → one representative + affected_locations list
    pre_cluster = len(enriched)
    if args.disable_clustering:
        clustered = enriched
        print(f"[INFO] clustering disabled: {pre_cluster} issues kept as-is", file=sys.stderr)
    else:
        clustered = _apply_clustering(enriched)
        cluster_reduced = pre_cluster - len(clustered)
        if cluster_reduced > 0:
            print(f"[INFO] clustering: {pre_cluster} → {len(clustered)} ({cluster_reduced} merged into affected_locations)", file=sys.stderr)

    # (b) Diff-mode: skip issues already seen in last_scan (incremental) + refresh snapshot
    filtered, skipped = _diff_mode_filter(clustered, args.state_dir, args.mode)
    if skipped > 0:
        print(f"[diff-mode] skipped {skipped} cached issues (mode={args.mode})", file=sys.stderr)

    # ---------------------------------------------------------------
    # [Step 5] Save the result.
    # The full enriched issue list goes into a single JSON file used as
    # input by the next stage (dify_sonar_issue_analyzer.py).
    # ---------------------------------------------------------------
    # Phase E E2-lite — write fields common to every issue (project_overview
    # etc.) once into the metadata section. The analyzer reads metadata and
    # attaches it identically to every LLM call.
    output_payload = {
        "metadata": {
            "project_overview": project_overview,
            "commit_sha": args.commit_sha or "",
            "project_key": args.project_key,
        },
        "issues": filtered,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    print(f"[OK] Exported {len(filtered)} issues (from {len(enriched)} pre-cluster, {skipped} skipped by diff-mode).", file=sys.stdout)

if __name__ == "__main__":
    main()
