from pathlib import Path
import os
import subprocess
import sys
import time

import pytest

from zero_touch_qa.__main__ import (
    ScenarioValidationError,
    _sanitize_scenario,
    _validate_scenario,
)
from zero_touch_qa.config import Config
from zero_touch_qa.executor import QAExecutor
from zero_touch_qa.regression_generator import _emit_step_code, _target_to_playwright_code


def _config(tmp_path: Path) -> Config:
    return Config(
        dify_base_url="http://localhost/v1",
        dify_api_key="dummy",
        artifacts_dir=str(tmp_path / "artifacts"),
        viewport=(1440, 900),
        slow_mo=0,
        headed_step_pause_ms=0,
        step_interval_min_ms=0,
        step_interval_max_ms=0,
        heal_threshold=0.8,
        heal_timeout_sec=60,
        scenario_timeout_sec=300,
        dom_snapshot_limit=4000,
    )


def test_validate_scenario_accepts_new_actions():
    scenario = [
        {"step": 1, "action": "navigate", "value": "https://example.com"},
        {"step": 2, "action": "upload", "target": "#file", "value": "upload.pdf"},
        {"step": 3, "action": "drag", "target": "#source", "value": "#target"},
        {"step": 4, "action": "scroll", "target": "#footer", "value": "into_view"},
        {"step": 5, "action": "mock_status", "target": "**/api/profile", "value": "500"},
        {
            "step": 6,
            "action": "mock_data",
            "target": "**/api/list",
            "value": "{\"items\":[]}",
        },
        {"step": 7, "action": "verify", "target": "#status", "condition": "visible"},
    ]

    _validate_scenario(scenario)


def test_convert_only_rejects_non_convert_mode_before_dify_call(tmp_path: Path):
    """`--convert-only` 오용은 Dify retry 로 들어가기 전에 즉시 실패해야 한다."""
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            "ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "DIFY_BASE_URL": "http://127.0.0.1:9/v1",
        }
    )

    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "zero_touch_qa",
            "--mode",
            "chat",
            "--convert-only",
            "--srs-text",
            "smoke",
            "--target-url",
            "file:///tmp/noop.html",
        ],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 1
    assert elapsed < 2
    combined = proc.stdout + proc.stderr
    assert "--convert-only 는 --mode convert 와 함께만 사용한다" in combined
    assert "Retry" not in combined


def test_validate_scenario_rejects_invalid_scroll_value():
    scenario = [{"step": 1, "action": "scroll", "target": "#footer", "value": "bottom"}]

    with pytest.raises(ScenarioValidationError, match="into_view"):
        _validate_scenario(scenario)


def test_resolve_upload_path_only_allows_artifacts_root(tmp_path: Path):
    executor = QAExecutor(_config(tmp_path))
    artifacts_dir = Path(executor.config.artifacts_dir)
    artifacts_dir.mkdir(parents=True)
    allowed_file = artifacts_dir / "fixtures" / "sample.txt"
    allowed_file.parent.mkdir(parents=True)
    allowed_file.write_text("ok", encoding="utf-8")

    resolved = executor._resolve_upload_path("fixtures/sample.txt")
    assert resolved == str(allowed_file)

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("nope", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        executor._resolve_upload_path("../secret.txt")


def test_normalize_mock_body_accepts_dict_and_json_string(tmp_path: Path):
    executor = QAExecutor(_config(tmp_path))

    assert executor._normalize_mock_body({"items": []}) == '{"items": []}'
    assert executor._normalize_mock_body('{"items":[]}') == '{"items": []}'
    assert executor._normalize_mock_body("plain-text") == "plain-text"


def test_validate_scenario_demotes_unknown_verify_condition():
    """LLM 비결정성 대응: 화이트리스트 밖 condition 은 reject 대신 ""(default) 로 강등."""
    scenario = [
        {"step": 1, "action": "navigate", "value": "https://example.com"},
        {"step": 2, "action": "verify", "target": "#x", "condition": "exists"},
    ]
    _validate_scenario(scenario)
    assert scenario[1]["condition"] == ""


def test_validate_scenario_accepts_extended_verify_conditions():
    scenario = [
        {"step": 1, "action": "navigate", "value": "https://example.com"},
        {"step": 2, "action": "verify", "target": "#a", "condition": "disabled"},
        {"step": 3, "action": "verify", "target": "#b", "condition": "checked"},
        {"step": 4, "action": "verify", "target": "#c", "condition": "value", "value": "42"},
        {"step": 5, "action": "verify", "target": "#d", "condition": "contains_text", "value": "ok"},
    ]
    _validate_scenario(scenario)


def test_validate_scenario_rejects_non_integer_mock_times():
    scenario = [
        {"step": 1, "action": "mock_status", "target": "**/api", "value": "500", "times": "abc"},
    ]
    with pytest.raises(ScenarioValidationError, match="times"):
        _validate_scenario(scenario)


def test_validate_scenario_rejects_zero_mock_times():
    scenario = [
        {"step": 1, "action": "mock_data", "target": "**/api", "value": "{}", "times": 0},
    ]
    with pytest.raises(ScenarioValidationError, match="times"):
        _validate_scenario(scenario)


def test_validate_scenario_accepts_positive_mock_times():
    scenario = [
        {"step": 1, "action": "mock_status", "target": "**/api", "value": "500", "times": 5},
    ]
    _validate_scenario(scenario)


def test_regression_generator_emits_upload_call():
    code_lines = _emit_step_code(
        "upload", "#file", "fixtures/sample.txt", {}, _target_to_playwright_code("#file")
    )
    joined = "\n".join(code_lines)
    assert "set_input_files" in joined
    assert '"fixtures/sample.txt"' in joined


def test_regression_generator_emits_drag_with_two_locators():
    code = "\n".join(
        _emit_step_code(
            "drag", "#src", "#dst", {}, _target_to_playwright_code("#src")
        )
    )
    assert "drag_to" in code
    assert "_src" in code and "_dst" in code


def test_regression_generator_emits_scroll_into_view():
    code = "\n".join(
        _emit_step_code(
            "scroll", "#footer", "into_view", {}, _target_to_playwright_code("#footer")
        )
    )
    assert "scroll_into_view_if_needed" in code


def test_regression_generator_emits_mock_status_route():
    code = "\n".join(
        _emit_step_code(
            "mock_status", "**/api/users", "500", {},
            _target_to_playwright_code("**/api/users"),
        )
    )
    assert "page.route" in code
    assert "status=500" in code
    assert "times=1" in code


def test_regression_generator_emits_mock_data_route():
    code = "\n".join(
        _emit_step_code(
            "mock_data", "**/api/list", '{"items":[]}', {},
            _target_to_playwright_code("**/api/list"),
        )
    )
    assert "page.route" in code
    assert "application/json" in code
    assert '{\\"items\\":[]}' in code


def test_regression_generator_emits_verify_condition_branches():
    base_target = "#status"
    locator = _target_to_playwright_code(base_target)

    hidden_code = "\n".join(
        _emit_step_code("verify", base_target, "", {"condition": "hidden"}, locator)
    )
    assert "not " in hidden_code and "is_visible" in hidden_code

    disabled_code = "\n".join(
        _emit_step_code("verify", base_target, "", {"condition": "disabled"}, locator)
    )
    assert "is_disabled" in disabled_code

    value_code = "\n".join(
        _emit_step_code("verify", base_target, "ABC", {"condition": "value"}, locator)
    )
    assert "input_value()" in value_code and '"ABC"' in value_code


def test_install_mock_route_clamps_times_minimum(tmp_path: Path):
    """times <= 0 이 들어와도 1 로 끌어올려 page.route 호출이 깨지지 않게 한다."""
    calls = []

    class _FakePage:
        def route(self, pattern, handler, times=None):
            calls.append((pattern, times))

    QAExecutor._install_mock_route(_FakePage(), "**/api", status=200, times=0)
    QAExecutor._install_mock_route(_FakePage(), "**/api", status=200, times=3)

    assert calls[0][1] == 1
    assert calls[1][1] == 3


# ─── Sanitizer meta-reasoning leak 회복 (Sprint 5 §10.5 후속) ──────────────


def test_sanitize_recovers_leading_valid_action_from_polluted_field():
    """LLM 이 action 필드에 'verify, target: ...' 형태로 meta-reasoning 을 섞어 emit
    하더라도 앞쪽 첫 토큰이 valid action 이면 회복한다."""
    raw = [
        {"action": 'verify, target: id=status, value: "clicked", condition: text',
         "target": "#status", "value": "clicked"},
        {"action": "`upload`, target: <input type='file'>", "target": "#file"},
        {"action": "click", "target": "#btn"},  # 정상
    ]
    out = _sanitize_scenario(raw)
    assert len(out) == 3
    assert out[0]["action"] == "verify"
    assert out[1]["action"] == "upload"
    assert out[2]["action"] == "click"


def test_sanitize_drops_when_leading_token_not_valid():
    """앞쪽 첫 토큰조차 valid action 이 아니면 drop 한다."""
    raw = [
        {"action": "* target is source, value is destination", "target": "x"},
        {"action": "click", "target": "#btn"},
    ]
    out = _sanitize_scenario(raw)
    assert len(out) == 1
    assert out[0]["action"] == "click"


# ─── Healer action whitelist 전이 (Sprint 6 / Option-2) ────────────────────


def test_heal_action_transition_whitelist_accepts_intra_group():
    from zero_touch_qa.executor import _is_allowed_action_transition

    # 의미 등가 전이는 허용
    assert _is_allowed_action_transition("select", "fill")
    assert _is_allowed_action_transition("fill", "select")
    assert _is_allowed_action_transition("check", "click")
    assert _is_allowed_action_transition("click", "check")
    assert _is_allowed_action_transition("click", "press")
    assert _is_allowed_action_transition("press", "click")
    assert _is_allowed_action_transition("upload", "click")
    assert _is_allowed_action_transition("click", "upload")
    # identity 도 허용 (Healer 가 같은 action 으로 재confirm 하는 경우)
    assert _is_allowed_action_transition("click", "click")


def test_heal_action_transition_whitelist_rejects_cross_group():
    from zero_touch_qa.executor import _is_allowed_action_transition

    # false-PASS 위험 전이는 거절
    assert not _is_allowed_action_transition("drag", "click")  # 의미 변경 — 검증 무력화
    assert not _is_allowed_action_transition("click", "drag")
    assert not _is_allowed_action_transition("verify", "click")  # 검증 그룹간 변경
    assert not _is_allowed_action_transition("navigate", "click")
    assert not _is_allowed_action_transition("mock_status", "mock_data")  # 다른 그룹
    assert not _is_allowed_action_transition("upload", "mock_data")  # 결함 4 케이스
    assert not _is_allowed_action_transition("wait", "verify")
    assert not _is_allowed_action_transition("hover", "click")  # hover 는 부분적으로 click 과 다른 의미


def test_heal_action_transition_whitelist_rejects_invalid_inputs():
    from zero_touch_qa.executor import _is_allowed_action_transition

    assert not _is_allowed_action_transition(None, "click")
    assert not _is_allowed_action_transition("click", None)
    assert not _is_allowed_action_transition("", "click")
    assert not _is_allowed_action_transition("click", "")
    # 대문자 입력은 함수 내부 lowercase 정규화로 화이트리스트와 매치된다 (방어적 처리)
    assert _is_allowed_action_transition("CLICK", "PRESS")
