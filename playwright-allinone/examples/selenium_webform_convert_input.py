"""External-site example for the Zero-Touch QA `convert` mode input.

Target:
- The public Selenium test page `https://www.selenium.dev/selenium/web/web-form.html`.

Goals:
- Provide a sample that exercises all 9 DSL actions against an external site.
- Keep the Playwright code deterministically convertible in convert mode.
"""

from playwright.sync_api import Playwright, expect, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://www.selenium.dev/selenium/web/web-form.html")
    expect(page.locator("h1")).to_have_text("Web form")

    page.locator("#my-text-id").fill("Zero Touch QA")
    page.locator("#my-text-id").press("Tab")

    page.locator("textarea[name='my-textarea']").fill("External site regression scenario")
    page.locator("select[name='my-select']").select_option(label="Two")

    page.locator("#my-check-2").check()
    page.locator("button").hover()
    page.wait_for_timeout(400)

    page.locator("button").click()
    expect(page.locator("#message")).to_have_text("Received!")

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
