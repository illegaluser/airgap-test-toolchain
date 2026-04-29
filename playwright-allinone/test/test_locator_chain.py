"""LocatorResolver chain 해석 (P0.1 #2) 단위 테스트.

AST 변환기 (T-A) 가 emit 하는 nested locator 형태 — `#sidebar >> role=button,
name=Settings`, `frame=#x >> role=textbox, name=Card`, `.card >> button.confirm` —
가 resolver 에서 정확히 누적 chain 으로 풀리는지 검증한다. 이전에는 page-level
로케이터에 `>>` 가 그대로 들어가 false-positive PASS / 매칭 실패가 발생했다.
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
    assert loc is not None, f"resolver 가 매치 실패: {target!r}"
    loc.click()
    return page.locator("#last-clicked").text_content().strip()


def test_chain_css_then_role(page_for_chain):
    """`#sidebar >> role=button, name=Settings` — sidebar 안의 Settings 만 매치.
    main 안에도 같은 텍스트가 있으므로 chain 이 안 풀리면 잘못된 element 클릭."""
    last = _click_and_read(page_for_chain, "#sidebar >> role=button, name=Settings")
    assert last == "sidebar:Settings"


def test_chain_css_then_css(page_for_chain):
    """`.card >> button.confirm` — 첫 .card 의 confirm 버튼 (.first)."""
    last = _click_and_read(page_for_chain, ".card >> button.confirm")
    assert last == "card:Confirm"


def test_chain_css_then_testid(page_for_chain):
    last = _click_and_read(page_for_chain, "#sidebar >> testid=sidebar-help")
    assert last == "sidebar:Help"


def test_chain_with_modifier_nth(page_for_chain):
    """`.card >> button.confirm, nth=1` — 두 번째 .card 의 confirm 버튼."""
    last = _click_and_read(page_for_chain, ".card >> button.confirm, nth=1")
    assert last == "card:Confirm-2"


def test_chain_with_modifier_has_text(page_for_chain):
    last = _click_and_read(
        page_for_chain, ".card >> button, has_text=Cancel",
    )
    assert last == "card:Cancel"


def test_chain_frame_locator(page_for_chain):
    """`frame=#payment-iframe >> role=button, name=Pay` — iframe 내부 버튼."""
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve("frame=#payment-iframe >> role=button, name=Pay")
    assert loc is not None
    # iframe 내부 click 은 부모 페이지의 #last-clicked 에 안 잡히지만
    # locator 가 정상 매치되는 것만 확인하면 충분.
    assert loc.count() == 1


def test_chain_frame_locator_with_role_textbox(page_for_chain):
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve(
        "frame=#payment-iframe >> role=textbox, name=Card number",
    )
    assert loc is not None
    assert loc.count() == 1


def test_chain_returns_none_when_segment_missing(page_for_chain):
    """첫 segment 가 매칭 안 되면 chain 전체 None."""
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve("#nonexistent-container >> role=button, name=Settings")
    assert loc is None


def test_single_segment_path_unchanged(page_for_chain):
    """`>>` 없는 selector 는 기존 단일 segment 경로로 동일하게 동작 (회귀 가드)."""
    resolver = LocatorResolver(page_for_chain)
    loc = resolver.resolve("testid=content-greeting")
    assert loc is not None
    assert loc.text_content().strip() == "welcome"


def test_chain_text_segment(page_for_chain):
    last = _click_and_read(page_for_chain, "#sidebar >> text=Profile")
    assert last == "sidebar:Profile"
