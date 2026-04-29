"""DOM Grounding inventory data classes.

Design doc: docs/grounding-schema.md
"""

from dataclasses import dataclass, field
from typing import Optional


# Interactive roles — pruner preserves these first.
INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "combobox", "checkbox",
    "radio", "tab", "menuitem", "option", "searchbox",
    "switch", "slider", "spinbutton",
})

# Auxiliary context roles — included by pruner only when token budget allows.
CONTEXT_ROLES = frozenset({
    "heading", "main", "navigation", "banner", "contentinfo",
    "region", "form", "search", "complementary",
})


@dataclass
class InventoryElement:
    """A single DOM element."""

    role: str
    name: str = ""
    text: str = ""
    selector_hint: str = ""
    visible: bool = True
    enabled: bool = True
    position: Optional[tuple[int, int]] = None  # (x, y) — not surfaced in serialization
    # Aux info such as heading level. Inlined during serialization.
    extras: dict = field(default_factory=dict)

    def is_interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES

    def has_label(self) -> bool:
        """Whether either name or text holds a meaningful value."""
        return bool(self.name.strip() or self.text.strip())


@dataclass
class Inventory:
    """Full inventory of one target_url page."""

    target_url: str
    elements: list[InventoryElement] = field(default_factory=list)
    truncated: bool = False
    fetched_at: str = ""
    error: Optional[str] = None

    def is_empty(self) -> bool:
        return not self.elements

    def interactive_count(self) -> int:
        return sum(1 for e in self.elements if e.is_interactive())
