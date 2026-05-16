"""D 그룹 build-time selftest — container 시나리오 변환 import + smoke.

가드 대상:
  - d4d957b: 컨테이너 안에서 ``converter_ast`` import 가 실패하던 회귀.
  - 35bd5e0: iframe chain 보존 (smoke 한 줄).

실행 환경: 빌드 PC (Mac native / WSL2 / Linux container). PYTHONPATH 가 ``shared``
와 ``replay-ui`` 를 포함해야 동작. 환경 부적합 시 silent skip (build 자체는
계속).

호출자: ``./build.sh`` 끝 부분의 selftest dispatcher.
"""

from __future__ import annotations

import sys


def main() -> int:
    # import probe — d4d957b 회귀: 컨테이너에서 converter_ast import 실패.
    # importlib 사용해 lint(unused-import) 회피하면서도 import 자체가 살아있는지 확인.
    try:
        import importlib
        for _mod in (
            "zero_touch_qa.converter_ast",
            "zero_touch_qa.regression_generator",
            "zero_touch_qa.executor",
        ):
            importlib.import_module(_mod)
    except ImportError as e:
        print(f"[selftest_convert] FAIL — import: {e}", file=sys.stderr)
        return 1

    # smoke: 회귀 .py emit 1건 — codegen → AST → emit 체인이 살아 있는지.
    try:
        from zero_touch_qa.regression_generator import generate_regression_test
        from zero_touch_qa.executor import StepResult
        import tempfile
        scenario = [
            {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
            {"step": 2, "action": "click", "target": "#btn", "value": ""},
        ]
        results = [
            StepResult(step_id=s["step"], action=s["action"], target="", value="",
                       description="", status="PASS", heal_stage="none")
            for s in scenario
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = generate_regression_test(scenario, results, tmp)
            if not out:
                print("[selftest_convert] FAIL — emit returned None", file=sys.stderr)
                return 2
    except Exception as e:
        print(f"[selftest_convert] FAIL — emit smoke: {e}", file=sys.stderr)
        return 3

    print("[selftest_convert] OK — converter_ast + generator import + emit smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
