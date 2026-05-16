"""Regression guard for 3c9156e — action timeout 환경변수 ``REGRESSION_ACTION_TIMEOUT_MS``.

증상: 사이트 응답이 느린 케이스는 회귀 .py 의 action 단위 timeout
(15s 하드코딩) 도 부족. 운영자가 케이스별로 임계값 조정해야 정확한 검증 가능.

조치: 회귀 .py 의 click/select/drag/scroll 등 action timeout 도 환경변수
``REGRESSION_ACTION_TIMEOUT_MS`` 로 노출. 기본 15000ms.

본 슈트는 (1) setup 에 환경변수 read emit, (2) click 이 하드코딩 15000 대신
``_action_timeout_ms`` 변수를 timeout 으로 emit 하는지 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_emit_declares_action_timeout_env_var(emit_regression):
    """setup 에 ``REGRESSION_ACTION_TIMEOUT_MS`` 환경변수 read 가 emit 된다."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    assert "REGRESSION_ACTION_TIMEOUT_MS" in src, (
        f"action timeout 환경변수 emit 누락 — 운영자 조정 불가: {src[:300]!r}"
    )
    assert "_action_timeout_ms" in src, "_action_timeout_ms 변수 emit 누락"


@pytest.mark.unit
def test_click_emit_uses_action_timeout_env_var_not_hardcoded(emit_regression):
    """click step emit (현재 ``_safe_click`` 으로 감싸진 형태) 이 환경변수를 timeout 으로 사용한다.

    fix 3c9156e 이후, 후속 변경 (0c520c0 actionability JS fallback) 으로 click
    이 ``_safe_click(<locator>, timeout=_action_timeout_ms)`` 형태로 감싸져
    emit. 본 assertion 은 그 timeout kwarg 가 환경변수 ``_action_timeout_ms``
    를 가리키는지를 가드.
    """
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    # 현재 emit 형태 — _safe_click(..., timeout=_action_timeout_ms)
    assert "timeout=_action_timeout_ms" in src, (
        f"click timeout 이 환경변수 대신 하드코딩 회귀: {src!r}"
    )
    # 옛 하드코딩 timeout=15000 (action 자리, _safe_click 정의 외부) 회귀 방지.
    # _safe_click 정의 안에는 timeout 파라미터 이름으로 timeout 이 들어가니,
    # try 블록 안의 click step 라인 한정 검사.
    click_step_lines = [
        ln for ln in src.splitlines()
        if "_safe_click(page.locator" in ln or "page.locator" in ln and ".click(timeout=" in ln
    ]
    for ln in click_step_lines:
        assert "timeout=15000" not in ln, (
            f"click step 에 하드코딩 timeout=15000 회귀: {ln!r}"
        )
