"""TR.7 — Play (R-Plus). Two modes:

1. **Codegen Output Replay** — run the codegen original ``original.py`` directly
   on the host. The recorded actions are reproduced visibly (headed).

2. **Play with LLM** — run the converted 14-DSL scenario (``scenario.json``)
   through the host zero_touch_qa executor. Full 14-DSL features
   (healing / verify / mock, etc.) plus headed playback.

Both modes shell out to a host venv python — the container docker exec path is
not used because it cannot display the screen. ``<venv_py>`` resolves to the
``RECORDING_VENV_PY`` env var or ``sys.executable`` (= the recording-service
daemon's venv python).

P4 — auth-profile integration:
    If the session's ``metadata.json`` has the ``auth_profile`` field (D15),
    we force a verify pass before replay begins, and automatically inject the
    storage_state argument plus the fingerprint env vars.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_REPLAY_TIMEOUT_SEC = int(
    os.environ.get("RECORDING_REPLAY_TIMEOUT_SEC", "300")
)


class ReplayProxyError(RuntimeError):
    """Explicit error for the play stage (shared by codegen / LLM)."""


class ReplayAuthExpiredError(ReplayProxyError):
    """auth-profile verify failed — re-seed required (P4.4).

    Split out as its own exception type so the UI can branch into the
    expiration modal.
    """

    def __init__(self, profile_name: str, detail: dict):
        super().__init__(
            f"auth-profile '{profile_name}' verify failed (re-seed required): {detail}"
        )
        self.profile_name = profile_name
        self.detail = dict(detail)


def _load_session_metadata(host_session_dir: str) -> dict:
    """Load the session directory's ``metadata.json``. Empty dict if missing."""
    p = Path(host_session_dir) / "metadata.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[replay] failed to load metadata.json (%s): %s", p, e)
        return {}


def _resolve_auth_for_replay(
    host_session_dir: str,
) -> tuple[Optional[str], Optional[dict], Optional[str]]:
    """``auth_profile`` from metadata.json → (storage_path, fingerprint_env, profile_name).

    - If the metadata has no auth_profile key → ``(None, None, None)`` (no-auth replay).
    - On profile lookup / verify failure → ``ReplayAuthExpiredError`` (P4.4).
    """
    meta = _load_session_metadata(host_session_dir)
    profile_name = meta.get("auth_profile")
    if not profile_name:
        return None, None, None

    # auth_profiles depends on fcntl, so import lazily.
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import (
        AuthProfileError, ProfileNotFoundError,
    )

    try:
        prof = auth_profiles.get_profile(profile_name)
    except ProfileNotFoundError as e:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "profile_not_found", "message": str(e)},
        ) from e
    except AuthProfileError as e:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "profile_error", "message": str(e)},
        ) from e

    try:
        ok, vdetail = auth_profiles.verify_profile(prof)
    except AuthProfileError as e:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "verify_error", "message": str(e)},
        ) from e
    if not ok:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "verify_failed", **vdetail},
        )

    return str(prof.storage_path), prof.fingerprint.to_env(), profile_name


@dataclass
class PlayResult:
    """Host subprocess run result — shared by codegen output / LLM modes.

    The codegen original is just a regular Playwright script, so there is no
    notion of 'PASS step count' — returncode 0 = ran end-to-end successfully.
    The 14-DSL executor also does not expose pass/fail counts in this response
    (an HTML report is written separately into artifacts and may be surfaced
    in the UI later).
    """
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float


def _resolve_venv_py(venv_py: str | None) -> str:
    return venv_py or os.environ.get("RECORDING_VENV_PY") or sys.executable


def _dump_play_log(cwd: str, log_name: str, cmd: list[str], stdout: str, stderr: str,
                   returncode: int, elapsed_ms: float) -> None:
    """Drop the subprocess stdout/stderr into the session directory so healer /
    executor internals can be traced after the fact. This is the only place where
    child-process output (which never reaches the daemon log) is preserved — the
    bridge between the scenario and the actual actions taken.

    Failures are silent — if this dump is blocked, the scenario result itself is
    unaffected.
    """
    try:
        path = Path(cwd) / log_name
        with path.open("w", encoding="utf-8") as f:
            f.write(f"# cmd: {' '.join(cmd)}\n")
            f.write(f"# returncode: {returncode}\n")
            f.write(f"# elapsed_ms: {elapsed_ms:.0f}\n")
            f.write("# ── stdout ──────────────────────────────────────\n")
            f.write(stdout or "(empty)\n")
            if not (stdout or "").endswith("\n"):
                f.write("\n")
            f.write("# ── stderr ──────────────────────────────────────\n")
            f.write(stderr or "(empty)\n")
    except OSError as e:
        log.warning("[play-log] dump failed (%s): %s", cwd, e)


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    env: dict | None,
    timeout_sec: int,
    started: float,
    log_name: str = "play.log",
) -> PlayResult:
    """Shared subprocess runner + PlayResult conversion."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = (time.time() - started) * 1000
        # On timeout too, dump whatever output accumulated — for tracing where it stopped
        partial_stdout = ""
        partial_stderr = ""
        if e.stdout:
            partial_stdout = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else str(e.stdout)
        if e.stderr:
            partial_stderr = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else str(e.stderr)
        _dump_play_log(cwd, log_name, cmd, partial_stdout, partial_stderr, -1, elapsed)
        raise ReplayProxyError(
            f"play did not finish within {timeout_sec}s (elapsed={elapsed:.0f}ms). "
            f"See {log_name} for partial output."
        ) from e
    except FileNotFoundError as e:
        raise ReplayProxyError(f"python invocation failed: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    _dump_play_log(cwd, log_name, cmd, stdout, stderr, proc.returncode, elapsed_ms)
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
    prefer_annotated: bool = True,
) -> PlayResult:
    """Run the codegen original ``original.py`` directly on the host (headed).

    Args:
        host_session_dir: host-side session directory — where ``original.py`` lives.
        timeout_sec: subprocess timeout (default 300s).
        venv_py: interpreter path override.
        prefer_annotated: if True and ``original_annotated.py`` exists, run it first
            (the T-H static hover-injection variant). Default True.
    """
    annotated = Path(host_session_dir) / "original_annotated.py"
    if prefer_annotated and annotated.is_file():
        script = annotated
    else:
        script = Path(host_session_dir) / "original.py"
    if not script.is_file():
        raise ReplayProxyError(f"target .py missing: {script}")

    py = _resolve_venv_py(venv_py)
    cmd = [py, str(script)]

    # P4.3 — auto-match auth-profile. If the metadata has auth_profile, pass verify
    # then inject AUTH_STORAGE_STATE_IN env (read by the portabilized original.py).
    # Also inject fingerprint env so the executor builds the context with the same fingerprint.
    storage_path, fingerprint_env, profile_name = _resolve_auth_for_replay(host_session_dir)
    env: Optional[dict]
    if storage_path:
        env = os.environ.copy()
        env["AUTH_STORAGE_STATE_IN"] = storage_path
        if fingerprint_env:
            env.update(fingerprint_env)
        log.info(
            "[play-codegen] auth-profile=%s storage=%s",
            profile_name, storage_path,
        )
    else:
        env = None

    log.info("[play-codegen] %s (script=%s)", " ".join(cmd), script.name)
    return _run_subprocess(
        cmd, cwd=host_session_dir, env=env,
        timeout_sec=timeout_sec, started=time.time(),
        log_name="play-codegen.log",
    )


def run_llm_play(
    *,
    host_session_dir: str,
    project_root: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    venv_py: str | None = None,
) -> PlayResult:
    """Run the converted 14-DSL ``scenario.json`` through the zero_touch_qa executor (headed).

    Args:
        host_session_dir: host-side session directory — ``scenario.json`` lives here
            and artifacts are written into the same folder (ARTIFACTS_DIR).
        project_root: project root containing the zero_touch_qa package — injected via PYTHONPATH.
        timeout_sec: subprocess timeout (default 300s).
        venv_py: interpreter path override.
    """
    scenario = Path(host_session_dir) / "scenario.json"
    if not scenario.is_file():
        raise ReplayProxyError(f"scenario.json missing: {scenario}")

    py = _resolve_venv_py(venv_py)
    cmd = [
        py, "-m", "zero_touch_qa",
        "--mode", "execute",
        "--scenario", str(scenario),
    ]

    # P4.2 — auto-match auth-profile. If the metadata has auth_profile, pass verify
    # then inject ``--storage-state-in <path>`` argument + fingerprint env.
    storage_path, fingerprint_env, profile_name = _resolve_auth_for_replay(host_session_dir)
    if storage_path:
        cmd += ["--storage-state-in", storage_path]
        log.info(
            "[play-llm] auth-profile=%s storage=%s",
            profile_name, storage_path,
        )

    env = os.environ.copy()
    env["PYTHONPATH"] = project_root + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    env["ARTIFACTS_DIR"] = host_session_dir
    if fingerprint_env:
        env.update(fingerprint_env)
    log.info("[play-llm] %s (cwd=%s)", " ".join(cmd), host_session_dir)
    return _run_subprocess(
        cmd, cwd=host_session_dir, env=env,
        timeout_sec=timeout_sec, started=time.time(),
        log_name="play-llm.log",
    )
