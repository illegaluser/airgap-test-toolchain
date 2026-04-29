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
