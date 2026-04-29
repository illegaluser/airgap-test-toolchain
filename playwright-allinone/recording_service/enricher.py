"""TR.5 — Recording → IEEE 829-lite test plan back-inference (R-Plus).

Design: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.5"

Direct HTTP call to the host's Ollama. We bypass the Dify chatflow (chatflow is
chat/doc-mode only, so this track uses Ollama HTTP /api/generate without that
overhead).

3 few-shots + a 5-point evaluation rubric — embedded as the table in PLAN §"TR.5".
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger(__name__)


DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:26b")
DEFAULT_TIMEOUT_SEC = int(os.environ.get("RECORDING_ENRICH_TIMEOUT_SEC", "180"))


SYSTEM_PROMPT = (
    "You are a QA engineer. The following user-action sequence (14-DSL) records\n"
    "actions a real user performed on a web page. Back-infer an IEEE 829-lite\n"
    "test plan (Purpose, Scope, Preconditions, Steps, Expected Result,\n"
    "Acceptance Criteria) from this sequence.\n"
    "\n"
    "Output format: Markdown. Fill in all six sections. Number the steps in order.\n"
    "No hallucinations — do not invent actions or UI elements that are not in the input.\n"
    "No missed steps — every step in the input must appear 1:1 in the Steps section.\n"
    "Write in American English. Be concise.\n"
)


# 3 few-shots (PLAN §"TR.5" table FS-A1 / FS-A2 / FS-A3)
FEW_SHOT_EXAMPLES: list[tuple[str, str]] = [
    (
        # FS-A1 login
        json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/login"},
            {"step": 2, "action": "fill", "target": "#email", "value": "user@example.com"},
            {"step": 3, "action": "fill", "target": "#password", "value": "secret"},
            {"step": 4, "action": "click", "target": "button[type=submit]", "value": ""},
            {"step": 5, "action": "verify", "target": "#welcome", "value": "Welcome", "condition": "text"},
        ], ensure_ascii=False, indent=2),
        (
            "## Purpose\nVerify the happy path of the login flow.\n\n"
            "## Scope\nFrom the auth page through to entering the dashboard.\n\n"
            "## Preconditions\n- A registered user credential (user@example.com / secret).\n"
            "- The login page URL is reachable.\n\n"
            "## Steps\n"
            "1. Open https://app.example.com/login.\n"
            "2. Enter user@example.com in the email field.\n"
            "3. Enter secret in the password field.\n"
            "4. Click the submit button.\n"
            "5. Verify the welcome area shows the text 'Welcome'.\n\n"
            "## Expected Result\nAfter a successful login, the dashboard's welcome message is visible.\n\n"
            "## Acceptance Criteria\nThe #welcome element renders the text 'Welcome'.\n"
        ),
    ),
    (
        # FS-A2 CRUD create
        json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/items"},
            {"step": 2, "action": "click", "target": "button#new", "value": ""},
            {"step": 3, "action": "fill", "target": "#name", "value": "Item A"},
            {"step": 4, "action": "fill", "target": "#price", "value": "1000"},
            {"step": 5, "action": "select", "target": "#category", "value": "books"},
            {"step": 6, "action": "fill", "target": "#desc", "value": "Sample"},
            {"step": 7, "action": "click", "target": "button#save", "value": ""},
            {"step": 8, "action": "verify", "target": ".item-row", "value": "Item A", "condition": "text"},
        ], ensure_ascii=False, indent=2),
        (
            "## Purpose\nVerify the new-item creation flow (CRUD-Create).\n\n"
            "## Scope\nFrom the list page → creation form → item registration → reflection in the list.\n\n"
            "## Preconditions\n- A logged-in session. Existing items may or may not be present.\n\n"
            "## Steps\n"
            "1. Open the list page at https://app.example.com/items.\n"
            "2. Click the 'New' button to enter the creation form.\n"
            "3. Enter name 'Item A', price '1000', category 'books', description 'Sample'.\n"
            "4. Click the save button.\n"
            "5. Verify the 'Item A' entry in the list.\n\n"
            "## Expected Result\nAfter saving, the new item appears in the list view.\n\n"
            "## Acceptance Criteria\nThe .item-row element shows the text 'Item A'.\n"
        ),
    ),
    (
        # FS-A3 multi-stage search
        json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/search"},
            {"step": 2, "action": "fill", "target": "#q", "value": "DSCORE"},
            {"step": 3, "action": "select", "target": "#type", "value": "doc"},
            {"step": 4, "action": "select", "target": "#sort", "value": "newest"},
            {"step": 5, "action": "click", "target": "button.next-page", "value": ""},
            {"step": 6, "action": "verify", "target": ".results-count", "value": "10", "condition": "text"},
        ], ensure_ascii=False, indent=2),
        (
            "## Purpose\nVerify combined behavior of multi-filter, sort, and pagination on search results.\n\n"
            "## Scope\nQuery entry → type/sort filters → next page → results count check.\n\n"
            "## Preconditions\n- The index contains many documents matching 'DSCORE'.\n\n"
            "## Steps\n"
            "1. Open https://app.example.com/search.\n"
            "2. Enter the query 'DSCORE'.\n"
            "3. Select the type filter 'doc'.\n"
            "4. Select sort 'newest'.\n"
            "5. Click the next-page button.\n"
            "6. Verify that the results count is '10'.\n\n"
            "## Expected Result\nThe results count on page 2 shows at least 10 entries.\n\n"
            "## Acceptance Criteria\nThe .results-count element contains the text '10'.\n"
        ),
    ),
]


@dataclass
class EnrichResult:
    markdown: str
    elapsed_ms: float
    model: str
    prompt_tokens_estimate: int
    error: Optional[str] = None


class EnrichError(RuntimeError):
    """Explicit error for the Ollama call / response-parsing stage."""


def enrich_recording(
    *,
    scenario: list[dict],
    target_url: str,
    page_title: Optional[str] = None,
    inventory_block: Optional[str] = None,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> EnrichResult:
    """Back-infer a recorded 14-DSL scenario into IEEE 829-lite Markdown.

    Args:
        scenario: list of 14-DSL step dicts (verify steps may be included).
        target_url: page context.
        page_title: page title (when present, strengthens the context).
        inventory_block: Phase 1 grounding inventory block (optional).
        ollama_url: host Ollama base URL.
        model: model name. Default gemma4:26b (overridable via env OLLAMA_MODEL).
        timeout_sec: HTTP timeout.

    Raises:
        EnrichError: HTTP failure, timeout, or response parse failure.
    """
    if not scenario:
        raise EnrichError("scenario is empty — nothing to back-infer.")

    user_prompt = _build_user_prompt(scenario, target_url, page_title, inventory_block)
    system = _build_system_prompt()
    full_prompt = system + "\n\n" + user_prompt

    started = time.time()
    try:
        res = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=timeout_sec,
        )
    except requests.Timeout as e:
        raise EnrichError(
            f"Ollama call did not finish within {timeout_sec}s."
        ) from e
    except requests.RequestException as e:
        raise EnrichError(f"Ollama HTTP communication failed: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    if res.status_code != 200:
        raise EnrichError(
            f"Ollama response code {res.status_code}: {res.text[:300]}"
        )

    try:
        body = res.json()
    except json.JSONDecodeError as e:
        raise EnrichError(f"Failed to parse Ollama response JSON: {e}") from e

    markdown = (body.get("response") or "").strip()
    if not markdown:
        raise EnrichError("Ollama response is empty.")

    # Light check that all six required sections exist — surface common omissions early.
    missing = [
        title for title in ("Purpose", "Scope", "Preconditions", "Steps", "Expected Result", "Acceptance Criteria")
        if f"## {title}" not in markdown
    ]
    if missing:
        log.warning(
            "[enricher] sections missing from response: %s (model=%s)", missing, model,
        )

    return EnrichResult(
        markdown=markdown,
        elapsed_ms=elapsed_ms,
        model=model,
        prompt_tokens_estimate=_rough_token_count(full_prompt),
    )


def _build_system_prompt() -> str:
    parts = [SYSTEM_PROMPT, "\n## Examples (3 few-shots)\n"]
    for idx, (seed, golden) in enumerate(FEW_SHOT_EXAMPLES, start=1):
        parts.append(f"### Example {idx}\n")
        parts.append("**Input sequence (JSON)**:\n```json\n")
        parts.append(seed)
        parts.append("\n```\n\n")
        parts.append("**Back-inferred result**:\n")
        parts.append(golden)
        parts.append("\n---\n")
    return "".join(parts)


def _build_user_prompt(
    scenario: list[dict],
    target_url: str,
    page_title: Optional[str],
    inventory_block: Optional[str],
) -> str:
    lines = ["## This input"]
    lines.append(f"- target_url: {target_url}")
    if page_title:
        lines.append(f"- page_title: {page_title}")
    if inventory_block:
        lines.append("\n### Page inventory (Phase 1 grounding)\n")
        lines.append(inventory_block)
    lines.append("\n### User action sequence (14-DSL)\n")
    lines.append("```json")
    lines.append(json.dumps(scenario, ensure_ascii=False, indent=2))
    lines.append("```\n")
    lines.append("\nFrom the sequence above, write an IEEE 829-lite test plan.")
    return "\n".join(lines)


def _rough_token_count(text: str) -> int:
    """Use tiktoken if available, otherwise approximate as char/4."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


# ── Item 4 (UI improvement) — codegen original ↔ LLM healed regression diff analysis ──

_DIFF_ANALYSIS_SYSTEM = """\
You are an expert reviewer of Playwright automated regression tests. Analyze
the differences between two Python scripts (the codegen original and the
LLM-healed regression) at the semantic level so the user can decide whether to
adopt this regression into the regression suite.

## Output format (Markdown — exactly 4 sections)

### 1. Key change summary
- The most important changes in 1-3 lines (selector swap / hover added / etc.).

### 2. Per-line change analysis
For each changed line:
- **L<number>**: `<original code>` → `<changed code>`
  Type: <selector swap | hover added | step removed | step added | other>
  Meaning: <why it changed — inferred healing intent>

### 3. Risk assessment
- **Determinism**: Is the new selector robust against site changes?
- **Intent match**: Does it match the user-recorded action?
- **Potential risk**: Things likely to break in a regression run.

### 4. Regression-adoption recommendation
Pick exactly one:
- ✅ **Recommended** — the diff is a clean healing and matches intent
- ⚠ **Review needed** — some changes have ambiguous intent
- ❌ **Not recommended** — possible behavior divergence from intent

Reason: 1-2 sentences.

## Rules
- Quote selectors inside code blocks verbatim (backticks).
- Do not mention unchanged lines.
- If guessing, mark it as "inferred".
- Write in American English. No filler.
"""


@dataclass
class DiffAnalysisResult:
    markdown: str
    elapsed_ms: float
    model: str
    error: Optional[str] = None


def analyze_codegen_vs_regression(
    *,
    original_py: str,
    regression_py: str,
    unified_diff: str,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> DiffAnalysisResult:
    """Use the LLM to semantically analyze the codegen original .py and the LLM-healed
    regression_test.py, returning markdown with a change summary + risk assessment +
    adoption recommendation.

    If the env var ``RECORDING_DIFF_ANALYSIS_STUB=1`` is set, return deterministic
    stub markdown without calling Ollama — used for E2E tests / environments
    where Ollama is unavailable.

    Raises:
        EnrichError: Ollama HTTP/timeout/response parse failure.
    """
    if not regression_py.strip():
        raise EnrichError("regression_test.py is empty — nothing to analyze.")

    if os.environ.get("RECORDING_DIFF_ANALYSIS_STUB") == "1":
        return DiffAnalysisResult(
            markdown=(
                "### 1. Key change summary\n"
                "- (stub) 1 selector healed\n\n"
                "### 2. Per-line change analysis\n"
                "- **L1**: stub analysis result — bypassing the actual Ollama call\n"
                "  Type: selector swap\n"
                "  Meaning: ensure determinism in the test environment\n\n"
                "### 3. Risk assessment\n"
                "- **Determinism**: this stub is deterministic\n"
                "- **Intent match**: N/A (stub)\n\n"
                "### 4. Regression-adoption recommendation\n"
                "✅ **Recommended** — happy path verified via stub.\n"
            ),
            elapsed_ms=10.0,
            model="stub:RECORDING_DIFF_ANALYSIS_STUB",
        )

    user_prompt = (
        "## Input 1 — codegen original (original.py)\n"
        "```python\n" + (original_py or "(empty)") + "\n```\n\n"
        "## Input 2 — LLM healed regression (regression_test.py)\n"
        "```python\n" + regression_py + "\n```\n\n"
        "## Input 3 — unified diff (reference)\n"
        "```diff\n" + (unified_diff or "(no diff)") + "\n```\n\n"
        "Analyze the differences between the two scripts as 4-section Markdown."
    )
    full_prompt = _DIFF_ANALYSIS_SYSTEM + "\n\n" + user_prompt

    started = time.time()
    try:
        res = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=timeout_sec,
        )
    except requests.Timeout as e:
        raise EnrichError(
            f"Ollama call did not finish within {timeout_sec}s."
        ) from e
    except requests.RequestException as e:
        raise EnrichError(f"Ollama HTTP communication failed: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    if res.status_code != 200:
        raise EnrichError(
            f"Ollama response code {res.status_code}: {res.text[:300]}"
        )
    try:
        body = res.json()
    except json.JSONDecodeError as e:
        raise EnrichError(f"Failed to parse Ollama response JSON: {e}") from e

    markdown = (body.get("response") or "").strip()
    if not markdown:
        raise EnrichError("Ollama response is empty.")

    return DiffAnalysisResult(
        markdown=markdown,
        elapsed_ms=elapsed_ms,
        model=model,
    )
