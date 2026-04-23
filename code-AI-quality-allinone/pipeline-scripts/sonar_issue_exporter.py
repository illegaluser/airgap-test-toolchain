#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# 파일명: sonar_issue_exporter.py
# 버전: 1.2
#
# [시스템 개요]
# 이 스크립트는 품질 분석 파이프라인(Phase 3)의 **1단계(정적 분석 결과 수집)**를 담당합니다.
# SonarQube REST API를 통해 미해결(open) 이슈 목록을 페이지네이션으로 전수 조회하고,
# 각 이슈에 대해 관련 소스 코드 라인과 위반 규칙 상세 정보를 추가로 수집(enrichment)하여
# 하나의 JSON 파일로 통합합니다.
#
# [파이프라인 내 위치]
# SonarQube (정적 분석 결과)
#       ↓ REST API
# >>> sonar_issue_exporter.py (이슈 수집 + 코드/룰 보강) <<<
#       ↓ sonar_issues.json
# dify_sonar_issue_analyzer.py (AI 분석)
#
# [핵심 동작 흐름]
# 1. /api/issues/search: 미해결 이슈 목록을 100건 단위로 페이지네이션하여 전수 조회
# 2. /api/rules/show: 각 이슈의 위반 규칙 상세 설명을 조회 (캐싱하여 중복 호출 방지)
# 3. /api/sources/lines: 이슈 발생 위치 전후 100줄의 소스 코드를 조회
# 4. 모든 정보를 통합하여 sonar_issues.json 파일로 저장
#
# [실행 예시]
# python3 sonar_issue_exporter.py \
#   --sonar-host-url http://sonarqube:9000 \
#   --sonar-token squ_xxxxx \
#   --project-key myproject \
#   --output sonar_issues.json
# ==================================================================================

import argparse
import base64
import glob
import hashlib
import json
import subprocess
import sys
import html
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

# Step R 에서 enclosing_function 추출을 위해 repo_context_builder 의 청킹
# 로직 재사용. 같은 /opt/pipeline-scripts/ (entrypoint.sh 가 scripts 로 심볼릭
# 링크) 안에 형제 모듈이므로 직접 import. 실패 시 graceful — enclosing_function
# 추출만 skip 되고 나머지 파이프라인은 동작.
try:
    from repo_context_builder import extract_chunks_from_file, LANG_CONFIG  # type: ignore
    _TS_AVAILABLE = True
except Exception as _e:  # noqa: BLE001
    _TS_AVAILABLE = False
    _TS_IMPORT_ERR = str(_e)


def _clean_html_tags(text: str) -> str:
    """
    HTML 태그를 제거하고 HTML 엔티티를 디코딩합니다.

    SonarQube API 응답에는 코드와 룰 설명에 HTML 태그가 포함되어 있습니다.
    (예: <span class="k">public</span>, &lt;String&gt;)
    LLM이 코드를 정확히 분석하려면 순수 텍스트가 필요하므로,
    태그를 제거하고 엔티티를 원래 문자로 복원합니다.

    Args:
        text: HTML이 포함된 원본 텍스트

    Returns:
        HTML 태그가 제거되고 엔티티가 디코딩된 순수 텍스트
    """
    if not text: return ""
    # 1단계: HTML 태그 제거 (<span ...>, </div> 등)
    text = re.sub(r'<[^>]+>', '', text)
    # 2단계: HTML 엔티티 디코딩 (&lt; → <, &amp; → & 등)
    text = html.unescape(text)
    return text


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    HTTP GET 요청을 보내고 JSON 응답을 파싱하여 반환합니다.

    SonarQube의 모든 API 호출에 공통으로 사용되는 헬퍼 함수입니다.
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _build_basic_auth(token: str) -> str:
    """
    SonarQube 토큰을 HTTP Basic Authentication 헤더 값으로 변환합니다.

    SonarQube는 토큰을 사용자명으로, 비밀번호는 빈 문자열로 하는
    Basic Auth 방식을 사용합니다. (token: 형식)
    """
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")


def _api_url(host: str, path: str, params: dict = None) -> str:
    """
    SonarQube API 엔드포인트의 전체 URL을 생성합니다.

    Args:
        host: SonarQube 호스트 URL (예: http://sonarqube:9000)
        path: API 경로 (예: /api/issues/search)
        params: 쿼리 파라미터 딕셔너리

    Returns:
        완전한 URL 문자열 (쿼리스트링 포함)
    """
    base = host.rstrip("/") + "/"
    url = urljoin(base, path.lstrip("/"))
    if params:
        url += "?" + urlencode(params, doseq=True)
    return url

def _get_rule_details(host: str, headers: dict, rule_key: str) -> dict:
    """
    SonarQube 위반 규칙의 상세 정보를 조회합니다.

    /api/rules/show 엔드포인트에서 규칙의 이름, 설명, 심각도, 언어 정보를 가져옵니다.
    이 정보는 LLM이 이슈를 분석할 때 "왜 이것이 문제인지"를 이해하는 데 사용됩니다.

    SonarQube 규칙 설명은 여러 섹션(descriptionSections)으로 구성될 수 있으며,
    각 섹션에는 HTML 태그가 포함되어 있으므로 태그를 제거한 후 반환합니다.

    Args:
        host: SonarQube 호스트 URL
        headers: Basic Auth 인증 헤더
        rule_key: 규칙 키 (예: "java:S1192")

    Returns:
        dict: 규칙 상세 정보 (key, name, description, severity, lang)
              API 호출 실패 시 기본값을 반환하여 전체 프로세스가 중단되지 않습니다.
    """
    if not rule_key:
        return {"key": "UNKNOWN", "name": "Unknown", "description": "No rule key."}

    url = _api_url(host, "/api/rules/show", {"key": rule_key})

    # API 호출 실패 시 사용할 기본값
    fallback = {
        "key": rule_key,
        "name": f"Rule {rule_key}",
        "description": "No detailed description available.",
        "lang": "code"
    }

    try:
        resp = _http_get_json(url, headers)
        rule = resp.get("rule", {})
        if not rule: return fallback

        # 구조화된 설명 섹션(예: ROOT_CAUSE, HOW_TO_FIX)을 순회하며 텍스트를 수집합니다.
        desc_parts = []
        sections = rule.get("descriptionSections", [])
        for sec in sections:
            k = sec.get("key", "").upper().replace("_", " ")  # 섹션 이름을 대문자로 정리
            c = sec.get("content", "")
            if c:
                # HTML 태그를 제거하여 LLM이 순수 텍스트로 읽을 수 있게 합니다.
                desc_parts.append(f"[{k}]\n{_clean_html_tags(c)}")

        full_desc = "\n\n".join(desc_parts)
        # 구조화 섹션이 없으면 레거시 필드(mdDesc, htmlDesc)를 대안으로 사용합니다.
        if not full_desc:
            raw_desc = rule.get("mdDesc") or rule.get("htmlDesc") or rule.get("description") or ""
            full_desc = _clean_html_tags(raw_desc)

        return {
            "key": rule.get("key", rule_key),
            "name": rule.get("name", fallback["name"]),
            "description": full_desc if full_desc else fallback["description"],
            "severity": rule.get("severity", "UNKNOWN"),
            "lang": rule.get("lang", "code")
        }
    except:
        return fallback

def _relative_path_from_component(component: str, project_key: str) -> str:
    """Sonar component (예: 'dscore-ttc-sample:src/auth.py') 에서 프로젝트 prefix 를
    제거해 레포 상대 경로를 얻는다. 예상 패턴이 아니면 원문 유지.
    """
    if not component:
        return ""
    prefix = f"{project_key}:"
    if component.startswith(prefix):
        return component[len(prefix):]
    # 프로젝트키 없이 그냥 'src/...' 로 들어온 경우 그대로
    return component


def _enclosing_function(repo_root: str, rel_path: str, target_line: int) -> tuple:
    """레포 루트·상대경로·이슈 라인을 받아 해당 라인을 포함하는 함수/메서드의
    (symbol, lines_str) 튜플을 반환. 실패 시 ("", "").

    repo_context_builder 의 `extract_chunks_from_file` 가 이미 LANG_CONFIG 에 맞춰
    AST 청크를 만들므로, 그중 `lines` 범위가 target_line 을 포함하는 청크의
    `symbol` 을 가져온다. 리팩터 대신 재사용.
    """
    if not _TS_AVAILABLE or not repo_root or not rel_path or target_line <= 0:
        return ("", "")
    abs_path = Path(repo_root) / rel_path
    if not abs_path.is_file():
        return ("", "")
    # 지원 확장자 필터 (LANG_CONFIG 에 없으면 skip)
    if abs_path.suffix.lower() not in LANG_CONFIG:
        return ("", "")
    try:
        chunks = extract_chunks_from_file(abs_path, Path(repo_root), commit_sha="")
    except Exception:
        return ("", "")
    # 가장 좁은 범위(= 이슈 라인에 가장 가까운) 청크 선택. class 가 전체 파일을
    # 감쌀 수 있으므로 function/method 를 우선, 없으면 class 도 수용.
    best = None
    best_span = float("inf")
    for ch in chunks:
        lines = ch.get("lines", "")
        try:
            s, e = map(int, lines.split("-", 1))
        except Exception:
            continue
        if s <= target_line <= e:
            span = e - s
            kind = ch.get("kind", "")
            # function/method 는 class 보다 선호 (span tie-break 전에 kind 우선)
            pref = 0 if kind in ("function", "method") else 1
            key = (pref, span)
            if best is None or key < best_span:
                best = ch
                best_span = key
    if best is None:
        return ("", "")
    return (best.get("symbol", ""), best.get("lines", ""))


def _git_context(repo_root: str, rel_path: str, line: int) -> str:
    """Step B — 이슈 라인의 git blame + 파일 최근 log 요약을 3줄 텍스트로 반환.

    실패 시 빈 문자열 (파이프라인 계속). LLM 에 "이 코드를 누가 언제 왜 넣었는지"
    맥락을 공급.
    """
    if not repo_root or not rel_path or line <= 0:
        return ""
    if not Path(repo_root).is_dir():
        return ""
    try:
        blame = subprocess.run(
            ["git", "-C", repo_root, "blame", "-L", f"{line},{line}", "--porcelain", rel_path],
            capture_output=True, text=True, timeout=15
        )
        author = ""
        committed = ""
        sha = ""
        if blame.returncode == 0:
            for ln in blame.stdout.splitlines():
                if ln.startswith("author "):
                    author = ln[len("author "):]
                elif ln.startswith("author-time "):
                    # unix epoch — 사람 읽기 좋은 포맷으로 변환은 생략 (복잡도 회피)
                    committed = ln[len("author-time "):]
                elif not sha and re.match(r"^[0-9a-f]{40}", ln):
                    sha = ln.split()[0][:12]

        log_line = ""
        log_run = subprocess.run(
            ["git", "-C", repo_root, "log", "-1", "--format=%an|%ar|%s", "--", rel_path],
            capture_output=True, text=True, timeout=15
        )
        if log_run.returncode == 0 and log_run.stdout.strip():
            log_line = log_run.stdout.strip()

        parts = []
        if author or sha:
            parts.append(f"blame L{line}: {author} ({sha})")
        if committed:
            parts.append(f"committed_at(epoch)={committed}")
        if log_line:
            parts.append(f"last_commit: {log_line}")
        return "\n".join(parts)
    except Exception:
        return ""


def _load_callgraph_index(callgraph_dir: str) -> dict:
    """Step B — callgraph_dir 아래 *.jsonl 을 한 번 로딩해 `callee_symbol → [caller path::symbol, ...]`
    역인덱스를 구성. exporter 실행당 최초 1회만 돌고, 이후 `_direct_callers` 는 이 dict 만 조회.
    """
    idx: dict = {}
    if not callgraph_dir or not Path(callgraph_dir).is_dir():
        return idx
    for jp in sorted(glob.glob(os.path.join(callgraph_dir, "*.jsonl"))):
        try:
            with open(jp, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        ch = json.loads(ln)
                    except Exception:
                        continue
                    path = ch.get("path") or ""
                    symbol = ch.get("symbol") or ""
                    callees = ch.get("callees") or []
                    caller_ref = f"{path}::{symbol}" if path and symbol else (path or symbol)
                    if not caller_ref:
                        continue
                    for cal in callees:
                        if not cal:
                            continue
                        idx.setdefault(cal, []).append(caller_ref)
        except Exception:
            continue
    return idx


def _direct_callers(cg_index: dict, symbol: str, limit: int = 10) -> list:
    """cg_index 에서 symbol 을 호출하는 caller 리스트를 반환 (최대 limit)."""
    if not symbol or not cg_index:
        return []
    refs = cg_index.get(symbol, [])
    # 동일 caller 가 중복으로 들어갈 수 있으므로 dedup + 순서 보존
    seen = set()
    out = []
    for r in refs:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _classify_severity(severity: str) -> tuple:
    """Step B — 모든 severity 를 gemma4:e4b 로 분석.

    severity 와 무관하게 Dify/LLM 분석을 수행하며 skip_llm 분기를 사용하지 않는다.
    """
    _ = (severity or "").upper()
    return ("gemma4:e4b", False)


def _cluster_key(rule_key: str, enclosing_function: str, component: str) -> str:
    """Step B — 같은 규칙 · 같은 함수 · 같은 디렉터리 안 이슈는 한 cluster.

    대표 1건만 emit 하고 나머지는 affected_locations 로 묶어 P3 LLM 호출 절감.
    """
    base_dir = os.path.dirname(component or "")
    raw = f"{rule_key or ''}|{enclosing_function or ''}|{base_dir}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _severity_rank(sev: str) -> int:
    """Clustering 대표 선정용 — 심각도가 높을수록 작은 rank 값."""
    order = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}
    return order.get((sev or "").upper(), 9)


def _apply_clustering(records: list) -> list:
    """cluster_key 동일 그룹 → 대표 1건만 남기고 나머지는 affected_locations 로 합침.

    대표 선정: severity 가장 심각 → line 번호 작은 순.
    비결정적 정렬 회피를 위해 cluster_key 내 원본 순서 유지 후 key 로만 pick.
    """
    groups: dict = {}
    for r in records:
        k = r.get("cluster_key") or r.get("sonar_issue_key")
        groups.setdefault(k, []).append(r)
    out: list = []
    for k, items in groups.items():
        if len(items) == 1:
            items[0]["affected_locations"] = []
            out.append(items[0])
            continue
        items.sort(key=lambda r: (_severity_rank(r.get("issue_search_item", {}).get("severity", "")), r.get("line") or 0))
        leader = items[0]
        followers = items[1:]
        leader["affected_locations"] = [
            {
                "component": f.get("component"),
                "line": f.get("line"),
                "sonar_issue_key": f.get("sonar_issue_key"),
                "relative_path": f.get("relative_path"),
            }
            for f in followers
        ]
        out.append(leader)
    return out


def _diff_mode_filter(records: list, state_dir: str, mode: str) -> tuple:
    """Step B — diff-mode.

    `mode=incremental` → {state_dir}/last_scan.json 의 이슈 key set 과 비교, 기존 key 는 drop.
    `mode=full` → 필터 없음 + last_scan 덮어쓰기.
    반환: (filtered_records, skipped_count).
    """
    state_path = Path(state_dir) / "last_scan.json" if state_dir else None
    prev_keys: set = set()
    if mode == "incremental" and state_path and state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            prev_keys = set(data.get("issue_keys", []))
        except Exception:
            prev_keys = set()

    filtered = records
    skipped = 0
    if mode == "incremental" and prev_keys:
        filtered = [r for r in records if r.get("sonar_issue_key") not in prev_keys]
        skipped = len(records) - len(filtered)

    # 새 last_scan 기록 (full/incremental 무관 — 다음 incremental 실행이 이 snapshot 기준)
    if state_path:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "issue_keys": sorted({r.get("sonar_issue_key") for r in records if r.get("sonar_issue_key")}),
                "snapshot_size": len(records),
            }
            state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[WARN] last_scan.json 기록 실패: {e}", file=sys.stderr)

    return (filtered, skipped)


def _get_code_lines(host: str, headers: dict, component: str, target_line: int) -> str:
    """
    이슈가 발생한 소스 코드의 전후 50줄(총 101줄)을 텍스트로 추출합니다.

    SonarQube /api/sources/lines 엔드포인트에서 코드를 가져오며,
    이슈 발생 라인에 ">>" 마커를 붙여 LLM이 문제 지점을 쉽게 식별할 수 있도록 합니다.

    SonarQube가 반환하는 코드에는 구문 강조용 HTML 태그가 포함되어 있으므로
    _clean_html_tags()로 제거합니다.

    Args:
        host: SonarQube 호스트 URL
        headers: Basic Auth 인증 헤더
        component: 파일 컴포넌트 키 (예: "myproject:src/main/App.java")
        target_line: 이슈가 발생한 라인 번호

    Returns:
        str: 줄번호가 포함된 코드 텍스트 (이슈 라인에 ">>" 표시)
             조회 실패 시 빈 문자열 반환
    """
    if target_line <= 0 or not component: return ""

    # 이슈 발생 라인 전후 50줄을 요청합니다.
    start = max(1, target_line - 50)
    end = target_line + 50

    url = _api_url(host, "/api/sources/lines", {"key": component, "from": start, "to": end})
    try:
        resp = _http_get_json(url, headers)
        sources = resp.get("sources", [])
        if not sources: return ""

        out = []
        for src in sources:
            ln = src.get("line", 0)
            raw_code = src.get("code", "")

            # SonarQube가 구문 강조용으로 삽입한 HTML 태그를 제거합니다.
            # (예: <span class="k">public</span> → public)
            code = _clean_html_tags(raw_code)

            # 이슈 발생 라인에 ">>" 마커를 붙여 시각적으로 구분합니다.
            marker = ">> " if ln == target_line else "   "
            # 한 줄이 너무 길면 잘라냅니다 (LLM 토큰 절약).
            if len(code) > 400: code = code[:400] + " ...[TRUNCATED]"
            out.append(f"{marker}{ln:>5} | {code}")
        return "\n".join(out)
    except:
        return ""

def main():
    """
    메인 실행 함수: SonarQube에서 미해결 이슈를 전수 조회하고 코드/룰 정보로 보강합니다.

    [전체 처리 흐름]
    1. CLI 인자 파싱 (SonarQube 접속 정보, 프로젝트 키 등)
    2. /api/issues/search로 미해결 이슈 목록을 페이지네이션하여 전수 조회
    3. 각 이슈에 대해:
       a. 위반 규칙 상세 정보 조회 (동일 규칙은 캐싱하여 중복 호출 방지)
       b. 이슈 발생 위치의 소스 코드 전후 50줄 조회
       c. 모든 정보를 하나의 enriched 객체로 통합
    4. 전체 결과를 sonar_issues.json 파일로 저장
    """
    # ---------------------------------------------------------------
    # [1단계] CLI 인자 파싱
    # ---------------------------------------------------------------
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)   # SonarQube 호스트 URL
    ap.add_argument("--sonar-token", required=True)       # SonarQube 인증 토큰
    ap.add_argument("--project-key", required=True)       # 분석 대상 프로젝트 키
    ap.add_argument("--output", default="sonar_issues.json")  # 출력 파일 경로
    ap.add_argument("--severities", default="")           # 심각도 필터 (미사용, 하위 호환)
    ap.add_argument("--statuses", default="")             # 상태 필터 (미사용, 하위 호환)
    ap.add_argument("--sonar-public-url", default="")     # 외부 접근용 URL (미사용, 하위 호환)
    # Step R 신규 — GitLab Issue 본문 렌더 및 RAG 검색의 스냅샷 고정에 사용.
    # 03 Jenkinsfile 이 git ls-remote 로 해석해 전달 (Phase 1.5 정식 도입 전 임시).
    ap.add_argument("--commit-sha", default="")
    # Step R 신규 — enclosing_function 추출용 레포 루트. 보통 /var/knowledges/codes/<repo>.
    # 비어있으면 enclosing_function 생략 (파이프라인은 정상 동작).
    ap.add_argument("--repo-root", default="")
    # Step B 신규 — diff-mode, state, callgraph 경로.
    ap.add_argument("--mode", choices=["full", "incremental"], default="full",
                    help="full: last_scan 리셋 후 전수 emit. incremental: 이전 스냅샷과 diff.")
    ap.add_argument("--state-dir", default="/var/knowledges/state",
                    help="last_scan.json 등 스캐너 상태 저장 경로.")
    ap.add_argument("--callgraph-dir", default="/var/knowledges/docs/result",
                    help="P1 이 남긴 JSONL 청크 디렉터리 — direct_callers 역인덱스 소스.")
    ap.add_argument("--disable-clustering", action="store_true",
                    help="같은 rule+function+dir 이슈를 대표 1건으로 합치지 않고 개별 emit.")
    args, _ = ap.parse_known_args()

    # SonarQube API 인증 헤더 (Basic Auth)
    headers = {"Authorization": _build_basic_auth(args.sonar_token)}

    # ---------------------------------------------------------------
    # [2단계] 미해결 이슈 전수 조회 (페이지네이션)
    # SonarQube는 한 번에 최대 100건까지 반환하므로,
    # 전체 이슈를 가져오려면 page를 증가시키며 반복 호출해야 합니다.
    # ---------------------------------------------------------------
    issues = []
    p = 1
    while True:
        query = {
            "componentKeys": args.project_key,  # 조회 대상 프로젝트
            "resolved": "false",                # 미해결 이슈만 조회
            "p": p, "ps": 100,                  # 페이지 번호 / 페이지 크기
            "additionalFields": "_all"          # 모든 부가 정보 포함
        }
        if args.severities.strip():
            query["severities"] = args.severities.strip()
        if args.statuses.strip():
            query["statuses"] = args.statuses.strip()
        url = _api_url(args.sonar_host_url, "/api/issues/search", query)
        try:
            res = _http_get_json(url, headers)
            items = res.get("issues", [])
            issues.extend(items)
            # 더 이상 가져올 이슈가 없거나 전체 수에 도달하면 루프 종료
            if not items or p * 100 >= res.get("paging", {}).get("total", 0): break
            p += 1
        except: break

    print(f"[INFO] Processing {len(issues)} issues...", file=sys.stderr)

    # Step B — callgraph 역인덱스 최초 1회 로드 (symbol → callers 리스트)
    cg_index = _load_callgraph_index(args.callgraph_dir)
    if cg_index:
        print(f"[INFO] callgraph index loaded: {len(cg_index)} callees", file=sys.stderr)

    # ---------------------------------------------------------------
    # [3단계] 각 이슈에 대해 룰 정보 + 소스 코드 보강(Enrichment)
    # ---------------------------------------------------------------
    enriched = []
    # 동일한 규칙 키에 대한 중복 API 호출을 방지하는 캐시입니다.
    # 프로젝트에서 같은 규칙 위반이 수십~수백 건 발생할 수 있기 때문입니다.
    rule_cache = {}

    for issue in issues:
        key = issue.get("key")              # SonarQube 이슈 고유 키
        rule_key = issue.get("rule")        # 위반 규칙 ID
        component = issue.get("component")  # 파일 컴포넌트 키

        # 이슈 발생 라인 번호 추출 (두 가지 위치 표현 방식을 모두 지원)
        line = issue.get("line")
        if not line and "textRange" in issue:
            line = issue["textRange"].get("startLine")
        line = int(line) if line else 0

        # --- 3-a. 위반 규칙 상세 정보 조회 (캐싱) ---
        if rule_key not in rule_cache:
            rule_cache[rule_key] = _get_rule_details(args.sonar_host_url, headers, rule_key)

        # --- 3-b. 이슈 발생 위치의 소스 코드 조회 ---
        snippet = _get_code_lines(args.sonar_host_url, headers, component, line)
        if not snippet: snippet = "(Code not found in SonarQube)"

        # --- 3-c. Step R: 위치 메타 보강 (relative_path + enclosing_function) ---
        rel_path = _relative_path_from_component(component, args.project_key)
        enclosing_symbol, enclosing_lines = _enclosing_function(
            args.repo_root, rel_path, line
        )

        # --- 3-d. Step B: git context + direct_callers + severity routing + cluster_key ---
        git_ctx = _git_context(args.repo_root, rel_path, line)
        callers = _direct_callers(cg_index, enclosing_symbol)
        severity = (issue.get("severity") or rule_cache[rule_key].get("severity", "") or "").upper()
        judge_model, skip_llm = _classify_severity(severity)
        cluster_k = _cluster_key(rule_key, enclosing_symbol, component)

        # --- 3-e. 통합 객체 생성 ---
        # 이 객체가 dify_sonar_issue_analyzer.py의 입력으로 사용됩니다.
        enriched.append({
            "sonar_issue_key": key,           # 이슈 고유 키
            "sonar_rule_key": rule_key,       # 위반 규칙 ID
            "sonar_project_key": args.project_key,  # 프로젝트 키
            "sonar_issue_url": f"{args.sonar_host_url}/project/issues?id={args.project_key}&issues={key}&open={key}",  # SonarQube 이슈 직링크
            "issue_search_item": issue,       # /api/issues/search 원본 응답 항목
            "rule_detail": rule_cache[rule_key],  # 규칙 상세 (이름, 설명, 심각도)
            "code_snippet": snippet,          # 이슈 전후 소스 코드 (">>" 마커 포함)
            "component": component,           # 파일 컴포넌트 키
            # Step R 신규 필드 — creator 의 deterministic 렌더에 사용
            "relative_path": rel_path,        # 예: "src/auth.py"
            "line": line,                     # 정수 라인 번호
            "enclosing_function": enclosing_symbol,   # 예: "login" (tree-sitter, 실패 시 "")
            "enclosing_lines": enclosing_lines,       # 예: "22-27"
            "commit_sha": args.commit_sha,            # 빈 문자열이면 본문에 commit 섹션 생략
            # Step B 신규 필드 — P3 LLM 프롬프트 + clustering + skip_llm 분기
            "git_context": git_ctx,                   # "blame L24: alice (abc123)" 등 3줄
            "direct_callers": callers,                # 최대 10개. fs-based callgraph.
            "cluster_key": cluster_k,                 # sha1 앞 16글자
            "judge_model": judge_model,               # "qwen3-coder:30b" / "gemma4:e4b" / "skip_llm"
            "skip_llm": skip_llm,                     # True 면 analyzer 가 Dify 호출 생략
            "severity": severity,                     # clustering/creator 용 top-level 복사
            # affected_locations 은 _apply_clustering 에서 채움 (대표 1건만 비지 않음)
            "affected_locations": [],
        })

    # ---------------------------------------------------------------
    # [4단계] Step B — Clustering + diff-mode
    # ---------------------------------------------------------------
    # (a) Clustering: 같은 rule+function+dir 이슈 → 대표 1건 + affected_locations 리스트
    pre_cluster = len(enriched)
    if args.disable_clustering:
        clustered = enriched
        print(f"[INFO] clustering disabled: {pre_cluster} issues kept as-is", file=sys.stderr)
    else:
        clustered = _apply_clustering(enriched)
        cluster_reduced = pre_cluster - len(clustered)
        if cluster_reduced > 0:
            print(f"[INFO] clustering: {pre_cluster} → {len(clustered)} ({cluster_reduced} merged into affected_locations)", file=sys.stderr)

    # (b) Diff-mode: last_scan 과 비교해 이미 본 이슈는 skip (incremental) + snapshot 갱신
    filtered, skipped = _diff_mode_filter(clustered, args.state_dir, args.mode)
    if skipped > 0:
        print(f"[diff-mode] skipped {skipped} cached issues (mode={args.mode})", file=sys.stderr)

    # ---------------------------------------------------------------
    # [5단계] 결과 저장
    # 전체 enriched 이슈를 하나의 JSON 파일로 저장합니다.
    # 다음 단계(dify_sonar_issue_analyzer.py)가 이 파일을 입력으로 사용합니다.
    # ---------------------------------------------------------------
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"issues": filtered}, f, ensure_ascii=False, indent=2)

    print(f"[OK] Exported {len(filtered)} issues (from {len(enriched)} pre-cluster, {skipped} skipped by diff-mode).", file=sys.stdout)

if __name__ == "__main__":
    main()
