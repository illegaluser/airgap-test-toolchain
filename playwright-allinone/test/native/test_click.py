"""click 액션 — 4 케이스."""

from playwright.sync_api import Page, expect


def test_click_plain_button(page: Page, fixture_url):
    """id selector 로 버튼 클릭 → 상태 업데이트."""
    page.goto(fixture_url("click.html"))
    page.locator("#btn-plain").click()
    expect(page.locator("#status-plain")).to_have_text("clicked")


def test_click_by_role_and_name(page: Page, fixture_url):
    """role=button, name=로그인 (aria-label) 셀렉터로 버튼 클릭."""
    page.goto(fixture_url("click.html"))
    page.get_by_role("button", name="로그인").click()
    expect(page.locator("#status-login")).to_have_text("clicked")


def test_click_link_navigates(page: Page, fixture_url):
    """링크 클릭 → 다른 페이지로 이동."""
    page.goto(fixture_url("click.html"))
    page.locator("#nav-link").click()
    page.wait_for_url("**/navigate.html", timeout=3000)
    expect(page.locator("#arrived-marker")).to_be_visible()


def test_click_with_fallback_selector(page: Page, fixture_url):
    """첫 selector 가 매치 안 되면 fallback selector 로 성공."""
    page.goto(fixture_url("click.html"))

    # zero_touch_qa 의 fallback_targets 동작을 모사: 첫 selector count==0 이면 다음 시도.
    primary = page.locator("#missing-btn")
    fallback = page.locator("#fallback-btn")

    if primary.count() == 0:
        fallback.click()
    else:
        primary.click()

    expect(page.locator("#status-fallback")).to_have_text("clicked")
