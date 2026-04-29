"""Unit tests for LocatorResolver chain parsing (P0.1 #2).

Verifies that nested locator forms emitted by the AST transformer (T-A) —
`#sidebar >> role=button, name=Settings`,
`frame=#x >> role=textbox, name=Card`, `.card >> button.confirm` —
unwind into proper accumulated chains in the resolver. Previously, `>>`
flowed straight into the page-level locator and produced false-positive
PASSes / match failures.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from zero_touch_qa.locator_resolver import LocatorResolver


FIXTURES_DIR = Path(__file__).parent / "fixtures"
NESTED_URL = (FIXTURES_DIR / "nested_locator.html").as_uri()


@pytest.fixture(scope="module")
def page_for_chain():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(NESTED_URL)
        yield page
        ctx.close()
        browser.close()


def _click_and_read(page, target: str) -> str:
    resolver = LocatorResolver(page)
    loc = resolver.resolve(target)
    assert loc is not None, f"resolver failed to match: {target!r}"
    loc.click()
    return page.locator("#last-clicked").text_content().strip()


def test_chain_css_then_role(page_for_chain):
    """`#sidebar >> role=button, name=Settings` — only the Settings inside sidebar.
    The same text exists inside main, so if the chain doesn't unwind we click the wrong element."""
    last = _click_and_read(page_for_chain, "#sidebar >> role=button, name=Settings")
    assert last == "sidebar:Settings"


def test_chain_css_then_css(page_for_chain):
    """`.card >> button.confirm` — confirm button on the first .card (.first)."""
    last = _click_and_read(page_for_chain, ".card >> button.confirm")
    assert last == "card:Confirm"


def test_chain_css_then_testid(page_for_chain):
    last = _click_and_read(page_for_chain, "#sidebar >> testid=sidebar-help")
    assert last == "sidebar:Help"


def test_chain_with_modifier_nth(page_for_chain):
    """`.card >> button.confirm, nth=1` — confirm button on the second .card."""
    last = _click_and_read(page_for_chain, ".card >> button.confirm, nth=1")
    assert last == "card:Confirm-2"


def test_chain_with_modifier_has_text(page_for_chain):
    last = _click_and_read(
        page_for_chain, ".card >> button, has_text=Cancel",
    )
    assert last == "card:Cancel"


def test_chain_frame_locator(page_for_chain):
    """`frame=#payment-iframe >> role=button, name=Pay` — button inside the iframe."""
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve("frame=#payment-iframe >> role=button, name=Pay")
    assert loc is not None
    # Clicks inside the iframe don't reach the parent page's #last-clicked,
    # so confirming the locator matches is enough.
    assert loc.count() == 1


def test_chain_frame_locator_with_role_textbox(page_for_chain):
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve(
        "frame=#payment-iframe >> role=textbox, name=Card number",
    )
    assert loc is not None
    assert loc.count() == 1


def test_chain_returns_none_when_segment_missing(page_for_chain):
    """If the first segment doesn't match, the whole chain returns None."""
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve("#nonexistent-container >> role=button, name=Settings")
    assert loc is None


def test_single_segment_path_unchanged(page_for_chain):
    """A selector without `>>` keeps the original single-segment path (regression guard)."""
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve("testid=content-greeting")
    assert loc is not None
    assert loc.text_content().strip() == "welcome"


def test_chain_text_segment(page_for_chain):
    last = _click_and_read(page_for_chain, "#sidebar >> text=Profile")
    assert last == "sidebar:Profile"
