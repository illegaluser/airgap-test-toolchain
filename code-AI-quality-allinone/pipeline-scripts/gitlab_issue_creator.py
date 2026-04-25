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
#   --gitlab-host-url http://gitlab:80 \
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


# ─ Rule 설명 한글 번역 (Ollama gemma4) ────────────────────────────────────
# rule_key 기준 module-level dict 캐시. 같은 룰이 여러 이슈에서 반복돼도 1회만 번역.
# 번역 실패 / Ollama 부재 / 빈 입력 시 graceful fallback (원문 영어 그대로 반환).
# 번역 결과는 한국어로만 GitLab Issue 본문에 노출 — 원문은 SonarQube 링크에서 확인.
_rule_translation_cache: dict = {}


def _translate_rule_to_korean(rule_key: str, rule_desc: str,
                              ollama_base_url: str, ollama_model: str,
                              timeout: int = 60) -> str:
    """Sonar 룰 설명 (보통 영어) 을 한국어로 번역. 실패 시 원문 반환.

    캐시 키: rule_key — 같은 룰은 다수 이슈에서 동일 설명이라 1회 번역 후 재사용.
    Ollama API: POST {base}/api/chat (Ollama 표준).
    """
    if not rule_desc or not rule_desc.strip():
        return rule_desc
    if not ollama_base_url or not ollama_model:
        return rule_desc
    if rule_key in _rule_translation_cache:
        return _rule_translation_cache[rule_key]

    # 너무 긴 설명은 cap (Ollama 추론 시간 보호).
    src = rule_desc.strip()[:4000]
    system_msg = (
        "당신은 SonarQube 정적분석 룰 설명을 한국어로 번역하는 번역기입니다.\n"
        "규칙:\n"
        "- 코드 블록 (```...```) 과 인라인 코드 (`...`) 는 절대 변경하지 않고 그대로 유지.\n"
        "- HTML 태그가 있으면 그대로 유지하되 안의 텍스트만 번역.\n"
        "- 자연스러운 한국어 — 직역 X, 의역 OK.\n"
        "- 머리말 (예: '번역:', '한국어:'), 꼬리말, 메타 설명 모두 금지. 번역 본문만 출력.\n"
        "- '비준수 코드 예제' / '준수 솔루션' 같은 SonarQube 표준 용어는 그대로 사용."
    )
    body = {
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": src},
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
        },
    }
    url = ollama_base_url.rstrip("/") + "/api/chat"
    try:
        req = Request(
            url, method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translated = (data.get("message", {}) or {}).get("content", "").strip()
        if not translated or len(translated) < 20:
            # 번역이 너무 짧거나 빈 응답 — 실패로 간주
            _rule_translation_cache[rule_key] = rule_desc
            return rule_desc
        _rule_translation_cache[rule_key] = translated
        return translated
    except Exception as e:
        print(f"[rule-translate:WARN] {rule_key} — {e} — 원문 유지", file=sys.stderr)
        _rule_translation_cache[rule_key] = rule_desc
        return rule_desc


def _sonar_add_comment(sonar_host: str, sonar_token: str, issue_key: str,
                       text: str, timeout: int = 30) -> tuple:
    """SonarQube `POST /api/issues/add_comment` 으로 LLM fp_reason 을 코멘트로 부착.

    false_positive 전이 직전에 호출해 분석가가 Sonar UI 에서 "왜 오탐인지"
    근거를 즉시 확인할 수 있도록 한다. 전이는 별도 호출 (do_transition).
    실패해도 전이는 시도 — 코멘트는 부가 가치이지 차단 조건이 아니다.

    빈 text / 빈 token 은 silent skip (운영 환경에서 sonar-token 미주입 가능성).
    """
    if not sonar_host or not sonar_token or not issue_key or not (text or "").strip():
        return (False, "missing sonar_host / token / issue_key / text")
    url = f"{sonar_host.rstrip('/')}/api/issues/add_comment"
    form = urlencode({"issue": issue_key, "text": text.strip()}).encode("utf-8")
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


def _action_verdict(severity: str, classification: str, confidence: str,
                    cited_count: int, total_used: int) -> tuple:
    """Phase C — PM 친화 action verdict 신호등.

    Severity / classification / confidence + RAG citation 정황을 종합해
    "지금 뭘 해야 하는가" 를 한 줄로 압축. PM 이 본문 첫 줄만 봐도 우선순위
    판단 가능.

    반환: (emoji, 한 줄 라벨, 1줄 근거).
    """
    sev = (severity or "").upper()
    cls = (classification or "").lower()
    conf = (confidence or "").lower()
    no_context = total_used == 0
    weak_basis = (no_context or (cited_count == 0 and conf in ("low", ""))) and cls != "false_positive"

    # false_positive 는 우선순위 낮음 (특히 confidence high 면 자신있게 오탐)
    if cls == "false_positive":
        if conf == "high":
            return ("⚪", "오탐 가능성 (확신도 높음)",
                    "AI 가 분석 결과 SonarQube 의 오판으로 판정. 무시 또는 sonar 측 mark FP 처리.")
        return ("⚪", "오탐 가능성",
                "AI 가 오판으로 추정 — 개발자 검토 후 mark FP 또는 fix 결정.")

    if cls == "wont_fix":
        return ("🟢", "무시 가능 (기술 부채)",
                "실제 문제는 맞으나 비용 대비 우선순위 낮음. 백로그 등록 후 차후 처리.")

    # true_positive 또는 빈 값 — 심각도 + 근거에 따라 색깔.
    if sev in ("BLOCKER", "CRITICAL"):
        if weak_basis:
            return ("🟠", "즉시 수정 권장 (단, AI 근거 약함)",
                    "심각도가 높아 즉시 조치 권장. 다만 AI 답변이 일반 원칙에 의존했으니 "
                    "**개발자가 코드 컨텍스트를 직접 확인 후** 수정하세요.")
        return ("🔴", "즉시 수정",
                "심각도 높음 + AI 근거 충실. 본 이슈는 우선 처리 대상입니다.")

    if sev == "MAJOR":
        if weak_basis:
            return ("🟡", "검토 후 수정",
                    "AI 분석 근거가 약하니 개발자 검토 후 수정 여부 결정.")
        return ("🟡", "검토 후 수정",
                "중간 심각도 — 다음 스프린트 내 처리 권장.")

    # MINOR / INFO
    if sev in ("MINOR", "INFO"):
        return ("🟢", "여유 처리",
                "낮은 심각도 — 코드 정리 시 함께 처리.")

    # severity 없음 (auto_template 같은 케이스)
    return ("⚪", "추가 검토 필요",
            "심각도 정보 부재 — 개발자가 직접 우선순위 평가 권장.")


def _location_natural_text(rel_path: str, line_int: int, enclosing_fn: str,
                            enclosing_kind: str) -> str:
    """Phase C — 위치 정보를 자연어 한 줄로. PM 시야 친화."""
    if not rel_path:
        return "(파일 위치 정보 없음)"
    fn_part = ""
    if enclosing_fn:
        kind_word = {
            "method": "메서드", "function": "함수", "class": "클래스",
            "type": "타입", "interface": "인터페이스",
        }.get(enclosing_kind, "함수")
        fn_part = f" 의 `{enclosing_fn}` {kind_word}"
    return f"`{rel_path}` (line {line_int}){fn_part}"


def _format_static_context_pm(row: dict) -> str:
    """Phase C — PM 친화 정적 메타 노출. enclosing 함수의 HTTP route, decorator,
    매개변수 등을 자연어 1~3줄로. 비어있으면 빈 문자열.
    """
    lines = []
    endpoint = (row.get("enclosing_endpoint") or "").strip()
    decorators = row.get("enclosing_decorators") or []
    params = row.get("enclosing_doc_params") or []

    if endpoint:
        lines.append(f"- 🌐 외부 노출: HTTP `{endpoint}` 엔드포인트로 접근 가능합니다.")
    if decorators:
        # 식별자만 추출 (`@app.route('/x')` → `app.route`)
        dec_idents = []
        for d in decorators[:3]:
            if isinstance(d, str):
                body = d.lstrip("@").split("(", 1)[0].strip()
                if body:
                    dec_idents.append(f"`@{body}`")
        if dec_idents:
            lines.append(f"- 🛡️ 적용된 정적 의도: {', '.join(dec_idents)}")
    if params:
        names = []
        for p in params[:5]:
            if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1]:
                names.append(f"`{p[1]}`")
        if names:
            lines.append(f"- 📝 입력 매개변수: {', '.join(names)}")
    return "\n".join(lines)


def _format_ai_basis_pm(row: dict) -> str:
    """Phase C — 'AI 판단 근거' 섹션. 사전학습 → 답변 인과를 PM 에게 노출.

    rag_diagnostic 의 used_items + tree_sitter_hits 를 자연어로 요약.
    """
    diag = row.get("rag_diagnostic") or {}
    if not diag:
        # skip_llm 또는 진단 없는 경우 — 짧은 안내
        if row.get("llm_skipped"):
            return "_(자동 템플릿 응답 — RAG 분석 미수행. 개발자 직접 검토를 권장합니다.)_"
        return ""

    used_items = diag.get("used_items") or []
    citation = diag.get("citation") or {}
    cited = citation.get("cited_count", 0) or 0
    total_used = citation.get("total_used", 0) or 0
    ts = diag.get("tree_sitter_hits") or {}
    ts_total = ts.get("total_hits", 0) or 0

    lines = []

    if total_used == 0:
        lines.append(
            "- ⚠️ AI 가 우리 프로젝트의 관련 코드 청크를 받지 못한 상태에서 답변을 생성했습니다. "
            "이번 분석은 **일반 원칙·rule 설명** 에 의존했으며, 프로젝트 특수성은 반영되지 않았을 수 있습니다."
        )
        return "\n".join(lines)

    # 1) 참조한 코드 — used_items 의 path::symbol 을 카테고리별로 묶어 자연어
    callers = [it for it in used_items if it.get("bucket") == "callers"]
    tests = [it for it in used_items if it.get("bucket") == "tests"]
    others = [it for it in used_items if it.get("bucket") == "others"]

    def _refs(items, limit=3):
        out = []
        for it in items[:limit]:
            sym = it.get("symbol", "")
            path = it.get("path", "")
            if sym and sym != "?":
                out.append(f"`{sym}`")
            elif path and path != "?":
                out.append(f"`{path.rsplit('/', 1)[-1]}`")
        return out

    parts = []
    if callers:
        cs = _refs(callers)
        if cs:
            parts.append(f"호출하는 함수 {len(callers)} 곳 ({', '.join(cs)})")
    if tests:
        ts_refs = _refs(tests)
        if ts_refs:
            parts.append(f"관련 테스트 {len(tests)} 개 ({', '.join(ts_refs)})")
    if others:
        os_refs = _refs(others)
        if os_refs:
            parts.append(f"관련 코드 {len(others)} 곳 ({', '.join(os_refs)})")

    if parts:
        lines.append(f"- 🔎 참조한 프로젝트 코드: {' · '.join(parts)}")

    # 2) 인용 정황 — cited / total
    if total_used > 0:
        rate = cited * 100.0 / total_used
        if rate >= 60:
            lines.append(
                f"- ✅ AI 가 받은 청크 {total_used} 개 중 {cited} 개를 답변에 직접 인용 "
                f"({rate:.0f}%). 근거 충실."
            )
        elif rate >= 30:
            lines.append(
                f"- 🟡 AI 가 받은 청크 {total_used} 개 중 {cited} 개만 답변에 인용 "
                f"({rate:.0f}%). 근거 일부 활용."
            )
        elif cited == 0:
            lines.append(
                f"- ⚠️ AI 가 받은 청크 {total_used} 개를 답변에 인용하지 않음 — "
                "근거가 일반 원칙에 의존했을 가능성이 있어 직접 검토 권장."
            )

    # 3) 정적 메타 활용 — tree_sitter_hits
    if ts_total > 0:
        sigs = []
        if ts.get("endpoint_hits"):
            sigs.append("HTTP route")
        if ts.get("decorator_hits"):
            sigs.append("decorator")
        if ts.get("param_hits"):
            sigs.append("매개변수명")
        if ts.get("rag_meta_hits"):
            sigs.append("RAG 청크 메타")
        if sigs:
            lines.append(
                f"- 📚 사전학습 정적 메타 활용: {', '.join(sigs)} ({ts_total} 회 인용). "
                "AI 답변이 우리 프로젝트의 어휘·구조를 직접 반영했습니다."
            )

    return "\n".join(lines)


def render_issue_body(row: dict, args) -> str:
    """Phase C — PM 친화 GitLab Issue 본문 재설계.

    개발자 친화에서 PM 친화로 전환:
    - 최상단: 🚦 Action Verdict — 심각도/오탐/근거 종합 신호등
    - 📌 무엇이 문제인가 (LLM 한 줄 요약)
    - 🎯 어디서 발생하나 (자연어 위치 + 정적 컨텍스트)
    - ⚠️ 영향과 이유 (LLM impact_analysis)
    - 🛠️ 어떻게 고치나 (LLM suggested_fix + diff)
    - 🔍 AI 판단 근거 (NEW — 사전학습 활용 인과)
    - 📂 같은 패턴의 다른 위치 (조건부)
    - 📖 기술 상세 (▶ 접기 — 코드 블록 / Rule key / 모든 링크)
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
    enclosing_kind = row.get("enclosing_kind", "") or ""
    rule_key = row.get("rule_key", "") or ""
    rule_name = row.get("rule_name", "") or ""
    rule_desc = row.get("rule_description", "") or ""
    severity = row.get("severity", "") or ""
    commit_sha = row.get("commit_sha", "") or ""
    sonar_msg = row.get("sonar_message", "") or ""
    sonar_issue_url = row.get("sonar_issue_url", "") or ""
    classification = (outputs.get("classification") or "").lower()
    confidence = (outputs.get("confidence") or "").lower()
    impact_md = (outputs.get("impact_analysis_markdown") or "").strip()
    suggested_fix = (outputs.get("suggested_fix_markdown") or "").strip()
    suggested_diff = (outputs.get("suggested_diff") or "").strip()

    # URL 구성
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

    # ─ 0. Action Verdict 신호등 ───────────────────────────────────────────
    diag = row.get("rag_diagnostic") or {}
    citation = (diag.get("citation") or {}) if diag else {}
    verdict_emoji, verdict_label, verdict_basis = _action_verdict(
        severity, classification, confidence,
        citation.get("cited_count", 0) or 0,
        citation.get("total_used", 0) or 0,
    )
    verdict_block = (
        f"> ## {verdict_emoji} {verdict_label}\n"
        f"> {verdict_basis}\n"
        f"> _(severity={severity or '—'} · classification={classification or '—'} · confidence={confidence or '—'})_"
    )

    # ─ 1. 무엇이 문제인가 ─────────────────────────────────────────────────
    # LLM impact 의 첫 줄을 가져와 PM 한 줄 요약으로. 빈 경우 sonar_message fallback.
    one_line = ""
    if impact_md:
        for ln in impact_md.splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                one_line = s
                break
    if not one_line:
        one_line = sonar_msg or rule_name or "(영향 분석 없음)"
    # 너무 길면 자르기
    if len(one_line) > 240:
        one_line = one_line[:237] + "..."
    what_section = f"### 📌 무엇이 문제인가\n\n{one_line}"

    # ─ 2. 어디서 발생하나 ─────────────────────────────────────────────────
    location_natural = _location_natural_text(rel_path, line_int, enclosing_fn, enclosing_kind)
    static_ctx = _format_static_context_pm(row)
    where_section = "### 🎯 어디서 발생하나\n\n" + location_natural
    if static_ctx:
        where_section += "\n\n" + static_ctx

    # ─ 3. 영향과 이유 ─────────────────────────────────────────────────────
    if impact_md:
        impact_section = f"### ⚠️ 영향과 이유\n\n{impact_md}"
    else:
        impact_section = "### ⚠️ 영향과 이유\n\n_(LLM 이 영향 분석을 제공하지 않음)_"

    # ─ 4. 어떻게 고치나 ───────────────────────────────────────────────────
    # 본문 중복 방지 하드 가드 — 프롬프트 "케이스 A" 의 deterministic enforcement.
    # suggested_diff 가 채워지면 suggested_fix_markdown 의 코드펜스 블록을
    # strip 해서 자연어 설명만 남긴다. LLM 이 프롬프트 규칙을 위반해도
    # 본문에 같은 코드가 두 번 렌더되지 않도록 차단. fence strip 후 자연어가
    # 비면 fix_blocks 에 포함 안 됨 (아래 truthy 체크가 자동 거름).
    diff_present = bool(suggested_diff) and suggested_diff.lower() not in ("null", "none")
    if diff_present and suggested_fix:
        suggested_fix = re.sub(r"```[\s\S]*?```", "", suggested_fix).strip()

    fix_blocks = []
    if suggested_fix:
        if "```" not in suggested_fix:
            lines = suggested_fix.splitlines()
            diff_count = sum(1 for ln in lines if ln.startswith(("+ ", "- ", "+", "-")))
            lang = "diff" if diff_count >= 2 else "python"
            suggested_fix = f"```{lang}\n{suggested_fix}\n```"
        fix_blocks.append(suggested_fix)
    if diff_present:
        if "```" not in suggested_diff:
            suggested_diff = f"```diff\n{suggested_diff}\n```"
        fix_blocks.append("**기계 적용 가능한 diff:**\n\n" + suggested_diff)
    fix_section = ""
    if fix_blocks:
        fix_section = "### 🛠️ 어떻게 고치나\n\n" + "\n\n".join(fix_blocks)

    # ─ 5. AI 판단 근거 (Phase C 신규) ─────────────────────────────────────
    ai_basis = _format_ai_basis_pm(row)
    basis_section = ""
    if ai_basis:
        basis_section = "### 🔍 AI 판단 근거\n\n" + ai_basis

    # ─ 6. 같은 패턴의 다른 위치 (조건부) ───────────────────────────────────
    affected = row.get("affected_locations") or []
    aff_section = ""
    if affected:
        rows_md = ["| 파일 | 라인 | Sonar Key |", "|------|------|-----------|"]
        for a in affected[:20]:
            comp = a.get("component") or a.get("relative_path") or ""
            lno = a.get("line") or ""
            skey = a.get("sonar_issue_key") or ""
            rows_md.append(f"| `{comp}` | {lno} | `{skey}` |")
        aff_section = (
            f"### 📂 같은 패턴의 다른 위치 ({len(affected)} 곳)\n\n"
            + "\n".join(rows_md)
        )

    # ─ 7. 기술 상세 (접기) ────────────────────────────────────────────────
    # 코드 블록 + Rule 전체 + 위치 표 + 모든 링크 — 모두 collapsed 안.
    # Markdown 안에서 details 사용 시, GitLab 은 HTML <details> 태그를 인식.
    snippet = _trim_code_snippet(
        row.get("code_snippet", "") or "", line_int, context=10
    )
    code_block = ""
    if snippet and snippet != "(Code not found in SonarQube)":
        code_block = "**문제 코드:**\n\n```\n" + snippet + "\n```"

    file_cell = f"[`{rel_path}:{line_int}`]({blob_url})" if blob_url else (
        f"`{rel_path}:{line_int}`" if rel_path else "(unknown)"
    )
    fn_cell = (
        f"`{enclosing_fn}`" + (f" *(line {enclosing_ln})*" if enclosing_ln else "")
        if enclosing_fn else "—"
    )
    rule_cell = f"`{rule_key}`" + (f" · {rule_name}" if rule_name else "")
    commit_cell = (
        f"[`{_short_sha(commit_sha)}`]({commit_url})"
        if commit_url and commit_sha
        else (f"`{_short_sha(commit_sha)}`" if commit_sha else "—")
    )
    loc_table = (
        "**메타 정보:**\n\n"
        "| 항목 | 값 |\n"
        "|------|-----|\n"
        f"| 파일 | {file_cell} |\n"
        f"| 함수 | {fn_cell} |\n"
        f"| Rule | {rule_cell} |\n"
        f"| Severity | `{severity or '—'}` |\n"
        f"| Commit | {commit_cell} |"
    )

    rule_full = ""
    if rule_desc:
        # 한글 번역 (Ollama gemma4) — rule_key 캐시 + 실패 시 원문 fallback.
        # 사용자 결정: 영문 원문은 GitLab Issue 에 노출 X (SonarQube 링크에서 확인 가능).
        translated = _translate_rule_to_korean(
            rule_key, rule_desc,
            ollama_base_url=getattr(args, "ollama_base_url", "") or "",
            ollama_model=getattr(args, "ollama_model", "") or "",
        )
        rule_full = f"**Rule 전체 설명 (한글):**\n\n{translated.strip()}"

    link_lines = []
    if sonar_public_url:
        link_lines.append(f"- [SonarQube 이슈 상세]({sonar_public_url})")
    if blob_url:
        line_suffix = f" (line {line_int})" if line_int else ""
        link_lines.append(f"- [GitLab 파일{line_suffix}]({blob_url})")
    if commit_url:
        link_lines.append(f"- [GitLab 커밋 `{_short_sha(commit_sha)}`]({commit_url})")
    links_block = ""
    if link_lines:
        links_block = "**링크:**\n\n" + "\n".join(link_lines)

    detail_inner_parts = [p for p in (loc_table, code_block, rule_full, links_block) if p]
    technical_section = ""
    if detail_inner_parts:
        inner = "\n\n".join(detail_inner_parts)
        technical_section = (
            "<details><summary>📖 기술 상세 (개발자용 — 코드 / Rule / 링크)</summary>\n\n"
            + inner + "\n\n</details>"
        )

    # ─ 8. footer ──────────────────────────────────────────────────────────
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

    # 조립
    sections = [verdict_block, what_section, where_section, impact_section]
    for s in (fix_section, basis_section, aff_section, technical_section):
        if s:
            sections.append(s)
    body = "\n\n---\n\n".join(sections)
    if footer:
        body = body + "\n\n" + footer
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
    # Rule 설명 한글 번역 (Ollama gemma4). 빈 값이면 번역 skip → 원문(영어) 유지.
    # rule_key 기준 dict 캐시로 같은 룰은 1회만 호출.
    ap.add_argument("--ollama-base-url", default="",
                    help="Ollama API base URL (예: http://host.docker.internal:11434). "
                         "비어있으면 rule 번역 skip.")
    ap.add_argument("--ollama-model", default="gemma4:e4b",
                    help="Ollama 모델 이름 (rule 번역용)")
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
            # fp_reason 을 Sonar UI 코멘트로 부착 (전이 직전 — 실패해도 전이는 진행).
            # LLM 의 오탐 판정 근거를 분석가가 Sonar UI 에서 바로 확인 가능.
            fp_reason = (outputs.get("fp_reason") or "").strip()
            if fp_reason:
                comment = (
                    f"[Auto-FP by LLM · confidence={confidence or 'n/a'}]\n"
                    f"{fp_reason}"
                )
                ok_c, err_c = _sonar_add_comment(
                    args.sonar_host_url, args.sonar_token, sonar_key, comment
                )
                if not ok_c:
                    print(f"[FP-COMMENT:WARN] {sonar_key} — {err_c}")
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