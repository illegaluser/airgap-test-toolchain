"""T-C (P0.2) — iframe / open shadow / closed shadow integration tests.

Coverage:
- single iframe payment fixture: fill + click + verify all pass
- 2-level nested iframe: click passes
- open shadow Web Component: fill + click pass
- closed shadow: ShadowAccessError fails immediately (no hang)
- when an in-frame selector fails, the healer falls back only inside the same frame
- LocatorResolver's frame-chain + shadow= segments work end-to-end
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
# Resolver-level — frame chain + shadow= segment
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _browser_context():
    """Single sync_playwright scope — multiple fixtures share one browser/context.

    Module-scoped single context to avoid clashing with pytest-playwright's
    async loop. Each test gets a fresh page."""
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
    """Type a card number into an input inside the iframe."""
    resolver = LocatorResolver(page_payment)
    loc = resolver.resolve("frame=#payment-iframe >> #card")
    assert loc is not None
    loc.fill("4242424242424242")
    assert loc.input_value() == "4242424242424242"


def test_iframe_payment_pay_button_approves(page_payment):
    """Click the in-iframe button and the result inside the same iframe is approved."""
    resolver = LocatorResolver(page_payment)
    card = resolver.resolve("frame=#payment-iframe >> #card")
    card.fill("4242424242424242")
    btn = resolver.resolve("frame=#payment-iframe >> #pay-btn")
    btn.click()
    result = resolver.resolve("frame=#payment-iframe >> #result")
    assert result.text_content().strip() == "approved"


def test_iframe_payment_postmessage_to_parent(page_payment):
    """The iframe's postMessage updates the parent's #parent-status."""
    resolver = LocatorResolver(page_payment)
    card = resolver.resolve("frame=#payment-iframe >> #card")
    card.fill("4242424242424242")
    btn = resolver.resolve("frame=#payment-iframe >> #pay-btn")
    btn.click()
    # wait for postMessage handling.
    page_payment.wait_for_function(
        "() => document.getElementById('parent-status').textContent === 'parent:received'",
        timeout=2000,
    )
    assert page_payment.locator("#parent-status").text_content() == "parent:received"


def test_nested_iframe_deep_button_click(page_nested):
    """Click a button at outer → inner 2-level iframe depth."""
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
    """Fill an input inside an open shadow + submit → external signal fires."""
    resolver = LocatorResolver(page_shadow_open)
    # Playwright auto-pierces open shadow — a simple chain is fine.
    name_input = resolver.resolve("shadow=#form-component >> #name-input")
    assert name_input is not None
    name_input.fill("alice")
    submit = resolver.resolve("shadow=#form-component >> #submit-btn")
    submit.click()
    external = resolver.resolve("#external-status")
    assert external.text_content() == "submitted"


def test_open_shadow_status_updated_inside(page_shadow_open):
    """Text inside the open shadow's status span reads through with piercing."""
    resolver = LocatorResolver(page_shadow_open)
    name_input = resolver.resolve("shadow=#form-component >> #name-input")
    name_input.fill("bob")
    submit = resolver.resolve("shadow=#form-component >> #submit-btn")
    submit.click()
    status = resolver.resolve("shadow=#form-component >> #status")
    assert status.text_content() == "hello bob"


def test_closed_shadow_raises_shadow_access_error(page_shadow_closed):
    """Closed shadow escalates immediately as ShadowAccessError (no hang, < 1s)."""
    resolver = LocatorResolver(page_shadow_closed)
    with pytest.raises(ShadowAccessError, match="closed shadow"):
        resolver.resolve("shadow=#private-form >> #secret-input")


def test_resolver_falls_back_to_none_when_host_missing(page_shadow_open):
    """If the shadow= host is absent from the page → None (distinct from closed)."""
    resolver = LocatorResolver(page_shadow_open)
    loc = resolver.resolve("shadow=#nonexistent-host >> #anything")
    assert loc is None


# ─────────────────────────────────────────────────────────────────────
# Healer frame-scoped fallback
# ─────────────────────────────────────────────────────────────────────


def test_healer_scopes_to_frame_for_frame_target(page_payment):
    """When target is frame=...>>..., healer searches only inside that frame."""
    from zero_touch_qa.local_healer import LocalHealer

    healer = LocalHealer(page_payment, threshold=0.5)
    # leaf descriptor 'Pay' — must match the in-frame 'Pay' button precisely.
    step = {
        "action": "click",
        "target": "frame=#payment-iframe >> Pay",
    }
    loc = healer.try_heal(step)
    assert loc is not None
    txt = (loc.inner_text() or "").strip()
    assert "Pay" in txt


def test_healer_page_scope_for_non_frame_target(page_shadow_open):
    """Targets without frame= use the regular page-level search."""
    from zero_touch_qa.local_healer import LocalHealer

    healer = LocalHealer(page_shadow_open, threshold=0.5)
    # 'Submitt' (typo) → page-level matches the 'Submit' button.
    step = {"action": "click", "target": "Submitt"}
    loc = healer.try_heal(step)
    assert loc is not None


def test_healer_handles_real_ast_emit_form_role_name(page_payment):
    """Review #2 regression — the form converter_ast actually emits
    (`frame=... >> role=button, name=Pay`) must work in the healer.
    Previously _clean_target produced 'button, name=Pay' which failed to match."""
    from zero_touch_qa.local_healer import LocalHealer

    healer = LocalHealer(page_payment, threshold=0.5)
    step = {
        "action": "click",
        "target": "frame=#payment-iframe >> role=button, name=Pay",
    }
    loc = healer.try_heal(step)
    assert loc is not None, (
        "healer fallback failed on the real chain shape converter_ast emits"
    )
    txt = (loc.inner_text() or "").strip()
    assert "Pay" in txt


def test_healer_skips_anonymous_role_only_target(page_payment):
    """role=X (no name) alone has no text to match → _clean_target returns
    empty and try_heal returns None safely (prevents false-positive matches)."""
    from zero_touch_qa.local_healer import LocalHealer

    healer = LocalHealer(page_payment, threshold=0.5)
    step = {"action": "click", "target": "frame=#payment-iframe >> role=button"}
    loc = healer.try_heal(step)
    assert loc is None
