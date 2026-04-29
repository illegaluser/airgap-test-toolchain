"""Inventory pruning heuristic (Phase 1 T1.3).

Design: docs/grounding-schema.md §"Serialization rules"
"""

from __future__ import annotations

from .schema import Inventory, InventoryElement, INTERACTIVE_ROLES, CONTEXT_ROLES


# Per-role top-N caps (prevent blow-up on large pages).
DEFAULT_PER_ROLE_LIMIT = {
    "button": 15,
    "link": 20,
    "textbox": 15,
    "combobox": 10,
    "checkbox": 10,
    "radio": 10,
    "tab": 10,
    "option": 12,    # for option-heavy pages like select.html
    "menuitem": 10,
    "searchbox": 5,
    "switch": 10,
    "slider": 5,
    "spinbutton": 5,
}


def prune(
    inv: Inventory,
    *,
    keep_context: bool = True,
    drop_invisible: bool = True,
    drop_disabled: bool = False,
    per_role_limit: dict[str, int] | None = None,
) -> Inventory:
    """Apply first-pass pruning rules to the inventory.

    Token-budget guard (T1.4) adds further trimming in budget.py.
    """
    limits = {**DEFAULT_PER_ROLE_LIMIT, **(per_role_limit or {})}

    seen_pairs: set[tuple[str, str]] = set()
    role_count: dict[str, int] = {}

    pruned: list[InventoryElement] = []
    for el in inv.elements:
        if drop_invisible and not el.visible:
            continue
        if drop_disabled and not el.enabled:
            continue

        # Drop auxiliary context roles when keep_context=False
        if el.role in CONTEXT_ROLES and not keep_context:
            continue

        # Defense: extractor already filters label-less interactives, but check again.
        if el.role in INTERACTIVE_ROLES and not el.has_label():
            continue

        # De-duplicate by (role, name)
        key = (el.role, el.name or el.text)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        # Per-role top-N cap
        cnt = role_count.get(el.role, 0)
        limit = limits.get(el.role)
        if limit is not None and cnt >= limit:
            continue
        role_count[el.role] = cnt + 1

        pruned.append(el)

    inv.elements = pruned
    return inv
