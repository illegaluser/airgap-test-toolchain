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
        "judge_model": "qwen3-coder:30b",
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
    assert "qwen3-coder:30b" in html
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
        "judge_model": "qwen3-coder:30b",
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


def test_turn_sort_key():
    """turn_id 정렬이 실제 사용 패턴(int + None, 또는 digit-string + None)에서 안정적."""
    # 실사용 케이스 1: 정수 turn_id + None → None 이 뒤로
    assert sorted([None, 2, 1, 3, None], key=tr._turn_sort_key) == [1, 2, 3, None, None]

    # 실사용 케이스 2: 숫자 문자열은 int 로 정규화되어 순서 유지
    assert sorted(["3", "1", "2"], key=tr._turn_sort_key) == ["1", "2", "3"]

    # 단일 타입 안에서의 정렬 안정성만 보장. int/str 혼합은 정의되지 않은 동작이며
    # golden.csv 운용 규약상 금지 (한 conversation 내 turn_id 타입은 일관)
