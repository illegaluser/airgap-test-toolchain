"""Regression guard for ae5731b — 시나리오 검증 화이트리스트의 ``close`` 액션.

증상: popup 두 개를 열고 닫는 시나리오를 녹화하면 시나리오 변환 직후 로드가
거부 ("step[N].action 이 유효하지 않음: 'close'"). LLM 실행이 즉시 실패하고
popup 들이 화면에 그대로 남음.

원인: ``_VALID_ACTIONS`` 화이트리스트에 ``"close"`` 누락. 0e59953 에서 close
보존 기능을 넣었지만 validation 쪽 동기화가 빠져 있었음.

조치:
  - ``"close"`` 를 ``_VALID_ACTIONS`` 에 추가
  - ``"close"`` 를 ``_TARGET_OPTIONAL_ACTIONS`` 에 추가 (target 빈 값도 OK)
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_close_action_is_in_scenario_validation_whitelist():
    """``close`` 가 시나리오 검증 화이트리스트에 포함되어 있다."""
    from zero_touch_qa.__main__ import _VALID_ACTIONS
    assert "close" in _VALID_ACTIONS, (
        "close 가 _VALID_ACTIONS 누락 — popup 시나리오 변환 후 로드 통째 거부 회귀"
    )


@pytest.mark.unit
def test_close_action_is_in_target_optional_actions():
    """``close`` 는 target 이 빈 값이어도 검증 통과 (page 자체를 닫음)."""
    from zero_touch_qa.__main__ import _TARGET_OPTIONAL_ACTIONS
    assert "close" in _TARGET_OPTIONAL_ACTIONS, (
        "close 는 target 빈 값 허용해야 함 — 시나리오 검증 규칙 누락 회귀"
    )
