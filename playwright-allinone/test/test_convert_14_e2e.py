"""S4C-03 — convert 14대 E2E 통합 회귀.

`recorded-14actions.py` 의 인메모리 변형을 fixture URL 로 만든 뒤:
1. `convert_playwright_to_dsl` 로 14대 DSL scenario 변환
2. `_validate_scenario` 통과 확인
3. `QAExecutor` 로 fixture `full_dsl.html` 위에서 14 step 실행 → 모두 PASS

LLM 우회 경로 (convert 모드는 정규식 파서만 사용) 이므로 Dify monkeypatch 불필요.
이 테스트가 PASS 하면 Jenkins `RUN_MODE=convert` E2E 의 결정론적 회귀 책임을 진다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zero_touch_qa.__main__ import _validate_scenario
from zero_touch_qa.converter import convert_playwright_to_dsl


def _write_recorded(tmp_path: Path, fixture_uri: str) -> Path:
    """fixture file:// URL 기반 14대 codegen-style 스크립트를 임시 파일로 기록."""
    src = tmp_path / "recorded.py"
    src.write_text(
        f'''from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto("{fixture_uri}")
    page.wait_for_timeout(50)
    page.locator("#lang").select_option(label="한국어")
    page.locator("#agree").check()
    page.locator("#search-input").fill("DSCORE")
    page.locator("#search-input").press("Enter")
    page.locator("#primary-btn").click()
    page.locator("#card").hover()
    page.locator("#file-input").set_input_files("upload_sample.txt")
    page.locator("#card").drag_to(page.locator("#dst-zone"))
    page.locator("#footer").scroll_into_view_if_needed()
    page.route("**/api/profile", lambda r: r.fulfill(status=500))
    page.route("**/api/items", lambda r: r.fulfill(status=200, content_type="application/json", body="{{\\"items\\":[]}}"))
    expect(page.locator("#footer")).to_have_text("FOOTER")

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
''',
        encoding="utf-8",
    )
    return src


def test_convert_14_actions_full_pipeline(tmp_path: Path, fixture_url, make_executor, run_scenario):
    """convert → validate → execute 풀 사슬 — fixture full_dsl.html 위에서 14/14 PASS."""
    fixture_uri = fixture_url("full_dsl.html")
    recorded = _write_recorded(tmp_path, fixture_uri)

    out_dir = tmp_path / "out"
    scenario = convert_playwright_to_dsl(str(recorded), str(out_dir))

    # 1. 14대 액션이 모두 들어왔는지 확인.
    assert len(scenario) == 14
    actions = [s["action"] for s in scenario]
    expected = [
        "navigate", "wait", "select", "check", "fill", "press",
        "click", "hover", "upload", "drag", "scroll",
        "mock_status", "mock_data", "verify",
    ]
    assert actions == expected

    # 2. _validate_scenario 통과.
    _validate_scenario(scenario)

    # 3. executor 가 풀 시나리오를 실행 — 14/14 PASS.
    #    upload step 의 fallback 경로(`artifacts/upload_sample.txt`) 를 위해
    #    artifacts 에 더미 파일을 미리 생성한다 (Sprint 5 §10.3.2 default fallback).
    executor = make_executor()
    artifacts = Path(executor.config.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "upload_sample.txt").write_text("hello", encoding="utf-8")

    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert statuses == ["PASS"] * 14, f"expected 14/14 PASS, got: {statuses}"


def test_convert_14_actions_artifact_outputs(tmp_path: Path, fixture_url):
    """convert 산출물 — scenario.json 이 output_dir 에 기록되는지 확인."""
    fixture_uri = fixture_url("full_dsl.html")
    recorded = _write_recorded(tmp_path, fixture_uri)

    out_dir = tmp_path / "out"
    convert_playwright_to_dsl(str(recorded), str(out_dir))

    scenario_path = out_dir / "scenario.json"
    assert scenario_path.exists()
    # 비어있지 않은 JSON 배열인지 표면적 검증.
    text = scenario_path.read_text(encoding="utf-8")
    assert text.strip().startswith("[")
    assert "navigate" in text and "mock_data" in text
