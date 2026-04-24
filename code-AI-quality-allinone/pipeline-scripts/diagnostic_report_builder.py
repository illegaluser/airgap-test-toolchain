#!/usr/bin/env python3
# P1.5 M-1~M-4 — per-이슈 진단 리포트 생성.
#
# dify_sonar_issue_analyzer.py 가 만든 llm_analysis.jsonl 을 읽어 각 Sonar 이슈별로:
#   - context_filter 의 버킷 상태 (callers / tests / others 각각 몇 개)
#   - 실제 LLM 프롬프트에 넘어간 used_items (top-3 × 3 버킷)
#   - LLM 이 impact_analysis_markdown 에서 used_items 를 인용했는지 (citation)
#   - classification, confidence, context:empty 플래그 여부
# 를 한 장의 HTML 로 렌더해 Jenkins publishHTML 탭에 노출한다.
#
# 집계 지표 (상단 카드):
#   - 이슈 총 수 / LLM 호출 수 / skip_llm 수
#   - bucket 채움률 (각 버킷이 최소 1개 청크를 공급한 이슈 비율)
#   - 평균 citation rate = cited_count / total_used
#   - confidence 분포 (high/medium/low)
#   - context:empty 라벨 비율 (C-3 가드 발동 비율)
#
# 이 리포트가 "이슈 분석 품질" 의 **진짜** 지표 — Pre-training Report 와 달리
# LLM 실제 답변 품질과 연결된다.

import argparse
import html as _html
import json
import sys
from pathlib import Path
from collections import Counter


def _esc(s: str) -> str:
    return _html.escape(s or "", quote=True)


def _load_rows(jsonl_path: Path) -> list:
    rows = []
    if not jsonl_path.exists():
        return rows
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _aggregate(rows: list) -> dict:
    total = len(rows)
    llm_rows = [r for r in rows if not r.get("llm_skipped")]
    diag_rows = [r for r in llm_rows if r.get("rag_diagnostic")]
    skip_cnt = total - len(llm_rows)

    bucket_filled_counts = Counter()
    citation_ratios = []
    confidence_counts = Counter()
    context_empty = 0
    classification_counts = Counter()
    retry_exhausted = 0  # M-4 지표
    for r in llm_rows:
        if r.get("retry_exhausted"):
            retry_exhausted += 1
        labels = (r.get("outputs") or {}).get("labels") or []
        conf = ((r.get("outputs") or {}).get("confidence") or "").lower()
        cls = ((r.get("outputs") or {}).get("classification") or "").lower()
        confidence_counts[conf or "(empty)"] += 1
        classification_counts[cls or "(empty)"] += 1
        if "context:empty" in labels:
            context_empty += 1
        d = r.get("rag_diagnostic")
        if not d:
            continue
        for bucket, n in (d.get("used_per_bucket") or {}).items():
            if n > 0:
                bucket_filled_counts[bucket] += 1
        citation = d.get("citation") or {}
        total_used = citation.get("total_used") or 0
        cited = citation.get("cited_count") or 0
        if total_used > 0:
            citation_ratios.append(cited / total_used)

    avg_citation = (sum(citation_ratios) / len(citation_ratios)) if citation_ratios else 0.0

    def pct(n: int, denom: int) -> float:
        return (n * 100.0 / denom) if denom else 0.0

    return {
        "total": total,
        "llm_count": len(llm_rows),
        "diag_count": len(diag_rows),
        "skip_count": skip_cnt,
        "bucket_filled_pct": {
            b: pct(bucket_filled_counts.get(b, 0), len(diag_rows))
            for b in ("callers", "tests", "others")
        },
        "avg_citation_rate": avg_citation * 100.0,
        "confidence_dist": dict(confidence_counts),
        "classification_dist": dict(classification_counts),
        "context_empty_count": context_empty,
        "context_empty_pct": pct(context_empty, len(llm_rows)),
        "retry_exhausted_count": retry_exhausted,
        "retry_exhausted_pct": pct(retry_exhausted, len(llm_rows)),
    }


_CSS = """
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       margin: 24px; color: #24292e; max-width: 1200px; }
h1 { border-bottom: 2px solid #0366d6; padding-bottom: 6px; }
h2 { margin-top: 32px; border-bottom: 1px solid #e1e4e8; padding-bottom: 4px; }
h3 { margin-top: 20px; color: #24292e; }
.meta { color: #586069; font-size: 13px; margin-bottom: 24px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
.card { flex: 1; min-width: 160px; padding: 12px 16px; background: #f6f8fa;
        border: 1px solid #e1e4e8; border-radius: 6px; }
.card .label { color: #586069; font-size: 12px; text-transform: uppercase;
                letter-spacing: 0.5px; }
.card .value { font-size: 22px; font-weight: 600; color: #0366d6; margin-top: 4px; }
.card .sub { color: #586069; font-size: 11px; }
table { border-collapse: collapse; margin: 8px 0 16px; font-size: 12px; width: 100%; }
th, td { border: 1px solid #e1e4e8; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.issue { border: 1px solid #d1d5da; border-radius: 6px; padding: 12px 16px;
         margin-bottom: 12px; background: #fff; }
.issue-header { display: flex; gap: 10px; flex-wrap: wrap; align-items: baseline; }
.issue-key { font-family: monospace; font-weight: 600; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 11px; color: #fff; }
.badge-sev-BLOCKER { background: #b60205; }
.badge-sev-CRITICAL { background: #d93f0b; }
.badge-sev-MAJOR { background: #fbca04; color: #24292e; }
.badge-sev-MINOR { background: #0e8a16; }
.badge-sev-INFO { background: #c5def5; color: #24292e; }
.badge-conf-high { background: #0e8a16; }
.badge-conf-medium { background: #fbca04; color: #24292e; }
.badge-conf-low { background: #b60205; }
.badge-empty { background: #6a737d; }
.empty { color: #b08800; }
.ok { color: #0e8a16; }
code { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 11px;
       background: #f6f8fa; padding: 1px 4px; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 4px;
      padding: 8px; overflow-x: auto; font-size: 11px; line-height: 1.35;
      max-height: 260px; }
.note { color: #586069; font-size: 11px; margin: 4px 0; }

/* --- v2: 비개발자 친화 신호등 요약 블록 --- */
.tldr { border-left: 6px solid #0366d6; background: #f0f7ff;
        padding: 14px 18px; margin: 0 0 20px 0; border-radius: 4px;
        font-size: 15px; line-height: 1.55; }
.tldr strong { color: #0366d6; }
.signals { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
           gap: 12px; margin-bottom: 24px; }
.signal { padding: 14px 18px; border-radius: 6px; border: 1px solid #e1e4e8;
          background: #fff; }
.signal-head { font-size: 16px; font-weight: 600; margin-bottom: 8px;
               display: flex; align-items: center; gap: 10px; }
.signal-dot { font-size: 20px; line-height: 1; }
.signal-body { color: #24292e; font-size: 13px; line-height: 1.55; }
.signal-body ul { margin: 6px 0 0 18px; padding: 0; }
.signal-body li { margin: 2px 0; }
.signal-red    { border-color: #f6b6b6; background: #fff5f5; }
.signal-yellow { border-color: #ead29a; background: #fffdf5; }
.signal-green  { border-color: #a8dbb0; background: #f5fdf7; }
.signal-gray   { border-color: #d1d5da; background: #fafbfc; }

.action-box { background: #fff8dc; border: 1px solid #f0d78a;
              border-left: 6px solid #fbca04; padding: 14px 18px;
              margin-bottom: 24px; border-radius: 4px; font-size: 13px;
              line-height: 1.6; }
.action-box h3 { margin-top: 0; font-size: 14px; color: #735c0f; }
.action-box ul { margin: 6px 0 0 18px; padding: 0; }
.action-box li { margin: 3px 0; }

.issue-verdict { margin: 6px 0 8px 0; padding: 6px 10px; border-radius: 4px;
                 font-size: 12px; background: #fafbfc; border: 1px dashed #d1d5da; }
.issue-verdict.warn { background: #fffdf5; border-color: #f0d78a; color: #735c0f; }
.issue-verdict.ok { background: #f5fdf7; border-color: #a8dbb0; color: #144619; }

details.technical { margin-top: 24px; padding: 8px 12px;
                    background: #f6f8fa; border: 1px solid #e1e4e8;
                    border-radius: 6px; }
details.technical > summary { cursor: pointer; font-weight: 600; color: #0366d6;
                              font-size: 13px; padding: 4px 0; }
details.technical > summary:hover { color: #0a4a96; }
</style>
"""


# ─ v2 — 신호등 임계치 및 판정 ────────────────────────────────────────────
# 세 축으로 "이번 AI 분석이 프로젝트 맥락을 얼마나 반영했는가" 를 평가.
#
# (1) 근거 충분도 (citation_rate) — LLM 이 RAG 로 받은 청크를 답변에 실제로
#     인용한 비율. 낮으면 답변은 "일반 지식" 에 가깝다.
# (2) 참고자료 다양성 (bucket filled) — callers / tests 버킷이 채워진 비율.
#     낮으면 KB 메타데이터가 부실해 "누가 이 함수를 부르는지" 정보 자체가
#     LLM 에 전달되지 못한 것.
# (3) 응답 품질 (retry_exhausted + context_empty) — 기술적으로 답변이
#     나왔는가. 이전 04 "Dify succeeded but outputs empty" 의 재발 방지 지표.
#
# 임계치: 30% / 60% — 도메인 특성상 "30% 인용이면 의미 있음", "60% 면 충분".


def _verdict_citation(rate: float) -> tuple:
    if rate >= 60.0:
        return ("green", "🟢", "충분",
                f"AI 가 프로젝트 코드 맥락을 답변에 적극 반영했습니다 (인용률 {rate:.1f}%).")
    if rate >= 30.0:
        return ("yellow", "🟡", "보통",
                f"AI 가 일부 프로젝트 맥락을 반영했으나 일반 원칙 비중이 큽니다 (인용률 {rate:.1f}%).")
    return ("red", "🔴", "낮음",
            f"AI 가 제공된 프로젝트 코드 맥락을 답변에 거의 반영하지 않았습니다 (인용률 {rate:.1f}%). "
            f"이번 분석은 '일반 지식 기반 조언'에 가깝습니다.")


def _verdict_buckets(callers_pct: float, tests_pct: float) -> tuple:
    avg = (callers_pct + tests_pct) / 2
    if avg >= 60.0:
        return ("green", "🟢", "풍부",
                "관련 호출 코드·테스트 정보가 대부분의 이슈에 제공되었습니다.")
    if avg >= 30.0:
        return ("yellow", "🟡", "보통",
                "관련 호출 코드·테스트 정보가 일부 이슈에만 제공되었습니다.")
    return ("red", "🔴", "부족",
            f"관련 호출 코드(callers {callers_pct:.0f}%) / 테스트(tests {tests_pct:.0f}%) "
            f"정보가 대부분 이슈에 제공되지 않았습니다. "
            f"지식 베이스(RAG) 메타데이터가 부실할 가능성이 높습니다.")


def _verdict_quality(retry_pct: float, context_empty_pct: float) -> tuple:
    if retry_pct <= 5.0 and context_empty_pct <= 10.0:
        return ("green", "🟢", "정상",
                f"AI 가 모든 이슈에 정상 응답했습니다 (재시도 소진 {retry_pct:.1f}%).")
    if retry_pct <= 20.0:
        return ("yellow", "🟡", "보통",
                "일부 이슈에서 AI 재시도 또는 빈 컨텍스트가 발생했습니다.")
    return ("red", "🔴", "불안정",
            f"재시도 소진 {retry_pct:.1f}% — AI 응답 품질이 불안정합니다. "
            f"빌드 로그의 EMPTY-DEBUG 블록을 확인하세요 (README §12.18).")


def _render_executive_summary(agg: dict) -> str:
    """v2 — 비개발자 친화 상단 요약 블록 (TL;DR + 신호등 3축 + 실무 액션)."""
    citation_rate = agg["avg_citation_rate"]
    callers_pct = agg["bucket_filled_pct"]["callers"]
    tests_pct = agg["bucket_filled_pct"]["tests"]
    retry_pct = agg["retry_exhausted_pct"]
    ctx_empty_pct = agg["context_empty_pct"]
    llm_count = agg["llm_count"]

    v_cite = _verdict_citation(citation_rate)
    v_buckets = _verdict_buckets(callers_pct, tests_pct)
    v_qual = _verdict_quality(retry_pct, ctx_empty_pct)

    # 전반 verdict — 가장 나쁜 것 기준 (빨강 > 노랑 > 초록 우선순위)
    order = {"red": 0, "yellow": 1, "green": 2}
    overall = min([v_cite, v_buckets, v_qual], key=lambda v: order[v[0]])
    _overall_color, overall_dot, overall_label, _ = overall

    # TL;DR 본문 — citation 구간별로 맥락화된 한 문장 조언
    tldr_msg = (
        f"분석한 이슈 <strong>{llm_count} 건</strong> 중 AI 가 프로젝트 코드 맥락을 "
        f"답변에 실제로 반영한 정도(인용률)는 <strong>{citation_rate:.1f}%</strong> 입니다. "
    )
    if citation_rate < 30:
        tldr_msg += (
            "이 값이 낮다는 것은 AI 가 '이 프로젝트 고유의 맥락'이 아닌 "
            "'일반적인 코드 리뷰 원칙'에 의존해 답했다는 신호입니다. "
            "중요 이슈 (CRITICAL/MAJOR) 는 반드시 개발자 직접 리뷰가 필요합니다."
        )
    elif citation_rate < 60:
        tldr_msg += (
            "일반 원칙과 프로젝트 맥락이 절반씩 섞인 상태입니다. "
            "요건 추적성 확보가 필요한 이슈는 개발자 재검토를 권장합니다."
        )
    else:
        tldr_msg += (
            "AI 가 프로젝트 코드 맥락을 적극 활용했습니다. "
            "리뷰 시간을 대폭 단축할 수 있는 신뢰도 있는 분석입니다."
        )

    tldr_html = (
        f"<div class='tldr'>{overall_dot} <strong>종합 판정: {overall_label}</strong> — {tldr_msg}</div>"
    )

    # 3축 신호등 카드
    def _sig_card(v, title: str, metric_lines: list) -> str:
        color, dot, label, msg = v
        metrics_html = (
            "<ul>" + "".join(f"<li>{_esc(ml)}</li>" for ml in metric_lines) + "</ul>"
        ) if metric_lines else ""
        return (
            f"<div class='signal signal-{color}'>"
            f"<div class='signal-head'><span class='signal-dot'>{dot}</span>"
            f"<span>{_esc(title)}: {_esc(label)}</span></div>"
            f"<div class='signal-body'>{_esc(msg)}{metrics_html}</div>"
            "</div>"
        )

    signals_html = (
        "<div class='signals'>"
        + _sig_card(v_cite, "AI 분석 근거 충분도",
                    [f"프로젝트 코드 맥락 인용률: {citation_rate:.1f}%"])
        + _sig_card(v_buckets, "참고자료 다양성",
                    [f"관련 호출 코드 제공률: {callers_pct:.1f}%",
                     f"관련 테스트 제공률: {tests_pct:.1f}%"])
        + _sig_card(v_qual, "AI 응답 기술적 품질",
                    [f"재시도 소진: {retry_pct:.1f}%",
                     f"빈 컨텍스트(context:empty): {ctx_empty_pct:.1f}%"])
        + "</div>"
    )

    # 액션 가이드 — verdict 조합에 따라 실무 권장 사항
    actions = []
    if v_cite[0] == "red":
        actions.append(
            "<strong>중요 이슈 (CRITICAL / MAJOR) 는 반드시 개발자가 직접 재검토</strong>하세요. "
            "AI 답변이 프로젝트 특수성보다 일반 원칙에 의존했습니다."
        )
        actions.append(
            "<code>false_positive</code> 로 분류된 이슈는 분류 근거가 약할 수 있으니 신중히 재평가하세요."
        )
    if v_buckets[0] in ("red", "yellow"):
        actions.append(
            "지식 베이스(RAG) 메타데이터 품질 개선이 필요합니다. 특히 "
            "<code>callers</code>, <code>tests</code>, <code>symbol</code> 필드가 대부분 비어 있으면 "
            "02 파이프라인의 pretraining 단계를 확인하세요."
        )
    if v_qual[0] == "red":
        actions.append(
            "04 파이프라인의 빌드 로그에서 <code>[EMPTY-DEBUG]</code> 블록을 찾아 "
            "<code>parse_status</code> 값별 원인을 확인하세요 (README §12.18)."
        )
    if not actions:
        actions.append(
            "모든 품질 지표가 양호합니다. 이번 분석 결과를 신뢰하고 "
            "GitLab 이슈 등록 내용을 기반으로 수정 작업을 진행하세요."
        )

    actions_html = (
        "<div class='action-box'>"
        "<h3>📋 실무 권장 액션</h3>"
        "<ul>" + "".join(f"<li>{a}</li>" for a in actions) + "</ul>"
        "</div>"
    )

    return tldr_html + signals_html + actions_html


def _issue_verdict_text(row: dict) -> tuple:
    """개별 이슈 한 줄 평가 — (css_class, message) 혹은 (None, None)."""
    if row.get("llm_skipped"):
        return ("warn", "ⓘ MINOR/INFO severity 자동 템플릿 — LLM 분석 skip. 수동 리뷰 권장.")
    if row.get("retry_exhausted"):
        return ("warn", "⚠️ AI 가 3 회 재시도에도 정상 응답 실패 — 본문 신뢰도 낮음, 개발자 직접 리뷰 필수.")
    diag = row.get("rag_diagnostic") or {}
    citation = diag.get("citation") or {}
    total_used = citation.get("total_used") or 0
    cited = citation.get("cited_count") or 0
    out = row.get("outputs") or {}
    conf = (out.get("confidence") or "").lower()
    cls = (out.get("classification") or "").lower()

    if total_used == 0:
        return ("warn",
                "⚠️ RAG 가 관련 코드 청크를 제공하지 못함 — 답변이 rule 설명 + 일반 원칙만으로 생성됨.")
    if cited == 0 and conf == "high":
        msg = ("⚠️ AI 가 제공된 프로젝트 코드 맥락을 답변에 인용하지 않았습니다 "
               f"(cited 0/{total_used}). 그럼에도 confidence=high — 근거 약함, ")
        if cls == "false_positive":
            msg += "특히 false_positive 분류는 재검토를 권장합니다."
        else:
            msg += "개발자 재검토 권장."
        return ("warn", msg)
    if cited > 0:
        return ("ok",
                f"✓ AI 가 프로젝트 코드 맥락 {cited}/{total_used} 개를 실제로 답변에 인용했습니다.")
    return (None, None)


def _render_issue(row: dict) -> str:
    key = row.get("sonar_issue_key", "")
    sev = row.get("severity", "")
    rel = row.get("relative_path", "")
    line = row.get("line", "")
    enc = row.get("enclosing_function", "")
    out = row.get("outputs") or {}
    conf = (out.get("confidence") or "").lower()
    cls = (out.get("classification") or "").lower()
    labels = out.get("labels") or []
    impact = out.get("impact_analysis_markdown") or ""
    diag = row.get("rag_diagnostic")

    sev_class = "badge-sev-" + (sev or "INFO").upper()
    conf_class = "badge-conf-" + (conf if conf in ("high", "medium", "low") else "empty")

    head_parts = [
        f"<span class='issue-key'>{_esc(key)}</span>",
        f"<span class='badge {sev_class}'>{_esc(sev)}</span>",
        f"<span class='badge {conf_class}'>conf={_esc(conf)}</span>",
        f"<span>class={_esc(cls)}</span>",
    ]
    if row.get("llm_skipped"):
        head_parts.append("<span class='badge badge-empty'>skip_llm</span>")
    if "context:empty" in labels:
        head_parts.append("<span class='badge badge-empty'>context:empty</span>")
    head = "<div class='issue-header'>" + " ".join(head_parts) + "</div>"
    loc = f"<div class='note'><code>{_esc(rel)}:{_esc(str(line))}</code> — fn: <code>{_esc(enc)}</code></div>"

    # RAG 진단 블록
    if diag:
        buckets = diag.get("buckets") or {}
        used_per = diag.get("used_per_bucket") or {}
        used_items = diag.get("used_items") or []
        citation = diag.get("citation") or {}
        cited_set = {(c.get("path"), c.get("symbol")) for c in (citation.get("cited_items") or [])}

        def row_for(item):
            key_ = (item.get("path"), item.get("symbol"))
            cited = "✓" if key_ in cited_set else ""
            cls = "ok" if cited else ""
            score = item.get("score")
            score_s = f"{score:.2f}" if isinstance(score, (int, float)) else ""
            return (
                f"<tr>"
                f"<td>{_esc(item.get('bucket',''))}</td>"
                f"<td><code>{_esc(item.get('path','?'))}::{_esc(item.get('symbol','?'))}</code></td>"
                f"<td class='num'>{score_s}</td>"
                f"<td class='num {cls}'>{cited}</td>"
                f"</tr>"
            )
        used_table = (
            "<table>"
            "<tr><th>bucket</th><th>symbol</th><th>score</th><th>cited?</th></tr>"
            + "".join(row_for(i) for i in used_items) +
            "</table>"
        ) if used_items else "<p class='empty'>(used_items 없음 — context_filter 가 빈 상태로 LLM 호출)</p>"

        diag_block = (
            "<table>"
            "<tr><th>retrieved</th><th>excl_self</th><th>kept</th><th>used</th>"
            "<th>callers</th><th>tests</th><th>others</th>"
            "<th>cited / used</th></tr>"
            "<tr>"
            f"<td class='num'>{diag.get('retrieved_total',0)}</td>"
            f"<td class='num'>{diag.get('excluded_self',0)}</td>"
            f"<td class='num'>{diag.get('kept_total',0)}</td>"
            f"<td class='num'>{diag.get('used_total',0)}</td>"
            f"<td class='num'>{used_per.get('callers',0)}/{buckets.get('callers',0)}</td>"
            f"<td class='num'>{used_per.get('tests',0)}/{buckets.get('tests',0)}</td>"
            f"<td class='num'>{used_per.get('others',0)}/{buckets.get('others',0)}</td>"
            f"<td class='num'>{citation.get('cited_count',0)} / {citation.get('total_used',0)}</td>"
            "</tr></table>"
            + used_table
        )
    else:
        diag_block = "<p class='empty'>rag_diagnostic 없음 (skip_llm 또는 과거 포맷)</p>"

    # v2 — 개별 이슈 한 줄 평가 (비개발자 친화)
    verdict_cls, verdict_msg = _issue_verdict_text(row)
    verdict_html = (
        f"<div class='issue-verdict {verdict_cls}'>{_esc(verdict_msg)}</div>"
        if verdict_msg else ""
    )

    return (
        "<div class='issue'>"
        + head + loc + verdict_html + diag_block
        + ("<details><summary>impact_analysis_markdown</summary>"
           f"<pre>{_esc(impact)}</pre></details>" if impact else "")
        + "</div>"
    )


def render(rows: list, title: str = "RAG Diagnostic Report") -> str:
    agg = _aggregate(rows)

    # v2 — 비개발자 친화 상단 블록
    exec_summary_html = _render_executive_summary(agg)

    # 기존 상세 카드 — 기술 관점 수치. 이제는 technical details 안으로.
    cards = [
        ("total issues", agg["total"], ""),
        ("LLM calls", agg["llm_count"], f"{agg['skip_count']} skip_llm"),
        ("callers bucket filled", f"{agg['bucket_filled_pct']['callers']:.1f}%",
         "이슈 중 callers 청크 1개 이상 받은 비율"),
        ("tests bucket filled", f"{agg['bucket_filled_pct']['tests']:.1f}%",
         "이슈 중 tests 청크 1개 이상 받은 비율"),
        ("others bucket filled", f"{agg['bucket_filled_pct']['others']:.1f}%",
         "이슈 중 others 청크 1개 이상 받은 비율"),
        ("avg citation rate", f"{agg['avg_citation_rate']:.1f}%",
         "used_items 중 LLM 이 실제 인용한 비율 평균"),
        ("context:empty", f"{agg['context_empty_pct']:.1f}%",
         f"{agg['context_empty_count']} 이슈에서 C-3 가드 발동"),
        ("retry exhausted", f"{agg['retry_exhausted_pct']:.1f}%",
         f"{agg['retry_exhausted_count']} 이슈가 3회 재시도 후 빈 응답"),
    ]
    cards_html = "<div class='cards'>" + "".join(
        f"<div class='card'><div class='label'>{_esc(k)}</div>"
        f"<div class='value'>{_esc(str(v))}</div>"
        f"<div class='sub'>{_esc(sub)}</div></div>"
        for k, v, sub in cards
    ) + "</div>"

    def dist_table(d: dict, header: str) -> str:
        if not d:
            return ""
        total = sum(d.values()) or 1
        rows_ = "".join(
            f"<tr><td>{_esc(str(k))}</td><td class='num'>{v}</td><td class='num'>{v*100/total:.1f}%</td></tr>"
            for k, v in sorted(d.items(), key=lambda x: -x[1])
        )
        return f"<h3>{_esc(header)}</h3><table><tr><th>value</th><th>count</th><th>%</th></tr>{rows_}</table>"

    dist_html = dist_table(agg["confidence_dist"], "confidence 분포") + dist_table(
        agg["classification_dist"], "classification 분포"
    )

    # 기술 지표 + 분포 블록은 접기 섹션으로 — 비개발자는 안 열어도 됨.
    technical_block = (
        "<details class='technical'>"
        "<summary>🔧 기술 상세 지표 (개발자용)</summary>"
        "<div style='margin-top:12px;'>"
        "<p class='note'>상세 수치와 분포. 비개발자는 위 신호등 요약만 읽어도 충분합니다.</p>"
        + cards_html + dist_html
        + "</div></details>"
    )

    issues_html = "".join(_render_issue(r) for r in rows)

    body = (
        f"<h1>{_esc(title)}</h1>"
        "<div class='meta'>이 리포트는 AI 가 정적분석 이슈를 검토하면서 "
        "<strong>'이 프로젝트 고유의 코드 맥락'</strong>을 얼마나 반영했는지 측정합니다. "
        "맨 위 신호등 3 개가 종합 판정입니다.</div>"
        + exec_summary_html
        + technical_block
        + "<h2>📄 이슈별 상세</h2>"
        + "<p class='note'>각 이슈 상단에 한 줄 평가가 표시됩니다. "
          "⚠️ 표시 이슈는 개발자 직접 리뷰가 권장됩니다.</p>"
        + issues_html
    )
    return (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title>{_CSS}</head><body>{body}</body></html>"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG Diagnostic Report (HTML) from llm_analysis.jsonl")
    ap.add_argument("--input", required=True, help="llm_analysis.jsonl 경로")
    ap.add_argument("--output", required=True, help="HTML 출력 경로")
    ap.add_argument("--title", default="RAG Diagnostic Report", help="리포트 제목")
    args = ap.parse_args()

    rows = _load_rows(Path(args.input))
    html_doc = render(rows, title=args.title)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"[diagnostic_report_builder] rows={len(rows)} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
