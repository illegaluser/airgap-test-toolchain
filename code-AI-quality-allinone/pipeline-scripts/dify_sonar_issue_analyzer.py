#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# 파일명: dify_sonar_issue_analyzer.py
# 버전: 1.2
#
# [시스템 개요]
# 이 스크립트는 품질 분석 파이프라인(Phase 3)의 **2단계(AI 기반 자동 진단)**를 담당합니다.
# sonar_issue_exporter.py가 추출한 정적 분석 이슈 JSON 파일을 입력으로 받아,
# 각 이슈를 Dify AI 워크플로우(Workflow)에 전송하고, LLM이 생성한
# 원인 분석/위험 평가/수정 방안을 JSONL 형식으로 저장합니다.
#
# [파이프라인 내 위치]
# sonar_issue_exporter.py (이슈 수집)
#       ↓ sonar_issues.json
# >>> dify_sonar_issue_analyzer.py (AI 분석) <<<
#       ↓ llm_analysis.jsonl
# gitlab_issue_creator.py (이슈 등록)
#
# [핵심 동작 흐름]
# 1. sonar_issues.json에서 이슈 목록을 읽어들입니다.
# 2. 각 이슈에 대해 코드 스니펫, 룰 설명, 메타데이터를 조합하여 Dify Workflow 입력을 구성합니다.
# 3. Dify /v1/workflows/run API를 blocking 모드로 호출하여 LLM 분석 결과를 받습니다.
# 4. 실패 시 최대 3회 재시도하며, 성공한 결과를 JSONL 파일에 한 줄씩 기록합니다.
#
# [실행 예시]
# python3 dify_sonar_issue_analyzer.py \
#   --dify-api-base http://api:5001 \
#   --dify-api-key app-xxxxxxxx \
#   --input sonar_issues.json \
#   --output llm_analysis.jsonl
# ==================================================================================

import argparse
import json
import sys
import time
import uuid
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Jenkins console 에서 실시간 진행 로그 확인을 위해 stdout/stderr 를 라인 버퍼링.
# -u (PYTHONUNBUFFERED) 옵션 없이도 `[DEBUG] >>> Sending Issue ...` 같은 진행 표시가
# 한 줄씩 즉시 console 에 반영된다. 파이프 모드에서 Python 기본 = 블록 버퍼링이라
# 수십 줄이 한꺼번에 flush 되어 오래 기동 중인 듯 보이는 혼동 방지.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def truncate_text(text, max_chars=1000):
    """
    텍스트를 지정된 최대 문자 수로 잘라냅니다.

    Dify 워크플로우에 전송하는 룰 설명(description)이 너무 길면
    코드 스니펫이 컨텍스트 윈도우에서 밀려나 LLM이 코드를 참조하지 못하게 됩니다.
    이를 방지하기 위해 룰 설명에만 길이 제한을 적용합니다.

    참고: 이전에 존재하던 HTML 정제 함수는 데이터 손실을 유발하여 삭제되었습니다.

    Args:
        text: 원본 텍스트
        max_chars: 최대 허용 문자 수 (기본 1000자)

    Returns:
        잘린 텍스트 (초과 시 "... (Rule Truncated)" 접미사 추가)
    """
    if not text: return ""
    if len(text) <= max_chars: return text
    return text[:max_chars] + "... (Rule Truncated)"

def hyde_expand(row, ollama_base_url: str, ollama_model: str, timeout: int = 60) -> str:
    """P2 R-3 — Hypothetical Document Embedding (간소화).

    Sonar 이슈를 한 문장 자연어 검색 쿼리로 변환해 kb_query 의 보조 라인으로
    추가. 임베딩 공간에서 dense 매칭이 약했던 케이스 (코드 식별자만으로는
    의미 매칭 어려운 룰) 의 retrieval recall 을 보강.

    호출 비용 (gemma4:e4b 약 30~60s) 때문에 일반 케이스에 매번 호출하지 않고
    analyzer 의 최종 retry (attempt=2) 에서만 활용. 호출 실패 시 빈 문자열
    반환 — kb_query 가 평소처럼 작동.
    """
    if not ollama_base_url:
        return ""
    rule_detail = row.get("rule_detail", {}) or {}
    prompt = (
        "Sonar 정적분석 이슈를 한 문장 자연어 검색 쿼리로 바꿔라. "
        "코드 식별자 그대로 두고, 동작/의도 위주로 50자 내. 다른 설명 금지.\n\n"
        f"Rule: {row.get('sonar_rule_key','')} - {rule_detail.get('name','')}\n"
        f"Function: {row.get('enclosing_function','')}\n"
        f"File: {row.get('relative_path','')}\n"
        f"Message: {row.get('sonar_message','')}\n\n"
        "답:"
    )
    try:
        import urllib.request
        body = json.dumps({
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 80},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{ollama_base_url.rstrip('/')}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            text = (data.get("response") or "").strip()
            # 첫 줄 + 300자 cap
            return text.splitlines()[0].strip()[:300] if text else ""
    except Exception as e:
        print(f"   [HyDE WARN] Ollama 호출 실패: {e}", file=sys.stderr)
        return ""


def build_kb_query(row, attempt: int = 0, hyde_text: str = ""):
    """P1 — 구조화 multi-query kb_query 구성.

    P2 R-4: attempt 별 variation — 동일 쿼리를 3회 반복하던 기존 retry 로직의
    효율을 끌어올리기 위해, 매번 다른 모양의 쿼리를 보낸다. 같은 KB 에 대해
    검색 신호를 다양화하면 누적 recall 이 단일 쿼리보다 높다는 multi-query
    retrieval 의 직관에 기반.

    attempt=0 (기본 — 풀 구조화):
      1) 이슈 라인 근처 코드 창 (`>>` 마커 앞뒤 3~4줄)
      2) function: enclosing_function
      3) callees: enclosing_function   — caller 정의 청크 유도
      4) test_for: enclosing_function  — test 청크 유도
      5) is_test: true                 — test 청크 일반 매칭
      6) path: relative_path
      7) rule name

    attempt=1 (자연어 중심):
      1) rule name + 짧은 sonar_message — 의미 매칭 가중
      2) function: enclosing_function
      3) path: relative_path

    attempt=2 (식별자 중심):
      1) enclosing_function 만 — symbol 정확 일치 BM25
      2) callees: enclosing_function
      3) callers: enclosing_function
    """
    snippet = row.get("code_snippet", "") or ""
    lines = snippet.splitlines()
    marker_idx = 0
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(">>"):
            marker_idx = i
            break
    window = "\n".join(lines[max(0, marker_idx - 3): marker_idx + 4]) if lines else ""

    enclosing = row.get("enclosing_function", "") or ""
    rel_path = row.get("relative_path", "") or ""
    rule_detail = row.get("rule_detail", {}) or {}
    rule_name = rule_detail.get("name") or row.get("sonar_rule_key", "") or ""
    sonar_msg = row.get("sonar_message", "") or ""

    if attempt == 1:
        parts = []
        if rule_name:
            parts.append(rule_name)
        if sonar_msg:
            parts.append(sonar_msg[:300])
        if enclosing:
            parts.append(f"function: {enclosing}")
        if rel_path:
            parts.append(f"path: {rel_path}")
        return "\n".join([p for p in parts if p])

    if attempt == 2:
        parts = []
        if enclosing:
            parts.append(enclosing)
            parts.append(f"callees: {enclosing}")
            parts.append(f"callers: {enclosing}")
        elif rule_name:
            parts.append(rule_name)
        # P2 R-3 — HyDE 자연어 보강 (analyzer 가 호스트 Ollama 호출 결과 주입)
        if hyde_text:
            parts.append(hyde_text)
        return "\n".join([p for p in parts if p])

    # 기본 (attempt=0) — 풀 구조화
    parts = [window]
    if enclosing:
        parts.append(f"function: {enclosing}")
        parts.append(f"callees: {enclosing}")
        parts.append(f"test_for: {enclosing}")
    parts.append("is_test: true")
    if rel_path:
        parts.append(f"path: {rel_path}")
    if rule_name:
        parts.append(rule_name)
    return "\n".join([p for p in parts if p])


# Step C — skip_llm 이슈 (MINOR/INFO) 에 대한 템플릿 응답 생성.
# Dify 호출 없이 analyzer 가 직접 outputs 를 구성해 llm_analysis.jsonl 에 기록.
def build_skip_llm_outputs(severity: str, msg: str) -> dict:
    return {
        "title": f"[{severity}] {msg}",
        "labels": [
            f"severity:{severity}",
            "classification:true_positive",
            "confidence:low",
            "auto_template:true",
        ],
        "impact_analysis_markdown": (
            "(자동 템플릿 — MINOR/INFO Severity 로 LLM 호출 skip. "
            "수동 리뷰 권장.)"
        ),
        "suggested_fix_markdown": "",
        "classification": "true_positive",
        "fp_reason": "",
        "confidence": "low",
        "suggested_diff": "",
    }


# Step C — outputs 스키마 안전 기본값 주입.
# Dify parameter-extractor 가 신규 필드를 못 뽑은 경우에도 creator 쪽이 KeyError
# 없이 돌도록 보장. `classification` 빈 값이면 true_positive 로 간주.
def normalize_outputs(outputs: dict) -> dict:
    out = dict(outputs or {})
    defaults = {
        "title": "",
        "labels": [],
        "impact_analysis_markdown": "",
        "suggested_fix_markdown": "",
        "classification": "true_positive",
        "fp_reason": "",
        "confidence": "medium",
        "suggested_diff": "",
    }
    for k, v in defaults.items():
        if k not in out or out[k] is None or out[k] == "":
            # classification 만 빈값 → true_positive 로 강제 기본값
            if k == "classification":
                out[k] = "true_positive"
            else:
                out[k] = v
    return out


def _compute_citation(impact_md: str, used_items: list) -> dict:
    """P1.5 M-2 — LLM 의 impact_analysis_markdown 이 used_items 중 어떤 청크를
    실제로 인용했는지 계산.

    Heuristic (Fix C 로 3-tier 완화 + Fix 4 로 dedup):
      tier 1: 전체 path 문자열 일치 (`src/auth/login.py`)
      tier 2: symbol (함수명) 일치
      tier 3: path 의 basename 일치 (`login.py` 만 언급해도 인정)
              — LLM 이 긴 path 를 생략하고 파일명만 쓰는 실전 패턴 반영.
    `?` 또는 빈 값은 매칭 대상에서 제외 — 잘못된 청크 메타데이터가 실제
    내용에 우연히 substring 으로 맞아도 cited 가 되는 false-positive 차단.

    Fix 4 (dedup): Dify segmentation 으로 동일 document 의 여러 segment 가
    각각 retrieve 되어 used_items 에 같은 (path, symbol) 이 중복 등장한다.
    cited_count 와 total_used 둘 다 (path, symbol) 기준 dedup 후 산출 — 같은
    파일을 한 번 인용한 것이 두 번으로 부풀려져 100% 처럼 보이는 현상 방지.

    반환: {"cited_count": int, "cited_items": [{...}, ...], "total_used": int}
    """
    impact = impact_md or ""

    # Fix 4 — used_items 를 (path, symbol) 기준 dedup. 첫 등장 보존.
    seen = set()
    deduped = []
    for it in (used_items or []):
        path = (it.get("path") or "").strip()
        symbol = (it.get("symbol") or "").strip()
        key = (path, symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    cited = []
    for it in deduped:
        path = (it.get("path") or "").strip()
        symbol = (it.get("symbol") or "").strip()
        # 메타데이터 footer 미포함 청크 (`?::?`) 는 매칭 대상 아님
        if path in ("", "?") and symbol in ("", "?"):
            continue
        matched = False
        if path and path not in ("?",) and path in impact:
            matched = True
        elif symbol and symbol not in ("?",) and symbol in impact:
            matched = True
        elif path and path not in ("?",):
            base = path.rsplit("/", 1)[-1]
            if len(base) >= 5 and base in impact:
                matched = True
        if matched:
            cited.append({
                "bucket": it.get("bucket"),
                "path": path,
                "symbol": symbol,
                "score": it.get("score"),
            })
    return {
        "cited_count": len(cited),
        "cited_items": cited,
        "total_used": len(deduped),
    }


def _build_out_row(*, item, key, severity, msg, line, enclosing_fn, enclosing_ln,
                   commit_sha, rule, rule_detail, final_code, outputs, llm_skipped: bool,
                   context_stats: dict = None):
    """Step C — out_row 공통 빌더. Dify 성공 경로 / skip_llm 경로 모두 같은 포맷.

    creator 가 기대하는 사실 정보 passthrough + outputs (정규화된 8 필드) +
    Step B 에서 exporter 가 넣어둔 clustering/routing 필드도 보존.

    P1.5 M-1/M-2: context_stats (context_filter 가 workflow 에서 집계해 올린
    dict) 과 LLM 답변의 citation 분석 결과를 out_row 에 담는다. 후속 단계인
    diagnostic_report_builder.py 가 이 필드를 읽어 per-이슈 진단 리포트를 생성.
    """
    normalized = normalize_outputs(outputs)
    diagnostic = None
    if context_stats is not None:
        used = context_stats.get("used_items") or []
        citation = _compute_citation(normalized.get("impact_analysis_markdown", ""), used)
        diagnostic = {
            "retrieved_total": context_stats.get("retrieved_total", 0),
            "excluded_self": context_stats.get("excluded_self", 0),
            "kept_total": context_stats.get("kept_total", 0),
            "used_total": context_stats.get("used_total", 0),
            "buckets": context_stats.get("buckets", {}),
            "used_per_bucket": context_stats.get("used_per_bucket", {}),
            "used_items": used,
            "citation": citation,
        }
    return {
        "sonar_issue_key": key,
        "severity": severity,
        "sonar_message": msg,
        "sonar_issue_url": item.get("sonar_issue_url", ""),
        # 위치 정보 (creator header 렌더용)
        "relative_path": item.get("relative_path", "") or "",
        "line": line,
        "enclosing_function": enclosing_fn,
        "enclosing_lines": enclosing_ln,
        "commit_sha": commit_sha,
        # Rule 정보 (creator '📖 Rule 상세' 섹션용)
        "rule_key": rule,
        "rule_name": rule_detail.get("name", ""),
        "rule_description": rule_detail.get("description", ""),
        # 문제 코드 블록 (creator '🔴 문제 코드' 섹션용)
        "code_snippet": final_code,
        # Step B passthrough — creator 의 Affected Locations 섹션 + FP 전이 라우팅
        "cluster_key": item.get("cluster_key", ""),
        "affected_locations": item.get("affected_locations", []) or [],
        "direct_callers": item.get("direct_callers", []) or [],
        "git_context": item.get("git_context", "") or "",
        "judge_model": item.get("judge_model", ""),
        "llm_skipped": llm_skipped,
        # LLM 생성 — 정규화된 8 필드 outputs
        "outputs": normalized,
        # P1.5 M-1/M-2 — diagnostic_report_builder.py 가 읽어 HTML 렌더.
        # skip_llm 경로에선 None (Dify 호출 없이 템플릿 응답).
        "rag_diagnostic": diagnostic,
        "generated_at": int(time.time()),
    }


def send_dify_request(url, api_key, payload):
    """
    Dify Workflow API에 HTTP POST 요청을 전송합니다.

    Jenkins 컨테이너 내부에서 Dify API 컨테이너로 직접 통신하며,
    타임아웃은 5분(300초)으로 설정합니다.
    LLM 추론은 오래 걸릴 수 있으므로 넉넉한 타임아웃이 필요합니다.

    Args:
        url: Dify Workflow 실행 엔드포인트 (예: http://api:5001/v1/workflows/run)
        api_key: Dify 앱 API 키 (Bearer 토큰)
        payload: 워크플로우 입력 데이터 (dict)

    Returns:
        tuple: (HTTP 상태 코드, 응답 본문 문자열)
               네트워크 오류 시 상태 코드 0과 에러 메시지를 반환합니다.
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, data=data)
    try:
        with urlopen(req, timeout=300) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def main():
    """
    메인 실행 함수: CLI 인자를 파싱하고, SonarQube 이슈를 순회하며 Dify 워크플로우로 분석을 요청합니다.

    [전체 처리 흐름]
    1. CLI 인자 파싱 (Dify 접속 정보, 입출력 파일 경로 등)
    2. sonar_issues.json 파일에서 이슈 목록 로드
    3. 각 이슈에 대해:
       a. 코드 스니펫, 룰 정보, 메타데이터를 추출하여 Dify 입력 포맷으로 가공
       b. Dify Workflow API 호출 (blocking 모드, 최대 3회 재시도)
       c. 성공 시 분석 결과를 JSONL 파일에 기록
    4. 결과 파일 닫기 (llm_analysis.jsonl)
    """
    # ---------------------------------------------------------------
    # [1단계] CLI 인자 파싱
    # ---------------------------------------------------------------
    parser = argparse.ArgumentParser()
    parser.add_argument("--dify-api-base", required=True)   # Dify API 베이스 URL
    parser.add_argument("--dify-api-key", required=True)     # Dify 앱 API 키
    parser.add_argument("--input", required=True)            # sonar_issues.json 경로
    parser.add_argument("--output", default="llm_analysis.jsonl")  # 분석 결과 출력 파일
    parser.add_argument("--max-issues", type=int, default=0) # 분석할 최대 이슈 수 (0=전체)
    parser.add_argument("--user", default="")                # Dify 사용자 식별자
    parser.add_argument("--response-mode", default="")       # 응답 모드 (미사용, 하위 호환)
    parser.add_argument("--timeout", type=int, default=0)    # 타임아웃 (미사용, 하위 호환)
    parser.add_argument("--print-first-errors", type=int, default=0)  # 에러 출력 수 제한
    # P2 R-3 — HyDE (간소화). attempt=2 일 때만 호스트 Ollama 로 한 줄 자연어
    # 변환 호출해 kb_query 에 추가. 빈 값이면 비활성. 일반 케이스 (1차 성공)
    # 에는 영향 0, 마지막 retry 에서만 부담.
    parser.add_argument("--hyde-ollama-base-url", default="",
                        help="Ollama base URL (예: http://host.docker.internal:11434). 빈 값 = HyDE off")
    parser.add_argument("--hyde-ollama-model", default="gemma4:e4b",
                        help="HyDE 변환에 사용할 Ollama 모델")
    # Step R 신규 — creator 가 deterministic 본문 렌더에 쓸 commit 정보 전달용.
    # 비어있으면 out_row 의 commit_sha 도 빈 문자열 (creator 가 commit 섹션 생략).
    parser.add_argument("--commit-sha", default="")
    args, _ = parser.parse_known_args()

    # ---------------------------------------------------------------
    # [2단계] 입력 파일(sonar_issues.json) 로드
    # sonar_issue_exporter.py가 생성한 이슈 목록을 읽어들입니다.
    # ---------------------------------------------------------------
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Cannot read input file: {e}", file=sys.stderr)
        sys.exit(1)

    # 이슈 목록 추출 및 개수 제한 적용
    issues = data.get("issues", [])
    if args.max_issues > 0: issues = issues[:args.max_issues]

    # 결과를 기록할 JSONL 파일 열기
    # buffering=1 = line-buffered. 각 out_fp.write 뒤 명시적 flush 없이도 줄 단위로
    # 디스크 반영됨 → 장기 실행 중 Jenkins 가 실시간 JSONL 을 읽어 진행 상황 확인
    # 가능. text 모드에서 line buffering 은 Python 에서 합법.
    out_fp = open(args.output, "w", encoding="utf-8", buffering=1)

    # Dify API 엔드포인트 구성
    # 사용자가 /v1 접미사를 빠뜨려도 자동으로 보정합니다.
    base_url = args.dify_api_base.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    target_api_url = f"{base_url}/workflows/run"

    print(f"[INFO] Analyzing {len(issues)} issues...", file=sys.stderr)

    # --print-first-errors: empty-output 사유 상세 덤프를 최대 N회만 출력.
    # 0 이면 무제한. 매 빈 outputs 마다 parse_status + llm_text_preview 를 찍는다.
    empty_debug_budget = [args.print_first_errors]  # list 로 감싸 inner scope 에서 뮤터블 참조

    # ---------------------------------------------------------------
    # [3단계] 각 이슈를 순회하며 Dify 워크플로우에 분석 요청
    # ---------------------------------------------------------------
    for item in issues:
        # --- 3-a. 이슈 메타데이터 추출 ---
        key = item.get("sonar_issue_key")           # SonarQube 이슈 고유 키
        rule = item.get("sonar_rule_key", "")       # 위반 규칙 ID (예: java:S1192)
        project = item.get("sonar_project_key", "") # SonarQube 프로젝트 키

        # issue_search_item: SonarQube /api/issues/search 원본 응답 항목
        issue_item = item.get("issue_search_item", {})
        msg = issue_item.get("message", "")          # 이슈 설명 메시지
        severity = issue_item.get("severity", "")    # 심각도 (BLOCKER, CRITICAL 등)
        component = item.get("component", "")        # 파일 경로 (프로젝트키:src/...)
        line = issue_item.get("line") or issue_item.get("textRange", {}).get("startLine", 0)

        # --- 3-b. 코드 스니펫 추출 ---
        # 여러 키 이름을 시도하여 코드를 확보합니다.
        # sonar_issue_exporter.py는 code_snippet 키에 저장하지만,
        # 다른 소스에서 온 데이터도 호환 지원합니다.
        raw_code = item.get("code_snippet", "")
        if not raw_code:
            raw_code = item.get("source", "") or item.get("code", "")

        # HTML 정제 등의 가공 없이 원본 코드를 그대로 사용합니다.
        # 이전 버전에서 HTML 태그 정제가 코드 내용을 훼손한 사례가 있었기 때문입니다.
        final_code = raw_code if raw_code else "(NO CODE CONTENT)"

        # --- 3-c. 룰 정보 가공 ---
        # 룰 설명은 길이만 제한하되 내용은 그대로 유지합니다.
        rule_detail = item.get("rule_detail", {})
        raw_desc = rule_detail.get("description", "")
        safe_desc = truncate_text(raw_desc, max_chars=800)

        # Dify 워크플로우의 Jinja2 템플릿에서 중괄호({})를 변수 구분자로 사용하므로,
        # 설명 텍스트 내의 중괄호를 소괄호로 치환하여 파싱 에러를 방지합니다.
        safe_rule_json = json.dumps({
            "key": rule_detail.get("key"),
            "name": rule_detail.get("name"),
            "description": safe_desc.replace("{", "(").replace("}", ")")
        }, ensure_ascii=False)

        # 이슈 메타데이터를 JSON 문자열로 직렬화하여 Dify 입력에 포함합니다.
        safe_issue_json = json.dumps({
            "key": key, "rule": rule, "message": msg, "severity": severity,
            "project": project, "component": component, "line": line
        }, ensure_ascii=False)

        # 각 이슈 요청마다 고유한 사용자 ID를 생성합니다.
        # Dify가 세션을 분리하여 이전 대화의 영향을 받지 않도록 합니다.
        session_user = f"jenkins-{uuid.uuid4()}"

        print(f"\n[DEBUG] >>> Sending Issue {key}")

        # Step R 공통 메타데이터는 skip_llm / llm 호출 경로 모두에서 사용한다.
        # 기존에는 skip_llm 분기 아래에서 초기화되어 MINOR/INFO 라우팅 시
        # UnboundLocalError 가 발생했다.
        enclosing_fn = item.get("enclosing_function", "") or ""
        enclosing_ln = item.get("enclosing_lines", "") or ""
        commit_sha = item.get("commit_sha", "") or args.commit_sha or ""

        # Step C — exporter 가 severity routing 으로 skip_llm=True 로 태깅한 이슈는
        # Dify 호출 자체를 건너뛰고 템플릿 응답으로 out_row 를 구성. MINOR/INFO
        # 이하 이슈에서 LLM 비용을 줄이는 경로.
        skip_llm = bool(item.get("skip_llm"))
        if skip_llm:
            print(f"[SKIP_LLM] {key} — template response generated")
            rd = item.get("rule_detail", {}) or {}
            templated = build_skip_llm_outputs(severity, msg)
            out_row = _build_out_row(
                item=item, key=key, severity=severity, msg=msg,
                line=line, enclosing_fn=enclosing_fn, enclosing_ln=enclosing_ln,
                commit_sha=commit_sha, rule=rule, rule_detail=rd,
                final_code=final_code, outputs=templated, llm_skipped=True,
            )
            out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            continue

        # --- 3-d. Dify 워크플로우 입력 데이터 구성 ---
        # Step C: kb_query 는 multi-query 로 확장 (기존 `rule + msg` 1줄 대신
        # 이슈 라인 근처 코드 + enclosing function + path + rule name 4줄).
        # 이렇게 하면 RAG 가 rule 이름뿐 아니라 "이 함수/파일 맥락의 유사 청크"
        # 까지 꺼낸다.
        # Step R: exporter 가 추출한 enclosing_function / enclosing_lines /
        # commit_sha 를 LLM 에 추가 힌트로 전달 (프롬프트에 "위치 사실은 별도
        # 렌더되므로 본문에 반복하지 말 것" 명시).
        # 초기 kb_query 는 attempt=0 (풀 구조화). 재시도 시 build_kb_query(item, attempt=i)
        # 로 다른 모양의 쿼리로 변경 — P2 R-4.
        inputs = {
            "sonar_issue_key": key,
            "sonar_project_key": project,
            "code_snippet": final_code,
            "sonar_issue_url": item.get("sonar_issue_url", ""),
            "kb_query": build_kb_query(item, attempt=0),
            "sonar_issue_json": safe_issue_json,
            "sonar_rule_json": safe_rule_json,
            # Step R 신규 inputs
            "enclosing_function": enclosing_fn,
            "enclosing_lines": enclosing_ln,
            "commit_sha": commit_sha,
            # P1: self-exclusion — workflow 의 context_filter Code 노드가 이 경로와
            # 일치하는 RAG 청크를 제외해 "자기 파일을 다시 돌려받는" degenerate case 해소.
            "issue_file_path": item.get("relative_path", "") or "",
            # retry_hint 는 아래 retry 루프에서 attempt 마다 갱신.
            # 워크플로우의 LLM user 프롬프트 끝에 {{#start.retry_hint#}} 로 삽입됨.
        }

        # 디버깅용: 실제로 전송되는 코드 내용을 확인합니다.
        print(f"   [DATA CHECK] Code Length: {len(final_code)}")
        print(f"   [DATA CHECK] Preview: {final_code[:100].replace(chr(10), ' ')}...")

        # Dify Workflow 실행 페이로드
        # response_mode="blocking": 워크플로우 완료까지 대기 후 결과 반환
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": session_user
        }

        # --- 3-e. API 호출 및 재시도 로직 ---
        # 최대 3회 시도하며, 실패 시 2초 대기 후 재시도합니다.
        # LLM 추론 과부하나 일시적 네트워크 문제에 대한 내결함성을 확보합니다.
        # Dify 가 `succeeded` 를 반환해도 LLM 본체가 빈/비-JSON 응답을 내뱉어
        # Code 노드의 default fallback 으로 떨어진 경우 핵심 필드가 비므로
        # impact_analysis_markdown 비면 재시도로 간주.
        success = False
        last_outputs = None
        # 재시도 힌트 — attempt index 별로 escalating.
        # 1차: 기본 시스템 프롬프트만. 2차: JSON 엄격 재강조. 3차: 최소 뼈대 복사 지시.
        # 4B 모델이 첫 시도에서 스키마 붕괴 시 동일 프롬프트 반복은 효과 낮음.
        retry_hints = [
            "",
            (
                "**[재시도 1]** 이전 응답이 JSON 스키마를 충족하지 못했습니다. "
                "이번엔 **JSON 객체 하나만** 출력하세요. 코드펜스 / 설명 문장 금지. "
                "최소 `title`, `labels`, `impact_analysis_markdown` 3 필드는 "
                "반드시 비지 않게 채우세요."
            ),
            (
                "**[재시도 2 — 최후]** 출력은 다음 뼈대를 그대로 복사하되, "
                "**값 안의 `...` 는 placeholder 이므로 반드시 실제 이슈 내용으로 교체**하세요. "
                "`...` 를 그대로 두면 안 됩니다. 필드 삭제·추가 금지, 코드펜스 금지:\n"
                "{\n"
                '  "title": "<실제 한줄 요약>",\n'
                '  "labels": ["<severity>", "<type>"],\n'
                '  "impact_analysis_markdown": "<3~6줄 영향 분석>",\n'
                '  "suggested_fix_markdown": "",\n'
                '  "classification": "true_positive",\n'
                '  "fp_reason": "",\n'
                '  "confidence": "medium",\n'
                '  "suggested_diff": ""\n'
                "}"
            ),
        ]
        for i in range(3):
            inputs["retry_hint"] = retry_hints[i]
            # P2 R-3 — attempt=2 (마지막 retry) 에 한해 HyDE 자연어 변환 호출.
            # 이전 attempt 에서 모두 빈 응답이었다는 신호 → recall 보강 필요.
            hyde_text = ""
            if i == 2 and args.hyde_ollama_base_url:
                hyde_text = hyde_expand(item, args.hyde_ollama_base_url, args.hyde_ollama_model)
                if hyde_text:
                    print(f"   [HyDE] attempt=2 보강 쿼리: {hyde_text[:80]}...", file=sys.stderr)
            # P2 R-4: 매 attempt 마다 다른 모양의 kb_query.
            inputs["kb_query"] = build_kb_query(item, attempt=i, hyde_text=hyde_text)
            status, body = send_dify_request(target_api_url, args.dify_api_key, payload)

            if status == 200:
                try:
                    res = json.loads(body)
                    # Dify 워크플로우 내부 실행이 성공했는지 확인합니다.
                    if res.get("data", {}).get("status") == "succeeded":
                        outputs = res["data"].get("outputs", {}) or {}
                        if (outputs.get("impact_analysis_markdown") or "").strip():
                            rd = item.get("rule_detail", {}) or {}
                            # P1.5 M-1 — context_filter 가 올린 stats JSON 파싱.
                            # 실패 시 None → diagnostic 필드가 None 으로 기록.
                            ctx_stats = None
                            raw_stats = outputs.get("context_stats_json") or ""
                            if raw_stats:
                                try:
                                    ctx_stats = json.loads(raw_stats)
                                except Exception:
                                    ctx_stats = None
                            out_row = _build_out_row(
                                item=item, key=key, severity=severity, msg=msg,
                                line=line, enclosing_fn=enclosing_fn, enclosing_ln=enclosing_ln,
                                commit_sha=commit_sha, rule=rule, rule_detail=rd,
                                final_code=final_code,
                                outputs=outputs,
                                llm_skipped=False,
                                context_stats=ctx_stats,
                            )
                            out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                            success = True
                            print(f"   -> Success.")
                            break
                        else:
                            last_outputs = outputs
                            parse_status = outputs.get("parse_status") or "(workflow 미갱신? parse_status 미노출)"
                            print(
                                f"   -> Dify succeeded but outputs empty "
                                f"(impact_analysis_markdown missing) [parse_status={parse_status}]",
                                file=sys.stderr,
                            )
                            # 상세 덤프: 할당량 남아있을 때만 llm_text_preview 전문 출력
                            if empty_debug_budget[0] != 0:
                                preview = outputs.get("llm_text_preview") or ""
                                parse_error = outputs.get("parse_error_msg") or ""
                                ctx_raw = outputs.get("context_stats_json") or ""
                                ctx_summary = ""
                                if ctx_raw:
                                    try:
                                        cs = json.loads(ctx_raw)
                                        ctx_summary = (
                                            f"retrieved={cs.get('retrieved_total')}, "
                                            f"kept={cs.get('kept_total')}, "
                                            f"used={cs.get('used_total')}, "
                                            f"buckets={cs.get('buckets')}"
                                        )
                                    except Exception:
                                        ctx_summary = "(context_stats_json 파싱 실패)"
                                print(
                                    f"      [EMPTY-DEBUG] key={key} attempt={i} "
                                    f"parse_status={parse_status}\n"
                                    f"      [EMPTY-DEBUG] parse_error: {parse_error}\n"
                                    f"      [EMPTY-DEBUG] context: {ctx_summary}\n"
                                    f"      [EMPTY-DEBUG] llm_text_preview ({len(preview)} chars):\n"
                                    f"------------------ LLM RAW ------------------\n"
                                    f"{preview}\n"
                                    f"---------------------------------------------",
                                    file=sys.stderr,
                                )
                                if empty_debug_budget[0] > 0:
                                    empty_debug_budget[0] -= 1
                    else:
                        # HTTP 200이지만 워크플로우 내부에서 실패한 경우
                        print(f"   -> Dify Internal Fail: {res}", file=sys.stderr)
                except: pass

            print(f"   -> Retry {i+1}/3 due to Status {status} | Error: {body}")
            time.sleep(2)

        if not success:
            if last_outputs is not None:
                # 3회 재시도가 전부 빈 outputs 로 끝난 경우 — 이슈 자체를 드롭하는 대신
                # 마지막 빈 응답을 그대로 기록해 GitLab 이슈는 생성되도록 한다.
                # creator 가 "LLM 이 영향 분석을 제공하지 않음" 폴백 텍스트로 렌더.
                rd = item.get("rule_detail", {}) or {}
                # P1.5 M-1/M-4 — retry 실패 경로에서도 context_filter 통계는 유효.
                ctx_stats = None
                raw_stats = last_outputs.get("context_stats_json") or ""
                if raw_stats:
                    try:
                        ctx_stats = json.loads(raw_stats)
                    except Exception:
                        ctx_stats = None
                out_row = _build_out_row(
                    item=item, key=key, severity=severity, msg=msg,
                    line=line, enclosing_fn=enclosing_fn, enclosing_ln=enclosing_ln,
                    commit_sha=commit_sha, rule=rule, rule_detail=rd,
                    final_code=final_code, outputs=last_outputs, llm_skipped=False,
                    context_stats=ctx_stats,
                )
                out_row["retry_exhausted"] = True  # M-4 집계 키
                out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                print(f"[FAIL-EMPTY] {key} — 3 retries exhausted with empty outputs; row written anyway", file=sys.stderr)
            else:
                print(f"[FAIL] Failed to analyze {key}", file=sys.stderr)

    # ---------------------------------------------------------------
    # [4단계] 결과 파일 닫기
    # ---------------------------------------------------------------
    out_fp.close()

if __name__ == "__main__":
    main()
