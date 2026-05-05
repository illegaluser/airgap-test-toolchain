"""Pattern 10 — popup 발생 후 후속 액션은 모두 원본 page 에서.

5e1e5a6f141a (디지털융합플랫폼 ChatBot SSO) 케이스 재현. window.open 으로
새 탭이 뜨지만 사용자는 원본 page 에서 계속 인터랙션. 자동전환 휴리스틱이
오작동했던 케이스 — popup_to 마킹 + 후속 step page="page" 유지가 핵심.
"""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/portal")
    with page.expect_popup() as page1_info:
        page.get_by_role("link", name="ChatBot").click()
    page1 = page1_info.value
    page.get_by_role("textbox", name="키워드").click()
    page.get_by_role("textbox", name="키워드").fill("API")
    page.get_by_role("button", name="확인").click()
    page1.close()
    page.close()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
