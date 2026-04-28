"""DOM Grounding 모듈 (Phase 1).

Planner LLM 호출 직전 target_url 의 실제 DOM 인벤토리를 추출해 srs_text 앞에
prepend 한다. 셀렉터 추측을 "인벤토리에서 선택" 으로 전환한다.

진입점:
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
