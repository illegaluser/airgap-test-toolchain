"""S3-10 — integration check for mock_* healing paths.

Verifies Sprint 2's _execute_mock_step can recover an invalid URL pattern
via a fallback URL or a Dify LLM heal. Dify responses are mocked with
monkeypatch.

Two cases:
- fallback URL pattern — primary pattern invalid (e.g. empty) → recover via fallback_targets[0]
- Dify LLM heal — fallback also invalid → mocked Dify response fixes the target
"""

from __future__ import annotations

from helpers.scenarios import click, mock_status, navigate, verify


def test_mock_status_recovers_via_fallback_pattern(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """Primary pattern is empty → recover via the valid pattern in fallback_targets."""
    recorder = monkeypatch_dify(heal_response=None)
    executor = make_executor()

    scenario = [
        # Primary target empty → _install_mock_route raises ValueError → enters fallback
        {
            "step": 1,
            "action": "mock_status",
            "target": "",  # intentionally invalid
            "value": "500",
            "fallback_targets": ["**/api/users/*"],
            "description": "pattern recovery — apply valid glob via fallback",
        },
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        verify("#error", step=4, condition="contains_text", value="(500)"),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert statuses == ["HEALED", "PASS", "PASS", "PASS"], statuses
    assert results[0].heal_stage == "fallback"
    assert scenario_after[0]["target"] == "**/api/users/*"
    assert recorder.heal_calls == 0  # Dify not called


def test_mock_status_recovers_via_dify_llm_heal(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """Both primary and fallback invalid → Dify LLM heal's deterministic response fixes target."""
    recorder = monkeypatch_dify(
        heal_response={"target": "**/api/users/*", "value": "500"},
    )
    executor = make_executor()

    scenario = [
        {
            "step": 1,
            "action": "mock_status",
            "target": "",  # invalid
            "value": "500",
            "fallback_targets": [""],  # empty pattern → fallback also fails
            "description": "reaches Dify LLM heal — recovered via mocked response",
        },
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        verify("#error", step=4, condition="contains_text", value="(500)"),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert statuses == ["HEALED", "PASS", "PASS", "PASS"], statuses
    assert results[0].heal_stage == "dify"
    assert scenario_after[0]["target"] == "**/api/users/*"
    assert recorder.heal_calls == 1
