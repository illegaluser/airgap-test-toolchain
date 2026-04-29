"""Token-budget guard (Phase 1 T1.4).

Design: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"T1.4 — Token Budget Guard"

Default limit 1500 tokens (safety margin assuming 12288 context + concurrent RAG).
Step-down when over budget:
  1. Drop context roles (heading/landmark)
  2. Drop non-visible elements
  3. Drop low-priority interactives (option/menuitem)
  4. Further compress to per-role top-N
  5. If still over, truncate + flag
"""

from __future__ import annotations

import logging

from .schema import Inventory, InventoryElement, CONTEXT_ROLES
from .serializer import serialize_block

log = logging.getLogger(__name__)

DEFAULT_TOKEN_BUDGET = 1500


def estimate_tokens(text: str) -> int:
    """Prefer tiktoken cl100k_base; fall back to char/4 if not installed."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        # Graceful fallback — tiktoken not installed / model not cached
        return max(1, len(text) // 4)


def fit_to_budget(
    inv: Inventory,
    *,
    budget: int = DEFAULT_TOKEN_BUDGET,
) -> Inventory:
    """If serialized tokens exceed budget, step down progressively.

    Mutates inv in place. Sets inv.truncated=True if anything is dropped.
    """
    rendered = serialize_block(inv)
    used = estimate_tokens(rendered)
    if used <= budget:
        return inv

    log.info(
        "[grounding/budget] inventory %d tokens > limit %d — starting step-down",
        used, budget,
    )

    # Once on the step-down path the final render will be truncated=True (~30 extra tokens).
    # Set the flag immediately so _within accounts for that overhead up front.
    inv.truncated = True

    # Step 1: drop context roles (heading/landmark)
    inv.elements = [e for e in inv.elements if e.role not in CONTEXT_ROLES]
    if _within(inv, budget):
        return inv

    # Step 2: drop non-visible / disabled elements
    inv.elements = [e for e in inv.elements if e.visible and e.enabled]
    if _within(inv, budget):
        return inv

    # Step 3: drop low-priority interactives (option / menuitem)
    LOW_PRIO = {"option", "menuitem"}
    inv.elements = [e for e in inv.elements if e.role not in LOW_PRIO]
    if _within(inv, budget):
        return inv

    # Step 4: keep only the top 5 per role
    seen: dict[str, int] = {}
    keep: list[InventoryElement] = []
    for el in inv.elements:
        c = seen.get(el.role, 0)
        if c < 5:
            keep.append(el)
            seen[el.role] = c + 1
    inv.elements = keep
    if _within(inv, budget):
        return inv

    # Step 5: last resort — keep the first N and set truncate flag
    head: list[InventoryElement] = []
    for el in inv.elements:
        head.append(el)
        rendered = serialize_block(Inventory(
            target_url=inv.target_url, elements=head,
            fetched_at=inv.fetched_at, truncated=True,
        ))
        if estimate_tokens(rendered) > budget:
            head.pop()
            break
    inv.elements = head
    inv.truncated = True
    log.warning(
        "[grounding/budget] forced truncate at limit %d tokens — keeping %d elements",
        budget, len(inv.elements),
    )
    return inv


def _within(inv: Inventory, budget: int) -> bool:
    return estimate_tokens(serialize_block(inv)) <= budget
