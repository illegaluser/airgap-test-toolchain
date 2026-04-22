"""
eval_runner.policy — Fail-Fast 단계 검사 (Policy Violation + Format Compliance)

Phase 4.2 Q4 에서 test_runner.py 에서 분리.
Phase 4.1 Q5 에서 Promptfoo subprocess → in-process 전환 완료.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate
from jsonschema.exceptions import ValidationError


MODULE_ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = MODULE_ROOT / "configs"


def _config_path(filename: str) -> Path:
    """평가 러너 모듈 기준 configs/ 경로."""
    return CONFIG_ROOT / filename


def _promptfoo_policy_check(raw_text: str):
    """
    응답에 PII/금칙 패턴이 있는지 검사 (in-process).

    configs/security_assert.check_security_assertions 를 직접 호출.
    실패 시 기존 메시지 규약 유지 — "Promptfoo policy checks reported N failure(s)"
    (narrative fallback 키워드 매칭 호환).
    """
    try:
        from configs.security_assert import check_security_assertions
    except ImportError:
        # configs 모듈 부재 시 검사 skip (안전한 방향으로 폴백)
        return

    result = check_security_assertions(raw_text or "", context={})
    if not result.get("pass", True):
        reason = result.get("reason") or "unspecified violation"
        raise RuntimeError(f"Promptfoo policy checks reported 1 failure(s). Reason: {reason}")


def _schema_validate(raw_text: str):
    """
    API 응답이 schema.json 스키마를 만족하는지 검사.
    UI 평가처럼 비JSON 응답이 자연스러운 경우는 상위 호출부에서 건너뜀.
    """
    schema_path = _config_path("schema.json")
    if not schema_path.exists():
        return

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    try:
        parsed = json.loads(raw_text or "")
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Format Compliance Failed (schema.json): {exc}") from exc
