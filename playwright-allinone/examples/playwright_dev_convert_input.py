"""Zero-Touch QA `convert` 모드 입력용 Playwright 스크립트 샘플.

목표:
- 외부 사이트 `https://playwright.dev` 를 20 step 으로 순회
- `zero_touch_qa.converter` 가 실제로 target 을 추출할 수 있는 형태로 작성
- `page.goto(...)`, `page.locator(...).click()`, `expect(...).to_have_text(...)`
  패턴 위주로 맞춰 DSL 변환 안정성 확보

사용 예:
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
