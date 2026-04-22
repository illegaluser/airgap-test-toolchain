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


def test_turn_sort_key():
    """turn_id 정렬이 실제 사용 패턴(int + None, 또는 digit-string + None)에서 안정적."""
    # 실사용 케이스 1: 정수 turn_id + None → None 이 뒤로
    assert sorted([None, 2, 1, 3, None], key=tr._turn_sort_key) == [1, 2, 3, None, None]

    # 실사용 케이스 2: 숫자 문자열은 int 로 정규화되어 순서 유지
    assert sorted(["3", "1", "2"], key=tr._turn_sort_key) == ["1", "2", "3"]

    # 단일 타입 안에서의 정렬 안정성만 보장. int/str 혼합은 정의되지 않은 동작이며
    # golden.csv 운용 규약상 금지 (한 conversation 내 turn_id 타입은 일관)
