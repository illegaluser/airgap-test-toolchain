"""Playwright codegen 형식 flat 스크립트 — 14대 DSL 액션 전부 포함.

`recorded-9actions.py` 의 14대 확장본. converter.py 가 신규 5종
(`upload`/`drag`/`scroll`/`mock_status`/`mock_data`) 까지 매핑하는지
회귀 검증하는 fixture.

페이지 fixture 경로는 개발 머신 기준 절대경로 `file://` 로 하드코딩 — 데모 목적.
converter 가 `def` / `from` / `with` / `browser` / `context` 로 시작하는 라인을
skip 하므로 wrapper 는 자동 무시되고, `page.X` / `expect(...)` 라인만 DSL 로 매핑된다.
"""
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # ── 1. navigate ─────────────────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/full_dsl.html")

    # ── 2. wait ─────────────────────────────────────────────────────────
    page.wait_for_timeout(500)

    # ── 3. select ──────────────────────────────────────────────────────
    page.locator("#lang").select_option(label="한국어")

    # ── 4. check ───────────────────────────────────────────────────────
    page.locator("#agree").check()

    # ── 5. fill ────────────────────────────────────────────────────────
    page.locator("#search-input").fill("query")

    # ── 6. press ───────────────────────────────────────────────────────
    page.locator("#search-input").press("Enter")

    # ── 7. click ───────────────────────────────────────────────────────
    page.locator("#primary-btn").click()

    # ── 8. hover ───────────────────────────────────────────────────────
    page.locator("#card").hover()

    # ── 9. upload ──────────────────────────────────────────────────────
    page.locator("#file-input").set_input_files("upload_sample.txt")

    # ── 10. drag ───────────────────────────────────────────────────────
    page.locator("#card").drag_to(page.locator("#dst-zone"))

    # ── 11. scroll ─────────────────────────────────────────────────────
    page.locator("#footer").scroll_into_view_if_needed()

    # ── 12. mock_status ────────────────────────────────────────────────
    page.route("**/api/profile", lambda r: r.fulfill(status=500))

    # ── 13. mock_data ──────────────────────────────────────────────────
    page.route("**/api/items", lambda r: r.fulfill(status=200, content_type="application/json", body="{\"items\":[]}"))

    # ── 14. verify ─────────────────────────────────────────────────────
    expect(page.locator("#footer")).to_have_text("FOOTER")

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
