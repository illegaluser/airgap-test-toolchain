"""Playwright codegen subprocess 래퍼 (Phase R-MVP TR.2).

설계: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.2"

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
            start_new_session=True,
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


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Process group 에 신호. group 권한 없거나 이미 죽었으면 단일 PID fallback.

    Windows 는 process group 개념이 POSIX 와 달라 ``os.killpg`` 자체가 없다 —
    이 경우 곧장 단일 PID 로 신호를 보낸다.
    """
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.send_signal(sig)
    except ProcessLookupError:
        pass


def _close_pipes(proc: subprocess.Popen) -> None:
    """stdout/stderr pipe 를 닫는다. 자식이 잡고 있던 FD 의 EOF 를 기다리지 않는다."""
    for p in (proc.stdout, proc.stderr):
        if p is None:
            continue
        try:
            p.close()
        except Exception:  # noqa: BLE001
            pass


def _kill_process_tree_windows(pid: int) -> None:
    """Windows: ``taskkill /F /T /PID <pid>`` 로 process tree 강제 종료.

    Windows 는 POSIX 의 process group 개념이 없어 ``proc.send_signal(SIGTERM)``
    이 호출되면 codegen 부모만 죽고 그 자식 Chrome for Testing 은 부모 사망을
    자동 감지하지 않아 orphan 상태로 살아남는다. ``taskkill /T`` 는 PID 의
    process tree 전체 (자식 / 손자 포함) 를 한 번에 종료한다.

    이미 죽은 PID 면 rc=128 로 silently no-op.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[codegen] taskkill tree 실패 (PID=%d): %s", pid, e)


def _terminate_proc(proc: subprocess.Popen, grace_sec: float) -> None:
    """codegen subprocess + 자식 Chromium 종료.

    POSIX: ``start_new_session=True`` 로 띄운 process group 단위로
    ``SIGTERM`` → 유예 → ``SIGKILL`` (자식 Chromium 자동 포함).

    Windows: process group 개념 부재 → ``taskkill /F /T /PID`` 한 번으로 트리
    전체 강제 종료. codegen 의 ``.py`` 출력은 사용자가 액션할 때마다 incremental
    하게 디스크에 기록되므로 강제 종료 시점에도 이미 저장됨.
    """
    if os.name == "nt":
        _kill_process_tree_windows(proc.pid)
        try:
            proc.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            log.error("[codegen] taskkill 후에도 wait timeout — handle 만 닫음")
        return

    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_sec)
        return
    except subprocess.TimeoutExpired:
        log.warning("[codegen] SIGKILL (유예 %.1fs 초과)", grace_sec)
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        log.error("[codegen] SIGKILL 후에도 종료되지 않음 — handle 만 닫음")


def stop_codegen(handle: CodegenHandle, *, grace_sec: float = 5.0) -> CodegenHandle:
    """SIGTERM → 유예 후 SIGKILL. handle 에 결과 기록 후 그대로 반환.

    이미 종료된 프로세스에는 신호를 안 보내고 즉시 returncode 만 회수.

    Process group 단위로 신호 전송: Playwright codegen 의 자식 Chromium 까지
    포함해 한 번에 종료. 자식이 stdout/stderr pipe 를 잡고 있으면 EOF 가
    오지 않아 _read_pipe 가 영구 블록되는 것을 방지.
    """
    proc = handle.process
    if proc.poll() is None:
        log.info("[codegen] SIGTERM PID=%d (elapsed=%.1fs)", proc.pid, handle.elapsed_sec())
        _terminate_proc(proc, grace_sec)

    # pipe read 는 자식 Chromium 들이 FD 를 안 닫으면 무기한 블록된다.
    # handle.stdout/stderr 는 어디서도 사용되지 않으므로 그냥 닫고 빈 값으로 둔다.
    _close_pipes(proc)
    handle.stdout = ""
    handle.stderr = ""
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
