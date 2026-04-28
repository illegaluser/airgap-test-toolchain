"""Pattern 05 — .filter(has_text=...): 텍스트로 매칭 좁히기."""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/products")
    page.get_by_role("listitem").filter(has_text="Premium").click()
    page.locator("a").filter(has_text="구매하기").first.click()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
