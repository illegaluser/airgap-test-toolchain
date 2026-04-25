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
    citation_depths = []        # +T2
    partial_citation_count = 0  # P7 신호 — confidence 강등된 이슈 수
    confidence_counts = Counter()
    context_empty = 0
    classification_counts = Counter()
    retry_exhausted = 0  # M-4 지표
    # Phase C F4 — Stage 4 의 핵심 입력. 사전학습 신호 → 답변 활용 인과.
    ts_total_hits_list = []
    ts_endpoint_hits = 0       # 이슈 단위 — endpoint 신호 1회 이상 등장
    ts_decorator_hits = 0
    ts_param_hits = 0
    ts_rag_meta_hits = 0
    ts_any_hit_issues = 0      # total_hits > 0 인 이슈 수
    # used_items 메타 보유율 (Phase C F3 의 has_* 플래그)
    used_with_decorators = 0
    used_with_endpoint = 0
    used_with_doc_struct = 0
    used_total_count = 0
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
        if "partial_citation" in labels:
            partial_citation_count += 1
        d = r.get("rag_diagnostic")
        if not d:
            continue
        for bucket, n in (d.get("used_per_bucket") or {}).items():
            if n > 0:
                bucket_filled_counts[bucket] += 1
        citation = d.get("citation") or {}
        total_used = citation.get("total_used") or 0
        cited = citation.get("cited_count") or 0
        depth = citation.get("citation_depth") or 0
        if total_used > 0:
            citation_ratios.append(cited / total_used)
            citation_depths.append(depth)
        # Phase C F4 — tree_sitter_hits 집계
        ts = d.get("tree_sitter_hits") or {}
        if ts:
            t = ts.get("total_hits", 0) or 0
            ts_total_hits_list.append(t)
            if t > 0:
                ts_any_hit_issues += 1
            if (ts.get("endpoint_hits") or 0) > 0:
                ts_endpoint_hits += 1
            if (ts.get("decorator_hits") or 0) > 0:
                ts_decorator_hits += 1
            if (ts.get("param_hits") or 0) > 0:
                ts_param_hits += 1
            if (ts.get("rag_meta_hits") or 0) > 0:
                ts_rag_meta_hits += 1
        # Phase C F3 — used_items 메타 보유 집계
        for it in d.get("used_items") or []:
            used_total_count += 1
            if it.get("has_decorators"):
                used_with_decorators += 1
            if it.get("has_endpoint"):
                used_with_endpoint += 1
            if it.get("has_doc_struct"):
                used_with_doc_struct += 1

    avg_citation = (sum(citation_ratios) / len(citation_ratios)) if citation_ratios else 0.0
    avg_depth = (sum(citation_depths) / len(citation_depths)) if citation_depths else 0.0
    avg_ts_hits = (
        sum(ts_total_hits_list) / len(ts_total_hits_list)
    ) if ts_total_hits_list else 0.0

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
        "avg_citation_depth": avg_depth,  # +T2
        "partial_citation_count": partial_citation_count,
        "partial_citation_pct": pct(partial_citation_count, len(llm_rows)),
        "confidence_dist": dict(confidence_counts),
        "classification_dist": dict(classification_counts),
        "context_empty_count": context_empty,
        "context_empty_pct": pct(context_empty, len(llm_rows)),
        "retry_exhausted_count": retry_exhausted,
        "retry_exhausted_pct": pct(retry_exhausted, len(llm_rows)),
        # Phase C F4 — Stage 4 narrative 데이터
        "ts_avg_hits": avg_ts_hits,
        "ts_any_hit_issues": ts_any_hit_issues,
        "ts_any_hit_pct": pct(ts_any_hit_issues, len(diag_rows)),
        "ts_endpoint_issue_count": ts_endpoint_hits,
        "ts_decorator_issue_count": ts_decorator_hits,
        "ts_param_issue_count": ts_param_hits,
        "ts_rag_meta_issue_count": ts_rag_meta_hits,
        # Phase C F3 — used_items 메타 보유율
        "used_items_total": used_total_count,
        "used_with_decorators_pct": pct(used_with_decorators, used_total_count),
        "used_with_endpoint_pct": pct(used_with_endpoint, used_total_count),
        "used_with_doc_struct_pct": pct(used_with_doc_struct, used_total_count),
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
.signal-gray   { border-color: #c7cdd5; background: #f5f6f8; }

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

/* --- Phase C: 4-stage 학습진단 sliding ladder --- */
.stages { display: flex; flex-direction: column; gap: 12px;
          margin: 12px 0 24px 0; }
.stage { border-left: 6px solid #0366d6; background: #fff;
         border: 1px solid #e1e4e8; border-radius: 6px;
         padding: 14px 18px; }
.stage-green  { border-left-color: #2ea043; background: #f7fef9; }
.stage-yellow { border-left-color: #d4a017; background: #fffdf5; }
.stage-red    { border-left-color: #cf222e; background: #fff5f5; }
.stage-gray   { border-left-color: #8b949e; background: #f5f6f8; }
.stage-head { display: flex; align-items: center; gap: 12px;
              flex-wrap: wrap; margin-bottom: 6px; }
.stage-num { font-size: 11px; font-weight: 700; color: #586069;
             text-transform: uppercase; letter-spacing: 0.5px;
             background: #e1e4e8; padding: 2px 8px; border-radius: 10px; }
.stage-title { font-size: 16px; font-weight: 600; color: #24292e; flex: 1; }
.stage-dot { font-size: 18px; }
.stage-label { font-size: 13px; font-weight: 600; color: #586069; }
.stage-msg { color: #24292e; font-size: 13px; line-height: 1.55;
             margin: 4px 0 12px 0; }
.stage-body { margin-top: 6px; }
.mini-cards { display: grid;
              grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
              gap: 8px; }
.mini-card { background: #fff; border: 1px solid #d8dee4;
             border-radius: 4px; padding: 10px 12px; }
.mini-v { font-size: 22px; font-weight: 600; color: #0366d6;
          line-height: 1.1; }
.mini-l { font-size: 12px; color: #24292e; margin-top: 3px;
          font-weight: 500; }
.mini-sub { font-size: 11px; color: #586069; margin-top: 4px;
            line-height: 1.4; }
.mini-h { font-size: 12px; font-weight: 600; color: #586069;
          margin: 0 0 6px 0; text-transform: uppercase;
          letter-spacing: 0.3px; }
.depth-bar { height: 4px; background: #eef0f3; border-radius: 2px;
             margin-top: 6px; overflow: hidden; }
.depth-fill { display: block; height: 100%; background: #2ea043; }
.lang-bars { margin-top: 12px; padding: 10px 12px; background: #f6f8fa;
             border: 1px solid #e1e4e8; border-radius: 4px; }
.lang-row { display: flex; align-items: center; gap: 10px;
            margin: 3px 0; font-size: 12px; }
.lang-name { width: 72px; color: #24292e; font-family: 'SF Mono', Monaco, monospace; }
.lang-bar { flex: 1; height: 8px; background: #eef0f3; border-radius: 4px;
            overflow: hidden; }
.lang-fill { display: block; height: 100%; background: #0366d6; }
.lang-pct { width: 76px; text-align: right; color: #586069; }
.lang-sub { font-size: 10px; color: #8b949e; }
.warn-box { margin-top: 10px; padding: 10px 12px; background: #fffdf5;
            border: 1px solid #e6c878; border-radius: 4px; }
.example-box { margin-top: 10px; padding: 6px 10px;
               background: #f6f8fa; border: 1px solid #e1e4e8;
               border-radius: 4px; font-size: 12px; }
.example-box > summary { cursor: pointer; color: #0366d6;
                          padding: 2px 0; }

/* --- Phase C F3: per-issue used_items 메타 컬럼 아이콘 --- */
.meta-icons { display: inline-block; font-size: 11px;
              letter-spacing: 1px; }
.meta-icons span { opacity: 0.85; }
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


_INSUFFICIENT_SAMPLE_THRESHOLD = 3  # citation 측정에 필요한 최소 effective sample


def _verdict_citation(rate: float, effective_n: int) -> tuple:
    """citation 신뢰도 — effective_n (used_items 가 있는 이슈 수) < 3 이면 회색 처리.

    rate 자체는 100% 여도 분모가 1 이면 통계적으로 무의미. 실제로 ttc-sample-app
    4 이슈 중 3 이슈가 context:empty 인 상황에서 citation 100% 가 🟢 으로 표시되어
    "신뢰도 있는 분석" 이라는 잘못된 인상을 주는 문제 (관측). gray 신호등으로
    "측정 불충분" 임을 명확히 한다.
    """
    if effective_n < _INSUFFICIENT_SAMPLE_THRESHOLD:
        return ("gray", "⚪", "측정 불충분",
                f"인용률 {rate:.1f}% 는 measurable 이슈 {effective_n} 건 기준으로만 산출돼 "
                f"통계적으로 의미가 약합니다 (최소 {_INSUFFICIENT_SAMPLE_THRESHOLD} 건 필요). "
                f"먼저 RAG 가 빈 결과를 돌려준 이슈 (참고자료 다양성 신호 참조) 부터 "
                f"개선해야 인용률 자체를 신뢰할 수 있습니다.")
    if rate >= 60.0:
        return ("green", "🟢", "충분",
                f"AI 가 프로젝트 코드 맥락을 답변에 적극 반영했습니다 (인용률 {rate:.1f}%, n={effective_n}).")
    if rate >= 30.0:
        return ("yellow", "🟡", "보통",
                f"AI 가 일부 프로젝트 맥락을 반영했으나 일반 원칙 비중이 큽니다 (인용률 {rate:.1f}%, n={effective_n}).")
    return ("red", "🔴", "낮음",
            f"AI 가 제공된 프로젝트 코드 맥락을 답변에 거의 반영하지 않았습니다 "
            f"(인용률 {rate:.1f}%, n={effective_n}). 이번 분석은 '일반 지식 기반 조언'에 가깝습니다.")


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
    """기술적 품질 — retry 와 context_empty 모두 강하게 가중.

    이전 임계치 (retry≤5 AND ctx≤10 → 🟢, retry≤20 → 🟡) 는 context_empty 75%
    같은 명백한 이상치도 🟡 보통 으로 표시. 수정: retry / context_empty 둘 중
    하나라도 임계 초과면 단계적으로 강등.
    """
    # 빨강 — 둘 중 하나가 압도적으로 나쁨
    if retry_pct > 20.0 or context_empty_pct > 50.0:
        return ("red", "🔴", "불안정",
                f"재시도 소진 {retry_pct:.1f}% / 빈 컨텍스트 {context_empty_pct:.1f}% — "
                f"AI 응답 품질이 불안정합니다. RAG retrieval 또는 EMPTY-DEBUG 점검 필요 (README §12.18).")
    # 노랑 — 일부 문제
    if retry_pct > 5.0 or context_empty_pct > 25.0:
        return ("yellow", "🟡", "보통",
                f"일부 이슈에서 AI 재시도({retry_pct:.1f}%) 또는 빈 컨텍스트({context_empty_pct:.1f}%) 가 발생했습니다.")
    # 초록 — 모두 양호
    return ("green", "🟢", "정상",
            f"AI 가 모든 이슈에 정상 응답했습니다 "
            f"(재시도 {retry_pct:.1f}% / 빈 컨텍스트 {context_empty_pct:.1f}%).")


def _render_executive_summary(agg: dict) -> str:
    """v2 — 비개발자 친화 상단 요약 블록 (TL;DR + 신호등 3축 + 실무 액션)."""
    citation_rate = agg["avg_citation_rate"]
    callers_pct = agg["bucket_filled_pct"]["callers"]
    tests_pct = agg["bucket_filled_pct"]["tests"]
    retry_pct = agg["retry_exhausted_pct"]
    ctx_empty_pct = agg["context_empty_pct"]
    llm_count = agg["llm_count"]
    # citation 분모: context:empty 도 retry_exhausted 도 아닌, 즉 RAG 청크가
    # 실제로 LLM 에 전달된 이슈 수. used_items 가 있는 이슈만 인용률 측정 의미.
    effective_n = max(0, llm_count - agg.get("context_empty_count", 0)
                                   - agg.get("retry_exhausted_count", 0))

    v_cite = _verdict_citation(citation_rate, effective_n)
    v_buckets = _verdict_buckets(callers_pct, tests_pct)
    v_qual = _verdict_quality(retry_pct, ctx_empty_pct)

    # 전반 verdict — 가장 나쁜 것 기준. gray (측정 불충분) 도 worst 후보로 포함.
    order = {"red": 0, "gray": 1, "yellow": 2, "green": 3}
    overall = min([v_cite, v_buckets, v_qual], key=lambda v: order.get(v[0], 99))
    overall_color, overall_dot, overall_label, _ = overall

    # TL;DR 본문 — overall verdict 색상 + 어느 축이 worst 인지 명시.
    # 이전 결함: red 메시지가 "RAG 검색 또는 인용 품질에 심각 문제" 로 두 축을
    # 섞어 표현 → 인용 65% 인데 "인용 품질 문제" 라는 모순. 이번엔 worst 가
    # 어떤 축인지 (citation / buckets / quality) 구체 명시.
    axis_names = {
        v_cite: "AI 분석 근거 충분도 (인용)",
        v_buckets: "참고자료 다양성 (RAG 검색)",
        v_qual: "AI 응답 기술적 품질",
    }
    worst_axis = axis_names[overall]

    base = (
        f"분석한 이슈 <strong>{llm_count} 건</strong> 중 RAG 가 의미있는 컨텍스트를 제공해 "
        f"인용률을 측정할 수 있었던 이슈는 <strong>{effective_n} 건</strong> 입니다 "
        f"(평균 인용률 {citation_rate:.1f}%, 빈 컨텍스트 {ctx_empty_pct:.1f}%). "
    )
    if overall_color == "red":
        # worst 축이 무엇이냐에 따라 메시지 구체화
        if overall is v_cite:
            axis_msg = (
                "AI 가 받은 RAG 자료를 답변에 거의 인용하지 않아 분석이 "
                "프로젝트 맥락보다 일반 원칙에 의존했습니다."
            )
        elif overall is v_buckets:
            # 참고자료 다양성이 worst — 인용/품질은 정상일 수 있음을 명시
            cite_note = (
                f"인용 자체는 평균 {citation_rate:.1f}% 로 정상이지만, "
                if v_cite[0] in ("green", "yellow") else ""
            )
            axis_msg = (
                f"{cite_note}RAG 검색 단계에서 '이 함수를 호출하는 코드'·'관련 테스트' "
                f"청크를 충분히 회수하지 못했습니다 (callers {callers_pct:.0f}%, "
                f"tests {tests_pct:.0f}%). 답변이 같은 파일·유사 코드 위주로 좁아질 위험이 "
                "있어 호출 관계·요건 traceability 가 중요한 이슈는 개발자 재검토를 권장합니다."
            )
        else:  # v_qual
            axis_msg = (
                f"AI 응답이 기술적으로 불안정합니다 (재시도 {retry_pct:.0f}%, "
                f"빈 컨텍스트 {ctx_empty_pct:.0f}%). RAG retrieval 또는 LLM 응답 파이프라인 "
                "점검이 필요합니다 (README §12.18)."
            )
        tldr_msg = base + (
            f"<strong>가장 심각한 축: {worst_axis}.</strong> {axis_msg} "
            f"CRITICAL/MAJOR 이슈는 개발자 직접 리뷰를 권장합니다."
        )
    elif overall_color == "gray":
        tldr_msg = base + (
            "측정에 충분한 표본이 모이지 않아 신호등 자체를 신뢰하기 어렵습니다. "
            "먼저 RAG 검색이 비어있는 이슈를 줄여야 인용률 지표가 의미를 가집니다 "
            f"(현재 measurable n={effective_n}, 권장 ≥{_INSUFFICIENT_SAMPLE_THRESHOLD})."
        )
    elif overall_color == "yellow":
        tldr_msg = base + (
            f"전반적으로 작동하지만 <strong>{worst_axis}</strong> 축에 품질 저하가 있습니다. "
            "아래 해당 신호등의 상세 권장 액션을 참고하세요."
        )
    else:  # green
        tldr_msg = base + (
            "모든 품질 축이 양호합니다. 이번 분석은 신뢰도 있게 활용 가능합니다."
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
    if v_cite[0] == "gray":
        actions.append(
            f"<strong>RAG 가 빈 결과를 돌려준 이슈가 {ctx_empty_pct:.0f}%</strong> 로 많아 인용률 측정 자체가 "
            "의미를 잃었습니다. retrieval threshold (현재 0.35) 완화, kb_query 변형, 또는 self-exclusion "
            "범위를 path 단위에서 symbol 단위로 좁히는 방향을 우선 검토하세요."
        )
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
            f"빈 컨텍스트(context:empty) {ctx_empty_pct:.0f}% — RAG retrieval 단계에서 "
            "self-exclusion (issue_file_path 와 동일 path 청크 제거) 이 너무 공격적인지 확인하세요. "
            "stub 함수 위주의 작은 프로젝트에서는 동일 파일의 sibling 함수까지 모두 제외되어 "
            "kept=0 이 되는 경향이 있습니다."
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
    """개별 이슈 한 줄 평가 — (css_class, message) 혹은 (None, None).

    +T2 보강: citation_depth (impact_md 의 distinct backtick 수) 도 함께 표시해
    "구체적 인용" 정도를 한눈에 보이게 한다. 단순 1회 인용 vs 깊은 다중 인용을
    구분할 수 있어야 RAG 효용을 정직히 측정.
    """
    if row.get("llm_skipped"):
        return ("warn", "ⓘ MINOR/INFO severity 자동 템플릿 — LLM 분석 skip. 수동 리뷰 권장.")
    if row.get("retry_exhausted"):
        return ("warn", "⚠️ AI 가 3 회 재시도에도 정상 응답 실패 — 본문 신뢰도 낮음, 개발자 직접 리뷰 필수.")
    diag = row.get("rag_diagnostic") or {}
    citation = diag.get("citation") or {}
    total_used = citation.get("total_used") or 0
    cited = citation.get("cited_count") or 0
    depth = citation.get("citation_depth") or 0
    out = row.get("outputs") or {}
    conf = (out.get("confidence") or "").lower()
    cls = (out.get("classification") or "").lower()
    labels = out.get("labels") or []
    is_partial = "partial_citation" in labels

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
        depth_note = f" · 구체 인용 {depth} 개" if depth else ""
        if is_partial:
            return ("warn",
                    f"⚠️ 부분 인용 — AI 가 RAG 청크 {cited}/{total_used} 개만 답변에 반영"
                    f"{depth_note}. confidence 는 medium 으로 자동 강등됨 (P7).")
        return ("ok",
                f"✓ AI 가 프로젝트 코드 맥락 {cited}/{total_used} 개를 실제로 답변에 인용했습니다"
                f"{depth_note}.")
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
            # Phase C F3 — tree-sitter 메타 보유 아이콘
            icons = []
            if item.get("has_decorators"):
                icons.append("<span title='decorators 보유'>🛡️</span>")
            if item.get("has_endpoint"):
                icons.append("<span title='HTTP route 보유'>🌐</span>")
            if item.get("has_doc_struct"):
                icons.append("<span title='docstring 구조 (params/returns/throws) 보유'>📝</span>")
            meta_html = (
                "<span class='meta-icons'>" + "".join(icons) + "</span>"
            ) if icons else ""
            return (
                f"<tr>"
                f"<td>{_esc(item.get('bucket',''))}</td>"
                f"<td><code>{_esc(item.get('path','?'))}::{_esc(item.get('symbol','?'))}</code></td>"
                f"<td class='num'>{score_s}</td>"
                f"<td class='num {cls}'>{cited}</td>"
                f"<td>{meta_html}</td>"
                f"</tr>"
            )
        used_table = (
            "<table>"
            "<tr><th>bucket</th><th>symbol</th><th>score</th><th>cited?</th>"
            "<th title='tree-sitter 메타 보유: 🛡️ decorator · 🌐 endpoint · 📝 docstring'>meta</th></tr>"
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


def _collect_kb_intelligence_stats(kb_dir) -> dict:
    """사전학습 인텔리전스 통계 수집. Phase A 의 _kb_intelligence.json 사이드카가
    있으면 우선 읽고, 없으면 JSONL 직접 스캔으로 fallback (backward-compat).

    PM 시각: "AI 가 우리 프로젝트에서 무엇을 배웠는가" 4-stage narrative 데이터.
    """
    if not kb_dir or not Path(kb_dir).exists():
        return {}

    # Phase A 사이드카 우선
    sidecar = Path(kb_dir) / "_kb_intelligence.json"
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            # 요약 평면 필드도 함께 반환 — 기존 코드가 참조하는 키와 호환.
            depth = data.get("depth", {}) or {}
            scope = data.get("scope", {}) or {}
            quality = data.get("quality", {}) or {}
            data["_flat"] = {
                "total_files": scope.get("files_analyzed", 0),
                "total_chunks": depth.get("total_chunks", 0),
                "lang_breakdown": depth.get("lang_breakdown", {}),
                "callers_links": depth.get("callers_links_total", 0),
                "test_links": depth.get("test_links_total", 0),
                "decorators_count": depth.get("decorators_count", 0),
                "endpoints_count": depth.get("endpoints_count", 0),
                "endpoints_examples": depth.get("endpoints_examples", []),
                "type_chunks_count": depth.get("type_chunks_count", 0),
                "test_chunks_count": depth.get("test_chunks_count", 0),
                "docstring_count": depth.get("docstring_count", 0),
            }
            return data
        except Exception:
            pass  # fallback

    # Fallback: JSONL 직접 스캔 (사이드카 부재 — 구버전 KB)
    from collections import Counter as _Counter
    flat = {
        "total_files": 0, "total_chunks": 0, "lang_breakdown": {},
        "callers_links": 0, "test_links": 0,
        "decorators_count": 0, "endpoints_count": 0, "endpoints_examples": [],
        "type_chunks_count": 0, "test_chunks_count": 0, "docstring_count": 0,
    }
    lang_counter = _Counter()
    endpoints_seen = []
    for jsonl_path in sorted(Path(kb_dir).glob("*.jsonl")):
        flat["total_files"] += 1
        try:
            with jsonl_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ch = json.loads(line)
                    except Exception:
                        continue
                    flat["total_chunks"] += 1
                    lang_counter[ch.get("lang") or "?"] += 1
                    flat["callers_links"] += len(ch.get("callers") or [])
                    flat["test_links"] += len(ch.get("test_paths") or [])
                    if ch.get("decorators"):
                        flat["decorators_count"] += 1
                    ep = (ch.get("endpoint") or "").strip()
                    if ep:
                        flat["endpoints_count"] += 1
                        if len(endpoints_seen) < 5:
                            endpoints_seen.append({
                                "endpoint": ep,
                                "path": ch.get("path", "?"),
                                "symbol": ch.get("symbol", "?"),
                            })
                    if (ch.get("kind") or "") in ("type", "enum", "interface"):
                        flat["type_chunks_count"] += 1
                    if ch.get("is_test"):
                        flat["test_chunks_count"] += 1
                    if (ch.get("doc") or "").strip():
                        flat["docstring_count"] += 1
        except Exception:
            continue
    flat["lang_breakdown"] = dict(lang_counter)
    flat["endpoints_examples"] = endpoints_seen
    return {"_flat": flat, "schema_version": 0}


# ─ Phase C — 4-stage 학습진단 verdict 함수들 ────────────────────────────────
# 사전학습이 PM 에게 의미 있도록 4단계 narrative 신호등으로 변환.
# Stage 1 (범위) / Stage 2 (깊이) / Stage 3 (품질) / Stage 4 (영향).
# 임계치는 작은 sample-app ~ 큰 nodegoat 둘 다 의미있는 verdict 가 나오도록
# 휴리스틱하게 잡음. 절대치보다 비율 기반 (parser_success_rate, callees_present_rate 등).

def _verdict_learn_scope(scope: dict, depth: dict) -> tuple:
    """Stage 1 — 학습 범위. 분석 대상 파일을 충분히 봤는가."""
    files_seen = scope.get("files_seen_total", 0) or 0
    files_analyzed = scope.get("files_analyzed", 0) or 0
    parser_failed = scope.get("parser_failed", 0) or 0
    n_chunks = depth.get("total_chunks", 0) or 0
    if n_chunks == 0:
        return ("red", "🔴", "분석 실패",
                "AI 가 학습할 코드 청크를 하나도 추출하지 못했습니다. "
                "지원 언어 (Python/Java/JS/TS/Go/Rust 등) 의 파일이 레포에 있는지 확인하세요.")
    # parser 성공률 — 지원 확장자였는데 청크 0 인 비율이 높으면 경고.
    denom = files_analyzed + parser_failed
    success_rate = (files_analyzed * 100.0 / denom) if denom else 0.0
    if success_rate < 70.0:
        return ("yellow", "🟡", "부분 학습",
                f"지원 언어 파일 중 {success_rate:.0f}% 만 청크 추출 성공 "
                f"(분석 {files_analyzed} / 파서 실패·빈파일 {parser_failed}). "
                "일부 파일은 정의 패턴이 특수해 분해되지 않았을 수 있습니다.")
    return ("green", "🟢", "충분",
            f"{files_analyzed} 개 파일에서 {n_chunks:,} 개의 코드 조각 (함수·클래스·타입) 을 "
            f"학습했습니다. 파서 성공률 {success_rate:.0f}%.")


def _verdict_learn_depth(depth: dict) -> tuple:
    """Stage 2 — 학습 깊이. 호출 관계/라우트/테스트 연결이 풍부한가."""
    n_chunks = depth.get("total_chunks", 0) or 0
    if n_chunks == 0:
        return ("gray", "⚪", "측정 불가", "청크가 없어 깊이 측정 불가.")
    callees_rate = depth.get("callees_present_rate_pct", 0) or 0.0
    test_link_rate = depth.get("test_link_rate_pct", 0) or 0.0
    endpoints = depth.get("endpoints_count", 0) or 0
    types = depth.get("type_chunks_count", 0) or 0
    docstr_rate = (
        (depth.get("docstring_count", 0) or 0) * 100.0 / n_chunks
    ) if n_chunks else 0.0

    # 4개 신호 중 몇 개가 양호한가 (각 신호 임계: callees ≥40%, test ≥20%,
    # endpoint ≥1, types ≥1, docstring ≥30%).
    healthy = 0
    if callees_rate >= 40.0:
        healthy += 1
    if test_link_rate >= 20.0:
        healthy += 1
    if endpoints > 0:
        healthy += 1
    if types > 0 or docstr_rate >= 30.0:
        healthy += 1

    if healthy >= 3:
        return ("green", "🟢", "풍부",
                f"호출 관계·라우트·테스트 연결·도메인 모델 4개 신호 중 {healthy} 개가 충실하게 추출됐습니다.")
    if healthy >= 2:
        return ("yellow", "🟡", "보통",
                f"4개 학습 신호 중 {healthy} 개만 충실. 일부 신호가 약하면 "
                "이슈 분석 시 caller/test 청크 회수가 떨어질 수 있습니다.")
    return ("red", "🔴", "얕음",
            f"4개 학습 신호 중 {healthy} 개만 충실. AI 가 함수 간 관계나 "
            "외부 진입점을 거의 학습하지 못해 답변이 단편적일 수 있습니다.")


def _verdict_learn_quality(quality: dict, scope: dict) -> tuple:
    """Stage 3 — 학습 품질. 노이즈 잘 걸러졌고 언어 비대칭 없나."""
    asymm = quality.get("lang_callers_asymmetry", []) or []
    minified = scope.get("skipped_minified_files", 0) or 0
    excluded = scope.get("skipped_excluded_dirs", 0) or 0
    parser_rate = quality.get("parser_success_rate_pct", 0) or 0.0
    if parser_rate < 50.0:
        return ("red", "🔴", "낮음",
                f"파서 성공률 {parser_rate:.0f}% — 학습 결과 신뢰도가 낮습니다. "
                "특수 문법 (script tag JS, kotlin DSL 등) 이 많은 레포일 수 있습니다.")
    if asymm:
        lang_msg = ", ".join(f"{a['lang']} ({a['chunks']}개)" for a in asymm[:3])
        return ("yellow", "🟡", "비대칭",
                f"일부 언어 ({lang_msg}) 는 청크는 있으나 호출 관계가 추출되지 않았습니다. "
                "해당 영역 이슈는 caller 정보가 약할 수 있습니다.")
    if minified or excluded:
        return ("green", "🟢", "정상",
                f"노이즈 자동 제외 정상 (벤더·minified {minified} 개, 제외 디렉토리 파일 {excluded} 개). "
                f"파서 성공률 {parser_rate:.0f}%.")
    return ("green", "🟢", "정상",
            f"파서 성공률 {parser_rate:.0f}% · 언어별 비대칭 없음.")


def _verdict_learn_impact(agg: dict) -> tuple:
    """Stage 4 — 학습 영향. 사전학습 신호가 답변에 실제로 반영됐나.

    핵심 지표: ts_any_hit_pct = 답변 중 endpoint/decorator/param/RAG-meta 신호가
    1번이라도 등장한 비율. 이게 높을수록 "사전학습 → 답변 활용" 인과가 살아있음.
    """
    diag_n = agg.get("diag_count", 0) or 0
    if diag_n == 0:
        return ("gray", "⚪", "측정 불충분",
                "RAG 진단 데이터가 있는 이슈가 없습니다 — Stage 4 측정 불가.")
    any_hit_pct = agg.get("ts_any_hit_pct", 0.0) or 0.0
    avg_hits = agg.get("ts_avg_hits", 0.0) or 0.0
    if any_hit_pct >= 50.0:
        return ("green", "🟢", "잘 활용됨",
                f"AI 답변의 {any_hit_pct:.0f}% 가 우리 프로젝트의 라우트·데코레이터·매개변수명 등 "
                f"정적 메타를 직접 인용했습니다 (이슈당 평균 {avg_hits:.1f} 개 신호).")
    if any_hit_pct >= 20.0:
        return ("yellow", "🟡", "부분 활용",
                f"AI 답변의 {any_hit_pct:.0f}% 만 정적 메타를 인용. 사전학습은 풍부했으나 "
                "이슈 분석 단계에서 일부만 답변에 반영됐습니다.")
    return ("red", "🔴", "거의 미반영",
            f"AI 답변의 {any_hit_pct:.0f}% 만 정적 메타 인용. 사전학습 결과가 답변에 거의 전달되지 "
            "않았습니다 — RAG retrieval 또는 LLM 프롬프트 점검 필요.")


def _render_kb_intelligence(kb_intel: dict, agg: dict) -> str:
    """Phase C — 4-stage 학습진단 narrative 섹션.

    PM 이 위→아래 사다리로 읽으면서 (1) 범위 → (2) 깊이 → (3) 품질 → (4) 영향
    인과를 체감. 기존 8개 카드 나열 대신 각 stage 가 신호등 1개 + 한 줄 메시지
    + 핵심 mini-card 묶음.
    """
    if not kb_intel:
        return ""
    flat = kb_intel.get("_flat", {})
    if not flat.get("total_chunks"):
        return ""
    scope = kb_intel.get("scope", {}) or {}
    depth = kb_intel.get("depth", {}) or {}
    quality = kb_intel.get("quality", {}) or {}

    v_scope = _verdict_learn_scope(scope, depth)
    v_depth = _verdict_learn_depth(depth)
    v_quality = _verdict_learn_quality(quality, scope)
    v_impact = _verdict_learn_impact(agg)

    def _stage(idx: int, title: str, verdict: tuple, body_html: str) -> str:
        color, dot, label, msg = verdict
        return (
            f"<div class='stage stage-{color}'>"
            f"<div class='stage-head'>"
            f"<span class='stage-num'>Stage {idx}</span>"
            f"<span class='stage-title'>{_esc(title)}</span>"
            f"<span class='stage-dot'>{dot}</span>"
            f"<span class='stage-label'>{_esc(label)}</span>"
            "</div>"
            f"<div class='stage-msg'>{_esc(msg)}</div>"
            f"<div class='stage-body'>{body_html}</div>"
            "</div>"
        )

    # ─ Stage 1 body — 학습 범위 ───────────────────────────────────────────
    files_seen = scope.get("files_seen_total", 0) or 0
    files_analyzed = scope.get("files_analyzed", 0) or 0
    n_chunks = depth.get("total_chunks", 0) or 0
    lang_bd = depth.get("lang_breakdown", {}) or {}
    lang_pairs = sorted(lang_bd.items(), key=lambda x: -x[1])
    # 언어별 막대 — 비율 기준
    lang_bar_html = ""
    if lang_pairs:
        bars = []
        for lang, n in lang_pairs[:6]:
            pct = (n * 100.0 / n_chunks) if n_chunks else 0.0
            bars.append(
                f"<div class='lang-row'><span class='lang-name'>{_esc(lang)}</span>"
                f"<span class='lang-bar'><span class='lang-fill' style='width:{pct:.1f}%'></span></span>"
                f"<span class='lang-pct'>{n} <span class='lang-sub'>({pct:.0f}%)</span></span></div>"
            )
        lang_bar_html = (
            "<div class='lang-bars'><div class='mini-h'>언어별 코드 조각 분포</div>"
            + "".join(bars) + "</div>"
        )
    skip_lines = []
    sm = scope.get("skipped_minified_files", 0) or 0
    sd = scope.get("skipped_duplicate_chunks", 0) or 0
    se = scope.get("skipped_excluded_dirs", 0) or 0
    se_ext = scope.get("skipped_unsupported_ext", 0) or 0
    if sm or sd:
        skip_lines.append(f"노이즈 자동 제외 — 미니파이/벤더 파일 {sm}, 중복 청크 {sd}")
    if se or se_ext:
        skip_lines.append(f"분석 대상 외 — 제외 디렉토리 {se} 파일, 비지원 확장자 {se_ext} 파일")
    skip_html = (
        "<div class='note' style='margin-top:8px;'>"
        + " · ".join(_esc(l) for l in skip_lines) + "</div>"
    ) if skip_lines else ""

    # Phase D Fix C — parser_failed 가시화: 어떤 파일이 실패했는지 펼침 박스로
    # 노출. PM/운영자가 "왜 청크가 적은지" 즉시 추적 가능.
    pf_files = scope.get("parser_failed_files") or []
    pf_count = scope.get("parser_failed", 0) or 0
    pf_html = ""
    if pf_files:
        rows = "".join(f"<li><code>{_esc(p)}</code></li>" for p in pf_files[:50])
        more = (
            f"<div class='note' style='margin-top:6px;'>+{pf_count - len(pf_files)} 파일 더 (cap 50)</div>"
            if pf_count > len(pf_files) else ""
        )
        pf_html = (
            "<details class='example-box' style='margin-top:10px;'>"
            f"<summary>⚠️ 청크 추출 실패 / 빈파일 {pf_count}개 — 어떤 파일인지 보기 ▾</summary>"
            f"<ul style='margin:6px 0 0 18px;padding:0;font-size:11px;line-height:1.5;'>{rows}</ul>"
            + more
            + "<div class='note' style='margin-top:8px;'>가능한 원인: "
              "(1) 함수/클래스 정의 부재 (라이센스 헤더만, 빈 파일), "
              "(2) tree-sitter 파서가 파일의 특수 문법 구조 미지원, "
              "(3) 익명 모듈 패턴 (`module.exports = () => {}` 만 있고 명명된 함수 없음). "
              "(3) 의 경우 사이클 3 Fix A 가 부모 컨텍스트에서 이름을 부여하므로 일부 회복됨."
            "</div>"
            "</details>"
        )

    stage1_body = (
        "<div class='mini-cards'>"
        f"<div class='mini-card'><div class='mini-v'>{files_seen:,}</div>"
        "<div class='mini-l'>전체 파일 수</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{files_analyzed:,}</div>"
        "<div class='mini-l'>학습 가능한 파일 (지원 언어)</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{n_chunks:,}</div>"
        "<div class='mini-l'>학습된 코드 조각 (함수·클래스·타입)</div></div>"
        "</div>"
        + lang_bar_html
        + skip_html
        + pf_html
    )

    # ─ Stage 2 body — 학습 깊이 ───────────────────────────────────────────
    callees_rate = depth.get("callees_present_rate_pct", 0) or 0.0
    test_link_rate = depth.get("test_link_rate_pct", 0) or 0.0
    endpoints_n = depth.get("endpoints_count", 0) or 0
    decorators_n = depth.get("decorators_count", 0) or 0
    types_n = depth.get("type_chunks_count", 0) or 0
    test_chunks = depth.get("test_chunks_count", 0) or 0
    docstr_n = depth.get("docstring_count", 0) or 0

    def _depth_mini(value: str, label: str, sub: str = "", bar_pct: float = -1) -> str:
        bar = ""
        if 0 <= bar_pct <= 100:
            bar = (
                "<div class='depth-bar'>"
                f"<span class='depth-fill' style='width:{bar_pct:.1f}%'></span></div>"
            )
        sub_html = f"<div class='mini-sub'>{_esc(sub)}</div>" if sub else ""
        return (
            "<div class='mini-card'>"
            f"<div class='mini-v'>{_esc(value)}</div>"
            f"<div class='mini-l'>{_esc(label)}</div>"
            f"{bar}{sub_html}</div>"
        )

    # endpoint 예시 펼침
    ep_examples_html = ""
    eps = depth.get("endpoints_examples", []) or []
    if eps:
        rows = "".join(
            f"<tr><td><code>{_esc(e.get('endpoint',''))}</code></td>"
            f"<td><code>{_esc(e.get('path','?'))}</code></td>"
            f"<td><code>{_esc(e.get('symbol','?'))}</code></td></tr>"
            for e in eps[:5]
        )
        ep_examples_html = (
            "<details class='example-box'>"
            "<summary>AI 가 알아낸 우리 프로젝트의 API 진입점 보기 ▾</summary>"
            "<table><tr><th>endpoint</th><th>파일</th><th>함수</th></tr>"
            f"{rows}</table></details>"
        )
    stage2_body = (
        "<div class='mini-cards'>"
        + _depth_mini(
            f"{callees_rate:.0f}%",
            "함수 간 호출 관계 매핑률",
            "이 함수가 다른 함수를 부른다는 정보 비율 — 영향 범위 추적의 기반",
            callees_rate,
        )
        + _depth_mini(
            f"{endpoints_n:,}",
            "HTTP API 진입점",
            "@app.route / app.get('/x', handler) 등 외부 노출 지점",
        )
        + _depth_mini(
            f"{test_link_rate:.0f}%",
            "테스트 ↔ 본문 자동 연결률",
            "비-테스트 코드 중 검증 테스트가 자동 매핑된 비율",
            test_link_rate,
        )
        + _depth_mini(
            f"{decorators_n:,}",
            "정적 의도 정보 (데코레이터)",
            "@require_role · @cached 등 — 코드 한 줄로 동작 의도 명시",
        )
        + _depth_mini(
            f"{types_n:,}",
            "도메인 모델 (타입·인터페이스)",
            "비즈니스 어휘 — User · Order 등 도메인 개념",
        )
        + _depth_mini(
            f"{docstr_n:,}",
            "문서화된 함수 / 클래스",
            "docstring · JSDoc · Javadoc 보유 — 자연어 의도 활용",
        )
        + "</div>"
        + ep_examples_html
    )

    # ─ Stage 3 body — 학습 품질 ───────────────────────────────────────────
    parser_rate = quality.get("parser_success_rate_pct", 0) or 0.0
    asymm = quality.get("lang_callers_asymmetry", []) or []
    asymm_html = ""
    if asymm:
        rows = "".join(
            f"<tr><td><code>{_esc(a.get('lang',''))}</code></td>"
            f"<td class='num'>{a.get('chunks', 0)}</td>"
            f"<td class='num'>{a.get('callers_links', 0)}</td></tr>"
            for a in asymm
        )
        asymm_html = (
            "<div class='warn-box'>"
            "<div class='mini-h'>⚠️ 언어별 학습 비대칭</div>"
            "<table><tr><th>언어</th><th>청크 수</th><th>호출 관계</th></tr>"
            f"{rows}</table>"
            "<div class='note'>해당 언어의 이슈는 caller 청크 회수가 부족할 수 있습니다.</div>"
            "</div>"
        )
    stage3_body = (
        "<div class='mini-cards'>"
        f"<div class='mini-card'><div class='mini-v'>{parser_rate:.0f}%</div>"
        "<div class='mini-l'>tree-sitter 파서 성공률</div>"
        f"<div class='depth-bar'><span class='depth-fill' style='width:{parser_rate:.1f}%'></span></div>"
        "</div>"
        f"<div class='mini-card'><div class='mini-v'>{sm:,}</div>"
        "<div class='mini-l'>자동 제외된 비-원본 파일</div>"
        "<div class='mini-sub'>벤더 라이브러리 · minified 번들 — AI 답변 노이즈 차단 목적</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{sd:,}</div>"
        "<div class='mini-l'>중복 제거된 청크</div>"
        "<div class='mini-sub'>같은 본문이 여러 위치에 복제된 코드 — KB 인덱싱 노이즈 차단</div></div>"
        "</div>"
        + asymm_html
    )

    # ─ Stage 4 body — 사전학습이 답변에 미친 영향 ─────────────────────────
    diag_n = agg.get("diag_count", 0) or 0
    ts_any_pct = agg.get("ts_any_hit_pct", 0.0) or 0.0
    avg_hits = agg.get("ts_avg_hits", 0.0) or 0.0
    ts_endpoint_n = agg.get("ts_endpoint_issue_count", 0) or 0
    ts_decorator_n = agg.get("ts_decorator_issue_count", 0) or 0
    ts_param_n = agg.get("ts_param_issue_count", 0) or 0
    ts_rag_meta_n = agg.get("ts_rag_meta_issue_count", 0) or 0
    citation_pct = agg.get("avg_citation_rate", 0.0) or 0.0
    used_with_ep = agg.get("used_with_endpoint_pct", 0.0) or 0.0
    used_with_dec = agg.get("used_with_decorators_pct", 0.0) or 0.0
    stage4_body = (
        "<div class='mini-cards'>"
        f"<div class='mini-card'><div class='mini-v'>{ts_any_pct:.0f}%</div>"
        "<div class='mini-l'>답변에 정적 메타가 등장한 이슈 비율</div>"
        f"<div class='depth-bar'><span class='depth-fill' style='width:{ts_any_pct:.1f}%'></span></div>"
        "<div class='mini-sub'>endpoint · decorator · 매개변수명 · RAG 청크 메타 중 하나라도 인용</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{avg_hits:.1f}</div>"
        "<div class='mini-l'>이슈당 평균 정적 메타 인용 수</div>"
        "<div class='mini-sub'>구체성 깊이 — 클수록 답변이 우리 프로젝트 어휘로 작성됨</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{citation_pct:.0f}%</div>"
        "<div class='mini-l'>RAG 청크 인용률 (평균)</div>"
        f"<div class='depth-bar'><span class='depth-fill' style='width:{citation_pct:.1f}%'></span></div>"
        "<div class='mini-sub'>받은 청크 대비 답변에 실제로 인용된 비율</div></div>"
        "</div>"
        "<div class='mini-cards' style='margin-top:8px;'>"
        f"<div class='mini-card'><div class='mini-v'>{ts_endpoint_n}</div>"
        "<div class='mini-l'>HTTP route 인용 이슈</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{ts_decorator_n}</div>"
        "<div class='mini-l'>데코레이터 인용 이슈</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{ts_param_n}</div>"
        "<div class='mini-l'>매개변수명 인용 이슈</div></div>"
        f"<div class='mini-card'><div class='mini-v'>{ts_rag_meta_n}</div>"
        "<div class='mini-l'>RAG 청크 메타 인용 이슈</div></div>"
        "</div>"
        + (
            "<div class='note' style='margin-top:8px;'>"
            f"AI 가 받은 RAG 청크 중 정적 메타 보유 비율 — endpoint {used_with_ep:.0f}% · "
            f"decorator {used_with_dec:.0f}%. 보유 비율이 낮으면 답변에 인용될 수 있는 신호 자체가 적은 것."
            "</div>"
        )
        + (
            "<div class='note'>대상 이슈 수 (RAG 진단 보유): "
            f"<code>{diag_n}</code></div>"
        )
    )

    # Phase D Fix D — narrative 도입문 동적 분기.
    # 기존 고정 문구 ("Stage 4 빨강이면 활용이 새고 있다") 는 Stage 1/3 도 빨강/노랑인
    # 케이스 (원료 부재) 에서 잘못된 trouble-shooting 을 안내. 색상 조합으로 분기.
    s1_c, s3_c, s4_c = v_scope[0], v_quality[0], v_impact[0]
    bad_set = {"red", "yellow"}
    if s4_c == "red" and (s1_c in bad_set or s3_c in bad_set):
        # 원료 부재 시나리오 — 사전학습이 부족해서 답변 활용도 자연 낮음
        narrative_msg = (
            "<strong>이번 빌드는 사전학습 단계 (Stage 1·3) 자체가 약해 답변 활용도 (Stage 4) "
            "가 자연스럽게 낮아진 케이스</strong>입니다. 'RAG retrieval 누수' 보다 "
            "<strong>원료 부족</strong>이 먼저 — 파서 실패 파일 정체 (Stage 1 의 펼침 박스), "
            "지원 언어 추가, 또는 chunk 추출 로직 점검을 우선 검토하세요."
        )
    elif s4_c == "red":
        # 원료는 충분한데 활용 누수 — 기존 메시지
        narrative_msg = (
            "<strong>Stage 1~3 은 정상인데 Stage 4 만 빨강</strong> — 사전학습은 "
            "잘됐는데 답변 활용이 새고 있다는 뜻. 이슈별 RAG 검색 / LLM 프롬프트 / "
            "context_filter 의 메타 보유 여부를 점검하세요."
        )
    elif s4_c == "yellow":
        narrative_msg = (
            "Stage 4 가 노랑 (부분 활용) — 사전학습 신호가 답변에 일부만 반영됐습니다. "
            "정적 메타 추출량 확보 (Stage 2 endpoint/decorator/docstring) 와 LLM 프롬프트 "
            "정적 메타 섹션 노출 둘 다 검토 가능."
        )
    elif s4_c == "gray":
        narrative_msg = (
            "Stage 4 측정 표본이 부족합니다. RAG 진단 데이터가 있는 이슈가 너무 적어 "
            "활용 인과를 신뢰성 있게 측정할 수 없습니다."
        )
    else:  # green
        narrative_msg = (
            "Stage 4 초록 — 사전학습 → 답변 활용 인과가 정상 작동. "
            "사이클 3 의 tree-sitter 메타 주입이 답변에 반영됨을 정량 입증."
        )

    return (
        "<h2>📚 사전학습 진단 — AI 가 우리 프로젝트를 어떻게 이해했는가</h2>"
        "<p class='note'>아래 4단계는 사다리입니다. <strong>범위</strong>(무엇을 봤는가) → "
        "<strong>깊이</strong>(얼마나 풍부히 이해했는가) → <strong>품질</strong>(노이즈 없이 깨끗한가) → "
        "<strong>영향</strong>(이번 분석 답변에 어떻게 반영됐는가).</p>"
        f"<p class='note'>{narrative_msg}</p>"
        "<div class='stages'>"
        + _stage(1, "학습 범위 — AI 가 본 것", v_scope, stage1_body)
        + _stage(2, "학습 깊이 — 이해의 풍부함", v_depth, stage2_body)
        + _stage(3, "학습 품질 — 학습이 깨끗한가", v_quality, stage3_body)
        + _stage(4, "분석에 미친 영향 — 답변에 어떻게 쓰였나", v_impact, stage4_body)
        + "</div>"
    )


def _render_failure_diagnostic(rows: list) -> str:
    """F2 — RAG 검색 실패 패턴 자동 진단. callers/tests bucket=0 또는
    context:empty 이슈를 분류하고 retrieve 단계의 어떤 신호가 막혔는지
    카테고리별 카운트.

    카테고리:
      A. context:empty — RAG 가 빈 결과 (자기 파일 청크만 있어 모두 self
         exclusion 됨 OR 모든 청크가 score threshold 미달)
      B. callers=0 — caller 청크가 retrieve 되지 않음 (KB 메타 부족 OR
         BM25 매칭 약함)
      C. tests=0 — test 청크가 retrieve 되지 않음 (보통 임베딩 의미 거리)
      D. partial_citation — 받은 청크의 절반 미만만 인용
    """
    diag_rows = [r for r in rows if r.get("rag_diagnostic") and not r.get("llm_skipped")]
    if not diag_rows:
        return ""

    n = len(diag_rows)
    a_ctx_empty = []   # 카테고리 A
    b_callers_zero = []  # B
    c_tests_zero = []    # C
    d_partial = []       # D

    for r in diag_rows:
        diag = r.get("rag_diagnostic") or {}
        used_per = diag.get("used_per_bucket") or {}
        out = r.get("outputs") or {}
        labels = out.get("labels") or []
        kept = diag.get("kept_total", 0)
        if kept == 0:
            a_ctx_empty.append(r)
        else:
            if (used_per.get("callers") or 0) == 0:
                b_callers_zero.append(r)
            if (used_per.get("tests") or 0) == 0:
                c_tests_zero.append(r)
        if "partial_citation" in labels:
            d_partial.append(r)

    def _cat_section(title: str, items: list, hint: str) -> str:
        if not items:
            return f"<li><strong>{_esc(title)}</strong>: 0 / {n} ✓</li>"
        examples = ", ".join(
            f"<code>{_esc((r.get('relative_path') or '?'))[-50:]}:{r.get('line','?')}</code>"
            for r in items[:3]
        )
        more = f" +{len(items)-3} more" if len(items) > 3 else ""
        return (
            f"<li><strong>{_esc(title)}</strong>: <strong>{len(items)} / {n}</strong> "
            f"({len(items)*100/n:.0f}%) — {hint}<br>"
            f"<span style='color:#586069;font-size:11px'>예시: {examples}{more}</span></li>"
        )

    return (
        "<h3>🔬 RAG 검색 실패 패턴 자동 진단</h3>"
        "<p class='note'>callers/tests bucket=0 또는 context:empty 이슈를 분류해 "
        "retrieve 단계의 어떤 신호가 막혔는지 카테고리별로 보여줍니다. "
        "후속 fix 우선순위 결정에 활용.</p>"
        "<ul style='line-height:1.8'>"
        + _cat_section(
            "A · context:empty (RAG 빈 결과)",
            a_ctx_empty,
            "kept=0 — 모두 self-exclusion 또는 score threshold 미달. "
            "self-exclusion 세분화(P5)·threshold 완화(B) 검토."
        )
        + _cat_section(
            "B · callers bucket=0",
            b_callers_zero,
            "호출 관계 청크가 retrieve 안 됨. KB callers 메타 부족 OR BM25 매칭 약함. "
            "P3 (callers blacklist), B threshold 완화, D1 (summary footer) 검토."
        )
        + _cat_section(
            "C · tests bucket=0",
            c_tests_zero,
            "테스트 청크가 retrieve 안 됨. cypress/e2e vs 본문 코드의 임베딩 거리 큰 "
            "전형적 패턴. D3 (test footer 동의어), A1 (e2e description) 검토."
        )
        + _cat_section(
            "D · partial_citation (인용 < 50%)",
            d_partial,
            "AI 가 받은 청크 절반 미만만 답변에 반영. confidence 자동 medium 강등됨 (P7)."
        )
        + "</ul>"
    )


def render(rows: list, title: str = "RAG Diagnostic Report", kb_stats: dict = None) -> str:
    agg = _aggregate(rows)

    # v2 — 비개발자 친화 상단 블록
    exec_summary_html = _render_executive_summary(agg)

    # 사전학습 4-stage 진단 — Phase C 재설계.
    # PM 이 사다리 형태로 (1)범위 → (2)깊이 → (3)품질 → (4)영향 인과를 따라 읽는다.
    kb_intel_html = _render_kb_intelligence(kb_stats, agg) if kb_stats else ""

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
        # +T2 — 인용의 깊이 (구체성). 평균 backtick 식별자 수.
        ("avg citation depth", f"{agg['avg_citation_depth']:.1f}",
         "impact_md 의 distinct backtick 식별자 평균 (구체성 신호)"),
        # P7 — 부분 인용으로 confidence 강등된 이슈 비율
        ("partial_citation", f"{agg['partial_citation_pct']:.1f}%",
         f"{agg['partial_citation_count']} 이슈에서 cited/total<0.5 → confidence medium 강등"),
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

    # F2 — failed bucket 자동 진단 섹션. callers/tests bucket=0 또는
    # context:empty 이슈를 모아 retrieve 단계에서 무엇이 막혔는지 분석.
    diag_html = _render_failure_diagnostic(rows)

    # 기술 지표 + 분포 블록은 접기 섹션으로 — 비개발자는 안 열어도 됨.
    technical_block = (
        "<details class='technical'>"
        "<summary>🔧 기술 상세 지표 (개발자용)</summary>"
        "<div style='margin-top:12px;'>"
        "<p class='note'>상세 수치와 분포. 비개발자는 위 신호등 요약만 읽어도 충분합니다.</p>"
        + cards_html + dist_html + diag_html
        + "</div></details>"
    )

    issues_html = "".join(_render_issue(r) for r in rows)

    body = (
        f"<h1>{_esc(title)}</h1>"
        "<div class='meta'>이 리포트는 AI 가 정적분석 이슈를 검토하면서 "
        "<strong>'이 프로젝트 고유의 코드 맥락'</strong>을 얼마나 반영했는지 측정합니다. "
        "맨 위 신호등 3 개가 종합 판정입니다.</div>"
        + exec_summary_html
        + kb_intel_html
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
    ap.add_argument("--kb-dir", default="",
                    help="사전학습 KB 디렉터리 (JSONL 모음). 지정 시 코드 인텔리전스 카드 추가.")
    args = ap.parse_args()

    rows = _load_rows(Path(args.input))
    kb_stats = _collect_kb_intelligence_stats(Path(args.kb_dir)) if args.kb_dir else None
    html_doc = render(rows, title=args.title, kb_stats=kb_stats)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"[diagnostic_report_builder] rows={len(rows)} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
