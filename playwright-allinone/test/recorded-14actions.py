"""Flat script in Playwright codegen format — covers all 14 DSL actions.

The 14-action expansion of `recorded-9actions.py`. Regression fixture that
checks converter.py also maps the 5 new actions
(`upload`/`drag`/`scroll`/`mock_status`/`mock_data`).

The fixture page path is hard-coded as an absolute `file://` URL on the dev
machine — demo only. The converter skips lines starting with `def` / `from`
/ `with` / `browser` / `context`, so the wrapper is auto-ignored and only
`page.X` / `expect(...)` lines map into the DSL.
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
