"""S3-03 — scroll action integration check.

Two cases:
- happy: an off-viewport #footer is verified visible after scroll
- values other than into_view are rejected by _validate_scenario
"""

from __future__ import annotations

import pytest

from zero_touch_qa.__main__ import ScenarioValidationError, _validate_scenario
from helpers.scenarios import navigate, scroll, verify


def test_scroll_brings_footer_into_view(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        navigate(fixture_url("scroll.html"), step=1),
        scroll("#footer", step=2),
        verify("#footer", step=3, condition="visible"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_scroll_value_other_than_into_view_is_rejected():
    bad = [
        navigate("file:///x", step=1),
        {"step": 2, "action": "scroll", "target": "#footer", "value": "bottom"},
    ]
    with pytest.raises(ScenarioValidationError, match="into_view"):
        _validate_scenario(bad)
