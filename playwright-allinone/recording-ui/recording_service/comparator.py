"""TR.6 — Doc-DSL ↔ Recording-DSL 의미 비교 (R-Plus).

설계: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.6"

핵심 결정 — 입력 비대칭 처리:
  codegen 은 verify / mock_status / mock_data 를 emit 하지 않는다. 따라서
  doc-DSL 의 verify/mock_* 는 LCS 정렬 대상에서 제외하고 별도 카테고리
  "녹화 외 의도(intent-only)" 로 표시한다.

5분류:
  - exact          : 정확한 일치 (action + role/target + name/value 모두 매칭)
  - value_diff     : 의미는 같으나 값만 다름 (action·target 동일, value 차이)
  - missing        : doc 에 있고 recording 에 없음 (정렬 대상 액션 한정)
  - extra          : recording 에 있고 doc 에 없음
  - intent_only    : doc 의 verify/mock_* (codegen 비대칭으로 인한 의도 표시)
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# 정렬 대상 액션 — codegen 이 자연 emit 하는 행위
ALIGNABLE_ACTIONS = frozenset({
    "navigate", "click", "fill", "press", "select", "check",
    "hover", "drag", "upload", "scroll", "wait",
})

# doc 전용 — 사용자 행동이 아닌 의도 표시
INTENT_ONLY_ACTIONS = frozenset({
    "verify", "mock_status", "mock_data",
})

# fuzzy 매칭 임계값 (PLAN §"TR.6 비교 알고리즘")
DEFAULT_FUZZY_THRESHOLD = 0.7


@dataclass
class NormalizedStep:
    """LCS 정렬용으로 정규화된 step."""

    index: int           # 원본 시나리오 안의 위치 (0-base)
    action: str
    target: str
    value: str
    raw: dict            # 원본 step dict


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


# ── 정규화 ───────────────────────────────────────────────────────────────────

def normalize(scenario: list[dict]) -> list[NormalizedStep]:
    """원본 14-DSL step 리스트 → NormalizedStep 리스트.

    target / value 는 lowercase·strip 으로 비교 친화화. action 은 그대로.
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
    """정렬 대상 / 의도 전용 분리."""
    alignable: list[NormalizedStep] = []
    intent: list[NormalizedStep] = []
    for s in steps:
        if s.action in ALIGNABLE_ACTIONS:
            alignable.append(s)
        elif s.action in INTENT_ONLY_ACTIONS:
            intent.append(s)
        else:
            # 알 수 없는 action — alignable 로 취급해 누락/추가 분류 가능하게
            alignable.append(s)
    return alignable, intent


# ── LCS + fuzzy 매칭 ─────────────────────────────────────────────────────────

def _step_match_score(a: NormalizedStep, b: NormalizedStep) -> float:
    """두 step 의 매칭 점수 (0.0 ~ 1.0).

    action 다르면 0. 같으면 target ratio + value bonus.
    """
    if a.action != b.action:
        return 0.0
    if a.target == b.target and a.value == b.value:
        return 1.0
    target_ratio = difflib.SequenceMatcher(None, a.target, b.target).ratio()
    value_match = 1.0 if a.value == b.value else 0.0
    # action 일치 가중치 0.3 + target 0.5 + value 0.2
    return 0.3 + 0.5 * target_ratio + 0.2 * value_match


def lcs_align(
    doc: list[NormalizedStep], rec: list[NormalizedStep], *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> list[tuple[Optional[int], Optional[int]]]:
    """LCS 정렬. (doc_idx, rec_idx) 페어 리스트 반환.

    한쪽만 있으면 다른 쪽은 None (missing / extra).
    threshold 미만 매칭은 LCS 진행 안 함.
    """
    n, m = len(doc), len(rec)
    # dp[i][j] = doc[:i] 과 rec[:j] 의 최대 매칭 점수 합
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


# ── 비교 본 함수 ─────────────────────────────────────────────────────────────

def compare(
    doc_dsl: list[dict], rec_dsl: list[dict], *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> CompareResult:
    """두 시나리오를 의미 비교해 DiffEntry 리스트 + 카테고리 카운트 반환."""
    doc_n = normalize(doc_dsl)
    rec_n = normalize(rec_dsl)

    doc_align, doc_intent = split_alignable(doc_n)
    rec_align, _rec_intent = split_alignable(rec_n)
    # rec 의 intent 는 사용자가 Assertion 추가 UI 로 직접 넣은 경우 → alignable 로 분류된다.

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
                # action+target 동일은 아니지만 fuzzy match 통과 — value_diff 로 분류
                cat = "value_diff"
            else:
                # threshold 통과 못 한 LCS 결과는 사실상 missing/extra
                cat = "value_diff"
            entries.append(DiffEntry(
                category=cat, doc_step=d_step, rec_step=r_step,
                note=f"score={score:.2f}",
            ))
            counts[cat] += 1
        elif d_step is not None:
            entries.append(DiffEntry(
                category="missing", doc_step=d_step,
                note="doc 에 있고 recording 에 없음",
            ))
            counts["missing"] += 1
        elif r_step is not None:
            entries.append(DiffEntry(
                category="extra", rec_step=r_step,
                note="recording 에 있고 doc 에 없음",
            ))
            counts["extra"] += 1

    # doc 의 intent_only (verify/mock_*) 는 별도 분류로 끝에 추가
    for s in doc_intent:
        entries.append(DiffEntry(
            category="intent_only", doc_step=s,
            note="codegen 비대칭 — doc 의 verify/mock_* (정렬 대상 외)",
        ))
        counts["intent_only"] += 1

    return CompareResult(entries=entries, counts=counts, threshold_used=threshold)


# ── HTML 리포트 ──────────────────────────────────────────────────────────────

CATEGORY_LABEL = {
    "exact": ("정확", "#d4edda", "#155724"),
    "value_diff": ("값 차이", "#fff3cd", "#856404"),
    "missing": ("누락 (doc only)", "#f8d7da", "#721c24"),
    "extra": ("추가 (recording only)", "#cce5ff", "#004085"),
    "intent_only": ("녹화 외 의도", "#e2e3e5", "#6c757d"),
}


def render_html(result: CompareResult, *, doc_label: str = "doc-DSL", rec_label: str = "recording-DSL") -> str:
    """compare 결과를 사이드바이사이드 HTML 리포트로 렌더."""
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
        '<p class="note">⚠ codegen 은 사용자 행동만 캡처합니다. doc-DSL 의 '
        'verify / mock_* 는 비교 대상에서 제외되어 별도 "녹화 외 의도" 로 표시됩니다.</p>'
        if result.counts.get("intent_only", 0) > 0 else ""
    )

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>Doc ↔ Recording 비교 리포트</title>
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
<h1>Doc ↔ Recording 비교</h1>
<div class="summary">{summary_items}</div>
{intent_note}
<table>
<thead><tr><th>분류</th><th>{_html_escape(doc_label)}</th><th>{_html_escape(rec_label)}</th><th>note</th></tr></thead>
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
