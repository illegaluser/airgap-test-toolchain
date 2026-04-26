from pathlib import Path

import pytest

from zero_touch_qa.__main__ import ScenarioValidationError, _validate_scenario
from zero_touch_qa.config import Config
from zero_touch_qa.executor import QAExecutor


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
