"""T-C (P0.2) — iframe / open shadow / closed shadow 통합 테스트.

검증:
- 단일 iframe 결제 fixture: fill + click + verify 통과
- nested iframe 2단: click 통과
- open shadow Web Component: fill + click 통과
- closed shadow: ShadowAccessError 즉시 FAIL (hang 없음)
- frame 안 selector 실패 시 healer 가 같은 frame 안에서만 fallback
- LocatorResolver 의 frame chain + shadow= segment 단위 동작
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from zero_touch_qa.locator_resolver import LocatorResolver, ShadowAccessError


FIXTURES_DIR = Path(__file__).parent / "fixtures"
IFRAME_PAYMENT_URL = (FIXTURES_DIR / "iframe_payment.html").as_uri()
IFRAME_NESTED_URL = (FIXTURES_DIR / "iframe_nested.html").as_uri()
SHADOW_OPEN_URL = (FIXTURES_DIR / "shadow_open.html").as_uri()
SHADOW_CLOSED_URL = (FIXTURES_DIR / "shadow_closed.html").as_uri()


# ─────────────────────────────────────────────────────────────────────
# Resolver 단위 — frame chain + shadow= segment
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _browser_context():
    """단일 sync_playwright 스코프 — 여러 fixture 가 같은 browser/context 공유.

    pytest-playwright 의 async 루프와 sync_playwright 가 충돌하지 않도록
    module 단위 single context. 각 test 가 새 page 를 받는다."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        yield ctx
        ctx.close()
        browser.close()


def _nav_page(ctx, url):
    page = ctx.new_page()
    page.goto(url)
    return page


@pytest.fixture
def page_payment(_browser_context):
    page = _nav_page(_browser_context, IFRAME_PAYMENT_URL)
    yield page
    page.close()


@pytest.fixture
def page_nested(_browser_context):
    page = _nav_page(_browser_context, IFRAME_NESTED_URL)
    yield page
    page.close()


@pytest.fixture
def page_shadow_open(_browser_context):
    page = _nav_page(_browser_context, SHADOW_OPEN_URL)
    yield page
    page.close()


@pytest.fixture
def page_shadow_closed(_browser_context):
    page = _nav_page(_browser_context, SHADOW_CLOSED_URL)
    yield page
    page.close()


def test_iframe_payment_card_input(page_payment):
    """iframe 안의 input 에 카드번호 입력."""
    resolver = LocatorResolver(page_payment)
    loc = resolver.resolve("frame=#payment-iframe >> #card")
    assert loc is not None
    loc.fill("4242424242424242")
    assert loc.input_value() == "4242424242424242"


def test_iframe_payment_pay_button_approves(page_payment):
    """iframe 안 버튼 클릭 후 같은 iframe 의 result 가 approved."""
    resolver = LocatorResolver(page_payment)
    card = resolver.resolve("frame=#payment-iframe >> #card")
    card.fill("4242424242424242")
    btn = resolver.resolve("frame=#payment-iframe >> #pay-btn")
    btn.click()
    result = resolver.resolve("frame=#payment-iframe >> #result")
    assert result.text_content().strip() == "approved"


def test_iframe_payment_postmessage_to_parent(page_payment):
    """iframe 의 postMessage 가 부모의 #parent-status 를 갱신."""
    resolver = LocatorResolver(page_payment)
    card = resolver.resolve("frame=#payment-iframe >> #card")
    card.fill("4242424242424242")
    btn = resolver.resolve("frame=#payment-iframe >> #pay-btn")
    btn.click()
    # postMessage 처리 대기.
    page_payment.wait_for_function(
        "() => document.getElementById('parent-status').textContent === 'parent:received'",
        timeout=2000,
    )
    assert page_payment.locator("#parent-status").text_content() == "parent:received"


def test_nested_iframe_deep_button_click(page_nested):
    """outer → inner 2단 iframe 깊이의 버튼 클릭."""
    resolver = LocatorResolver(page_nested)
    btn = resolver.resolve(
        "frame=#outer-frame >> frame=#inner-frame >> #deep-btn"
    )
    assert btn is not None
    btn.click()
    result = resolver.resolve(
        "frame=#outer-frame >> frame=#inner-frame >> #deep-result"
    )
    assert result.text_content().strip() == "clicked"


def test_open_shadow_fill_and_submit(page_shadow_open):
    """open shadow 안 input 에 값 + submit → external 신호."""
    resolver = LocatorResolver(page_shadow_open)
    # Playwright 가 open shadow 자동 piercing — 단순 chain 으로도 OK.
    name_input = resolver.resolve("shadow=#form-component >> #name-input")
    assert name_input is not None
    name_input.fill("alice")
    submit = resolver.resolve("shadow=#form-component >> #submit-btn")
    submit.click()
    external = resolver.resolve("#external-status")
    assert external.text_content() == "submitted"


def test_open_shadow_status_updated_inside(page_shadow_open):
    """open shadow 내부 status span 의 텍스트도 piercing 으로 읽힌다."""
    resolver = LocatorResolver(page_shadow_open)
    name_input = resolver.resolve("shadow=#form-component >> #name-input")
    name_input.fill("bob")
    submit = resolver.resolve("shadow=#form-component >> #submit-btn")
    submit.click()
    status = resolver.resolve("shadow=#form-component >> #status")
    assert status.text_content() == "hello bob"


def test_closed_shadow_raises_shadow_access_error(page_shadow_closed):
    """closed shadow 는 ShadowAccessError 로 즉시 escalate (hang 없이 < 1s)."""
    resolver = LocatorResolver(page_shadow_closed)
    with pytest.raises(ShadowAccessError, match="closed shadow"):
        resolver.resolve("shadow=#private-form >> #secret-input")


def test_resolver_falls_back_to_none_when_host_missing(page_shadow_open):
    """shadow= 의 host 가 페이지에 없으면 None (closed 와 구분)."""
    resolver = LocatorResolver(page_shadow_open)
    loc = resolver.resolve("shadow=#nonexistent-host >> #anything")
    assert loc is None


# ─────────────────────────────────────────────────────────────────────
# Healer frame-scoped fallback
# ─────────────────────────────────────────────────────────────────────


def test_healer_scopes_to_frame_for_frame_target(page_payment):
    """target 이 frame=...>>... 일 때 healer 가 같은 frame 안에서만 검색."""
    from zero_touch_qa.local_healer import LocalHealer

    healer = LocalHealer(page_payment, threshold=0.5)
    # leaf descriptor 'Pay' — frame 안의 'Pay' 버튼이 정확히 매치되어야 함.
    step = {
        "action": "click",
        "target": "frame=#payment-iframe >> Pay",
    }
    loc = healer.try_heal(step)
    assert loc is not None
    txt = (loc.inner_text() or "").strip()
    assert "Pay" in txt


def test_healer_page_scope_for_non_frame_target(page_shadow_open):
    """frame= 없는 target 은 기존 page-level 검색."""
    from zero_touch_qa.local_healer import LocalHealer

    healer = LocalHealer(page_shadow_open, threshold=0.5)
    # 'Submitt' (오타) → page-level 에서 'Submit' 버튼 매치.
    step = {"action": "click", "target": "Submitt"}
    loc = healer.try_heal(step)
    assert loc is not None
