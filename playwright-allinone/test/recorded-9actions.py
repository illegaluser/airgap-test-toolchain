"""Playwright codegen 형식 flat 스크립트 — 9 대 DSL 액션 전부 포함.

Jenkins Pipeline 의 `RUN_MODE=convert` 업로드용. converter.py 가 각 `page.X` /
`expect(...)` 라인을 DSL 스텝으로 변환하고 executor 가 실행한다.

페이지 fixture 경로는 개발 머신 기준 **절대경로 `file://`** 로 하드코딩. 다른
머신에서 재사용하려면 저장소가 있는 절대경로로 치환해야 한다 — 데모 목적.

converter 가 `def`, `from`, `with`, `browser`, `context` 로 시작하는 라인을 skip
하므로 아래 wrapper (def run / with sync_playwright ...) 는 자동 무시되고,
`page.goto(...)` / `.click()` / `expect(...)` 라인만 DSL 로 매핑된다.
"""
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # ── 1. navigate + verify (텍스트 일치) ──────────────────────────────
    # converter 의 `to_be_visible` 타겟 추출은 non-greedy 정규식 이슈로 공백이
    # 되므로 `to_have_text` 로 검증한다 (converter 가 정상 처리).
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/navigate.html")
    expect(page.locator("#arrived-marker")).to_have_text("navigation arrived")

    # ── 2. wait (고정 ms 대기) ─────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/wait.html")
    page.wait_for_timeout(700)

    # ── 3. click + verify (텍스트 매칭) ────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/click.html")
    page.locator("#btn-plain").click()
    expect(page.locator("#status-plain")).to_have_text("clicked")

    # ── 4. fill ─────────────────────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/fill.html")
    page.locator("#input-name").fill("홍길동")

    # ── 5. press (Enter 로 form submit) ────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/press.html")
    page.locator("#search-box").fill("query")
    page.locator("#search-box").press("Enter")

    # ── 6. select (label 로 option 선택) ───────────────────────────────
    # executor 가 항상 `select_option(label=value)` 로 호출하므로 label 텍스트로 지정.
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/select.html")
    page.locator("#country").select_option(label="Korea")

    # ── 7. check (checkbox) ────────────────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/check.html")
    page.locator("#cb-subscribe").check()

    # ── 8. hover + verify (tooltip 텍스트) ─────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/hover.html")
    page.locator("#tooltip-trigger").hover()
    expect(page.locator("#tooltip-content")).to_have_text("Tooltip text")

    # ── 9. verify (최종 — 텍스트 일치) ─────────────────────────────────
    page.goto("file:///Users/luuuuunatic/Developer/airgap-test-toolchain/playwright-allinone/test/fixtures/verify.html")
    expect(page.locator("#visible-item")).to_have_text("This element is visible.")

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
