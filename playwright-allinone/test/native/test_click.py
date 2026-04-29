"""click action — 4 cases."""

from playwright.sync_api import Page, expect


def test_click_plain_button(page: Page, fixture_url):
    """Click a button by id selector → state updates."""
    page.goto(fixture_url("click.html"))
    page.locator("#btn-plain").click()
    expect(page.locator("#status-plain")).to_have_text("clicked")


def test_click_by_role_and_name(page: Page, fixture_url):
    """Click a button via role=button, name=로그인 (aria-label) selector."""
    page.goto(fixture_url("click.html"))
    page.get_by_role("button", name="로그인").click()
    expect(page.locator("#status-login")).to_have_text("clicked")


def test_click_link_navigates(page: Page, fixture_url):
    """Click a link → navigates to another page."""
    page.goto(fixture_url("click.html"))
    page.locator("#nav-link").click()
    page.wait_for_url("**/navigate.html", timeout=3000)
    expect(page.locator("#arrived-marker")).to_be_visible()


def test_click_with_fallback_selector(page: Page, fixture_url):
    """Falls back to second selector when the first doesn't match."""
    page.goto(fixture_url("click.html"))

    # Mirrors zero_touch_qa's fallback_targets behavior: try next when first selector count==0.
    primary = page.locator("#missing-btn")
    fallback = page.locator("#fallback-btn")

    if primary.count() == 0:
        fallback.click()
    else:
        primary.click()

    expect(page.locator("#status-fallback")).to_have_text("clicked")
