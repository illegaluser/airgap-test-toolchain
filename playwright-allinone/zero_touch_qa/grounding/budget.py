"""토큰 예산 가드 (Phase 1 T1.4).

설계: PLAN_GROUNDING_RECORDING_AGENT.md §"T1.4 — 토큰 예산 가드"

기본 한도 1500 토큰 (12288 컨텍스트 + RAG 동시 가정 안전 마진).
한도 초과 시 단계별 축소:
  1. 컨텍스트 role (heading/landmark) 제거
  2. 가시성 외 요소 제거
  3. 우선순위 낮은 인터랙티브 (option/menuitem) 제거
  4. role 별 상위 N개로 추가 압축
  5. 그래도 초과 시 truncate + flag
"""

from __future__ import annotations

import logging

from .schema import Inventory, InventoryElement, CONTEXT_ROLES
from .serializer import serialize_block

log = logging.getLogger(__name__)

DEFAULT_TOKEN_BUDGET = 1500


def estimate_tokens(text: str) -> int:
    """tiktoken cl100k_base 우선, 미설치 시 char/4 근사."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        # graceful fallback — tiktoken 미설치/모델 미캐시 경우
        return max(1, len(text) // 4)


def fit_to_budget(
    inv: Inventory,
    *,
    budget: int = DEFAULT_TOKEN_BUDGET,
) -> Inventory:
    """직렬화 후 토큰이 budget 을 초과하면 단계별로 축소.

    원본 inv 를 그대로 변경(in-place). 잘리면 inv.truncated=True.
    """
    rendered = serialize_block(inv)
    used = estimate_tokens(rendered)
    if used <= budget:
        return inv

    log.info(
        "[grounding/budget] 인벤토리 %d 토큰 > 한도 %d — 단계별 축소 시작",
        used, budget,
    )

    # 1단계: 컨텍스트 role (heading/landmark) 제거
    inv.elements = [e for e in inv.elements if e.role not in CONTEXT_ROLES]
    if _within(inv, budget):
        inv.truncated = True
        return inv

    # 2단계: 가시성 외 / disabled 요소 제거
    inv.elements = [e for e in inv.elements if e.visible and e.enabled]
    if _within(inv, budget):
        inv.truncated = True
        return inv

    # 3단계: 우선순위 낮은 인터랙티브 (option / menuitem) 제거
    LOW_PRIO = {"option", "menuitem"}
    inv.elements = [e for e in inv.elements if e.role not in LOW_PRIO]
    if _within(inv, budget):
        inv.truncated = True
        return inv

    # 4단계: role 별 상위 5개 만 유지
    seen: dict[str, int] = {}
    keep: list[InventoryElement] = []
    for el in inv.elements:
        c = seen.get(el.role, 0)
        if c < 5:
            keep.append(el)
            seen[el.role] = c + 1
    inv.elements = keep
    if _within(inv, budget):
        inv.truncated = True
        return inv

    # 5단계: 마지막 — 첫 N개만 유지하고 truncate flag
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
        "[grounding/budget] 한도 %d 토큰 강제 truncate — %d 요소만 유지",
        budget, len(inv.elements),
    )
    return inv


def _within(inv: Inventory, budget: int) -> bool:
    return estimate_tokens(serialize_block(inv)) <= budget
