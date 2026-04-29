"""S4C-02 — unit tests for converter.py covering all 14 actions.

When `recorded-14actions.py` is run through the converter:
1. each of the 14 actions appears exactly once (no drops/omissions).
2. the converted result passes `_validate_scenario`.
3. the new 5 actions (upload / drag / scroll / mock_status / mock_data)
   have target/value 1:1 consistent with the `regression_generator`
   emitter (re-runnable code).

The existing 9-action regression (`recorded-9actions.py`) is also
verified to keep passing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zero_touch_qa.__main__ import _validate_scenario
from zero_touch_qa.converter import convert_playwright_to_dsl
from zero_touch_qa.regression_generator import _emit_step_code, _target_to_playwright_code


def _emit(step: dict) -> list[str]:
    """Test convenience — thin wrapper to call the emitter with just a step dict."""
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
    assert actions == expected, f"expected 14-action 1:1 ordering, got: {actions}"


def test_recorded_14_actions_passes_validate_scenario(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    # _validate_scenario requires step numbers 1..N to be sequential, so it must pass with converter-assigned numbers.
    _validate_scenario(scenario)


def test_recorded_14_actions_step_numbers_are_sequential(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    assert [s["step"] for s in scenario] == list(range(1, len(scenario) + 1))


def test_recorded_14_actions_fallback_targets_present(out_dir):
    scenario = _convert("recorded-14actions.py", out_dir)
    for step in scenario:
        assert "fallback_targets" in step
        assert isinstance(step["fallback_targets"], list)


# ─── 5 new actions — target/value contract checks ──────────────────────────


def _step_by_action(scenario: list[dict], action: str) -> dict:
    matches = [s for s in scenario if s["action"] == action]
    assert matches, f"no {action} step"
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
    # body is a JSON string — quotes are unescaped on the way in.
    assert step["value"] == '{"items":[]}'


# ─── consistency regression with the regression_generator emitter ──────────


def test_regression_generator_emits_for_all_14(out_dir):
    """Every one of the 14 converted steps must produce valid emitter lines (no skips)."""
    scenario = _convert("recorded-14actions.py", out_dir)
    for step in scenario:
        lines = _emit(step)
        assert lines, f"{step['action']} emitter returned empty code"
        # confirm stub-only code (`# unsupported`) didn't sneak in.
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


# ─── 9-action regression — preserve original conversion behavior ───────────


def test_recorded_9_actions_still_converts(out_dir):
    scenario = _convert("recorded-9actions.py", out_dir)
    actions = [s["action"] for s in scenario]
    # navigate/verify/wait/click/fill/press/select/check/hover (each ≥1)
    for a in ("navigate", "verify", "wait", "click", "fill", "press", "select", "check", "hover"):
        assert a in actions, f"existing 9-action regression missing {a}"
    _validate_scenario(scenario)
