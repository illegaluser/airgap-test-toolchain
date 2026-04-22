"""
test_golden.py — eval_runner 회귀 검출 안전망 (Phase 0.2)

## 목적
후속 Phase 1~5 변경이 기존 **결정론적** 동작을 깨뜨리지 않는지 검증. 전체 파이프라인
byte-match 가 아니라, 외부 의존(DeepEval Judge · Ollama · 네트워크 · Promptfoo CLI)
없이도 고정적으로 돌아가는 **순수 유틸리티 함수** 들을 fixture 기반으로 검증한다.

## 검증 대상
- `load_dataset()` — tiny_dataset.csv 를 conversation 단위로 그룹화
- `_parse_success_criteria_mode()` — DSL/GEval/none 분기
- `_schema_validate()` — configs/schema.json 기반 JSON 검증
- `_evaluate_simple_contains_criteria()` — 한국어/영문 '포함' 템플릿 매칭
- `_is_blank_value()` / `_turn_sort_key()` — 데이터셋 정규화 헬퍼

## 실행
필수 의존성: pandas, jsonschema (eval_runner 일반 실행 deps 의 부분 집합).
DeepEval / Ollama / Langfuse 없이도 `test_runner.py` import 만 성공하면 pass.

```bash
# eval_runner/ 를 cwd 로 실행 (tests 패키지 + adapters 패키지 동시 해상을 위해)
cd code-AI-quality-allinone/eval_runner
LANGFUSE_PUBLIC_KEY="" pytest tests/test_golden.py -v
```

## 원칙
- fixture 수정 = intentional 회귀 허용 → expected_golden.json 함께 갱신해야 함.
- 외부 네트워크 호출 금지. OLLAMA_BASE_URL 은 127.0.0.1:0 로 차단.
- 새 util 함수 추가 시, 결정론이면 본 파일에 test 추가 권장.
"""

import json
import os
import sys
from pathlib import Path

import pytest

THIS_DIR = Path(__file__).resolve().parent
EVAL_RUNNER_DIR = THIS_DIR.parent
FIXTURES_DIR = THIS_DIR / "fixtures"

# test_runner.py 는 모듈 import 시 GOLDEN_CSV_PATH 환경변수를 읽는다. fixture 로 고정.
os.environ.setdefault("GOLDEN_CSV_PATH", str(FIXTURES_DIR / "tiny_dataset.csv"))
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")
os.environ.setdefault("LANGFUSE_HOST", "")
os.environ.setdefault("REPORT_DIR", str(FIXTURES_DIR / ".tmp_report"))
# Ollama 네트워크 호출이 실수로 일어나더라도 즉시 실패하도록 unreachable 주소.
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:0")

if str(EVAL_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_RUNNER_DIR))


def _install_deepeval_stubs() -> None:
    """
    test_runner 는 import 시점에 deepeval 심볼들을 직접 가져온다.
    본 골든 하네스는 외부 의존 없이 돌아가야 하므로, deepeval 미설치 환경에서도
    import 가 성공하도록 sys.modules 에 빈 stub 모듈을 주입한다.
    실제 측정/호출은 본 파일에서 하지 않으므로 stub 에 시그니처만 있으면 충분하다.
    """
    import types

    stub_module_names = [
        "deepeval",
        "deepeval.metrics",
        "deepeval.models",
        "deepeval.models.llms",
        "deepeval.models.llms.ollama_model",
        "deepeval.test_case",
    ]
    for name in stub_module_names:
        sys.modules.setdefault(name, types.ModuleType(name))

    metrics_mod = sys.modules["deepeval.metrics"]
    for cls_name in (
        "AnswerRelevancyMetric",
        "ContextualRecallMetric",
        "ContextualPrecisionMetric",
        "FaithfulnessMetric",
        "GEval",
        "ToxicityMetric",
    ):
        if not hasattr(metrics_mod, cls_name):
            setattr(metrics_mod, cls_name, type(cls_name, (), {}))

    ollama_mod = sys.modules["deepeval.models.llms.ollama_model"]
    if not hasattr(ollama_mod, "OllamaModel"):
        ollama_mod.OllamaModel = type("OllamaModel", (), {"__init__": lambda self, **kw: None})

    test_case_mod = sys.modules["deepeval.test_case"]
    if not hasattr(test_case_mod, "LLMTestCase"):
        test_case_mod.LLMTestCase = type("LLMTestCase", (), {"__init__": lambda self, **kw: None})
    if not hasattr(test_case_mod, "LLMTestCaseParams"):
        class _Params:
            INPUT = "input"
            ACTUAL_OUTPUT = "actual_output"
            EXPECTED_OUTPUT = "expected_output"
            RETRIEVAL_CONTEXT = "retrieval_context"
            CONTEXT = "context"
        test_case_mod.LLMTestCaseParams = _Params


try:
    import deepeval  # noqa: F401
except ImportError:
    _install_deepeval_stubs()

# deepeval OllamaModel 생성은 import 시점에 네트워크 호출을 하지 않으므로 안전.
from tests import test_runner as tr  # noqa: E402


@pytest.fixture(scope="session")
def expected() -> dict:
    with open(FIXTURES_DIR / "expected_golden.json", encoding="utf-8") as f:
        return json.load(f)


def test_dataset_structure(expected):
    """tiny_dataset.csv 가 예상대로 conversation 단위로 그룹화된다."""
    conversations = tr.load_dataset()
    spec = expected["dataset"]

    assert len(conversations) == spec["conversation_count"]

    single_turn = [c for c in conversations if len(c) == 1]
    multi_turn = [c for c in conversations if len(c) > 1]

    assert len(multi_turn) == spec["multi_turn_count"]
    assert [c[0]["case_id"] for c in single_turn] == spec["single_turn_case_ids"]

    # 멀티턴 그룹의 turn_id 순서 보장
    for actual_conv, expected_conv in zip(multi_turn, spec["multi_turn_conversations"]):
        assert str(actual_conv[0]["conversation_id"]) == expected_conv["conversation_id"]
        assert [t.get("turn_id") for t in actual_conv] == expected_conv["turn_ids"]
        assert [t["case_id"] for t in actual_conv] == expected_conv["case_ids"]


def test_success_criteria_mode(expected):
    """_parse_success_criteria_mode 분기가 고정된 매핑을 따른다."""
    for case in expected["criteria_modes"]:
        got = tr._parse_success_criteria_mode(case["criteria"])
        assert got == case["expected"], (
            f"criteria={case['criteria']!r} expected={case['expected']} got={got}"
        )


def test_schema_validate(expected):
    """schema.json 이 올바른 JSON 을 통과시키고 잘못된 JSON 을 거부한다."""
    for valid_json in expected["schema_cases"]["valid"]:
        tr._schema_validate(valid_json)  # 예외 없음

    for invalid_json in expected["schema_cases"]["invalid"]:
        with pytest.raises(RuntimeError, match="Format Compliance"):
            tr._schema_validate(invalid_json)


def test_simple_contains_criteria(expected):
    """자연어 '포함' 템플릿이 결정론적으로 매칭된다."""
    for case in expected["contains_criteria"]:
        got = tr._evaluate_simple_contains_criteria(
            case["criteria"], case["actual_output"]
        )
        assert got == case["expected"], f"case={case} got={got}"


def test_is_blank_value():
    """공백/None/NaN 처리 정상."""
    for blank in (None, "", "   ", "\n", "\t"):
        assert tr._is_blank_value(blank) is True, f"expected blank: {blank!r}"
    for non_blank in ("something", 0, "a", "0"):
        assert tr._is_blank_value(non_blank) is False, f"expected non-blank: {non_blank!r}"


def test_normalize_usage(expected):
    """[Phase 1 G3] usage 딕셔너리의 camelCase/snake_case 키를 정규화."""
    for case in expected["usage_normalization"]:
        got = tr._normalize_usage(case["input"])
        assert got == case["expected"], f"case={case} got={got}"


def test_percentile(expected):
    """[Phase 1 G3] nearest-rank percentile 계산이 고정된 값을 낸다."""
    for case in expected["percentile_cases"]:
        values = sorted(case["values"])
        got = tr._percentile(values, case["percentile"])
        assert got == case["expected"], f"case={case} got={got}"


def test_render_summary_html_smoke(expected):
    """[Phase 2.0] reporting.html.render_summary_html 이 state dict 로 HTML 을 정상 생성."""
    from reporting.html import render_summary_html

    # test_runner 의 SUMMARY_STATE 와 동일 스키마의 최소 샘플
    state = {
        "run_id": "smoke-001",
        "target_url": "http://127.0.0.1:8000/invoke",
        "target_type": "http",
        "judge_model": "gemma4:e4b",
        "langfuse_enabled": False,
        "thresholds": {"answer_relevancy": 0.7, "task_completion": 0.5},
        "metric_guide": {"AnswerRelevancyMetric": {"description": "답변 관련성", "pass_rule": "≥ 0.7"}},
        "totals": {
            "conversations": 1,
            "passed_conversations": 1,
            "failed_conversations": 0,
            "turns": 1,
            "passed_turns": 1,
            "failed_turns": 0,
            "conversation_pass_rate": 100.0,
            "turn_pass_rate": 100.0,
            "latency_ms": {"count": 1, "min": 500, "max": 500, "p50": 500, "p95": 500, "p99": 500},
            "tokens": {"turns_with_usage": 1, "prompt": 10, "completion": 5, "total": 15},
        },
        "metric_averages": {"AnswerRelevancyMetric": 0.9},
        "conversations": [
            {
                "conversation_id": None,
                "conversation_key": "sample-case",
                "status": "passed",
                "failure_message": "",
                "multi_turn_consistency": None,
                "turns": [
                    {
                        "case_id": "sample-case",
                        "turn_id": None,
                        "input": "test input",
                        "expected_output": "expected",
                        "success_criteria": "",
                        "expected_outcome": "pass",
                        "status": "passed",
                        "latency_ms": 500,
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                        "policy_check": {"name": "PolicyCheck", "passed": True},
                        "schema_check": {"name": "SchemaValidation", "status": "passed"},
                        "task_completion": None,
                        "metrics": [],
                        "actual_output": "test output",
                        "raw_response": "test output",
                        "has_retrieval_context": False,
                        "has_context_ground_truth": False,
                        "failure_message": "",
                    }
                ],
            }
        ],
    }

    html = render_summary_html(state)
    # 필수 랜드마크 존재 확인 — 각 섹션의 구조 검증
    assert "<!DOCTYPE html>" in html
    assert "AI 에이전트 평가 요약" in html
    assert "smoke-001" in html
    assert "gemma4:e4b" in html
    assert "sample-case" in html
    assert "단일턴 대화 결과" in html
    assert "멀티턴 대화 결과" in html


def test_render_summary_html_exec_summary_block():
    """[Phase 2.1 R1.1] state['aggregate']['exec_summary'] 가 있으면 헤더에 🤖 블록이 렌더된다."""
    from reporting.html import render_summary_html

    state_base = {
        "run_id": "exec-001",
        "target_url": "http://127.0.0.1:8000/invoke",
        "target_type": "http",
        "judge_model": "gemma4:e4b",
        "langfuse_enabled": False,
        "thresholds": {},
        "metric_guide": {},
        "totals": {
            "conversations": 1, "passed_conversations": 1, "failed_conversations": 0,
            "turns": 1, "passed_turns": 1, "failed_turns": 0,
            "conversation_pass_rate": 100.0, "turn_pass_rate": 100.0,
        },
        "metric_averages": {},
        "conversations": [],
    }

    # Case 1: source=llm (배지 "🤖 LLM 생성")
    state_llm = {
        **state_base,
        "aggregate": {"exec_summary": {"text": "전체 통과. 품질 이슈 없음.", "source": "llm", "role": "exec_summary"}},
    }
    html_llm = render_summary_html(state_llm)
    assert "<div class='exec-summary'>" in html_llm
    assert "이번 빌드 한 줄 요약" in html_llm
    assert "🤖 LLM 생성" in html_llm
    assert "전체 통과. 품질 이슈 없음." in html_llm

    # Case 2: source=fallback (배지 "📋 기본 메시지")
    state_fallback = {
        **state_base,
        "aggregate": {"exec_summary": {
            "text": "총 10건 중 1건 실패 (통과율 90%). 상세 원인은 case drill-down 을 확인하세요.",
            "source": "fallback",
            "role": "exec_summary",
        }},
    }
    html_fallback = render_summary_html(state_fallback)
    assert "📋 기본 메시지" in html_fallback
    assert "총 10건 중 1건 실패" in html_fallback

    # Case 3: source=cached
    state_cached = {
        **state_base,
        "aggregate": {"exec_summary": {"text": "요약 텍스트", "source": "cached", "role": "exec_summary"}},
    }
    assert "🤖 LLM (캐시)" in render_summary_html(state_cached)

    # Case 4: exec_summary 없음 → 섹션 자체 생략 (렌더 에러 없이)
    html_empty = render_summary_html(state_base)
    assert "<div class='exec-summary'>" not in html_empty
    assert "이번 빌드 한 줄 요약" not in html_empty

    # Case 5: exec_summary.text 가 빈 문자열 → 섹션 생략
    state_empty_text = {
        **state_base,
        "aggregate": {"exec_summary": {"text": "", "source": "fallback"}},
    }
    html_empty2 = render_summary_html(state_empty_text)
    assert "이번 빌드 한 줄 요약" not in html_empty2


def test_summary_totals_aggregates_latency_and_tokens(expected):
    """[Phase 1 G3] _recompute_summary_totals 가 latency 분포 + 토큰 합계를 기록."""
    # SUMMARY_STATE 를 격리된 샘플로 교체 후 재집계
    expected_case = expected["summary_totals"]
    original = tr.SUMMARY_STATE
    try:
        tr.SUMMARY_STATE = {
            **(original or {}),
            "conversations": expected_case["conversations"],
            "totals": {},
            "metric_averages": {},
        }
        tr._recompute_summary_totals()
        totals = tr.SUMMARY_STATE["totals"]
    finally:
        tr.SUMMARY_STATE = original

    assert totals["latency_ms"] == expected_case["expected_latency_ms"], \
        f"latency_ms 불일치: {totals['latency_ms']}"
    assert totals["tokens"] == expected_case["expected_tokens"], \
        f"tokens 불일치: {totals['tokens']}"


def test_llm_role_enablement(monkeypatch):
    """[Phase 2.0(d)] env flag 에 따라 role 이 on/off 된다."""
    from reporting import llm

    # exec_summary 기본 on, 명시적 off 가능
    monkeypatch.delenv("SUMMARY_LLM_EXEC_SUMMARY", raising=False)
    assert llm.is_role_enabled("exec_summary") is True
    monkeypatch.setenv("SUMMARY_LLM_EXEC_SUMMARY", "off")
    assert llm.is_role_enabled("exec_summary") is False

    # indicator_narrative 기본 off, 명시적 on 가능
    monkeypatch.delenv("SUMMARY_LLM_INDICATOR_NARRATIVE", raising=False)
    assert llm.is_role_enabled("indicator_narrative") is False
    monkeypatch.setenv("SUMMARY_LLM_INDICATOR_NARRATIVE", "true")
    assert llm.is_role_enabled("indicator_narrative") is True


def test_llm_generate_fallback_when_role_disabled(monkeypatch):
    """[Phase 2.0(f)] role 이 비활성일 때 즉시 fallback 반환."""
    from reporting import llm

    monkeypatch.setenv("SUMMARY_LLM_EXEC_SUMMARY", "off")
    result = llm.generate("exec_summary", {"key": "v1"}, "prompt")
    assert result["source"] == "fallback"
    assert result["text"] == ""
    assert "disabled" in result.get("reason", "")


def test_llm_generate_cache_key_determinism(monkeypatch):
    """[Phase 2.0(c)] 동일 cache_key → 동일 해시 → 동일 캐시 슬롯."""
    from reporting import llm

    llm.cache_clear()
    # Ollama 호출을 stub 으로 만들어 결정론 확인
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"response": f"generated-{call_count['n']}"}
        return FakeResponse()

    monkeypatch.setattr("reporting.llm.requests.post", fake_post)
    monkeypatch.setenv("SUMMARY_LLM_EXEC_SUMMARY", "on")

    # 동일 cache_key (dict 순서 다르게) → 동일 해시 → 캐시 hit
    result1 = llm.generate("exec_summary", {"a": 1, "b": 2}, "prompt")
    result2 = llm.generate("exec_summary", {"b": 2, "a": 1}, "prompt")
    assert result1["source"] == "llm"
    assert result2["source"] == "cached"
    assert result1["text"] == result2["text"]
    assert call_count["n"] == 1  # 두 번째는 캐시


def test_narrative_exec_summary_fallback():
    """[Phase 2.0 R1.1 fallback] LLM 비활성 시 결정론 템플릿."""
    import os

    os.environ["SUMMARY_LLM_EXEC_SUMMARY"] = "off"
    try:
        from reporting import narrative

        state = {
            "totals": {
                "conversations": 10, "passed_conversations": 9, "failed_conversations": 1,
                "turns": 12, "passed_turns": 11, "failed_turns": 1,
                "conversation_pass_rate": 90.0, "turn_pass_rate": 91.67,
            },
            "metric_averages": {"AnswerRelevancyMetric": 0.9},
            "conversations": [
                {"status": "passed", "turns": [{"status": "passed"}]},
                {"status": "failed", "turns": [
                    {"status": "failed", "case_id": "c1", "failure_message": "x"},
                ]},
            ],
        }
        result = narrative.generate_exec_summary(state)
        assert result["source"] == "fallback"
        assert result["role"] == "exec_summary"
        # 사실 기반 템플릿에 숫자 포함
        assert "10건" in result["text"]
        assert "1건 실패" in result["text"]
        assert "c1" in result["text"]
    finally:
        del os.environ["SUMMARY_LLM_EXEC_SUMMARY"]


def test_narrative_easy_explanation_fallback_hardcoded_rules():
    """[Phase 2.0 R3.1 fallback] LLM 비활성 시 html.py 와 동일한 키워드 매칭."""
    import os

    os.environ["SUMMARY_LLM_EASY_EXPLANATION"] = "off"
    try:
        from reporting import narrative

        # 정책 위반
        result = narrative.generate_easy_explanation({
            "status": "failed",
            "failure_message": "Promptfoo policy checks reported 1 failure(s).",
        })
        assert result["source"] == "fallback"
        assert "보안" in result["text"]

        # 형식 위반
        result = narrative.generate_easy_explanation({
            "status": "failed",
            "failure_message": "Format Compliance Failed (schema.json): ...",
        })
        assert "응답 형식" in result["text"] or "형식" in result["text"]

        # Adapter 에러
        result = narrative.generate_easy_explanation({
            "status": "failed",
            "failure_message": "Adapter Error: connection refused",
        })
        assert "통신" in result["text"]

        # 기타
        result = narrative.generate_easy_explanation({
            "status": "failed",
            "failure_message": "some unrelated reason",
        })
        assert result["source"] == "fallback"
        assert result["text"]  # non-empty

        # Passed turn → passed 메시지
        result = narrative.generate_easy_explanation({"status": "passed"})
        assert result["source"] == "fallback"
        assert "통과" in result["text"]
    finally:
        del os.environ["SUMMARY_LLM_EASY_EXPLANATION"]


def test_narrative_indicator_narrative_fallback():
    """[Phase 2.0 R2.1 fallback] 지표별 1줄 해설의 결정론 템플릿."""
    from reporting import narrative

    # Opt-in off (기본) 이면 fallback
    result = narrative.generate_indicator_narrative(
        "AnswerRelevancyMetric", pass_count=9, total_count=10,
        threshold=0.7, fail_case_ids=["deep-relevancy-offtopic"]
    )
    assert result["source"] == "fallback"
    assert "9/10" in result["text"]
    assert "deep-relevancy-offtopic" in result["text"]

    # 전부 통과
    result = narrative.generate_indicator_narrative(
        "ToxicityMetric", pass_count=11, total_count=11, threshold=0.5, fail_case_ids=[]
    )
    assert "전부 통과" in result["text"]

    # 적용 case 없음 (N/A)
    result = narrative.generate_indicator_narrative(
        "FaithfulnessMetric", pass_count=0, total_count=0, threshold=0.9, fail_case_ids=[]
    )
    assert "N/A" in result["text"]


def test_narrative_remediation_fallback_empty():
    """[Phase 2.0 R3.2 fallback] 조치 권장 비활성 시 텍스트 비움 (UI 섹션 숨김)."""
    from reporting import narrative

    # R3.2 는 기본 off → fallback text 비움
    result = narrative.generate_remediation({
        "status": "failed",
        "case_id": "x1",
        "failure_message": "whatever",
    })
    assert result["source"] == "fallback"
    assert result["text"] == ""

    # passed turn 도 동일
    result = narrative.generate_remediation({"status": "passed"})
    assert result["text"] == ""


def test_render_indicator_cards_landmarks():
    """[Phase 2.2 R2] 11지표 카드가 전부 렌더된다."""
    from reporting.html import render_summary_html, INDICATOR_ORDER

    assert len(INDICATOR_ORDER) == 11

    state = {
        "run_id": "r2-001",
        "target_url": "", "target_type": "http", "judge_model": "",
        "langfuse_enabled": False,
        "thresholds": {}, "metric_guide": {},
        "totals": {
            "conversations": 0, "passed_conversations": 0, "failed_conversations": 0,
            "turns": 0, "passed_turns": 0, "failed_turns": 0,
            "conversation_pass_rate": 0, "turn_pass_rate": 0,
        },
        "metric_averages": {},
        "conversations": [],
        "indicators": {
            "PolicyCheck": {"pass": 10, "fail": 1, "skipped": 0, "scores": [], "threshold": None,
                            "failed_case_ids": ["policy-fail-pii"]},
            "SchemaValidation": {"pass": 10, "fail": 0, "skipped": 1, "scores": [], "threshold": None,
                                 "failed_case_ids": []},
            "TaskCompletion": {"pass": 9, "fail": 1, "skipped": 0, "scores": [0.9, 1.0],
                               "threshold": 0.5, "failed_case_ids": ["task-fail-criteria"]},
            "AnswerRelevancyMetric": {"pass": 10, "fail": 1, "skipped": 0, "scores": [0.9],
                                      "threshold": 0.7, "failed_case_ids": ["deep-relevancy-offtopic"]},
            "ToxicityMetric": {"pass": 11, "fail": 0, "skipped": 0, "scores": [0.02],
                               "threshold": 0.5, "failed_case_ids": []},
            "FaithfulnessMetric": {"pass": 1, "fail": 1, "skipped": 0, "scores": [0.95, 0.3],
                                   "threshold": 0.9, "failed_case_ids": ["rag-hallucinate"]},
            "ContextualRecallMetric": {"pass": 2, "fail": 0, "skipped": 0, "scores": [0.9, 0.85],
                                       "threshold": 0.8, "failed_case_ids": []},
            "ContextualPrecisionMetric": {"pass": 0, "fail": 0, "skipped": 0, "scores": [],
                                          "threshold": 0.8, "failed_case_ids": []},  # N/A
            "MultiTurnConsistency": {"pass": 1, "fail": 0, "skipped": 0, "scores": [0.95],
                                     "threshold": 0.7, "failed_case_ids": []},
            "Latency": {"pass": 0, "fail": 0, "skipped": 0, "scores": [500, 800], "threshold": None,
                        "failed_case_ids": [], "kind": "informational",
                        "stats": {"count": 2, "min": 500, "max": 800, "p50": 500, "p95": 800, "p99": 800}},
            "TokenUsage": {"pass": 0, "fail": 0, "skipped": 0, "scores": [], "threshold": None,
                           "failed_case_ids": [], "kind": "informational",
                           "stats": {"turns_with_usage": 2, "prompt": 100, "completion": 50, "total": 150}},
        },
    }

    html = render_summary_html(state)
    # 11지표 각각의 한글 라벨이 들어있는지
    for symbol, name, label, stage in INDICATOR_ORDER:
        assert symbol in html, f"symbol {symbol} missing"
        assert label in html, f"label {label} missing"
    # 배지 종류
    assert "🟢 PASS" in html  # Toxicity (전부 pass)
    assert "🔴 FAIL" in html or "🟡 WARN" in html  # PolicyCheck 는 WARN
    assert "⚪ N/A" in html  # ContextualPrecision
    # 실패 case_id 노출
    assert "policy-fail-pii" in html
    assert "deep-relevancy-offtopic" in html
    # Latency/TokenUsage stats
    assert "P50" in html
    assert "합계" in html


def test_render_turn_narrative_and_error_type():
    """[Phase 2.3 R3.1/R3.2 + Phase 2.4 R4] turn 에 주입된 narrative + error type 이 렌더됨."""
    from reporting.html import render_summary_html

    state = {
        "run_id": "r3-001",
        "target_url": "", "target_type": "http", "judge_model": "",
        "langfuse_enabled": False,
        "thresholds": {}, "metric_guide": {},
        "totals": {
            "conversations": 1, "passed_conversations": 0, "failed_conversations": 1,
            "turns": 1, "passed_turns": 0, "failed_turns": 1,
            "conversation_pass_rate": 0.0, "turn_pass_rate": 0.0,
        },
        "metric_averages": {},
        "indicators": {},
        "conversations": [{
            "conversation_id": None,
            "conversation_key": "fail-case-1",
            "status": "failed",
            "failure_message": "Adapter Error: Connection refused",
            "turns": [{
                "case_id": "fail-case-1",
                "turn_id": None,
                "status": "failed",
                "input": "hi", "expected_output": "-", "success_criteria": "",
                "actual_output": "", "raw_response": "",
                "latency_ms": None, "usage": None,
                "policy_check": None, "schema_check": None,
                "task_completion": None, "metrics": [],
                "failure_message": "Adapter Error: Connection refused",
                "has_retrieval_context": False, "has_context_ground_truth": False,
                # R3.1 주입 — LLM 생성됐다고 가정 (source=llm)
                "easy_explanation": {"text": "대상 시스템 통신 실패.", "source": "llm", "role": "easy_explanation"},
                # R3.2 주입 — opt-in 활성 시 (source=cached)
                "remediation": {"text": "TARGET_URL 재확인 및 Ollama wrapper 상태 점검.",
                                "source": "cached", "role": "remediation"},
            }],
            "multi_turn_consistency": None,
        }],
    }

    html = render_summary_html(state)
    # R3.1 narrative + provenance 배지
    assert "🤖 LLM" in html
    assert "대상 시스템 통신 실패" in html
    # R3.2 remediation
    assert "조치 권장" in html
    assert "TARGET_URL 재확인" in html
    # R4 error classification: Adapter Error → system
    assert "시스템 에러" in html
    assert "시스템 1" in html  # breakdown


def test_classify_error_type():
    """[Phase 2.4 R4] failure_message 기반 system/quality 분류."""
    from reporting.html import _classify_error_type, classify_failure_buckets

    # Adapter / Connection / 5xx / Timeout → system
    assert _classify_error_type({"status": "failed", "failure_message": "Adapter Error: x"}) == "system"
    assert _classify_error_type({"status": "failed", "failure_message": "Connection Error"}) == "system"
    assert _classify_error_type({"status": "failed", "failure_message": "HTTP 503 Bad"}) == "system"
    assert _classify_error_type({"status": "failed", "failure_message": "Timeout after 20s"}) == "system"
    # policy/metric 실패 → quality
    assert _classify_error_type({"status": "failed", "failure_message": "Promptfoo policy checks reported 1"}) == "quality"
    assert _classify_error_type({"status": "failed", "failure_message": "Metrics failed: AnswerRelevancy"}) == "quality"
    # passed → 빈 문자열
    assert _classify_error_type({"status": "passed"}) == ""
    # 명시적 error_type 필드 (Phase 3 Q1 이후)
    assert _classify_error_type({"status": "failed", "error_type": "system", "failure_message": "x"}) == "system"
    assert _classify_error_type({"status": "failed", "error_type": "quality", "failure_message": "x"}) == "quality"

    # classify_failure_buckets
    state = {"conversations": [{"turns": [
        {"status": "failed", "failure_message": "HTTP 500 x"},
        {"status": "failed", "failure_message": "Metrics failed"},
        {"status": "passed"},
    ]}]}
    result = classify_failure_buckets(state)
    assert result == {"system": 1, "quality": 1}


def test_phase3_judge_meta_collection():
    """[Phase 3.1 Q2] _collect_judge_meta 가 JUDGE_MODEL + base_url + temperature 를 기록."""
    meta = tr._collect_judge_meta()
    assert "model" in meta
    assert "base_url" in meta
    assert meta["temperature"] == 0  # 결정성 고정
    # digest 는 best-effort — Ollama unreachable 환경에서는 None 허용
    assert "digest" in meta


def test_phase3_dataset_meta_collection():
    """[Phase 3.2 Q3] _collect_dataset_meta 가 sha256/rows/mtime 을 기록."""
    meta = tr._collect_dataset_meta()
    assert "path" in meta
    # tiny_dataset.csv 가 실제 존재하므로 sha/rows/mtime 이 채워져야 함
    assert meta["sha256"] is not None, "sha256 should be computed"
    assert len(meta["sha256"]) == 64, "sha256 hex length"
    assert meta["rows"] and meta["rows"] >= 10, f"rows={meta['rows']} (expected ≥10)"
    assert meta["mtime"] is not None


def test_phase3_error_type_field_on_universal_eval_output():
    """[Phase 3.3 Q1] UniversalEvalOutput 에 error_type 필드 + to_dict 포함."""
    from adapters.base import UniversalEvalOutput, ErrorType  # noqa: F401

    out = UniversalEvalOutput(input="x", actual_output="y")
    assert out.error_type is None  # 기본값

    out2 = UniversalEvalOutput(input="x", actual_output="", error="HTTP 500", error_type="system")
    d = out2.to_dict()
    assert d["error_type"] == "system"


def test_phase3_http_adapter_tags_system_error():
    """[Phase 3.3 Q1] http_adapter 가 ConnError/5xx 에 error_type='system' 설정."""
    import types as _types
    import requests as _requests
    from adapters.http_adapter import GenericHttpAdapter

    adapter = GenericHttpAdapter("http://127.0.0.1:0/invoke")

    # Case 1: Connection Error (requests.exceptions.RequestException)
    class _FakeConnError(_requests.exceptions.ConnectionError):
        pass

    def _raise(*a, **kw):
        raise _FakeConnError("refused")

    original_post = _requests.post
    try:
        _requests.post = _raise  # monkeypatch
        result = adapter.invoke("ping")
        assert result.error is not None
        assert result.error_type == "system"
    finally:
        _requests.post = original_post

    # Case 2: 5xx response → error_type="system"
    class _FakeResponse:
        status_code = 503
        text = "unavailable"
        def raise_for_status(self):
            pass
        def json(self):
            return {"error": "upstream down"}

    def _fake_post_5xx(*a, **kw):
        return _FakeResponse()

    try:
        _requests.post = _fake_post_5xx
        result = adapter.invoke("ping")
        assert result.http_status == 503
        assert result.error_type == "system"
    finally:
        _requests.post = original_post


def test_phase3_classify_error_type_uses_explicit_field():
    """[Phase 3.3 Q1] _classify_error_type 이 turn['error_type'] 구조화 필드 우선 사용."""
    from reporting.html import _classify_error_type

    # Phase 2.4 와 동일: explicit 필드 > 휴리스틱
    assert _classify_error_type({"status": "failed", "error_type": "system", "failure_message": "metric failed"}) == "system"
    assert _classify_error_type({"status": "failed", "error_type": "quality", "failure_message": "Adapter Error"}) == "quality"
    # 명시 없음 → 휴리스틱 fallback
    assert _classify_error_type({"status": "failed", "failure_message": "Adapter Error"}) == "system"
    assert _classify_error_type({"status": "failed", "failure_message": "Metrics failed"}) == "quality"


def test_phase3_html_header_shows_judge_and_dataset_meta():
    """[Phase 3.1 + 3.2] 렌더된 HTML 에 judge/dataset 메타가 노출된다."""
    from reporting.html import render_summary_html

    state = {
        "run_id": "p3-001",
        "target_url": "", "target_type": "http", "judge_model": "",
        "langfuse_enabled": False,
        "thresholds": {}, "metric_guide": {},
        "totals": {
            "conversations": 0, "passed_conversations": 0, "failed_conversations": 0,
            "turns": 0, "passed_turns": 0, "failed_turns": 0,
            "conversation_pass_rate": 0, "turn_pass_rate": 0,
        },
        "metric_averages": {},
        "indicators": {},
        "conversations": [],
        "aggregate": {
            "judge": {
                "model": "gemma4:e4b",
                "base_url": "http://host.docker.internal:11434",
                "temperature": 0,
                "digest": "sha256:abcd1234567890",
            },
            "dataset": {
                "path": "/var/knowledges/eval/data/golden.csv",
                "sha256": "fedcba9876543210abcdef0123456789",
                "rows": 42,
                "mtime": "2026-04-22T08:00:00+00:00",
            },
        },
    }

    html = render_summary_html(state)
    # Judge 메타
    assert "gemma4:e4b" in html
    assert "T=0" in html
    assert "digest=…abcd12345678" in html  # 12자 truncated
    # Dataset 메타
    assert "golden.csv" in html
    assert "sha256=…fedcba987654" in html
    assert "rows=42" in html
    assert "2026-04-22" in html


def test_phase4_q5_policy_check_in_process():
    """[Phase 4.1 Q5] _promptfoo_policy_check 가 subprocess 없이 security_assert 를 직접 호출."""
    import pytest as _pytest

    # Clean → 통과
    tr._promptfoo_policy_check("This is a benign message.")

    # PII 주민번호 → RuntimeError with 기존 메시지 규약 유지
    with _pytest.raises(RuntimeError, match=r"Promptfoo policy checks reported 1 failure"):
        tr._promptfoo_policy_check("주민번호 901234-1234567 입니다")

    # API 키 → RuntimeError
    with _pytest.raises(RuntimeError, match=r"Promptfoo policy checks reported"):
        tr._promptfoo_policy_check('api_key = "sk-abcdef0123456789abcd"')

    # JSON 응답 형태 — 값만 검사
    tr._promptfoo_policy_check('{"answer": "안전한 응답입니다"}')
    with _pytest.raises(RuntimeError):
        tr._promptfoo_policy_check('{"answer": "주민번호 901234-1234567"}')


def test_phase4_q5_no_subprocess_dependency():
    """[Phase 4.1 Q5] test_runner 에서 subprocess/tempfile/uuid import 제거 확인."""
    import inspect

    source = inspect.getsource(tr)
    # test_runner 본문에서 subprocess.run 을 더 이상 호출하지 않음
    assert "subprocess.run(" not in source, "subprocess.run() should be removed"
    # import 문 자체도 제거됨
    assert "\nimport subprocess\n" not in source
    assert "\nimport tempfile\n" not in source
    assert "\nimport uuid\n" not in source


def test_turn_sort_key():
    """turn_id 정렬이 실제 사용 패턴(int + None, 또는 digit-string + None)에서 안정적."""
    # 실사용 케이스 1: 정수 turn_id + None → None 이 뒤로
    assert sorted([None, 2, 1, 3, None], key=tr._turn_sort_key) == [1, 2, 3, None, None]

    # 실사용 케이스 2: 숫자 문자열은 int 로 정규화되어 순서 유지
    assert sorted(["3", "1", "2"], key=tr._turn_sort_key) == ["1", "2", "3"]

    # 단일 타입 안에서의 정렬 안정성만 보장. int/str 혼합은 정의되지 않은 동작이며
    # golden.csv 운용 규약상 금지 (한 conversation 내 turn_id 타입은 일관)


# ============================================================================
# Phase 5 — Evaluation Robustness (Q7)
# 5.1 보정 세트 집계 + 5.2 경계 case N-repeat
# ============================================================================


def test_phase5_is_truthy_flag():
    """[Phase 5.1] CSV calib 컬럼의 boolean-like 문자열 정규화."""
    # Falsy
    for v in (None, "", "   ", "false", "0", "no", "FALSE", "abc"):
        assert tr._is_truthy_flag(v) is False, f"expected False for {v!r}"
    # Truthy
    for v in ("true", "TRUE", "1", "yes", "y", "Y", "t", "on", "calib", "  true  "):
        assert tr._is_truthy_flag(v) is True, f"expected True for {v!r}"
    # native types
    assert tr._is_truthy_flag(True) is True
    assert tr._is_truthy_flag(False) is False
    assert tr._is_truthy_flag(1) is True
    assert tr._is_truthy_flag(0) is False


def test_phase5_calib_column_in_dataset():
    """[Phase 5.1] tiny_dataset.csv 에 calib 컬럼 파싱 — 2 케이스 True, 나머지 False."""
    conversations = tr.load_dataset()
    all_turns = [t for conv in conversations for t in conv]
    calib_cases = {t["case_id"] for t in all_turns if tr._is_truthy_flag(t.get("calib"))}
    # 고정 기대: policy-pass-clean + task-pass-simple 두 건만 보정 세트.
    assert calib_cases == {"policy-pass-clean", "task-pass-simple"}


def test_phase5_median_helper():
    """[Phase 5.2] _median — odd/even/empty/단일값."""
    assert tr._median([]) is None
    assert tr._median([0.7]) == 0.7
    assert tr._median([1.0, 0.5, 0.9]) == 0.9  # sorted=[0.5,0.9,1.0] mid=0.9
    assert tr._median([0.2, 0.4, 0.6, 0.8]) == 0.5  # (0.4+0.6)/2
    # None 섞여 있어도 무시
    assert tr._median([None, 0.4, 0.6]) == 0.5


def test_phase5_stdev_helper():
    """[Phase 5.1] 표본 표준편차 (분모 n-1). 1개 이하 → 0.0."""
    assert tr._stdev([]) == 0.0
    assert tr._stdev([0.9]) == 0.0
    # 2값: [0.8, 1.0] mean=0.9, var=(0.01+0.01)/1=0.02, std=sqrt(0.02)
    got = tr._stdev([0.8, 1.0])
    assert abs(got - 0.1414213) < 1e-4, got


def test_phase5_borderline_detection():
    """[Phase 5.2] _is_borderline 경계 판단."""
    # threshold=0.7, margin=0.05 → [0.65, 0.75] 이내만 borderline
    assert tr._is_borderline(0.7, 0.7, 0.05) is True
    assert tr._is_borderline(0.65, 0.7, 0.05) is True
    assert tr._is_borderline(0.75, 0.7, 0.05) is True
    assert tr._is_borderline(0.64, 0.7, 0.05) is False
    assert tr._is_borderline(0.76, 0.7, 0.05) is False
    assert tr._is_borderline(None, 0.7, 0.05) is False
    assert tr._is_borderline(0.7, None, 0.05) is False


def test_phase5_borderline_config_defaults_and_env(monkeypatch):
    """[Phase 5.2] REPEAT_BORDERLINE_N / BORDERLINE_MARGIN 환경변수 해석."""
    monkeypatch.delenv("REPEAT_BORDERLINE_N", raising=False)
    monkeypatch.delenv("BORDERLINE_MARGIN", raising=False)
    n, m = tr._borderline_config()
    assert n == 1 and m == 0.05  # 기본 = off
    monkeypatch.setenv("REPEAT_BORDERLINE_N", "3")
    monkeypatch.setenv("BORDERLINE_MARGIN", "0.08")
    n, m = tr._borderline_config()
    assert n == 3 and m == 0.08
    # 이상값 → safe fallback
    monkeypatch.setenv("REPEAT_BORDERLINE_N", "abc")
    monkeypatch.setenv("BORDERLINE_MARGIN", "xyz")
    n, m = tr._borderline_config()
    assert n == 1 and m == 0.05


def test_phase5_rescore_borderline_noop_when_n_one():
    """[Phase 5.2] N=1 (기본) 일 땐 remeasure 호출 없음, 원점수 그대로."""
    calls = {"count": 0}

    def _remeasure():
        calls["count"] += 1
        return 0.99

    score, samples = tr._rescore_borderline(0.70, _remeasure, 0.70, n=1, margin=0.05)
    assert score == 0.70
    assert samples == [0.70]
    assert calls["count"] == 0


def test_phase5_rescore_borderline_noop_when_outside_margin():
    """[Phase 5.2] 점수가 경계 밖이면 remeasure 호출 없음."""
    calls = {"count": 0}

    def _remeasure():
        calls["count"] += 1
        return 0.99

    score, samples = tr._rescore_borderline(0.30, _remeasure, 0.70, n=5, margin=0.05)
    assert score == 0.30
    assert samples == [0.30]
    assert calls["count"] == 0


def test_phase5_rescore_borderline_uses_median_when_inside():
    """[Phase 5.2] 경계 점수 + N=3 이면 remeasure 2회 호출, 3점 median 반환."""
    next_scores = iter([0.80, 0.60])  # remeasure 는 2회 호출됨
    calls = {"count": 0}

    def _remeasure():
        calls["count"] += 1
        return next(next_scores)

    score, samples = tr._rescore_borderline(0.72, _remeasure, 0.70, n=3, margin=0.05)
    assert calls["count"] == 2
    # 수집 순서: [0.72, 0.80, 0.60] — sorted=[0.60, 0.72, 0.80], median=0.72
    assert score == 0.72
    assert sorted(samples) == [0.60, 0.72, 0.80]


def test_phase5_calibration_block_empty():
    """[Phase 5.1] 보정 세트가 0 case 이면 enabled=False + 정상 구조 반환."""
    tr.SUMMARY_STATE["_calib_raw"] = {"turn_count": 0, "case_ids": [], "per_metric_scores": {}}
    block = tr._build_calibration_block()
    assert block["enabled"] is False
    assert block["turn_count"] == 0
    assert block["per_metric"] == {}
    assert block["overall"]["score_count"] == 0


def test_phase5_calibration_block_with_scores():
    """[Phase 5.1] per_metric_scores 로부터 mean/std/min/max 산출."""
    tr.SUMMARY_STATE["_calib_raw"] = {
        "turn_count": 2,
        "case_ids": ["policy-pass-clean", "task-pass-simple"],
        "per_metric_scores": {
            "TaskCompletion": [1.0, 0.95, 0.90],
            "AnswerRelevancyMetric": [0.88, 0.92],
        },
    }
    block = tr._build_calibration_block()
    assert block["enabled"] is True
    assert block["turn_count"] == 2
    assert block["case_ids"] == ["policy-pass-clean", "task-pass-simple"]
    tc = block["per_metric"]["TaskCompletion"]
    assert tc["count"] == 3
    assert abs(tc["mean"] - 0.95) < 1e-6
    assert tc["min"] == 0.90 and tc["max"] == 1.0
    assert tc["std"] > 0
    overall = block["overall"]
    assert overall["score_count"] == 5
    assert overall["mean"] is not None
    assert overall["std"] >= 0


def test_phase5_html_header_shows_calibration_and_judge_calls():
    """[Phase 5.1] HTML 헤더에 calibration 편차 + judge_calls_total 노출."""
    from reporting.html import render_summary_html

    state = {
        "run_id": "p5-001",
        "target_url": "", "target_type": "http", "judge_model": "",
        "langfuse_enabled": False,
        "thresholds": {}, "metric_guide": {},
        "totals": {
            "conversations": 0, "passed_conversations": 0, "failed_conversations": 0,
            "turns": 0, "passed_turns": 0, "failed_turns": 0,
            "conversation_pass_rate": 0, "turn_pass_rate": 0,
        },
        "metric_averages": {},
        "indicators": {},
        "conversations": [],
        "aggregate": {
            "judge": {"model": "gemma4:e4b", "base_url": "http://127.0.0.1:11434", "temperature": 0},
            "dataset": {"path": "/tmp/golden.csv", "sha256": "abc", "rows": 11, "mtime": "2026-04-22T08:00:00+00:00"},
            "calibration": {
                "enabled": True,
                "turn_count": 2,
                "case_ids": ["policy-pass-clean", "task-pass-simple"],
                "per_metric": {"TaskCompletion": {"count": 2, "mean": 0.95, "std": 0.05, "min": 0.9, "max": 1.0}},
                "overall": {"score_count": 2, "mean": 0.95, "std": 0.05},
            },
            "judge_calls_total": 42,
            "borderline_policy": {"repeat_n": 3, "margin": 0.05},
        },
    }

    html = render_summary_html(state)
    assert "Judge 변동성:" in html
    assert "보정 σ=" in html
    assert "Judge calls=42" in html
    assert "경계 재실행 N=3" in html


# ============================================================================
# Phase 6 — Target Flexibility
# ============================================================================


def test_phase6_adapter_registry_target_type_routing():
    """[Phase 6.1] AdapterRegistry 가 TARGET_TYPE 문자열에 맞는 어댑터 클래스를 선택."""
    from adapters.registry import AdapterRegistry
    from adapters.http_adapter import GenericHttpAdapter

    http = AdapterRegistry.get_instance("http", "http://127.0.0.1:5001/x")
    assert isinstance(http, GenericHttpAdapter)

    # ui_chat 은 Playwright 미설치 환경에서도 인스턴스 생성까지는 가능해야 함
    # (실 .invoke 만 Playwright 의존). 여기서는 클래스 명 확인.
    try:
        ui = AdapterRegistry.get_instance("ui_chat", "http://127.0.0.1:28081/chat")
        assert type(ui).__name__ == "BrowserUIAdapter"
    except ImportError:
        pytest.skip("Playwright not installed in this env")

    # 알 수 없는 이름 → 기본 http
    fallback = AdapterRegistry.get_instance("unknown_kind", "http://127.0.0.1:x")
    assert isinstance(fallback, GenericHttpAdapter)


def test_phase6_http_adapter_openai_compat_payload(monkeypatch):
    """[Phase 6.2] TARGET_REQUEST_SCHEMA=openai_compat 일 때 payload 가 OpenAI 호환 구조."""
    import types as _types
    from adapters.http_adapter import GenericHttpAdapter

    monkeypatch.setenv("TARGET_REQUEST_SCHEMA", "openai_compat")
    monkeypatch.setenv("JUDGE_MODEL", "gemma4:e4b")
    adapter = GenericHttpAdapter("http://127.0.0.1:5001/v1/chat/completions")

    captured = {}

    class _FakeResp:
        status_code = 200
        text = '{"choices":[{"message":{"content":"hello"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
        def raise_for_status(self): pass
        def json(self):
            import json as _j
            return _j.loads(self.text)

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url; captured["json"] = json; captured["headers"] = headers
        return _FakeResp()

    import requests as _requests
    monkeypatch.setattr(_requests, "post", _fake_post)

    out = adapter.invoke("hi")
    payload = captured["json"]
    assert set(payload.keys()) == {"model", "messages"}
    assert payload["model"] == "gemma4:e4b"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    # 응답은 choices[0].message.content 에서 추출
    assert out.actual_output == "hello"
    assert out.usage.get("promptTokens") == 3


def test_phase6_http_adapter_standard_payload_unchanged(monkeypatch):
    """[Phase 6.2] TARGET_REQUEST_SCHEMA=standard 기본에서는 기존 payload 형태 유지 (회귀)."""
    from adapters.http_adapter import GenericHttpAdapter

    monkeypatch.delenv("TARGET_REQUEST_SCHEMA", raising=False)
    adapter = GenericHttpAdapter("http://127.0.0.1:5001/x")

    captured = {}

    class _FakeResp:
        status_code = 200
        text = '{"answer":"ok"}'
        def raise_for_status(self): pass
        def json(self):
            import json as _j
            return _j.loads(self.text)

    import requests as _requests

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(_requests, "post", _fake_post)
    out = adapter.invoke("hi")
    keys = set(captured["json"].keys())
    assert {"messages", "query", "input", "user"}.issubset(keys)
    assert out.actual_output == "ok"


def test_phase6_http_adapter_auth_header_injection(monkeypatch):
    """[Phase 6.1] TARGET_AUTH_HEADER 가 "Header: value" 형식으로 주입된다."""
    from adapters.http_adapter import GenericHttpAdapter

    adapter = GenericHttpAdapter("http://127.0.0.1:5001/x", auth_header="X-API-Key: abc123")
    headers = adapter._build_headers()
    assert headers.get("X-API-Key") == "abc123"
    assert headers.get("Content-Type") == "application/json"

    # ":" 없으면 Bearer 로 Authorization 에 삽입
    adapter2 = GenericHttpAdapter("http://127.0.0.1:5001/x", auth_header="sk-xxx")
    h2 = adapter2._build_headers()
    assert h2.get("Authorization") == "sk-xxx"


def test_phase5_html_header_calibration_disabled():
    """[Phase 5.1] 보정 세트 미설정 시에도 헤더 라인 정상 (placeholder)."""
    from reporting.html import render_summary_html

    state = {
        "run_id": "p5-002",
        "target_url": "", "target_type": "http", "judge_model": "",
        "langfuse_enabled": False,
        "thresholds": {}, "metric_guide": {},
        "totals": {
            "conversations": 0, "passed_conversations": 0, "failed_conversations": 0,
            "turns": 0, "passed_turns": 0, "failed_turns": 0,
            "conversation_pass_rate": 0, "turn_pass_rate": 0,
        },
        "metric_averages": {},
        "indicators": {},
        "conversations": [],
        "aggregate": {
            "calibration": {"enabled": False, "turn_count": 0, "case_ids": [], "per_metric": {}, "overall": {}},
            "judge_calls_total": 0,
            "borderline_policy": {"repeat_n": 1, "margin": 0.05},
        },
    }

    html = render_summary_html(state)
    assert "보정 세트: 미설정" in html
    assert "Judge calls=0" in html
    # N=1 (기본 off) 은 재실행 배지 미출력
    assert "경계 재실행" not in html
