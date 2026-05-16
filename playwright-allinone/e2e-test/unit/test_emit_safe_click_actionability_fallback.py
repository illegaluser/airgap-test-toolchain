"""Regression guard for 0c520c0 — actionability 거부 시 JS dispatch click 폴백.

증상: 같은 시나리오가 LLM 모드에선 성공, 회귀 .py 로 돌리면 마지막 단계 클릭이
15s timeout 으로 실패. closing layer / transition 잔여물 / line-height:0 같은
computed style 이 hit-test 를 막아 Playwright actionability 영원히 거부.

조치 (emit 측면): 모든 click 을 ``_safe_click`` 헬퍼로 감쌈. 헬퍼 안:
  - 정상 click 시도
  - actionability 거부 (특정 메시지 매칭) 시 — 대상이 a / button / clickable
    role 면 ``locator.evaluate('el => el.click()')`` JS dispatch 폴백.
  - 비-인터랙티브 요소면 raise (false-positive PASS 방지).

본 슈트는 (1) 헬퍼 정의 emit, (2) click step 이 ``_safe_click`` 사용, (3) 헬퍼
가 안전 태그 가드 + JS dispatch 폴백 로직 포함을 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_emit_defines_safe_click_helper(emit_regression):
    """setup 에 ``_safe_click`` 헬퍼 정의 emit."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    assert "def _safe_click(" in src, (
        f"_safe_click 헬퍼 정의 emit 누락: {src[:400]!r}"
    )


@pytest.mark.unit
def test_click_step_uses_safe_click_wrapper(emit_regression):
    """click step 이 ``.click(`` 직접이 아닌 ``_safe_click(`` 으로 감싸짐."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    # try 블록 안 click step 라인이 _safe_click 으로 호출.
    click_step_lines = [
        ln for ln in src.splitlines()
        if "_safe_click(page.locator" in ln
    ]
    assert click_step_lines, (
        f"_safe_click wrapper 사용 안 함 — 직접 .click() 회귀: {src!r}"
    )


@pytest.mark.unit
def test_safe_click_helper_has_actionability_error_pattern_match(emit_regression):
    """``_safe_click`` 안에 Playwright actionability 거부 메시지 패턴 매칭 포함."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    # 헬퍼 가 처리하는 actionability 거부 메시지 시그니처들 (실행기 mirror).
    expected_signatures = (
        "not visible",
        "outside of the viewport",
        "intercepts pointer events",
    )
    for sig in expected_signatures:
        assert sig in src, (
            f"actionability 거부 메시지 '{sig}' 매칭 누락 — height:0 / "
            f"closing-layer 회귀 fallback 조건 결손: {src!r}"
        )


@pytest.mark.unit
def test_safe_click_helper_falls_back_to_js_dispatch_click(emit_regression):
    """``_safe_click`` 이 JS dispatch (``el.click()``) 폴백 포함."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    # JS dispatch — locator.evaluate('el => el.click()').
    assert "el.click()" in src, (
        f"JS dispatch click 폴백 emit 누락 — height:0 anchor 회귀 (27c0ecc, "
        f"ktds.com GNB) 재발 가능: {src!r}"
    )
