"""select 액션 — 3 케이스."""

import pytest
from playwright.sync_api import Page, expect, Error


def test_select_by_value(page: Page, fixture_url):
    """value 속성으로 옵션 선택."""
    page.goto(fixture_url("select.html"))
    page.locator("#country").select_option(value="kr")
    expect(page.locator("#country")).to_have_value("kr")


def test_select_by_label(page: Page, fixture_url):
    """option 의 visible label 로 선택."""
    page.goto(fixture_url("select.html"))
    page.locator("#language").select_option(label="한국어")
    expect(page.locator("#language")).to_have_value("ko")


def test_select_invalid_value_raises(page: Page, fixture_url):
    """존재하지 않는 value 로 select 시도 → Playwright raise."""
    page.goto(fixture_url("select.html"))
    with pytest.raises(Error):
        page.locator("#city").select_option(value="atlantis", timeout=1500)
