"""DOM 인벤토리 추출기 (Phase 1 T1.2).

Playwright 1.57+ 에서 `page.accessibility.snapshot()` 가 제거되어, CDP 의
`Accessibility.getFullAXTree` 를 직접 호출한다. 결과를 InventoryElement
리스트로 정규화한다.

진입점:
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
    """target_url 의 DOM 인벤토리를 추출한다.

    실패 시 Inventory.error 에 사유 기록. 호출자는 graceful degradation.
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
    except Exception as e:  # noqa: BLE001 — graceful degradation 정책
        inv.error = f"{type(e).__name__}: {e}"
        log.warning("[grounding] %s extraction failed: %s", target_url, e)

    if inv.error is None:
        log.info(
            "[grounding] %s 추출 완료 — %d 요소 (인터랙티브 %d)",
            target_url, len(inv.elements), inv.interactive_count(),
        )

    return inv


def _extract_via_cdp(page: Page) -> list[InventoryElement]:
    """CDP Accessibility.getFullAXTree 결과를 InventoryElement 로 정규화."""
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
        # heading 같은 컨텍스트 role 은 name 없어도 text 로 가치 있음.
        # 인터랙티브 role 은 name 이 비면 LLM 이 식별 불가 → 스킵.
        if role in INTERACTIVE_ROLES and not name:
            continue

        ignored = bool(n.get("ignored"))
        if ignored:
            continue

        properties = {p.get("name"): _value_of(p.get("value"))
                      for p in n.get("properties", []) if p.get("name")}

        visible = not properties.get("hidden", False)
        # disabled / focusable 등을 enabled 로 환산
        enabled = not properties.get("disabled", False)

        # selector_hint — getByRole(role, {name}) 우선. 이름 없을 시 getByText.
        if name:
            selector_hint = f"getByRole('{role}', {{ name: {name!r} }})"
        else:
            selector_hint = f"getByRole('{role}')"

        extras: dict = {}
        # heading level 은 토큰 비용 작고 의미 큼 — 노출.
        if role == "heading" and "level" in properties:
            try:
                extras["level"] = int(properties["level"])
            except (TypeError, ValueError):
                pass

        elem = InventoryElement(
            role=role,
            name=name,
            text=name,  # CDP 는 별도 text 필드를 안 줌. name 으로 일원화.
            selector_hint=selector_hint,
            visible=visible,
            enabled=enabled,
            position=None,
            extras=extras,
        )
        elements.append(elem)

    return elements


def _value_of(field) -> str | bool | None:
    """CDP property 형식 `{type: 'string', value: '...'}` 에서 value 추출."""
    if field is None:
        return None
    if isinstance(field, dict):
        return field.get("value")
    return field
