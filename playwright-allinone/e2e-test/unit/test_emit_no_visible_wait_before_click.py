"""Regression guard for c57f537 — click 앞 ``wait_for(state='visible')`` 제거.

사용자 보고 (6802f8a6faef step 17 — "모두의 AI 실험실"): 실행기는 PASS 한
시나리오가 회귀 .py 재생 시 메뉴 아이템 클릭에서 "보이지 않음" 으로 15s 대기.

원인: 직전 fix 가 click 앞에 ``wait_for(state='visible')`` 박았는데, 이 대기는
viewport *안* 가시성을 요구. 화면 밖 요소는 자동 스크롤 없이는 영영 visible
판정 안 됨 → 회귀에서 FAIL.

조치: ``wait_for(state='visible')`` 제거. click 의 내장 auto-wait + auto-scroll
+ 재시도에 모든 안전망 위임.

본 슈트는 emit 된 click 라인에 viewport-bound visible wait 이 *되돌아오지*
않도록 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_click_emit_has_no_viewport_visible_wait_prepended(emit_regression):
    """click step emit 에 buggy ``_loc = ...; _loc.wait_for(state='visible')`` 가 다시 안 들어간다.

    회귀 c57f537 의 버그 패턴은 click 직전에 ``_loc`` 라는 임시 변수에 locator 를
    바인딩하고 그 locator 에 ``wait_for(state='visible')`` 를 호출한 뒤 click 하는
    3-line 구조였다. 본 슈트는 그 임시 변수 패턴이 다시 등장하지 않는지를 가드.

    참고: 별도 lookahead wait (50868fa) 의 ``next-step.wait_for(state='visible',
    timeout=_step_wait_ms)`` 와는 의도/대상이 다르므로 본 assertion 은 그건
    허용한다. 본 assertion 의 대상은 *현재 step click 의 같은 locator* 에 대한
    prepend wait 한정.
    """
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None

    # click 자체는 emit 됨 (현재는 _safe_click 으로 감싸진 형태).
    assert "_safe_click(" in src or ".click(timeout=" in src, (
        f"click emit 자체가 누락: {src!r}"
    )

    # 버그 패턴 — ``_loc = `` 임시 변수 자체가 다시 등장하면 안 됨.
    # c57f537 의 buggy 3-line 패턴 (``_loc = ...; _loc.wait_for(state='visible',
    # timeout=15000); _loc.click(timeout=15000)``) 의 시그니처는 ``_loc = `` 임시
    # 변수. 그 변수가 안 보이면 버그 패턴 자체가 부재.
    assert "_loc = " not in src, (
        "회귀 재발 — click 앞 ``_loc = <locator>; _loc.wait_for(state='visible',"
        " ...); _loc.click(...)`` 3-line 버그 패턴이 되돌아옴. viewport 외 요소가"
        " 영영 visible 판정 안 돼 15s timeout"
    )
