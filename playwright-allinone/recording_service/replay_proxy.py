"""TR.7 — Play (R-Plus). 두 가지 모드:

1. **Codegen Output Replay** — codegen 원본 ``original.py`` 를 호스트에서 그대로
   실행. 녹화한 동작이 화면에 그대로 재현 (headed).

2. **Play with LLM** — 변환된 14-DSL 시나리오 (``scenario.json``) 를 호스트
   zero_touch_qa executor 로 실행. healing/verify/mock 등 14-DSL 의 풀 기능
   동작 + 화면에 재생 (headed).

두 모드 모두 호스트 venv python 으로 subprocess 실행 — 컨테이너 docker exec
경로는 화면 표시 불가라 사용 안 함. ``<venv_py>`` 는 ``RECORDING_VENV_PY`` env
또는 ``sys.executable`` (= recording-service daemon 의 venv python).
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
    """play 단계의 명시적 에러 (codegen / LLM 공통)."""


@dataclass
class PlayResult:
    """호스트 subprocess 실행 결과 — codegen output / LLM 공통.

    codegen 원본은 평범한 Playwright 스크립트이므로 'PASS step 수' 같은 개념이
    없다 — returncode 0 = 끝까지 정상 실행. 14-DSL executor 도 본 응답에는
    pass/fail 카운트를 노출하지 않는다 (HTML 리포트가 artifacts 안에 따로
    생성되며 향후 UI 에서 노출 가능).
    """
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float


def _resolve_venv_py(venv_py: str | None) -> str:
    return venv_py or os.environ.get("RECORDING_VENV_PY") or sys.executable


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    env: dict | None,
    timeout_sec: int,
    started: float,
) -> PlayResult:
    """공용 subprocess 실행 + PlayResult 변환."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = (time.time() - started) * 1000
        raise ReplayProxyError(
            f"play 가 {timeout_sec}s 안에 끝나지 않았습니다 (elapsed={elapsed:.0f}ms)."
        ) from e
    except FileNotFoundError as e:
        raise ReplayProxyError(f"python 호출 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    return PlayResult(
        returncode=proc.returncode,
        stdout=stdout, stderr=stderr,
        elapsed_ms=elapsed_ms,
    )


def run_codegen_replay(
    *,
    host_session_dir: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    venv_py: str | None = None,
) -> PlayResult:
    """codegen 원본 ``original.py`` 를 호스트에서 그대로 실행 (headed).

    Args:
        host_session_dir: 호스트 측 세션 디렉토리 — ``original.py`` 위치.
        timeout_sec: subprocess timeout (기본 300s).
        venv_py: 인터프리터 경로 override.
    """
    script = Path(host_session_dir) / "original.py"
    if not script.is_file():
        raise ReplayProxyError(f"original.py 없음: {script}")

    py = _resolve_venv_py(venv_py)
    cmd = [py, str(script)]
    log.info("[play-codegen] %s", " ".join(cmd))
    return _run_subprocess(
        cmd, cwd=host_session_dir, env=None,
        timeout_sec=timeout_sec, started=time.time(),
    )


def run_llm_play(
    *,
    host_session_dir: str,
    project_root: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    venv_py: str | None = None,
) -> PlayResult:
    """변환된 14-DSL ``scenario.json`` 을 zero_touch_qa executor 로 실행 (headed).

    Args:
        host_session_dir: 호스트 측 세션 디렉토리 — ``scenario.json`` 위치 +
            artifacts 산출물도 같은 폴더로 떨어진다 (ARTIFACTS_DIR).
        project_root: zero_touch_qa 패키지가 있는 프로젝트 루트 — PYTHONPATH 주입.
        timeout_sec: subprocess timeout (기본 300s).
        venv_py: 인터프리터 경로 override.
    """
    scenario = Path(host_session_dir) / "scenario.json"
    if not scenario.is_file():
        raise ReplayProxyError(f"scenario.json 없음: {scenario}")

    py = _resolve_venv_py(venv_py)
    cmd = [
        py, "-m", "zero_touch_qa",
        "--mode", "execute",
        "--scenario", str(scenario),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = project_root + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    env["ARTIFACTS_DIR"] = host_session_dir
    log.info("[play-llm] %s (cwd=%s)", " ".join(cmd), host_session_dir)
    return _run_subprocess(
        cmd, cwd=host_session_dir, env=env,
        timeout_sec=timeout_sec, started=time.time(),
    )
