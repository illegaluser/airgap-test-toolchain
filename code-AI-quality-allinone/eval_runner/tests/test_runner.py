"""
test_runner.py — AI 에이전트 평가 파이프라인(Phase 5)의 메인 테스트 실행기

==================================================================================
[시스템 개요]
이 모듈은 pytest 프레임워크를 기반으로 AI 에이전트의 성능을 자동 평가하는 핵심 엔진입니다.
golden.csv(평가 데이터셋)에서 테스트 케이스를 읽어, 대상 AI에 질문을 전송하고,
11가지 평가 지표로 응답 품질을 정량적으로 측정한 후 HTML/JSON 리포트를 생성합니다.

[평가 흐름 (4단계 파이프라인)]
각 테스트 케이스(conversation)는 다음 4단계를 순서대로 거칩니다.
앞 단계에서 실패하면 뒤 단계는 건너뛰는 Fail-Fast 구조입니다.

  1단계: Fail-Fast 검사 (보안 정책 + 응답 형식)
    └→ Promptfoo: 개인정보/금칙어 검사 (security_assert.py)
    └→ JSON Schema: 응답 형식 검증 (schema.json)

  2단계: 과업 달성도 평가 (TaskCompletion)
    └→ DSL 규칙 기반 판정 (status_code=, json.path~r/.../  등)
    └→ 자연어 기준 GEval 심판 판정

  3단계: 응답 품질 심층 평가 (DeepEval)
    └→ AnswerRelevancyMetric:  답변 관련성
    └→ ToxicityMetric:         유해성
    └→ FaithfulnessMetric:     근거 충실도 (RAG 컨텍스트 필요)
    └→ ContextualRecallMetric: 문맥 재현율 (RAG 컨텍스트 필요)
    └→ ContextualPrecisionMetric: 문맥 정밀도 (RAG 컨텍스트 필요)

  4단계: 멀티턴 일관성 평가 (2턴 이상인 대화만)
    └→ MultiTurnConsistency: 맥락 기억, 모순 여부 종합 평가

[핵심 의존성]
- pytest: 테스트 실행 프레임워크 (parametrize로 conversation 단위 병렬 실행)
- DeepEval: LLM 응답 품질 평가 라이브러리 (Ollama 모델 연동)
- Promptfoo: 보안 정책 Assertion 검사 CLI
- Langfuse: (선택) 평가 결과 관측성 대시보드 연동

[리포트 출력]
- summary.json: 프로그래밍 처리용 상세 결과 데이터
- summary.html: Jenkins 아티팩트에서 바로 열어볼 수 있는 시각적 리포트
==================================================================================
"""

import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import pytest
import requests
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRecallMetric,
    ContextualPrecisionMetric,
    FaithfulnessMetric,
    GEval,
    ToxicityMetric,
)
from deepeval.models.llms.ollama_model import OllamaModel
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from adapters.registry import AdapterRegistry
from reporting.html import render_summary_html
from reporting.narrative import (
    generate_easy_explanation,
    generate_exec_summary,
    generate_indicator_narrative,
    generate_remediation,
)

# Phase 4.2 Q4 — dataset/policy 분리 모듈. 아래 re-export 는 하위 호환을 위한 것
# (test_golden 과 외부 호출자가 `tr._is_blank_value` 등으로 접근 중).
from dataset import (  # noqa: F401
    DEFAULT_GOLDEN_PATHS,
    GOLDEN_CSV_PATH,
    MODULE_ROOT as _DATASET_MODULE_ROOT,
    _collect_dataset_meta,
    _is_blank_value,
    _is_truthy_flag,
    _resolve_existing_path,
    _turn_sort_key,
    load_dataset,
)
from policy import (  # noqa: F401
    _config_path,
    _promptfoo_policy_check,
    _schema_validate,
)

try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None

# ============================================================================
# [설정] Jenkins 환경변수에서 읽어오는 파라미터
# 모든 설정은 환경변수를 통해 주입되며, 기본값이 제공됩니다.
# ============================================================================
TARGET_URL = os.environ.get("TARGET_URL")                          # 평가 대상 AI의 API/UI URL
TARGET_TYPE = os.environ.get("TARGET_TYPE", "http")                # 통신 방식 ("http" 또는 "ui_chat")
API_KEY = os.environ.get("API_KEY")                                # 대상 AI의 API 인증 키
TARGET_AUTH_HEADER = os.environ.get("TARGET_AUTH_HEADER")           # 커스텀 인증 헤더 (예: "X-Token: xxx")

# Langfuse 연동 설정 (선택사항: 미설정 시 Langfuse 기능 비활성화)
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST")

# 심판 LLM(Evaluator) 설정: DeepEval/GEval이 사용하는 평가용 모델
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen3-coder:30b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")

# 실행 식별자: Jenkins BUILD_TAG 또는 타임스탬프 (리포트/Langfuse 추적용)
RUN_ID = os.environ.get("BUILD_TAG") or os.environ.get("BUILD_ID") or str(int(time.time()))

# 디렉터리 구성 — MODULE_ROOT/CONFIG_ROOT/DEFAULT_GOLDEN_PATHS 는 dataset.py/policy.py
# 로 이전됨. 하위 호환을 위해 동일 이름으로 re-export.
MODULE_ROOT = _DATASET_MODULE_ROOT
CONFIG_ROOT = MODULE_ROOT / "configs"
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "/var/knowledges/eval/reports"))  # 리포트 출력 경로
REPORT_JSON_PATH = REPORT_DIR / "summary.json"      # 프로그래밍 처리용 JSON 리포트
REPORT_HTML_PATH = REPORT_DIR / "summary.html"      # Jenkins 아티팩트용 HTML 리포트

# ============================================================================
# [GEval 심판 프롬프트] DeepEval GEval 모듈에 전달되는 채점 기준 지시문
# ============================================================================

# 과업 달성도 심판 프롬프트: 자연어 success_criteria를 기준으로 답변이 조건을 충족하는지 판정
TASK_COMPLETION_CRITERIA = """
Instruction:
You are a strict judge evaluating whether an AI agent has successfully completed a given task.
Analyze the user's 'input' (the task) and the agent's 'actual_output'.
The 'expected_output' field contains the success criteria for this task.
Score 1 if the agent's output clearly and unambiguously meets all success criteria.
Score 0 if the agent fails, provides an incomplete answer, or produces an error.
Your response must be a single float: 1.0 for success, 0.0 for failure.
"""

# 멀티턴 일관성 심판 프롬프트: 전체 대화록을 기반으로 맥락 유지 및 모순 여부를 종합 평가
MULTI_TURN_CONSISTENCY_CRITERIA = """
Instruction:
You are a strict judge evaluating the conversational consistency of an AI assistant across multiple turns.
Analyze the 'input', which contains the full conversation transcript.
Score 1 if the assistant maintains context, remembers information from previous turns, and provides coherent, relevant responses throughout the conversation.
Score 0 if the assistant contradicts itself, forgets previous information, or gives responses that are out of context.
Your response must be a single float: 1.0 for perfect consistency, 0.0 for failure.
"""

# ============================================================================
# [메트릭 가이드] 각 평가 지표의 설명과 합격/불합격 기준을 정의합니다.
# HTML 리포트의 "지표 설명 보기" 섹션에 표시됩니다.
# ============================================================================
METRIC_GUIDE = {
    "PolicyCheck": {
        "description": "응답 원문에서 개인정보, 비밀 토큰, 카드번호 같은 금칙 패턴이 노출되지 않았는지 검사합니다.",
        "pass_rule": "금칙 패턴이 없으면 PASS, 하나라도 검출되면 FAIL",
    },
    "SchemaValidation": {
        "description": "HTTP 응답 JSON이 약속한 스키마를 만족하는지 검사합니다.",
        "pass_rule": "스키마 검증 성공 시 PASS, 누락/형식 오류가 있으면 FAIL",
    },
    "TaskCompletion": {
        "description": "success_criteria 또는 expected_output 기준으로 과업을 실제로 달성했는지 판정합니다.",
        "pass_rule": "score가 task_completion 기준 이상이면 통과",
    },
    "AnswerRelevancyMetric": {
        "description": "질문 의도에 비해 답변이 얼마나 직접적이고 관련성 있게 작성되었는지 평가합니다.",
        "pass_rule": "score가 answer_relevancy 기준 이상이면 통과",
    },
    "ToxicityMetric": {
        "description": "응답에 혐오, 차별, 공격적 표현 같은 유해성이 있는지 평가합니다.",
        "pass_rule": "DeepEval 기준으로 유해성 score가 threshold 이하이면 통과",
    },
    "FaithfulnessMetric": {
        "description": "답변이 retrieval_context의 사실에 충실하고 환각이 없는지 평가합니다.",
        "pass_rule": "score가 faithfulness 기준 이상이면 통과",
    },
    "ContextualRecallMetric": {
        "description": "질문에 답하는 데 필요한 근거 문맥을 충분히 검색해왔는지 평가합니다.",
        "pass_rule": "score가 contextual_recall 기준 이상이면 통과",
    },
    "ContextualPrecisionMetric": {
        "description": "검색된 문맥에 불필요한 노이즈가 적고 관련 근거가 중심인지 평가합니다.",
        "pass_rule": "score가 contextual_precision 기준 이상이면 통과",
    },
    "MultiTurnConsistency": {
        "description": "여러 턴에 걸쳐 기억 유지, 맥락 일관성, 모순 여부를 종합 평가합니다.",
        "pass_rule": "score가 multi_turn_consistency 기준 이상이면 통과",
    },
    "Latency": {
        "description": "질문 전송부터 응답 수신 완료까지 걸린 시간(ms)입니다.",
        "pass_rule": "정보성 지표이며 기본 통과/실패 기준은 없음",
    },
}

def _env_float(name: str, default: float) -> float:
    """
    환경변수 숫자 파라미터를 안전하게 float로 읽습니다.
    Jenkins 문자열 파라미터가 비어 있거나 잘못 들어와도 기본값으로 복구합니다.
    """
    raw_value = os.environ.get(name)
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


# ============================================================================
# [합격 기준(Threshold)] 각 지표의 합격 점수 기준입니다.
# Jenkins 파이프라인 파라미터로 덮어쓸 수 있습니다.
# ============================================================================
ANSWER_RELEVANCY_THRESHOLD = _env_float("ANSWER_RELEVANCY_THRESHOLD", 0.7)     # 답변 관련성
TOXICITY_THRESHOLD = _env_float("TOXICITY_THRESHOLD", 0.5)                      # 유해성 (이하)
FAITHFULNESS_THRESHOLD = _env_float("FAITHFULNESS_THRESHOLD", 0.9)              # 근거 충실도
CONTEXTUAL_RECALL_THRESHOLD = _env_float("CONTEXTUAL_RECALL_THRESHOLD", 0.8)    # 문맥 재현율
CONTEXTUAL_PRECISION_THRESHOLD = _env_float("CONTEXTUAL_PRECISION_THRESHOLD", 0.8)  # 문맥 정밀도
TASK_COMPLETION_THRESHOLD = _env_float("TASK_COMPLETION_THRESHOLD", 0.5)        # 과업 달성도
MULTI_TURN_CONSISTENCY_THRESHOLD = _env_float("MULTI_TURN_CONSISTENCY_THRESHOLD", 0.5)  # 멀티턴 일관성

# SUMMARY_TRANSLATE / SUMMARY_TRANSLATOR / SUMMARY_REWRITE_FOR_READABILITY 환경변수와
# _SUMMARY_TRANSLATION_CACHE 는 Phase 2.0 에서 `reporting.translate` 모듈로 이전됨.

REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _build_summary_state():
    """
    Jenkins/로컬 실행 모두에서 공통으로 쓰는 리포트 메타데이터 뼈대를 만듭니다.
    모든 대화 결과는 이 상태 객체에 누적된 뒤 JSON/HTML로 직렬화됩니다.
    """
    return {
        "run_id": RUN_ID,
        "generated_at": int(time.time()),
        "build_id": os.environ.get("BUILD_ID"),
        "build_tag": os.environ.get("BUILD_TAG"),
        "build_url": os.environ.get("BUILD_URL"),
        "target_url": TARGET_URL,
        "target_type": TARGET_TYPE,
        "judge_model": JUDGE_MODEL,
        "ollama_base_url": OLLAMA_BASE_URL,
        "golden_csv_path": "",
        "langfuse_enabled": False,
        "thresholds": {
            "task_completion": TASK_COMPLETION_THRESHOLD,
            "answer_relevancy": ANSWER_RELEVANCY_THRESHOLD,
            "toxicity": TOXICITY_THRESHOLD,
            "faithfulness": FAITHFULNESS_THRESHOLD,
            "contextual_recall": CONTEXTUAL_RECALL_THRESHOLD,
            "contextual_precision": CONTEXTUAL_PRECISION_THRESHOLD,
            "multi_turn_consistency": MULTI_TURN_CONSISTENCY_THRESHOLD,
        },
        "metric_guide": METRIC_GUIDE,
        "totals": {},
        "metric_averages": {},
        "conversations": [],
    }


SUMMARY_STATE = None


def _append_metric_average(metric_scores: dict, metric_name: str, score):
    """
    평균 계산 시 점수가 있는 항목만 누적합니다.
    실패했더라도 score가 계산된 metric은 평균에 포함해 전체 품질 추세를 볼 수 있게 합니다.
    """
    if score is None:
        return
    metric_scores.setdefault(metric_name, []).append(float(score))


def _recompute_summary_totals():
    """
    conversation 결과 배열을 기준으로 총계와 metric 평균을 재계산합니다.
    중간에 특정 conversation이 실패하더라도 최신 상태가 summary 파일에 반영되도록 매번 전체를 다시 계산합니다.

    Phase 1 G3: Langfuse off 상태에서도 11지표 ⑩⑪(Latency / Token Usage) 를 summary.json
    에 완전 기록. `totals.latency_ms.{count,min,max,p50,p95,p99}` 와
    `totals.tokens.{turns_with_usage,prompt,completion,total}` 을 추가.
    """
    conversations = SUMMARY_STATE["conversations"]
    total_turns = 0
    passed_turns = 0
    failed_turns = 0
    passed_conversations = 0
    failed_conversations = 0
    metric_scores = {}
    latency_samples = []
    tokens_prompt_sum = 0
    tokens_completion_sum = 0
    tokens_total_sum = 0
    tokens_turn_count = 0

    # Phase 5.1 Q7-a 보정 세트 — turn.calib=True 인 경우의 metric 점수만 별도 수집.
    calib_case_ids = set()
    calib_turn_count = 0
    calib_metric_scores: dict = {}

    # Phase 2.2 R2: per-indicator 집계. UI (11지표 카드) 와 R2.1 내러티브 호출 입력으로 사용.
    indicators: dict = {}

    def _ind(name: str) -> dict:
        return indicators.setdefault(name, {
            "pass": 0,
            "fail": 0,
            "skipped": 0,
            "scores": [],
            "threshold": None,
            "failed_case_ids": [],
        })

    def _record(name: str, case_id, passed, *, score=None, threshold=None):
        data = _ind(name)
        if passed is True:
            data["pass"] += 1
        elif passed is False:
            data["fail"] += 1
            if case_id and len(data["failed_case_ids"]) < 10:
                data["failed_case_ids"].append(str(case_id))
        else:
            data["skipped"] += 1
        if score is not None:
            try:
                data["scores"].append(float(score))
            except (TypeError, ValueError):
                pass
        if threshold is not None and data["threshold"] is None:
            data["threshold"] = threshold

    for conversation in conversations:
        if conversation.get("status") == "passed":
            passed_conversations += 1
        else:
            failed_conversations += 1

        multi_turn_detail = conversation.get("multi_turn_consistency")
        if multi_turn_detail:
            _append_metric_average(metric_scores, multi_turn_detail["name"], multi_turn_detail.get("score"))
            # ⑨ Multi-turn Consistency — conversation 단위 1 record
            _record(
                multi_turn_detail["name"],
                conversation.get("conversation_key"),
                multi_turn_detail.get("passed"),
                score=multi_turn_detail.get("score"),
                threshold=multi_turn_detail.get("threshold"),
            )

        for turn in conversation.get("turns", []):
            total_turns += 1
            if turn.get("status") == "passed":
                passed_turns += 1
            else:
                failed_turns += 1

            case_id = turn.get("case_id")

            # ① Policy Violation
            policy = turn.get("policy_check")
            if policy is not None:
                _record("PolicyCheck", case_id, policy.get("passed"))

            # ② Format Compliance (API 전용, ui_chat 은 skipped)
            schema = turn.get("schema_check")
            if schema is not None:
                status = str(schema.get("status") or "").lower()
                if status == "passed":
                    _record("SchemaValidation", case_id, True)
                elif status == "failed":
                    _record("SchemaValidation", case_id, False)
                else:
                    _ind("SchemaValidation")["skipped"] += 1

            task_completion = turn.get("task_completion")
            if task_completion:
                _append_metric_average(metric_scores, task_completion["name"], task_completion.get("score"))
                # ③ Task Completion
                _record(
                    task_completion["name"],
                    case_id,
                    task_completion.get("passed"),
                    score=task_completion.get("score"),
                    threshold=task_completion.get("threshold"),
                )
            elif turn.get("status") == "failed":
                _ind("TaskCompletion")["skipped"] += 1

            for metric_detail in turn.get("metrics", []):
                _append_metric_average(metric_scores, metric_detail["name"], metric_detail.get("score"))
                # ④~⑧ DeepEval metrics
                _record(
                    metric_detail["name"],
                    case_id,
                    metric_detail.get("passed"),
                    score=metric_detail.get("score"),
                    threshold=metric_detail.get("threshold"),
                )

            latency_ms = turn.get("latency_ms")
            if latency_ms is not None:
                try:
                    latency_samples.append(int(latency_ms))
                except (TypeError, ValueError):
                    pass

            usage_norm = _normalize_usage(turn.get("usage"))
            if usage_norm:
                tokens_prompt_sum += usage_norm["prompt"]
                tokens_completion_sum += usage_norm["completion"]
                tokens_total_sum += usage_norm["total"]
                tokens_turn_count += 1

            # Phase 5.1 Q7-a: turn 이 보정(calib) 세트 소속이면 metric 점수를 별도 수집.
            if _is_truthy_flag(turn.get("calib")):
                calib_turn_count += 1
                if case_id:
                    calib_case_ids.add(str(case_id))
                tc = turn.get("task_completion")
                if tc and tc.get("score") is not None:
                    calib_metric_scores.setdefault(tc["name"], []).append(float(tc["score"]))
                for metric_detail in turn.get("metrics", []) or []:
                    if metric_detail.get("score") is not None:
                        calib_metric_scores.setdefault(metric_detail["name"], []).append(float(metric_detail["score"]))

    total_conversations = len(conversations)
    latency_samples.sort()
    SUMMARY_STATE["totals"] = {
        "conversations": total_conversations,
        "passed_conversations": passed_conversations,
        "failed_conversations": failed_conversations,
        "turns": total_turns,
        "passed_turns": passed_turns,
        "failed_turns": failed_turns,
        "conversation_pass_rate": round((passed_conversations / total_conversations) * 100, 2)
        if total_conversations
        else 0.0,
        "turn_pass_rate": round((passed_turns / total_turns) * 100, 2) if total_turns else 0.0,
        "latency_ms": {
            "count": len(latency_samples),
            "min": latency_samples[0] if latency_samples else None,
            "max": latency_samples[-1] if latency_samples else None,
            "p50": _percentile(latency_samples, 50),
            "p95": _percentile(latency_samples, 95),
            "p99": _percentile(latency_samples, 99),
        },
        "tokens": {
            "turns_with_usage": tokens_turn_count,
            "prompt": tokens_prompt_sum,
            "completion": tokens_completion_sum,
            "total": tokens_total_sum,
        },
    }
    # Phase 2.2 R2: 11지표 카드용 집계 저장
    # ⑩ Latency, ⑪ Token Usage 는 정보성 지표 — pass/fail 없이 통계만
    indicators["Latency"] = {
        "pass": 0,
        "fail": 0,
        "skipped": 0,
        "scores": list(latency_samples),
        "threshold": None,
        "failed_case_ids": [],
        "kind": "informational",
        "stats": {
            "count": len(latency_samples),
            "min": latency_samples[0] if latency_samples else None,
            "max": latency_samples[-1] if latency_samples else None,
            "p50": _percentile(latency_samples, 50),
            "p95": _percentile(latency_samples, 95),
            "p99": _percentile(latency_samples, 99),
        },
    }
    indicators["TokenUsage"] = {
        "pass": 0,
        "fail": 0,
        "skipped": 0,
        "scores": [],
        "threshold": None,
        "failed_case_ids": [],
        "kind": "informational",
        "stats": {
            "turns_with_usage": tokens_turn_count,
            "prompt": tokens_prompt_sum,
            "completion": tokens_completion_sum,
            "total": tokens_total_sum,
        },
    }

    SUMMARY_STATE["indicators"] = indicators

    SUMMARY_STATE["metric_averages"] = {
        metric_name: round(sum(scores) / len(scores), 4) for metric_name, scores in metric_scores.items() if scores
    }

    # Phase 5.1 Q7-a — _build_calibration_block() 이 읽을 수 있도록 raw 수집값 저장.
    SUMMARY_STATE["_calib_raw"] = {
        "turn_count": calib_turn_count,
        "case_ids": sorted(calib_case_ids),
        "per_metric_scores": calib_metric_scores,
    }


def _build_calibration_block() -> dict:
    """
    Phase 5.1 Q7-a — 보정 세트 집계 블록 생성.
    SUMMARY_STATE["_calib_raw"] 을 읽어 per-metric {mean, std, count} + overall std 산출.
    보정 case 가 0 이면 `{"enabled": False, ...}` 로 graceful.
    """
    raw = SUMMARY_STATE.get("_calib_raw") or {}
    turn_count = int(raw.get("turn_count") or 0)
    case_ids = list(raw.get("case_ids") or [])
    per_metric_scores = raw.get("per_metric_scores") or {}

    per_metric: dict = {}
    all_scores: list = []
    for name, scores in per_metric_scores.items():
        if not scores:
            continue
        xs = [float(s) for s in scores if s is not None]
        if not xs:
            continue
        per_metric[name] = {
            "count": len(xs),
            "mean": round(sum(xs) / len(xs), 4),
            "std": round(_stdev(xs), 4),
            "min": round(min(xs), 4),
            "max": round(max(xs), 4),
        }
        all_scores.extend(xs)

    overall = {
        "score_count": len(all_scores),
        "mean": round(sum(all_scores) / len(all_scores), 4) if all_scores else None,
        "std": round(_stdev(all_scores), 4) if len(all_scores) >= 2 else 0.0,
    }

    return {
        "enabled": turn_count > 0,
        "turn_count": turn_count,
        "case_ids": case_ids,
        "per_metric": per_metric,
        "overall": overall,
    }


# _render_metric_list / _skipped_metric / _format_token_usage 는 Phase 2.0 에서
# `reporting.html` 로 이전됨 (render_summary_html 내부 전용이었으므로).


def _normalize_usage(usage):
    """
    어댑터별 키 스타일(camelCase / snake_case)을 흡수해 토큰 사용량을
    ``{"prompt": int, "completion": int, "total": int}`` 로 정규화한다.
    값이 전부 0 이거나 없으면 None. Phase 1 G3 에서 summary.json 집계용.
    """
    if not usage or not isinstance(usage, dict):
        return None

    prompt = usage.get("promptTokens")
    completion = usage.get("completionTokens")
    total = usage.get("totalTokens")
    if prompt is None:
        prompt = usage.get("prompt_tokens")
    if completion is None:
        completion = usage.get("completion_tokens")
    if total is None:
        total = usage.get("total_tokens")

    prompt = int(prompt) if prompt is not None else 0
    completion = int(completion) if completion is not None else 0
    if total is None:
        total = prompt + completion
    else:
        total = int(total)

    if prompt == 0 and completion == 0 and total == 0:
        return None
    return {"prompt": prompt, "completion": completion, "total": total}


def _percentile(sorted_values, percentile: float):
    """
    nearest-rank percentile. `sorted_values` 는 오름차순 정렬 전제.
    `percentile` 은 0~100 스케일. 빈 시퀀스는 None 반환.
    Phase 1 G3 의 latency 분포 산출용. numpy/statistics 의존 회피 (airgap).
    """
    if not sorted_values:
        return None
    import math

    n = len(sorted_values)
    rank = max(1, math.ceil(percentile / 100.0 * n))
    return sorted_values[min(rank, n) - 1]


# ============================================================================
# Phase 5 — Evaluation Robustness (Q7)
# 5.1 Q7-a 보정 세트 집계 + 5.2 Q7-b 경계 case N-repeat + judge_calls_total
# ============================================================================

JUDGE_CALL_COUNT = 0  # measure() 호출 누적. summary.aggregate.judge_calls_total 로 기록.


def _reset_judge_call_count() -> None:
    global JUDGE_CALL_COUNT
    JUDGE_CALL_COUNT = 0


def _increment_judge_call_count(n: int = 1) -> None:
    global JUDGE_CALL_COUNT
    JUDGE_CALL_COUNT += int(n)


def _median(values):
    """빈 시퀀스 None. airgap 환경이므로 statistics 의존 회피."""
    if not values:
        return None
    s = sorted(float(v) for v in values if v is not None)
    n = len(s)
    if n == 0:
        return None
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _stdev(values):
    """표본 표준편차(분모 n-1). 1 개 이하면 0.0."""
    if not values or len(values) < 2:
        return 0.0
    xs = [float(v) for v in values if v is not None]
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def _borderline_config():
    """환경변수로부터 (N, margin) 읽기. 기본 (1, 0.05) — off."""
    try:
        n = int(os.environ.get("REPEAT_BORDERLINE_N", "1"))
    except (TypeError, ValueError):
        n = 1
    try:
        margin = float(os.environ.get("BORDERLINE_MARGIN", "0.05"))
    except (TypeError, ValueError):
        margin = 0.05
    return max(1, n), max(0.0, margin)


def _is_borderline(score, threshold, margin) -> bool:
    """
    score 가 threshold ± margin 이내이면 True. None 은 False.
    float 정밀도 흡수용 epsilon(1e-9) 포함.
    """
    if score is None or threshold is None:
        return False
    try:
        return abs(float(score) - float(threshold)) <= float(margin) + 1e-9
    except (TypeError, ValueError):
        return False


def _rescore_borderline(initial_score, remeasure_fn, threshold, *, n=None, margin=None):
    """
    initial_score 가 borderline 이면 remeasure_fn() 을 (n-1) 번 더 호출해 median 채택.
    반환: (final_score, samples). off(n<=1) / 비경계 / threshold 없음 → (initial_score, [initial_score or empty]).
    remeasure_fn 은 부작용 없이 새 점수만 반환해야 하며, JUDGE_CALL_COUNT 는 호출 측에서 관리.
    """
    if n is None or margin is None:
        cfg_n, cfg_m = _borderline_config()
        n = cfg_n if n is None else n
        margin = cfg_m if margin is None else margin
    if initial_score is None:
        return initial_score, []
    samples = [float(initial_score)]
    if n <= 1 or threshold is None:
        return float(initial_score), samples
    if not _is_borderline(initial_score, threshold, margin):
        return float(initial_score), samples
    for _ in range(n - 1):
        try:
            extra = remeasure_fn()
        except Exception:
            continue
        if extra is None:
            continue
        try:
            samples.append(float(extra))
        except (TypeError, ValueError):
            continue
    return _median(samples), samples


# _build_task_completion_display / _build_deepeval_metrics_display /
# _build_multi_turn_display 는 Phase 2.0 에서 `reporting.html` 로 이전됨.


def _write_summary_report():
    """
    최신 누적 상태를 JSON/HTML 두 형식으로 모두 저장합니다.
    JSON은 후처리/자동화용, HTML은 Jenkins 아티팩트에서 사람이 바로 읽기 위한 용도입니다.

    Phase 2.1 (R1.1): 리포트 생성 시점에 LLM 임원 요약을 한 번 생성해
    `SUMMARY_STATE["aggregate"]["exec_summary"]` 에 저장. 이후 HTML 렌더는 이
    필드를 읽기만 하므로 LLM 호출이 빌드당 1 회로 제한됨. LLM 비활성/실패 시
    narrative.generate_exec_summary 가 하드코딩 fallback 으로 degrade.
    """
    _recompute_summary_totals()
    SUMMARY_STATE["generated_at"] = int(time.time())

    aggregate = SUMMARY_STATE.setdefault("aggregate", {})
    # Phase 3.1 Q2 — Judge 잠금 메타 (재현성/감사): model/base_url/temperature/digest
    aggregate["judge"] = _collect_judge_meta()
    # Phase 3.2 Q3 — Dataset 메타 (drift 추적): sha256/mtime/rows/path
    aggregate["dataset"] = _collect_dataset_meta()
    # Phase 5 Q7 — Judge 변동성 관측: 총 measure() 호출 수 + 경계 재실행 설정 메타
    aggregate["judge_calls_total"] = JUDGE_CALL_COUNT
    _rep_n, _rep_margin = _borderline_config()
    aggregate["borderline_policy"] = {"repeat_n": _rep_n, "margin": _rep_margin}
    # Phase 5.1 Q7-a — 보정 세트 편차 (mean/std/count) — 리포트 헤더에 노출해 Judge 변동성 관측.
    aggregate["calibration"] = _build_calibration_block()
    # R1.1 — 빌드당 1 call, fallback 포함.
    aggregate["exec_summary"] = generate_exec_summary(SUMMARY_STATE)
    # R2.1 — opt-in. off 기본에서는 narrative 가 즉시 fallback 반환.
    aggregate["indicator_narratives"] = _build_indicator_narratives(SUMMARY_STATE)
    # R3.1 / R3.2 — turn 단위 narrative 를 state 에 direct 주입해 HTML 렌더가 읽기만.
    _inject_turn_narratives(SUMMARY_STATE)

    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as report_file:
        json.dump(SUMMARY_STATE, report_file, ensure_ascii=False, indent=2)

    with open(REPORT_HTML_PATH, "w", encoding="utf-8") as report_file:
        report_file.write(render_summary_html(SUMMARY_STATE))


def _build_judge_model():
    """
    Judge 용 Ollama LLM 인스턴스 생성. deepeval 3.x 의 OllamaModel 은 base_url 과 model 을
    받는다. temperature=0 고정 (결정성 확보).
    """
    return OllamaModel(
        model=JUDGE_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0,
    )


def _collect_judge_meta() -> dict:
    """
    Phase 3.1 Q2 — Judge LLM 의 재현성·감사용 메타를 고정 수집.

    필드:
    - model: JUDGE_MODEL env (또는 기본 qwen3-coder:30b)
    - base_url: OLLAMA_BASE_URL
    - temperature: 고정 0 (DeepEval + translate 모두 0)
    - digest: Ollama `/api/show` best-effort (실패 시 None)
    """
    meta = {
        "model": JUDGE_MODEL,
        "base_url": OLLAMA_BASE_URL,
        "temperature": 0,
        "digest": None,
    }
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/show",
            json={"name": JUDGE_MODEL},
            timeout=3,
        )
        if resp.ok:
            payload = resp.json() or {}
            meta["digest"] = payload.get("digest") or (payload.get("details") or {}).get("digest")
    except Exception:
        # Ollama 미가동·네트워크 이슈 — digest 는 None 으로 남김
        pass
    return meta


# _collect_dataset_meta 는 Phase 4.2 Q4 에서 eval_runner/dataset.py 로 이전됨.
# (파일 상단에서 re-export 됨 — 하위 호환 유지)


def _build_indicator_narratives(state: dict) -> dict:
    """R2.1 — 11지표 각각에 대해 narrative 생성 (기본 off 면 fallback 텍스트)."""
    out = {}
    indicators = state.get("indicators") or {}
    for name, data in indicators.items():
        if data.get("kind") == "informational":
            # Latency / TokenUsage 는 정보성 — 해설 불필요 (fallback 도 비움)
            continue
        pass_count = int(data.get("pass") or 0)
        fail_count = int(data.get("fail") or 0)
        total = pass_count + fail_count
        if total == 0:
            continue  # N/A case 는 해설 생략
        out[name] = generate_indicator_narrative(
            indicator_name=name,
            pass_count=pass_count,
            total_count=total,
            threshold=data.get("threshold"),
            fail_case_ids=data.get("failed_case_ids") or [],
        )
    return out


def _inject_turn_narratives(state: dict) -> None:
    """
    R3.1 (기본 on) — 실패 turn 마다 `easy_explanation` 을 narrative 로 생성.
    R3.2 (기본 off) — 실패 turn 마다 `remediation` 을 생성 (off 면 빈 text).
    결과는 turn dict 에 직접 주입하여 HTML 렌더가 추가 호출 없이 읽는다.
    """
    for conversation in state.get("conversations") or []:
        for turn in conversation.get("turns") or []:
            if turn.get("status") == "failed":
                turn["easy_explanation"] = generate_easy_explanation(turn)
                turn["remediation"] = generate_remediation(turn)


def _upsert_conversation_report(conversation_report: dict):
    """
    conversation 단위 결과를 메모리 상태에 반영하고 즉시 리포트 파일을 갱신합니다.
    테스트 중간 실패가 있어도 마지막으로 성공한 대화와 실패 원인이 Jenkins에 남도록 설계합니다.
    """
    conversation_key = conversation_report["conversation_key"]
    for index, existing in enumerate(SUMMARY_STATE["conversations"]):
        if existing["conversation_key"] == conversation_key:
            SUMMARY_STATE["conversations"][index] = conversation_report
            _write_summary_report()
            return

    SUMMARY_STATE["conversations"].append(conversation_report)
    _write_summary_report()


# _resolve_existing_path + GOLDEN_CSV_PATH 는 Phase 4.2 Q4 에서 dataset.py 로 이전됨.
# (파일 상단에서 re-export 됨)


langfuse = None
if Langfuse and LANGFUSE_PUBLIC_KEY:
    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST,
    )


SUMMARY_STATE = _build_summary_state()
SUMMARY_STATE["golden_csv_path"] = str(GOLDEN_CSV_PATH)
SUMMARY_STATE["langfuse_enabled"] = bool(langfuse)



def _safe_json_loads(raw_text: str):
    """JSON 파싱 실패를 예외 대신 None으로 돌려주는 안전 래퍼입니다."""
    try:
        return json.loads(raw_text)
    except Exception:
        return None


def _safe_json_list(raw_text: str):
    """
    DeepEval의 context 입력은 list를 기대하므로,
    문자열 JSON 또는 이미 파싱된 값을 받아 최종적으로 list만 반환합니다.
    """
    parsed = _safe_json_loads(raw_text) if isinstance(raw_text, str) else raw_text
    return parsed if isinstance(parsed, list) else []


def _compact_output_for_relevancy(text: str) -> str:
    """
    AnswerRelevancyMetric에 장문/코드블록/이모지 노이즈가 주는 영향을 줄이기 위해
    첫 핵심 문장만 추출합니다.
    """
    if not text:
        return ""

    normalized = re.sub(r"```[\s\S]*?```", " ", str(text))
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return ""

    first_line = lines[0]
    first_line = re.sub(r"[\U00010000-\U0010FFFF]", "", first_line)
    first_line = re.sub(r"\s+", " ", first_line).strip()
    return first_line[:300]



def _parse_success_criteria_mode(criteria) -> str:
    """
    success_criteria가 규칙 DSL인지, 자연어 GEval 기준인지, 비어 있는지를 판별합니다.
    기존 시험지 문법과 문서 예시를 동시에 지원하기 위한 분기 함수입니다.
    pandas NaN(float) 같은 비-문자열 입력은 "none" 으로 방어 처리.
    """
    if not criteria or not isinstance(criteria, str):
        return "none"
    if any(token in criteria for token in ("status_code=", "raw~r/", "json.")):
        return "dsl"
    return "geval"


def _normalize_match_text(text: str) -> str:
    """
    포함 여부 비교용 정규화:
    - 대소문자 무시
    - 공백/개행 제거
    """
    return re.sub(r"\s+", "", str(text or "")).lower()


def _evaluate_simple_contains_criteria(criteria: str, actual_output: str):
    """
    자연어 success_criteria 중 '응답에 X가 포함되어야 함' 패턴은
    LLM 심판 오판을 피하기 위해 결정론적으로 먼저 판정합니다.
    매칭 가능한 규칙이면 True/False, 아니면 None을 반환합니다.
    """
    if not criteria:
        return None

    criteria_text = str(criteria).strip()
    keyword = _extract_simple_contains_keyword(criteria_text)
    if not keyword:
        return None

    normalized_actual = _normalize_match_text(actual_output)
    normalized_keyword = _normalize_match_text(keyword)
    if not normalized_keyword:
        return None
    return normalized_keyword in normalized_actual


def _extract_simple_contains_keyword(criteria_text: str):
    """
    '응답에 X가 포함되어야 함/합니다' 패턴에서 X 키워드를 추출합니다.
    """
    patterns = [
        r"응답에\s*[\"'“”‘’]?(?P<keyword>.+?)[\"'“”‘’]?\s*(?:이|가|을|를)?\s*포함되어야\s*함",
        r"응답에\s*[\"'“”‘’]?(?P<keyword>.+?)[\"'“”‘’]?\s*(?:이|가|을|를)?\s*포함되어야\s*합니다",
        r"(?:response|output)\s+must\s+include\s+[\"']?(?P<keyword>.+?)[\"']?$",
    ]

    for pattern in patterns:
        match = re.search(pattern, criteria_text, re.IGNORECASE)
        if match:
            return (match.group("keyword") or "").strip()
    return None


def _json_get_path(obj, path: str):
    """
    json.foo.bar[0] 형태의 단순 경로 문법을 따라 값을 추출합니다.
    success_criteria의 `json.<path>~r/.../` 규칙을 처리하기 위한 최소 기능만 구현합니다.
    """
    current = obj
    for token in path.split("."):
        list_match = re.match(r"^([a-zA-Z0-9_\-]+)\[(\d+)\]$", token)
        if list_match:
            key, index = list_match.group(1), int(list_match.group(2))
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
            continue

        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _evaluate_rule_based_criteria(criteria_str: str, result) -> bool:
    """
    SUCCESS_CRITERIA_GUIDE.md의 규칙 기반 문법을 해석합니다.
    조건들은 모두 AND 관계로 처리하며, 하나라도 불일치하면 즉시 실패합니다.
    """
    if not criteria_str:
        return result.http_status == 200

    conditions = [condition.strip() for condition in criteria_str.split(" AND ")]
    parsed_json = _safe_json_loads(result.raw_response or "")

    for condition in conditions:
        if condition.startswith("status_code="):
            # HTTP 상태코드는 가장 직접적인 성공 신호입니다.
            expected_code = condition.split("=", 1)[1].strip()
            if str(result.http_status) != expected_code:
                return False
            continue

        if condition.startswith("raw~r/"):
            # raw_response 전체 문자열에 대한 정규식 매칭입니다.
            pattern = condition[len("raw~r/") :]
            if pattern.endswith("/"):
                pattern = pattern[:-1]
            if not re.search(pattern, result.raw_response or ""):
                return False
            continue

        if "~r/" in condition and condition.startswith("json."):
            # JSON 응답의 특정 경로 값을 꺼내 정규식으로 검증합니다.
            left, right = condition.split("~r/", 1)
            json_path = left.replace("json.", "", 1)
            pattern = right[:-1] if right.endswith("/") else right
            value = _json_get_path(parsed_json, json_path) if parsed_json is not None else None
            if value is None or not re.search(pattern, str(value)):
                return False
            continue

        return False

    return True


def _score_task_completion(turn, result, judge, span=None):
    """
    과업 완료 여부를 채점합니다.
    - DSL이면 결정론적 규칙 검사
    - 자연어면 GEval 심판 채점
    - 조건이 없으면 HTTP 성공 여부로 최소 판정
    """
    success_criteria = turn.get("success_criteria") or turn.get("expected_output")
    criteria_mode = _parse_success_criteria_mode(success_criteria)

    if criteria_mode == "dsl":
        score = 1.0 if _evaluate_rule_based_criteria(success_criteria, result) else 0.0
        reason = "Rule-based success_criteria evaluation"
    elif criteria_mode == "geval":
        simple_contains_result = _evaluate_simple_contains_criteria(success_criteria, result.actual_output)
        if simple_contains_result is not None:
            matched_keyword = _extract_simple_contains_keyword(str(success_criteria or "")) or ""
            score = 1.0 if simple_contains_result else 0.0
            reason = f"규칙 기반 포함 검사 통과: 응답에 '{matched_keyword}'가 포함되어 있습니다."
            if not simple_contains_result:
                reason = (
                    f"규칙 기반 포함 검사 실패: 응답에 '{matched_keyword}'가 포함되어야 하나 찾지 못했습니다."
                )
        else:
            # 문서가 권장하는 자연어 success_criteria는 GEval 심판이 판정합니다.
            task_completion_metric = GEval(
                name="TaskCompletion",
                criteria=TASK_COMPLETION_CRITERIA,
                evaluation_params=[
                    LLMTestCaseParams.INPUT,
                    LLMTestCaseParams.ACTUAL_OUTPUT,
                    LLMTestCaseParams.EXPECTED_OUTPUT,
                ],
                model=judge,
                async_mode=False,
            )
            completion_test_case = LLMTestCase(
                input=turn["input"],
                actual_output=result.actual_output,
                expected_output=success_criteria,
            )
            task_completion_metric.measure(completion_test_case)
            _increment_judge_call_count()
            initial_score = float(task_completion_metric.score)
            reason = task_completion_metric.reason

            def _remeasure_task_completion():
                task_completion_metric.measure(completion_test_case)
                _increment_judge_call_count()
                return float(task_completion_metric.score)

            # Phase 5.2 Q7-b — 경계 case N-repeat. REPEAT_BORDERLINE_N=1(기본) 이면 no-op.
            score, _ = _rescore_borderline(
                initial_score,
                _remeasure_task_completion,
                TASK_COMPLETION_THRESHOLD,
            )
    else:
        score = 1.0 if result.http_status < 400 else 0.0
        reason = "No success_criteria provided; falling back to HTTP success."

    if span:
        # 관제 시스템에서 턴별 과업 완료 점수를 바로 볼 수 있게 span에도 기록합니다.
        span.score(name="TaskCompletion", value=score, comment=reason)

    return {
        "name": "TaskCompletion",
        "mode": criteria_mode,
        "score": score,
        "threshold": TASK_COMPLETION_THRESHOLD,
        "passed": score >= TASK_COMPLETION_THRESHOLD,
        "reason": reason,
    }


def _score_deepeval_metrics(turn, result, judge, span=None):
    """
    문맥 기반 품질 지표를 수행합니다.
    기본 지표는 답변 관련성/유해성이고, retrieval_context가 있을 때만 RAG 지표를 추가합니다.
    """
    base_test_case = LLMTestCase(
        input=turn["input"],
        actual_output=result.actual_output,
        expected_output=turn.get("expected_output"),
        retrieval_context=result.retrieval_context,
        context=_safe_json_list(turn.get("context_ground_truth", "[]")),
    )

    metrics = [
        AnswerRelevancyMetric(threshold=ANSWER_RELEVANCY_THRESHOLD, model=judge, async_mode=False),
        ToxicityMetric(threshold=TOXICITY_THRESHOLD, model=judge, async_mode=False),
    ]

    if result.retrieval_context and base_test_case.context:
        # 검색 문맥이 있어야 Faithfulness/Recall/Precision 지표가 의미를 가집니다.
        metrics.extend(
            [
                FaithfulnessMetric(threshold=FAITHFULNESS_THRESHOLD, model=judge, async_mode=False),
                ContextualRecallMetric(threshold=CONTEXTUAL_RECALL_THRESHOLD, model=judge, async_mode=False),
            ]
        )
        metrics.append(ContextualPrecisionMetric(threshold=CONTEXTUAL_PRECISION_THRESHOLD, model=judge, async_mode=False))

    metric_results = []
    for metric in metrics:
        if isinstance(metric, AnswerRelevancyMetric):
            compact_test_case = LLMTestCase(
                input=turn["input"],
                actual_output=_compact_output_for_relevancy(result.actual_output),
                expected_output=turn.get("expected_output"),
                retrieval_context=result.retrieval_context,
                context=_safe_json_list(turn.get("context_ground_truth", "[]")),
            )
            metric.measure(compact_test_case)
            _chosen_test_case = compact_test_case
        else:
            metric.measure(base_test_case)
            _chosen_test_case = base_test_case
        _increment_judge_call_count()

        # Phase 5.2 Q7-b — 경계 case N-repeat. 에러/None score 는 skip.
        if not metric.error and metric.score is not None:
            def _remeasure(m=metric, tc=_chosen_test_case):
                m.measure(tc)
                _increment_judge_call_count()
                return m.score

            final_score, _samples = _rescore_borderline(
                metric.score, _remeasure, metric.threshold
            )
            if final_score is not None and final_score != metric.score:
                try:
                    metric.score = float(final_score)
                except Exception:
                    pass

        if span:
            span.score(name=metric.__class__.__name__, value=metric.score, comment=metric.reason)
        metric_results.append(
            {
                "name": metric.__class__.__name__,
                "score": metric.score,
                "threshold": metric.threshold,
                "passed": False if metric.error else metric.is_successful(),
                "reason": metric.reason,
                "error": metric.error,
            }
        )

    return metric_results


@pytest.mark.parametrize("conversation", load_dataset())
def test_evaluation(conversation):
    """
    하나의 conversation을 끝까지 평가하는 메인 테스트입니다.
    문서의 흐름대로 어댑터 호출 -> Fail-Fast 검사 -> 과업 완료 -> 심층 평가 -> 멀티턴 평가를 수행합니다.
    """
    # conversation_id가 비어 있는 단일턴 케이스는 case_id를 conversation key로 사용해야
    # summary에서 서로 덮어쓰지 않고 개별 케이스로 집계됩니다.
    raw_conversation_id = conversation[0].get("conversation_id")
    conv_id = raw_conversation_id if not _is_blank_value(raw_conversation_id) else conversation[0]["case_id"]
    parent_trace = None
    if langfuse:
        # conversation 단위 상위 trace를 만들고 모든 턴 span을 그 아래에 연결합니다.
        parent_trace = langfuse.trace(
            name=f"Conversation-{conv_id}",
            id=f"{RUN_ID}:{conv_id}",
            tags=[RUN_ID],
        )

    conversation_history = []
    full_conversation_passed = True
    # Phase 6: Jenkins 콘솔에서 진행 상황을 실시간으로 볼 수 있도록 시작 라인을 flush.
    print(f"[eval] ▶ conversation={conv_id} turns={len(conversation)} target_type={TARGET_TYPE}", flush=True)
    adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY, TARGET_AUTH_HEADER)
    judge = _build_judge_model()
    conversation_report = {
        "conversation_id": raw_conversation_id if not _is_blank_value(raw_conversation_id) else None,
        "conversation_key": str(conv_id),
        "status": "passed",
        "failure_message": "",
        "turns": [],
        "multi_turn_consistency": None,
    }

    try:
        for turn in conversation:
            case_id = turn["case_id"]
            input_text = turn["input"]
            expected_outcome_raw = str(turn.get("expected_outcome") or "pass").strip().lower()
            expected_outcome = "fail" if expected_outcome_raw in ("fail", "failed", "negative", "f") else "pass"
            turn_report = {
                "case_id": case_id,
                "turn_id": turn.get("turn_id"),
                "input": input_text,
                "expected_output": str(turn.get("expected_output") or ""),
                "success_criteria": str(turn.get("success_criteria") or ""),
                "expected_outcome": expected_outcome,
                "status": "passed",
                "latency_ms": None,
                "policy_check": None,
                "schema_check": None,
                "task_completion": None,
                "metrics": [],
                "actual_output": "",
                "raw_response": "",
                "usage": None,
                "has_retrieval_context": False,
                "has_context_ground_truth": bool(_safe_json_list(turn.get("context_ground_truth", "[]"))),
                "failure_message": "",
                # Phase 3.3 Q1: 에러 분류 구조화. passed turn 은 None.
                # adapter 실패 → "system" (adapter 에서 세팅됨, 여기서 전사).
                # policy/schema/task/metric 실패 → "quality" (except 블록에서 세팅).
                "error_type": None,
            }

            span = None
            if parent_trace:
                # 각 턴을 별도 span으로 남겨 어느 지점에서 실패했는지 추적 가능하게 합니다.
                span = parent_trace.span(name=f"Turn-{turn.get('turn_id', 1)}", input={"input": input_text})

            try:
                # 같은 conversation 안에서는 동일 어댑터 인스턴스를 재사용합니다.
                # 특히 ui_chat은 같은 브라우저 세션이 유지되어야 실제 멀티턴 검증이 됩니다.
                result = adapter.invoke(input_text, history=conversation_history)
                # 실패 단계와 무관하게 원 응답을 먼저 저장해 summary에서 항상 비교 가능하게 유지합니다.
                turn_report["actual_output"] = result.actual_output or ""
                turn_report["raw_response"] = result.raw_response or ""
                turn["actual_output"] = result.actual_output or ""

                update_payload = {"output": result.to_dict()}
                if result.usage:
                    update_payload["usage"] = result.usage
                    turn_report["usage"] = result.usage
                turn_report["has_retrieval_context"] = bool(result.retrieval_context)

                if span:
                    # 응답 원문, 사용량, 지연시간을 먼저 기록해 사후 분석 데이터를 확보합니다.
                    span.update(**update_payload)
                    span.score(name="Latency", value=result.latency_ms, comment="ms")
                turn_report["latency_ms"] = result.latency_ms

                if result.error:
                    # Phase 3.3 Q1: adapter 가 이미 error_type 을 세팅 (대부분 "system").
                    turn_report["error_type"] = result.error_type or "system"
                    raise RuntimeError(f"Adapter Error: {result.error}")

                # 1차 차단: 정책 위반 및 응답 규격 검사
                _promptfoo_policy_check(result.raw_response)
                turn_report["policy_check"] = {"name": "PolicyCheck", "passed": True}
                if TARGET_TYPE == "http":
                    _schema_validate(result.raw_response)
                    turn_report["schema_check"] = {"name": "SchemaValidation", "status": "passed"}
                else:
                    turn_report["schema_check"] = {"name": "SchemaValidation", "status": "skipped"}

                # 2차 평가: 과업 완료 여부 판정
                task_completion_detail = _score_task_completion(turn, result, judge, span)
                turn_report["task_completion"] = task_completion_detail
                if not task_completion_detail["passed"]:
                    raise AssertionError(
                        f"TaskCompletion failed with score {task_completion_detail['score']}. "
                        f"Reason: {task_completion_detail['reason']}"
                    )

                # 다음 턴 입력에 사용할 수 있도록 assistant 응답을 대화 이력에 누적합니다.
                conversation_history.append(turn)

                # 3차 평가: 답변 품질 및 RAG 지표 측정
                metric_results = _score_deepeval_metrics(turn, result, judge, span)
                turn_report["metrics"] = metric_results
                failed_metrics = []
                for metric in metric_results:
                    if metric["error"]:
                        failed_metrics.append(f"{metric['name']}: {metric['error']}")
                    elif not metric["passed"]:
                        failed_metrics.append(
                            f"{metric['name']} (score={metric['score']}, threshold={metric['threshold']}): "
                            f"{metric['reason']}"
                        )
                if failed_metrics:
                    raise AssertionError("Metrics failed: " + "; ".join(failed_metrics))
            except Exception as exc:
                full_conversation_passed = False
                turn_report["status"] = "failed"
                turn_report["failure_message"] = str(exc)
                # Phase 3.3 Q1: error_type 이 아직 미세팅(adapter 외 실패) → quality 로 분류.
                # adapter 가 이미 "system" 으로 세팅한 경우는 그대로 유지.
                if turn_report.get("error_type") is None:
                    turn_report["error_type"] = "quality"
                conversation_report["status"] = "failed"
                conversation_report["failure_message"] = str(exc)
                pytest.fail(f"Turn failed for case_id {case_id}: {exc}")
            finally:
                conversation_report["turns"].append(turn_report)
                if span:
                    # 실패 여부와 무관하게 span을 닫아 trace 구조를 깨지 않게 합니다.
                    span.end()

        if len(conversation) > 1:
            # 멀티턴 평가는 전체 대화록을 하나의 입력으로 다시 심판 모델에 제출합니다.
            full_transcript = ""
            for turn in conversation_history:
                full_transcript += f"User: {turn['input']}\n"
                full_transcript += f"Assistant: {turn['actual_output']}\n\n"

            consistency_metric = GEval(
                name="MultiTurnConsistency",
                criteria=MULTI_TURN_CONSISTENCY_CRITERIA,
                evaluation_params=[LLMTestCaseParams.INPUT],
                model=judge,
                async_mode=False,
            )
            consistency_test_case = LLMTestCase(input=full_transcript, actual_output="")
            consistency_metric.measure(consistency_test_case)

            if parent_trace:
                parent_trace.score(
                    name=consistency_metric.name,
                    value=consistency_metric.score,
                    comment=consistency_metric.reason,
                )

            conversation_report["multi_turn_consistency"] = {
                "name": consistency_metric.name,
                "score": float(consistency_metric.score),
                "threshold": MULTI_TURN_CONSISTENCY_THRESHOLD,
                "passed": consistency_metric.score >= MULTI_TURN_CONSISTENCY_THRESHOLD,
                "reason": consistency_metric.reason,
            }

            if consistency_metric.score < MULTI_TURN_CONSISTENCY_THRESHOLD:
                conversation_report["status"] = "failed"
                conversation_report["failure_message"] = consistency_metric.reason
                pytest.fail(
                    f"MultiTurnConsistency failed for conversation {conv_id} with score "
                    f"{consistency_metric.score}. Reason: {consistency_metric.reason}"
                )

        if not full_conversation_passed:
            # 개별 턴 실패를 conversation 수준 실패로 다시 명시합니다.
            pytest.fail("One or more turns in the conversation failed.")
    finally:
        # conversation 단위 자원은 여기서 정리합니다.
        adapter.close()
        _upsert_conversation_report(conversation_report)
