"""performance 액션 (Sprint 6 / 측정 액션 4/5) 테스트.

page load time 측정 + 임계값 비교. file:// 픽스처는 ms 단위로 빠르게 로드되므로
관대한 임계는 PASS, 비현실적 낮은 임계는 FAIL 로 분기 검증.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import QAExecutor


def _nav(url: str) -> dict:
    return {"step": 1, "action": "navigate", "target": "", "value": url, "description": "init"}


# ─────────────────────────────────────────────────────────────────────────
# Unit — value validation
# ─────────────────────────────────────────────────────────────────────────


def test_performance_rejects_non_integer(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="ms 정수 아님"):
        executor._execute_performance(
            page=None,
            step={"action": "performance", "target": "", "value": "3s"},
            artifacts="",
        )


def test_performance_rejects_zero_or_negative(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="양의 정수"):
        executor._execute_performance(
            page=None,
            step={"action": "performance", "target": "", "value": "0"},
            artifacts="",
        )


# ─────────────────────────────────────────────────────────────────────────
# Integration — 실제 페이지 navigate 후 timing 측정
# ─────────────────────────────────────────────────────────────────────────


def test_performance_pass_with_generous_threshold(fixture_url, make_executor, run_scenario):
    """60초 임계 — file:// 페이지는 항상 그 이내 로드."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "performance", "target": "", "value": "60000",
         "description": "관대한 임계"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_performance_fail_with_unrealistic_threshold(fixture_url, make_executor, run_scenario):
    """1ms 임계 — 어떤 페이지도 그 이내 load 못 함."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "performance", "target": "", "value": "1",
         "description": "비현실적 임계"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert results[0].status == "PASS"
    assert results[1].status == "FAIL"


def test_performance_result_records_elapsed(fixture_url, make_executor, run_scenario):
    """PASS 결과 value 에 'Nms/Mms' 형식으로 측정값 기록."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "performance", "target": "", "value": "60000",
         "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert results[1].status == "PASS"
    # value 형식: "<elapsed>ms/<threshold>ms"
    assert "ms/60000ms" in results[1].value
