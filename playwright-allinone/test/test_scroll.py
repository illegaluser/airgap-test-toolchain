"""S3-03 — scroll 액션 통합 검증.

2 케이스:
- happy: viewport 밖 #footer 가 scroll 후 visible 로 검증
- into_view 외 value 는 _validate_scenario 단계에서 거부
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
