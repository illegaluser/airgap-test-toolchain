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
import os
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
    """P1 + Phase B F1 — 구조화 multi-query kb_query 구성.

    P2 R-4: attempt 별 variation — 동일 쿼리를 3회 반복하던 기존 retry 로직의
    효율을 끌어올리기 위해, 매번 다른 모양의 쿼리를 보낸다. 같은 KB 에 대해
    검색 신호를 다양화하면 누적 recall 이 단일 쿼리보다 높다는 multi-query
    retrieval 의 직관에 기반.

    Phase B F1: 이슈 함수의 tree-sitter 메타 (endpoint / decorators / doc_params)
    를 attempt=0 과 attempt=2 쿼리에 추가. 같은 라우트의 다른 핸들러,
    @require_role 같은 공통 데코레이터 패턴, 같은 param 이름의 caller 청크가
    BM25/dense 양쪽에서 매칭되도록 검색 면적을 넓힌다.

    attempt=0 (기본 — 풀 구조화):
      1) 이슈 라인 근처 코드 창 (`>>` 마커 앞뒤 3~4줄)
      2) function: enclosing_function
      3) callees: enclosing_function   — caller 정의 청크 유도
      4) test_for: enclosing_function  — test 청크 유도
      5) is_test: true                 — test 청크 일반 매칭
      6) path: relative_path
      7) rule name
      8) [F1] endpoint: <method path>  — 같은 라우트의 다른 핸들러
      9) [F1] decorators: ...          — 같은 데코레이터 패턴
      10) [F1] params: ...             — 같은 매개변수 이름의 caller

    attempt=1 (자연어 중심):
      1) rule name + 짧은 sonar_message — 의미 매칭 가중
      2) function: enclosing_function
      3) path: relative_path

    attempt=2 (식별자 중심):
      1) enclosing_function 만 — symbol 정확 일치 BM25
      2) callees: enclosing_function
      3) callers: enclosing_function
      4) [F1] endpoint: <method path>  — symbol 매칭 fallback 으로 라우트 매칭
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

    # Phase B F1 — tree-sitter 메타 추출 (exporter 가 채운 enclosing_* 필드)
    endpoint = (row.get("enclosing_endpoint") or "").strip()
    decorators = row.get("enclosing_decorators") or []
    doc_params = row.get("enclosing_doc_params") or []
    # decorator 식별자만 (`@app.route('/x')` → `app.route`) 짧은 토큰화로 BM25 매칭 강화
    dec_tokens = []
    for d in decorators[:5]:
        if not isinstance(d, str):
            continue
        # `@module.func(args)` → `module.func`
        body = d.lstrip("@").split("(", 1)[0].strip()
        if body:
            dec_tokens.append(body)
    # param 이름만 (type/desc 제외) — caller 코드의 실제 호출 인자명 매칭
    param_names = []
    for p in doc_params[:8]:
        if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1]:
            param_names.append(str(p[1]).strip())

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
        # F1 — endpoint 매칭 (식별자 중심 모드에서도 라우트 신호는 살린다)
        if endpoint:
            parts.append(f"endpoint: {endpoint}")
        # P2 R-3 — HyDE 자연어 보강 (analyzer 가 호스트 Ollama 호출 결과 주입)
        if hyde_text:
            parts.append(hyde_text)
        return "\n".join([p for p in parts if p])

    # 기본 (attempt=0) — 풀 구조화 + P1 자연어 힌트 + F1 tree-sitter 메타
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
    # F1 — endpoint / decorators / params 라인 (값 있을 때만).
    # KB footer 가 동일 키 형식으로 직렬화되므로 BM25 직접 매칭 + dense 매칭 양쪽 가능.
    if endpoint:
        parts.append(f"endpoint: {endpoint}")
    if dec_tokens:
        parts.append(f"decorators: {' '.join(dec_tokens)}")
    if param_names:
        parts.append(f"params: {' '.join(param_names)}")
    # P1 — bge-m3 dense retrieval 이 metadata-style 라인 (`callees: X`) 의
    # 의미를 약하게 잡는 경향이 관측됨 (callers bucket fill 10%, tests 0%).
    # 자연어 한 줄을 추가해 caller/test 카테고리의 임베딩 매칭 면적을 넓힌다.
    if enclosing:
        parts.append(
            f"이 함수 {enclosing} 를 호출하는 caller route handler controller, "
            f"관련 테스트 spec e2e cypress 시나리오"
        )
    return "\n".join([p for p in parts if p])


def format_dependency_tree(item) -> str:
    """Phase E E1 — depth-2 caller graph 를 LLM 친화 텍스트로.

    Phase E' (a): 헤더(`## Dependency Graph`) 도 결과에 포함. 빈 값이면 빈
    문자열 반환 → user 프롬프트의 해당 라인이 통째 빈 줄. LLM noise 감소.
    """
    direct = item.get("direct_callers") or []
    depth2 = item.get("depth2_callers") or []
    if not direct and not depth2:
        return ""
    lines = ["## Dependency Graph (depth-2)"]
    if direct:
        lines.append("직접 caller (depth 1):")
        for c in direct[:8]:
            lines.append(f"  - {c}")
    if depth2:
        lines.append("그 caller 의 caller (depth 2):")
        for c in depth2[:5]:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def format_git_history(item) -> str:
    """Phase E E3 — git 이력을 LLM 친화 텍스트로. 헤더 포함, 빈 값이면 통째 생략."""
    parts = []
    git_ctx = (item.get("git_context") or "").strip()
    similar = item.get("git_history_similar") or []
    if not git_ctx and not similar:
        return ""
    parts.append("## Git History")
    if git_ctx:
        parts.append(git_ctx)
    if similar:
        parts.append("같은 rule 의 과거 fix 이력:")
        for s in similar[:3]:
            parts.append(f"  - {s}")
    return "\n".join(parts)


def format_similar_locations(item) -> str:
    """Phase E E5 — 같은 rule 의 다른 위치. 헤더 포함, 빈 값이면 통째 생략."""
    locs = item.get("similar_rule_locations") or []
    if not locs:
        return ""
    lines = [
        f"## 같은 rule 의 다른 위치 ({len(locs)} 곳에서 같은 패턴)",
    ]
    for loc in locs[:5]:
        lines.append(f"  - {loc.get('relative_path', '?')}:{loc.get('line', '?')}")
    return "\n".join(lines)


def format_project_overview(text: str) -> str:
    """Phase E E2-lite — project_overview 를 헤더와 함께. 빈 값이면 통째 생략."""
    if not text or not text.strip():
        return ""
    return "## 프로젝트 개요 (README + 의존성 + CONTRIBUTING)\n" + text.strip()


def format_enclosing_meta(item) -> str:
    """Phase B F2b — exporter 가 채운 enclosing_* 메타를 LLM 친화 멀티라인 텍스트로.

    Dify start.enclosing_meta paragraph 변수에 들어가 LLM user 프롬프트의
    '이슈 함수 정적 메타' 섹션으로 렌더된다. 비어있으면 빈 문자열 — workflow
    템플릿이 자동으로 해당 줄을 비워 출력 (jinja 가 빈 paragraph 처리).

    포맷 (값 있는 라인만 출력):
      - decorators: @app.post('/login'), @require_role('user')
      - HTTP route: POST /login
      - parameters: email (str), password (str)
      - returns: User
      - raises: AuthError
      - callees: hash_password, verify_session
      - leading doc: Authenticate user against the local DB.
    """
    lines = []
    decorators = item.get("enclosing_decorators") or []
    if decorators:
        lines.append("- decorators: " + ", ".join(d for d in decorators[:5] if d))
    endpoint = (item.get("enclosing_endpoint") or "").strip()
    if endpoint:
        lines.append(f"- HTTP route: {endpoint}")
    doc_params = item.get("enclosing_doc_params") or []
    if doc_params:
        param_strs = []
        for p in doc_params[:10]:
            if not isinstance(p, (list, tuple)):
                continue
            t = (p[0] or "").strip() if len(p) >= 1 else ""
            n = (p[1] or "").strip() if len(p) >= 2 else ""
            if not n:
                continue
            param_strs.append(f"{n} ({t})" if t else n)
        if param_strs:
            lines.append("- parameters: " + ", ".join(param_strs))
    doc_returns = item.get("enclosing_doc_returns")
    if isinstance(doc_returns, (list, tuple)) and len(doc_returns) >= 2:
        rt, rd = (doc_returns[0] or "").strip(), (doc_returns[1] or "").strip()
        if rt or rd:
            lines.append("- returns: " + (f"{rt} — {rd}" if rt and rd else (rt or rd)))
    doc_throws = item.get("enclosing_doc_throws") or []
    if doc_throws:
        thr_strs = []
        for t in doc_throws[:5]:
            if not isinstance(t, (list, tuple)):
                continue
            ex = (t[1] or t[0] or "").strip() if len(t) >= 2 else ""
            if ex:
                thr_strs.append(ex)
        if thr_strs:
            lines.append("- raises: " + ", ".join(thr_strs))
    callees = item.get("enclosing_callees") or []
    if callees:
        lines.append("- internal callees: " + ", ".join(callees[:8]))
    doc = (item.get("enclosing_doc") or "").strip()
    if doc:
        # 200자 cap 이미 적용된 oneline. LLM 자연어 의도 인식.
        lines.append(f"- leading doc: {doc}")
    return "\n".join(lines)


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


def _compute_tree_sitter_hits(impact_md: str, item: dict, used_items: list) -> dict:
    """Phase C F4 — LLM 답변이 tree-sitter 정적 메타를 실제로 인용했는지 측정.

    citation rate (path/symbol 매칭) 만으로는 "AI 가 사전학습 결과를 활용했는가" 의
    인과를 알 수 없다. 이 함수는 답변 본문에서 다음 4종 신호의 등장 횟수를 센다:

    1. enclosing 함수의 endpoint URL (`POST /login` → `/login`, `POST` 토큰)
    2. enclosing 함수의 decorator (`@require_role` 등 식별자)
    3. enclosing 함수의 docstring param 이름 (`email`, `password` 등)
    4. used_items 의 endpoint/decorator (RAG 로 받은 다른 청크의 메타 활용)

    반환:
      {
        "endpoint_hits": int,       # endpoint URL 또는 method 토큰 매칭 수
        "decorator_hits": int,      # decorator 식별자 매칭 수
        "param_hits": int,          # param 이름 매칭 수
        "rag_meta_hits": int,       # used_items 의 endpoint/decorator 매칭 수
        "total_hits": int,          # 위 4개의 합 (대시보드용 단일 지표)
      }

    PM 관점: total_hits 가 0 이면 "사전학습 신호가 답변에 안 닿음", >0 이면
    "정적 메타가 실제 답변에 반영됨". 4-stage 진단 리포트의 Stage 4 핵심 입력.
    """
    impact = impact_md or ""
    if not impact:
        return {"endpoint_hits": 0, "decorator_hits": 0, "param_hits": 0,
                "rag_meta_hits": 0, "total_hits": 0}

    # 1. enclosing endpoint
    endpoint_hits = 0
    enc_ep = (item.get("enclosing_endpoint") or "").strip()
    if enc_ep:
        # "POST /login" → 두 토큰 모두 검사. URL path 가 들어가면 1점, method 도 들어가면 +1.
        parts = enc_ep.split(maxsplit=1)
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""
        if path and path in impact:
            endpoint_hits += 1
        if method and method in ("GET", "POST", "PUT", "PATCH", "DELETE") and method in impact:
            # method 단독 매칭은 노이즈 (영어 단어 GET 흔함) → path 와 함께 있을 때만.
            if path and path in impact:
                endpoint_hits += 1

    # 2. enclosing decorators
    decorator_hits = 0
    enc_decs = item.get("enclosing_decorators") or []
    seen_dec_idents = set()
    for d in enc_decs[:5]:
        if not isinstance(d, str):
            continue
        body = d.lstrip("@").split("(", 1)[0].strip()
        if not body or len(body) < 3:
            continue
        if body in seen_dec_idents:
            continue
        seen_dec_idents.add(body)
        if body in impact:
            decorator_hits += 1

    # 3. enclosing doc_params
    param_hits = 0
    enc_params = item.get("enclosing_doc_params") or []
    seen_pnames = set()
    for p in enc_params[:10]:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        name = (p[1] or "").strip()
        if not name or len(name) < 3 or name in seen_pnames:
            continue
        seen_pnames.add(name)
        # backtick 으로 감쌌거나 단어 경계로 분리된 경우만 — 흔한 영단어 false-positive 차단
        if re.search(rf"[`\b]{re.escape(name)}[`\b]", impact) or f"`{name}`" in impact:
            param_hits += 1

    # 4. used_items 의 endpoint/decorator — RAG 로 받은 다른 청크의 정적 메타가
    #    답변에 반영됐는지. context_filter 가 has_endpoint/decorators_raw 를 전달.
    rag_meta_hits = 0
    seen_rag = set()
    for it in used_items or []:
        ep = (it.get("endpoint_raw") or "").strip()
        if ep:
            parts = ep.split(maxsplit=1)
            path = parts[1] if len(parts) > 1 else ""
            if path and path in impact and path not in seen_rag:
                rag_meta_hits += 1
                seen_rag.add(path)
        # decorators_raw 는 footer 의 한 줄 string ("@app.route('/x') @auth")
        dec_raw = (it.get("decorators_raw") or "").strip()
        if dec_raw:
            # 첫 decorator 식별자만 취함 (`@module.func(args)` → `module.func`)
            for token in dec_raw.split():
                token = token.lstrip("@").split("(", 1)[0].strip()
                if len(token) >= 3 and token in impact and token not in seen_rag:
                    rag_meta_hits += 1
                    seen_rag.add(token)
                    break

    total = endpoint_hits + decorator_hits + param_hits + rag_meta_hits
    return {
        "endpoint_hits": endpoint_hits,
        "decorator_hits": decorator_hits,
        "param_hits": param_hits,
        "rag_meta_hits": rag_meta_hits,
        "total_hits": total,
    }


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
    # +T2 — citation_depth: impact_md 의 distinct backtick 식별자 수.
    # backtick 으로 감싼 코드-like 토큰을 LLM 의 "구체성 신호" 로 본다. 단순
    # 한두 번 인용보다 여러 식별자를 backtick 으로 감싸 referencing 한 답변이
    # RAG 컨텍스트를 더 깊게 활용한 것으로 판단 (heuristic).
    backtick_idents = set(re.findall(r"`([^`\s]+)`", impact))
    # 단일 단어 코드-like 만 — `[MAJOR]` 같은 라벨, 한국어 한 단어 등 제외.
    backtick_idents = {
        b for b in backtick_idents
        if 2 <= len(b) <= 80 and any(c.isalpha() for c in b) and "[" not in b and "]" not in b
    }

    return {
        "cited_count": len(cited),
        "cited_items": cited,
        "total_used": len(deduped),
        # +T2 — measurement only (gate 안 함). 리포트에 색인용.
        "citation_depth": len(backtick_idents),
        # P7 — partial citation 신호. analyzer 가 이를 보고 confidence 강등.
        "is_partial_citation": (
            len(deduped) >= 2 and (len(cited) / len(deduped)) < 0.5
        ),
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
        # Phase E' (b) — LLM 이 E4 검토범위 의무를 무시하면 (이전 cycle 측정 0/10)
        # analyzer 가 답변 끝에 자동 부착. deterministic 보장률 100%.
        # LLM 답변에 이미 '🔍 검토' 패턴이 있으면 중복 방지.
        impact_md = normalized.get("impact_analysis_markdown", "") or ""
        if impact_md.strip() and "🔍 검토" not in impact_md:
            buckets = context_stats.get("used_per_bucket") or {}
            cn = buckets.get("callers", 0) or 0
            tn = buckets.get("tests", 0) or 0
            on = buckets.get("others", 0) or 0
            git_hist_n = len(item.get("git_history_similar") or [])
            sim_n = len(item.get("similar_rule_locations") or [])
            depth2_n = len(item.get("depth2_callers") or [])
            scope_line = (
                f"\n\n🔍 검토: callers {cn} · tests {tn} · others {on} · "
                f"depth-2 {depth2_n} · git history {git_hist_n} · 유사 위치 {sim_n}"
            )
            impact_md = impact_md.rstrip() + scope_line
            normalized["impact_analysis_markdown"] = impact_md
        citation = _compute_citation(impact_md, used)
        # Phase C F4 — tree-sitter 메타가 답변에 실제로 반영됐는지 측정.
        ts_hits = _compute_tree_sitter_hits(impact_md, item, used)
        # P7 — confidence calibration: 부분 인용이면 high → medium 강등 + 라벨.
        # is_partial_citation 은 _compute_citation 이 (cited/total < 0.5) 일 때 true.
        if citation.get("is_partial_citation") and (normalized.get("confidence") or "").lower() == "high":
            normalized["confidence"] = "medium"
            labels = list(normalized.get("labels") or [])
            if "partial_citation" not in labels:
                labels.append("partial_citation")
            normalized["labels"] = labels
        diagnostic = {
            "retrieved_total": context_stats.get("retrieved_total", 0),
            "excluded_self": context_stats.get("excluded_self", 0),
            "kept_total": context_stats.get("kept_total", 0),
            "used_total": context_stats.get("used_total", 0),
            "buckets": context_stats.get("buckets", {}),
            "used_per_bucket": context_stats.get("used_per_bucket", {}),
            "used_items": used,
            "citation": citation,
            # Phase C F4 — Stage 4 진단 리포트의 핵심 입력.
            "tree_sitter_hits": ts_hits,
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
        # Phase B F2a passthrough — GitLab issue creator (PM 친화 본문) 가
        # 'AI 판단 근거' 섹션과 정적 메타 노출에 사용.
        "enclosing_kind": item.get("enclosing_kind", "") or "",
        "enclosing_lang": item.get("enclosing_lang", "") or "",
        "enclosing_decorators": item.get("enclosing_decorators", []) or [],
        "enclosing_endpoint": item.get("enclosing_endpoint", "") or "",
        "enclosing_doc_params": item.get("enclosing_doc_params", []) or [],
        "enclosing_doc_returns": item.get("enclosing_doc_returns"),
        "enclosing_doc_throws": item.get("enclosing_doc_throws", []) or [],
        "enclosing_doc": item.get("enclosing_doc", "") or "",
        "enclosing_callees": item.get("enclosing_callees", []) or [],
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

    # Phase E E2-lite — exporter 가 1회 작성한 metadata 섹션 (project_overview 등) 추출.
    # 모든 LLM 호출에 동일 첨부 (이슈별 다른 정보 아님).
    metadata = data.get("metadata", {}) or {}
    project_overview_text = metadata.get("project_overview", "") or ""
    if project_overview_text:
        print(f"[INFO] project_overview 적용: {len(project_overview_text)}자", file=sys.stderr)

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
        # Phase B F2b — exporter 가 채운 tree-sitter 메타를 LLM 프롬프트용 텍스트로 직렬화.
        # 빈 문자열이면 workflow user 프롬프트의 해당 섹션이 자동으로 비어 출력.
        enclosing_meta_text = format_enclosing_meta(item)
        # Phase E — graph / git history / similar locations 텍스트 직렬화.
        # Phase E' (a): format_*() 가 헤더(## ...) 까지 포함하므로 빈 값일 때
        # 통째 빈 문자열 → workflow user 프롬프트의 해당 줄이 빈 줄로.
        dependency_tree_text = format_dependency_tree(item)
        git_history_text = format_git_history(item)
        similar_locations_text = format_similar_locations(item)
        project_overview_block = format_project_overview(project_overview_text)

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
            # Phase B F2b — enclosing 함수의 tree-sitter 메타 (decorators / endpoint /
            # doc_params/returns/throws / callees / leading doc). 빈 값이면 빈 문자열.
            "enclosing_meta": enclosing_meta_text,
            # Phase E — 실질적 원인분석 강화 4종 입력 (헤더 포함, 빈 값이면 빈 문자열)
            "dependency_tree": dependency_tree_text,        # E1 — depth-2 caller graph
            "git_history": git_history_text,                # E3 — 함수 변경 이력 + 같은 rule fix 이력
            "similar_locations": similar_locations_text,    # E5 — 같은 rule 의 다른 위치
            "project_overview": project_overview_block,     # E2-lite — 프로젝트 개요 (모든 이슈 동일)
            # P1: self-exclusion — workflow 의 context_filter Code 노드가 이 경로와
            # 일치하는 RAG 청크를 제외해 "자기 파일을 다시 돌려받는" degenerate case 해소.
            "issue_file_path": item.get("relative_path", "") or "",
            # P5 — 정확한 self-exclusion 을 위해 이슈가 발생한 line 번호 전달.
            # context_filter 가 "청크 lines 가 issue_line 을 포함하는가" 로
            # self 판정 → ProfileDAO 같은 동명 method 다중 케이스에서 sibling 활용 가능.
            "issue_line": str(line) if line else "",
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
                            # F1 — retrieve trace stderr 한 줄 로깅. 어떤 청크가
                            # 어느 bucket 으로 분류됐는지 한눈에 보여 callers/tests
                            # bucket=0 의 진짜 원인 (점수 컷 vs 메타 부재 vs query
                            # 매칭 약함) 을 사후 분석 가능. 환경변수 RAG_TRACE=1
                            # 일 때만 활성 — 기본 OFF (로그 비대 방지).
                            if os.environ.get("RAG_TRACE") and ctx_stats:
                                used_items = ctx_stats.get("used_items") or []
                                ret_total = ctx_stats.get("retrieved_total", 0)
                                excl = ctx_stats.get("excluded_self", 0)
                                kept = ctx_stats.get("kept_total", 0)
                                used_brief = ", ".join(
                                    f"[{u.get('bucket','?')[:3]}]{u.get('symbol','?')}"
                                    for u in used_items[:6]
                                )
                                print(
                                    f"   [RAG-TRACE] {key[:8]} retr={ret_total} "
                                    f"excl={excl} kept={kept} used={len(used_items)} "
                                    f"items=[{used_brief}]",
                                    file=sys.stderr,
                                )
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
