"""Playwright codegen subprocess 래퍼 (Phase R-MVP TR.2).

설계: PLAN_GROUNDING_RECORDING_AGENT.md §"TR.2"

책임 범위:
- subprocess.Popen 으로 codegen 시작 + 핸들 추적
- SIGTERM (유예) → SIGKILL 종료 흐름
- 시간 한도 검사 (`is_timed_out` / `terminate_if_timed_out`)
- 출력 파일 크기 검증 (0 = 액션 미녹화)
- 명확한 에러 메시지 (playwright 미설치 / target_url 도달 불가)

이 모듈은 호스트 측만 사용. 컨테이너 안에서는 실행되지 않는다.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# 환경변수로 override 가능한 codegen 한도. 기본 30분.
DEFAULT_TIMEOUT_SEC = int(os.environ.get("RECORDING_CODEGEN_TIMEOUT_SEC", "1800"))


@dataclass
class CodegenHandle:
    """실행 중인 codegen subprocess 의 추적 핸들."""

    pid: int
    started_at: float
    output_path: Path
    process: subprocess.Popen
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    target_url: str = ""
    # stop_codegen 후 기록되는 결과
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    extras: dict = field(default_factory=dict)

    def elapsed_sec(self) -> float:
        return time.time() - self.started_at


class CodegenError(RuntimeError):
    """codegen 시작/종료 단계의 명시적 에러."""


def is_codegen_available() -> bool:
    """`playwright` 실행 파일이 PATH 에 있는지."""
    return shutil.which("playwright") is not None


def start_codegen(
    target_url: str,
    output_path: Path,
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    extra_args: Optional[list[str]] = None,
) -> CodegenHandle:
    """playwright codegen 시작.

    Raises:
        CodegenError: playwright 실행 파일 없음 또는 subprocess 실행 실패
    """
    if not is_codegen_available():
        raise CodegenError(
            "playwright 실행 파일을 찾을 수 없습니다. "
            "호스트 venv 의 PATH 또는 mac-agent-setup.sh 의 REQ_PKGS 를 확인하세요."
        )

    if not target_url or not isinstance(target_url, str):
        raise CodegenError(f"target_url 이 유효하지 않습니다: {target_url!r}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "playwright", "codegen",
        target_url,
        "--target=python",
        "--output", str(output_path),
    ]
    if extra_args:
        cmd.extend(extra_args)

    log.info("[codegen] 시작: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        raise CodegenError(f"codegen subprocess 실행 실패: {e}") from e

    return CodegenHandle(
        pid=proc.pid,
        started_at=time.time(),
        output_path=output_path,
        process=proc,
        timeout_sec=timeout_sec,
        target_url=target_url,
    )


def stop_codegen(handle: CodegenHandle, *, grace_sec: float = 5.0) -> CodegenHandle:
    """SIGTERM → 유예 후 SIGKILL. handle 에 결과 기록 후 그대로 반환.

    이미 종료된 프로세스에는 신호를 안 보내고 즉시 returncode 만 회수.
    """
    proc = handle.process

    if proc.poll() is None:
        log.info("[codegen] SIGTERM PID=%d (elapsed=%.1fs)", proc.pid, handle.elapsed_sec())
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            # 신호 보내기 직전 알아서 종료된 경우 — 정상 흐름
            pass
        try:
            proc.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            log.warning("[codegen] SIGKILL (유예 %.1fs 초과)", grace_sec)
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                log.error("[codegen] SIGKILL 후에도 종료되지 않음 — handle 만 닫음")

    handle.stdout = _read_pipe(proc.stdout)
    handle.stderr = _read_pipe(proc.stderr)
    handle.returncode = proc.returncode if proc.returncode is not None else -1
    return handle


def is_running(handle: CodegenHandle) -> bool:
    return handle.process.poll() is None


def is_timed_out(handle: CodegenHandle) -> bool:
    return handle.elapsed_sec() > handle.timeout_sec


def terminate_if_timed_out(handle: CodegenHandle) -> bool:
    """timeout 초과 시 SIGTERM 보내고 True 반환. 아니면 False."""
    if is_running(handle) and is_timed_out(handle):
        log.warning(
            "[codegen] timeout %ds 초과 — 강제 종료 (PID=%d)",
            handle.timeout_sec, handle.pid,
        )
        handle.timed_out = True
        stop_codegen(handle)
        return True
    return False


def output_size_bytes(handle: CodegenHandle) -> int:
    """codegen 출력 파일 크기. 0 = 액션 미녹화."""
    try:
        return handle.output_path.stat().st_size
    except OSError:
        return 0


def _read_pipe(pipe) -> str:
    if pipe is None:
        return ""
    try:
        data = pipe.read()
    except (ValueError, OSError):
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""
