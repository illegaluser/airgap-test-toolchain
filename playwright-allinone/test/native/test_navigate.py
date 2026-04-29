"""navigate action — 3 cases."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_navigate_basic_goto(page: Page, fixture_url):
    """Navigating to a page reflects the URL and the expected element appears."""
    url = fixture_url("navigate.html")
    page.goto(url)
    assert page.url == url
    expect(page.locator("#welcome")).to_have_text("Welcome")
    expect(page.locator("#arrived-marker")).to_be_visible()


def test_navigate_follows_meta_redirect(page: Page, fixture_url):
    """meta refresh redirect → ends up at the final target URL."""
    start = fixture_url("redirect.html")
    target = fixture_url("navigate.html")
    page.goto(start)
    # meta refresh fires after 0s — wait until navigate.html's marker appears
    expect(page.locator("#arrived-marker")).to_be_visible(timeout=5000)
    assert page.url == target


def test_navigate_invalid_url_raises(page: Page):
    """Navigating to an invalid protocol/host raises a Playwright Error."""
    with pytest.raises(Error):
        # unreachable port — ERR_CONNECTION_REFUSED
        page.goto("http://127.0.0.1:1/does-not-exist", timeout=3000)
