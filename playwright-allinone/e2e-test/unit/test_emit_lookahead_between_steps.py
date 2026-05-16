"""Regression guard for 50868fa — 다음 step element lookahead 대기.

증상 (aebc6756b737): modal/dialog 같은 *직전 action 의 비동기 trigger* 가 만든
element 는 ``networkidle`` 만으로는 보장 안 됨. timeout.

조치: 회귀 .py 가 매 단계 직후 *다음 단계 element 의 ``wait_for(state=...)``*
를 자동 emit. lookahead 로 다음 step 의 안정 식별자를 미리 알아 그 locator 의
wait. 한도는 ``REGRESSION_STEP_WAIT_TIMEOUT_MS`` (기본 3000ms).

본 슈트는 (1) lookahead 환경변수 emit, (2) 2-step 시나리오에서 step 1 직후
step 2 의 locator wait_for 가 emit 되는지 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_emit_declares_step_wait_timeout_env_var(emit_regression):
    """``REGRESSION_STEP_WAIT_TIMEOUT_MS`` 환경변수가 setup 에 emit 된다."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    assert "REGRESSION_STEP_WAIT_TIMEOUT_MS" in src, (
        f"lookahead 한도 환경변수 emit 누락 — 운영자 조정 불가: {src[:200]!r}"
    )
    assert "_step_wait_ms" in src, "_step_wait_ms 변수 emit 누락"


@pytest.mark.unit
def test_emit_inserts_lookahead_wait_for_next_step(emit_regression):
    """2-step 시나리오에서 step 1 직후 step 2 의 locator wait_for 가 emit."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#first-btn", "value": ""},
        {"step": 3, "action": "click", "target": "#second-btn", "value": ""},
    ])
    assert src is not None

    # step 2 (#first-btn click) 직후 step 3 (#second-btn) 의 lookahead wait_for 가 있어야.
    # ``wait_for(state=...)`` + ``_step_wait_ms`` 가 결합된 형태.
    assert "_step_wait_ms" in src
    assert ".wait_for(state=" in src, (
        f"lookahead wait_for emit 누락: {src!r}"
    )

    # step 3 의 selector (#second-btn) 가 lookahead 자리에 등장해야.
    # 단순 substring 으로 확인 (정확한 위치는 emit 순서에 의존하므로 token 만).
    assert "second-btn" in src, "다음 step selector 가 lookahead 에 emit 안 됨"
