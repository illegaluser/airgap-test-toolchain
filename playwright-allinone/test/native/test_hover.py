"""hover action — 3 cases."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_hover_shows_tooltip(page: Page, fixture_url):
    """On hover, CSS :hover sets the tooltip to `display: block`."""
    page.goto(fixture_url("hover.html"))
    tooltip = page.locator("#tooltip-content")
    expect(tooltip).to_be_hidden()

    page.locator("#tooltip-trigger").hover()
    expect(tooltip).to_be_visible()


def test_hover_opens_submenu(page: Page, fixture_url):
    """Hovering a menu item opens its submenu."""
    page.goto(fixture_url("hover.html"))
    submenu = page.locator("#submenu-file")
    expect(submenu).to_be_hidden()

    page.locator("#menu-file").hover()
    expect(submenu).to_be_visible()


def test_hover_on_always_hidden_times_out(page: Page, fixture_url):
    """Hover on a display:none element → Playwright timeout (actionability wait)."""
    page.goto(fixture_url("hover.html"))
    with pytest.raises(Error):
        page.locator("#always-hidden").hover(timeout=1500)
