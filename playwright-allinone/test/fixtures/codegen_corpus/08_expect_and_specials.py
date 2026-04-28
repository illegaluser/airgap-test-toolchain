"""Pattern 08 — expect/verify + 특수 액션 (upload, drag, scroll, mock)."""

from playwright.sync_api import Playwright, expect, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/full")

    # upload
    page.get_by_label("file").set_input_files("artifacts/sample.txt")

    # drag
    page.locator("#src").drag_to(page.locator("#dst"))

    # scroll
    page.locator("#footer").scroll_into_view_if_needed()

    # mock_status
    page.route("**/api/list", lambda r: r.fulfill(status=500))

    # mock_data
    page.route("**/api/items", lambda r: r.fulfill(body='{"items":[]}'))

    # expect to_have_text
    expect(page.locator("h1")).to_have_text("Welcome")

    # expect to_be_visible
    expect(page.get_by_role("button", name="Submit")).to_be_visible()

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
