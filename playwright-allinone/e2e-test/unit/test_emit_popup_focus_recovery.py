"""Regression guard for 01819c6 — popup 직후 본 page focus 복구 emit.

증상: 녹화 중 새 창(팝업) 띄운 직후 곧바로 원래 페이지에서 메뉴 hover 가 필요한
시나리오에서 회귀 .py 가 1.5s 안에 hover 못 마쳐 timeout. LLM 실행에선 통과하므로
export 단의 문제.

조치: popup 직후 *다음 step 이 다른 page (보통 원본 page)* 를 대상으로 하면
``<page_var>.bring_to_front()`` + ``wait_for_timeout(200)`` 를 emit 해 background
상태 회피.

본 슈트는 popup_to 직후 main page step 이 따라올 때 ``bring_to_front()`` 가 emit
되는지 가드. popup 안에서 동작이 이어지면 bring_to_front 가 *안* 들어가는지도
회귀 격리.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_popup_then_main_page_step_emits_bring_to_front(emit_regression):
    """popup 직후 main page step → ``page.bring_to_front()`` emit."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        # popup 생성.
        {"step": 2, "action": "click", "target": "#open-popup", "value": "",
         "popup_to": "page1"},
        # 다음 step 은 본 page — focus 복구 필요.
        {"step": 3, "action": "click", "target": "#main-btn", "value": ""},
    ])
    assert src is not None
    assert "page.bring_to_front()" in src, (
        f"popup 직후 본 page focus 복구 emit 누락 — background page hover "
        f"timeout 회귀: {src!r}"
    )


@pytest.mark.unit
def test_popup_then_popup_step_does_not_emit_bring_to_front(emit_regression):
    """popup 안에서 동작이 이어지면 ``bring_to_front`` 가 *안* emit — 회귀 격리."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#open-popup", "value": "",
         "popup_to": "page1"},
        # popup 안에서 동작 — focus 복구 불필요.
        {"step": 3, "action": "click", "target": "#inside", "value": "",
         "page": "page1"},
    ])
    assert src is not None
    # popup 안 동작 흐름에선 본 page focus 복구가 emit 되면 안 됨.
    assert "page.bring_to_front()" not in src, (
        f"popup 안 동작 흐름에서 본 page bring_to_front 가 잘못 emit — "
        f"focus 가 popup 에서 원본으로 넘어가버려 popup 안 element 못 찾는 회귀: "
        f"{src!r}"
    )
