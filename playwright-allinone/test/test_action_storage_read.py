"""storage_read 액션 (Sprint 6 / 측정 액션 2/5) 테스트.

file:// origin 에서 localStorage / sessionStorage 가 동작하는 Chromium 특성을
이용해 통합 검증. button 클릭으로 storage 에 값 set → storage_read 로 검증.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import QAExecutor


# ─────────────────────────────────────────────────────────────────────────
# Unit — target/value validation
# ─────────────────────────────────────────────────────────────────────────


def test_storage_read_rejects_unknown_scope(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="알 수 없는 scope"):
        executor._execute_storage_read(
            page=None,
            step={"action": "storage_read", "target": "cookie:sid", "value": "x"},
            artifacts="",
        )


def test_storage_read_rejects_empty_key(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="key 가 비어있음"):
        executor._execute_storage_read(
            page=None,
            step={"action": "storage_read", "target": "local:", "value": "x"},
            artifacts="",
        )


# ─────────────────────────────────────────────────────────────────────────
# Integration — file:// 픽스처
# ─────────────────────────────────────────────────────────────────────────


def _nav(url: str) -> dict:
    return {"step": 1, "action": "navigate", "target": "", "value": url, "description": "init"}


def test_storage_read_local_exact_match(fixture_url, make_executor, run_scenario):
    scenario = [
        _nav(fixture_url("storage_set.html")),
        {"step": 2, "action": "click", "target": "#set-local", "value": "", "description": ""},
        {"step": 3, "action": "storage_read", "target": "local:cart_id", "value": "abc123",
         "description": "localStorage 검증"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_storage_read_session_exact_match(fixture_url, make_executor, run_scenario):
    scenario = [
        _nav(fixture_url("storage_set.html")),
        {"step": 2, "action": "click", "target": "#set-session", "value": "", "description": ""},
        {"step": 3, "action": "storage_read", "target": "session:flow", "value": "checkout",
         "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_storage_read_existence_only(fixture_url, make_executor, run_scenario):
    """value 가 빈 문자열이면 key 존재만 검증 — 값 무관."""
    scenario = [
        _nav(fixture_url("storage_set.html")),
        {"step": 2, "action": "click", "target": "#set-local", "value": "", "description": ""},
        {"step": 3, "action": "storage_read", "target": "local:cart_id", "value": "",
         "description": "존재만 검증"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_storage_read_missing_key_fails(fixture_url, make_executor, run_scenario):
    scenario = [
        _nav(fixture_url("storage_set.html")),
        # set-local 클릭 안 함 → cart_id 미존재
        {"step": 2, "action": "storage_read", "target": "local:cart_id", "value": "",
         "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert results[0].status == "PASS"
    assert results[1].status == "FAIL"


def test_storage_read_value_mismatch_fails(fixture_url, make_executor, run_scenario):
    scenario = [
        _nav(fixture_url("storage_set.html")),
        {"step": 2, "action": "click", "target": "#set-local", "value": "", "description": ""},
        {"step": 3, "action": "storage_read", "target": "local:cart_id", "value": "WRONG",
         "description": ""},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results[:2]] == ["PASS", "PASS"]
    assert results[2].status == "FAIL"


def test_storage_read_scope_default_local(fixture_url, make_executor, run_scenario):
    """target 에 콜론 없으면 local 로 디폴트."""
    scenario = [
        _nav(fixture_url("storage_set.html")),
        {"step": 2, "action": "click", "target": "#set-local", "value": "", "description": ""},
        {"step": 3, "action": "storage_read", "target": "cart_id", "value": "abc123",
         "description": "scope 생략 → local"},
    ]
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]
