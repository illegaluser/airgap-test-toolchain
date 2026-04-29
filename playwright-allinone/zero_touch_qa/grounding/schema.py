"""DOM Grounding 인벤토리 데이터 클래스.

설계 문서: docs/grounding-schema.md
"""

from dataclasses import dataclass, field
from typing import Optional


# 인터랙티브 role — pruner 가 우선 보존.
INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "combobox", "checkbox",
    "radio", "tab", "menuitem", "option", "searchbox",
    "switch", "slider", "spinbutton",
})

# 보조 컨텍스트 role — pruner 가 토큰 여유 있을 때만 포함.
CONTEXT_ROLES = frozenset({
    "heading", "main", "navigation", "banner", "contentinfo",
    "region", "form", "search", "complementary",
})


@dataclass
class InventoryElement:
    """단일 DOM 요소 표현."""

    role: str
    name: str = ""
    text: str = ""
    selector_hint: str = ""
    visible: bool = True
    enabled: bool = True
    position: Optional[tuple[int, int]] = None  # (x, y) — 직렬화에 노출 안 됨
    # heading 의 level 같은 보조 정보. 직렬화 시 인라인.
    extras: dict = field(default_factory=dict)

    def is_interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES

    def has_label(self) -> bool:
        """name 또는 text 중 하나라도 의미 있는 값이 있나."""
        return bool(self.name.strip() or self.text.strip())


@dataclass
class Inventory:
    """target_url 한 페이지의 인벤토리 전체."""

    target_url: str
    elements: list[InventoryElement] = field(default_factory=list)
    truncated: bool = False
    fetched_at: str = ""
    error: Optional[str] = None

    def is_empty(self) -> bool:
        return not self.elements

    def interactive_count(self) -> int:
        return sum(1 for e in self.elements if e.is_interactive())
