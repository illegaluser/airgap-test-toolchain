"""인벤토리 → LLM 친화 마커 블록 직렬화 (Phase 1 T1.1 / T1.6 가이드).

설계: docs/grounding-schema.md
"""

from __future__ import annotations

from .schema import Inventory, InventoryElement


GUIDE_FOOTER = (
    "위 인벤토리는 target_url 의 실제 DOM 에서 추출된 요소 목록이다.\n"
    "- 셀렉터는 가능하면 위 인벤토리의 selector_hint 를 그대로 사용한다.\n"
    "- 인벤토리에 없는 요소가 필요하면 출력에 `(요소 미발견: <설명>)` 마커를 남긴다.\n"
    "- 우선순위: getByRole(role, {name}) > getByText > getByTestId > CSS"
)


def serialize_block(inv: Inventory) -> str:
    """Inventory 를 마커 블록(`=== DOM INVENTORY ===` … `=== END INVENTORY ===`) 문자열로.

    호출자가 이 문자열을 srs_text 앞에 prepend 한다.
    """
    if inv.error or inv.is_empty():
        # 추출 실패·빈 인벤토리는 prepend 하지 않는다 (T1.5 graceful degradation).
        return ""

    lines = [f"=== DOM INVENTORY (target_url={inv.target_url}) ==="]
    for el in inv.elements:
        lines.append(_format_line(el))
    if inv.truncated:
        lines.append("- (인벤토리가 토큰 예산으로 잘림 — 일부 요소만 표시됨)")
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
