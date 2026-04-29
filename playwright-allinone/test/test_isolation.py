"""T-B (P0.3-A) — 클라이언트 측 세션 격리 회귀 테스트.

검증 영역:
- BrowserContext 가 시나리오마다 fresh 한지 (separate `execute()` 호출 시
  쿠키/로컬스토리지/세션스토리지/IDB 격리)
- `reset_state` DSL 액션이 같은 컨텍스트 안에서도 흔적을 비우는지
  (cookie / storage / indexeddb / all 4 케이스)
- CLI 검증이 reset_state 의 value 화이트리스트를 통과/거부하는지
"""

from __future__ import annotations

from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
ISOLATION_A_URL = (FIXTURES_DIR / "isolation_a.html").as_uri()
ISOLATION_B_URL = (FIXTURES_DIR / "isolation_b.html").as_uri()


# ─────────────────────────────────────────────────────────────────────
# CLI 검증 — reset_state 화이트리스트 회귀
# ─────────────────────────────────────────────────────────────────────


def _scenario_with(step: dict) -> list[dict]:
    return [
        {
            "step": 1, "action": "navigate", "target": "",
            "value": ISOLATION_A_URL, "description": "nav",
            "fallback_targets": [],
        },
        step,
    ]


@pytest.mark.parametrize("scope", ["cookie", "storage", "indexeddb", "all"])
def test_cli_validates_reset_state_scope(scope):
    from zero_touch_qa.__main__ import _validate_scenario

    _validate_scenario(_scenario_with({
        "step": 2, "action": "reset_state", "target": "",
        "value": scope, "description": f"reset {scope}",
        "fallback_targets": [],
    }))


def test_cli_rejects_reset_state_invalid_scope():
    from zero_touch_qa.__main__ import _validate_scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(_scenario_with({
            "step": 2, "action": "reset_state", "target": "",
            "value": "everything", "description": "잘못된 scope",
            "fallback_targets": [],
        }))


def test_cli_rejects_reset_state_empty_value():
    from zero_touch_qa.__main__ import _validate_scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(_scenario_with({
            "step": 2, "action": "reset_state", "target": "",
            "value": "", "description": "scope 누락",
            "fallback_targets": [],
        }))


# ─────────────────────────────────────────────────────────────────────
# 격리 회귀 — execute() 호출당 fresh BrowserContext
# ─────────────────────────────────────────────────────────────────────


def _navigate_step(url: str, step: int = 1) -> dict:
    return {
        "step": step, "action": "navigate", "target": "", "value": url,
        "description": f"{url} 로 이동", "fallback_targets": [],
    }


def _verify_step(target: str, value: str, step: int = 2) -> dict:
    return {
        "step": step, "action": "verify", "target": target, "value": value,
        "description": "verdict", "fallback_targets": [],
        "condition": "text",
    }


def _reset_step(scope: str, step: int = 2) -> dict:
    return {
        "step": step, "action": "reset_state", "target": "",
        "value": scope, "description": f"reset {scope}",
        "fallback_targets": [],
    }


def test_separate_execute_calls_have_fresh_context(make_executor, run_scenario):
    """`execute()` 두 번 호출 — 첫 번째는 흔적을 남기고 두 번째 호출은 새 컨텍스트로
    시작해 흔적이 없어야 한다 (B level baseline)."""
    executor1 = make_executor()
    results1, _, _ = run_scenario(executor1, [_navigate_step(ISOLATION_A_URL)])
    assert results1[0].status == "PASS"

    # 다른 executor 인스턴스 = 다른 sync_playwright = 새 BrowserContext.
    executor2 = make_executor()
    results2, _, _ = run_scenario(executor2, [
        _navigate_step(ISOLATION_B_URL),
        _verify_step("#verdict", "ISOLATED"),
    ])
    statuses = [r.status for r in results2]
    assert statuses == ["PASS", "PASS"], f"두 번째 실행에서 흔적 누출: {statuses}"


def test_reset_state_all_clears_within_same_context(make_executor, run_scenario):
    """단일 시나리오 안에서도 reset_state value=all 후 verify 가 ISOLATED 여야 한다."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("all", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "B 로 이동",
            "fallback_targets": [],
        },
        _verify_step("#verdict", "ISOLATED", step=4),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS", "PASS"], f"실제: {statuses}"


def test_reset_state_storage_only_keeps_cookies(make_executor, run_scenario):
    """value=storage 는 localStorage/sessionStorage 만 비우고 쿠키는 보존.

    cookie 가 file:// 에서는 어차피 안 들어가므로, A→B 이동 후 cookie marker 는
    어떤 경우에도 ABSENT. 본 테스트는 localStorage 만 비워졌는지 확인."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("storage", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "B 로 이동",
            "fallback_targets": [],
        },
        _verify_step("#local-status", "ABSENT", step=4),
        _verify_step("#session-status", "ABSENT", step=5),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS"] * 5, f"실제: {statuses}"


def test_reset_state_indexeddb_clears_idb_only(make_executor, run_scenario):
    """value=indexeddb 는 IDB 만 비운다."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("indexeddb", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "B 로 이동",
            "fallback_targets": [],
        },
        _verify_step("#idb-status", "ABSENT", step=4),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS"] * 4, f"실제: {statuses}"


def test_reset_state_all_invokes_clear_permissions(monkeypatch):
    """리뷰 #3 회귀 — value=all 은 cookie + storage + indexeddb +
    permissions reset 까지 한다 (docs/PLAN_PRODUCTION_READINESS.md §T-B Day 2).

    실제 Playwright 호출을 흉내내는 stub context 로 clear_permissions 가
    호출되는지 확인. 이전엔 cookie/storage/IDB 만 처리되어 권한 노출 위험."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.executor import QAExecutor

    calls: list[str] = []

    class _Ctx:
        def clear_cookies(self): calls.append("clear_cookies")
        def clear_permissions(self): calls.append("clear_permissions")

    class _Page:
        def __init__(self): self.context = _Ctx()
        def evaluate(self, script): calls.append(f"evaluate:{script[:30]}")

    cfg = Config(
        dify_base_url="http://test/v1", dify_api_key="x",
        artifacts_dir="/tmp/_t_b_perm_test",
        viewport=(1280, 800), slow_mo=0,
        headed_step_pause_ms=0, step_interval_min_ms=0, step_interval_max_ms=0,
        heal_threshold=0.8, heal_timeout_sec=10, scenario_timeout_sec=60,
        dom_snapshot_limit=4000,
    )
    executor = QAExecutor(cfg)
    # _screenshot 은 실제 page.screenshot 을 호출하므로 우회.
    monkeypatch.setattr(executor, "_screenshot", lambda *a, **kw: "")

    import os as _os
    _os.makedirs("/tmp/_t_b_perm_test", exist_ok=True)

    result = executor._execute_reset_state(
        _Page(),
        {"step": 1, "action": "reset_state", "target": "", "value": "all",
         "description": "test"},
        "/tmp/_t_b_perm_test",
    )
    assert result.status == "PASS"
    assert "clear_cookies" in calls
    assert "clear_permissions" in calls, (
        f"reset_state all 이 clear_permissions 를 호출하지 않음 (review #3). 호출: {calls}"
    )
    # localStorage / IDB 도 호출됐는지 확인 (소문자 매칭).
    joined = "|".join(calls)
    assert "evaluate" in joined


def test_isolation_pass_rate_meets_95pct_threshold(
    make_executor, run_scenario, monkeypatch,
):
    """T-B 수락 기준 — 동일 시나리오 N회 연속 통과율 ≥95%.

    기본 N=5 (CI 친화). 환경변수 ``T_B_REPEAT=100`` 으로 풀 측정 가능.
    docs/PLAN_PRODUCTION_READINESS.md §"T-B Day 5 — 측정" 의 ≥95% 기준을 회귀
    형태로 가두는 것이 목적 — 통과율 회귀가 발생하면 즉시 노출된다.
    """
    import os as _os
    n = int(_os.environ.get("T_B_REPEAT", "5"))

    scenario = [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("all", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "B 로 이동",
            "fallback_targets": [],
        },
        _verify_step("#verdict", "ISOLATED", step=4),
    ]

    passes = 0
    for _ in range(n):
        executor = make_executor()
        results, _scn, _art = run_scenario(executor, scenario)
        if all(r.status == "PASS" for r in results):
            passes += 1

    rate = passes / n
    # 5회는 분산이 커서 100% 만 합격 (1 fail = 80% < 95%). 100회는 95개 이상.
    threshold = 0.95
    assert rate >= threshold, (
        f"통과율 {passes}/{n} = {rate:.0%} < {threshold:.0%} (T-B 수락 기준 미달)"
    )


def test_without_reset_state_storage_leaks_within_same_context(
    make_executor, run_scenario,
):
    """음성 회귀 — reset_state 없이 같은 컨텍스트에서 A→B 이동 시 localStorage 가
    그대로 남아 verdict 가 LEAKED. reset_state 의 효용을 입증하는 baseline."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        {
            "step": 2, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "B 로 이동",
            "fallback_targets": [],
        },
        _verify_step("#local-status", "PRESENT", step=3),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"실제: {statuses}"
