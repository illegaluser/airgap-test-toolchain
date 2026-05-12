"""visual_diff 액션 (Sprint 6 / 측정 액션 5/5) 테스트.

auto 모드 (golden 신규 생성) + 동일 페이지 0% diff + 다른 페이지 임계 초과 FAIL
세 시나리오로 핵심 분기 커버. PIL ``ImageChops.difference`` 픽셀 비교.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import QAExecutor


def _nav(url: str, step: int = 1) -> dict:
    return {"step": step, "action": "navigate", "target": "", "value": url, "description": ""}


# ─────────────────────────────────────────────────────────────────────────
# Unit — target/value validation
# ─────────────────────────────────────────────────────────────────────────


def test_visual_diff_rejects_empty_target(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="target .* 필수"):
        executor._execute_visual_diff(
            page=None,
            step={"action": "visual_diff", "target": "", "value": "1.0"},
            artifacts="",
        )


def test_visual_diff_rejects_invalid_value(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="auto.*아님"):
        executor._execute_visual_diff(
            page=None,
            step={"action": "visual_diff", "target": "golden.png", "value": "fast"},
            artifacts="",
        )


def test_visual_diff_rejects_out_of_range_value(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="0~100"):
        executor._execute_visual_diff(
            page=None,
            step={"action": "visual_diff", "target": "golden.png", "value": "150"},
            artifacts="",
        )


# ─────────────────────────────────────────────────────────────────────────
# Integration — file:// 픽스처 + PIL 픽셀 비교
# ─────────────────────────────────────────────────────────────────────────


def test_visual_diff_auto_creates_golden(fixture_url, make_executor, run_scenario, tmp_path):
    """auto 모드 + golden 미존재 → 현재 스크린샷을 golden 으로 복사 + PASS."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "visual_diff", "target": "golden_home.png", "value": "auto",
         "description": "golden 신규 생성"},
    ]
    executor = make_executor()
    results, _, artifacts = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]
    # artifacts/golden_home.png 가 실제로 생성됐는지 확인.
    assert (artifacts / "golden_home.png").is_file()


def test_visual_diff_same_page_zero_diff(fixture_url, make_executor, run_scenario):
    """auto 로 golden 생성 → 같은 페이지 재진입 → 1% 임계 이내 PASS."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "visual_diff", "target": "golden_home.png", "value": "auto",
         "description": "1차 — golden 만듦"},
        _nav(fixture_url("navigate.html"), step=3),
        {"step": 4, "action": "visual_diff", "target": "golden_home.png", "value": "1.0",
         "description": "2차 — 같은 페이지 비교"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS"] * 4


def test_visual_diff_different_page_fails(fixture_url, make_executor, run_scenario):
    """navigate.html 의 golden 생성 후 dialog_alert.html 비교 → 콘텐츠 다름 → FAIL."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "visual_diff", "target": "golden_home.png", "value": "auto",
         "description": ""},
        _nav(fixture_url("dialog_alert.html"), step=3),
        {"step": 4, "action": "visual_diff", "target": "golden_home.png", "value": "0.5",
         "description": "0.5% 임계 — 다른 페이지라 초과"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results[:3]] == ["PASS"] * 3
    assert results[3].status == "FAIL"


def test_visual_diff_missing_golden_fails(fixture_url, make_executor, run_scenario):
    """auto 가 아닌 모드 + golden 미존재 → FAIL."""
    scenario = [
        _nav(fixture_url("navigate.html")),
        {"step": 2, "action": "visual_diff", "target": "nonexistent_golden.png", "value": "1.0",
         "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert results[0].status == "PASS"
    assert results[1].status == "FAIL"
