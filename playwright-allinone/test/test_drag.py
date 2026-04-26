"""S3-02 — drag 액션 통합 검증.

2 케이스:
- happy: source → destination drop 후 dst[data-dropped] == "yes"
- destination 미존재: drag 실행 시 RuntimeError 로 FAIL (resolver 가 value 측 못 찾음)
"""

from __future__ import annotations

from helpers.scenarios import drag, navigate, verify


def test_drag_moves_card_to_destination(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    scenario = [
        navigate(fixture_url("drag.html"), step=1),
        drag("#card", "#dst", step=2, description="카드를 우측 컬럼으로 이동"),
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
    # source 는 찾았지만 destination 미존재 — RuntimeError 가 fallback chain 으로 가서 FAIL
    assert results[1].status == "FAIL"
