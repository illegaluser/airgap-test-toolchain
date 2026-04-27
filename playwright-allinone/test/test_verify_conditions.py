"""S3-06 — verify.condition 분기별 통합 검증.

7 케이스 (5 happy + 2 negative validation):
- visible / hidden / disabled / enabled / checked / value / text(=contains_text)
- 빈 condition + value → contains 해석
- unknown condition 은 _validate_scenario 거부
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
    """condition='value' 는 input form 값 정확 매칭."""
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#text-input", step=2, condition="value", value="exact-value-42"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_text_substring_contains(make_executor, run_scenario, fixture_url):
    """condition='text' (별칭 contains_text/contains) 는 substring contains."""
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#contain-paragraph", step=2, condition="contains_text", value="12,345"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_blank_condition_with_value_means_contains(make_executor, run_scenario, fixture_url):
    """condition 이 비어있고 value 가 있으면 contains 해석 (executor 분기 호환)."""
    executor = make_executor()
    scenario = _scenario(
        fixture_url,
        verify("#contain-paragraph", step=2, condition="", value="가입자"),
    )
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_verify_unknown_condition_is_demoted_at_validation():
    """화이트리스트 밖 condition 은 reject 대신 ""(default fallback) 로 강등."""
    bad = [
        navigate("file:///x", step=1),
        verify("#x", step=2, condition="exists"),
    ]
    _validate_scenario(bad)
    assert bad[1]["condition"] == ""
