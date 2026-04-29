"""Wrapper around the Playwright codegen subprocess (Phase R-MVP TR.2).

Design: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.2"

Responsibilities:
- Start codegen via subprocess.Popen and track the handle
- SIGTERM (with grace) → SIGKILL termination flow
- Time-limit checks (`is_timed_out` / `terminate_if_timed_out`)
- Output-file size verification (0 = no actions recorded)
- Clear error messages (playwright not installed / target_url unreachable)

This module is host-only. It does not run inside the container.
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


# Codegen time limit, overridable via env var. Default 30 minutes.
DEFAULT_TIMEOUT_SEC = int(os.environ.get("RECORDING_CODEGEN_TIMEOUT_SEC", "1800"))


@dataclass
class CodegenHandle:
    """Tracking handle for a running codegen subprocess."""

    pid: int
    started_at: float
    output_path: Path
    process: subprocess.Popen
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    target_url: str = ""
    # Result recorded after stop_codegen
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    extras: dict = field(default_factory=dict)

    def elapsed_sec(self) -> float:
        return time.time() - self.started_at


class CodegenError(RuntimeError):
    """Explicit error for the codegen start/stop stages."""


def is_codegen_available() -> bool:
    """Whether the `playwright` executable is on PATH."""
    return shutil.which("playwright") is not None


def start_codegen(
    target_url: str,
    output_path: Path,
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    extra_args: Optional[list[str]] = None,
) -> CodegenHandle:
    """Start playwright codegen.

    Raises:
        CodegenError: playwright executable missing or subprocess launch failed
    """
    if not is_codegen_available():
        raise CodegenError(
            "playwright executable not found. "
            "Check the host venv PATH or REQ_PKGS in mac-agent-setup.sh."
        )

    if not target_url or not isinstance(target_url, str):
        raise CodegenError(f"target_url is invalid: {target_url!r}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "playwright", "codegen",
        target_url,
        "--target=python",
        "--output", str(output_path),
    ]
    if extra_args:
        cmd.extend(extra_args)

    log.info("[codegen] start: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        raise CodegenError(f"failed to launch codegen subprocess: {e}") from e

    return CodegenHandle(
        pid=proc.pid,
        started_at=time.time(),
        output_path=output_path,
        process=proc,
        timeout_sec=timeout_sec,
        target_url=target_url,
    )


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal the process group. Falls back to single PID if no group permission or already dead."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


def _close_pipes(proc: subprocess.Popen) -> None:
    """Close stdout/stderr pipes. Avoids waiting for EOF on FDs the child still holds."""
    for p in (proc.stdout, proc.stderr):
        if p is None:
            continue
        try:
            p.close()
        except Exception:  # noqa: BLE001
            pass


def _terminate_proc(proc: subprocess.Popen, grace_sec: float) -> None:
    """SIGTERM → grace → SIGKILL. Per process group (covers child Chromium)."""
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_sec)
        return
    except subprocess.TimeoutExpired:
        log.warning("[codegen] SIGKILL (grace of %.1fs exceeded)", grace_sec)
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        log.error("[codegen] still not exited after SIGKILL — closing handle only")


def stop_codegen(handle: CodegenHandle, *, grace_sec: float = 5.0) -> CodegenHandle:
    """SIGTERM → grace → SIGKILL. Records the result on the handle and returns it.

    For already-exited processes, skip the signal and just collect returncode.

    Signals are sent per process group, so child Chromium is killed alongside
    Playwright codegen in one shot. This also prevents _read_pipe from blocking
    forever when a child holds the stdout/stderr pipes open and EOF never arrives.
    """
    proc = handle.process
    if proc.poll() is None:
        log.info("[codegen] SIGTERM PID=%d (elapsed=%.1fs)", proc.pid, handle.elapsed_sec())
        _terminate_proc(proc, grace_sec)

    # Reading the pipes blocks forever if child Chromiums never close the FDs.
    # handle.stdout/stderr are unused anywhere, so we close them and leave empty.
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
    """If past the timeout, send SIGTERM and return True. Otherwise False."""
    if is_running(handle) and is_timed_out(handle):
        log.warning(
            "[codegen] timeout %ds exceeded — forced termination (PID=%d)",
            handle.timeout_sec, handle.pid,
        )
        handle.timed_out = True
        stop_codegen(handle)
        return True
    return False


def output_size_bytes(handle: CodegenHandle) -> int:
    """Codegen output file size. 0 = no actions recorded."""
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
