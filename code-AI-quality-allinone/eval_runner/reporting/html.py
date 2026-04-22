"""
reporting.html — summary.json 상태를 받아 단일 HTML 문자열로 렌더

test_runner 의 SUMMARY_STATE 와 분리된 순수 렌더링 함수 집합. 호출 측은
`render_summary_html(state: dict) -> str` 만 쓰면 된다.

Phase 2 R1~R6 개편은 이 모듈 안에서 진행한다:
- R1 임원 요약 헤더
- R2 11지표 카드 대시보드
- R3 Case drill-down
- R4 시스템 에러 vs 품질 실패 분리
- R5 Build-over-build delta (스트레치)
- R6 Jenkins publishHTML 통합 (Jenkinsfile post 블록 쪽)

현재 Step 2.0 은 기존 로직을 기능 변화 0 으로 이전만 수행한다.
"""

from html import escape

from reporting.translate import (
    escape_with_linebreaks,
    translate_text_to_korean,
)


# ============================================================================
# 표시 이름 매핑 (Phase 2 R2 에서 지표 카드 라벨 재사용)
# ============================================================================

METRIC_DISPLAY_NAMES = {
    "TaskCompletion": "과업 달성도 (Task Completion)",
    "AnswerRelevancyMetric": "답변 관련성 (Answer Relevancy)",
    "ToxicityMetric": "유해성 (Toxicity)",
    "FaithfulnessMetric": "근거 충실도 (Faithfulness, RAG 전용)",
    "ContextualRecallMetric": "문맥 재현율 (Contextual Recall, RAG 전용)",
    "ContextualPrecisionMetric": "문맥 정밀도 (Contextual Precision, RAG 전용)",
    "MultiTurnConsistency": "멀티턴 일관성 (Multi-turn Consistency)",
}

THRESHOLD_DISPLAY_NAMES = {
    "task_completion": "과업 달성도",
    "answer_relevancy": "답변 관련성",
    "toxicity": "유해성(이하)",
    "faithfulness": "근거 충실도",
    "contextual_recall": "문맥 재현율",
    "contextual_precision": "문맥 정밀도",
    "multi_turn_consistency": "멀티턴 일관성",
}


# ============================================================================
# 소형 헬퍼 (test_runner._is_blank_value 와 대칭 — 리포트 판단용)
# ============================================================================

def _is_blank_conversation_id(value) -> bool:
    """단일턴 case 를 묶지 않기 위한 conv_id 공백 판정. test_runner 의 동명 함수와 규약 동일."""
    if value is None:
        return True
    try:
        import pandas as pd

        if pd.isna(value):
            return True
    except Exception:
        pass
    if isinstance(value, str) and not value.strip():
        return True
    return False


# ============================================================================
# 부분 렌더러 (metric 리스트, 토큰 사용량, 빌더 헬퍼)
# ============================================================================

def _render_metric_list(metric_details):
    """
    HTML 리포트에 표시할 metric 리스트를 렌더링합니다.
    각 항목에 score/threshold/pass 여부와 이유를 함께 남겨 Jenkins 아티팩트만으로도 원인 파악이 가능하게 합니다.
    """
    if not metric_details:
        return "<em>측정 결과가 없습니다.</em>"

    items = []
    for metric in metric_details:
        status = metric.get("status")
        if not status:
            passed = metric.get("passed")
            if passed is True:
                status = "PASS"
            elif passed is False:
                status = "FAIL"
            else:
                status = "SKIPPED"
        status_label = {
            "PASS": "통과",
            "FAIL": "실패",
            "SKIPPED": "건너뜀",
        }.get(status, str(status))
        reason_text = str(metric.get("reason") or metric.get("error") or "")
        reason = escape_with_linebreaks(translate_text_to_korean(reason_text))
        score = metric.get("score")
        threshold = metric.get("threshold")
        score_display = "-" if score is None else score
        threshold_display = "-" if threshold is None else threshold
        metric_name = str(metric.get("name") or "")
        metric_label = METRIC_DISPLAY_NAMES.get(metric_name, metric_name)
        items.append(
            "<li>"
            f"<strong>{escape(metric_label)}</strong> "
            f"[{status_label}] 점수={score_display}, 기준={threshold_display}"
            f"<br><span>{reason}</span>"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _skipped_metric(name: str, reason: str):
    """요약 화면에서 미실행 지표를 명시적으로 SKIPPED 상태로 표현합니다."""
    return {
        "name": name,
        "score": None,
        "threshold": None,
        "passed": None,
        "reason": reason,
        "error": None,
        "status": "SKIPPED",
    }


def _format_token_usage(usage):
    """어댑터별 키 차이를 흡수해 토큰 사용량을 단일 문자열로 표시합니다."""
    if not usage or not isinstance(usage, dict):
        return "-"

    prompt = usage.get("promptTokens")
    completion = usage.get("completionTokens")
    total = usage.get("totalTokens")
    if prompt is None:
        prompt = usage.get("prompt_tokens")
    if completion is None:
        completion = usage.get("completion_tokens")
    if total is None:
        total = usage.get("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)

    if prompt is None and completion is None and total is None:
        return "-"
    return f"입력={int(prompt or 0)}, 출력={int(completion or 0)}, 합계={int(total or 0)}"


def _build_task_completion_display(turn: dict):
    """
    Task Completion 표시용 리스트를 구성합니다.
    실제 측정값이 없으면 실패 지점에 맞춰 SKIPPED 이유를 보여줍니다.
    """
    task_completion = turn.get("task_completion")
    if task_completion:
        return [task_completion]

    failure = str(turn.get("failure_message") or "")
    if "Adapter Error" in failure:
        reason = "어댑터 호출 실패로 과업 달성도 평가를 건너뛰었습니다."
    elif "Promptfoo policy checks reported" in failure:
        reason = "보안 정책 검사 실패로 과업 달성도 평가를 건너뛰었습니다."
    elif "Format Compliance Failed" in failure:
        reason = "응답 형식 검사 실패로 과업 달성도 평가를 건너뛰었습니다."
    else:
        reason = "이전 단계 실패로 과업 달성도 평가를 건너뛰었습니다."
    return [_skipped_metric("TaskCompletion", reason)]


def _build_deepeval_metrics_display(turn: dict):
    """
    DeepEval 지표 표시 리스트를 구성합니다.
    - 실행된 지표는 실제 결과를 그대로 사용
    - 실행되지 않은 지표는 SKIPPED로 보강
    """
    metric_names = [
        "AnswerRelevancyMetric",
        "ToxicityMetric",
        "FaithfulnessMetric",
        "ContextualRecallMetric",
        "ContextualPrecisionMetric",
    ]
    existing_metrics = turn.get("metrics", []) or []
    existing_by_name = {metric.get("name"): metric for metric in existing_metrics}
    display_metrics = []

    task_completion = turn.get("task_completion")
    failure = str(turn.get("failure_message") or "")
    has_retrieval_context = bool(turn.get("has_retrieval_context"))
    has_context_ground_truth = bool(turn.get("has_context_ground_truth"))

    for metric_name in metric_names:
        if metric_name in existing_by_name:
            display_metrics.append(existing_by_name[metric_name])
            continue

        if task_completion and not task_completion.get("passed"):
            reason = "과업 달성도 실패로 해당 지표 평가를 건너뛰었습니다."
        elif "Adapter Error" in failure:
            reason = "어댑터 호출 실패로 해당 지표 평가를 건너뛰었습니다."
        elif "Promptfoo policy checks reported" in failure:
            reason = "보안 정책 검사 실패로 해당 지표 평가를 건너뛰었습니다."
        elif "Format Compliance Failed" in failure:
            reason = "응답 형식 검사 실패로 해당 지표 평가를 건너뛰었습니다."
        elif metric_name in (
            "FaithfulnessMetric",
            "ContextualRecallMetric",
            "ContextualPrecisionMetric",
        ) and not (has_retrieval_context and has_context_ground_truth):
            reason = "retrieval_context 또는 context_ground_truth가 없어 해당 지표를 건너뛰었습니다."
        else:
            reason = "이전 단계 실패로 해당 지표 평가를 건너뛰었습니다."

        display_metrics.append(_skipped_metric(metric_name, reason))

    return display_metrics


# ============================================================================
# Phase 2.2 R2 — 11지표 카드 대시보드
# ============================================================================

# 11지표 표시 순서 + 이름 (번호는 REPORT_SPEC §1.1 정본 기준)
INDICATOR_ORDER = [
    ("①", "PolicyCheck", "Policy Violation", "Fail-Fast"),
    ("②", "SchemaValidation", "Format Compliance", "Fail-Fast"),
    ("③", "TaskCompletion", "Task Completion", "과제"),
    ("④", "AnswerRelevancyMetric", "Answer Relevancy", "심층"),
    ("⑤", "ToxicityMetric", "Toxicity", "심층"),
    ("⑥", "FaithfulnessMetric", "Faithfulness", "심층 · RAG"),
    ("⑦", "ContextualRecallMetric", "Contextual Recall", "심층 · RAG"),
    ("⑧", "ContextualPrecisionMetric", "Contextual Precision", "심층 · RAG"),
    ("⑨", "MultiTurnConsistency", "Multi-turn Consistency", "다중턴"),
    ("⑩", "Latency", "Latency", "운영"),
    ("⑪", "TokenUsage", "Token Usage", "운영"),
]


def _render_indicator_cards(state: dict) -> str:
    """
    R2 — 11지표 카드 그리드. state["indicators"] 에서 읽음.
    각 카드: 배지(🟢/🔴/🟡/⚪), pass/total, threshold, 실패 case_id 최대 3개.
    Latency/TokenUsage 는 정보성 — 점수 대신 stats 요약.
    R2.1 (기본 off): state["aggregate"]["indicator_narratives"][name] 이 있으면
    카드 하단에 1줄 해설 추가.
    """
    indicators = state.get("indicators") or {}
    narratives = (state.get("aggregate") or {}).get("indicator_narratives") or {}

    cards = []
    for symbol, name, label, stage in INDICATOR_ORDER:
        data = indicators.get(name) or {}
        cards.append(_render_one_indicator_card(symbol, name, label, stage, data, narratives.get(name)))

    return (
        "<section class='section'>"
        "<div class='section-header'><h2>11 지표 대시보드</h2>"
        "<span class='meta-inline'>단계별 카드 · 🟢 PASS / 🔴 FAIL / 🟡 WARN / ⚪ N/A</span>"
        "</div>"
        f"<div class='indicator-grid'>{''.join(cards)}</div>"
        "</section>"
    )


def _render_one_indicator_card(symbol: str, name: str, label: str, stage: str,
                                data: dict, narrative: dict | None) -> str:
    """11지표 카드 1개."""
    kind = data.get("kind")
    passed = int(data.get("pass") or 0)
    failed = int(data.get("fail") or 0)
    skipped = int(data.get("skipped") or 0)
    total = passed + failed

    # 배지 + 색상 결정
    if kind == "informational":
        # ⑩ Latency, ⑪ TokenUsage — 정보성
        stats = data.get("stats") or {}
        badge_class = "skip"
        badge_text = "INFO"
        if name == "Latency":
            p50 = stats.get("p50")
            p95 = stats.get("p95")
            body = (
                f"<div class='ind-metric'>P50 <strong>{_fmt_num(p50)}ms</strong></div>"
                f"<div class='ind-metric'>P95 <strong>{_fmt_num(p95)}ms</strong></div>"
                f"<div class='ind-metric'>count <strong>{stats.get('count', 0)}</strong></div>"
            )
        else:  # TokenUsage
            body = (
                f"<div class='ind-metric'>합계 <strong>{stats.get('total', 0):,}</strong></div>"
                f"<div class='ind-metric'>입력 <strong>{stats.get('prompt', 0):,}</strong></div>"
                f"<div class='ind-metric'>출력 <strong>{stats.get('completion', 0):,}</strong></div>"
            )
    elif total == 0 and skipped == 0:
        # 적용된 case 0 → N/A
        badge_class = "skip"
        badge_text = "⚪ N/A"
        body = "<div class='ind-metric'>적용된 case 가 없습니다.</div>"
    elif total == 0 and skipped > 0:
        # 모두 SKIPPED
        badge_class = "skip"
        badge_text = "⚪ SKIP"
        body = f"<div class='ind-metric'>SKIPPED <strong>{skipped}</strong></div>"
    else:
        pass_rate = round(passed / total * 100, 1) if total else 0.0
        if failed == 0:
            badge_class, badge_text = "ok", "🟢 PASS"
        elif passed == 0:
            badge_class, badge_text = "fail", "🔴 FAIL"
        else:
            badge_class, badge_text = "warn", "🟡 WARN"
        threshold = data.get("threshold")
        threshold_line = (
            f"<div class='ind-metric'>threshold <strong>{threshold}</strong></div>"
            if threshold is not None else ""
        )
        failed_case_ids = data.get("failed_case_ids") or []
        fail_line = ""
        if failed_case_ids:
            shown = ", ".join(escape(cid) for cid in failed_case_ids[:3])
            more = f" 외 {len(failed_case_ids) - 3}" if len(failed_case_ids) > 3 else ""
            fail_line = f"<div class='ind-metric fail-ids'>실패: {shown}{more}</div>"
        body = (
            f"<div class='ind-metric'>pass <strong>{passed}/{total}</strong> ({pass_rate}%)</div>"
            f"{threshold_line}"
            f"{fail_line}"
        )

    # R2.1 narrative (opt-in)
    narrative_html = ""
    if narrative and isinstance(narrative, dict) and narrative.get("text"):
        source = str(narrative.get("source") or "fallback").lower()
        badge = {"llm": "🤖", "cached": "🤖", "fallback": "📋"}.get(source, "📋")
        narrative_html = (
            f"<div class='ind-narrative'>{badge} {escape(narrative['text'])}</div>"
        )

    return (
        "<div class='ind-card'>"
        "<div class='ind-head'>"
        f"<span class='ind-symbol'>{symbol}</span> "
        f"<strong>{escape(label)}</strong> "
        f"<span class='meta-inline'>({escape(stage)})</span>"
        f"<span class='badge {badge_class}'>{escape(badge_text)}</span>"
        "</div>"
        f"<div class='ind-body'>{body}</div>"
        f"{narrative_html}"
        "</div>"
    )


def _fmt_num(value) -> str:
    """None → '-', 숫자 → 그대로."""
    if value is None:
        return "-"
    return str(value)


# Phase 2.4 R4 — 임시 문자열 패턴 매칭. Phase 3 Q1 에서 UniversalEvalOutput.error_type
# 구조화 필드로 교체 예정.
_SYSTEM_ERROR_PATTERNS = (
    "Adapter Error",
    "Connection Error",
    "HTTP 5",
    "Timeout",
    "timeout",
    "Wrapper startup failed",
)


def _classify_error_type(turn: dict) -> str:
    """
    실패 turn 의 에러를 'system' vs 'quality' 로 분류.
    - Phase 3 이후: `turn['error_type']` enum 우선 사용 (아직 미구현)
    - 현재: failure_message 키워드 매칭으로 추정
    반환: 'system' | 'quality' | '' (passed turn)
    """
    status = str(turn.get("status") or "")
    if status != "failed":
        return ""
    # 선행: Phase 3 에서 주입될 구조화 필드
    explicit = str(turn.get("error_type") or "").lower()
    if explicit in ("system", "quality"):
        return explicit
    # 임시 휴리스틱
    failure = str(turn.get("failure_message") or "")
    for pattern in _SYSTEM_ERROR_PATTERNS:
        if pattern in failure:
            return "system"
    return "quality"


def classify_failure_buckets(state: dict) -> dict:
    """
    R4 집계 — 전체 실패를 {system, quality} 로 나눈 카운트.
    R1 헤더·exec_summary 섹션 외부 노출용.
    """
    system = 0
    quality = 0
    for conversation in state.get("conversations") or []:
        for turn in conversation.get("turns") or []:
            kind = _classify_error_type(turn)
            if kind == "system":
                system += 1
            elif kind == "quality":
                quality += 1
    return {"system": system, "quality": quality}


def _render_exec_summary_block(exec_summary: dict | None) -> str:
    """
    R1.1 — 🤖 LLM 임원 요약 섹션. `exec_summary` 는 `reporting.narrative.generate_exec_summary`
    의 반환 dict: `{text, source, role, reason?}`. 미설정이면 빈 문자열 반환 (섹션 자체 생략).

    provenance 배지는 source 에 따라:
    - "llm"      → 🤖 LLM 생성
    - "cached"   → 🤖 LLM (캐시)
    - "fallback" → 📋 기본 메시지
    """
    if not exec_summary or not isinstance(exec_summary, dict):
        return ""
    text = str(exec_summary.get("text") or "").strip()
    if not text:
        return ""

    source = str(exec_summary.get("source") or "fallback").lower()
    badge_class = {
        "llm": "llm",
        "cached": "cached",
        "fallback": "fallback",
    }.get(source, "fallback")
    badge_label = {
        "llm": "🤖 LLM 생성",
        "cached": "🤖 LLM (캐시)",
        "fallback": "📋 기본 메시지",
    }.get(source, "📋 기본 메시지")

    return (
        "<div class='exec-summary'>"
        "<h2>이번 빌드 한 줄 요약"
        f"<span class='provenance {badge_class}'>{escape(badge_label)}</span>"
        "</h2>"
        f"<div class='body'>{escape(text)}</div>"
        "</div>"
    )


def _build_multi_turn_display(conversation: dict):
    """
    Multi-turn 지표 표시를 구성합니다.
    1턴 대화나 조기 종료 대화에서도 SKIPPED 이유를 명시합니다.
    """
    if conversation.get("multi_turn_consistency"):
        return [conversation["multi_turn_consistency"]]

    turns = conversation.get("turns", []) or []
    if len(turns) <= 1:
        reason = "단일턴 대화라 멀티턴 일관성 평가는 건너뛰었습니다."
    else:
        reason = "대화가 중간에 실패하여 멀티턴 일관성 평가는 건너뛰었습니다."
    return [_skipped_metric("MultiTurnConsistency", reason)]


# ============================================================================
# 메인 렌더 — 상태 사전을 받아 단일 HTML 반환
# ============================================================================

def render_summary_html(state: dict) -> str:
    """
    Jenkins 아티팩트 탭에서 바로 열어볼 수 있는 단일 HTML 리포트를 생성합니다.
    비개발자도 이해하기 쉽도록 요약/상세를 분리하고, 단일턴/멀티턴 결과를 구분해 표시합니다.

    `state` 는 test_runner 의 SUMMARY_STATE 와 동일한 스키마의 dict.
    (Phase 0.3 REPORT_SPEC §4 와 호환. Phase 2 에서 점진 확장 예정.)
    """
    totals = state["totals"]
    metric_averages = state["metric_averages"]
    all_conversations = state.get("conversations", [])

    def _short_text(value, limit=180, translate=True):
        text = str(value or "").strip()
        if translate:
            text = translate_text_to_korean(text).strip()
        if not text:
            return "-"
        return text if len(text) <= limit else f"{text[:limit]}..."

    def _easy_explanation_with_provenance(turn: dict):
        """
        R3.1: turn 에 미리 주입된 `easy_explanation` dict 사용. 없으면 legacy
        하드코딩 로직으로 fallback (test_runner 미갱신 배포 호환).
        반환: (text, source) 튜플.
        """
        injected = turn.get("easy_explanation")
        if isinstance(injected, dict) and injected.get("text"):
            return injected["text"], str(injected.get("source") or "fallback").lower()
        return _legacy_easy_explanation(turn), "fallback"

    def _legacy_easy_explanation(turn: dict) -> str:
        """Legacy 하드코딩 경로 (Phase 2.0 narrative.py fallback 과 동일 규칙)."""
        status = str(turn.get("status") or "")
        failure = translate_text_to_korean(str(turn.get("failure_message") or ""))
        task_completion = turn.get("task_completion") or {}
        if status == "passed":
            return "질문 의도와 평가 기준을 모두 만족해 통과했습니다."
        if "Promptfoo policy checks reported" in failure or "정책 검사" in failure:
            return "민감정보 또는 금칙 패턴이 감지되어 보안 기준에서 실패했습니다."
        if "Format Compliance Failed" in failure or "응답 형식" in failure:
            return "응답 형식이 약속된 규격과 달라 연동 기준에서 실패했습니다."
        if "Adapter Error" in failure or "Connection Error" in failure:
            return "대상 시스템 통신에 실패해 평가를 진행할 수 없었습니다."
        if "TaskCompletion failed" in failure or (task_completion and task_completion.get("passed") is False):
            return "응답에 반드시 들어가야 할 핵심 정보가 빠져 과업 달성 기준을 통과하지 못했습니다."
        if "AnswerRelevancyMetric" in failure or "답변 관련성" in failure:
            return "질문과 직접 관련 없는 내용이 섞여 답변 관련성 기준을 통과하지 못했습니다."
        if "Metrics failed" in failure or "지표 평가 실패" in failure:
            return "품질 지표 중 하나 이상이 기준 미달이라 실패했습니다."
        return "평가 기준 미달로 실패했습니다."

    def _conversation_status_meta(status):
        if status == "passed":
            return ("통과", "ok", "정상 통과")
        if status == "failed":
            return ("실패", "fail", "실패")
        return ("알 수 없음", "skip", str(status or "unknown"))

    def _turn_status_meta(status):
        if status == "passed":
            return ("PASS", "ok", "통과")
        if status == "failed":
            return ("FAIL", "fail", "실패")
        return ("UNKNOWN", "skip", str(status or "unknown"))

    def _is_passed_conversation(status):
        return status == "passed"

    def _group_stats(conversations):
        total = len(conversations)
        passed = sum(1 for conversation in conversations if _is_passed_conversation(conversation.get("status")))
        failed = total - passed
        rate = round((passed / total) * 100, 2) if total else 0.0
        return {"total": total, "passed": passed, "failed": failed, "pass_rate": rate}

    def _is_multi_turn_conversation(conversation):
        return not _is_blank_conversation_id(conversation.get("conversation_id"))

    multi_turn_conversations = [conversation for conversation in all_conversations if _is_multi_turn_conversation(conversation)]
    single_turn_conversations = [conversation for conversation in all_conversations if not _is_multi_turn_conversation(conversation)]
    multi_stats = _group_stats(multi_turn_conversations)
    single_stats = _group_stats(single_turn_conversations)

    def _render_turn_rows(turns):
        rows = []
        for turn in turns:
            fail_fast_parts = []
            if turn.get("policy_check"):
                fail_fast_parts.append(f"보안 정책: {'통과' if turn['policy_check']['passed'] else '실패'}")
            if turn.get("schema_check"):
                schema_status = turn["schema_check"].get("status", "skipped")
                schema_status_label = {
                    "passed": "통과",
                    "skipped": "건너뜀",
                    "failed": "실패",
                }.get(str(schema_status).lower(), str(schema_status))
                fail_fast_parts.append(f"응답 형식: {schema_status_label}")
            fail_fast = "<br>".join(fail_fast_parts) if fail_fast_parts else "-"

            task_completion_html = _render_metric_list(_build_task_completion_display(turn))
            metrics_html = _render_metric_list(_build_deepeval_metrics_display(turn))
            token_usage_html = escape(_format_token_usage(turn.get("usage")))
            input_text = str(turn.get("input") or "-")
            expected_output_text = str(turn.get("expected_output") or "-")
            success_criteria_text = str(turn.get("success_criteria") or "-")
            actual_output_text = str(turn.get("actual_output") or turn.get("raw_response") or "-")
            input_preview = escape_with_linebreaks(_short_text(input_text, limit=220, translate=False), max_line_len=60)
            expected_preview = escape_with_linebreaks(_short_text(expected_output_text, limit=180, translate=False), max_line_len=60)
            success_criteria_preview = escape_with_linebreaks(_short_text(success_criteria_text, limit=180, translate=False), max_line_len=60)
            output_preview = escape_with_linebreaks(_short_text(actual_output_text, limit=260, translate=False), max_line_len=60)
            actual_output = escape(actual_output_text)
            input_full = escape(input_text)
            expected_output_full = escape(expected_output_text)
            success_criteria_full = escape(success_criteria_text)
            failure_raw = str(turn.get("failure_message") or "-")
            failure_localized = escape_with_linebreaks(translate_text_to_korean(failure_raw))
            failure_raw_escaped = escape_with_linebreaks(failure_raw)
            # R3.1 — 주입된 narrative (fallback 포함) + provenance 배지
            easy_text, easy_source = _easy_explanation_with_provenance(turn)
            easy_badge = {"llm": "🤖 LLM", "cached": "🤖 LLM (캐시)", "fallback": "📋"}.get(easy_source, "📋")

            # R3.2 — remediation (기본 off → text 비움 → 섹션 숨김)
            remediation = turn.get("remediation") if isinstance(turn.get("remediation"), dict) else None
            remediation_html = ""
            if remediation and remediation.get("text"):
                rem_source = str(remediation.get("source") or "fallback").lower()
                rem_badge = {"llm": "🤖 LLM", "cached": "🤖 LLM (캐시)", "fallback": "📋"}.get(rem_source, "📋")
                remediation_html = (
                    f"<p><strong>조치 권장 <span class='provenance {rem_source}'>{escape(rem_badge)}</span></strong>"
                    f"<br>{escape_with_linebreaks(remediation['text'], max_line_len=80)}</p>"
                )

            # R4 — 실패 사유의 에러 분류 (임시 문자열 매칭; Phase 3 Q1 이 error_type enum 도입 시 교체)
            err_type = _classify_error_type(turn)
            err_class = {"system": "system-err", "quality": "quality-err"}.get(err_type, "")
            err_label = {"system": "시스템 에러", "quality": "품질 실패"}.get(err_type, "")

            _, status_class, status_label = _turn_status_meta(turn.get("status"))

            if turn.get("status") == "failed":
                key_reason = turn.get("failure_message") or "실패"
            else:
                task_completion = turn.get("task_completion") or {}
                key_reason = task_completion.get("reason") or "정상 통과"

            detail_html = (
                "<details>"
                "<summary>평가 상세 보기</summary>"
                f"<p><strong>입력값</strong><pre>{input_full}</pre></p>"
                f"<p><strong>기대값</strong><pre>{expected_output_full}</pre></p>"
                f"<p><strong>성공조건</strong><pre>{success_criteria_full}</pre></p>"
                f"<p><strong>쉬운 해설 <span class='provenance {easy_source}'>{escape(easy_badge)}</span></strong>"
                f"<br>{escape_with_linebreaks(easy_text, max_line_len=80)}</p>"
                f"{remediation_html}"
                f"<p><strong>사전 검사</strong><br>{fail_fast}</p>"
                f"<p><strong>과업 달성도</strong><br>{task_completion_html}</p>"
                f"<p><strong>품질 지표</strong><br>{metrics_html}</p>"
                f"<p><strong>실제 AI 응답</strong><pre>{actual_output}</pre></p>"
                f"<p><strong>가독성 요약 사유</strong><br>{failure_localized}</p>"
                f"<p><strong>원문 실패 메시지</strong><pre>{failure_raw_escaped}</pre></p>"
                "</details>"
            )

            # R4: 실패 시 에러 분류 배지를 판정 배지 옆에 병기
            err_badge_html = (
                f"<span class='badge {err_class}'>{escape(err_label)}</span>"
                if err_class else ""
            )

            rows.append(
                "<tr>"
                f"<td>{escape(str(turn.get('case_id') or ''))}</td>"
                f"<td>{escape(str(turn.get('expected_outcome') or 'pass'))}</td>"
                f"<td><span class='badge {status_class}'>{escape(status_label)}</span> {err_badge_html}</td>"
                f"<td>{escape(str(turn.get('latency_ms') or '-'))}</td>"
                f"<td>{token_usage_html}</td>"
                f"<td>{input_preview}</td>"
                f"<td>{expected_preview}</td>"
                f"<td>{success_criteria_preview}</td>"
                f"<td>{output_preview}</td>"
                f"<td>{escape_with_linebreaks(_short_text(key_reason), max_line_len=60)}</td>"
                f"<td>{detail_html}</td>"
                "</tr>"
            )
        return "".join(rows)

    def _render_conversation_blocks(conversations):
        if not conversations:
            return "<p class='empty'>해당 유형의 대화 결과가 없습니다.</p>"

        blocks = []
        for conversation in conversations:
            status_text, status_class, status_label = _conversation_status_meta(conversation.get("status"))
            turn_count = len(conversation.get("turns", []))
            failure_message = conversation.get("failure_message") or "-"
            multi_turn_html = _render_metric_list(_build_multi_turn_display(conversation))
            turn_rows = _render_turn_rows(conversation.get("turns", []))
            conversation_title = escape(str(conversation.get("conversation_key") or "미지정"))

            blocks.append(
                "<details class='conversation' open>"
                "<summary>"
                f"<span class='badge {status_class}'>{escape(status_text)}</span> "
                f"<strong>{conversation_title}</strong> "
                f"<span class='meta-inline'>({turn_count}턴, {escape(status_label)})</span>"
                "</summary>"
                f"<p class='summary-line'><strong>핵심 사유:</strong> {escape_with_linebreaks(_short_text(failure_message), max_line_len=70)}</p>"
                f"<p class='summary-line'><strong>멀티턴 일관성:</strong> {multi_turn_html}</p>"
                "<table class='result-table'>"
                "<thead><tr>"
                "<th>Case ID</th><th>기대결과</th><th>판정</th><th>응답시간(ms)</th><th>토큰 사용량</th><th>입력값</th><th>기대값</th><th>성공조건</th><th>실제 AI 응답</th><th>핵심 사유</th><th>상세</th>"
                "</tr></thead>"
                f"<tbody>{turn_rows}</tbody>"
                "</table>"
                "</details>"
            )
        return "".join(blocks)

    # R1.1: LLM 임원 요약 — test_runner._write_summary_report 가 미리 state 에 주입.
    #        LLM 호출 중복 방지를 위해 여기서는 read-only.
    exec_summary_html = _render_exec_summary_block(state.get("aggregate", {}).get("exec_summary"))

    # R2: 11지표 카드 대시보드. 지표별 aggregation 은 _recompute_summary_totals 가 생성.
    indicator_cards_html = _render_indicator_cards(state)

    # R4: 시스템 vs 품질 에러 breakdown
    err_breakdown = classify_failure_buckets(state)
    error_breakdown_html = (
        f"<div class='error-breakdown'>"
        f"<span class='badge system-err'>시스템 {err_breakdown['system']}</span>"
        f"<span class='badge quality-err'>품질 {err_breakdown['quality']}</span>"
        f"</div>"
    ) if (err_breakdown["system"] + err_breakdown["quality"]) > 0 else ""

    metric_average_rows = "".join(
        f"<tr><td>{escape(METRIC_DISPLAY_NAMES.get(name, name))}</td><td>{value}</td></tr>"
        for name, value in sorted(metric_averages.items())
    )
    threshold_rows = "".join(
        f"<tr><td>{escape(THRESHOLD_DISPLAY_NAMES.get(name, name))}</td><td>{value}</td></tr>"
        for name, value in sorted(state["thresholds"].items())
    )
    metric_guide_rows = "".join(
        "<tr>"
        f"<td>{escape(METRIC_DISPLAY_NAMES.get(metric_name, metric_name))}</td>"
        f"<td>{escape(str(metric_meta.get('description') or ''))}</td>"
        f"<td>{escape(str(metric_meta.get('pass_rule') or ''))}</td>"
        "</tr>"
        for metric_name, metric_meta in sorted((state.get("metric_guide") or {}).items())
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>AI 에이전트 평가 요약</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #0f172a; background: #f1f5f9; line-height: 1.45; }}
    h1, h2, h3 {{ margin: 0 0 10px; }}
    p {{ margin: 6px 0; }}
    .meta, .cards, .section, .conversation {{ margin-bottom: 16px; }}
    .meta {{ background: #ffffff; border: 1px solid #dbe2ea; border-radius: 12px; padding: 14px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .card {{ background: #ffffff; border: 1px solid #dbe2ea; border-radius: 12px; padding: 12px; }}
    .card .label {{ font-size: 12px; color: #475569; }}
    .card .value {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
    .section {{ border: 1px solid #dbe2ea; border-radius: 12px; background: #ffffff; padding: 14px; }}
    .section-header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
    .help {{ margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; background: #ffffff; margin-top: 8px; border: 2px solid #334155; }}
    th, td {{ border: 1px solid #64748b; padding: 8px; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ background: #eef2ff; }}
    .result-table {{ border: 2px solid #1f2937; }}
    .result-table th {{ border-bottom: 2px solid #1f2937; }}
    pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 12px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    .conversation {{ border: 1px solid #dbe2ea; border-radius: 12px; background: #ffffff; padding: 10px 12px; }}
    .conversation > summary {{ cursor: pointer; font-size: 15px; display: flex; align-items: center; gap: 8px; }}
    .summary-line {{ margin: 8px 0; }}
    .meta-inline {{ color: #64748b; font-size: 12px; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; }}
    .ok {{ background: #dcfce7; color: #166534; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .warn {{ background: #fef3c7; color: #92400e; }}
    .skip {{ background: #e2e8f0; color: #334155; }}
    .empty {{ color: #64748b; margin: 8px 0 0; }}
    .note {{ background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412; border-radius: 8px; padding: 8px; margin-top: 10px; }}
    .exec-summary {{ background: #ecfeff; border: 1px solid #67e8f9; border-radius: 12px; padding: 14px 16px; margin: 8px 0 16px; }}
    .exec-summary h2 {{ font-size: 15px; color: #0e7490; display: flex; align-items: center; gap: 6px; }}
    .exec-summary .body {{ font-size: 15px; line-height: 1.6; color: #0f172a; margin-top: 6px; white-space: pre-line; }}
    .provenance {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 11px; font-weight: 700; margin-left: 6px; vertical-align: middle; }}
    .provenance.llm {{ background: #cffafe; color: #0e7490; }}
    .provenance.cached {{ background: #e0e7ff; color: #3730a3; }}
    .provenance.fallback {{ background: #fef3c7; color: #92400e; }}
    .indicator-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 8px; }}
    .ind-card {{ background: #ffffff; border: 1px solid #dbe2ea; border-radius: 12px; padding: 12px; }}
    .ind-head {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 14px; }}
    .ind-symbol {{ font-size: 18px; color: #475569; }}
    .ind-head .badge {{ margin-left: auto; }}
    .ind-body {{ margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }}
    .ind-metric {{ font-size: 13px; color: #334155; }}
    .ind-metric strong {{ color: #0f172a; }}
    .ind-metric.fail-ids {{ color: #991b1b; }}
    .ind-narrative {{ margin-top: 8px; padding-top: 8px; border-top: 1px dashed #cbd5e1; font-size: 12px; color: #334155; line-height: 1.5; }}
    .badge.system-err {{ background: #e0e7ff; color: #3730a3; }}
    .badge.quality-err {{ background: #fee2e2; color: #991b1b; }}
    .error-breakdown {{ display: inline-flex; gap: 6px; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>AI 에이전트 평가 요약</h1>
  {exec_summary_html}
  <div class="meta">
    <p>실행 ID: <strong>{escape(str(state['run_id']))}</strong></p>
    <p>평가 대상: {escape(str(state.get('target_url') or ''))} ({escape(str(state.get('target_type') or ''))})</p>
    <p>심판 모델: {escape(str(state.get('judge_model') or ''))}</p>
    <p>Langfuse 사용: {'사용' if state.get('langfuse_enabled') else '미사용'}</p>
    <div class="note">
      이 화면의 Turn 수는 <strong>실제로 실행된 턴</strong> 기준입니다. 대화 중간 실패 시 남은 턴은 실행되지 않을 수 있습니다.
    </div>
  </div>
  <div class="cards">
    <div class="card"><div class="label">전체 대화 수</div><div class="value">{totals.get('conversations', 0)}</div></div>
    <div class="card"><div class="label">통과 대화 수</div><div class="value">{totals.get('passed_conversations', 0)}</div></div>
    <div class="card"><div class="label">실패 대화 수</div><div class="value">{totals.get('failed_conversations', 0)}</div></div>
    <div class="card"><div class="label">대화 통과율</div><div class="value">{totals.get('conversation_pass_rate', 0)}%</div></div>
    <div class="card"><div class="label">실행된 턴 수</div><div class="value">{totals.get('turns', 0)}</div></div>
    <div class="card"><div class="label">턴 통과율</div><div class="value">{totals.get('turn_pass_rate', 0)}%</div></div>
  </div>
  {error_breakdown_html}
  {indicator_cards_html}
  <section class="section">
    <div class="section-header">
      <h2>멀티턴 대화 결과</h2>
      <span class="meta-inline">총 {multi_stats['total']}개, 통과 {multi_stats['passed']}개, 실패 {multi_stats['failed']}개, 통과율 {multi_stats['pass_rate']}%</span>
    </div>
    {_render_conversation_blocks(multi_turn_conversations)}
  </section>
  <section class="section">
    <div class="section-header">
      <h2>단일턴 대화 결과</h2>
      <span class="meta-inline">총 {single_stats['total']}개, 통과 {single_stats['passed']}개, 실패 {single_stats['failed']}개, 통과율 {single_stats['pass_rate']}%</span>
    </div>
    {_render_conversation_blocks(single_turn_conversations)}
  </section>
  <section class="section">
    <h2>평가 기준 및 평균 점수</h2>
    <details class="help">
      <summary>합격 기준(Threshold) 보기</summary>
      <table><thead><tr><th>지표</th><th>기준값</th></tr></thead><tbody>{threshold_rows}</tbody></table>
    </details>
    <details class="help">
      <summary>지표 설명 보기</summary>
      <table>
        <thead><tr><th>지표</th><th>설명</th><th>판정 기준</th></tr></thead>
        <tbody>{metric_guide_rows}</tbody>
      </table>
    </details>
    <details class="help">
      <summary>지표 평균 점수 보기</summary>
      <table><thead><tr><th>지표</th><th>평균 점수</th></tr></thead><tbody>{metric_average_rows}</tbody></table>
    </details>
  </section>
</body>
</html>"""
