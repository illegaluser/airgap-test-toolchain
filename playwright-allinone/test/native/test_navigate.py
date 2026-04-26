"""navigate 액션 — 3 케이스."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_navigate_basic_goto(page: Page, fixture_url):
    """페이지로 이동하면 URL 이 반영되고 기대 element 가 보인다."""
    url = fixture_url("navigate.html")
    page.goto(url)
    assert page.url == url
    expect(page.locator("#welcome")).to_have_text("Welcome")
    expect(page.locator("#arrived-marker")).to_be_visible()


def test_navigate_follows_meta_redirect(page: Page, fixture_url):
    """meta refresh 리다이렉트 → 최종 목적지 URL 로 이동한다."""
    start = fixture_url("redirect.html")
    target = fixture_url("navigate.html")
    page.goto(start)
    # meta refresh 가 0초 후 발동 — navigate.html 의 marker 가 뜰 때까지 대기
    expect(page.locator("#arrived-marker")).to_be_visible(timeout=5000)
    assert page.url == target


def test_navigate_invalid_url_raises(page: Page):
    """유효하지 않은 프로토콜/호스트로 이동하면 Playwright 가 Error."""
    with pytest.raises(Error):
        # 연결 불가 포트 — ERR_CONNECTION_REFUSED
        page.goto("http://127.0.0.1:1/does-not-exist", timeout=3000)
