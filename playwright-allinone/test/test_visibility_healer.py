"""T-H — Visibility Healer 단위 테스트.

codegen 이 hover-then-click sequence 의 hover 를 빠뜨려 element 가 hidden 상태로
click 시도되는 케이스를 ancestor hover 로 자동 복구하는지 검증.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


FIXTURES_DIR = Path(__file__).parent / "fixtures"
HOVER_MENU_URL = (FIXTURES_DIR / "hover_menu.html").as_uri()


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
    p.goto(HOVER_MENU_URL)
    yield p
    p.close()


def test_submenu_is_hidden_initially(page):
    """베이스라인 — submenu 의 link 는 hover 전에는 visible 하지 않다."""
    assert not page.locator("#link-about").is_visible()


def test_visibility_healer_makes_hidden_link_clickable(page, make_executor):
    """visibility healer 가 li:hover 트리거 후 hidden link 를 visible 로 만든다."""
    executor = make_executor()
    loc = page.locator("#link-about").first
    assert not loc.is_visible()

    executor._heal_visibility(page, loc, step_id=99)

    # hover 후 visible 이어야 함.
    assert loc.is_visible()


def test_visibility_healer_noop_when_already_visible(page, make_executor):
    """이미 visible 한 element 에는 healer 가 부수 효과 0 — 그대로 visible 유지."""
    executor = make_executor()
    loc = page.locator("#menu-company").first  # nav 최상단 — 항상 visible
    assert loc.is_visible()
    executor._heal_visibility(page, loc, step_id=99)
    assert loc.is_visible()


# ── T-H (B + C) — 다중 매치 + 모바일/데스크탑 dual rendering ────────────────


@pytest.fixture
def dual_match_page(_ctx):
    """모바일 드로어(hidden) + 데스크탑 GNB(visible) 둘 다 같은 라벨을 가진 fixture."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "dual_match_menu.html").as_uri())
    yield p
    p.close()


def test_resolver_prefers_visible_match_over_hidden(dual_match_page):
    """T-H (B) — resolver 가 role+name 매치 시 visible 매치를 우선 선택.

    fixture: 모바일 드로어(transform:scale(0), DOM 순서 0) + 데스크탑 GNB(visible, 순서 1).
    이전엔 `.first` 가 mobile 을 잡아 click timeout. 이제 visible 한 desktop 선택.
    """
    from zero_touch_qa.locator_resolver import LocatorResolver

    resolver = LocatorResolver(dual_match_page)
    loc = resolver.resolve("role=link, name=회사소개")
    assert loc is not None
    assert loc.is_visible()
    where = loc.get_attribute("data-where")
    assert where == "desktop", f"hidden 모바일 드로어가 선택됨: where={where}"


def test_visibility_healer_swaps_via_filter_visible(dual_match_page, make_executor):
    """T-H (C) — `.filter(visible=True)` 로 다중 매치에서 visible 만 추려 swap.

    multi-match locator 가 hidden first 를 잡았을 때 healer 의 _find_visible_sibling
    이 동일 base 의 visible 한 element 로 교체. role+name 외 경로 (text/CSS) 의
    안전망.
    """
    executor = make_executor()
    raw = dual_match_page.get_by_role("link", name="회사소개")
    # raw 는 multi-match locator (count=2). first 는 hidden mobile, second 는 visible desktop.
    assert raw.count() == 2
    assert not raw.first.is_visible()

    swap = executor._find_visible_sibling(raw, step_id=42)
    assert swap is not None
    assert swap.is_visible()
    assert swap.get_attribute("data-where") == "desktop"
