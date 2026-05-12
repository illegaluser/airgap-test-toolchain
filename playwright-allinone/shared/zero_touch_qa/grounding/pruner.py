"""인벤토리 가지치기 휴리스틱 (Phase 1 T1.3).

설계: docs/grounding-schema.md §"직렬화 규칙"
"""

from __future__ import annotations

from .schema import Inventory, InventoryElement, INTERACTIVE_ROLES, CONTEXT_ROLES


# 인터랙티브 role 별 상위 N개 한도 (대규모 페이지에서 폭발 방지).
DEFAULT_PER_ROLE_LIMIT = {
    "button": 15,
    "link": 20,
    "textbox": 15,
    "combobox": 10,
    "checkbox": 10,
    "radio": 10,
    "tab": 10,
    "option": 12,    # select.html 같이 옵션이 많은 페이지 대비
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
    """인벤토리에 1차 가지치기 룰 적용.

    토큰 예산 가드(T1.4)는 budget.py 에서 추가 트리밍.
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

        # 보조 컨텍스트 role 은 keep_context=False 시 제외
        if el.role in CONTEXT_ROLES and not keep_context:
            continue

        # 라벨 없는 인터랙티브는 미리 extractor 단계에서 걸러졌지만 방어.
        if el.role in INTERACTIVE_ROLES and not el.has_label():
            continue

        # 같은 (role, name) 중복 제거
        key = (el.role, el.name or el.text)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        # role 별 상위 N개 한도
        cnt = role_count.get(el.role, 0)
        limit = limits.get(el.role)
        if limit is not None and cnt >= limit:
            continue
        role_count[el.role] = cnt + 1

        pruned.append(el)

    inv.elements = pruned
    return inv
