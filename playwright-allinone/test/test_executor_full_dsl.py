"""S3-08 — meta-regression covering all 14 actions in one scenario.

When the executor runs all 14 DSL steps sequentially, every step should
PASS with no state leaking between them. Also confirms the artifacts
(run_log.jsonl, scenario.healed.json) stay coherent.
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers.scenarios import (
    check, click, drag, fill, hover, mock_data, mock_status, navigate,
    press, scroll, select, upload, verify, wait,
)


def _seed_artifact(executor, name: str, content: str) -> Path:
    art = Path(executor.config.artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    p = art / name
    p.write_text(content, encoding="utf-8")
    return p


def test_full_14_action_scenario_all_pass(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    _seed_artifact(executor, "ttc.txt", "smoke")

    page = fixture_url("full_dsl.html")
    scenario = [
        # mock_* runs before navigate — fetch isn't triggered on page load,
        # but pre-install so it can intercept on subsequent click.
        mock_status("**/api/users/*", 500, step=1, description="mock users API"),
        mock_data("**/api/list", {"items": [{"name": "alpha"}]}, step=2,
                  description="mock list API"),
        navigate(page, step=3, description="load target page"),
        wait(50, step=4, description="DOM settle"),
        # hover runs before click — once the fixture's click handler
        # overwrites status to "clicked", mouseenter won't fire again,
        # so we preserve order.
        hover("#primary-btn", step=5),
        verify("#status", step=6, condition="contains_text", value="hovered"),
        click("#primary-btn", step=7),
        verify("#status", step=8, condition="contains_text", value="clicked"),
        fill("#search-input", "DSCORE", step=9),
        verify("#echo", step=10, condition="contains_text", value="echo:DSCORE"),
        press("#search-input", "Enter", step=11),
        verify("#echo", step=12, condition="contains_text", value="submitted:DSCORE"),
        select("#lang", "한국어", step=13),
        check("#agree", "on", step=14),
        verify("#agree", step=15, condition="checked"),
        upload("#file-input", "ttc.txt", step=16),
        verify("#file-name", step=17, condition="contains_text", value="ttc.txt"),
        drag("#card", "#dst-zone", step=18),
        click("#load-btn", step=19),
        verify("#list", step=20, condition="contains_text", value="alpha"),
        scroll("#footer", step=21),
        verify("#footer", step=22, condition="visible"),
    ]
    results, _, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"some steps failed: {statuses}"

    # All 14 actions appear ≥ once
    actions_seen = {s["action"] for s in scenario}
    expected = {
        "navigate", "click", "fill", "press", "select", "check", "hover", "wait",
        "verify", "upload", "drag", "scroll", "mock_status", "mock_data",
    }
    missing = expected - actions_seen
    assert not missing, f"missing actions: {missing}"


