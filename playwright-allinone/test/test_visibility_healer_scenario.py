"""T-H — Visibility Healer 시나리오 통합 테스트.

별도 파일 — make_executor 가 자체 sync_playwright 를 띄우므로 module-scoped
playwright context 와 충돌하는 단위 테스트와 분리.
"""

from __future__ import annotations

from pathlib import Path


FIXTURES_DIR = Path(__file__).parent / "fixtures"
HOVER_MENU_URL = (FIXTURES_DIR / "hover_menu.html").as_uri()


def test_full_scenario_click_hidden_submenu_link_passes(
    make_executor, run_scenario,
):
    """codegen 이 hover step 을 빠뜨린 형태의 click 만 emit 해도 visibility
    healer 가 ancestor hover 후 복구해 시나리오가 PASS 끝까지 간다."""
    executor = make_executor()
    scenario = [
        {
            "step": 1, "action": "navigate", "target": "",
            "value": HOVER_MENU_URL, "description": "nav",
            "fallback_targets": [],
        },
        {
            "step": 2, "action": "click", "target": "#link-about",
            "value": "", "description": "About 클릭 (hover 누락 시뮬레이션)",
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
    assert statuses == ["PASS", "PASS", "PASS"], f"실제: {statuses}"


# T-H (D)(E)(F) — lazy menu (페이지 로드 직후 height:0, body mouseover 로 활성화)
LAZY_MENU_URL = (FIXTURES_DIR / "lazy_menu.html").as_uri()


def test_full_scenario_lazy_menu_click_passes(make_executor, run_scenario):
    """페이지 로드 직후 GNB link 가 height:0 인 사이트 — page-level activator
    probe 가 body 에 hover 를 트리거해 menu 가 펼쳐지면 click PASS."""
    executor = make_executor()
    scenario = [
        {
            "step": 1, "action": "navigate", "target": "", "value": LAZY_MENU_URL,
            "description": "nav", "fallback_targets": [],
        },
        {
            "step": 2, "action": "click", "target": "role=link, name=회사소개",
            "value": "", "description": "GNB 클릭 (lazy menu)",
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
    assert statuses == ["PASS", "PASS", "PASS"], f"실제: {statuses}"
