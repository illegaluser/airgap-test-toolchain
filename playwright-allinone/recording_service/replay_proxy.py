"""TR.7 — Replay (R-Plus).

설계: PLAN_GROUNDING_RECORDING_AGENT.md §"TR.7" + 사용자 결정 (2026-04-29)

**Replay 의 의미 (사용자 정의)**: codegen 으로 녹화한 원본 ``original.py`` 를
호스트 브라우저(headed)에서 그대로 재실행한다. 변환된 14-DSL 시나리오를
컨테이너 안에서 headless 로 실행하던 이전 구현은 폐기.

호출:
    <venv_py> <host_session_dir>/original.py

``<venv_py>`` 는 ``RECORDING_VENV_PY`` 환경변수가 있으면 그 값, 없으면
``sys.executable`` (= recording-service daemon 자신의 venv python). mac-agent-setup
이 띄운 daemon 이라면 같은 venv 에 Playwright Chromium 이 이미 설치돼 있어
``original.py`` 의 ``sync_playwright()`` 호출이 즉시 동작한다.

codegen 산출물의 기본은 ``headless=False`` 라 별도 설정 없이도 호스트 브라우저
창이 뜬다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


DEFAULT_REPLAY_TIMEOUT_SEC = int(
    os.environ.get("RECORDING_REPLAY_TIMEOUT_SEC", "300")
)


class ReplayProxyError(RuntimeError):
    """replay 단계의 명시적 에러."""


@dataclass
class ReplayResult:
    """codegen 원본 .py 의 호스트 실행 결과.

    이전 구현이 노출하던 pass/fail/healed/run_log 필드는 의미가 없어 제거.
    원본 .py 는 14-DSL executor 가 아니라 평범한 Playwright 스크립트이므로
    'PASS step 수' 같은 개념이 없다 — returncode 0 = 끝까지 정상 실행.
    """
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float


def run_replay(
    *,
    host_session_dir: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    venv_py: str | None = None,
) -> ReplayResult:
    """호스트에서 ``<host_session_dir>/original.py`` 를 그대로 실행 (headed).

    Args:
        host_session_dir: 호스트 측 세션 디렉토리. ``original.py`` 가 이 안에 있어야 함.
        timeout_sec: subprocess.run 단일 호출 timeout (기본 300s).
        venv_py: Python 인터프리터 경로. None 이면 ``RECORDING_VENV_PY`` env →
            ``sys.executable`` 순으로 결정. recording-service daemon 의 venv 에
            playwright chromium 이 설치된 전제.
    """
    script = Path(host_session_dir) / "original.py"
    if not script.is_file():
        raise ReplayProxyError(f"original.py 없음: {script}")

    py = venv_py or os.environ.get("RECORDING_VENV_PY") or sys.executable

    cmd = [py, str(script)]
    log.info("[replay-proxy] %s", " ".join(cmd))

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=host_session_dir,  # 상대 경로 (artifacts) 가 세션 디렉토리 기준
            capture_output=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = (time.time() - started) * 1000
        raise ReplayProxyError(
            f"replay 가 {timeout_sec}s 안에 끝나지 않았습니다 (elapsed={elapsed:.0f}ms)."
        ) from e
    except FileNotFoundError as e:
        raise ReplayProxyError(f"python 호출 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    return ReplayResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_ms=elapsed_ms,
    )
