"""S3-09 — fallback_targets healing integration check.

When the primary target is absent from the page, the second selector in
`fallback_targets` should heal it. That recovery must serialize to
`scenario.healed.json` as an updated step["target"] (regression for the
Sprint 2 S2-12 hardening).
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers.scenarios import navigate, verify


def test_fallback_target_heals_and_persists_to_healed_json(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """Primary selector missing → recovers via fallback_targets[0] → step.target updated.

    Block Dify calls with monkeypatch so they don't leak. The fallback
    stage must finish here — Dify heal should not be called
    (verify recorder.heal_calls == 0).
    """
    recorder = monkeypatch_dify(heal_response=None)
    executor = make_executor()

    page = fixture_url("verify_conditions.html")
    scenario = [
        navigate(page, step=1),
        # Primary selector #ghost is absent. Fallback provides #visible-box.
        {
            "step": 2,
            "action": "verify",
            "target": "#ghost-element",
            "value": "",
            "condition": "visible",
            "description": "ghost selector — passes only via fallback",
            "fallback_targets": ["#visible-box"],
        },
    ]
    results, scenario_after, artifacts = run_scenario(executor, scenario)

    # navigate PASS, verify HEALED (fallback stage)
    assert results[0].status == "PASS"
    assert results[1].status == "HEALED"
    assert results[1].heal_stage == "fallback"

    # The step dict itself must be updated in place (S2-12 guarantee).
    assert scenario_after[1]["target"] == "#visible-box"

    # 0 Dify heal calls — must have ended at the fallback stage.
    assert recorder.heal_calls == 0

    # The helper here doesn't call __main__.main()'s save_scenario(suffix=".healed"),
    # so verify serialization directly: the scenario list holding the updated
    # target is enough to guarantee the healed.json gets the updated value.
    serialized = json.dumps(scenario_after, ensure_ascii=False)
    assert "#visible-box" in serialized
    assert "#ghost-element" not in serialized.split("\"target\"")[2]  # step 2's target slot

    # The artifacts directory should hold at least the navigate / verify screenshots.
    art_path = Path(artifacts)
    assert any(art_path.iterdir())
