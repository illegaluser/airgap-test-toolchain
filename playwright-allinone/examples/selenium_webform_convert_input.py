"""Zero-Touch QA `convert` 모드 입력용 외부 사이트 예제.

대상:
- Selenium 공개 테스트 페이지 `https://www.selenium.dev/selenium/web/web-form.html`

목표:
- 외부 사이트 기반으로 9대 DSL 액션을 모두 포함하는 샘플 제공
- convert 모드에서 deterministic 하게 변환 가능한 Playwright 코드 유지
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
