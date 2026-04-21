#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# 파일명: gitlab_issue_creator.py
# 버전: 1.1
#
# [시스템 개요]
# 이 스크립트는 품질 분석 파이프라인(Phase 3)의 **3단계(이슈 관리 시스템 자동 등록)**를 담당합니다.
# dify_sonar_issue_analyzer.py가 생성한 LLM 분석 결과(JSONL)를 읽어,
# 각 이슈를 GitLab 프로젝트의 이슈 트래커에 자동으로 생성합니다.
#
# [파이프라인 내 위치]
# dify_sonar_issue_analyzer.py (AI 분석)
#       ↓ llm_analysis.jsonl
# >>> gitlab_issue_creator.py (이슈 등록) <<<
#       ↓ gitlab_issues_created.json (등록 결과 요약)
#
# [핵심 기능]
# 1. JSONL 파일에서 분석 결과를 한 줄씩 읽어 GitLab 이슈로 변환합니다.
# 2. 이슈 제목은 "[심각도] 이슈메시지" 포맷으로 통일합니다.
# 3. SonarQube 내부 URL을 외부 접근 가능한 URL로 치환합니다.
# 4. 동일 SonarQube 이슈 키로 이미 등록된 이슈가 있으면 중복 생성을 방지합니다.
# 5. 생성/건너뜀/실패 결과를 JSON 파일로 저장하여 파이프라인 추적을 지원합니다.
#
# [실행 예시]
# python3 gitlab_issue_creator.py \
#   --gitlab-host-url http://gitlab:8929 \
#   --gitlab-token glpat-xxxxx \
#   --gitlab-project mygroup/myproject \
#   --input llm_analysis.jsonl \
#   --sonar-public-url http://localhost:9000
# ==================================================================================

import argparse
import base64
import json
import sys
import time
import re
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_post_form(url: str, headers: dict, form: dict, timeout: int = 60):
    """
    URL-encoded 폼 데이터를 POST로 전송합니다.

    GitLab Issues API는 JSON 대신 application/x-www-form-urlencoded을
    사용하는 것이 안정적이므로, 폼 인코딩 방식으로 전송합니다.

    Args:
        url: GitLab API 엔드포인트
        headers: PRIVATE-TOKEN이 포함된 인증 헤더
        form: 전송할 폼 데이터 (title, description, labels 등)
        timeout: 요청 타임아웃 (초)

    Returns:
        tuple: (HTTP 상태 코드, 응답 본문 문자열)
    """
    data = urlencode(form, doseq=True).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, headers=h, method="POST", data=data)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    HTTP GET 요청을 보내고 JSON 응답을 파싱하여 반환합니다.

    GitLab Issues 검색 API 호출에 사용됩니다 (중복 이슈 확인 등).
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _replace_sonar_url(text: str, sonar_host_url: str, sonar_public_url: str) -> str:
    """
    LLM이 생성한 마크다운 설명 내의 SonarQube URL을 외부 접근 가능한 URL로 치환합니다.

    컨테이너 내부 URL 과 LLM 이 임의 재구성한 변형 (예: 포트는 내부값 그대로 인데
    호스트명만 localhost 로 바꾼 경우) 을 모두 잡아 실제 사용자가 접근 가능한
    public URL (예: docker-compose 호스트 매핑 포트) 로 정규화합니다.

    치환 대상 패턴 (모두 동일 public base 로 치환):
      1. sonar_host_url 파라미터 (예: http://127.0.0.1:9000, http://sonarqube:9000)
      2. 흔한 내부 hostname 변형: http://sonarqube:9000, http://127.0.0.1:9000, http://localhost:9000
         (LLM 이 context 기반 재구성 시 흔히 생성)
      3. 호스트명 없이 상대경로로 시작하는 '/project/issues?' → public host 붙임

    Args:
        text: LLM이 생성한 마크다운 텍스트 (이슈 설명)
        sonar_host_url: Jenkins에서 사용하는 SonarQube 내부 URL
        sonar_public_url: 사용자가 접근 가능한 SonarQube 외부 URL

    Returns:
        URL이 치환된 텍스트
    """
    if not text: return text
    target_base = (sonar_public_url or "http://localhost:9000").rstrip("/")
    # 명시된 내부 URL + 흔한 변형 모두 정규화
    internal_variants = set()
    if sonar_host_url:
        internal_variants.add(sonar_host_url.rstrip("/"))
    # LLM 이 자주 재구성하는 변형 — hostname 만 바꾸고 내부 포트 그대로 쓰는 경우
    internal_variants.update([
        "http://sonarqube:9000",
        "http://127.0.0.1:9000",
        "http://localhost:9000",
    ])
    # 자기 자신으로 치환하지 않도록 target 은 제외
    internal_variants.discard(target_base)
    for variant in internal_variants:
        text = text.replace(variant, target_base)
    # 상대경로 형태의 SonarQube 링크에 호스트를 붙여줍니다.
    # lookbehind로 이미 http: 또는 https:가 앞에 있는 경우는 제외합니다.
    pattern = r"(?<!http:)(?<!https:)(?<![a-zA-Z0-9])(/project/issues\?)"
    text = re.sub(pattern, f"{target_base}\\1", text)
    return text


def _gitlab_blob_url(public_base: str, project: str, branch: str, path: str, line: int) -> str:
    """GitLab 파일 직접 링크 구성. 라인 앵커 포함.

    예: http://localhost:28090/root/dscore-ttc-sample/-/blob/main/src/auth.py#L24
    """
    if not public_base or not project or not path:
        return ""
    base = public_base.rstrip("/")
    anchor = f"#L{line}" if isinstance(line, int) and line > 0 else ""
    return f"{base}/{project}/-/blob/{branch or 'main'}/{path}{anchor}"


def _gitlab_commit_url(public_base: str, project: str, sha: str) -> str:
    """GitLab commit 링크 구성.

    예: http://localhost:28090/root/dscore-ttc-sample/-/commit/abc1234
    """
    if not public_base or not project or not sha:
        return ""
    base = public_base.rstrip("/")
    return f"{base}/{project}/-/commit/{sha}"


def _short_sha(sha: str, n: int = 8) -> str:
    return sha[:n] if sha else ""


def _sonar_mark_fp(sonar_host: str, sonar_token: str, issue_key: str, timeout: int = 30) -> tuple:
    """Step D — SonarQube `POST /api/issues/do_transition` 으로 이슈를 false positive 로 마킹.

    Community Edition 에서 동작 여부가 환경마다 다르므로 실패해도 파이프라인은
    계속 (Dual-path 설계). 반환: `(success: bool, err_msg: str)`.

    Auth: Basic auth with token as username, empty password (Sonar 표준).
    """
    if not sonar_host or not sonar_token or not issue_key:
        return (False, "missing sonar_host / token / issue_key")
    url = f"{sonar_host.rstrip('/')}/api/issues/do_transition"
    form = urlencode({"issue": issue_key, "transition": "falsepositive"}).encode("utf-8")
    auth = "Basic " + base64.b64encode(f"{sonar_token}:".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": auth,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = Request(url, method="POST", headers=headers, data=form)
    try:
        with urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return (True, "")
            body = resp.read().decode("utf-8", errors="replace")
            return (False, f"HTTP {resp.status} · {body[:200]}")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return (False, f"HTTP {e.code} · {body[:200]}")
    except URLError as e:
        return (False, f"URLError: {e}")
    except Exception as e:
        return (False, f"Exception: {e}")


def _merge_labels(llm_labels, severity: str, classification: str, confidence: str,
                  llm_skipped: bool, extra: list = None) -> str:
    """Step D — labels 조립. LLM 제안 + Step B/C 필드 + Step D 경고 라벨 병합 + 중복 제거.

    반환: "labelA,labelB,..." 문자열 (GitLab Issues API form 포맷).
    """
    merged = []
    if isinstance(llm_labels, list):
        merged.extend(str(x) for x in llm_labels if x)
    elif llm_labels:
        merged.append(str(llm_labels))

    if severity:
        merged.append(f"severity:{severity}")
    if classification:
        merged.append(f"classification:{classification}")
    if confidence:
        merged.append(f"confidence:{confidence}")
    if llm_skipped:
        merged.append("auto_template:true")
    if extra:
        merged.extend(extra)

    # 중복 제거 (순서 보존)
    seen = set()
    out = []
    for lbl in merged:
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return ",".join(out)


def _trim_code_snippet(snippet: str, target_line: int, context: int = 10) -> str:
    """exporter 가 ±50줄로 가져온 스니펫을 이슈 라인 ±context 줄로 축소.

    스니펫 라인 포맷: "{marker}{lineno:>5} | {code}" (sonar_issue_exporter 가 생성).
    본문 가독성을 위해 전체 101줄을 21줄 내외로 줄인다.
    """
    if not snippet:
        return snippet
    lines = snippet.splitlines()
    if not lines:
        return snippet
    # target_line 포함 라인 index 찾기 (lineno 파싱)
    target_idx = None
    for i, ln in enumerate(lines):
        try:
            # "   24 | code" 에서 24 뽑기
            parts = ln.split("|", 1)[0].strip().split()
            num = int(parts[-1])
            if num == target_line:
                target_idx = i
                break
        except Exception:
            continue
    if target_idx is None:
        return snippet  # 라인 못 찾으면 원본 유지
    lo = max(0, target_idx - context)
    hi = min(len(lines), target_idx + context + 1)
    return "\n".join(lines[lo:hi])


def render_issue_body(row: dict, args) -> str:
    """Step R 에서 도입된 deterministic 본문 렌더링.

    구조 (모두 섹션 제목에 이모지):
      1. TL;DR callout
      2. 📍 위치 테이블
      3. 🔴 문제 코드
      4. ✅ 수정 제안 (LLM, optional — 빈 문자열이면 섹션 생략)
      5. 📊 영향 분석 (LLM)
      6. 📖 Rule 상세 (<details> 접기)
      7. 🔗 링크 섹션

    LLM 출력 (`outputs`) 은 `impact_analysis_markdown`, `suggested_fix_markdown`
    두 필드. 나머지는 creator 가 row 의 사실 정보로 직접 렌더.
    """
    outputs = row.get("outputs") or {}
    rel_path = row.get("relative_path", "") or ""
    line = row.get("line") or 0
    try:
        line_int = int(line) if line else 0
    except Exception:
        line_int = 0
    enclosing_fn = row.get("enclosing_function", "") or ""
    enclosing_ln = row.get("enclosing_lines", "") or ""
    rule_key = row.get("rule_key", "") or ""
    rule_name = row.get("rule_name", "") or ""
    rule_desc = row.get("rule_description", "") or ""
    severity = row.get("severity", "") or ""
    commit_sha = row.get("commit_sha", "") or ""
    sonar_msg = row.get("sonar_message", "") or ""
    sonar_issue_url = row.get("sonar_issue_url", "") or ""

    # URL 구성 (public url 치환 포함)
    sonar_public_url = _replace_sonar_url(
        sonar_issue_url, args.sonar_host_url, args.sonar_public_url
    )
    blob_url = _gitlab_blob_url(
        args.gitlab_public_url, args.gitlab_project, args.gitlab_branch,
        rel_path, line_int,
    )
    commit_url = _gitlab_commit_url(
        args.gitlab_public_url, args.gitlab_project, commit_sha
    )

    # 1. TL;DR
    location_hint = f"`{rel_path}:{line_int}`" if rel_path and line_int else "이슈 위치"
    fn_hint = f" `{enclosing_fn}` 함수" if enclosing_fn else ""
    tldr = f"> **TL;DR** — {location_hint}{fn_hint} · {sonar_msg or rule_name}"

    # 2. 위치 테이블
    file_cell = f"[`{rel_path}:{line_int}`]({blob_url})" if blob_url else (
        f"`{rel_path}:{line_int}`" if rel_path else "(unknown)"
    )
    fn_cell = f"`{enclosing_fn}`" + (f" *(line {enclosing_ln})*" if enclosing_ln else "") if enclosing_fn else "—"
    rule_cell = f"`{rule_key}`" + (f" · {rule_name}" if rule_name else "")
    commit_cell = f"[`{_short_sha(commit_sha)}`]({commit_url})" if commit_url and commit_sha else (
        f"`{_short_sha(commit_sha)}`" if commit_sha else "—"
    )
    location_table = (
        "### 📍 위치\n\n"
        "| 항목 | 값 |\n"
        "|------|-----|\n"
        f"| 파일 | {file_cell} |\n"
        f"| 함수 | {fn_cell} |\n"
        f"| Rule | {rule_cell} |\n"
        f"| Severity | `{severity or '—'}` |\n"
        f"| Commit | {commit_cell} |"
    )

    # 3. 문제 코드
    snippet = _trim_code_snippet(
        row.get("code_snippet", "") or "", line_int, context=10
    )
    if snippet and snippet != "(Code not found in SonarQube)":
        code_section = (
            "### 🔴 문제 코드\n\n"
            "```\n"
            f"{snippet}\n"
            "```"
        )
    else:
        code_section = ""

    # 4. 수정 제안 (optional) — LLM 이 코드펜스 없이 코드만 준 경우 자동으로 감싸 가독성 유지.
    suggested_fix = (outputs.get("suggested_fix_markdown") or "").strip()
    fix_section = ""
    if suggested_fix:
        # 코드펜스(```) 없으면 python/diff 로 자동 감싸기 (LLM 의 흔한 실수 보완).
        if "```" not in suggested_fix:
            lines = suggested_fix.splitlines()
            # 간단 휴리스틱: diff 마커(+/-) 가 라인 시작에 2개 이상이면 diff.
            diff_count = sum(1 for ln in lines if ln.startswith(("+ ", "- ", "+", "-")))
            lang = "diff" if diff_count >= 2 else "python"
            suggested_fix = f"```{lang}\n{suggested_fix}\n```"
        fix_section = f"### ✅ 수정 제안\n\n{suggested_fix}"

    # 4-b. Step D — Suggested Diff (unified diff, optional) — suggested_fix_markdown 과 별개로
    # 기계 적용 가능한 diff 가 있을 때만 추가. LLM 이 비워두면 섹션 생략.
    suggested_diff = (outputs.get("suggested_diff") or "").strip()
    diff_section = ""
    if suggested_diff and suggested_diff.lower() not in ("", "null", "none"):
        # 이미 코드펜스 감싸져 있으면 그대로, 아니면 diff 펜스로 감싸기.
        if "```" not in suggested_diff:
            suggested_diff = f"```diff\n{suggested_diff}\n```"
        diff_section = f"### 💡 Suggested Diff\n\n{suggested_diff}"

    # 5. 영향 분석 (required from LLM)
    impact = (outputs.get("impact_analysis_markdown") or "").strip()
    if impact:
        impact_section = f"### 📊 영향 분석\n\n{impact}"
    else:
        impact_section = "### 📊 영향 분석\n\n_(LLM 이 영향 분석을 제공하지 않음)_"

    # 6. Rule 상세 (접기)
    rule_section = ""
    if rule_desc:
        rule_desc_safe = rule_desc.strip()
        rule_section = (
            "### 📖 Rule 상세\n\n"
            f"<details><summary>{rule_key or 'Rule'} 전체 설명</summary>\n\n"
            f"{rule_desc_safe}\n\n"
            "</details>"
        )

    # 7. Step D — Affected Locations (같은 cluster 의 나머지 이슈). row.affected_locations
    # 가 비어 있으면 섹션 생략 (단일 이슈). Step B exporter 가 채움.
    affected = row.get("affected_locations") or []
    aff_section = ""
    if affected:
        rows_md = ["| component | line | sonar key |", "|-----------|------|-----------|"]
        for a in affected[:20]:  # 본문이 너무 길어지지 않도록 상한
            comp = a.get("component") or a.get("relative_path") or ""
            lno = a.get("line") or ""
            skey = a.get("sonar_issue_key") or ""
            rows_md.append(f"| `{comp}` | {lno} | `{skey}` |")
        aff_section = "### 🧭 Affected Locations\n\n" + "\n".join(rows_md)

    # 8. 링크 섹션
    link_lines = []
    if sonar_public_url:
        link_lines.append(f"- [SonarQube 이슈 상세]({sonar_public_url})")
    if blob_url:
        line_suffix = f" (line {line_int})" if line_int else ""
        link_lines.append(f"- [GitLab 파일{line_suffix}]({blob_url})")
    if commit_url:
        link_lines.append(f"- [GitLab 커밋 `{_short_sha(commit_sha)}`]({commit_url})")
    link_section = ""
    if link_lines:
        link_section = "### 🔗 링크\n\n" + "\n".join(link_lines)

    # 9. Step D — footer: commit 추적용 메타 한 줄. dedup 에서도 활용.
    analysis_mode = getattr(args, "analysis_mode", "") or ""
    scan_label = f" ({analysis_mode} scan)" if analysis_mode else ""
    footer_parts = []
    if commit_sha:
        footer_parts.append(f"commit: `{_short_sha(commit_sha)}`{scan_label}")
    if sonar_public_url:
        footer_parts.append(f"sonar: {sonar_public_url}")
    footer = ""
    if footer_parts:
        footer = "---\n_" + " · ".join(footer_parts) + "_"

    # 조립 (섹션 사이 빈 줄 유지)
    sections = [tldr, location_table]
    for s in (code_section, fix_section, diff_section, impact_section, aff_section, rule_section, link_section):
        if s:
            sections.append(s)
    body = "\n\n---\n\n".join(sections)
    if footer:
        body = body + "\n\n" + footer
    # LLM 본문에 섞여 나올 수 있는 내부 URL 을 public 으로 최종 한 번 더 정규화
    body = _replace_sonar_url(body, args.sonar_host_url, args.sonar_public_url)
    return body


def _find_existing_by_sonar_key(gitlab_host_url: str, headers: dict, project: str, key: str) -> bool:
    """
    GitLab에서 동일한 SonarQube 이슈 키로 이미 등록된 이슈가 있는지 검색합니다.

    중복 이슈 생성을 방지하기 위한 핵심 함수입니다.
    파이프라인을 반복 실행해도 같은 이슈가 여러 번 등록되지 않습니다.

    Args:
        gitlab_host_url: GitLab 호스트 URL
        headers: PRIVATE-TOKEN 인증 헤더
        project: GitLab 프로젝트 경로 (예: "mygroup/myproject")
        key: SonarQube 이슈 고유 키

    Returns:
        bool: 기존 이슈가 존재하면 True, 없으면 False
    """
    if not key: return False
    url = f"{gitlab_host_url.rstrip('/')}/api/v4/projects/{quote(project, safe='')}/issues?search={key}"
    try:
        arr = _http_get_json(url, headers)
        return isinstance(arr, list) and len(arr) > 0
    except Exception:
        return False

def main() -> int:
    """
    메인 실행 함수: LLM 분석 결과를 읽어 GitLab 이슈를 자동 생성합니다.

    [전체 처리 흐름]
    1. CLI 인자 파싱 (GitLab 접속 정보, SonarQube URL 매핑 등)
    2. llm_analysis.jsonl에서 분석 결과를 한 줄씩 로드
    3. 각 분석 결과에 대해:
       a. 이슈 제목 구성: "[심각도] SonarQube메시지" 포맷 (메시지가 없으면 LLM 제목 사용)
       b. 설명 내 SonarQube URL을 외부 접근 가능 URL로 치환
       c. GitLab에서 동일 이슈 키로 중복 검색 → 이미 있으면 건너뜀
       d. GitLab Issues API로 이슈 생성 (LLM이 제안한 labels 포함)
    4. 생성/건너뜀/실패 결과를 JSON 파일로 저장

    Returns:
        int: 실패한 이슈가 있으면 2, 없으면 0 (Jenkins 빌드 상태에 반영)
    """
    # ---------------------------------------------------------------
    # [1단계] CLI 인자 파싱
    # ---------------------------------------------------------------
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitlab-host-url", required=True)   # GitLab 내부 호스트 URL (API 호출용)
    ap.add_argument("--gitlab-token", required=True)       # GitLab Personal Access Token
    ap.add_argument("--gitlab-project", required=True)     # 대상 프로젝트 경로
    ap.add_argument("--input", default="llm_analysis.jsonl")     # 입력 파일 (LLM 분석 결과)
    ap.add_argument("--output", default="gitlab_issues_created.json")  # 결과 요약 파일
    ap.add_argument("--sonar-host-url", default="")        # SonarQube 내부 URL (치환 원본)
    ap.add_argument("--sonar-public-url", default="")      # SonarQube 외부 URL (치환 대상)
    ap.add_argument("--timeout", type=int, default=60)     # API 요청 타임아웃 (초)
    # Step R 신규 — 본문 blob/commit 링크용 public URL.
    # 기본값은 docker-compose 호스트 매핑 (28090:80).
    ap.add_argument("--gitlab-public-url", default="http://localhost:28090")
    ap.add_argument("--gitlab-branch", default="main")
    ap.add_argument("--commit-sha", default="")            # row 에 없을 때 fallback
    # Step D — FP 전이 API 호출용 Sonar 토큰. Jenkins credential 에서 주입.
    # 빈 값이면 FP 전이는 시도 자체를 skip (GitLab Issue 는 정상 생성).
    ap.add_argument("--sonar-token", default="")
    # Step D — footer 의 `(<mode> scan)` 표기. 체인 Job 이 전달.
    ap.add_argument("--analysis-mode", default="")
    args = ap.parse_args()

    # GitLab API 인증 헤더
    headers = {"PRIVATE-TOKEN": args.gitlab_token}

    # 처리 결과를 여러 카테고리로 분류 — Step D Dual-path FP 집계 포함.
    created, skipped, failed = [], [], []
    fp_transitioned = []        # Sonar 전이 성공 → GitLab Issue 미생성
    fp_transition_failed = []   # 전이 실패 → GitLab Issue 는 생성 (라벨로 구분)
    rows = []

    # ---------------------------------------------------------------
    # [2단계] JSONL 입력 파일 로드
    # 각 줄이 하나의 JSON 객체이므로 줄 단위로 파싱합니다.
    # ---------------------------------------------------------------
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): rows.append(json.loads(line))
    except Exception as e:
        print(f"[ERROR] Read failed: {e}", file=sys.stderr)
        return 2

    # ---------------------------------------------------------------
    # [3단계] 각 분석 결과를 순회하며 GitLab 이슈 생성
    # ---------------------------------------------------------------
    for row in rows:
        sonar_key = row.get("sonar_issue_key")
        outputs = row.get("outputs") or {}  # Dify 워크플로우가 생성한 LLM 출력

        # --- 3-a. 이슈 제목 결정 ---
        # SonarQube 원본 메시지를 최우선으로 사용. 없으면 LLM 제목 사용.
        msg = row.get("sonar_message") or ""
        llm_title = outputs.get("title") or ""
        main_title = msg if msg else llm_title

        # 심각도 태그를 제목 앞에 붙여 시각적으로 우선순위를 구분합니다.
        severity = row.get("severity") or ""
        final_title = f"[{severity}] {main_title}" if severity else main_title

        # Step D — classification/confidence 는 LLM outputs 에서 추출 (analyzer 가 기본값 주입).
        classification = (outputs.get("classification") or "true_positive").lower()
        confidence = (outputs.get("confidence") or "").lower()
        llm_skipped = bool(row.get("llm_skipped"))

        # --- 3-b. Step D: Dual-path FP 처리 ---
        # classification == "false_positive" → Sonar 전이 API 먼저 시도.
        # 성공: GitLab Issue 생성 skip, 집계에 기록.
        # 실패(또는 --sonar-token 비어있음): GitLab Issue 는 생성하되 `fp_transition_failed` 라벨 추가.
        extra_labels = []
        if classification == "false_positive":
            ok, err = _sonar_mark_fp(args.sonar_host_url, args.sonar_token, sonar_key)
            if ok:
                print(f"[FP-TRANSITION] {sonar_key} → Sonar marked as false positive")
                fp_transitioned.append({"key": sonar_key, "title": final_title})
                continue
            print(f"[FP-TRANSITION:FAIL] {sonar_key} — {err} — GitLab Issue 는 생성합니다")
            fp_transition_failed.append({"key": sonar_key, "err": err})
            extra_labels.append("fp_transition_failed")

        # --- 3-c. deterministic 본문 렌더 (Step R + Step D 확장 포함) ---
        # row 에 --commit-sha (CLI) 가 비어있으면 덮어쓰기
        if not row.get("commit_sha"):
            row["commit_sha"] = args.commit_sha or ""
        desc = render_issue_body(row, args)

        # 제목이 비어있으면 생성 불가. 본문은 render 가 최소 섹션은 보장 (위치 + 영향).
        if not final_title:
            failed.append({"key": sonar_key, "reason": "Empty title"})
            continue

        # --- 3-d. 중복 이슈 검사 ---
        if _find_existing_by_sonar_key(args.gitlab_host_url, headers, args.gitlab_project, sonar_key):
            skipped.append({"key": sonar_key, "title": final_title, "reason": "Dedup"})
            continue

        # --- 3-e. GitLab 이슈 생성 ---
        form = {"title": final_title, "description": desc}
        form["labels"] = _merge_labels(
            llm_labels=outputs.get("labels"),
            severity=severity,
            classification=classification,
            confidence=confidence,
            llm_skipped=llm_skipped,
            extra=extra_labels,
        )

        url = f"{args.gitlab_host_url.rstrip('/')}/api/v4/projects/{quote(args.gitlab_project, safe='')}/issues"
        try:
            status, body = _http_post_form(url, headers, form, args.timeout)
            if status in (200, 201):
                created.append({"key": sonar_key, "title": final_title})
            else:
                failed.append({"key": sonar_key, "status": status, "body": body})
        except Exception as e:
            failed.append({"key": sonar_key, "err": str(e)})

    # ---------------------------------------------------------------
    # [4단계] 결과 요약 파일 저장
    # Jenkins 콘솔과 아티팩트에서 처리 현황을 확인할 수 있습니다.
    # ---------------------------------------------------------------
    summary = {
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "fp_transitioned": fp_transitioned,
        "fp_transition_failed": fp_transition_failed,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(
        f"[OK] created={len(created)} skipped={len(skipped)} failed={len(failed)} "
        f"fp_transitioned={len(fp_transitioned)} fp_transition_failed={len(fp_transition_failed)} "
        f"output={args.output}"
    )
    # 실패 건이 있으면 종료 코드 2를 반환하여 Jenkins 빌드를 UNSTABLE/FAILURE로 표시합니다.
    return 2 if failed else 0

if __name__ == "__main__":
    sys.exit(main())