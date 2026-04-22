"""
reporting.narrative — role 별 LLM 프롬프트 + 하드코딩 fallback

Phase 2.0(e)(f) 구현. 계획서 §2 의 R1.1/R2.1/R3.1/R3.2 네 role 을
공개 함수로 노출.

## 공통 규약
각 public 함수는 `{"text": str, "source": "llm" | "cached" | "fallback", "role": str}`
dict 반환. `source` 가 `"fallback"` 이면 하드코딩 템플릿이 사용됐음을 의미하며,
리포트 UI 에서 `📋` 배지로 구분 표시해야 한다.

## 프롬프트 가드레일 (공통)
모든 role 의 system 프롬프트 상단에 다음 원칙을 명시:
- 주어진 JSON 필드의 사실만 사용.
- 추측·새 해석·외부 지식·훈련 데이터 언급 금지.
- score/case_id/숫자/URL 원문 유지.
- 출력 길이 N 문장 제한 (role 별로 명시).
- 한국어로만 출력.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from reporting import llm


# ============================================================================
# 공통 가드레일
# ============================================================================

_COMMON_RULES = (
    "규칙:\n"
    "- 주어진 JSON 필드의 사실만 사용한다. 추측·새 해석·외부 지식 금지.\n"
    "- score / case_id / 숫자 / URL / 모델명은 원문 그대로 유지한다.\n"
    "- 한국어로만 출력한다. 영문·한자 섞지 않는다.\n"
    "- 머리말·주석·설명 없이 본문만 출력한다."
)


# ============================================================================
# R1.1 — 임원 요약 (빌드당 1 call, 기본 on)
# ============================================================================

def generate_exec_summary(state: dict) -> dict:
    """
    summary.json 의 state 를 받아 "상태·주원인·권장 조치" 2~3 문장 생성.

    캐시 키: totals + indicator pass rate + 실패 case 상위 3개 (case_id, failure_message).
    """
    totals = state.get("totals") or {}
    metric_averages = state.get("metric_averages") or {}
    failed_cases = _extract_failed_cases(state, limit=3)

    cache_key = {
        "totals": {k: totals.get(k) for k in (
            "conversations", "passed_conversations", "failed_conversations",
            "turns", "passed_turns", "failed_turns",
            "conversation_pass_rate", "turn_pass_rate",
        )},
        "tokens_total": (totals.get("tokens") or {}).get("total"),
        "latency_p95": (totals.get("latency_ms") or {}).get("p95"),
        "metric_averages": metric_averages,
        "failed_cases": failed_cases,
    }

    # Payload 를 프롬프트에 JSON 블록으로 노출 — LLM 이 "JSON 사실만" 지시를 지킬 근거.
    payload_json = json.dumps(cache_key, ensure_ascii=False, indent=2)
    prompt = (
        "당신은 AI 평가 파이프라인의 리포트 작성자다.\n"
        "아래 빌드 결과 요약 JSON 을 읽고, 운영자가 3초 만에 판단 가능한 문장을 작성한다.\n\n"
        f"{_COMMON_RULES}\n"
        "- 정확히 2~3 문장으로 출력한다.\n"
        "- 첫 문장: 전체 상태(PASS/FAIL, 비율).\n"
        "- 두 번째 문장: 주원인 (가장 많이 실패한 지표 또는 실패 case 패턴).\n"
        "- 세 번째 문장(선택): 권장 조치 1 가지.\n\n"
        f"입력 JSON:\n{payload_json}\n\n"
        "요약:"
    )

    result = llm.generate("exec_summary", cache_key, prompt, num_predict=256)
    if result["source"] == "fallback" or not result.get("text"):
        return _fallback_exec_summary(totals, failed_cases, result)
    return result


def _fallback_exec_summary(totals: dict, failed_cases: list, llm_result: dict) -> dict:
    """LLM 비활성·실패 시 결정론 템플릿."""
    total = int(totals.get("conversations") or 0)
    passed = int(totals.get("passed_conversations") or 0)
    failed = int(totals.get("failed_conversations") or 0)
    rate = totals.get("conversation_pass_rate", 0)

    if total == 0:
        text = "평가 대상 대화가 없습니다. 골든 데이터셋 경로를 확인하세요."
    elif failed == 0:
        text = f"총 {total}건 대화 전부 통과 ({rate}%). 품질 이슈 없음."
    else:
        top_cases = ", ".join(fc["case_id"] for fc in failed_cases[:3])
        text = (
            f"총 {total}건 중 {failed}건 실패 (통과율 {rate}%). "
            f"주요 실패 case: {top_cases or '정보 없음'}. "
            "상세 원인은 case drill-down 을 확인하세요."
        )

    reason = llm_result.get("reason", "llm disabled or failed")
    return {"text": text, "source": "fallback", "role": "exec_summary", "reason": reason}


# ============================================================================
# R3.1 — 실패 case 쉬운 해설 (실패 case 당 1 call, 기본 on)
# ============================================================================

def generate_easy_explanation(turn: dict) -> dict:
    """
    한 턴의 실패 사유를 비개발자 친화 문장 1개로 변환.
    PASS case 에는 호출하지 않음 (호출 측에서 가드) — 단순화 위해 여기서도 guard.
    """
    status = str(turn.get("status") or "")
    if status == "passed":
        return {"text": "질문 의도와 평가 기준을 모두 만족해 통과했습니다.",
                "source": "fallback", "role": "easy_explanation", "reason": "turn passed"}

    failure = str(turn.get("failure_message") or "")
    task_completion = turn.get("task_completion") or {}
    failed_metrics = [m for m in (turn.get("metrics") or []) if m.get("passed") is False]

    cache_key = {
        "case_id": turn.get("case_id"),
        "failure_message": failure[:500],  # 과도히 긴 메시지 truncate
        "task_completion_passed": task_completion.get("passed"),
        "failed_metric_names": [m.get("name") for m in failed_metrics],
    }

    payload_json = json.dumps(cache_key, ensure_ascii=False, indent=2)
    prompt = (
        "당신은 AI 평가 결과를 비개발자에게 설명하는 작성자다.\n"
        "한 개 턴의 실패 사유를 한국어 1 문장으로 설명한다.\n\n"
        f"{_COMMON_RULES}\n"
        "- 정확히 1 문장.\n"
        "- 기술 용어를 써야 할 때는 괄호로 쉬운 말 부연.\n"
        "- 운영자가 다음 조치를 판단할 수 있을 정도의 정보.\n\n"
        f"입력 JSON:\n{payload_json}\n\n"
        "해설:"
    )

    result = llm.generate("easy_explanation", cache_key, prompt, num_predict=160)
    if result["source"] == "fallback" or not result.get("text"):
        return _fallback_easy_explanation(failure, task_completion, result)
    return result


def _fallback_easy_explanation(failure: str, task_completion: dict, llm_result: dict) -> dict:
    """html.py 의 기존 하드코딩 if-else 와 동일 로직."""
    if "Promptfoo policy checks reported" in failure or "정책 검사" in failure:
        text = "민감정보 또는 금칙 패턴이 감지되어 보안 기준에서 실패했습니다."
    elif "Format Compliance Failed" in failure or "응답 형식" in failure:
        text = "응답 형식이 약속된 규격과 달라 연동 기준에서 실패했습니다."
    elif "Adapter Error" in failure or "Connection Error" in failure:
        text = "대상 시스템 통신에 실패해 평가를 진행할 수 없었습니다."
    elif "TaskCompletion failed" in failure or (task_completion and task_completion.get("passed") is False):
        text = "응답에 반드시 들어가야 할 핵심 정보가 빠져 과업 달성 기준을 통과하지 못했습니다."
    elif "AnswerRelevancyMetric" in failure or "답변 관련성" in failure:
        text = "질문과 직접 관련 없는 내용이 섞여 답변 관련성 기준을 통과하지 못했습니다."
    elif "Metrics failed" in failure or "지표 평가 실패" in failure:
        text = "품질 지표 중 하나 이상이 기준 미달이라 실패했습니다."
    else:
        text = "평가 기준 미달로 실패했습니다."

    return {"text": text, "source": "fallback", "role": "easy_explanation",
            "reason": llm_result.get("reason", "llm disabled or failed")}


# ============================================================================
# R2.1 — 지표별 해석 (지표당 1 call, 기본 off)
# ============================================================================

def generate_indicator_narrative(indicator_name: str, pass_count: int, total_count: int,
                                 threshold: Optional[float], fail_case_ids: Iterable[str]) -> dict:
    """
    지표 카드 하단의 1줄 해석. 옵션 기능 (기본 off). fallback 도 1줄.
    """
    fail_ids = list(fail_case_ids)[:3]
    cache_key = {
        "name": indicator_name,
        "pass": pass_count,
        "total": total_count,
        "threshold": threshold,
        "top_fails": fail_ids,
    }

    payload_json = json.dumps(cache_key, ensure_ascii=False, indent=2)
    prompt = (
        "당신은 평가 지표 해석 작성자다.\n"
        "한 개 지표의 결과를 한국어 1 문장으로 설명한다.\n\n"
        f"{_COMMON_RULES}\n"
        "- 정확히 1 문장.\n"
        "- pass rate 또는 실패 원인 언급.\n\n"
        f"입력 JSON:\n{payload_json}\n\n"
        "해설:"
    )

    result = llm.generate("indicator_narrative", cache_key, prompt, num_predict=120)
    if result["source"] == "fallback" or not result.get("text"):
        rate = round(pass_count / total_count * 100, 1) if total_count else 0
        if fail_ids:
            text = f"{indicator_name} pass {pass_count}/{total_count} ({rate}%). 실패 case: {', '.join(fail_ids)}."
        elif total_count == 0:
            text = f"{indicator_name} 적용 case 없음 (N/A)."
        else:
            text = f"{indicator_name} 전부 통과 ({pass_count}/{total_count})."
        return {"text": text, "source": "fallback", "role": "indicator_narrative",
                "reason": result.get("reason", "llm disabled or failed")}
    return result


# ============================================================================
# R3.2 — 조치 권장 (실패 case 당 1 call, 기본 off)
# ============================================================================

def generate_remediation(turn: dict) -> dict:
    """
    실패 case 에 대한 권장 조치 1~2 줄. 옵션 기능 (기본 off).
    LLM 비활성·실패 시 text 비움 (UI 에 섹션 자체를 숨김).
    """
    status = str(turn.get("status") or "")
    if status == "passed":
        return {"text": "", "source": "fallback", "role": "remediation", "reason": "turn passed"}

    failure = str(turn.get("failure_message") or "")
    failed_metrics = [m for m in (turn.get("metrics") or []) if m.get("passed") is False]

    cache_key = {
        "case_id": turn.get("case_id"),
        "failure_message": failure[:500],
        "failed_metric_names": [m.get("name") for m in failed_metrics],
        "input_preview": (str(turn.get("input") or ""))[:200],
        "actual_output_preview": (str(turn.get("actual_output") or ""))[:200],
    }

    payload_json = json.dumps(cache_key, ensure_ascii=False, indent=2)
    prompt = (
        "당신은 AI 에이전트 개선 조치 권고 작성자다.\n"
        "한 개 실패 case 에 대해 운영자/개발자가 시도할 만한 조치 1~2 줄을 제안한다.\n\n"
        f"{_COMMON_RULES}\n"
        "- 정확히 1~2 줄.\n"
        "- 구체적 조치 (system prompt 보강 / RAG context 추가 / 임계치 재검토 등).\n"
        "- 확정적 단정 금지 (예: '개선 가능합니다' 가 아니라 '개선 시도 권장').\n\n"
        f"입력 JSON:\n{payload_json}\n\n"
        "조치 권장:"
    )

    result = llm.generate("remediation", cache_key, prompt, num_predict=200)
    if result["source"] == "fallback" or not result.get("text"):
        # 조치 권장은 fallback 시 텍스트 없음 (UI 가 섹션 숨김)
        return {"text": "", "source": "fallback", "role": "remediation",
                "reason": result.get("reason", "llm disabled or failed")}
    return result


# ============================================================================
# 내부 헬퍼
# ============================================================================

def _extract_failed_cases(state: dict, *, limit: int = 3) -> list:
    """summary.json state 에서 실패 case 상위 N 개만 요약해 캐시 키에 넣는다."""
    failed = []
    for conv in state.get("conversations") or []:
        for turn in conv.get("turns") or []:
            if turn.get("status") == "failed":
                failed.append({
                    "case_id": turn.get("case_id"),
                    "failure_message": (str(turn.get("failure_message") or ""))[:200],
                })
                if len(failed) >= limit:
                    return failed
    return failed
