"""
reporting.llm — Ollama 기반 LLM 호출 공통 인프라

translate / exec_summary / indicator_narrative / easy_explanation / remediation
5 개 role 의 LLM 생성 요청을 단일 함수로 라우팅. 결정론 캐시 + timeout +
graceful fallback 을 내장해 호출 측(translate.py, narrative.py) 은 깨끗한
계약만 사용한다.

## 핵심 설계
- **단일 엔트리**: `generate(role, cache_key, prompt, ...)` 하나로 모든 생성 요청
- **결정론 캐시**: `(role, sha256(cache_key))` 키. temperature=0 이어도 캐시가
  동일 입력 → 동일 출력 보장. Phase 2 의 골든 하네스가 byte-match 가능.
- **opt-in/out 환경변수**:
    - `SUMMARY_LLM_EXEC_SUMMARY` (기본 on)
    - `SUMMARY_LLM_EASY_EXPLANATION` (기본 on)
    - `SUMMARY_LLM_INDICATOR_NARRATIVE` (기본 off)
    - `SUMMARY_LLM_REMEDIATION_HINTS` (기본 off)
    - `SUMMARY_LLM_TIMEOUT_SEC` (기본 20)
- **Graceful fallback**: timeout/network error → None 반환. 호출 측이
  하드코딩 텍스트로 degrade.
- **Provenance**: 성공 시 `{"text": ..., "source": "llm"}`, fallback 시
  `{"text": ..., "source": "fallback"}`. 리포트 UI 에서 배지로 구분.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

import requests


# ============================================================================
# 설정 (환경변수 기반 opt-in/out + 공통 파라미터)
# ============================================================================

_JUDGE_MODEL_DEFAULT = os.environ.get("JUDGE_MODEL", "gemma4:e4b")
SUMMARY_NARRATIVE_MODEL = os.environ.get("SUMMARY_NARRATIVE_MODEL", "").strip() or _JUDGE_MODEL_DEFAULT
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Role 별 기본 활성화 정책 — 계획서 §2.0(d) 반영
_ROLE_ENABLED_DEFAULTS = {
    "translate": True,  # 기존 translate.py 와 의미 통일 — SUMMARY_TRANSLATE_TO_KOREAN 은 translate.py 에서 별도 관리
    "exec_summary": True,
    "easy_explanation": True,
    "indicator_narrative": False,
    "remediation": False,
}

_ROLE_ENV_FLAGS = {
    "exec_summary": "SUMMARY_LLM_EXEC_SUMMARY",
    "easy_explanation": "SUMMARY_LLM_EASY_EXPLANATION",
    "indicator_narrative": "SUMMARY_LLM_INDICATOR_NARRATIVE",
    "remediation": "SUMMARY_LLM_REMEDIATION_HINTS",
}

TIMEOUT_SEC = _env_int("SUMMARY_LLM_TIMEOUT_SEC", 20)


def is_role_enabled(role: str) -> bool:
    """주어진 role 이 현 환경에서 활성화됐는지 확인."""
    if role == "translate":
        # translate 는 기존 SUMMARY_TRANSLATE_TO_KOREAN 규약 유지
        return _env_bool("SUMMARY_TRANSLATE_TO_KOREAN", True)
    env_flag = _ROLE_ENV_FLAGS.get(role)
    default = _ROLE_ENABLED_DEFAULTS.get(role, False)
    if env_flag is None:
        return default
    return _env_bool(env_flag, default)


# ============================================================================
# 결정론 캐시 — (role, cache_key) 기반. 프로세스 로컬.
# ============================================================================

_CACHE: dict[tuple[str, str], str] = {}


def _cache_hash(cache_key: Any) -> str:
    """cache_key 가 dict/list 여도 canonical JSON 으로 정규화해 sha256 산출."""
    try:
        payload = json.dumps(cache_key, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(cache_key)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_clear() -> None:
    """테스트/재실행 격리용. 운영 경로에서는 호출하지 않음."""
    _CACHE.clear()


def cache_peek(role: str, cache_key: Any) -> Optional[str]:
    """캐시 조회 (없으면 None)."""
    return _CACHE.get((role, _cache_hash(cache_key)))


# ============================================================================
# 메인 생성 함수
# ============================================================================

def generate(
    role: str,
    cache_key: Any,
    prompt: str,
    *,
    num_predict: int = 256,
    temperature: float = 0,
) -> dict:
    """
    LLM 생성 요청 단일 엔트리. 모든 역할(translate / exec_summary /
    indicator_narrative / easy_explanation / remediation) 이 이 함수로 라우팅.

    Args:
        role: role 식별자. is_role_enabled 와 캐시 네임스페이스로 사용.
        cache_key: 결정론 캐시 키. dict/list/str 모두 허용. canonical JSON 해시.
        prompt: Ollama 에 보낼 전체 프롬프트 (system 지시문 포함).
        num_predict: 생성 토큰 상한. 길이 제한 강제용.
        temperature: 기본 0 (결정론).

    Returns:
        `{"text": str, "source": "llm" | "cached" | "fallback", "role": str}`.
        fallback 은 호출 측이 하드코딩 텍스트로 degrade 해야 함을 의미.
    """
    if not is_role_enabled(role):
        return {"text": "", "source": "fallback", "role": role, "reason": "role disabled by env"}

    cached = cache_peek(role, cache_key)
    if cached is not None:
        return {"text": cached, "source": "cached", "role": role}

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model": SUMMARY_NARRATIVE_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": num_predict},
            },
            timeout=TIMEOUT_SEC,
        )
        response.raise_for_status()
        text = ((response.json() or {}).get("response") or "").strip()
    except Exception as exc:
        return {"text": "", "source": "fallback", "role": role, "reason": f"llm error: {exc}"}

    if not text:
        return {"text": "", "source": "fallback", "role": role, "reason": "empty llm response"}

    _CACHE[(role, _cache_hash(cache_key))] = text
    return {"text": text, "source": "llm", "role": role}
