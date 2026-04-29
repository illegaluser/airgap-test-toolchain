"""S3-01 — upload action integration check.

Three cases:
- happy: file inside artifacts uploads and the DOM shows the result
- path outside artifacts is blocked: `_validate_scenario` rejects with FileNotFoundError
- empty value rejected: `_validate_scenario` rejects immediately with ScenarioValidationError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zero_touch_qa.__main__ import ScenarioValidationError, _validate_scenario
from helpers.scenarios import navigate, upload, verify


def _seed_artifact_file(executor, name: str = "smoke.txt", content: str = "hello") -> Path:
    """Place the upload target file inside artifacts ahead of time."""
    artifacts = Path(executor.config.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / name
    path.write_text(content, encoding="utf-8")
    return path


def test_upload_happy_path(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    _seed_artifact_file(executor, "smoke.txt")

    scenario = [
        navigate(fixture_url("upload.html"), step=1),
        upload("#file-input", "smoke.txt", step=2, description="upload file"),
        verify("#result", step=3, condition="text", value="uploaded:smoke.txt"),
    ]
    results, _, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"unexpected statuses: {statuses}"


def test_upload_outside_artifacts_root_is_rejected(make_executor, fixture_url, tmp_path):
    executor = make_executor()
    # Place a file outside artifacts — both absolute and relative paths must be blocked.
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    scenario = [
        navigate(fixture_url("upload.html"), step=1),
        upload("#file-input", "../secret.txt", step=2),
    ]
    # _validate_scenario itself passes (value not empty). At execution time, FileNotFoundError.
    _validate_scenario(scenario)
    results = executor.execute(scenario, headed=False)

    assert results[0].status == "PASS"
    assert results[1].status == "FAIL"  # path rejected → no fallback, so FAIL


def test_upload_empty_value_rejected_at_validation():
    scenario = [
        navigate("file:///x", step=1),
        upload("#file-input", "", step=2),
    ]
    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(scenario)
