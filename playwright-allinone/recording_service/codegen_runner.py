"""Playwright codegen subprocess 래퍼 (Phase R-MVP).

TR.1 단계는 인터페이스 + 본 구현 stub 까지. TR.2 에서 실 subprocess 생명주기
관리·시간 한도·에러 케이스 처리 보강.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# 환경변수로 override 가능한 codegen 한도
DEFAULT_TIMEOUT_SEC = int(os.environ.get("RECORDING_CODEGEN_TIMEOUT_SEC", "1800"))


@dataclass
class CodegenHandle:
    """실행 중인 codegen subprocess 의 추적 핸들."""

    pid: int
    started_at: float
    output_path: Path
    process: subprocess.Popen


def is_codegen_available() -> bool:
    """`playwright` 실행 파일이 PATH 에 있는지."""
    return shutil.which("playwright") is not None


def start_codegen(
    target_url: str,
    output_path: Path,
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> CodegenHandle:
    """playwright codegen 시작.

    Raises:
        FileNotFoundError: playwright 실행 파일 없음
        OSError: subprocess 실행 실패
    """
    if not is_codegen_available():
        raise FileNotFoundError(
            "playwright 실행 파일을 찾을 수 없습니다. "
            "호스트 venv 의 PATH 또는 mac-agent-setup.sh 의 REQ_PKGS 를 확인하세요."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "playwright", "codegen",
        target_url,
        "--target=python",
        "--output", str(output_path),
    ]
    log.info("[codegen] 시작: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return CodegenHandle(
        pid=proc.pid,
        started_at=time.time(),
        output_path=output_path,
        process=proc,
    )


def stop_codegen(handle: CodegenHandle, *, grace_sec: float = 5.0) -> tuple[int, str, str]:
    """SIGTERM → 유예 후 SIGKILL. (returncode, stdout, stderr) 반환."""
    proc = handle.process
    if proc.poll() is None:
        log.info("[codegen] SIGTERM PID=%d", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            log.warning("[codegen] SIGKILL (유예 %.1fs 초과)", grace_sec)
            proc.kill()
            proc.wait(timeout=2.0)

    out = (proc.stdout.read() if proc.stdout else b"").decode("utf-8", errors="replace")
    err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, out, err


def is_running(handle: CodegenHandle) -> bool:
    return handle.process.poll() is None


def output_size_bytes(handle: CodegenHandle) -> int:
    """codegen 출력 파일 크기. 0 = 액션 미녹화."""
    try:
        return handle.output_path.stat().st_size
    except OSError:
        return 0
