"""TR.6 — Doc-DSL ↔ Recording-DSL semantic comparison (R-Plus).

Design: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.6"

Key decision — handling input asymmetry:
  codegen does not emit verify / mock_status / mock_data. The doc-DSL's
  verify/mock_* are therefore excluded from LCS alignment and put into the
  separate "intent-only" category.

5 categories:
  - exact          : exact match (action + role/target + name/value all match)
  - value_diff     : same intent, different value (action and target identical, value differs)
  - missing        : present in doc, absent in recording (alignable actions only)
  - extra          : present in recording, absent in doc
  - intent_only    : doc's verify/mock_* (intent shown because of codegen asymmetry)
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Alignable actions — actions codegen naturally emits
ALIGNABLE_ACTIONS = frozenset({
    "navigate", "click", "fill", "press", "select", "check",
    "hover", "drag", "upload", "scroll", "wait",
})

# Doc-only — intent markers, not user actions
INTENT_ONLY_ACTIONS = frozenset({
    "verify", "mock_status", "mock_data",
})

# Fuzzy match threshold (PLAN §"TR.6 comparison algorithm")
DEFAULT_FUZZY_THRESHOLD = 0.7


@dataclass
class NormalizedStep:
    """A step normalized for LCS alignment."""

    index: int           # position in the original scenario (0-based)
    action: str
    target: str
    value: str
    raw: dict            # the original step dict


@dataclass
class DiffEntry:
    category: str        # exact / value_diff / missing / extra / intent_only
    doc_step: Optional[NormalizedStep] = None
    rec_step: Optional[NormalizedStep] = None
    note: str = ""


@dataclass
class CompareResult:
    entries: list[DiffEntry] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    threshold_used: float = DEFAULT_FUZZY_THRESHOLD


# ── Normalization ────────────────────────────────────────────────────────────

def normalize(scenario: list[dict]) -> list[NormalizedStep]:
    """Original 14-DSL step list → NormalizedStep list.

    target / value are lowercased and stripped to make comparison friendlier;
    action is kept as is.
    """
    out: list[NormalizedStep] = []
    for i, st in enumerate(scenario):
        out.append(NormalizedStep(
            index=i,
            action=st.get("action", ""),
            target=str(st.get("target", "")).strip().lower(),
            value=str(st.get("value", "")).strip(),
            raw=st,
        ))
    return out


def split_alignable(steps: list[NormalizedStep]) -> tuple[list[NormalizedStep], list[NormalizedStep]]:
    """Split into alignable and intent-only."""
    alignable: list[NormalizedStep] = []
    intent: list[NormalizedStep] = []
    for s in steps:
        if s.action in ALIGNABLE_ACTIONS:
            alignable.append(s)
        elif s.action in INTENT_ONLY_ACTIONS:
            intent.append(s)
        else:
            # Unknown action — treat as alignable so missing/extra classification still works
            alignable.append(s)
    return alignable, intent


# ── LCS + fuzzy matching ────────────────────────────────────────────────────

def _step_match_score(a: NormalizedStep, b: NormalizedStep) -> float:
    """Match score between two steps (0.0 ~ 1.0).

    0 if actions differ. Otherwise target ratio + value bonus.
    """
    if a.action != b.action:
        return 0.0
    if a.target == b.target and a.value == b.value:
        return 1.0
    target_ratio = difflib.SequenceMatcher(None, a.target, b.target).ratio()
    value_match = 1.0 if a.value == b.value else 0.0
    # action-match weight 0.3 + target 0.5 + value 0.2
    return 0.3 + 0.5 * target_ratio + 0.2 * value_match


def lcs_align(
    doc: list[NormalizedStep], rec: list[NormalizedStep], *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> list[tuple[Optional[int], Optional[int]]]:
    """LCS alignment. Returns a list of (doc_idx, rec_idx) pairs.

    The other side is None when only one side is present (missing / extra).
    Matches below the threshold do not advance the LCS.
    """
    n, m = len(doc), len(rec)
    # dp[i][j] = max match-score sum of doc[:i] vs rec[:j]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    bt = [[None] * (m + 1) for _ in range(n + 1)]  # backtrace: 'd'/'r'/'m'

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score = _step_match_score(doc[i - 1], rec[j - 1])
            if score >= threshold and dp[i - 1][j - 1] + score >= max(dp[i - 1][j], dp[i][j - 1]):
                dp[i][j] = dp[i - 1][j - 1] + score
                bt[i][j] = "m"  # match
            elif dp[i - 1][j] >= dp[i][j - 1]:
                dp[i][j] = dp[i - 1][j]
                bt[i][j] = "d"  # doc-only (missing)
            else:
                dp[i][j] = dp[i][j - 1]
                bt[i][j] = "r"  # rec-only (extra)

    # backtrace
    pairs: list[tuple[Optional[int], Optional[int]]] = []
    i, j = n, m
    while i > 0 and j > 0:
        op = bt[i][j]
        if op == "m":
            pairs.append((i - 1, j - 1))
            i -= 1; j -= 1
        elif op == "d":
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    while i > 0:
        pairs.append((i - 1, None)); i -= 1
    while j > 0:
        pairs.append((None, j - 1)); j -= 1

    pairs.reverse()
    return pairs


# ── Comparison entry point ──────────────────────────────────────────────────

def compare(
    doc_dsl: list[dict], rec_dsl: list[dict], *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> CompareResult:
    """Semantically compare the two scenarios; return a DiffEntry list + per-category counts."""
    doc_n = normalize(doc_dsl)
    rec_n = normalize(rec_dsl)

    doc_align, doc_intent = split_alignable(doc_n)
    rec_align, _rec_intent = split_alignable(rec_n)
    # rec's intent steps come from manual Assertion-add via the UI, so they are classified as alignable.

    pairs = lcs_align(doc_align, rec_align, threshold=threshold)

    entries: list[DiffEntry] = []
    counts: dict[str, int] = {
        "exact": 0, "value_diff": 0, "missing": 0, "extra": 0, "intent_only": 0,
    }

    for di, ri in pairs:
        d_step = doc_align[di] if di is not None else None
        r_step = rec_align[ri] if ri is not None else None
        if d_step is not None and r_step is not None:
            score = _step_match_score(d_step, r_step)
            if score >= 0.999:
                cat = "exact"
            elif d_step.action == r_step.action and d_step.target == r_step.target:
                cat = "value_diff"
            elif score >= threshold:
                # action+target differ but the fuzzy match passes — classify as value_diff
                cat = "value_diff"
            else:
                # LCS results below threshold are effectively missing/extra
                cat = "value_diff"
            entries.append(DiffEntry(
                category=cat, doc_step=d_step, rec_step=r_step,
                note=f"score={score:.2f}",
            ))
            counts[cat] += 1
        elif d_step is not None:
            entries.append(DiffEntry(
                category="missing", doc_step=d_step,
                note="present in doc, absent in recording",
            ))
            counts["missing"] += 1
        elif r_step is not None:
            entries.append(DiffEntry(
                category="extra", rec_step=r_step,
                note="present in recording, absent in doc",
            ))
            counts["extra"] += 1

    # doc's intent_only (verify/mock_*) is appended separately at the end
    for s in doc_intent:
        entries.append(DiffEntry(
            category="intent_only", doc_step=s,
            note="codegen asymmetry — doc's verify/mock_* (not subject to alignment)",
        ))
        counts["intent_only"] += 1

    return CompareResult(entries=entries, counts=counts, threshold_used=threshold)


# ── HTML report ─────────────────────────────────────────────────────────────

CATEGORY_LABEL = {
    "exact": ("Exact", "#d4edda", "#155724"),
    "value_diff": ("Value diff", "#fff3cd", "#856404"),
    "missing": ("Missing (doc only)", "#f8d7da", "#721c24"),
    "extra": ("Extra (recording only)", "#cce5ff", "#004085"),
    "intent_only": ("Intent only", "#e2e3e5", "#6c757d"),
}


def render_html(result: CompareResult, *, doc_label: str = "doc-DSL", rec_label: str = "recording-DSL") -> str:
    """Render the compare result as a side-by-side HTML report."""
    rows = []
    for e in result.entries:
        label, bg, fg = CATEGORY_LABEL.get(
            e.category, (e.category, "#fff", "#000"),
        )
        d_html = _step_html(e.doc_step)
        r_html = _step_html(e.rec_step)
        rows.append(
            f'<tr style="background:{bg};color:{fg}">'
            f'<td><strong>{label}</strong></td>'
            f'<td>{d_html}</td><td>{r_html}</td>'
            f'<td class="muted">{_html_escape(e.note)}</td>'
            f'</tr>'
        )

    summary_items = " · ".join(
        f"<strong>{CATEGORY_LABEL[k][0]}</strong>: {v}"
        for k, v in result.counts.items()
    )

    intent_note = (
        '<p class="note">⚠ codegen captures only user actions. The doc-DSL\'s '
        'verify / mock_* are excluded from the comparison and shown separately as "Intent only".</p>'
        if result.counts.get("intent_only", 0) > 0 else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Doc ↔ Recording comparison report</title>
<style>
body {{ font: 14px/1.5 -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; margin: 24px; color: #1d1d1f; }}
h1 {{ margin: 0 0 8px; font-size: 1.4rem; }}
.summary {{ background: #f5f5f7; padding: 10px 14px; border-radius: 6px; margin: 12px 0 16px; }}
.note {{ background: #fff3cd; padding: 10px 14px; border-radius: 6px; color: #856404; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 8px 12px; vertical-align: top; border-bottom: 1px solid #e1e4e8; text-align: left; }}
th {{ background: #f5f5f7; }}
.muted {{ color: #6e6e73; font-size: 0.85rem; }}
code {{ background: #f0f0f2; padding: 1px 6px; border-radius: 4px; font-size: 0.88em; }}
.action {{ font-weight: 600; }}
</style></head>
<body>
<h1>Doc ↔ Recording comparison</h1>
<div class="summary">{summary_items}</div>
{intent_note}
<table>
<thead><tr><th>Category</th><th>{_html_escape(doc_label)}</th><th>{_html_escape(rec_label)}</th><th>note</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
<p class="muted">threshold={result.threshold_used:.2f} · DSCORE Recording Service R-Plus TR.6</p>
</body></html>
"""


def _step_html(step: Optional[NormalizedStep]) -> str:
    if step is None:
        return '<span class="muted">—</span>'
    return (
        f'<span class="action">{_html_escape(step.action)}</span> '
        f'<code>{_html_escape(step.target or "(no target)")}</code> '
        f'= <code>{_html_escape(step.value or "(no value)")}</code>'
    )


def _html_escape(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
