import json
import os
import time
import logging
from html import escape as html_escape

from .executor import StepResult
from .metrics import read_jsonl, summarize_llm_calls

log = logging.getLogger(__name__)


def save_run_log(results: list[StepResult], output_dir: str) -> str:
    """Save the execution results as JSONL (one line per step) and return the path.

    Args:
        results: list of StepResult.
        output_dir: directory to save into.

    Returns:
        Absolute path of the generated ``run_log.jsonl``.
    """
    path = os.path.join(output_dir, "run_log.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            entry = {
                "step": r.step_id,
                "action": r.action,
                "target": r.target,
                "value": r.value,
                "description": r.description,
                "status": r.status,
                "heal_stage": r.heal_stage,
                "ts": r.timestamp,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.info("[Log] run_log.jsonl written: %s", path)
    return path


def save_scenario(
    scenario: list[dict], output_dir: str, suffix: str = ""
) -> str:
    """Save the DSL scenario as a JSON file.

    Args:
        scenario: list of DSL step dicts.
        output_dir: directory to save into.
        suffix: filename suffix. e.g. ``".healed"`` → ``scenario.healed.json``.

    Returns:
        Absolute path of the generated JSON file.
    """
    filename = f"scenario{suffix}.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)
    log.info("[Scenario] %s saved", filename)
    return path


def build_html_report(
    results: list[StepResult],
    output_dir: str,
    version: str = "4.0",
    uploaded_file: str | None = None,
    run_mode: str = "chat",
) -> str:
    """Generate a visual report (index.html) for the Jenkins HTML Publisher plugin.

    Args:
        results: list of StepResult.
        output_dir: directory to save into.
        version: version string shown on the report.
        uploaded_file: basename of the original file the user uploaded that is also
            saved in artifacts (e.g. ``upload.pdf``). When ``None``, the "Attached
            document" section is skipped.
        run_mode: execution mode (``chat`` / ``doc`` / ``convert`` / ``execute``).
            Used to vary the "Attached document" label per mode.

    Returns:
        Absolute path of the generated ``index.html``.
    """
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    healed = sum(1 for r in results if r.status == "HEALED")
    failed = sum(1 for r in results if r.status == "FAIL")
    pass_rate = round((passed + healed) / total * 100, 1) if total else 0

    rows = _build_table_rows(results)
    upload_section = _build_upload_section(uploaded_file, run_mode, output_dir)
    operations_section = _build_operations_section(output_dir)

    # Final-state screenshot section — when the last step_N_*.png shows only
    # the page before click (e.g. due to a new-tab switch), display the actual final page separately.
    final_state_path = os.path.join(output_dir, "final_state.png")
    if os.path.exists(final_state_path):
        final_section = (
            '<h2 style="margin-top:24px;">Final page reached</h2>'
            '<div class="final-state">'
            '<img src="final_state.png" alt="final state"/>'
            '<p class="caption">State of the active page after all steps finish. '
            'When click opens a new tab, the page after the switch is shown.</p>'
            '</div>'
        )
    else:
        final_section = ""

    html = _HTML_TEMPLATE.format(
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        version=version,
        total=total,
        passed=passed,
        healed=healed,
        failed=failed,
        pass_rate=pass_rate,
        rows=rows,
        upload_section=upload_section,
        operations_section=operations_section,
        final_section=final_section,
    )

    path = os.path.join(output_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[Report] HTML report written: %s", path)
    return path


def _build_upload_section(
    uploaded_file: str | None, run_mode: str, output_dir: str
) -> str:
    """HTML section so the report can download + preview the originally uploaded file.

    - PDF: inline preview via ``<object>`` (browser's built-in PDF viewer)
    - Code/JSON/text: read the file content and **embed inline as ``<pre>``** —
      renders identically regardless of the Content-Type a static server (Jenkins
      HTML Publisher, etc.) serves ``.py`` with. If the file exceeds 200KB,
      show only the prefix + a full-download link.
    - Other extensions: download link only.
    """
    if not uploaded_file:
        return ""

    label_map = {
        "doc": "Uploaded specification (PDF)",
        "convert": "Uploaded Playwright recording script",
        "execute": "Uploaded scenario JSON",
    }
    label = html_escape(label_map.get(run_mode, "Uploaded file"))
    safe_name = html_escape(uploaded_file)
    ext = uploaded_file.lower().rsplit(".", 1)[-1] if "." in uploaded_file else ""

    preview = ""
    if ext == "pdf":
        preview = (
            f'<object data="{safe_name}#view=FitH" type="application/pdf" '
            f'class="upload-preview">'
            f'This browser does not support PDF preview — '
            f'open <a href="{safe_name}">{safe_name}</a> directly.'
            f'</object>'
        )
    elif ext in ("py", "json", "txt", "yaml", "yml", "md"):
        file_path = os.path.join(output_dir, uploaded_file)
        content = ""
        truncated = False
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > 200_000:
                content = content[:200_000]
                truncated = True
        except OSError as e:
            log.warning("[Upload] %s read failed: %s", uploaded_file, e)
        if content:
            preview = f'<pre class="upload-code"><code>{html_escape(content)}</code></pre>'
            if truncated:
                preview += (
                    '<p class="note">(File exceeds 200KB; only the prefix is shown. '
                    'Use the download link above to retrieve the full content.)</p>'
                )

    return (
        '<h2 style="margin-top:24px;">Attached document</h2>'
        '<div class="upload-section">'
        f'<p><strong>{label}:</strong> '
        f'<a href="{safe_name}" download>{safe_name}</a></p>'
        f'{preview}'
        '</div>'
    )


def _build_table_rows(results: list[StepResult]) -> str:
    """Convert a list of StepResult into HTML table ``<tr>`` row strings."""
    rows = []
    for r in results:
        badge_class = {"PASS": "ok", "HEALED": "warn", "FAIL": "fail"}.get(
            r.status, "skip"
        )
        heal_info = f" ({r.heal_stage})" if r.heal_stage != "none" else ""

        if r.status == "PASS":
            screenshot = f"step_{r.step_id}_pass.png"
        elif r.status == "HEALED":
            screenshot = f"step_{r.step_id}_healed.png"
        else:
            screenshot = "error_final.png"

        rows.append(
            "<tr>"
            f"<td>{html_escape(str(r.step_id))}</td>"
            f"<td>{html_escape(r.action)}</td>"
            f"<td title=\"{html_escape(r.target)}\">"
            f"{html_escape(r.target[:60])}</td>"
            f"<td>{html_escape(r.description[:60])}</td>"
            f"<td><span class='badge {badge_class}'>"
            f"{html_escape(r.status)}{html_escape(heal_info)}</span></td>"
            f"<td><a href='{html_escape(screenshot)}' target='_blank'>"
            f"<img src='{html_escape(screenshot)}' class='thumb' alt='step {html_escape(str(r.step_id))}'/>"
            f"</a></td>"
            "</tr>"
        )
    return "\n      ".join(rows)


def _build_operations_section(output_dir: str) -> str:
    """Build the optional operations metric section for Jenkins HTML reports."""
    rows: list[str] = []

    llm_calls_path = os.path.join(output_dir, "llm_calls.jsonl")
    llm_rows = read_jsonl(llm_calls_path)
    if llm_rows:
        summary = summarize_llm_calls(llm_rows)
        latency = summary["latency_ms"]
        rows.extend(
            [
                _metric_row(
                    "LLM calls",
                    f"{summary['total_calls']}",
                    "llm_calls.jsonl",
                    "Total Dify calls (Planner + Healer)",
                ),
                _metric_row(
                    "LLM latency",
                    (
                        f"p50 {latency['p50']}ms / "
                        f"p95 {latency['p95']}ms / p99 {latency['p99']}ms"
                    ),
                    "llm_calls.jsonl",
                    "Percentiles computed from llm_calls.jsonl",
                ),
                _metric_row(
                    "LLM timeout",
                    f"{summary['timeout_count']} ({summary['timeout_rate'] * 100:.1f}%)",
                    "llm_calls.jsonl",
                    "Ratio of timeout=true records",
                ),
                _metric_row(
                    "LLM retry",
                    f"{summary['retry_total']}",
                    "llm_calls.jsonl",
                    "Sum of retry_count",
                ),
            ]
        )
        for kind, by_kind in sorted(summary.get("by_kind", {}).items()):
            kind_latency = by_kind["latency_ms"]
            rows.append(
                _metric_row(
                    f"LLM {kind}",
                    (
                        f"{by_kind['total_calls']} calls, "
                        f"p95 {kind_latency['p95']}ms, "
                        f"timeout {by_kind['timeout_count']}"
                    ),
                    "llm_calls.jsonl",
                    "Per-kind call summary",
                )
            )

    rows.extend(_build_json_metric_rows(output_dir))

    if not rows:
        return ""

    return (
        '<h2 style="margin-top:24px;">Operations metrics</h2>'
        '<table class="ops-table">'
        '<thead><tr><th>Metric</th><th>Value</th><th>Source</th><th>Note</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _build_json_metric_rows(output_dir: str) -> list[str]:
    metric_files = [
        ("planner_accuracy.json", "Planner accuracy"),
        ("healer_accuracy.json", "Healer accuracy"),
        ("llm_sla.json", "LLM SLA"),
        ("heal_metrics.json", "Healing"),
        ("flake_metrics.json", "Flake"),
        ("pytest_summary.json", "pytest"),
    ]
    rows: list[str] = []
    for filename, label in metric_files:
        payload = _read_json_metric(os.path.join(output_dir, filename))
        if payload is None:
            continue
        rows.append(
            _metric_row(
                label,
                _summarize_metric_payload(payload),
                filename,
                "Link to the source metric JSON",
            )
        )
    return rows


def _read_json_metric(path: str) -> object | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[Report] metric file read failed: %s (%s)", path, e)
        return None


def _summarize_metric_payload(payload: object) -> str:
    if isinstance(payload, dict):
        preferred_keys = (
            "accuracy",
            "pass_rate",
            "success_rate",
            "flake_rate",
            "timeout_rate",
            "total",
            "passed",
            "failed",
            "p95",
        )
        parts = [
            f"{key}={payload[key]}"
            for key in preferred_keys
            if key in payload and payload[key] is not None
        ]
        if parts:
            return ", ".join(parts[:5])
        return f"{len(payload)} keys"
    if isinstance(payload, list):
        return f"{len(payload)} rows"
    return str(payload)


def _metric_row(metric: str, value: str, source: str, note: str) -> str:
    safe_source = html_escape(source)
    return (
        "<tr>"
        f"<td>{html_escape(metric)}</td>"
        f"<td>{html_escape(value)}</td>"
        f"<td><a href='{safe_source}' target='_blank'>{safe_source}</a></td>"
        f"<td>{html_escape(note)}</td>"
        "</tr>"
    )


# ── HTML template ──
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Zero-Touch QA execution report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           margin: 24px; color: #0f172a; background: #f1f5f9; line-height: 1.45; }}
    h1 {{ margin: 0 0 16px; }}
    .meta {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px;
             padding: 14px; margin-bottom: 16px; }}
    .meta p {{ margin: 4px 0; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
              gap: 10px; margin-bottom: 16px; }}
    .card {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px; padding: 12px; }}
    .card .label {{ font-size: 12px; color: #475569; }}
    .card .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
             border: 2px solid #334155; margin-top: 8px; }}
    th, td {{ border: 1px solid #64748b; padding: 8px; vertical-align: top;
              text-align: left; font-size: 13px; }}
    th {{ background: #eef2ff; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px;
              font-size: 12px; font-weight: 700; }}
    .ok {{ background: #dcfce7; color: #166534; }}
    .warn {{ background: #fef3c7; color: #92400e; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .skip {{ background: #e2e8f0; color: #334155; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .footer {{ margin-top: 16px; font-size: 12px; color: #64748b; }}
    .thumb {{ max-width: 160px; max-height: 100px; border: 1px solid #cbd5e1;
              border-radius: 4px; display: block; }}
    .thumb:hover {{ border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    h2 {{ margin: 24px 0 8px; font-size: 18px; color: #0f172a; }}
    .final-state {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px;
                    padding: 12px; }}
    .final-state img {{ max-width: 100%; border: 1px solid #cbd5e1; border-radius: 4px; }}
    .final-state .caption {{ margin-top: 8px; font-size: 13px; color: #475569; }}
    .upload-section {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px;
                       padding: 12px; }}
    .upload-section p {{ margin: 0 0 8px; }}
    .upload-section .note {{ color: #94a3b8; font-size: 12px; margin: 8px 0 0; }}
    .upload-preview {{ width: 100%; height: 520px; border: 1px solid #cbd5e1;
                       border-radius: 4px; }}
    .upload-code {{ background: #0f172a; color: #e2e8f0; padding: 12px 14px;
                    border-radius: 6px; overflow: auto; max-height: 480px;
                    font-family: Menlo, Consolas, "Liberation Mono", monospace;
                    font-size: 12px; line-height: 1.5; margin: 0;
                    white-space: pre; }}
    .upload-code code {{ background: transparent; color: inherit; padding: 0; }}
    .ops-table td:nth-child(2) {{ font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Zero-Touch QA execution report</h1>
  <div class="meta">
    <p>Run time: <strong>{timestamp}</strong></p>
    <p>Version: <strong>v{version}</strong></p>
  </div>
  <div class="cards">
    <div class="card">
      <div class="label">Total steps</div><div class="value">{total}</div>
    </div>
    <div class="card">
      <div class="label">Passed (PASS)</div><div class="value">{passed}</div>
    </div>
    <div class="card">
      <div class="label">Self-healed (HEALED)</div><div class="value">{healed}</div>
    </div>
    <div class="card">
      <div class="label">Failed (FAIL)</div><div class="value">{failed}</div>
    </div>
    <div class="card">
      <div class="label">Pass rate</div><div class="value">{pass_rate}%</div>
    </div>
  </div>
  {upload_section}
  {operations_section}
  <h2 style="margin-top:24px;">Per-step results</h2>
  <table>
    <thead>
      <tr>
        <th>Step</th><th>Action</th><th>Target</th>
        <th>Description</th><th>Status</th><th>Evidence</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  {final_section}
  <div class="footer">
    Generated by DSCORE Zero-Touch QA v{version}
  </div>
</body>
</html>"""
