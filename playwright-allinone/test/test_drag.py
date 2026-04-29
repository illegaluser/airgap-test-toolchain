"""S3-02 — drag action integration check.

Two cases:
- happy: after dropping source → destination, dst[data-dropped] == "yes"
- missing destination: drag execution FAILs with RuntimeError (resolver can't find value side)
"""

from __future__ import annotations

from helpers.scenarios import drag, navigate, verify


def test_drag_moves_card_to_destination(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        navigate(fixture_url("drag.html"), step=1),
        drag("#card", "#dst", step=2, description="move card to right column"),
        verify("#dst", step=3, condition="contains_text", value="CARD"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_drag_to_missing_destination_fails(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        navigate(fixture_url("drag.html"), step=1),
        drag("#card", "#nonexistent-target", step=2),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert results[0].status == "PASS"
    # source found, but destination missing — RuntimeError falls through fallback chain to FAIL
    assert results[1].status == "FAIL"
