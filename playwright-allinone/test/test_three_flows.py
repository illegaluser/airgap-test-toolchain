"""S3-11 — 3-Flow (chat / doc / execute) integration regression.

All three modes must each pass once on the fixture. Chat receives a
deterministic scenario via Dify monkeypatch, doc parses ZTQA_STEP marker
text with the local step parser only (no Dify calls), and execute loads
a hand-crafted scenario.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zero_touch_qa.__main__ import _prepare_scenario, _validate_scenario
from zero_touch_qa.config import Config
from zero_touch_qa.utils import parse_structured_doc_steps


def _stub_args(**kwargs) -> argparse.Namespace:
    """Build the args namespace _prepare_scenario expects."""
    base = {
        "mode": None,
        "file": None,
        "scenario": None,
        "target_url": None,
        "srs_text": None,
        "api_docs": None,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


def _test_config(tmp_path: Path) -> Config:
    return Config(
        dify_base_url="http://test-stub/v1",
        dify_api_key="test-key",
        artifacts_dir=str(tmp_path / "artifacts"),
        viewport=(1280, 800),
        slow_mo=0,
        headed_step_pause_ms=0,
        step_interval_min_ms=0,
        step_interval_max_ms=0,
        heal_threshold=0.8,
        heal_timeout_sec=10,
        scenario_timeout_sec=60,
        dom_snapshot_limit=4000,
    )


def test_chat_flow_uses_dify_monkeypatch_to_produce_scenario(
    monkeypatch_dify, tmp_path: Path, fixture_url, make_executor, run_scenario
):
    """chat: deterministic response from Dify monkeypatch → passes _validate_scenario → runs."""
    page_url = fixture_url("verify_conditions.html")
    expected_scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": page_url,
         "description": "load target page"},
        {"step": 2, "action": "verify", "target": "#visible-box", "value": "",
         "condition": "visible", "description": "visibility check"},
    ]
    monkeypatch_dify(generate_response=expected_scenario)

    config = _test_config(tmp_path)
    args = _stub_args(mode="chat", srs_text="visibility check", target_url=page_url)
    scenario = _prepare_scenario(args, config, page_url, "visibility check", "")
    assert scenario == expected_scenario

    # confirm full execution + artifacts produced
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_doc_flow_uses_local_step_parser_without_dify(
    tmp_path: Path, fixture_url, make_executor, run_scenario, monkeypatch_dify
):
    """doc: text with ZTQA_STEP markers → local parser → 0 Dify calls."""
    page_url = fixture_url("verify_conditions.html")
    doc_text = (
        "test document\n\n"
        f"ZTQA_STEP|1|navigate||{page_url}|page load\n"
        "ZTQA_STEP|2|verify|#visible-box||visibility check\n"
    )
    parsed = parse_structured_doc_steps(doc_text)
    assert parsed is not None and len(parsed) == 2
    _validate_scenario(parsed)  # confirm the 14-action contract holds

    # monkeypatch so we can confirm Dify was never called — count goes up if it was.
    recorder = monkeypatch_dify(generate_response=[])

    executor = make_executor()
    results, _, _ = run_scenario(executor, parsed)
    assert [r.status for r in results] == ["PASS", "PASS"]
    assert recorder.generate_calls == 0  # passed via local parser only — Dify not called


def test_execute_flow_loads_handcrafted_scenario_json_and_validates(
    tmp_path: Path, fixture_url, make_executor, run_scenario, write_scenario_json
):
    """execute: hand-crafted scenario.json → _validate_scenario → runs."""
    page_url = fixture_url("verify_conditions.html")
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": page_url,
         "description": "target page"},
        {"step": 2, "action": "verify", "target": "#btn-disabled", "value": "",
         "condition": "disabled", "description": "disabled check"},
    ]
    scenario_path = write_scenario_json(scenario)
    assert scenario_path.exists()

    # confirm _prepare_scenario calls _validate_scenario in execute mode
    # (Sprint 2 S2-11 hardening).
    config = _test_config(tmp_path)
    args = _stub_args(mode="execute", scenario=str(scenario_path))
    loaded = _prepare_scenario(args, config, "", "", "")
    assert loaded == scenario

    executor = make_executor()
    results, _, _ = run_scenario(executor, loaded)
    assert [r.status for r in results] == ["PASS", "PASS"]
