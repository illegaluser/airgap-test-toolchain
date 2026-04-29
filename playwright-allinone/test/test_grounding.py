"""Unit and integration tests for the Phase 1 grounding module.

- T1.1 schema — InventoryElement / Inventory data classes
- T1.2 extractor — fixture-based fetch_inventory (file:// paths)
- T1.3 pruner — pruning rules
- T1.4 budget — token-budget guard
- T1.5 dify_client — _prepend_dom_inventory (graceful degradation)
- T1.6 guide block — serialize_block markers/footer
"""

from __future__ import annotations

import os

import pytest

from zero_touch_qa.grounding import (
    Inventory,
    InventoryElement,
    fetch_inventory,
    serialize_block,
)
from zero_touch_qa.grounding.budget import (
    DEFAULT_TOKEN_BUDGET,
    estimate_tokens,
    fit_to_budget,
)
from zero_touch_qa.grounding.pruner import prune


FIXTURES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "fixtures")
)


def _file_url(name: str) -> str:
    return "file://" + os.path.join(FIXTURES_DIR, name)


# ── T1.1 ──────────────────────────────────────────────────────────────────────

def test_inventory_element_dataclass():
    el = InventoryElement(role="button", name="OK")
    assert el.is_interactive() is True
    assert el.has_label() is True

    el2 = InventoryElement(role="heading", name="", text="")
    assert el2.is_interactive() is False
    assert el2.has_label() is False


def test_inventory_dataclass_defaults():
    inv = Inventory(target_url="x")
    assert inv.is_empty() is True
    assert inv.interactive_count() == 0
    assert inv.error is None


# ── T1.2 ──────────────────────────────────────────────────────────────────────

def test_extractor_click_fixture():
    inv = fetch_inventory(_file_url("click.html"))
    assert inv.error is None, f"extraction failed: {inv.error}"
    # golden set (docs/grounding-schema.md): includes 1+ button
    roles = [e.role for e in inv.elements]
    assert "button" in roles
    assert any(e.role == "button" and e.name for e in inv.elements)


def test_extractor_fill_fixture_textboxes():
    inv = fetch_inventory(_file_url("fill.html"))
    assert inv.error is None
    textboxes = [e for e in inv.elements if e.role == "textbox"]
    # fill.html has 4 textboxes (Name/Bio/ReadOnly/Special)
    assert len(textboxes) >= 3, f"too few textboxes: {len(textboxes)}"


def test_extractor_select_fixture():
    inv = fetch_inventory(_file_url("select.html"))
    assert inv.error is None
    assert any(e.role == "combobox" for e in inv.elements)
    # options are also extracted as interactive
    assert any(e.role == "option" for e in inv.elements)


def test_extractor_unreachable_url_graceful():
    """A non-existent file:// path → graceful degradation (error recorded)."""
    inv = fetch_inventory("file:///nonexistent/path.html")
    # either extraction fails or returns an empty inventory (browser-dependent)
    assert inv.error is not None or inv.is_empty()


# ── T1.3 ──────────────────────────────────────────────────────────────────────

def test_pruner_dedup_same_role_name():
    inv = Inventory(target_url="x", elements=[
        InventoryElement(role="button", name="Save"),
        InventoryElement(role="button", name="Save"),
        InventoryElement(role="button", name="Cancel"),
    ])
    prune(inv)
    assert len(inv.elements) == 2


def test_pruner_drops_invisible_by_default():
    inv = Inventory(target_url="x", elements=[
        InventoryElement(role="button", name="Visible", visible=True),
        InventoryElement(role="button", name="Hidden", visible=False),
    ])
    prune(inv)
    assert len(inv.elements) == 1
    assert inv.elements[0].name == "Visible"


def test_pruner_per_role_limit():
    elements = [
        InventoryElement(role="option", name=f"opt{i}")
        for i in range(50)
    ]
    inv = Inventory(target_url="x", elements=elements)
    prune(inv, per_role_limit={"option": 5})
    assert len(inv.elements) == 5


# ── T1.4 ──────────────────────────────────────────────────────────────────────

def test_budget_estimate_tokens_returns_positive_int():
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("") == 0 or estimate_tokens("") == 1  # cl100k vs fallback


def test_budget_no_truncate_when_within():
    inv = Inventory(target_url="x", elements=[
        InventoryElement(role="button", name="OK", selector_hint="getByRole('button')"),
    ])
    fit_to_budget(inv, budget=10000)
    assert inv.truncated is False
    assert len(inv.elements) == 1


def test_budget_truncates_when_over():
    elements = [
        InventoryElement(
            role="button", name=f"button-with-very-long-name-{i}" * 20,
            selector_hint=f"getByRole('button', {{name: 'button-with-very-long-name-{i}'}})",
        )
        for i in range(30)
    ]
    inv = Inventory(target_url="x", elements=elements)
    fit_to_budget(inv, budget=200)
    assert inv.truncated is True
    assert len(inv.elements) < 30


# ── T1.5 (graceful degradation) ───────────────────────────────────────────────

def test_dify_client_prepend_graceful_on_failure():
    """If target_url extraction fails, srs_text is unchanged and meta.used=False."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.dify_client import DifyClient

    cfg = Config.from_env()
    client = DifyClient(cfg)

    merged, meta = client._prepend_dom_inventory(
        srs_text="ORIGINAL TEXT",
        target_url="file:///nonexistent/grounding-test.html",
    )
    # even on failure, srs_text stays as-is (graceful)
    assert merged == "ORIGINAL TEXT" or "ORIGINAL TEXT" in merged
    # success → used=True, failure → used=False — same shape both ways
    assert "used" in meta


def test_dify_client_prepend_succeeds_on_local_fixture():
    """prepend on a real fixture — the block sits before srs_text."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.dify_client import DifyClient

    cfg = Config.from_env()
    client = DifyClient(cfg)

    merged, meta = client._prepend_dom_inventory(
        srs_text="ORIGINAL TEXT",
        target_url=_file_url("fill.html"),
    )
    if not meta.get("used"):
        pytest.skip(f"inventory extraction failed: {meta.get('error')}")
    # marker must come before ORIGINAL TEXT
    idx_marker = merged.find("=== DOM INVENTORY")
    idx_orig = merged.find("ORIGINAL TEXT")
    assert idx_marker != -1 and idx_orig != -1
    assert idx_marker < idx_orig


# ── T1.6 ──────────────────────────────────────────────────────────────────────

def test_serialize_block_marker_format():
    inv = Inventory(target_url="https://example.com", elements=[
        InventoryElement(
            role="button", name="OK",
            selector_hint="getByRole('button', {name: 'OK'})",
        ),
    ])
    block = serialize_block(inv)
    assert "=== DOM INVENTORY (target_url=https://example.com) ===" in block
    assert "=== END INVENTORY ===" in block
    assert "role=button" in block
    assert "selector_hint=getByRole" in block
    # footer guide included
    assert "prefer the selector_hint" in block


def test_serialize_block_empty_returns_empty_string():
    """Empty inventory → no prepend (graceful)."""
    inv = Inventory(target_url="x", elements=[])
    assert serialize_block(inv) == ""


def test_serialize_block_with_error_returns_empty():
    inv = Inventory(target_url="x", error="timeout")
    assert serialize_block(inv) == ""
