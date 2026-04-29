"""press action — 3 cases."""

from playwright.sync_api import Page, expect


def test_press_enter_submits_form(page: Page, fixture_url):
    """Pressing Enter in the search input submits the form — URL hash becomes #submitted."""
    page.goto(fixture_url("press.html"))
    page.locator("#search-box").fill("query")
    page.locator("#search-box").press("Enter")
    page.wait_for_function("() => location.hash === '#submitted'", timeout=2000)
    assert page.url.endswith("#submitted")


def test_press_escape_closes_modal(page: Page, fixture_url):
    """Click Open Modal, then Escape closes the modal."""
    page.goto(fixture_url("press.html"))
    page.locator("#open-modal").click()
    expect(page.locator("#modal")).to_have_class("open")

    page.keyboard.press("Escape")
    # confirm `.open` class is removed
    expect(page.locator("#modal")).not_to_have_class("open")


def test_press_tab_moves_focus(page: Page, fixture_url):
    """Focus first input → Tab → focus moves to second input."""
    page.goto(fixture_url("press.html"))
    page.locator("#field-first").focus()
    assert page.evaluate("document.activeElement.id") == "field-first"

    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "field-second"
