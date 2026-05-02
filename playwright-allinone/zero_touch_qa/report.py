import json
import os
import time
import logging
from html import escape as html_escape

from .executor import StepResult
from .metrics import read_jsonl, summarize_llm_calls

log = logging.getLogger(__name__)


def save_run_log(results: list[StepResult], output_dir: str) -> str:
    """실행 결과를 JSONL(한 줄에 한 스텝) 형식으로 저장하고 파일 경로를 반환한다.

    Args:
        results: StepResult 리스트.
        output_dir: 저장 디렉터리.

    Returns:
        생성된 ``run_log.jsonl`` 의 절대 경로.
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
            # 스텝 실행 중 발생한 네이티브 dialog 텍스트 (있을 때만 키 추가).
            if getattr(r, "dialog_text", None):
                entry["dialog_text"] = r.dialog_text
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.info("[Log] run_log.jsonl 생성 완료: %s", path)
    return path


def save_scenario(
    scenario: list[dict], output_dir: str, suffix: str = ""
) -> str:
    """DSL 시나리오를 JSON 파일로 저장한다.

    Args:
        scenario: DSL 스텝 dict 리스트.
        output_dir: 저장 디렉터리.
        suffix: 파일명 접미사. 예: ``".healed"`` → ``scenario.healed.json``.

    Returns:
        생성된 JSON 파일의 절대 경로.
    """
    filename = f"scenario{suffix}.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)
    log.info("[Scenario] %s 저장 완료", filename)
    return path


def build_html_report(
    results: list[StepResult],
    output_dir: str,
    version: str = "4.0",
    uploaded_file: str | None = None,
    run_mode: str = "chat",
) -> str:
    """Jenkins HTML Publisher 플러그인용 시각적 리포트(index.html)를 생성한다.

    Args:
        results: StepResult 리스트.
        output_dir: 저장 디렉터리.
        version: 리포트에 표시할 버전 문자열.
        uploaded_file: 사용자가 업로드해 artifacts 에 함께 저장된 원본 파일의
            basename (예: ``upload.pdf``). ``None`` 이면 "첨부 문서" 섹션 생략.
        run_mode: 실행 모드 (``chat`` / ``doc`` / ``convert`` / ``execute``).
            "첨부 문서" 라벨을 모드별로 달리 표시하기 위해 사용.

    Returns:
        생성된 ``index.html`` 의 절대 경로.
    """
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    healed = sum(1 for r in results if r.status == "HEALED")
    failed = sum(1 for r in results if r.status == "FAIL")
    pass_rate = round((passed + healed) / total * 100, 1) if total else 0

    rows = _build_table_rows(results)
    upload_section = _build_upload_section(uploaded_file, run_mode, output_dir)
    operations_section = _build_operations_section(output_dir)

    # 최종 상태 스크린샷 섹션 — 새 탭 전환 등으로 마지막 step_N_*.png 가
    # click 직전 페이지만 보여주는 경우 실제 도달 페이지를 별도 표시.
    final_state_path = os.path.join(output_dir, "final_state.png")
    if os.path.exists(final_state_path):
        final_section = (
            '<h2 style="margin-top:24px;">최종 도달 페이지</h2>'
            '<div class="final-state">'
            '<img src="final_state.png" alt="최종 상태"/>'
            '<p class="caption">모든 스텝 종료 후 활성 페이지의 상태입니다. '
            'click 이 새 탭을 연 경우 전환 후 페이지가 표시됩니다.</p>'
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
    log.info("[Report] HTML 리포트 생성 완료: %s", path)
    return path


def _build_upload_section(
    uploaded_file: str | None, run_mode: str, output_dir: str
) -> str:
    """업로드된 원본 파일을 리포트에서 다운로드 + 미리보기 할 수 있는 HTML 섹션.

    - PDF: ``<object>`` 로 inline preview (브라우저 내장 PDF 뷰어)
    - 코드/JSON/텍스트: 파일 내용을 읽어 **인라인 ``<pre>`` 로 임베드** — Jenkins
      HTML Publisher 등 정적 서버가 ``.py`` 를 어떤 Content-Type 으로 serving
      하든 무관하게 동일하게 보인다. 파일이 200KB 를 넘으면 앞부분만 표시 +
      전체 다운로드 링크.
    - 그 외 확장자: 다운로드 링크만.
    """
    if not uploaded_file:
        return ""

    label_map = {
        "doc": "업로드 기획서 (PDF)",
        "convert": "업로드 Playwright 녹화 스크립트",
        "execute": "업로드 시나리오 JSON",
    }
    label = html_escape(label_map.get(run_mode, "업로드 파일"))
    safe_name = html_escape(uploaded_file)
    ext = uploaded_file.lower().rsplit(".", 1)[-1] if "." in uploaded_file else ""

    preview = ""
    if ext == "pdf":
        preview = (
            f'<object data="{safe_name}#view=FitH" type="application/pdf" '
            f'class="upload-preview">'
            f'PDF 미리보기를 지원하지 않는 브라우저입니다 — '
            f'<a href="{safe_name}">{safe_name}</a> 을(를) 직접 열어보세요.'
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
            log.warning("[Upload] %s 읽기 실패: %s", uploaded_file, e)
        if content:
            preview = f'<pre class="upload-code"><code>{html_escape(content)}</code></pre>'
            if truncated:
                preview += (
                    '<p class="note">(파일이 200KB 를 넘어 앞부분만 표시됩니다. '
                    '전체 내용은 위 다운로드 링크로 받아보세요.)</p>'
                )

    return (
        '<h2 style="margin-top:24px;">첨부 문서</h2>'
        '<div class="upload-section">'
        f'<p><strong>{label}:</strong> '
        f'<a href="{safe_name}" download>{safe_name}</a></p>'
        f'{preview}'
        '</div>'
    )


def _build_table_rows(results: list[StepResult]) -> str:
    """StepResult 리스트를 HTML 테이블 ``<tr>`` 행 문자열로 변환한다."""
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
                    "LLM 호출",
                    f"{summary['total_calls']}회",
                    "llm_calls.jsonl",
                    "Planner/Healer 전체 Dify 호출 수",
                ),
                _metric_row(
                    "LLM latency",
                    (
                        f"p50 {latency['p50']}ms / "
                        f"p95 {latency['p95']}ms / p99 {latency['p99']}ms"
                    ),
                    "llm_calls.jsonl",
                    "llm_calls.jsonl 기반 percentile",
                ),
                _metric_row(
                    "LLM timeout",
                    f"{summary['timeout_count']}건 ({summary['timeout_rate'] * 100:.1f}%)",
                    "llm_calls.jsonl",
                    "timeout=true 레코드 비율",
                ),
                _metric_row(
                    "LLM retry",
                    f"{summary['retry_total']}회",
                    "llm_calls.jsonl",
                    "retry_count 합계",
                ),
            ]
        )
        for kind, by_kind in sorted(summary.get("by_kind", {}).items()):
            kind_latency = by_kind["latency_ms"]
            rows.append(
                _metric_row(
                    f"LLM {kind}",
                    (
                        f"{by_kind['total_calls']}회, "
                        f"p95 {kind_latency['p95']}ms, "
                        f"timeout {by_kind['timeout_count']}건"
                    ),
                    "llm_calls.jsonl",
                    "kind 별 호출 요약",
                )
            )

    rows.extend(_build_json_metric_rows(output_dir))

    if not rows:
        return ""

    return (
        '<h2 style="margin-top:24px;">운영 지표</h2>'
        '<table class="ops-table">'
        '<thead><tr><th>Metric</th><th>Value</th><th>Source</th><th>Note</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _build_json_metric_rows(output_dir: str) -> list[str]:
    metric_files = [
        ("planner_accuracy.json", "Planner 정확도"),
        ("healer_accuracy.json", "Healer 정확도"),
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
                "원본 metric JSON 링크",
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
        log.warning("[Report] metric 파일 읽기 실패: %s (%s)", path, e)
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


# ── HTML 템플릿 ──
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Zero-Touch QA 실행 리포트</title>
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
  <h1>Zero-Touch QA 실행 리포트</h1>
  <div class="meta">
    <p>실행 시각: <strong>{timestamp}</strong></p>
    <p>버전: <strong>v{version}</strong></p>
  </div>
  <div class="cards">
    <div class="card">
      <div class="label">전체 스텝</div><div class="value">{total}</div>
    </div>
    <div class="card">
      <div class="label">성공 (PASS)</div><div class="value">{passed}</div>
    </div>
    <div class="card">
      <div class="label">자가치유 (HEALED)</div><div class="value">{healed}</div>
    </div>
    <div class="card">
      <div class="label">실패 (FAIL)</div><div class="value">{failed}</div>
    </div>
    <div class="card">
      <div class="label">성공률</div><div class="value">{pass_rate}%</div>
    </div>
  </div>
  {upload_section}
  {operations_section}
  <h2 style="margin-top:24px;">스텝별 실행 결과</h2>
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
