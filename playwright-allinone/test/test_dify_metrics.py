import json
from dataclasses import replace
from pathlib import Path

import pytest
import requests

from zero_touch_qa.config import Config
from zero_touch_qa.dify_client import DifyClient, DifyConnectionError
from zero_touch_qa.metrics import (
    aggregate_llm_sla,
    append_jsonl,
    read_jsonl,
    summarize_llm_calls,
)


class _FakeResponse:
    def __init__(self, status_code=200, answer="[]"):
        self.status_code = status_code
        self._answer = answer

    def json(self):
        return {"answer": self._answer}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


def _config(tmp_path: Path) -> Config:
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


def test_dify_call_writes_success_metric(monkeypatch, tmp_path: Path):
    cfg = _config(tmp_path)
    client = DifyClient(cfg)

    def fake_request(*args, **kwargs):
        return _FakeResponse(
            answer=json.dumps([
                {"step": 1, "action": "navigate", "target": "", "value": "http://x"}
            ])
        )

    monkeypatch.setattr(requests, "request", fake_request)

    scenario = client.generate_scenario("chat", "go", "http://x")

    assert scenario[0]["action"] == "navigate"
    rows = read_jsonl(str(Path(cfg.artifacts_dir) / "llm_calls.jsonl"))
    assert len(rows) == 1
    assert rows[0]["kind"] == "planner"
    assert rows[0]["status_code"] == 200
    assert rows[0]["timeout"] is False
    assert rows[0]["answer_chars"] > 0
    assert rows[0]["error"] == ""


def test_dify_call_writes_timeout_metric(monkeypatch, tmp_path: Path):
    cfg = replace(_config(tmp_path), scenario_timeout_sec=1)
    client = DifyClient(cfg)

    def fake_request(*args, **kwargs):
        raise requests.Timeout("slow model")

    monkeypatch.setattr(requests, "request", fake_request)

    with pytest.raises(DifyConnectionError):
        client.generate_scenario("chat", "go", "http://x")

    rows = read_jsonl(str(Path(cfg.artifacts_dir) / "llm_calls.jsonl"))
    assert len(rows) == 1
    assert rows[0]["kind"] == "planner"
    assert rows[0]["timeout"] is True
    assert rows[0]["retry_count"] == 1
    assert "slow model" in rows[0]["error"]


def test_summarize_llm_calls_computes_baseline_metrics():
    summary = summarize_llm_calls(
        [
            {"kind": "planner", "elapsed_ms": 100, "retry_count": 0},
            {"kind": "planner", "elapsed_ms": 200, "retry_count": 1},
            {"kind": "healer", "elapsed_ms": 300, "timeout": True, "retry_count": 0},
        ]
    )

    assert summary["total_calls"] == 3
    assert summary["timeout_count"] == 1
    assert summary["retry_total"] == 1
    assert summary["latency_ms"]["p50"] == 200.0
    assert summary["by_kind"]["planner"]["total_calls"] == 2
    assert summary["by_kind"]["healer"]["timeout_count"] == 1


# ─── S4C-05 — llm_sla.json aggregation ─────────────────────────────────


def test_aggregate_llm_sla_writes_summary_json(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    src = artifacts_dir / "llm_calls.jsonl"
    append_jsonl(str(src), {"kind": "planner", "elapsed_ms": 100, "retry_count": 0})
    append_jsonl(str(src), {"kind": "healer", "elapsed_ms": 250, "retry_count": 0})

    out_path = aggregate_llm_sla(str(artifacts_dir))
    assert out_path is not None
    payload = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert payload["total_calls"] == 2
    assert payload["latency_ms"]["p95"] >= 100
    assert "planner" in payload["by_kind"]
    assert "healer" in payload["by_kind"]


def test_aggregate_llm_sla_returns_none_when_jsonl_missing(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    assert aggregate_llm_sla(str(artifacts_dir)) is None
    assert not (artifacts_dir / "llm_sla.json").exists()


def test_aggregate_llm_sla_no_op_when_artifacts_dir_none():
    assert aggregate_llm_sla(None) is None
