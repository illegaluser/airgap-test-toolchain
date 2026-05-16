"""Regression guard for 8077ee6 — setup 의 native dialog auto-dismiss handler.

사용자 보고 (aebc6756b737): 실행기 PASS 한 시나리오의 회귀 .py 가 도중 멈춤.

원인: 실행기는 시작 시 모든 page 에 native dialog(alert/confirm/beforeunload/
prompt) auto-dismiss 핸들러를 자동 등록 (executor.py:412-446). 자동 생성기는
같은 핸들러 emit 안 함 → raw .py 가 dialog 위에 가려져 후속 selector 못 잡거나
click 자체가 멈춤.

조치: 회귀 .py setup 블록에 dialog auto-dismiss 핸들러 emit. 메인 page + 이후
열리는 popup page 모두에 자동 등록.

본 슈트는 (1) ``_auto_dismiss_dialog`` 헬퍼 정의, (2) 메인 page 등록,
(3) popup page 자동 등록 (context.on('page', ...)) 를 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_emit_defines_auto_dismiss_dialog_helper(emit_regression):
    """setup 에 ``_auto_dismiss_dialog`` 핸들러가 emit 된다."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
    ])
    assert src is not None
    assert "_auto_dismiss_dialog" in src, f"핸들러 emit 누락: {src[:400]!r}"
    assert "d.dismiss()" in src, "dismiss 호출 누락 — 핸들러가 no-op 일 수 있음"


@pytest.mark.unit
def test_emit_registers_dialog_handler_on_main_page(emit_regression):
    """메인 ``page`` 에 dialog 핸들러 등록 emit."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
    ])
    assert src is not None
    assert "page.on('dialog', _auto_dismiss_dialog)" in src, (
        f"메인 page dialog 핸들러 등록 누락: {src!r}"
    )


@pytest.mark.unit
def test_emit_registers_dialog_handler_for_future_popup_pages(emit_regression):
    """``context.on('page', ...)`` 로 향후 열리는 popup 에 자동 등록."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
    ])
    assert src is not None
    assert "context.on('page'" in src, (
        f"context-level page hook 누락 — popup 의 dialog 가 미처리됨: {src!r}"
    )
    # popup page 가 열릴 때 dialog 핸들러를 등록하는 패턴.
    assert "_p.on('dialog'" in src or "p.on('dialog'" in src, (
        "popup page 에 dialog 핸들러 자동 등록 누락"
    )
