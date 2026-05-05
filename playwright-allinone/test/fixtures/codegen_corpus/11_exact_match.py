"""Pattern 11 — get_by_role(..., name=..., exact=True) 의 exact 보존.

substring 매칭으로 ``name="API"`` 가 ``"오픈API"`` 같은 superset 텍스트에 잘못
잡히는 케이스 (5e1e5a6f1) 를 방지하기 위해 codegen 의 exact=True 가 14-DSL
target 끝에 ``, exact=true`` 로 보존돼야 한다.
"""

from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/search")
    page.get_by_role("button", name="API", exact=True).click()
    page.get_by_role("link", name="제출").click()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
