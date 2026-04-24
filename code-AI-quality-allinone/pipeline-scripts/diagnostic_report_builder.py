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
</style>
"""


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

    return (
        "<div class='issue'>"
        + head + loc + diag_block
        + ("<details><summary>impact_analysis_markdown</summary>"
           f"<pre>{_esc(impact)}</pre></details>" if impact else "")
        + "</div>"
    )


def render(rows: list, title: str = "RAG Diagnostic Report") -> str:
    agg = _aggregate(rows)

    cards = [
        ("total issues", agg["total"], ""),
        ("LLM calls", agg["llm_count"], f"{agg['skip_count']} skip_llm"),
        ("callers bucket filled", f"{agg['bucket_filled_pct']['callers']:.1f}%", "이슈 중 callers 청크 1개 이상 받은 비율"),
        ("tests bucket filled", f"{agg['bucket_filled_pct']['tests']:.1f}%", ""),
        ("others bucket filled", f"{agg['bucket_filled_pct']['others']:.1f}%", ""),
        ("avg citation rate", f"{agg['avg_citation_rate']:.1f}%", "used_items 중 LLM 이 실제 인용한 비율 평균"),
        ("context:empty", f"{agg['context_empty_pct']:.1f}%", f"{agg['context_empty_count']} 이슈에서 C-3 가드 발동"),
        ("retry exhausted", f"{agg['retry_exhausted_pct']:.1f}%", f"{agg['retry_exhausted_count']} 이슈가 3회 재시도 후 빈 응답"),
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

    issues_html = "".join(_render_issue(r) for r in rows)
    body = (
        f"<h1>{_esc(title)}</h1>"
        "<div class='meta'>Sonar 이슈 분석 per-이슈 RAG 진단 + LLM citation 측정.</div>"
        + cards_html
        + dist_html
        + "<h2>이슈별 상세</h2>"
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
