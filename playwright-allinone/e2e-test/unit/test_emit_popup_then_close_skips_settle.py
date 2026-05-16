"""Regression guard for a2645f1 — popup 직후 close 시 settle emit 생략.

증상: 녹화에서 새 창을 열고 바로 그 창을 닫는 흐름의 회귀 .py 가 새 창에 대해
``_settle`` (networkidle 대기) 를 한 번 거치고 닫음. 외부 사이트는 networkidle
에 도달 못 하는 경우가 많아 3s timeout → Playwright 트레이스에 step FAIL 로
박혀 결과 화면이 실패로 보이는 회귀.

조치: 새 창을 띄운 직후 *다음 단계가 같은 창의 close* 면 그 사이의 settle
emit 자체를 생략. 어차피 닫을 창이라 settle 결과는 버려지는 낭비.

본 슈트는 emit 출력에 popup_to 직후 close 가 따라올 때 ``_settle(<popup_var>)``
호출이 *emit 안 되는지* 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_popup_then_close_skips_settle_emit(emit_regression):
    """popup_to 직후 close 가 따라오면 그 popup 의 ``_settle`` 호출 emit 안 됨."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        # popup 트리거 — step 2 가 page1 새 창 생성.
        {"step": 2, "action": "click", "target": "#open-popup", "value": "",
         "popup_to": "page1"},
        # 곧장 page1 닫기 — 그 사이 settle emit 되어선 안 됨.
        {"step": 3, "action": "close", "target": "", "value": "", "page": "page1"},
    ])
    assert src is not None

    # popup_to 알리어스 (`page1`) 의 _settle 호출이 emit 0건.
    # close 가 직후 따라오므로 settle 결과는 버려지는 낭비 + 외부 사이트
    # networkidle 미도달 시 트레이스 FAIL 회귀 차단.
    assert "_settle(page1)" not in src, (
        f"popup 직후 close 시 _settle(page1) emit 됨 — 트레이스 FAIL 회귀 재발: "
        f"{src!r}"
    )


@pytest.mark.unit
def test_popup_then_other_action_still_emits_settle(emit_regression):
    """popup_to 직후 close 가 *아닌* 다른 동작이면 ``_settle`` 가 *유지* 된다 (회귀 격리)."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#open-popup", "value": "",
         "popup_to": "page1"},
        # popup 안에서 추가 동작 — settle 유지되어야.
        {"step": 3, "action": "click", "target": "#inside", "value": "",
         "page": "page1"},
    ])
    assert src is not None
    # popup 안에서 동작이 이어지는 흐름은 _settle(page1) 가 정상 emit.
    assert "_settle(page1)" in src, (
        f"popup 후속 동작 흐름에서 _settle(page1) emit 누락 — 회귀 격리 실패: "
        f"{src!r}"
    )
