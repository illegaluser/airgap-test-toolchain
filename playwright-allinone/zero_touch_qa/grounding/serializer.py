"""Inventory → LLM-friendly marker block serializer (Phase 1 T1.1 / T1.6 guide).

Design: docs/grounding-schema.md
"""

from __future__ import annotations

from .schema import Inventory, InventoryElement


GUIDE_FOOTER = (
    "The inventory above is the list of elements extracted from the actual DOM at target_url.\n"
    "- For selectors, prefer the selector_hint from the inventory verbatim when possible.\n"
    "- If you need an element not in the inventory, leave a `(element not found: <description>)` marker in the output.\n"
    "- Priority: getByRole(role, {name}) > getByText > getByTestId > CSS"
)


def serialize_block(inv: Inventory) -> str:
    """Render the Inventory as a marker block (`=== DOM INVENTORY ===` … `=== END INVENTORY ===`).

    The caller prepends this string to srs_text.
    """
    if inv.error or inv.is_empty():
        # Skip prepend for extraction failure / empty inventory (T1.5 graceful degradation).
        return ""

    lines = [f"=== DOM INVENTORY (target_url={inv.target_url}) ==="]
    for el in inv.elements:
        lines.append(_format_line(el))
    if inv.truncated:
        lines.append("- (inventory was truncated by the token budget — only some elements are shown)")
    lines.append("=== END INVENTORY ===")
    lines.append("")
    lines.append(GUIDE_FOOTER)
    lines.append("")
    return "\n".join(lines)


def _format_line(el: InventoryElement) -> str:
    parts = [f"role={el.role}"]
    if el.name:
        parts.append(f"name={el.name!r}")
    elif el.text:
        text = el.text if len(el.text) <= 100 else el.text[:97] + "…"
        parts.append(f"text={text!r}")
    if el.extras.get("level") is not None:
        parts.append(f"level={el.extras['level']}")
    if not el.visible:
        parts.append("visible=false")
    if not el.enabled:
        parts.append("enabled=false")
    if el.selector_hint:
        parts.append(f"selector_hint={el.selector_hint}")
    return "- {" + ", ".join(parts) + "}"
