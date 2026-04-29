"""S3-04 — integration check for mock_status + UI error handling.

The real differentiator in v4.1: we can confirm the UI handles a 5xx
response without breaking a live server. This test goes beyond just
installing the route and asserts that the **error UI actually appears
in the DOM**.

Three cases:
- mock 500 → trigger fetch with click → verify error UI shows
- times=1 default → second click hits real network (file:// fetch
  itself fails) → error UI stays
- times=3 → all 3 calls intercepted (counter==3, error UI on every click)
"""

from __future__ import annotations

from helpers.scenarios import click, mock_status, navigate, verify


def test_mock_status_500_shows_error_ui(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        mock_status("**/api/users/*", 500, step=1, description="mock users API 500"),
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        verify("#error", step=4, condition="contains_text", value="서버 에러"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS", "PASS"]


def test_mock_status_times_default_one_only_intercepts_first_call(
    make_executor, run_scenario, fixture_url
):
    """times default = 1: only first call is intercepted, the rest hit the real network.

    With a file:// fixture, real-network calls have fetch rejected and
    fall into the catch's "네트워크 오류" message. So the first call =
    500 error, second call = network error — confirms the messages differ.
    """
    executor = make_executor()
    scenario = [
        mock_status("**/api/users/*", 500, step=1),
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3, description="first call — intercepted by mock"),
        verify("#error", step=4, condition="contains_text", value="(500)"),
        click("#load-btn", step=5, description="second call — real network"),
        verify("#error", step=6, condition="contains_text", value="네트워크"),
        verify("#counter", step=7, condition="contains_text", value="calls:2"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 7


def test_mock_status_times_three_intercepts_three_calls(
    make_executor, run_scenario, fixture_url
):
    """times=3: all three calls intercepted by the mock → (500) message on every click."""
    executor = make_executor()
    scenario = [
        mock_status("**/api/users/*", 503, step=1, times=3),
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        click("#load-btn", step=4),
        click("#load-btn", step=5),
        verify("#counter", step=6, condition="contains_text", value="calls:3"),
        verify("#error", step=7, condition="contains_text", value="(503)"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 7
