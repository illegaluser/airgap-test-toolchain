"""dialog_choose 액션 (Sprint 5 / 측정 액션 1/5) 통합 테스트.

- 기본 동작: dialog_choose 없으면 모든 dialog 자동 dismiss (회귀 확인)
- accept / dismiss / prompt 텍스트 응답 3 가지 모두 동작
- one-shot: 등록 후 한 번 매치 응답하면 자동 해제 (다음 dialog 는 기본 dismiss)
- type 필터: target=alert 등록 + confirm 발생 시 매치 X → 기본 dismiss

외부 사이트 의존 없음 — fixture file:// 만 사용.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import QAExecutor


# ─────────────────────────────────────────────────────────────────────────
# Unit — validation (브라우저 없이)
# ─────────────────────────────────────────────────────────────────────────


def test_dialog_choose_rejects_unknown_target(make_executor):
    """알 수 없는 target 은 즉시 ValueError. 브라우저 spawn 비용 안 듦."""
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="알 수 없는 target"):
        executor._execute_dialog_choose(
            page=None,  # 실제로 안 쓰임 — validation 만 거치고 raise
            step={"action": "dialog_choose", "target": "tooltip", "value": "accept"},
            artifacts="",
        )


def test_dialog_choose_normalizes_empty_value_to_dismiss(make_executor):
    """value 가 비거나 None 이면 'dismiss' 로 정규화."""
    executor: QAExecutor = make_executor()
    result = executor._execute_dialog_choose(
        page=None,
        step={"action": "dialog_choose", "target": "any", "value": ""},
        artifacts="",
    )
    assert result.status == "PASS"
    assert executor._dialog_choice == ("any", "dismiss")


# ─────────────────────────────────────────────────────────────────────────
# Integration — 실제 dialog 발생까지 검증
# ─────────────────────────────────────────────────────────────────────────


def _scenario_navigate(url: str) -> list[dict]:
    return [{"step": 1, "action": "navigate", "target": "", "value": url, "description": "초기"}]


def test_default_dismiss_for_alert(fixture_url, make_executor, run_scenario):
    """dialog_choose 미사용 — alert 자동 dismiss 후 후속 코드 진행 (회귀 확인)."""
    scenario = _scenario_navigate(fixture_url("dialog_alert.html")) + [
        {"step": 2, "action": "click", "target": "#trigger", "value": "", "description": "alert 띄움"},
        {"step": 3, "action": "verify", "target": "#after", "value": "after",
         "condition": "text", "description": "alert 후 진행됨"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_confirm_accept(fixture_url, make_executor, run_scenario):
    """confirm accept 등록 → 클릭 → 'accepted' 표시."""
    scenario = _scenario_navigate(fixture_url("dialog_confirm.html")) + [
        {"step": 2, "action": "dialog_choose", "target": "confirm", "value": "accept",
         "description": "다음 confirm 은 OK"},
        {"step": 3, "action": "click", "target": "#trigger", "value": "", "description": ""},
        {"step": 4, "action": "verify", "target": "#result", "value": "accepted",
         "condition": "text", "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS", "PASS"]


def test_confirm_dismiss(fixture_url, make_executor, run_scenario):
    """confirm dismiss 등록 → 클릭 → 'dismissed'."""
    scenario = _scenario_navigate(fixture_url("dialog_confirm.html")) + [
        {"step": 2, "action": "dialog_choose", "target": "confirm", "value": "dismiss",
         "description": ""},
        {"step": 3, "action": "click", "target": "#trigger", "value": "", "description": ""},
        {"step": 4, "action": "verify", "target": "#result", "value": "dismissed",
         "condition": "text", "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS", "PASS"]


def test_prompt_with_text(fixture_url, make_executor, run_scenario):
    """prompt 응답 텍스트 'alice' 전달 → 'hello alice' 출력."""
    scenario = _scenario_navigate(fixture_url("dialog_prompt.html")) + [
        {"step": 2, "action": "dialog_choose", "target": "prompt", "value": "alice",
         "description": ""},
        {"step": 3, "action": "click", "target": "#trigger", "value": "", "description": ""},
        {"step": 4, "action": "verify", "target": "#result", "value": "hello alice",
         "condition": "text", "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS", "PASS"]


def test_one_shot_resets_after_match(fixture_url, make_executor, run_scenario):
    """등록은 1회만 — 두 번째 dialog 는 등록 해제 후라 기본 dismiss 로 떨어진다.

    confirm 픽스처에서 한 번 accept 등록 후, 두 번 click 한다. 첫 번째는
    accept('accepted'), 두 번째는 등록 없으니 기본 dismiss → 'dismissed'.
    """
    scenario = _scenario_navigate(fixture_url("dialog_confirm.html")) + [
        {"step": 2, "action": "dialog_choose", "target": "confirm", "value": "accept",
         "description": "1회만"},
        {"step": 3, "action": "click", "target": "#trigger", "value": "", "description": "1차"},
        {"step": 4, "action": "verify", "target": "#result", "value": "accepted",
         "condition": "text", "description": "1차는 accept"},
        {"step": 5, "action": "click", "target": "#trigger", "value": "", "description": "2차"},
        {"step": 6, "action": "verify", "target": "#result", "value": "dismissed",
         "condition": "text", "description": "2차는 기본 dismiss"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 6
    # one-shot 검증: 모든 step 후 _dialog_choice 가 비어야 함.
    assert executor._dialog_choice is None


def test_type_filter_mismatch_falls_back_to_default_dismiss(fixture_url, make_executor, run_scenario):
    """target=alert 등록인데 실제 발생은 confirm — 매치 X → 기본 dismiss."""
    scenario = _scenario_navigate(fixture_url("dialog_confirm.html")) + [
        {"step": 2, "action": "dialog_choose", "target": "alert", "value": "accept",
         "description": "alert 만 매치"},
        {"step": 3, "action": "click", "target": "#trigger", "value": "", "description": ""},
        {"step": 4, "action": "verify", "target": "#result", "value": "dismissed",
         "condition": "text", "description": "confirm 은 기본 dismiss"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS", "PASS"]
