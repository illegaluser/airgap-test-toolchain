"""
reporting.translate — 평가 리포트용 한국어 번역 + HTML-safe 줄바꿈 헬퍼

단일 기능 범위:
- 중국어/영문이 섞인 평가 사유 문자열을 한국어로 번역
- 번역기 실패 시 최소한의 고정 구문 치환으로 fallback
- HTML 표시용 `<br>` 삽입·escape

test_runner 와의 계약: 본 모듈은 state 를 갖지 않는다 (번역 캐시는 프로세스 로컬).
Ollama 기반 번역은 `_translate_with_ollama` 만 네트워크 호출 발생.
"""

import os
import re
from html import escape

import requests


# ============================================================================
# 환경 변수 기반 설정 (translate.py 독립)
# ============================================================================

_JUDGE_MODEL_DEFAULT = os.environ.get("JUDGE_MODEL", "gemma4:e4b")

SUMMARY_TRANSLATE_TO_KOREAN = os.environ.get("SUMMARY_TRANSLATE_TO_KOREAN", "true").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

SUMMARY_TRANSLATOR_MODEL = os.environ.get("SUMMARY_TRANSLATOR_MODEL", "").strip() or _JUDGE_MODEL_DEFAULT

SUMMARY_REWRITE_FOR_READABILITY = os.environ.get("SUMMARY_REWRITE_FOR_READABILITY", "true").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")


# 프로세스 로컬 캐시 — 동일 문장 반복 번역 비용 절감. test 격리가 필요하면 모듈 리로드.
_SUMMARY_TRANSLATION_CACHE: dict = {}


# ============================================================================
# 공개 API
# ============================================================================

def needs_translation_to_korean(text: str) -> bool:
    """
    요약 화면에서 가독성을 위해 중국어/영어 중심 문장을 한국어로 변환할지 판별합니다.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    # 한글이 일부 포함되어 있어도 중국어가 섞여 있으면 번역 대상으로 봅니다.
    if bool(re.search(r"[一-鿿]", stripped)):
        return True
    # 영문이 길게 포함된 경우도 번역 대상으로 처리합니다.
    english_chunks = re.findall(r"[A-Za-z]{4,}", stripped)
    if english_chunks:
        return True
    return False


def cleanup_translated_text(text: str) -> str:
    """모델이 번역 지시문까지 함께 출력하는 경우를 제거합니다."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned

    cleaned = re.sub(r"^\s*번역\s*[:：]\s*", "", cleaned)
    cleaned = cleaned.replace("코드, 숫자, 고유명사(case_id, 모델명, URL)는 유지", "")
    cleaned = cleaned.replace("출력은 번역문만 작성", "")
    cleaned = cleaned.replace("원문:", "")
    cleaned = cleaned.replace("번역문:", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def insert_line_breaks(text: str, max_line_len: int = 92) -> str:
    """번역 결과가 한 줄 장문으로 내려오는 경우 문장/어절 단위 줄바꿈을 강제합니다."""
    raw = str(text or "").strip()
    if not raw:
        return ""

    sentence_parts = re.split(r"(?<=[\.\!\?。！？])\s+|(?<=다)\s+", raw)
    sentence_parts = [part.strip() for part in sentence_parts if part and part.strip()]
    if not sentence_parts:
        sentence_parts = [raw]

    wrapped_lines = []
    for sentence in sentence_parts:
        line = sentence
        while len(line) > max_line_len:
            cut = line.rfind(" ", 0, max_line_len)
            if cut <= 0:
                cut = max_line_len
            wrapped_lines.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            wrapped_lines.append(line)

    return "\n".join(wrapped_lines)


def escape_with_linebreaks(text: str, max_line_len: int = 92) -> str:
    """줄바꿈을 포함한 텍스트를 HTML에서 읽기 좋게 표시합니다."""
    return escape(insert_line_breaks(text, max_line_len=max_line_len)).replace("\n", "<br>")


def fallback_localize_common_phrases(text: str) -> str:
    """번역기가 불안정할 때 자주 등장하는 영문 고정 문구를 최소한 한국어로 치환합니다."""
    localized = str(text or "")
    replacements = [
        (r"TaskCompletion failed with score", "TaskCompletion이 점수"),
        (r"Metrics failed:", "지표 평가 실패:"),
        (r"Promptfoo policy checks reported (\d+) failure\(s\)\.", r"Promptfoo 정책 검사에서 \1건 실패가 보고되었습니다."),
        (r"Reason:", "이유:"),
        (r"Skipped because", "다음 이유로 건너뜀:"),
    ]
    for pattern, replacement in replacements:
        localized = re.sub(pattern, replacement, localized)
    return localized


def translate_text_to_korean(text: str) -> str:
    """
    요약 화면 출력용 텍스트를 한국어로 번역합니다.
    - 번역 실패 시 원문 유지
    - 동일 문장은 캐시해 반복 호출 비용을 줄입니다.
    """
    raw = str(text or "")
    if not SUMMARY_TRANSLATE_TO_KOREAN:
        return raw
    if not needs_translation_to_korean(raw):
        return raw

    cache_key = raw.strip()
    if cache_key in _SUMMARY_TRANSLATION_CACHE:
        return _SUMMARY_TRANSLATION_CACHE[cache_key]

    try:
        if SUMMARY_REWRITE_FOR_READABILITY:
            primary_prompt = (
                "아래 원문을 한국어로 번역하고, 평가 리포트에 맞게 가독성 높은 문장으로 재구성해라.\n"
                "규칙:\n"
                "- 의미/사실/원인/판정(pass/fail/skip) 유지\n"
                "- score, threshold, case_id, metric 이름, 숫자, URL, 모델명은 원문과 동일하게 유지\n"
                "- 새로운 주장/해석/추측 추가 금지\n"
                "- 중국어/영어 문장을 남기지 말 것\n"
                "- 설명 없이 한국어 결과만 출력\n\n"
                f"원문:\n{cache_key}"
            )
        else:
            primary_prompt = (
                "아래 원문을 한국어로만 번역해라. "
                "원문의 코드/숫자/case_id/URL/모델명은 유지하고, 자연어만 한국어로 번역해라. "
                "중국어 한자나 영어 문장을 남기지 마라. "
                "설명 없이 번역 결과 한 문단만 출력해라.\n\n"
                f"원문:\n{cache_key}"
            )
        translated = cleanup_translated_text(_translate_with_ollama(primary_prompt))

        # 1차 결과에 중국어가 남거나 번역 지시문이 섞이면 재시도합니다.
        if translated and not re.search(r"[一-鿿]", translated) and "코드, 숫자, 고유명사" not in translated:
            _SUMMARY_TRANSLATION_CACHE[cache_key] = translated
            return translated

        retry_prompt = (
            "다음 문장을 한국어로 다시 번역해라. "
            "중국어/영어 문장을 남기지 말고, 한국어 문장만 출력해라. "
            "score/threshold/case_id/metric 이름/숫자/URL은 유지해라. "
            "설명/주석/머리말 없이 번역문만 출력해라.\n\n"
            f"{cache_key}"
        )
        retried = cleanup_translated_text(_translate_with_ollama(retry_prompt))
        if retried:
            _SUMMARY_TRANSLATION_CACHE[cache_key] = retried
            return retried
    except Exception:
        pass

    fallback = fallback_localize_common_phrases(raw)
    _SUMMARY_TRANSLATION_CACHE[cache_key] = fallback
    return fallback


# ============================================================================
# 내부 헬퍼
# ============================================================================

def _translate_with_ollama(prompt: str) -> str:
    """Ollama generate 호출을 공통화합니다."""
    response = requests.post(
        f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
        json={
            "model": SUMMARY_TRANSLATOR_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 512},
        },
        timeout=20,
    )
    response.raise_for_status()
    return ((response.json() or {}).get("response") or "").strip()
