"""Phase 1 T1.8 — fixture catalog pilot smoke (deterministic).

Verifies every role-based selector in each P0-FX-* golden scenario is
present in the actual grounding extraction (inventory). If even one is
missing, the LLM cannot generate that step's selector accurately even
with the prompt guide — i.e. the grounding upper-bound is below 1.0.

This test is deterministic (no LLM calls) and is the prerequisite for
the T1.7 pair runs that measure real impact. If a fixture fails, adjust
the fixture itself or the golden.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from grounding_eval.classifier import parse_selector

from zero_touch_qa.grounding import fetch_inventory, serialize_block
from zero_touch_qa.grounding.budget import (
    DEFAULT_TOKEN_BUDGET,
    estimate_tokens,
    fit_to_budget,
)
from zero_touch_qa.grounding.pruner import prune


FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = Path(__file__).parent / "grounding_eval" / "golden"


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _file_url(name: str) -> str:
    return "file://" + str(FIXTURES_DIR / name)


def _load_fx_golden(catalog_id: str) -> dict:
    path = GOLDEN_DIR / f"{catalog_id}.scenario.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_filename_from_url(url: str) -> str:
    # file:///app/test/fixtures/click.html → click.html
    return url.rsplit("/", 1)[-1]


@pytest.mark.parametrize("catalog_id", [
    "P0-FX-01", "P0-FX-02", "P0-FX-03", "P0-FX-04", "P0-FX-05",
])
def test_fixture_inventory_within_budget(catalog_id: str):
    """After extract → prune → budget fit, a page inventory stays within 1500 tokens."""
    spec = _load_fx_golden(catalog_id)
    fixture_name = _fixture_filename_from_url(spec["target_url"])
    url = _file_url(fixture_name)

    inv = fetch_inventory(url)
    if inv.error:
        pytest.skip(f"inventory extraction failed (no browser?): {inv.error}")

    prune(inv)
    fit_to_budget(inv, budget=DEFAULT_TOKEN_BUDGET)
    block = serialize_block(inv)
    assert block, "serialized block is empty"
    tokens = estimate_tokens(block)
    assert tokens <= DEFAULT_TOKEN_BUDGET, f"over budget: {tokens} > {DEFAULT_TOKEN_BUDGET}"


def _golden_role_step_missing_from_inventory(
    step: dict, inv_keys: set[tuple[str, str]],
) -> bool:
    """Check whether the golden step's role selector is missing from the inventory."""
    if step.get("mock_target"):
        return False
    ps = parse_selector(str(step.get("target") or ""))
    if ps.kind != "role":
        return False
    role = ps.role.lower() if ps.role else ""
    name_norm = _normalize(ps.name or "")
    if not name_norm:
        # role-only selectors with no name (e.g. getByRole('heading')) match on role alone
        return not any(r == role for r, _n in inv_keys)
    return (role, name_norm) not in inv_keys


@pytest.mark.parametrize("catalog_id", [
    "P0-FX-01", "P0-FX-02", "P0-FX-03", "P0-FX-05",
])
def test_fixture_role_selectors_present_in_inventory(catalog_id: str):
    """Every getByRole selector in the golden must exist in the inventory.

    P0-FX-04 is verify-only (no role-based selectors), so it's excluded.
    """
    spec = _load_fx_golden(catalog_id)
    fixture_name = _fixture_filename_from_url(spec["target_url"])
    url = _file_url(fixture_name)

    inv = fetch_inventory(url)
    if inv.error:
        pytest.skip(f"inventory extraction failed: {inv.error}")

    inv_keys = {(e.role.lower(), _normalize(e.name)) for e in inv.elements}
    missing = [
        (s.get("step"), s.get("target"))
        for s in spec["steps"]
        if _golden_role_step_missing_from_inventory(s, inv_keys)
    ]

    assert not missing, (
        f"{catalog_id}: {len(missing)} golden role selectors missing from inventory — "
        f"grounding upper-bound is below 100%, so the fixture or golden needs adjustment. "
        f"missing={missing}"
    )


def test_inventory_block_prepends_in_dify_client_for_each_fixture(monkeypatch):
    """For each of the 5 fixtures, _prepend_dom_inventory prepends the marker block before srs."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.dify_client import DifyClient

    cfg = Config.from_env()
    client = DifyClient(cfg)

    failures: list[str] = []
    for cid in ["P0-FX-01", "P0-FX-02", "P0-FX-03", "P0-FX-04", "P0-FX-05"]:
        spec = _load_fx_golden(cid)
        fixture_name = _fixture_filename_from_url(spec["target_url"])
        url = _file_url(fixture_name)
        merged, meta = client._prepend_dom_inventory(
            srs_text="ORIGINAL", target_url=url,
        )
        if not meta.get("used"):
            failures.append(f"{cid}: meta={meta}")
            continue
        if merged.find("=== DOM INVENTORY") > merged.find("ORIGINAL"):
            failures.append(f"{cid}: marker came after ORIGINAL")
        if (meta.get("grounding_inventory_tokens") or 0) <= 0:
            failures.append(f"{cid}: tokens=0")

    if failures:
        pytest.fail("fixture pilot failed: " + "; ".join(failures))


def test_grounding_eval_token_budget_env_override(monkeypatch):
    """env GROUNDING_TOKEN_BUDGET overrides the budget."""
    from zero_touch_qa.config import Config
    from zero_touch_qa.dify_client import DifyClient

    cfg = Config.from_env()
    client = DifyClient(cfg)

    # Tight budget → truncate must trigger. full_dsl.html's 8-element
    # inventory is ~275 tokens un-truncated (header/footer ~99 + ~22 per
    # element). With budget=180, only 3 elements survive at ~167 tokens
    # and truncated=True.
    # (The earlier budget=300 fit 8 elements / 275 tokens within the
    #  budget so truncate never fired — the token counter / fixture
    #  drift had loosened it; this fix tightens it again.)
    monkeypatch.setenv("GROUNDING_TOKEN_BUDGET", "180")
    url = _file_url("full_dsl.html")
    _, meta = client._prepend_dom_inventory(srs_text="x", target_url=url)
    if not meta.get("used"):
        pytest.skip(f"inventory extraction failed: {meta.get('error')}")
    assert meta.get("grounding_truncated") is True
    assert meta.get("grounding_inventory_tokens", 0) <= 180
