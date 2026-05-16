"""D 그룹 build-time selftest — 회귀 .py emit → 별도 프로세스 실행 → exit 0.

가드 대상:
  - 0da0036: Replay UI 회귀 재생 깨지던 다발 (구조적 회귀 차단 신호).
  - 73be3d3 / b1dc29e: 회귀 .py 재 import / 치유 응답 형식.

실행 환경: 빌드 PC. Playwright 가 설치돼 있고 Chromium 이 동작 가능해야 함
(headless OK). 부적합 시 silent skip.

호출자: ``./build.sh`` 끝 부분의 selftest dispatcher.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    try:
        from zero_touch_qa.executor import StepResult
        from zero_touch_qa.regression_generator import generate_regression_test
    except ImportError as e:
        print(f"[selftest_replay_regression] FAIL — import: {e}", file=sys.stderr)
        return 1

    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
    ]
    results = [
        StepResult(step_id=1, action="navigate", target="", value="about:blank",
                   description="", status="PASS", heal_stage="none"),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        out = generate_regression_test(scenario, results, tmp)
        if not out:
            print("[selftest_replay_regression] FAIL — emit None", file=sys.stderr)
            return 2

        # REGRESSION_HEADLESS=1 강제 — Mac CI / build PC 에서 headed 창 뜨면
        # 사용자 작업 방해.
        env = {**__import__("os").environ, "REGRESSION_HEADLESS": "1"}
        proc = subprocess.run(
            [sys.executable, str(out)],
            env=env, capture_output=True, timeout=60,
            text=True,
        )
        if proc.returncode != 0:
            print(
                f"[selftest_replay_regression] FAIL — exit {proc.returncode}\n"
                f"  stdout: {proc.stdout[-500:]}\n"
                f"  stderr: {proc.stderr[-500:]}",
                file=sys.stderr,
            )
            return 3

    print("[selftest_replay_regression] OK — emit + subprocess run exit 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
