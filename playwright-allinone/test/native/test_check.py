"""check 액션 — 3 케이스."""

from playwright.sync_api import Page, expect


def test_check_checkbox(page: Page, fixture_url):
    """일반 checkbox 체크."""
    page.goto(fixture_url("check.html"))
    cb = page.locator("#cb-subscribe")
    expect(cb).not_to_be_checked()
    cb.check()
    expect(cb).to_be_checked()


def test_check_radio_exclusive(page: Page, fixture_url):
    """radio 하나 선택 → 같은 name 그룹의 다른 radio 는 자동 해제."""
    page.goto(fixture_url("check.html"))
    page.locator("#size-md").check()
    expect(page.locator("#size-md")).to_be_checked()
    expect(page.locator("#size-sm")).not_to_be_checked()
    expect(page.locator("#size-lg")).not_to_be_checked()


def test_check_already_checked_idempotent(page: Page, fixture_url):
    """이미 checked 인 checkbox 에 check() → 그대로 체크 유지, 에러 없음."""
    page.goto(fixture_url("check.html"))
    cb = page.locator("#cb-prechecked")
    expect(cb).to_be_checked()  # 초기 상태
    cb.check()  # 재차 체크 — idempotent
    expect(cb).to_be_checked()  # 여전히 체크
