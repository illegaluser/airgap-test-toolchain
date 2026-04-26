"""S3-04 — mock_status + UI 예외처리 시나리오 통합 검증.

v4.1 의 진짜 차별점: 실 서버를 망가뜨리지 않고도 UI 가 5xx 응답에 대응하는지
검증할 수 있다. 본 테스트는 단순 라우트 설치가 아니라 **DOM 에 에러 UI 가
실제로 노출되는지** 까지 검증한다.

3 케이스:
- 500 모킹 → click 으로 fetch 트리거 → 에러 UI 노출 verify
- times=1 기본 → 두 번째 클릭은 실네트워크 (file:// 라 fetch 자체 실패) → 에러 UI 그대로
- times=3 → 3 회 모두 가로채기 (counter==3, 매 클릭마다 에러 UI 가 동일하게)
"""

from __future__ import annotations

from helpers.scenarios import click, mock_status, navigate, verify


def test_mock_status_500_shows_error_ui(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        mock_status("**/api/users/*", 500, step=1, description="users API 500 모킹"),
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        verify("#error", step=4, condition="contains_text", value="서버 에러"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS", "PASS"]


def test_mock_status_times_default_one_only_intercepts_first_call(
    make_executor, run_scenario, fixture_url
):
    """times 기본 1: 첫 호출만 가로채고 후속 호출은 실네트워크.

    file:// fixture 에서 실네트워크 호출은 fetch 가 reject 되어 catch 의
    "네트워크 오류" 메시지로 빠진다. 이로써 첫번째 = 500 에러, 두번째 =
    네트워크 오류로 메시지가 달라짐을 검증.
    """
    executor = make_executor()
    scenario = [
        mock_status("**/api/users/*", 500, step=1),
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3, description="첫 호출 — mock 가로채기"),
        verify("#error", step=4, condition="contains_text", value="(500)"),
        click("#load-btn", step=5, description="두 번째 호출 — 실네트워크"),
        verify("#error", step=6, condition="contains_text", value="네트워크"),
        verify("#counter", step=7, condition="contains_text", value="calls:2"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 7


def test_mock_status_times_three_intercepts_three_calls(
    make_executor, run_scenario, fixture_url
):
    """times=3: 세 번 모두 mock 가로채기 → 매 클릭마다 (500) 메시지."""
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
