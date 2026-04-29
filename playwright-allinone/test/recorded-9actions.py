"""Flat script in Playwright codegen format — covers all 9 DSL actions.

Upload artifact for the Jenkins pipeline `RUN_MODE=convert`. converter.py
turns each `page.X` / `expect(...)` line into a DSL step which the
executor then runs.

The fixture page paths are hard-coded as **absolute `file://` URLs** on
the dev machine. To reuse on another machine, swap them for the absolute
path to your repo — demo only.

The converter skips lines starting with `def`, `from`, `with`, `browser`,
`context`, so the wrapper below (def run / with sync_playwright ...) is
auto-ignored — only `page.goto(...)` / `.click()` / `expect(...)` lines
map into the DSL.
"""
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # ── 1. navigate + verify (text match) ───────────────────────────────
    # The converter's `to_be_visible` target extraction has a non-greedy
    # regex that yields whitespace, so we verify with `to_have_text`
    # instead (which the converter handles correctly).
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/navigate.html")
    expect(page.locator("#arrived-marker")).to_have_text("navigation arrived")

    # ── 2. wait (fixed-ms wait) ─────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/wait.html")
    page.wait_for_timeout(700)

    # ── 3. click + verify (text match) ──────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/click.html")
    page.locator("#btn-plain").click()
    expect(page.locator("#status-plain")).to_have_text("clicked")

    # ── 4. fill ─────────────────────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/fill.html")
    page.locator("#input-name").fill("홍길동")

    # ── 5. press (form submit via Enter) ────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/press.html")
    page.locator("#search-box").fill("query")
    page.locator("#search-box").press("Enter")

    # ── 6. select (option by label) ─────────────────────────────────────
    # Executor always calls `select_option(label=value)`, so use the label text.
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/select.html")
    page.locator("#country").select_option(label="Korea")

    # ── 7. check (checkbox) ─────────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/check.html")
    page.locator("#cb-subscribe").check()

    # ── 8. hover + verify (tooltip text) ────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/hover.html")
    page.locator("#tooltip-trigger").hover()
    expect(page.locator("#tooltip-content")).to_have_text("Tooltip text")

    # ── 9. verify (final — text match) ──────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/verify.html")
    expect(page.locator("#visible-item")).to_have_text("This element is visible.")

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
