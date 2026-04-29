"""DOM Grounding module (Phase 1).

Right before the Planner LLM call, extract the actual DOM inventory of the
target_url and prepend it to srs_text. This switches the model from "guess
selectors" to "pick from the inventory".

Entry point:
    from zero_touch_qa.grounding import fetch_inventory, serialize_block

    inv = fetch_inventory(target_url, token_budget=1500)
    if inv.error is None:
        block = serialize_block(inv)
        srs_text = block + "\n\n" + srs_text
"""

from .schema import Inventory, InventoryElement
from .extractor import fetch_inventory
from .serializer import serialize_block

__all__ = [
    "Inventory",
    "InventoryElement",
    "fetch_inventory",
    "serialize_block",
]
