"""S3-11 — 3-Flow (chat / doc / execute) 통합 회귀.

3 모드 모두 fixture 위에서 한 번씩 통과해야 한다. chat 은 Dify monkeypatch
로 결정론적 시나리오를 받고, doc 은 ZTQA_STEP marker 를 가진 텍스트 입력으로
로컬 step parser 만으로 통과 (Dify 호출 없음), execute 는 손작성 scenario.json
입력.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zero_touch_qa.__main__ import _prepare_scenario, _validate_scenario
from zero_touch_qa.config import Config
from zero_touch_qa.utils import parse_structured_doc_steps


def _stub_args(**kwargs) -> argparse.Namespace:
    """_prepare_scenario 가 기대하는 args namespace 를 합쳐서 만들어준다."""
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
    """chat: Dify monkeypatch 결정론적 응답 → _validate_scenario 통과 → 실행."""
    page_url = fixture_url("verify_conditions.html")
    expected_scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": page_url,
         "description": "대상 페이지 로드"},
        {"step": 2, "action": "verify", "target": "#visible-box", "value": "",
         "condition": "visible", "description": "가시성 검증"},
    ]
    monkeypatch_dify(generate_response=expected_scenario)

    config = _test_config(tmp_path)
    args = _stub_args(mode="chat", srs_text="가시성 확인", target_url=page_url)
    scenario = _prepare_scenario(args, config, page_url, "가시성 확인", "")
    assert scenario == expected_scenario

    # 실행까지 통과 확인 — 산출물도 정상 생성
    executor = make_executor()
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS"]


def test_doc_flow_uses_local_step_parser_without_dify(
    tmp_path: Path, fixture_url, make_executor, run_scenario, monkeypatch_dify
):
    """doc: ZTQA_STEP marker 가 있는 텍스트 → 로컬 파서 → Dify 호출 0."""
    page_url = fixture_url("verify_conditions.html")
    doc_text = (
        "테스트 문서\n\n"
        f"ZTQA_STEP|1|navigate||{page_url}|페이지 로드\n"
        "ZTQA_STEP|2|verify|#visible-box||가시성 검증\n"
    )
    parsed = parse_structured_doc_steps(doc_text)
    assert parsed is not None and len(parsed) == 2
    _validate_scenario(parsed)  # 14대 계약 통과 확인

    # Dify 호출 0 인지 확인하기 위해 monkeypatch — 호출되면 카운트가 증가.
    recorder = monkeypatch_dify(generate_response=[])

    executor = make_executor()
    results, _, _ = run_scenario(executor, parsed)
    assert [r.status for r in results] == ["PASS", "PASS"]
    assert recorder.generate_calls == 0  # 로컬 파서로만 통과 — Dify 안 부름


def test_execute_flow_loads_handcrafted_scenario_json_and_validates(
    tmp_path: Path, fixture_url, make_executor, run_scenario, write_scenario_json
):
    """execute: 손작성 scenario.json → _validate_scenario → 실행."""
    page_url = fixture_url("verify_conditions.html")
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": page_url,
         "description": "대상 페이지"},
        {"step": 2, "action": "verify", "target": "#btn-disabled", "value": "",
         "condition": "disabled", "description": "비활성 검증"},
    ]
    scenario_path = write_scenario_json(scenario)
    assert scenario_path.exists()

    # _prepare_scenario 가 execute 모드에서 _validate_scenario 를 호출하는지
    # 확인 (Sprint 2 의 S2-11 보강).
    config = _test_config(tmp_path)
    args = _stub_args(mode="execute", scenario=str(scenario_path))
    loaded = _prepare_scenario(args, config, "", "", "")
    assert loaded == scenario

    executor = make_executor()
    results, _, _ = run_scenario(executor, loaded)
    assert [r.status for r in results] == ["PASS", "PASS"]
