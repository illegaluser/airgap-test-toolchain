"""select action — 3 cases."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_select_by_value(page: Page, fixture_url):
    """Select option by its value attribute."""
    page.goto(fixture_url("select.html"))
    page.locator("#country").select_option(value="kr")
    expect(page.locator("#country")).to_have_value("kr")


def test_select_by_label(page: Page, fixture_url):
    """Select option by its visible label."""
    page.goto(fixture_url("select.html"))
    page.locator("#language").select_option(label="한국어")
    expect(page.locator("#language")).to_have_value("ko")


def test_select_invalid_value_raises(page: Page, fixture_url):
    """Selecting a non-existent value → Playwright raises."""
    page.goto(fixture_url("select.html"))
    with pytest.raises(Error):
        page.locator("#city").select_option(value="atlantis", timeout=1500)
