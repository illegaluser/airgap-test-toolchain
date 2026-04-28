"""press 액션 — 3 케이스."""

from playwright.sync_api import Page, expect


def test_press_enter_submits_form(page: Page, fixture_url):
    """검색 input 에서 Enter 누르면 form 이 submit — URL hash 가 #submitted 로 바뀜."""
    page.goto(fixture_url("press.html"))
    page.locator("#search-box").fill("query")
    page.locator("#search-box").press("Enter")
    page.wait_for_function("() => location.hash === '#submitted'", timeout=2000)
    assert page.url.endswith("#submitted")


def test_press_escape_closes_modal(page: Page, fixture_url):
    """Open Modal 클릭 후 Escape 누르면 모달 닫힘."""
    page.goto(fixture_url("press.html"))
    page.locator("#open-modal").click()
    expect(page.locator("#modal")).to_have_class("open")

    page.keyboard.press("Escape")
    # `.open` 클래스 제거 확인
    expect(page.locator("#modal")).not_to_have_class("open")


def test_press_tab_moves_focus(page: Page, fixture_url):
    """첫 input 포커스 → Tab → 두 번째 input 포커스."""
    page.goto(fixture_url("press.html"))
    page.locator("#field-first").focus()
    assert page.evaluate("document.activeElement.id") == "field-first"

    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "field-second"
