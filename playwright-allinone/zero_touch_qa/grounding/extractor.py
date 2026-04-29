"""DOM inventory extractor (Phase 1 T1.2).

Playwright 1.57+ removed `page.accessibility.snapshot()`, so we call CDP's
`Accessibility.getFullAXTree` directly and normalize the result into a list
of InventoryElement.

Entry point:
    fetch_inventory(target_url, token_budget=1500, wait_until="domcontentloaded")
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

from .schema import Inventory, InventoryElement, INTERACTIVE_ROLES, CONTEXT_ROLES

log = logging.getLogger(__name__)


def fetch_inventory(
    target_url: str,
    *,
    wait_until: str = "domcontentloaded",
    page_timeout_ms: int = 15000,
    user_agent: Optional[str] = None,
    viewport: Optional[dict] = None,
) -> Inventory:
    """Extract the DOM inventory of target_url.

    On failure, store the reason in Inventory.error. The caller graceful-degrades.
    """
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    inv = Inventory(target_url=target_url, fetched_at=started)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            ctx_kwargs = {}
            if user_agent:
                ctx_kwargs["user_agent"] = user_agent
            if viewport:
                ctx_kwargs["viewport"] = viewport
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            page.set_default_timeout(page_timeout_ms)
            page.goto(target_url, wait_until=wait_until)

            inv.elements = _extract_via_cdp(page)
            browser.close()

    except PWTimeout as e:
        inv.error = f"timeout: {e}"
        log.warning("[grounding] %s extraction timeout: %s", target_url, e)
    except Exception as e:  # noqa: BLE001 — graceful degradation policy
        inv.error = f"{type(e).__name__}: {e}"
        log.warning("[grounding] %s extraction failed: %s", target_url, e)

    if inv.error is None:
        log.info(
            "[grounding] %s extracted — %d elements (interactive %d)",
            target_url, len(inv.elements), inv.interactive_count(),
        )

    return inv


def _extract_via_cdp(page: Page) -> list[InventoryElement]:
    """Normalize CDP Accessibility.getFullAXTree results into InventoryElement."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Accessibility.enable")
    tree = cdp.send("Accessibility.getFullAXTree") or {}
    nodes = tree.get("nodes", [])

    elements: list[InventoryElement] = []
    for n in nodes:
        role = _value_of(n.get("role"))
        if not role:
            continue
        if role not in INTERACTIVE_ROLES and role not in CONTEXT_ROLES:
            continue

        name = (_value_of(n.get("name")) or "").strip()
        # Context roles like heading are still valuable via text even without a name.
        # Interactive roles without a name are unidentifiable to the LLM → skip.
        if role in INTERACTIVE_ROLES and not name:
            continue

        ignored = bool(n.get("ignored"))
        if ignored:
            continue

        properties = {p.get("name"): _value_of(p.get("value"))
                      for p in n.get("properties", []) if p.get("name")}

        visible = not properties.get("hidden", False)
        # Translate disabled / focusable etc. into enabled
        enabled = not properties.get("disabled", False)

        # selector_hint — prefer getByRole(role, {name}). Fall back to getByText when no name.
        if name:
            selector_hint = f"getByRole('{role}', {{ name: {name!r} }})"
        else:
            selector_hint = f"getByRole('{role}')"

        extras: dict = {}
        # heading level — tiny token cost, big information gain → surface.
        if role == "heading" and "level" in properties:
            try:
                extras["level"] = int(properties["level"])
            except (TypeError, ValueError):
                pass

        elem = InventoryElement(
            role=role,
            name=name,
            text=name,  # CDP does not provide a separate text field — unify with name.
            selector_hint=selector_hint,
            visible=visible,
            enabled=enabled,
            position=None,
            extras=extras,
        )
        elements.append(elem)

    return elements


def _value_of(field) -> str | bool | None:
    """Extract value from the CDP property shape `{type: 'string', value: '...'}`."""
    if field is None:
        return None
    if isinstance(field, dict):
        return field.get("value")
    return field
