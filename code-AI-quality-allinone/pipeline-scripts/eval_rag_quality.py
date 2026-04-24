#!/usr/bin/env python3
# P2 M-6 — Sonar 이슈 LLM 분석 품질 자동 평가.
#
# 사람이 라벨링한 golden CSV 를 정답지로 두고, dify_sonar_issue_analyzer.py 가
# 만든 llm_analysis.jsonl 의 결과와 비교한 metric 을 계산한다. RAG Diagnostic
# Report (M-1~M-4) 가 "현재 어떤 상태인가" 를 보여준다면, 이 스크립트는
# "변경 전후가 얼마나 좋아졌나" 를 숫자로 비교 가능하게 만든다.
#
# 입력:
#   --golden CSV — 컬럼: sonar_issue_key, expected_classification,
#       expected_confidence_min, expected_keywords (";" 구분), expected_cited_paths (";" 구분)
#   --analysis llm_analysis.jsonl
#
# 출력 (--output 미지정 시 stdout JSON):
#   {
#     "total_golden": N,
#     "matched": M,                  # golden 의 sonar_issue_key 중 analysis 에 있는 수
#     "classification_accuracy": x.x,
#     "confidence_pass_rate": x.x,
#     "keyword_coverage": x.x,       # golden keywords 중 답변 등장 비율 평균
#     "citation_precision": x.x,     # golden cited_paths 중 실제 인용 비율 평균
#     "issues": [{...}, ...]         # 이슈별 상세
#   }
#
# 운영자가 golden 행을 늘리면서 같은 명령으로 metric 추적 — 변경 전 baseline
# 측정 → 변경 적용 후 비교 → 개선/회귀 가시화.

import argparse
import csv
import json
import sys
from pathlib import Path


CONF_RANK = {"low": 0, "medium": 1, "high": 2, "": -1}


def _load_golden(path: Path) -> list:
    rows = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append({
                "sonar_issue_key": (r.get("sonar_issue_key") or "").strip(),
                "expected_classification": (r.get("expected_classification") or "").strip().lower(),
                "expected_confidence_min": (r.get("expected_confidence_min") or "").strip().lower(),
                "expected_keywords": [
                    s.strip() for s in (r.get("expected_keywords") or "").split(";") if s.strip()
                ],
                "expected_cited_paths": [
                    s.strip() for s in (r.get("expected_cited_paths") or "").split(";") if s.strip()
                ],
            })
    return [r for r in rows if r["sonar_issue_key"]]


def _load_analysis(path: Path) -> dict:
    by_key = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            key = row.get("sonar_issue_key")
            if key:
                by_key[key] = row
    return by_key


def _evaluate_one(g: dict, a: dict) -> dict:
    out = (a or {}).get("outputs") or {}
    classification = (out.get("classification") or "").lower()
    confidence = (out.get("confidence") or "").lower()
    impact = out.get("impact_analysis_markdown") or ""

    # 1) classification 일치
    cls_ok = (g["expected_classification"] == classification) if g["expected_classification"] else None

    # 2) confidence 최소 임계치 통과
    conf_ok = None
    if g["expected_confidence_min"]:
        need = CONF_RANK.get(g["expected_confidence_min"], -1)
        got = CONF_RANK.get(confidence, -1)
        conf_ok = got >= need

    # 3) keyword coverage — golden keywords 중 impact 에 등장한 비율
    kw_total = len(g["expected_keywords"])
    kw_hits = [k for k in g["expected_keywords"] if k.lower() in impact.lower()]
    kw_cov = (len(kw_hits) / kw_total) if kw_total else None

    # 4) citation precision — golden cited_paths 중 실제 cited_items 에 등장한 비율
    diag = (a or {}).get("rag_diagnostic") or {}
    cited_items = (diag.get("citation") or {}).get("cited_items") or []
    cited_paths_actual = {(c.get("path") or "") for c in cited_items}
    cp_total = len(g["expected_cited_paths"])
    cp_hits = [p for p in g["expected_cited_paths"] if p in cited_paths_actual]
    cp_prec = (len(cp_hits) / cp_total) if cp_total else None

    return {
        "sonar_issue_key": g["sonar_issue_key"],
        "found": bool(a),
        "classification_actual": classification,
        "classification_ok": cls_ok,
        "confidence_actual": confidence,
        "confidence_ok": conf_ok,
        "keyword_total": kw_total,
        "keyword_hits": len(kw_hits),
        "keyword_coverage": kw_cov,
        "citation_total": cp_total,
        "citation_hits": len(cp_hits),
        "citation_precision": cp_prec,
    }


def aggregate(golden: list, analysis: dict) -> dict:
    issue_results = []
    for g in golden:
        a = analysis.get(g["sonar_issue_key"])
        issue_results.append(_evaluate_one(g, a))

    matched = [r for r in issue_results if r["found"]]
    cls_evals = [r for r in matched if r["classification_ok"] is not None]
    conf_evals = [r for r in matched if r["confidence_ok"] is not None]
    kw_evals = [r for r in matched if r["keyword_coverage"] is not None]
    cp_evals = [r for r in matched if r["citation_precision"] is not None]

    def avg(rs, key):
        if not rs:
            return None
        vals = [r[key] for r in rs if r[key] is not None]
        return (sum(vals) / len(vals)) if vals else None

    return {
        "total_golden": len(golden),
        "matched": len(matched),
        "classification_accuracy": (
            sum(1 for r in cls_evals if r["classification_ok"]) / len(cls_evals)
            if cls_evals else None
        ),
        "confidence_pass_rate": (
            sum(1 for r in conf_evals if r["confidence_ok"]) / len(conf_evals)
            if conf_evals else None
        ),
        "keyword_coverage_avg": avg(kw_evals, "keyword_coverage"),
        "citation_precision_avg": avg(cp_evals, "citation_precision"),
        "issues": issue_results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Sonar 이슈 분석 품질 자동 평가 (golden CSV vs llm_analysis.jsonl)")
    ap.add_argument("--golden", required=True, help="golden CSV 경로")
    ap.add_argument("--analysis", required=True, help="llm_analysis.jsonl 경로")
    ap.add_argument("--output", default="", help="결과 JSON 저장 경로 (빈 값 = stdout)")
    args = ap.parse_args()

    golden_path = Path(args.golden)
    analysis_path = Path(args.analysis)
    if not golden_path.exists():
        print(f"[eval] golden 파일 없음: {golden_path}", file=sys.stderr)
        return 1
    if not analysis_path.exists():
        print(f"[eval] analysis 파일 없음: {analysis_path}", file=sys.stderr)
        return 1

    golden = _load_golden(golden_path)
    analysis = _load_analysis(analysis_path)
    result = aggregate(golden, analysis)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"[eval] saved → {args.output}")
    else:
        print(text)

    # 요약을 stderr 로도 한 줄
    summary_parts = [
        f"matched={result['matched']}/{result['total_golden']}",
    ]
    for key in ("classification_accuracy", "confidence_pass_rate",
                "keyword_coverage_avg", "citation_precision_avg"):
        v = result.get(key)
        if v is not None:
            summary_parts.append(f"{key}={v*100:.1f}%")
    print(f"[eval] {' '.join(summary_parts)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
