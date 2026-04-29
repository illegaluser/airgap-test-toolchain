"""S4C-03 — convert 14-action E2E integration regression.

Take an in-memory variant of `recorded-14actions.py` pointing to the
fixture URL, then:
1. convert it into a 14-action DSL scenario with `convert_playwright_to_dsl`
2. confirm `_validate_scenario` passes
3. run the 14 steps through `QAExecutor` over the `full_dsl.html`
   fixture → all PASS

LLM-bypass path (convert mode uses only the regex parser), so no Dify
monkeypatch is needed. When this test passes, it owns the deterministic
regression for the Jenkins `RUN_MODE=convert` E2E.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zero_touch_qa.__main__ import _validate_scenario
from zero_touch_qa.converter import convert_playwright_to_dsl


def _write_recorded(tmp_path: Path, fixture_uri: str) -> Path:
    """Write a 14-action codegen-style script to a temp file targeting the fixture file:// URL."""
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
    """convert → validate → execute full chain — 14/14 PASS over the full_dsl.html fixture."""
    fixture_uri = fixture_url("full_dsl.html")
    recorded = _write_recorded(tmp_path, fixture_uri)

    out_dir = tmp_path / "out"
    scenario = convert_playwright_to_dsl(str(recorded), str(out_dir))

    # 1. confirm all 14 actions are present.
    assert len(scenario) == 14
    actions = [s["action"] for s in scenario]
    expected = [
        "navigate", "wait", "select", "check", "fill", "press",
        "click", "hover", "upload", "drag", "scroll",
        "mock_status", "mock_data", "verify",
    ]
    assert actions == expected

    # 2. passes _validate_scenario.
    _validate_scenario(scenario)

    # 3. executor runs the full scenario — 14/14 PASS.
    #    For the upload step's fallback path (`artifacts/upload_sample.txt`),
    #    drop a dummy file under artifacts ahead of time
    #    (Sprint 5 §10.3.2 default fallback).
    executor = make_executor()
    artifacts = Path(executor.config.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "upload_sample.txt").write_text("hello", encoding="utf-8")

    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert statuses == ["PASS"] * 14, f"expected 14/14 PASS, got: {statuses}"


def test_convert_14_actions_artifact_outputs(tmp_path: Path, fixture_url):
    """convert artifact — confirm scenario.json is written into output_dir."""
    fixture_uri = fixture_url("full_dsl.html")
    recorded = _write_recorded(tmp_path, fixture_uri)

    out_dir = tmp_path / "out"
    convert_playwright_to_dsl(str(recorded), str(out_dir))

    scenario_path = out_dir / "scenario.json"
    assert scenario_path.exists()
    # surface-level check that it's a non-empty JSON array.
    text = scenario_path.read_text(encoding="utf-8")
    assert text.strip().startswith("[")
    assert "navigate" in text and "mock_data" in text
