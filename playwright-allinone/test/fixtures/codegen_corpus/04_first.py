"""Pattern 04 — .first: 첫 번째 매칭 선택 (= nth(0) 의 별칭)."""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://playwright.dev/")
    page.locator('a[href="/docs/intro"]').first.click()
    page.locator("h1").first.is_visible()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
