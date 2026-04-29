"""S3-05 — integration check for mock_data + empty-response scenarios.

Three cases:
- empty array response → "데이터 없음" UI shows
- one-item response (dict body input) → list render check
- JSON string input behaves identically
"""

from __future__ import annotations

from helpers.scenarios import click, mock_data, navigate, verify


def test_mock_data_empty_array_shows_empty_ui(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        mock_data("**/api/list", {"items": []}, step=1, description="empty array response"),
        navigate(fixture_url("mock_data.html"), step=2),
        click("#load-btn", step=3),
        verify("#empty", step=4, condition="visible"),
        verify("#empty", step=5, condition="contains_text", value="데이터 없음"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 5


def test_mock_data_with_dict_body_renders_items(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        mock_data(
            "**/api/list",
            {"items": [{"name": "alpha", "value": "1"}, {"name": "beta", "value": "2"}]},
            step=1,
        ),
        navigate(fixture_url("mock_data.html"), step=2),
        click("#load-btn", step=3),
        verify("#list", step=4, condition="contains_text", value="alpha:1"),
        verify("#list", step=5, condition="contains_text", value="beta:2"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 5


def test_mock_data_with_json_string_body_works_identically(
    make_executor, run_scenario, fixture_url
):
    executor = make_executor()
    scenario = [
        mock_data(
            "**/api/list",
            '{"items":[{"name":"gamma","value":"3"}]}',
            step=1,
            description="JSON string input",
        ),
        navigate(fixture_url("mock_data.html"), step=2),
        click("#load-btn", step=3),
        verify("#list", step=4, condition="contains_text", value="gamma:3"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 4
