"""wait 액션 — 3 케이스."""

import time

import pytest
from playwright.sync_api import Page, expect, TimeoutError as PWTimeoutError


def test_wait_for_timeout_ms(page: Page, fixture_url):
    """page.wait_for_timeout(ms) → 지정 시간만큼 실제 경과."""
    page.goto(fixture_url("wait.html"))
    start = time.monotonic()
    page.wait_for_timeout(600)
    elapsed_ms = (time.monotonic() - start) * 1000
    # 600ms 대기했으니 최소 550ms 는 지나있어야 정상 (시스템 jitter 허용).
    assert elapsed_ms >= 550, f"wait_for_timeout 이 너무 빨리 끝남: {elapsed_ms:.0f}ms"


def test_wait_for_selector_eventually_appears(page: Page, fixture_url):
    """500ms 후 동적으로 생성되는 element 를 기다림."""
    page.goto(fixture_url("wait.html"))
    # wait.html 의 setTimeout 이 500ms 후 #delayed 추가
    page.wait_for_selector("#delayed", timeout=3000)
    expect(page.locator("#delayed")).to_have_text("I appeared after 500ms.")


def test_wait_timeout_when_never_appears(page: Page, fixture_url):
    """존재하지 않는 selector 를 짧은 timeout 으로 기다리면 TimeoutError."""
    page.goto(fixture_url("wait.html"))
    with pytest.raises(PWTimeoutError):
        page.wait_for_selector("#never-exists", timeout=800)
