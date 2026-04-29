"""Pattern 02 — popup chain: page → page1 (new tab) → page2 (deeper popup).

이번 세션의 naver 시나리오와 동일 구조. 각 popup 의 액션이 모두 보존돼야 한다.
"""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.naver.com/")
    page.get_by_role("link", name="NAVER 로그인").click()
    page.get_by_role("link", name="NAVER").click()
    with page.expect_popup() as page1_info:
        page.get_by_role("link", name="뉴스홈").click()
    page1 = page1_info.value
    page1.get_by_role("link", name="엔터").click()
    with page1.expect_popup() as page2_info:
        page1.get_by_role("link", name="기사 헤드라인").click()
    page2 = page2_info.value
    page2.close()
    page1.close()
    page.close()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
