"""Pattern 07 — frame_locator chain: iframe 안의 요소 액션.

Note: T-A 는 frame chain 을 target prefix (`frame=...>>...`) 로 보존만 한다.
실제 frame_locator traversal 의 executor/resolver 측 구현은 T-C (P0.2) 에서.
"""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/checkout")
    page.frame_locator("#payment-iframe").get_by_role(
        "textbox", name="Card number"
    ).fill("4242424242424242")
    page.frame_locator("#payment-iframe").get_by_role(
        "button", name="Pay"
    ).click()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
