"""Pattern 06 — nested locator: page.locator(parent).locator(child) chain."""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/dashboard")
    page.locator("#sidebar").get_by_role("button", name="Settings").click()
    page.locator(".card").locator("button.confirm").click()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
