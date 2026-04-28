"""Pattern 01 — simple: navigate + click + fill + press + verify (single page)."""

from playwright.sync_api import Playwright, expect, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/login")
    page.get_by_role("textbox", name="Email").fill("user@example.com")
    page.get_by_role("textbox", name="Password").fill("secret")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Welcome")).to_be_visible()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
