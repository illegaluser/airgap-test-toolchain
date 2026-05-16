"""Regression guard for e8ebd83 — select 액션 3-전략 fallback (positional → value → label).

증상: 회귀 자동 실행이 드롭다운 선택 단계에서 항상 실패.
원인: 회귀가 ``label=`` 한 가지만 시도. 시나리오 value 가 내부 코드 ("01") 인
케이스는 라벨 매칭 0건 → fail.
조치: executor ``_do_select`` 와 동일한 3-전략 mirror — positional(no kwargs) →
``value=`` → ``label=`` 순서로 try/except 폴백.

본 슈트는 emit src 의 select 블록이 3-전략을 *그 순서로* 시도하는지 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_select_emit_uses_three_strategy_fallback_tuple(emit_regression):
    """select 의 fallback 튜플에 positional/value/label 3 전략이 *그 순서로* emit."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "select", "target": "#dropdown", "value": "01"},
    ])
    assert src is not None

    # for _kw in (...) 튜플 라인 찾기
    candidates = [ln for ln in src.splitlines() if "for _kw in" in ln]
    assert candidates, f"fallback 튜플 emit 자체가 누락: {src!r}"
    tuple_line = candidates[0]

    pos_idx = tuple_line.find("({},")
    val_idx = tuple_line.find("'value':")
    lbl_idx = tuple_line.find("'label':")

    assert pos_idx >= 0, f"positional 전략(빈 dict) emit 누락: {tuple_line!r}"
    assert val_idx >= 0, f"value= 전략 emit 누락: {tuple_line!r}"
    assert lbl_idx >= 0, f"label= 전략 emit 누락: {tuple_line!r}"
    assert pos_idx < val_idx < lbl_idx, (
        f"fallback 순서가 positional → value → label 가 아님: {tuple_line!r}"
    )


@pytest.mark.unit
def test_select_emit_wraps_strategies_with_try_except_chain(emit_regression):
    """3-전략 각각이 try/except 로 묶이고 마지막에 _last_err 재발 — 모두 실패 시 raise."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "select", "target": "#dropdown", "value": "01"},
    ])
    assert src is not None
    assert "_last_err = None" in src, "에러 누적 변수 _last_err 초기화 누락"
    assert "raise _last_err" in src, "모든 전략 실패 시 마지막 에러 raise 누락"
