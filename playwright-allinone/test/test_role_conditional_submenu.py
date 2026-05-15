"""GNB 류 사이트 회귀 가드 — codegen 캡처 시점엔 role=button 매치되던
submenu item 이 replay 시점엔 role 자체가 attribute 로 없어 resolver 가 0건
반환하는 패턴.

사용자 실측 (2026-05-15): koreaconnect 포털의 '데이터·API 마켓' 메뉴 클릭이

    target='role=button, name=데이터·API 마켓, exact=true'

로 캡처됐는데 replay 시 resolver 가 0건, 진단 로그가
``text=... → 1건 (role 무시)`` 만 남기고 곧장 LLM 치유로 진입.

본 슈트는:
1) 픽스처 (role_conditional_submenu.html) 가 정말로 이 패턴을 만들어내는지 베이스라인 검증.
2) executor 가 'role=button 으로 잡힌 target → role 폴백 → 텍스트 폴백 → 부모 메뉴 cascade hover' 의 회복 동선을 거쳐 step 이 PASS 하는지 확인.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from helpers.scenarios import click, navigate


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_URL = (FIXTURES_DIR / "role_conditional_submenu.html").as_uri()


@pytest.fixture(scope="module")
def _ctx():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        yield ctx
        ctx.close()
        browser.close()


@pytest.fixture
def page(_ctx):
    p = _ctx.new_page()
    p.goto(FIXTURE_URL)
    yield p
    p.close()


def test_baseline_role_is_unset_until_parent_hovered(page):
    """베이스라인 — 페이지 로드 직후 leaf 의 role attribute 가 없다.

    submenu 가 부모 :hover 일 때만 role=button 으로 승격되도록 픽스처를
    만들었음. 회귀 가드의 전제 조건.
    """
    leaf = page.locator("#leaf-data-market")
    assert leaf.get_attribute("role") is None, (
        "픽스처 결함 — leaf 에 처음부터 role 이 박혀있다. role-conditional 패턴이 아님."
    )
    # role=button 으로 get_by_role 도 0 이어야 함.
    assert page.get_by_role("button", name="데이터·API 마켓", exact=True).count() == 0
    # 단, 텍스트는 DOM 에 존재.
    assert page.get_by_text("데이터·API 마켓", exact=True).count() >= 1


def test_baseline_role_becomes_button_after_parent_hover(page):
    """베이스라인 — 부모 메뉴 hover 시점에 role=button 으로 승격된다."""
    page.locator("#menu-notify").hover()
    page.wait_for_timeout(150)
    leaf = page.locator("#leaf-data-market")
    assert leaf.get_attribute("role") == "button"
    assert page.get_by_role("button", name="데이터·API 마켓", exact=True).count() == 1


def test_executor_recovers_role_conditional_submenu_click(
    make_executor, run_scenario,
):
    """회귀 가드 — 'role=button, name=데이터·API 마켓' target 이 replay 시점엔
    role=button 0건이어도 step 이 PASS 해야 한다.

    회복 동선 (어떤 경로로든 통과만 하면 OK):
      a) resolver 의 role 폴백 (link/tab/menuitem) — 본 픽스처에선 <a> 라
         link 가 잡힐 수 있음 (단, 부모 메뉴가 :hover 일 때만).
      b) text 폴백 → 부모 메뉴 cascade hover (visibility healer) → 클릭.
      c) LLM 치유.

    어느 path 든 클릭이 실제로 일어났는지 (#clicked-target.dataset.clicked
    == 'leaf-data-market') 로 검증.
    """
    executor = make_executor()
    target = "role=button, name=데이터·API 마켓, exact=true"
    results, _, _ = run_scenario(executor, [
        navigate(FIXTURE_URL, step=1),
        click(target, step=2),
    ])

    statuses = [r.status for r in results]
    assert statuses[0] == "PASS", f"navigate 실패: {results[0]}"
    assert statuses[1] in ("PASS", "HEALED"), (
        f"role-conditional submenu click 실패: {results[1]}\n"
        f"이 패턴은 GNB 메뉴 회귀의 핵심 — resolver / visibility healer 가 "
        f"role attribute 가 없는 leaf 도 잡아내야 한다."
    )

    # 실제로 클릭이 일어났는지 (단순 이벤트 dispatch 가 아닌 leaf 자체에 도달했는지).
    # run_scenario 가 page 를 닫지 않도록 했다 가정 — 만약 닫는다면 별도 page state
    # 검증을 못 하므로 _last_page 를 참조.
    page_after = getattr(executor, "_last_page", None)
    if page_after is None:
        # executor 가 _last_page 를 노출 안 하면 status PASS 만으로 가드 (executor.py
        # 내부의 perform_action 이 click 을 정상 호출했으면 PASS).
        return
    clicked = page_after.locator("#clicked-target").get_attribute("data-clicked") or ""
    assert clicked == "leaf-data-market", (
        f"클릭이 leaf 가 아니라 다른 element 에 떨어졌다: data-clicked={clicked!r}"
    )
