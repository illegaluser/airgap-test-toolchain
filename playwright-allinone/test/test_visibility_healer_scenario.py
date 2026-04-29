"""T-H — Visibility Healer scenario integration tests.

In a separate file because make_executor spins up its own
sync_playwright context and would clash with the module-scoped
playwright context used by the unit tests.
"""

from __future__ import annotations

from pathlib import Path


FIXTURES_DIR = Path(__file__).parent / "fixtures"
HOVER_MENU_URL = (FIXTURES_DIR / "hover_menu.html").as_uri()


def test_full_scenario_click_hidden_submenu_link_passes(
    make_executor, run_scenario,
):
    """Even when codegen emits only the click (hover step missing), the
    visibility healer recovers by hovering the ancestor and the scenario
    runs PASS end-to-end."""
    executor = make_executor()
    scenario = [
        {
            "step": 1, "action": "navigate", "target": "",
            "value": HOVER_MENU_URL, "description": "nav",
            "fallback_targets": [],
        },
        {
            "step": 2, "action": "click", "target": "#link-about",
            "value": "", "description": "click About (simulating missing hover)",
            "fallback_targets": [],
        },
        {
            "step": 3, "action": "verify", "target": "#last-clicked",
            "value": "about", "description": "verdict",
            "fallback_targets": [], "condition": "text",
        },
    ]
    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"actual: {statuses}"


# T-H (D)(E)(F) — lazy menu (height:0 right after load, activated via body mouseover)
LAZY_MENU_URL = (FIXTURES_DIR / "lazy_menu.html").as_uri()


def test_full_scenario_lazy_menu_click_passes(make_executor, run_scenario):
    """Site where GNB links are height:0 right after load — the page-level
    activator probe triggers a body hover, the menu unfolds, and click PASSes."""
    executor = make_executor()
    scenario = [
        {
            "step": 1, "action": "navigate", "target": "", "value": LAZY_MENU_URL,
            "description": "nav", "fallback_targets": [],
        },
        {
            "step": 2, "action": "click", "target": "role=link, name=회사소개",
            "value": "", "description": "click GNB (lazy menu)",
            "fallback_targets": [],
        },
        {
            "step": 3, "action": "verify", "target": "#last-clicked",
            "value": "company", "description": "verdict",
            "fallback_targets": [], "condition": "text",
        },
    ]
    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"actual: {statuses}"


# T-H (G) — JS dispatch click fallback for height:0 / line-height:0 anchors
ZERO_HEIGHT_ANCHOR_URL = (FIXTURES_DIR / "zero_height_anchor.html").as_uri()


def test_full_scenario_zero_height_anchor_click_passes_via_js_click(
    make_executor, run_scenario,
):
    """ktds.com pattern — the anchor's computed style is height:0 /
    line-height:0, so Playwright's normal and force click both refuse.
    The JS dispatchEvent('click') fallback fires the DOM event directly,
    the listener runs → verify PASSes.
    """
    executor = make_executor()
    scenario = [
        {
            "step": 1, "action": "navigate", "target": "",
            "value": ZERO_HEIGHT_ANCHOR_URL,
            "description": "nav", "fallback_targets": [],
        },
        {
            "step": 2, "action": "click", "target": "role=link, name=회사소개",
            "value": "", "description": "click zero-height anchor",
            "fallback_targets": [],
        },
        {
            "step": 3, "action": "verify", "target": "#last-clicked",
            "value": "zero", "description": "verdict",
            "fallback_targets": [], "condition": "text",
        },
    ]
    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"actual: {statuses}"
