"""Pattern 03 — .nth(N): 같은 텍스트 매칭 중 N번째 선택."""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/list")
    page.get_by_role("link", name="Read more").nth(0).click()
    page.go_back()
    page.get_by_role("link", name="Read more").nth(2).click()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
