"""Lightweight metric helpers for Zero-Touch QA artifacts."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from typing import Any


def append_jsonl(path: str | None, record: dict[str, Any]) -> None:
    """Append one JSON record to a JSON Lines file.

    The helper is intentionally best-effort for callers that use it for
    observability. Directory creation or write failures should be surfaced to
    tests, but production call sites may catch OSError if they prefer no-op.
    """
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        f.write("\n")


def read_jsonl(path: str) -> list[dict[str, Any]]:
    """Read a JSON Lines file and skip blank lines."""
    if not os.path.exists(path):
        return []
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def percentile(values: Iterable[float], pct: float) -> float:
    """Return nearest-rank percentile for a small metric sample."""
    sorted_values = sorted(float(v) for v in values)
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    rank = max(1, int(round((pct / 100.0) * len(sorted_values))))
    return sorted_values[min(rank - 1, len(sorted_values) - 1)]


def summarize_llm_calls(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize Dify Planner/Healer call metrics."""
    durations = [float(r.get("elapsed_ms", 0)) for r in records]
    timeouts = [r for r in records if r.get("timeout")]
    errors = [r for r in records if r.get("error")]
    retry_total = sum(int(r.get("retry_count", 0) or 0) for r in records)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_calls": len(records),
        "timeout_count": len(timeouts),
        "timeout_rate": round(len(timeouts) / len(records), 4) if records else 0.0,
        "error_count": len(errors),
        "retry_total": retry_total,
        "latency_ms": {
            "p50": percentile(durations, 50),
            "p95": percentile(durations, 95),
            "p99": percentile(durations, 99),
        },
        "by_kind": _summarize_by_kind(records),
    }


def aggregate_llm_sla(artifacts_dir: str | None) -> str | None:
    """Read `llm_calls.jsonl` and aggregate it into `llm_sla.json`.

    Called at build-end time so the Zero-Touch QA Report ops-metrics section
    surfaces LLM SLA. No-op (returns None) if `llm_calls.jsonl` is absent or empty.

    Args:
        artifacts_dir: absolute path to the artifacts directory. No-op if missing or absent.

    Returns:
        absolute path of the produced `llm_sla.json`, or None if there is no input data.
    """
    if not artifacts_dir:
        return None
    src = os.path.join(artifacts_dir, "llm_calls.jsonl")
    rows = read_jsonl(src)
    if not rows:
        return None
    summary = summarize_llm_calls(rows)
    out_path = os.path.join(artifacts_dir, "llm_sla.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)
    return out_path


def _summarize_by_kind(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("kind", "unknown")), []).append(record)

    summary: dict[str, dict[str, Any]] = {}
    for kind, rows in grouped.items():
        durations = [float(r.get("elapsed_ms", 0)) for r in rows]
        summary[kind] = {
            "total_calls": len(rows),
            "timeout_count": sum(1 for r in rows if r.get("timeout")),
            "error_count": sum(1 for r in rows if r.get("error")),
            "retry_total": sum(int(r.get("retry_count", 0) or 0) for r in rows),
            "latency_ms": {
                "p50": percentile(durations, 50),
                "p95": percentile(durations, 95),
                "p99": percentile(durations, 99),
            },
        }
    return summary
