"""Pattern 09 — get_by_title / get_by_alt_text 보존.

회귀: 본 사례 (`개인정보처리방침` 메뉴 click) 의 silent drop 재발 방지.
"""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/menu")
    page.get_by_title("개인정보처리방침").click()
    page.get_by_alt_text("회사 로고").click()
    page.locator("#content").get_by_title("툴팁").click()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
