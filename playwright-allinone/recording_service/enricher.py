"""TR.5 — Recording → IEEE 829-lite 테스트 계획서 역추정 (R-Plus).

설계: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.5"

호스트의 Ollama 에 직접 HTTP 호출. Dify chatflow 우회 (chatflow 는 chat/doc
모드 전용이라 별도 오버헤드 없이 본 트랙은 Ollama HTTP /api/generate 사용).

few-shot 3종 + 평가 rubric 5점 — PLAN §"TR.5" 표 그대로 임베드.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger(__name__)


DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
DEFAULT_TIMEOUT_SEC = int(os.environ.get("RECORDING_ENRICH_TIMEOUT_SEC", "180"))


SYSTEM_PROMPT = (
    "당신은 QA 엔지니어다. 다음 사용자 액션 시퀀스(14-DSL)는 실제 사용자가\n"
    "웹 페이지에서 수행한 행동을 기록한 것이다. 이 시퀀스로부터 IEEE 829-lite\n"
    "형식의 테스트 계획서(목적, 범위, 사전조건, 단계, 예상 결과, 검증 기준)\n"
    "를 역추정하여 작성하라.\n"
    "\n"
    "출력 형식: Markdown. 6 섹션 모두 채워라. 단계는 순서대로 번호 매김.\n"
    "환각 금지 — 입력 시퀀스에 없는 액션·UI 요소를 만들어내지 말 것.\n"
    "단계 누락 금지 — 입력의 모든 step 이 단계 섹션에 1:1 로 반영되어야 함.\n"
)


# few-shot 3종 (PLAN §"TR.5" 표 FS-A1 / FS-A2 / FS-A3)
FEW_SHOT_EXAMPLES: list[tuple[str, str]] = [
    (
        # FS-A1 로그인
        json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/login"},
            {"step": 2, "action": "fill", "target": "#email", "value": "user@example.com"},
            {"step": 3, "action": "fill", "target": "#password", "value": "secret"},
            {"step": 4, "action": "click", "target": "button[type=submit]", "value": ""},
            {"step": 5, "action": "verify", "target": "#welcome", "value": "Welcome", "condition": "text"},
        ], ensure_ascii=False, indent=2),
        (
            "## 목적\n로그인 흐름의 정상 경로 검증.\n\n"
            "## 범위\n인증 페이지 → 대시보드 진입까지.\n\n"
            "## 사전조건\n- 등록된 사용자 자격증명 (user@example.com / secret).\n"
            "- 로그인 페이지 URL 도달 가능.\n\n"
            "## 단계\n"
            "1. https://app.example.com/login 진입.\n"
            "2. 이메일 필드에 user@example.com 입력.\n"
            "3. 비밀번호 필드에 secret 입력.\n"
            "4. 제출 버튼 클릭.\n"
            "5. 환영 영역에 'Welcome' 텍스트 확인.\n\n"
            "## 예상 결과\n로그인 성공 후 대시보드의 환영 메시지 노출.\n\n"
            "## 검증 기준\n#welcome 요소가 'Welcome' 텍스트로 렌더링.\n"
        ),
    ),
    (
        # FS-A2 CRUD 생성
        json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/items"},
            {"step": 2, "action": "click", "target": "button#new", "value": ""},
            {"step": 3, "action": "fill", "target": "#name", "value": "Item A"},
            {"step": 4, "action": "fill", "target": "#price", "value": "1000"},
            {"step": 5, "action": "select", "target": "#category", "value": "books"},
            {"step": 6, "action": "fill", "target": "#desc", "value": "Sample"},
            {"step": 7, "action": "click", "target": "button#save", "value": ""},
            {"step": 8, "action": "verify", "target": ".item-row", "value": "Item A", "condition": "text"},
        ], ensure_ascii=False, indent=2),
        (
            "## 목적\n신규 항목 생성 흐름 검증 (CRUD-Create).\n\n"
            "## 범위\n목록 페이지 → 생성 폼 → 항목 등록 후 목록 반영까지.\n\n"
            "## 사전조건\n- 로그인된 세션. 기존 항목이 있어도 무방.\n\n"
            "## 단계\n"
            "1. https://app.example.com/items 목록 페이지 진입.\n"
            "2. '신규' 버튼 클릭 → 생성 폼 진입.\n"
            "3. 이름 'Item A', 가격 '1000', 카테고리 'books', 설명 'Sample' 입력.\n"
            "4. 저장 버튼 클릭.\n"
            "5. 목록에서 'Item A' 항목 확인.\n\n"
            "## 예상 결과\n저장 후 목록 화면에 신규 항목이 표시.\n\n"
            "## 검증 기준\n.item-row 요소에 'Item A' 텍스트 노출.\n"
        ),
    ),
    (
        # FS-A3 다단계 검색
        json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/search"},
            {"step": 2, "action": "fill", "target": "#q", "value": "DSCORE"},
            {"step": 3, "action": "select", "target": "#type", "value": "doc"},
            {"step": 4, "action": "select", "target": "#sort", "value": "newest"},
            {"step": 5, "action": "click", "target": "button.next-page", "value": ""},
            {"step": 6, "action": "verify", "target": ".results-count", "value": "10", "condition": "text"},
        ], ensure_ascii=False, indent=2),
        (
            "## 목적\n검색 결과의 다중 필터·정렬·페이지네이션 결합 검증.\n\n"
            "## 범위\n검색어 입력 → type/sort 필터 → 다음 페이지 → 결과 건수 확인.\n\n"
            "## 사전조건\n- 인덱스에 'DSCORE' 매칭 문서 다수 존재.\n\n"
            "## 단계\n"
            "1. https://app.example.com/search 진입.\n"
            "2. 검색어 'DSCORE' 입력.\n"
            "3. type 필터 'doc' 선택.\n"
            "4. 정렬 'newest' 선택.\n"
            "5. 다음 페이지 버튼 클릭.\n"
            "6. 결과 카운트가 '10' 인지 확인.\n\n"
            "## 예상 결과\n2 페이지의 결과 카운트가 10건 이상 노출.\n\n"
            "## 검증 기준\n.results-count 요소가 '10' 텍스트.\n"
        ),
    ),
]


@dataclass
class EnrichResult:
    markdown: str
    elapsed_ms: float
    model: str
    prompt_tokens_estimate: int
    error: Optional[str] = None


class EnrichError(RuntimeError):
    """Ollama 호출 / 응답 파싱 단계의 명시적 에러."""


def enrich_recording(
    *,
    scenario: list[dict],
    target_url: str,
    page_title: Optional[str] = None,
    inventory_block: Optional[str] = None,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> EnrichResult:
    """녹화된 14-DSL 시나리오를 IEEE 829-lite Markdown 으로 역추정.

    Args:
        scenario: 14-DSL step dict 리스트 (verify 까지 포함 가능).
        target_url: 페이지 컨텍스트.
        page_title: 페이지 타이틀 (있으면 컨텍스트 강화).
        inventory_block: Phase 1 grounding 인벤토리 블록 (선택).
        ollama_url: 호스트 Ollama 베이스 URL.
        model: 모델 이름. default qwen3.5:9b (env OLLAMA_MODEL override).
        timeout_sec: HTTP 타임아웃.

    Raises:
        EnrichError: HTTP 실패·timeout·응답 파싱 실패.
    """
    if not scenario:
        raise EnrichError("scenario 가 비어있습니다 — 역추정 대상 없음.")

    user_prompt = _build_user_prompt(scenario, target_url, page_title, inventory_block)
    system = _build_system_prompt()
    full_prompt = system + "\n\n" + user_prompt

    started = time.time()
    try:
        res = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=timeout_sec,
        )
    except requests.Timeout as e:
        raise EnrichError(
            f"Ollama 호출이 {timeout_sec}s 안에 끝나지 않았습니다."
        ) from e
    except requests.RequestException as e:
        raise EnrichError(f"Ollama HTTP 통신 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    if res.status_code != 200:
        raise EnrichError(
            f"Ollama 응답 코드 {res.status_code}: {res.text[:300]}"
        )

    try:
        body = res.json()
    except json.JSONDecodeError as e:
        raise EnrichError(f"Ollama 응답 JSON 파싱 실패: {e}") from e

    markdown = (body.get("response") or "").strip()
    if not markdown:
        raise EnrichError("Ollama 응답이 비어있습니다.")

    # 필수 6 섹션이 있는지 가벼운 검증 — 자주 빠뜨리는 항목 미리 알림.
    missing = [
        title for title in ("목적", "범위", "사전조건", "단계", "예상 결과", "검증 기준")
        if f"## {title}" not in markdown
    ]
    if missing:
        log.warning(
            "[enricher] 응답에서 누락된 섹션: %s (model=%s)", missing, model,
        )

    return EnrichResult(
        markdown=markdown,
        elapsed_ms=elapsed_ms,
        model=model,
        prompt_tokens_estimate=_rough_token_count(full_prompt),
    )


def _build_system_prompt() -> str:
    parts = [SYSTEM_PROMPT, "\n## 예시 (few-shot, 3종)\n"]
    for idx, (seed, golden) in enumerate(FEW_SHOT_EXAMPLES, start=1):
        parts.append(f"### 예시 {idx}\n")
        parts.append("**입력 시퀀스 (JSON)**:\n```json\n")
        parts.append(seed)
        parts.append("\n```\n\n")
        parts.append("**역추정 결과**:\n")
        parts.append(golden)
        parts.append("\n---\n")
    return "".join(parts)


def _build_user_prompt(
    scenario: list[dict],
    target_url: str,
    page_title: Optional[str],
    inventory_block: Optional[str],
) -> str:
    lines = ["## 본 입력"]
    lines.append(f"- target_url: {target_url}")
    if page_title:
        lines.append(f"- page_title: {page_title}")
    if inventory_block:
        lines.append("\n### 페이지 인벤토리 (Phase 1 grounding)\n")
        lines.append(inventory_block)
    lines.append("\n### 사용자 액션 시퀀스 (14-DSL)\n")
    lines.append("```json")
    lines.append(json.dumps(scenario, ensure_ascii=False, indent=2))
    lines.append("```\n")
    lines.append("\n위 시퀀스로부터 IEEE 829-lite 테스트 계획서를 작성하라.")
    return "\n".join(lines)


def _rough_token_count(text: str) -> int:
    """tiktoken 우선, 없으면 char/4 근사."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


# ── 항목 4 (UI 개선) — codegen 원본 ↔ LLM healed regression 변경 분석 ───────

_DIFF_ANALYSIS_SYSTEM = """\
당신은 Playwright 자동화 회귀 테스트 검토 전문가다. 두 Python 스크립트
(codegen 원본 + LLM healed regression) 의 차이를 의미 단위로 분석해
사용자가 "이 regression 을 회귀 슈트로 채택할지" 판단할 수 있게 정리하라.

## 출력 포맷 (Markdown — 정확히 4 섹션)

### 1. 핵심 변경 요약
- 가장 중요한 변경 1~3 줄 (selector swap / hover 추가 / 등).

### 2. 변경 라인 분석
변경된 라인을 항목별로:
- **L<번호>**: `<원본 코드>` → `<변경된 코드>`
  유형: <selector swap | hover 추가 | step 삭제 | step 추가 | 기타>
  의미: <왜 변경됐는지 — healing 의도 추정>

### 3. 위험 평가
- **결정성**: 변경된 selector 가 사이트 변경에 강건한가?
- **의도 일치**: 사용자가 녹화한 행동과 일치하는가?
- **잠재 리스크**: 회귀 시 깨질 가능성 있는 부분.

### 4. 회귀 채택 권고
하나만 선택:
- ✅ **권장** — 차이가 명확한 healing 이고 의도와 일치
- ⚠ **검토 필요** — 일부 변경의 의도가 모호함
- ❌ **비권장** — 의도와 다른 동작 가능성

이유: 1~2 문장.

## 규칙
- 코드 블록 안의 selector 는 그대로 인용 (백틱).
- 변경 없는 라인은 언급하지 마라.
- 추측이면 "추정" 명시.
- 한국어로 작성. 군더더기 금지.
"""


@dataclass
class DiffAnalysisResult:
    markdown: str
    elapsed_ms: float
    model: str
    error: Optional[str] = None


def analyze_codegen_vs_regression(
    *,
    original_py: str,
    regression_py: str,
    unified_diff: str,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> DiffAnalysisResult:
    """codegen 원본 .py 와 LLM healed regression_test.py 를 LLM 으로 의미
    분석하여 변경 요약 + 위험 평가 + 채택 권고 markdown 반환.

    환경변수 ``RECORDING_DIFF_ANALYSIS_STUB=1`` 이면 Ollama 호출 없이 결정론적
    스텁 markdown 반환 — E2E 테스트 / Ollama 미가용 환경 회피용.

    Raises:
        EnrichError: Ollama HTTP/timeout/응답 파싱 실패.
    """
    if not regression_py.strip():
        raise EnrichError("regression_test.py 가 비어있어 분석 대상 없음.")

    if os.environ.get("RECORDING_DIFF_ANALYSIS_STUB") == "1":
        return DiffAnalysisResult(
            markdown=(
                "### 1. 핵심 변경 요약\n"
                "- (stub) selector 1건 healed\n\n"
                "### 2. 변경 라인 분석\n"
                "- **L1**: stub 분석 결과 — 실제 Ollama 호출 우회\n"
                "  유형: selector swap\n"
                "  의미: 테스트 환경 결정성 확보\n\n"
                "### 3. 위험 평가\n"
                "- **결정성**: 본 stub 은 결정론적\n"
                "- **의도 일치**: N/A (stub)\n\n"
                "### 4. 회귀 채택 권고\n"
                "✅ **권장** — stub 으로 정상 경로 검증 완료.\n"
            ),
            elapsed_ms=10.0,
            model="stub:RECORDING_DIFF_ANALYSIS_STUB",
        )

    user_prompt = (
        "## 입력 1 — codegen 원본 (original.py)\n"
        "```python\n" + (original_py or "(empty)") + "\n```\n\n"
        "## 입력 2 — LLM healed regression (regression_test.py)\n"
        "```python\n" + regression_py + "\n```\n\n"
        "## 입력 3 — unified diff (참고)\n"
        "```diff\n" + (unified_diff or "(no diff)") + "\n```\n\n"
        "위 두 스크립트의 변경을 4 섹션 Markdown 으로 분석하라."
    )
    full_prompt = _DIFF_ANALYSIS_SYSTEM + "\n\n" + user_prompt

    started = time.time()
    try:
        res = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=timeout_sec,
        )
    except requests.Timeout as e:
        raise EnrichError(
            f"Ollama 호출이 {timeout_sec}s 안에 끝나지 않았습니다."
        ) from e
    except requests.RequestException as e:
        raise EnrichError(f"Ollama HTTP 통신 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    if res.status_code != 200:
        raise EnrichError(
            f"Ollama 응답 코드 {res.status_code}: {res.text[:300]}"
        )
    try:
        body = res.json()
    except json.JSONDecodeError as e:
        raise EnrichError(f"Ollama 응답 JSON 파싱 실패: {e}") from e

    markdown = (body.get("response") or "").strip()
    if not markdown:
        raise EnrichError("Ollama 응답이 비어있습니다.")

    return DiffAnalysisResult(
        markdown=markdown,
        elapsed_ms=elapsed_ms,
        model=model,
    )
