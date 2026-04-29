"""fill action — 4 cases."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_fill_text_input(page: Page, fixture_url):
    """Fill a regular text input."""
    page.goto(fixture_url("fill.html"))
    page.locator("#input-name").fill("홍길동")
    expect(page.locator("#input-name")).to_have_value("홍길동")


def test_fill_textarea_multiline(page: Page, fixture_url):
    """Enter multiple lines into a textarea."""
    page.goto(fixture_url("fill.html"))
    multiline = "첫 줄\n둘째 줄\n셋째 줄"
    page.locator("#input-bio").fill(multiline)
    expect(page.locator("#input-bio")).to_have_value(multiline)


def test_fill_readonly_raises(page: Page, fixture_url):
    """fill on a readonly input → Playwright rejects with timeout/error."""
    page.goto(fixture_url("fill.html"))
    with pytest.raises(Error):
        # short timeout so the failure is fast
        page.locator("#input-readonly").fill("intrusion", timeout=1500)


def test_fill_special_chars(page: Page, fixture_url):
    """Hangul / emoji / special characters all round-trip intact."""
    page.goto(fixture_url("fill.html"))
    value = "한글 + English + 123 + 🚀 + \"quote\" + <tag>"
    page.locator("#input-special").fill(value)
    expect(page.locator("#input-special")).to_have_value(value)
