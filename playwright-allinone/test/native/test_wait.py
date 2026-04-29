"""wait action — 3 cases."""

import time

import pytest
from playwright.sync_api import Page, expect, TimeoutError as PWTimeoutError


def test_wait_for_timeout_ms(page: Page, fixture_url):
    """page.wait_for_timeout(ms) → wall-clock waits the specified time."""
    page.goto(fixture_url("wait.html"))
    start = time.monotonic()
    page.wait_for_timeout(600)
    elapsed_ms = (time.monotonic() - start) * 1000
    # waited 600ms, so at least 550ms must have passed (system jitter allowance).
    assert elapsed_ms >= 550, f"wait_for_timeout returned too early: {elapsed_ms:.0f}ms"


def test_wait_for_selector_eventually_appears(page: Page, fixture_url):
    """Wait for an element that is dynamically created 500ms later."""
    page.goto(fixture_url("wait.html"))
    # wait.html's setTimeout adds #delayed after 500ms
    page.wait_for_selector("#delayed", timeout=3000)
    expect(page.locator("#delayed")).to_have_text("I appeared after 500ms.")


def test_wait_timeout_when_never_appears(page: Page, fixture_url):
    """Waiting for a non-existent selector with a short timeout raises TimeoutError."""
    page.goto(fixture_url("wait.html"))
    with pytest.raises(PWTimeoutError):
        page.wait_for_selector("#never-exists", timeout=800)
