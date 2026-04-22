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
import subprocess
import tempfile
import time
import uuid
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
from reporting.narrative import generate_exec_summary

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

# 디렉터리 구성
MODULE_ROOT = Path(__file__).resolve().parents[1]   # eval_runner/ 디렉터리
CONFIG_ROOT = MODULE_ROOT / "configs"               # security.yaml, schema.json 등
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "/var/knowledges/eval/reports"))  # 리포트 출력 경로
REPORT_JSON_PATH = REPORT_DIR / "summary.json"      # 프로그래밍 처리용 JSON 리포트
REPORT_HTML_PATH = REPORT_DIR / "summary.html"      # Jenkins 아티팩트용 HTML 리포트

# 평가 데이터셋(golden.csv) 탐색 경로: 환경변수가 없으면 아래 순서대로 검색
DEFAULT_GOLDEN_PATHS = [
    MODULE_ROOT / "data" / "golden.csv",                    # 개발 환경
    Path("/var/knowledges/eval/data/golden.csv"),            # Jenkins 볼륨 마운트
    Path("/var/jenkins_home/knowledges/eval/data/golden.csv"),  # Jenkins 홈 디렉터리
    Path("/app/data/golden.csv"),                            # Docker 앱 디렉터리
]

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

    for conversation in conversations:
        if conversation.get("status") == "passed":
            passed_conversations += 1
        else:
            failed_conversations += 1

        multi_turn_detail = conversation.get("multi_turn_consistency")
        if multi_turn_detail:
            _append_metric_average(metric_scores, multi_turn_detail["name"], multi_turn_detail.get("score"))

        for turn in conversation.get("turns", []):
            total_turns += 1
            if turn.get("status") == "passed":
                passed_turns += 1
            else:
                failed_turns += 1

            task_completion = turn.get("task_completion")
            if task_completion:
                _append_metric_average(metric_scores, task_completion["name"], task_completion.get("score"))

            for metric_detail in turn.get("metrics", []):
                _append_metric_average(metric_scores, metric_detail["name"], metric_detail.get("score"))

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
    SUMMARY_STATE["metric_averages"] = {
        metric_name: round(sum(scores) / len(scores), 4) for metric_name, scores in metric_scores.items() if scores
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
    aggregate["exec_summary"] = generate_exec_summary(SUMMARY_STATE)

    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as report_file:
        json.dump(SUMMARY_STATE, report_file, ensure_ascii=False, indent=2)

    with open(REPORT_HTML_PATH, "w", encoding="utf-8") as report_file:
        report_file.write(render_summary_html(SUMMARY_STATE))


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


def _resolve_existing_path(env_value: str, fallback_paths):
    """
    환경변수에 명시된 경로가 있으면 그것을 사용하고,
    없으면 러너가 일반적으로 배포되는 위치들을 순서대로 탐색해 첫 번째 존재 경로를 선택합니다.
    """
    if env_value:
        return Path(env_value).expanduser()
    for path in fallback_paths:
        if path.exists():
            return path
    return Path(fallback_paths[0])


GOLDEN_CSV_PATH = _resolve_existing_path(os.environ.get("GOLDEN_CSV_PATH"), DEFAULT_GOLDEN_PATHS)


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


def _turn_sort_key(value):
    """
    turn_id를 정렬 가능한 값으로 바꿉니다.
    숫자는 숫자 순서대로, 문자열은 문자열 순서대로, 누락값은 가장 뒤로 보냅니다.
    """
    if value is None:
        return (1, 0)
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (0, str(value))


def _is_blank_value(value) -> bool:
    """
    CSV 로딩 후 들어오는 None/NaN/공백 문자열을 모두 비어 있는 값으로 취급합니다.
    단일턴 케이스가 `conversation_id=nan`으로 잘못 묶이는 것을 막기 위한 정규화입니다.
    """
    if value is None:
        return True
    if pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def load_dataset():
    """
    `golden.csv`를 읽어 conversation 단위로 그룹화합니다.
    `conversation_id`가 있으면 멀티턴으로 묶고, 없으면 각 row를 단일 턴 대화 1개로 취급합니다.
    """
    if not GOLDEN_CSV_PATH.exists():
        raise FileNotFoundError(f"Evaluation dataset not found at {GOLDEN_CSV_PATH}")

    df = pd.read_csv(GOLDEN_CSV_PATH)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if "conversation_id" not in df.columns:
        # 레거시 단일 턴 포맷입니다.
        return [[record] for record in records]

    grouped_conversations = {}
    grouped_order = []
    single_turn_conversations = []

    for record in records:
        conversation_id = record.get("conversation_id")
        if not _is_blank_value(conversation_id):
            # 같은 conversation_id를 가진 row들을 하나의 대화로 모읍니다.
            conversation_key = str(conversation_id)
            if conversation_key not in grouped_conversations:
                grouped_conversations[conversation_key] = []
                grouped_order.append(conversation_key)
            grouped_conversations[conversation_key].append(record)
        else:
            # conversation_id가 비어 있으면 독립 대화로 유지합니다.
            record["conversation_id"] = None
            single_turn_conversations.append([record])

    conversations = []
    for conversation_key in grouped_order:
        turns = grouped_conversations[conversation_key]
        if "turn_id" in df.columns:
            # turn_id 기준 정렬로 사용자-에이전트 문맥 순서를 고정합니다.
            turns = sorted(turns, key=lambda turn: _turn_sort_key(turn.get("turn_id")))
        conversations.append(turns)

    conversations.extend(single_turn_conversations)
    return conversations


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


def _config_path(filename: str) -> Path:
    """평가 러너 모듈 위치를 기준으로 설정 파일 절대경로를 계산합니다."""
    return CONFIG_ROOT / filename


def _build_judge_model():
    """
    DeepEval 최신 OllamaModel을 사용해 심판 LLM 객체를 생성합니다.
    모든 DeepEval/GEval 호출이 동일한 모델 설정을 공유하도록 중앙화합니다.
    """
    return OllamaModel(model=JUDGE_MODEL, base_url=OLLAMA_BASE_URL.rstrip("/"))


def _promptfoo_relpath(path: Path, base_dir: Path) -> str:
    """
    Promptfoo CLI는 절대경로를 작업 디렉터리에 다시 붙여 해석하는 경우가 있어
    항상 명시적으로 고정한 기준 디렉터리 상대경로로 넘깁니다.
    """
    return os.path.relpath(path, start=base_dir)


def _promptfoo_policy_check(raw_text: str):
    """
    Promptfoo 최신 CLI의 `--assertions` + `--model-outputs` 흐름으로
    응답 원문에 금칙 패턴이 있는지 검사합니다.
    컨테이너 이미지에 promptfoo를 고정 설치해 매 실행 시 다운로드를 피합니다.
    """
    config_path = _config_path("security.yaml")
    if not config_path.exists():
        return

    tmp_dir = None
    promptfoo_cwd = Path(__file__).resolve().parent
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix=".promptfoo-", dir=promptfoo_cwd))
        model_outputs_path = tmp_dir / f"{uuid.uuid4().hex}-outputs.json"
        result_path = tmp_dir / f"{uuid.uuid4().hex}-result.json"

        with open(model_outputs_path, "w", encoding="utf-8") as output_file:
            json.dump([raw_text or ""], output_file, ensure_ascii=False)

        # 결과 JSON을 파일로 남겨 CLI 출력 포맷 변화와 무관하게 실패/에러 건수를 읽습니다.
        command = [
            "promptfoo",
            "eval",
            "--assertions",
            _promptfoo_relpath(config_path, promptfoo_cwd),
            "--model-outputs",
            _promptfoo_relpath(model_outputs_path, promptfoo_cwd),
            "--output",
            _promptfoo_relpath(result_path, promptfoo_cwd),
            "--no-write",
            "--no-table",
        ]
        process = subprocess.run(command, capture_output=True, text=True, cwd=promptfoo_cwd)
        if process.returncode not in (0, 100) and not result_path.exists():
            raise RuntimeError(process.stderr or process.stdout or "Promptfoo failed")

        if result_path.exists():
            with open(result_path, "r", encoding="utf-8") as result_file:
                result_payload = json.load(result_file) or {}
            stats = ((result_payload.get("results") or {}).get("stats") or {})
            failures = stats.get("failures", 0)
            errors = stats.get("errors", 0)
            if errors:
                raise RuntimeError(f"Promptfoo policy checks reported {errors} error(s).")
            if failures:
                raise RuntimeError(f"Promptfoo policy checks reported {failures} failure(s).")
    finally:
        # Promptfoo 임시 산출물이 워크스페이스에 누적되지 않게 대화별로 정리합니다.
        if tmp_dir and tmp_dir.exists():
            for child in tmp_dir.iterdir():
                child.unlink(missing_ok=True)
            tmp_dir.rmdir()


def _schema_validate(raw_text: str):
    """
    API 응답이 약속된 JSON 스키마를 만족하는지 검사합니다.
    UI 평가처럼 비JSON 응답이 자연스러운 경우는 상위 호출부에서 이 함수를 건너뜁니다.
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


def _parse_success_criteria_mode(criteria: str) -> str:
    """
    success_criteria가 규칙 DSL인지, 자연어 GEval 기준인지, 비어 있는지를 판별합니다.
    기존 시험지 문법과 문서 예시를 동시에 지원하기 위한 분기 함수입니다.
    """
    if not criteria:
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
            score = float(task_completion_metric.score)
            reason = task_completion_metric.reason
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
        else:
            metric.measure(base_test_case)
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
