"""hover 액션 — 3 케이스."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_hover_shows_tooltip(page: Page, fixture_url):
    """hover 시 CSS :hover 로 tooltip 이 `display: block` 된다."""
    page.goto(fixture_url("hover.html"))
    tooltip = page.locator("#tooltip-content")
    expect(tooltip).to_be_hidden()

    page.locator("#tooltip-trigger").hover()
    expect(tooltip).to_be_visible()


def test_hover_opens_submenu(page: Page, fixture_url):
    """메뉴 아이템 hover 시 submenu 가 열린다."""
    page.goto(fixture_url("hover.html"))
    submenu = page.locator("#submenu-file")
    expect(submenu).to_be_hidden()

    page.locator("#menu-file").hover()
    expect(submenu).to_be_visible()


def test_hover_on_always_hidden_times_out(page: Page, fixture_url):
    """display:none 엘리먼트 hover 시도 → Playwright timeout (actionability 대기)."""
    page.goto(fixture_url("hover.html"))
    with pytest.raises(Error):
        page.locator("#always-hidden").hover(timeout=1500)
