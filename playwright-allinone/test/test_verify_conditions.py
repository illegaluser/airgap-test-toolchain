"""S3-06 — integration coverage for each verify.condition branch.

7 cases (5 happy + 2 negative validation):
- visible / hidden / disabled / enabled / checked / value / text(=contains_text)
- empty condition + value → interpreted as contains
- unknown condition rejected by _validate_scenario
"""

from __future__ import annotations

import pytest

from zero_touch_qa.__main__ import ScenarioValidationError, _validate_scenario
from helpers.scenarios import navigate, verify


def _scenario(fixture_url, *verify_steps) -> list[dict]:
    return [navigate(fixture_url("verify_conditions.html"), step=1), *verify_steps]


def test_verify_visible(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = _scenario(fixture_url, verify("#visible-box", step=2, condition="visible"))
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_hidden(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = _scenario(fixture_url, verify("#hidden-box", step=2, condition="hidden"))
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_disabled_and_enabled(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#btn-disabled", step=2, condition="disabled"),
        verify("#btn-enabled", step=3, condition="enabled"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_verify_checked(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = _scenario(fixture_url, verify("#cb-checked", step=2, condition="checked"))
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_value_exact_match(make_executor, run_scenario, fixture_url):
    """condition='value' does an exact match on form input values."""
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#text-input", step=2, condition="value", value="exact-value-42"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_text_substring_contains(make_executor, run_scenario, fixture_url):
    """condition='text' (aliases contains_text/contains) does substring contains."""
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#contain-paragraph", step=2, condition="contains_text", value="12,345"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_blank_condition_with_value_means_contains(make_executor, run_scenario, fixture_url):
    """Empty condition with a value is interpreted as contains (matches executor branch)."""
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#contain-paragraph", step=2, condition="", value="가입자"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_unknown_condition_is_demoted_at_validation():
    """Conditions outside the whitelist are demoted to "" (default fallback), not rejected."""
    bad = [
        navigate("file:///x", step=1),
        verify("#x", step=2, condition="exists"),
    ]
    _validate_scenario(bad)
    assert bad[1]["condition"] == ""
