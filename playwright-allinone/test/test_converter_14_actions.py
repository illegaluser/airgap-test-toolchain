"""S4C-02 — converter.py 14대 액션 변환 단위테스트.

`recorded-14actions.py` 를 converter 로 돌렸을 때:
1. 14대 액션이 1 회씩 정확히 등장해야 한다 (드롭/누락 0).
2. 변환 결과가 `_validate_scenario` 를 통과해야 한다.
3. 신규 5 종 (upload / drag / scroll / mock_status / mock_data) 의 target/value 가
   `regression_generator` emitter 와 1:1 일관 (재실행 가능 코드 emit).

기존 9 대 회귀(`recorded-9actions.py`) 도 깨지지 않았는지 같이 보장한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zero_touch_qa.__main__ import _validate_scenario
from zero_touch_qa.converter import convert_playwright_to_dsl
from zero_touch_qa.regression_generator import _emit_step_code, _target_to_playwright_code


def _emit(step: dict) -> list[str]:
    """테스트 편의 — step dict 만으로 emitter 를 호출하기 위한 thin wrapper."""
    return _emit_step_code(
        step.get("action", ""),
        step.get("target", ""),
        step.get("value", ""),
        step,
        _target_to_playwright_code(step.get("target", "")),
    )


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def out_dir(tmp_path: Path) -> str:
    return str(tmp_path / "out")


def _convert(rec_name: str, out_dir: str) -> list[dict]:
    src = REPO_ROOT / "test" / rec_name
    return convert_playwright_to_dsl(str(src), out_dir)


def test_recorded_14_actions_covers_full_dsl(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)

    actions = [s["action"] for s in scenario]
    expected = [
        "navigate", "wait", "select", "check", "fill", "press",
        "click", "hover", "upload", "drag", "scroll",
        "mock_status", "mock_data", "verify",
    ]
    assert actions == expected, f"expected 14대 순서 1:1 매핑, got: {actions}"


def test_recorded_14_actions_passes_validate_scenario(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    # _validate_scenario 는 step 번호 1..N 연속을 요구하므로 converter 가 부여한 번호로 통과해야 함.
    _validate_scenario(scenario)


def test_recorded_14_actions_step_numbers_are_sequential(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    assert [s["step"] for s in scenario] == list(range(1, len(scenario) + 1))


def test_recorded_14_actions_fallback_targets_present(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    for step in scenario:
        assert "fallback_targets" in step
        assert isinstance(step["fallback_targets"], list)


# ─── 신규 5종 액션 — target/value 계약 검증 ─────────────────────────────────


def _step_by_action(scenario: list[dict], action: str) -> dict:
    matches = [s for s in scenario if s["action"] == action]
    assert matches, f"{action} step 없음"
    return matches[0]


def test_upload_target_and_path(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "upload")
    assert step["target"] == "#file-input"
    assert step["value"] == "upload_sample.txt"


def test_drag_src_and_dst(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "drag")
    assert step["target"] == "#card"
    assert step["value"] == "#dst-zone"


def test_scroll_into_view_value(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "scroll")
    assert step["target"] == "#footer"
    assert step["value"] == "into_view"


def test_mock_status_pattern_and_status(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "mock_status")
    assert step["target"] == "**/api/profile"
    assert step["value"] == "500"


def test_mock_data_pattern_and_body(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "mock_data")
    assert step["target"] == "**/api/items"
    # body 는 JSON 문자열 — 따옴표는 풀려서 들어감.
    assert step["value"] == '{"items":[]}'


# ─── regression_generator emitter 와의 일관성 회귀 ─────────────────────────


def test_regression_generator_emits_for_all_14(out_dir):
    """변환된 14 step 모두 emitter 가 valid 라인을 생산해야 한다 (skip 없음)."""
    scenario = _convert("recorded-14actions.py", out_dir)
    for step in scenario:
        lines = _emit(step)
        assert lines, f"{step['action']} emitter 가 빈 코드 반환"
        # 명시된 stub-only 코드(`# unsupported`) 가 끼어들지 않았는지 확인.
        joined = "\n".join(lines)
        assert "unsupported" not in joined.lower()


def test_regression_generator_upload_emits_set_input_files(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "upload")
    code = "\n".join(_emit(step))
    assert "set_input_files" in code
    assert '"upload_sample.txt"' in code


def test_regression_generator_drag_emits_drag_to(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "drag")
    code = "\n".join(_emit(step))
    assert "drag_to" in code


def test_regression_generator_scroll_emits_scroll_into_view(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "scroll")
    code = "\n".join(_emit(step))
    assert "scroll_into_view_if_needed" in code


def test_regression_generator_mock_status_emits_route_fulfill(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "mock_status")
    code = "\n".join(_emit(step))
    assert "page.route(" in code
    assert "status=500" in code


def test_regression_generator_mock_data_emits_route_with_body(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    step = _step_by_action(scenario, "mock_data")
    code = "\n".join(_emit(step))
    assert "page.route(" in code
    assert "body=" in code


# ─── 9 대 회귀 — 기존 변환 결과 보존 검증 ─────────────────────────────────


def test_recorded_9_actions_still_converts(out_dir):
    scenario = _convert("recorded-9actions.py", out_dir)
    actions = [s["action"] for s in scenario]
    # navigate/verify/wait/click/fill/press/select/check/hover (각 ≥1)
    for a in ("navigate", "verify", "wait", "click", "fill", "press", "select", "check", "hover"):
        assert a in actions, f"기존 9 대 회귀에 {a} 누락"
    _validate_scenario(scenario)
