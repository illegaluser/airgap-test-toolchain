"""fill 액션 — 4 케이스."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_fill_text_input(page: Page, fixture_url):
    """일반 text input 채우기."""
    page.goto(fixture_url("fill.html"))
    page.locator("#input-name").fill("홍길동")
    expect(page.locator("#input-name")).to_have_value("홍길동")


def test_fill_textarea_multiline(page: Page, fixture_url):
    """textarea 에 여러 줄 입력."""
    page.goto(fixture_url("fill.html"))
    multiline = "첫 줄\n둘째 줄\n셋째 줄"
    page.locator("#input-bio").fill(multiline)
    expect(page.locator("#input-bio")).to_have_value(multiline)


def test_fill_readonly_raises(page: Page, fixture_url):
    """readonly input 에 fill 시도 → Playwright 가 timeout/error 로 거부."""
    page.goto(fixture_url("fill.html"))
    with pytest.raises(Error):
        # 짧은 timeout 으로 빠르게 실패 확인
        page.locator("#input-readonly").fill("intrusion", timeout=1500)


def test_fill_special_chars(page: Page, fixture_url):
    """한글 / 이모지 / 특수문자 모두 유지된다."""
    page.goto(fixture_url("fill.html"))
    value = "한글 + English + 123 + 🚀 + \"quote\" + <tag>"
    page.locator("#input-special").fill(value)
    expect(page.locator("#input-special")).to_have_value(value)
