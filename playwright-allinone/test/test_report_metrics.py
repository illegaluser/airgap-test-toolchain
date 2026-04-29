import json
from pathlib import Path

from zero_touch_qa.executor import StepResult
from zero_touch_qa.metrics import append_jsonl
from zero_touch_qa.report import build_html_report


def _result() -> StepResult:
    return StepResult(
        step_id=1,
        action="navigate",
        target="",
        value="http://example.test",
        description="open page",
        status="PASS",
    )


def test_report_omits_operations_section_when_metrics_are_absent(tmp_path: Path):
    report_path = build_html_report([_result()], str(tmp_path))

    html = Path(report_path).read_text(encoding="utf-8")

    assert "Operations metrics" not in html
    assert "Per-step results" in html


def test_report_renders_llm_metrics_and_json_metric_links(tmp_path: Path):
    append_jsonl(
        str(tmp_path / "llm_calls.jsonl"),
        {
            "kind": "planner",
            "elapsed_ms": 100,
            "retry_count": 1,
            "timeout": False,
        },
    )
    append_jsonl(
        str(tmp_path / "llm_calls.jsonl"),
        {
            "kind": "healer",
            "elapsed_ms": 300,
            "retry_count": 0,
            "timeout": True,
        },
    )
    (tmp_path / "planner_accuracy.json").write_text(
        json.dumps({"accuracy": 0.9, "total": 10}, ensure_ascii=False),
        encoding="utf-8",
    )

    report_path = build_html_report([_result()], str(tmp_path))

    html = Path(report_path).read_text(encoding="utf-8")
    assert "Operations metrics" in html
    assert "LLM latency" in html
    assert "p95 300.0ms" in html
    assert "LLM timeout" in html
    assert "50.0%" in html
    assert "planner_accuracy.json" in html
