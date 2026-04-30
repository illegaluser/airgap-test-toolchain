"""Sample Playwright script for the Zero-Touch QA `convert` mode input.

Goals:
- Walk through the external site `https://playwright.dev` in 20 steps.
- Written in a form `zero_touch_qa.converter` can actually extract targets from.
- Sticks to `page.goto(...)`, `page.locator(...).click()`, and
  `expect(...).to_have_text(...)` patterns to keep DSL conversion stable.

Usage:
  PYTHONPATH=playwright-allinone python3 -m zero_touch_qa \
    --mode convert \
    --file playwright-allinone/examples/playwright_dev_convert_input.py
"""

from playwright.sync_api import Playwright, expect, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://playwright.dev/")
    expect(page.locator('a[href="/docs/intro"]')).to_have_text("Docs")

    page.locator('a[href="/docs/intro"]').click()
    expect(page.locator("h1")).to_have_text("Installation")

    page.locator('a[href="/docs/writing-tests"]').first.click()
    expect(page.locator("h1")).to_have_text("Writing tests")

    page.locator('a[href="/docs/codegen-intro"]').first.click()
    expect(page.locator("h1")).to_have_text("Generating tests")

    page.locator('a[href="/docs/running-tests"]').first.click()
    expect(page.locator("h1")).to_have_text("Running and debugging tests")

    page.goto("https://playwright.dev/docs/trace-viewer")
    expect(page.locator("h1")).to_have_text("Trace viewer")

    page.goto("https://playwright.dev/docs/browsers")
    expect(page.locator("h1")).to_have_text("Browsers")

    page.goto("https://playwright.dev/docs/api/class-playwright")
    page.locator('a[href="/docs/api/class-page"]').first.click()

    expect(page.locator("h1")).to_have_text("Page")
    page.goto("https://playwright.dev/community/welcome")

    expect(page.locator("h1")).to_have_text("Welcome")
    expect(page.locator('a[href*="discord"] svg, a[href*="discord"]')).to_be_visible()

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
