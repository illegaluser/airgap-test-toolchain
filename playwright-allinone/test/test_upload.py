"""S3-01 — upload 액션 통합 검증.

3 케이스:
- happy: artifacts 안 파일 업로드 후 DOM 결과 표시
- artifacts 밖 경로 차단: `_validate_scenario` 가 FileNotFoundError 로 거부
- 빈 value 거부: `_validate_scenario` 가 ScenarioValidationError 로 즉시 거부
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zero_touch_qa.__main__ import ScenarioValidationError, _validate_scenario
from helpers.scenarios import navigate, upload, verify


def _seed_artifact_file(executor, name: str = "smoke.txt", content: str = "hello") -> Path:
    """artifacts 안에 업로드 대상 파일을 미리 둔다."""
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
        upload("#file-input", "smoke.txt", step=2, description="파일 업로드"),
        verify("#result", step=3, condition="text", value="uploaded:smoke.txt"),
    ]
    results, _, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"unexpected statuses: {statuses}"


def test_upload_outside_artifacts_root_is_rejected(make_executor, fixture_url, tmp_path):
    executor = make_executor()
    # artifacts 밖에 파일을 둔다 — 절대경로/상대경로 모두 차단되어야 한다.
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    scenario = [
        navigate(fixture_url("upload.html"), step=1),
        upload("#file-input", "../secret.txt", step=2),
    ]
    # _validate_scenario 자체는 통과 (value 비어있지 않음). 실행 시 FileNotFoundError.
    _validate_scenario(scenario)
    results = executor.execute(scenario, headed=False)

    assert results[0].status == "PASS"
    assert results[1].status == "FAIL"  # 경로 거부 → fallback 도 없어 FAIL


def test_upload_empty_value_rejected_at_validation():
    scenario = [
        navigate("file:///x", step=1),
        upload("#file-input", "", step=2),
    ]
    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(scenario)
