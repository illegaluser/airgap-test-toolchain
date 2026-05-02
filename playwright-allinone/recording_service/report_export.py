"""Self-contained HTML 리포트 생성 — 결과 패널의 'run_log' 를 외부 공유용 단일 파일로 export.

세션 디렉터리 안의 ``run_log.jsonl`` (LLM 모드) 와 ``codegen_run_log.jsonl``
(원본 모드) 를 읽어, 각 step 의 스크린샷을 base64 로 임베드한 단일 HTML 본문을
반환한다. 받는 쪽이 별도 파일 / 디렉터리 / 인터넷 없이도 더블클릭으로 모든
스텝 + 스크린샷을 검토 가능.

생성 흐름은 시간 순서나 외부 의존성 없이 *디스크에 떨어진 산출물* 만으로 결정
— LLM 모드만 실행했어도, 원본 모드만 실행했어도, 둘 다 실행했어도 자연스럽게
존재하는 섹션만 렌더된다.
"""

from __future__ import annotations

import base64
import json
from html import escape as html_escape
from pathlib import Path
from typing import Optional


def build_self_contained_report(session_dir: Path) -> Optional[str]:
    """세션 디렉터리에서 self-contained HTML 본문을 생성해 반환.

    LLM 모드 / 원본 모드 산출물이 둘 다 없으면 ``None`` 반환 — 호출자(endpoint)
    가 404 처리.

    Args:
        session_dir: 세션 디렉터리 절대 경로 (``run_log.jsonl`` 등이 있는 곳).

    Returns:
        완전한 HTML 본문 (``<!DOCTYPE html>`` 부터 ``</html>`` 까지) 또는 ``None``.
    """
    llm_section = _build_llm_section(session_dir)
    cg_section = _build_codegen_section(session_dir)
    if not llm_section and not cg_section:
        return None

    sid = session_dir.name
    meta_html = _build_meta_section(session_dir)

    body_sections = []
    if llm_section:
        body_sections.append(llm_section)
    if cg_section:
        body_sections.append(cg_section)

    return _PAGE_TEMPLATE.format(
        sid=html_escape(sid),
        meta_section=meta_html,
        body="\n".join(body_sections),
    )


def _build_meta_section(session_dir: Path) -> str:
    """세션 메타(metadata.json) 가 있으면 키-값 표로 렌더, 없으면 빈 문자열."""
    meta_path = session_dir / "metadata.json"
    if not meta_path.is_file():
        return ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(meta, dict):
        return ""
    rows = []
    # 보여줄 키만 화이트리스트 — 외부 공유 시 민감 정보 노출 방지.
    visible_keys = ("target_url", "auth_profile", "created_at",
                    "state", "action_count", "planning_doc_ref")
    for k in visible_keys:
        if k in meta and meta[k] not in (None, ""):
            rows.append(
                f"<tr><th>{html_escape(k)}</th>"
                f"<td>{html_escape(str(meta[k]))}</td></tr>"
            )
    if not rows:
        return ""
    return (
        '<section class="meta"><h2>세션 메타</h2>'
        f'<table class="meta-table"><tbody>{"".join(rows)}</tbody></table>'
        '</section>'
    )


def _build_llm_section(session_dir: Path) -> str:
    """LLM 모드 (``run_log.jsonl``) 섹션. 파일 없으면 빈 문자열."""
    log_path = session_dir / "run_log.jsonl"
    if not log_path.is_file():
        return ""
    records = _read_jsonl(log_path)
    if not records:
        return ""
    rows = []
    for r in records:
        step = r.get("step", "?")
        status = (r.get("status") or "").upper()
        heal = r.get("heal_stage", "none")
        screenshot_name = _llm_screenshot_name(step, status)
        img_html = _embed_image(session_dir, screenshot_name) or ""
        rows.append(_render_row_llm(step, r, status, heal, img_html))
    summary = _summary_html(records)
    return (
        '<section class="run"><h2>LLM 적용 코드 실행 결과</h2>'
        f'{summary}'
        f'<table class="run-table"><thead>{_LLM_HEADER}</thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        '</section>'
    )


def _build_codegen_section(session_dir: Path) -> str:
    """원본 모드 (``codegen_run_log.jsonl`` + ``codegen_screenshots/``)."""
    log_path = session_dir / "codegen_run_log.jsonl"
    if not log_path.is_file():
        return ""
    records = _read_jsonl(log_path)
    if not records:
        return ""
    cg_shots_dir = session_dir / "codegen_screenshots"
    rows = []
    for r in records:
        step = r.get("step", "?")
        status = (r.get("status") or "").upper()
        screenshot_name = r.get("screenshot") or ""
        img_html = _embed_image(cg_shots_dir, screenshot_name) or ""
        rows.append(_render_row_codegen(step, r, status, img_html))
    summary = _summary_html(records)
    return (
        '<section class="run"><h2>원본 코드 실행 결과 (codegen)</h2>'
        f'{summary}'
        f'<table class="run-table"><thead>{_CG_HEADER}</thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        '</section>'
    )


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _llm_screenshot_name(step: int | str, status: str) -> str:
    """run_log.jsonl 에는 screenshot 필드가 없어 status 기반으로 이름 추정."""
    if status == "PASS":
        return f"step_{step}_pass.png"
    if status == "HEALED":
        return f"step_{step}_healed.png"
    if status == "FAIL":
        return f"step_{step}_fail.png"
    return ""


def _embed_image(base_dir: Path, name: str) -> Optional[str]:
    """``base_dir / name`` 을 base64 임베드한 ``<img>`` 태그 반환. 없으면 None."""
    if not name:
        return None
    p = base_dir / name
    if not p.is_file():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    safe_alt = html_escape(name)
    return (
        f'<img class="shot" src="data:{mime};base64,{b64}" alt="{safe_alt}"/>'
    )


def _render_row_llm(step, rec: dict, status: str, heal: str, img_html: str) -> str:
    badge_class = _badge_class(status)
    heal_label = "" if heal in ("none", "", None) else f" ({html_escape(str(heal))})"
    dialog_html = _render_dialog_block(rec.get("dialog_text"))
    return (
        "<tr>"
        f"<td>{html_escape(str(step))}</td>"
        f"<td>{html_escape(str(rec.get('action', '')))}</td>"
        f"<td>{html_escape(str(rec.get('target', ''))[:120])}</td>"
        f"<td>{html_escape(str(rec.get('description', ''))[:200])}</td>"
        f"<td><span class='badge {badge_class}'>{html_escape(status)}{heal_label}</span></td>"
        f"<td class='shot-cell'>{img_html}{dialog_html}</td>"
        "</tr>"
    )


def _render_row_codegen(step, rec: dict, status: str, img_html: str) -> str:
    badge_class = _badge_class(status)
    ts_ms = rec.get("ts")
    ts_label = f"{ts_ms:.0f}ms" if isinstance(ts_ms, (int, float)) else ""
    dialog_html = _render_dialog_block(rec.get("dialog_text"))
    return (
        "<tr>"
        f"<td>{html_escape(str(step))}</td>"
        f"<td>{html_escape(str(rec.get('action', '')))}</td>"
        f"<td>{html_escape(str(rec.get('target', ''))[:120])}</td>"
        f"<td>{html_escape(ts_label)}</td>"
        f"<td><span class='badge {badge_class}'>{html_escape(status)}</span></td>"
        f"<td class='shot-cell'>{img_html}{dialog_html}</td>"
        "</tr>"
    )


def _render_dialog_block(dialog_text) -> str:
    """네이티브 dialog (alert/confirm 등) 텍스트를 노란 카드로 표시.

    스크린샷 셀 안 (이미지 아래) 에 위치 — Playwright 가 네이티브 dialog 를
    자동 dismiss 해 viewport 에 안 잡히는 한계를 텍스트로 보존.
    """
    if not dialog_text:
        return ""
    return (
        '<div class="dialog-block" title="Playwright 가 자동 dismiss 한 네이티브 dialog">'
        '💬 <strong>dialog</strong>'
        f'<pre>{html_escape(str(dialog_text))}</pre>'
        '</div>'
    )


def _badge_class(status: str) -> str:
    return {"PASS": "ok", "HEALED": "warn", "FAIL": "fail"}.get(status, "skip")


def _summary_html(records: list[dict]) -> str:
    total = len(records)
    passed = sum(1 for r in records if (r.get("status") or "").upper() == "PASS")
    healed = sum(1 for r in records if (r.get("status") or "").upper() == "HEALED")
    failed = sum(1 for r in records if (r.get("status") or "").upper() == "FAIL")
    return (
        '<div class="summary">'
        f'<span>총 {total}</span>'
        f'<span class="ok">PASS {passed}</span>'
        f'<span class="warn">HEALED {healed}</span>'
        f'<span class="fail">FAIL {failed}</span>'
        '</div>'
    )


_LLM_HEADER = (
    "<tr><th>#</th><th>action</th><th>target</th><th>description</th>"
    "<th>status</th><th>screenshot</th></tr>"
)
_CG_HEADER = (
    "<tr><th>#</th><th>action</th><th>target</th><th>elapsed</th>"
    "<th>status</th><th>screenshot</th></tr>"
)


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <title>실행 리포트 — {sid}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #fafafa; color: #1d1d1f; margin: 0; padding: 24px; }}
    h1 {{ margin: 0 0 6px; }}
    .sid {{ font-family: ui-monospace, Menlo, Consolas, monospace; color: #6e6e73;
            font-size: 0.9em; }}
    h2 {{ margin: 24px 0 8px; border-bottom: 1px solid #e5e5e7; padding-bottom: 6px; }}
    section {{ background: #fff; border: 1px solid #e5e5e7; border-radius: 8px;
               padding: 16px 20px; margin: 16px 0; }}
    .meta-table {{ border-collapse: collapse; }}
    .meta-table th {{ text-align: left; color: #6e6e73; font-weight: 500;
                      padding: 4px 12px 4px 0; vertical-align: top; }}
    .meta-table td {{ padding: 4px 0; }}
    .summary {{ display: flex; gap: 12px; margin: 8px 0 12px; font-size: 0.9em; }}
    .summary .ok {{ color: #0a7c2f; }}
    .summary .warn {{ color: #b25b00; }}
    .summary .fail {{ color: #c70000; }}
    .run-table {{ width: 100%; border-collapse: collapse; }}
    .run-table th, .run-table td {{
      border-bottom: 1px solid #f0f0f3; padding: 8px 6px; text-align: left;
      vertical-align: top; font-size: 0.88em; }}
    .run-table th {{ background: #f5f5f7; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
              font-size: 0.85em; font-weight: 500; }}
    .badge.ok {{ background: #d6f3df; color: #0a7c2f; }}
    .badge.warn {{ background: #fde4c2; color: #b25b00; }}
    .badge.fail {{ background: #fbd0d0; color: #c70000; }}
    .badge.skip {{ background: #ececf0; color: #6e6e73; }}
    .shot-cell {{ width: 280px; }}
    .shot {{ max-width: 260px; max-height: 160px; border: 1px solid #e5e5e7;
             border-radius: 4px; cursor: zoom-in; }}
    .shot:hover {{ outline: 2px solid #0071e3; }}
    .dialog-block {{ margin-top: 6px; padding: 6px 8px; background: #fff8e1;
                     border-left: 3px solid #f9c846; border-radius: 4px;
                     font-size: 0.82em; max-width: 260px; }}
    .dialog-block pre {{ margin: 4px 0 0; white-space: pre-wrap; word-break: break-word;
                         font-family: ui-monospace, Menlo, Consolas, monospace;
                         font-size: 0.95em; }}
  </style>
</head>
<body>
  <h1>실행 리포트</h1>
  <div class="sid">session: {sid}</div>
  {meta_section}
  {body}
</body>
</html>
"""
