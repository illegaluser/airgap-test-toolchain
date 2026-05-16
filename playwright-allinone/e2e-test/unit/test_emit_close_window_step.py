"""Regression guard for 0e59953 — '창 닫기' 시나리오 단계 보존 + emit.

증상: 사용자가 녹화 도중 명시적으로 닫은 탭/창이 회귀 재생 시 살아남음.

원인: 녹화 .py 를 시나리오로 변환하는 단계에서 '창 닫기' 동작이 일괄 무시.

조치: 변환기/실행기/회귀 .py emit 모두에서 'close' 액션을 보존. 회귀 .py 는
``<page_var>.close()`` 를 emit 하고 close 단계 직후 ``_settle`` / lookahead
모두 skip (닫힌 page 접근 시 예외 회피).

본 슈트는 regression_generator 의 close emit 측면만 가드:
  (1) close action 이 emit 으로 흘러가 ``.close()`` 호출 생성됨,
  (2) close step 직후 ``_settle`` / lookahead wait_for 가 emit 안 됨.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_close_action_emits_page_close_call(emit_regression):
    """close action 이 ``<page>.close()`` 로 emit 된다."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "close", "target": "", "value": ""},
    ])
    assert src is not None
    assert "page.close()" in src, (
        f"close action 의 .close() 호출 emit 누락 — 녹화 중 닫은 창 보존 회귀: "
        f"{src!r}"
    )


@pytest.mark.unit
def test_close_step_does_not_emit_settle_or_lookahead_after(emit_regression):
    """close 가 마지막 step 인 시나리오 — close 뒤에 ``_settle`` / ``wait_for`` 호출 0건.

    close 가 마지막이 아니면 *다음 step 의 출력 블록* 이 close 뒤에 이어져
    _settle 등이 정상적으로 등장하는데, 그건 다음 step 의 책임이지 close 의
    책임이 아니다. 따라서 close 의 emit 책임은 "자기 step 블록 안에서
    settle/lookahead 호출을 안 emit" 한정 — close 를 마지막 step 으로 두고
    검증.
    """
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
        {"step": 3, "action": "close", "target": "", "value": ""},
    ])
    assert src is not None
    assert "page.close()" in src

    # close 라인 이후 부분 추출 — finally 블록 진입 전까지 _settle 등장 0건.
    close_idx = src.index("page.close()")
    finally_idx = src.index("finally:", close_idx)
    after_close = src[close_idx:finally_idx]

    assert "_settle(page)" not in after_close, (
        f"close 직후 _settle(page) 가 emit 됨 — closed page 접근 예외 회귀: "
        f"{after_close!r}"
    )
    # lookahead wait_for 도 같은 이유로 close 직후 emit 금지.
    assert ".wait_for(state=" not in after_close, (
        f"close 직후 lookahead wait_for 가 emit 됨 — closed page 예외 회귀: "
        f"{after_close!r}"
    )
