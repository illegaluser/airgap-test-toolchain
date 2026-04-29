"""check action — 3 cases."""

from playwright.sync_api import Page, expect


def test_check_checkbox(page: Page, fixture_url):
    """Check a regular checkbox."""
    page.goto(fixture_url("check.html"))
    cb = page.locator("#cb-subscribe")
    expect(cb).not_to_be_checked()
    cb.check()
    expect(cb).to_be_checked()


def test_check_radio_exclusive(page: Page, fixture_url):
    """Selecting one radio auto-clears others in the same name group."""
    page.goto(fixture_url("check.html"))
    page.locator("#size-md").check()
    expect(page.locator("#size-md")).to_be_checked()
    expect(page.locator("#size-sm")).not_to_be_checked()
    expect(page.locator("#size-lg")).not_to_be_checked()


def test_check_already_checked_idempotent(page: Page, fixture_url):
    """check() on an already-checked checkbox stays checked, no error."""
    page.goto(fixture_url("check.html"))
    cb = page.locator("#cb-prechecked")
    expect(cb).to_be_checked()  # initial state
    cb.check()  # check again — idempotent
    expect(cb).to_be_checked()  # still checked
