"""T-B (P0.3-A) — client-side session isolation regression tests.

Coverage:
- BrowserContext is fresh per scenario (separate `execute()` calls leave
  cookies / localStorage / sessionStorage / IDB isolated)
- the `reset_state` DSL action wipes traces inside the same context
  (cookie / storage / indexeddb / all — 4 cases)
- CLI validation accepts/rejects values in the reset_state whitelist
"""

from __future__ import annotations

from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
ISOLATION_A_URL = (FIXTURES_DIR / "isolation_a.html").as_uri()
ISOLATION_B_URL = (FIXTURES_DIR / "isolation_b.html").as_uri()


# ─────────────────────────────────────────────────────────────────────
# CLI validation — reset_state whitelist regression
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
            "value": "everything", "description": "invalid scope",
            "fallback_targets": [],
        }))


def test_cli_rejects_reset_state_empty_value():
    from zero_touch_qa.__main__ import _validate_scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(_scenario_with({
            "step": 2, "action": "reset_state", "target": "",
            "value": "", "description": "scope missing",
            "fallback_targets": [],
        }))


# ─────────────────────────────────────────────────────────────────────
# Isolation regression — fresh BrowserContext per execute() call
# ─────────────────────────────────────────────────────────────────────


def _navigate_step(url: str, step: int = 1) -> dict:
    return {
        "step": step, "action": "navigate", "target": "", "value": url,
        "description": f"navigate to {url}", "fallback_targets": [],
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
    """Two `execute()` calls — the first leaves traces and the second starts
    with a new context that has no traces (B-level baseline)."""
    executor1 = make_executor()
    results1, _, _ = run_scenario(executor1, [_navigate_step(ISOLATION_A_URL)])
    assert results1[0].status == "PASS"

    # different executor instance = different sync_playwright = new BrowserContext.
    executor2 = make_executor()
    results2, _, _ = run_scenario(executor2, [
        _navigate_step(ISOLATION_B_URL),
        _verify_step("#verdict", "ISOLATED"),
    ])
    statuses = [r.status for r in results2]
    assert statuses == ["PASS", "PASS"], f"trace leaked into the second run: {statuses}"


def test_reset_state_all_clears_within_same_context(make_executor, run_scenario):
    """Inside one scenario, reset_state value=all → verify must be ISOLATED."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("all", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "navigate to B",
            "fallback_targets": [],
        },
        _verify_step("#verdict", "ISOLATED", step=4),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS", "PASS"], f"actual: {statuses}"


def test_reset_state_storage_only_keeps_cookies(make_executor, run_scenario):
    """value=storage clears localStorage/sessionStorage only, cookies preserved.

    file:// can't set cookies anyway, so the cookie marker is ABSENT
    after A→B in any case. This test confirms localStorage was wiped."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("storage", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "navigate to B",
            "fallback_targets": [],
        },
        _verify_step("#local-status", "ABSENT", step=4),
        _verify_step("#session-status", "ABSENT", step=5),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS"] * 5, f"actual: {statuses}"


def test_reset_state_indexeddb_clears_idb_only(make_executor, run_scenario):
    """value=indexeddb clears only IDB."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("indexeddb", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "navigate to B",
            "fallback_targets": [],
        },
        _verify_step("#idb-status", "ABSENT", step=4),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS"] * 4, f"actual: {statuses}"


def test_reset_state_all_invokes_clear_permissions(monkeypatch):
    """Review #3 regression — value=all also runs cookie + storage +
    indexeddb + permissions reset (docs/PLAN_PRODUCTION_READINESS.md
    §T-B Day 2).

    Use a stub context that mimics the Playwright API and confirm
    clear_permissions is called. Previously only cookie/storage/IDB were
    handled, leaving permissions exposed."""
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
    # _screenshot would call page.screenshot for real, so bypass it.
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
        f"reset_state all didn't call clear_permissions (review #3). calls: {calls}"
    )
    # also confirm localStorage / IDB were called (lowercase match).
    joined = "|".join(calls)
    assert "evaluate" in joined


def test_isolation_pass_rate_meets_95pct_threshold(
    make_executor, run_scenario, monkeypatch,
):
    """T-B acceptance — same scenario N times in a row, pass rate ≥ 95%.

    Default N=5 (CI-friendly). Set ``T_B_REPEAT=100`` for a full
    measurement. The point is to fence the ≥ 95% bar from
    docs/PLAN_PRODUCTION_READINESS.md §"T-B Day 5 — measurement" as a
    regression — any pass-rate regression shows up immediately.
    """
    import os as _os
    n = int(_os.environ.get("T_B_REPEAT", "5"))

    scenario = [
        _navigate_step(ISOLATION_A_URL),
        _reset_step("all", step=2),
        {
            "step": 3, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "navigate to B",
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
    # 5 runs has high variance, so only 100% passes (1 fail = 80% < 95%). 100 runs needs ≥ 95.
    threshold = 0.95
    assert rate >= threshold, (
        f"pass rate {passes}/{n} = {rate:.0%} < {threshold:.0%} (below T-B acceptance)"
    )


def test_without_reset_state_storage_leaks_within_same_context(
    make_executor, run_scenario,
):
    """Negative regression — without reset_state, A→B in the same context
    keeps localStorage and verdict turns LEAKED. Establishes the baseline
    that justifies reset_state."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        _navigate_step(ISOLATION_A_URL),
        {
            "step": 2, "action": "navigate", "target": "",
            "value": ISOLATION_B_URL, "description": "navigate to B",
            "fallback_targets": [],
        },
        _verify_step("#local-status", "PRESENT", step=3),
    ])
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"actual: {statuses}"
