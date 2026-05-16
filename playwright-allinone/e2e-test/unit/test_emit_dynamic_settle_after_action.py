"""Regression guard for 5c1134f — 각 action 직후 dynamic settle.

증상 (사용자 보고): 동일 시나리오가 실행기에서는 마지막까지 PASS, 회귀 .py
에서는 중간에 멈춤. 네트워크 지연 / SPA reflow 가 약간 늦으면 다음 단계 click
이 element 미생성 상태에서 들어가 timeout.

조치: 회귀 .py 의 setup 에 ``_settle(p)`` 헬퍼 emit (``wait_for_load_state(
'networkidle', timeout=N)``). 각 action 직후 + popup 트리거 직후 자동 호출.

본 슈트는 (1) ``_settle`` 헬퍼 정의 emit, (2) networkidle 사용, (3) click 같은
일반 action 직후 ``_settle(page)`` 호출 emit 을 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_emit_defines_settle_helper_with_networkidle(emit_regression):
    """setup 에 ``_settle(p)`` 헬퍼가 ``networkidle`` 으로 emit 된다."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    assert "def _settle(" in src, f"_settle 헬퍼 emit 누락: {src[:300]!r}"
    assert "networkidle" in src, "_settle 가 networkidle 을 안 씀 — 단순 sleep 회귀 의심"


@pytest.mark.unit
def test_emit_calls_settle_after_each_action_step(emit_regression):
    """click step 직후 ``_settle(page)`` 호출이 emit 된다."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    # navigate + click → 적어도 1회 이상 _settle(page) 호출.
    settle_calls = src.count("_settle(page)")
    assert settle_calls >= 1, (
        f"_settle(page) 호출이 action 직후 emit 안 됨 (count={settle_calls}). "
        f"네트워크 지연/SPA reflow 회귀 가능"
    )
