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
    healer 가 ancestor hover 후 복구해 시나리오가 끝까지 간다.

    2026-05-11 — visibility heal 이 살린 케이스는 status=HEALED / heal_stage=
    visibility 로 분류된다 (이전엔 PASS/heal_stage=none 으로 *기록 누락*
    이었음 — regression_generator 가 visibility 사실을 모르고 raw click 만
    emit해 Replay UI 에서 깨지던 root cause).
    """
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
    assert statuses == ["PASS", "HEALED", "PASS"], f"실제: {statuses}"
    # step 2 는 visibility healer 가 통과시켰음 — heal_stage / pre_actions 보존.
    assert results[1].heal_stage == "visibility"
    assert results[1].pre_actions, (
        "visibility heal 의 hover 시퀀스가 pre_actions 에 기록되어야 한다"
    )
    assert all(p.get("action") == "hover" for p in results[1].pre_actions)


# T-H (D)(E)(F) — lazy menu (페이지 로드 직후 height:0, body mouseover 로 활성화)
LAZY_MENU_URL = (FIXTURES_DIR / "lazy_menu.html").as_uri()


def test_full_scenario_lazy_menu_click_passes(make_executor, run_scenario):
    """페이지 로드 직후 GNB link 가 height:0 인 사이트 — page-level activator
    probe 가 body 에 hover 를 트리거해 menu 가 펼쳐지면 click 통과.

    2026-05-11 — visibility heal 작동 시 HEALED/visibility 분류로 명시.
    """
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
    assert statuses == ["PASS", "HEALED", "PASS"], f"실제: {statuses}"
    assert results[1].heal_stage == "visibility"
    # lazy menu 의 통과 경로는 사이트에 따라 (cascade hover / page-level hover /
    # size poll) 다양 — pre_actions 가 비어있을 수도 있고(scroll/sibling) 채워질
    # 수도 있음. 본 테스트는 *heal_stage 가 visibility 인 것* 만 단언.


# T-H (G) — height:0 / line-height:0 anchor 에 JS dispatch click 폴백
ZERO_HEIGHT_ANCHOR_URL = (FIXTURES_DIR / "zero_height_anchor.html").as_uri()


def test_full_scenario_zero_height_anchor_click_passes_via_js_click(
    make_executor, run_scenario,
):
    """ktds.com 패턴 — anchor 의 computed style 이 height:0/line-height:0 이라
    Playwright 의 normal/force click 모두 거부. JS dispatchEvent('click') 폴백이
    DOM event 를 직접 발화하여 listener 가 동작 → verify PASS.
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
            "value": "", "description": "zero-height anchor 클릭",
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
    assert statuses == ["PASS", "PASS", "PASS"], f"실제: {statuses}"
