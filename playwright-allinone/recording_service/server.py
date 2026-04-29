"""Recording service FastAPI entry point (Phase R-MVP TR.1/TR.2).

Boot:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

The endpoint table is in docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.1".
At TR.2 /start /stop are wired up to the real codegen subprocess.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Optional

from pathlib import Path as _Path

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import (
    codegen_runner, converter_proxy, post_process, session, storage,
)
from .codegen_runner import CodegenError, CodegenHandle
from .converter_proxy import ConverterProxyError

log = logging.getLogger("recording_service")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


app = FastAPI(
    title="DSCORE Recording Service",
    version=__version__,
    description="Phase R-MVP: user actions → 14-DSL auto-conversion (host GUI + container CLI delegation)",
)

# CORS — the Web UI is called from the host browser, which is not the same origin.
# This is a daemon limited to operator-trusted host environments, so allow wildcard
# at the R-MVP stage.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single-worker assumption. Process-local registry.
_registry = session.SessionRegistry()

# sid → CodegenHandle map (TR.2). Popen does not serialize, so it cannot live on
# the Session itself — managed in a separate dict. Single-worker assumption.
_handles: dict[str, CodegenHandle] = {}
_handles_lock = threading.Lock()


def _set_handle(sid: str, handle: CodegenHandle) -> None:
    with _handles_lock:
        _handles[sid] = handle


def _pop_handle(sid: str) -> Optional[CodegenHandle]:
    with _handles_lock:
        return _handles.pop(sid, None)


def _get_handle(sid: str) -> Optional[CodegenHandle]:
    with _handles_lock:
        return _handles.get(sid)


# Hook for tests to patch so a fake handle is returned instead of a real subprocess.
# Monkeypatch _start_codegen_impl on the server module.
def _start_codegen_impl(
    target_url: str,
    output_path,
    *,
    timeout_sec: int,
    extra_args: Optional[list[str]] = None,
) -> CodegenHandle:
    """Codegen start hook. ``extra_args`` is forwarded to ``playwright codegen`` as is
    (e.g. ``--load-storage <path>`` + fingerprint options — P3.7).
    """
    return codegen_runner.start_codegen(
        target_url, output_path, timeout_sec=timeout_sec, extra_args=extra_args,
    )


def _stop_codegen_impl(handle: CodegenHandle) -> CodegenHandle:
    return codegen_runner.stop_codegen(handle)


def _run_convert_impl(
    *, container_session_dir: str, host_scenario_path: str,
):
    """TR.3 conversion-stage monkeypatch hook."""
    return converter_proxy.run_convert(
        container_session_dir=container_session_dir,
        host_scenario_path=host_scenario_path,
    )


def _save_metadata_preserving_auth(sid: str, new_meta: dict) -> None:
    """Preserve the ``auth_profile`` key when ``recording_stop`` updates metadata.

    Post-review fix — ``save_metadata`` is a *full overwrite*, so the done/error
    branches of stop silently dropped the auth_profile that start put in. Result:
    on replay ``_resolve_auth_for_replay`` could not find auth_profile in the
    metadata, skipped the verify gate, and missed expirations.

    When the caller passes a new metadata dict, lift only auth_profile from the
    existing metadata and merge.
    """
    existing = storage.load_metadata(sid) or {}
    auth = existing.get("auth_profile")
    if auth and "auth_profile" not in new_meta:
        new_meta = dict(new_meta)
        new_meta["auth_profile"] = auth
    storage.save_metadata(sid, new_meta)


# ── auth-profile integration helpers (P3.7) ─────────────────────────────────

def _resolve_auth_profile_extras(
    profile_name: Optional[str],
) -> tuple[Optional[list[str]], bool]:
    """auth_profile name → codegen extra_args + machine_mismatch flag.

    Returns:
        (extra_args, machine_mismatch). extra_args is ``--load-storage <path> +
        fingerprint options``. ``(None, False)`` when profile is None.

    Raises:
        HTTPException(404): profile not found.
        HTTPException(409): verify failed (expired) — UI branches into the re-seed modal.
        HTTPException(503): environment issues such as missing CHIPS support.
    """
    if not profile_name:
        return None, False
    # auth_profiles depends on POSIX bits (fcntl, etc.), so import lazily.
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import (
        AuthProfileError,
        ChipsNotSupportedError,
        ProfileNotFoundError,
    )

    try:
        prof = auth_profiles.get_profile(profile_name)
    except ProfileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"reason": "profile_not_found", "name": profile_name},
        )
    except AuthProfileError as e:
        raise HTTPException(
            status_code=400,
            detail={"reason": "profile_error", "message": str(e)},
        )

    try:
        ok, vdetail = auth_profiles.verify_profile(prof)
    except ChipsNotSupportedError as e:
        raise HTTPException(
            status_code=503,
            detail={"reason": "chips_not_supported", "message": str(e)},
        )
    except AuthProfileError as e:
        raise HTTPException(
            status_code=400,
            detail={"reason": "verify_failed", "message": str(e)},
        )

    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"reason": "profile_expired", **vdetail},
        )

    # Machine binding (D11) — do not block, just signal via header.
    machine_mismatch = (prof.host_machine_id != auth_profiles.current_machine_id())

    extra_args: list[str] = ["--load-storage", str(prof.storage_path)]
    extra_args += prof.fingerprint.to_playwright_open_args()
    return extra_args, machine_mismatch




# ── Request / response models ───────────────────────────────────────────────

class RecordingStartReq(BaseModel):
    target_url: str = Field(..., description="URL codegen loads when recording starts")
    planning_doc_ref: Optional[str] = Field(
        None,
        description="Planning doc reference (used by Phase R-Plus scenario A. R-MVP keeps it as metadata only)",
    )
    auth_profile: Optional[str] = Field(
        None,
        description=(
            "(P3.7) profile name from the auth_profiles catalog. When set, force "
            "verify_profile to pass before codegen starts and auto-inject storage_state + fingerprint options."
        ),
    )


class RecordingStartResp(BaseModel):
    id: str
    state: str
    target_url: str
    output_path: str = Field(..., description="host path codegen writes the .py to")


class HealthResp(BaseModel):
    ok: bool
    version: str
    codegen_available: bool
    host_root: str


class SessionResp(BaseModel):
    id: str
    state: str
    target_url: str
    created_at_iso: Optional[str]
    started_at_iso: Optional[str]
    ended_at_iso: Optional[str]
    action_count: int
    error: Optional[str]
    planning_doc_ref: Optional[str]
    auth_profile: Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/healthz", response_model=HealthResp)
def healthz() -> HealthResp:
    """Service health check. Returns whether codegen is available."""
    return HealthResp(
        ok=True,
        version=__version__,
        codegen_available=codegen_runner.is_codegen_available(),
        host_root=str(storage.host_root()),
    )


# ── import-script — accept user-supplied .py + register a session ───────────

# Upload validation — sanity-only. Trust model: only upload scripts you trust
# (host venv runs them directly = same risk as `python script.py`). Localhost-only
# daemon assumption. No size limit — users know how big their scripts are.
_IMPORT_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_STEP_HINT_RE = re.compile(
    r"\.(click|fill|press|select_option|check|hover|goto|drag_to|set_input_files)\s*\(",
)


def _estimate_import_step_count(text: str) -> int:
    """Rough estimate of step count for an uploaded script — count Playwright API calls."""
    return len(_STEP_HINT_RE.findall(text))


@app.post("/recording/import-script", status_code=201)
async def import_script(file: UploadFile = File(...)) -> dict:
    """Upload a Playwright Python script → register a new session directory.

    Then run it directly in the host venv via ``▶ Run original test code`` on the
    result view. An alternate entry point to "Start Recording" — replay an
    already-written script without going through codegen recording.

    Validation (sanity-only):
      - Extension ``.py``
      - UTF-8 decoding + Python AST parse
      - The token ``playwright`` appears in the body (typo guard)

    Raises:
        400: validation failed
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    safe_name = _IMPORT_FILENAME_SAFE_RE.sub("_", file.filename)
    if not safe_name.endswith(".py"):
        raise HTTPException(status_code=400, detail="only .py files can be uploaded")

    body = await file.read()
    if not body.strip():
        raise HTTPException(status_code=400, detail="empty file")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="UTF-8 decoding failed")

    import ast as _ast
    try:
        _ast.parse(text)
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Python syntax error: {e}")

    if "playwright" not in text:
        raise HTTPException(
            status_code=400,
            detail="`playwright` import not found — does not look like a Playwright script",
        )

    # Register the session — reuse uuid as is. target_url is marked imported.
    sess = _registry.create(target_url=f"(imported: {safe_name})")
    sid = sess.id
    sess_dir = storage.session_dir(sid)
    (sess_dir / "original.py").write_text(text, encoding="utf-8")

    step_count = _estimate_import_step_count(text)
    import time as _time
    storage.save_metadata(sid, {
        "id": sid,
        "target_url": sess.target_url,
        "created_at": storage.now_iso(),
        "created_at_ts": _time.time(),
        "state": session.STATE_DONE,
        "step_count": step_count,
        "imported_filename": safe_name,
    })
    _registry.update(
        sid,
        state=session.STATE_DONE,
        action_count=step_count,
    )

    # Try to convert — like a codegen session, generate a 14-DSL scenario.json.
    # Failure is silent (only Play with LLM is unavailable — Run original test code
    # still works).
    convert_summary = _convert_imported_script(sid)

    log.info(
        "[/recording/import-script] %s — uploaded '%s' (%d bytes, ~%d steps) convert=%s",
        sid, safe_name, len(body), step_count, convert_summary,
    )
    return {
        "id": sid,
        "imported_filename": safe_name,
        "step_count": step_count,
        "size_bytes": len(body),
        "convert": convert_summary,
    }


def _convert_imported_script(sid: str) -> dict:
    """Convert the uploaded ``original.py`` → ``scenario.json`` (silent fail).

    docker missing / container down / converter failure all silent — the user can
    still replay via ``Run original test code`` (host venv direct). Only the result
    summary is returned in the response.
    """
    container_dir = storage.container_path_for(sid)
    host_scenario = str(storage.scenario_path(sid))
    try:
        result = _run_convert_impl(
            container_session_dir=container_dir,
            host_scenario_path=host_scenario,
        )
    except ConverterProxyError as e:
        log.warning("[/recording/import-script] %s — converter failed: %s", sid, e)
        return {"ok": False, "scenario_exists": False, "error": str(e)}
    if result.returncode != 0 or not result.scenario_exists:
        msg = (result.stderr or "").strip()[:500] or f"rc={result.returncode}"
        log.warning("[/recording/import-script] %s — convert rc=%d, stderr=%s",
                    sid, result.returncode, msg)
        return {
            "ok": False,
            "scenario_exists": result.scenario_exists,
            "returncode": result.returncode,
            "stderr_tail": msg,
        }
    return {
        "ok": True,
        "scenario_exists": True,
        "elapsed_ms": result.elapsed_ms,
    }


@app.post("/recording/start", response_model=RecordingStartResp, status_code=201)
def recording_start(req: RecordingStartReq, response: Response) -> RecordingStartResp:
    """Create a new recording session + start the codegen subprocess (TR.2).

    P3.7 — when auth_profile is set, gate on verify + auto-inject extra_args.
    On machine mismatch, signal the UI via the ``X-Auth-Machine-Mismatch: 1``
    response header.

    Order (post-review fix): perform auth verification *before* ``registry.create``.
    Prevents the regression of an orphan pending session sticking in memory when
    verification fails.
    """
    # P3.7 — when auth_profile is set, run verify gate + build extra_args.
    # Called before session creation — verification failure becomes 4xx, so no
    # orphan session is left behind.
    extra_args, machine_mismatch = _resolve_auth_profile_extras(req.auth_profile)

    sess = _registry.create(
        target_url=req.target_url,
        planning_doc_ref=req.planning_doc_ref,
    )

    output_path = storage.original_py_path(sess.id)

    # Start the codegen subprocess. On failure, mark the session error and return 4xx.
    try:
        handle = _start_codegen_impl(
            req.target_url,
            output_path,
            timeout_sec=codegen_runner.DEFAULT_TIMEOUT_SEC,
            extra_args=extra_args,
        )
    except CodegenError as e:
        _registry.update(sess.id, state=session.STATE_ERROR, error=str(e))
        log.error("[/recording/start] codegen failed to start — %s", e)
        # Missing playwright is an operator problem → 503. Other input errors → 400.
        if "not found" in str(e):
            raise HTTPException(status_code=503, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    import time as _time
    _set_handle(sess.id, handle)
    _registry.update(
        sess.id,
        state=session.STATE_RECORDING,
        started_at=_time.time(),
        pid=handle.pid,
    )

    # Persistence directory + metadata (in recording state)
    # P3.8 — auth_profile metadata only lives in metadata.json (D15: scenario.json untouched).
    # Same value is preserved in in-memory session.extras → exposed in the SessionResp.
    meta: dict = {
        "id": sess.id,
        "target_url": sess.target_url,
        "planning_doc_ref": sess.planning_doc_ref,
        "created_at": storage.now_iso(),
        "state": session.STATE_RECORDING,
        "pid": handle.pid,
    }
    if req.auth_profile:
        meta["auth_profile"] = req.auth_profile
        sess.extras["auth_profile"] = req.auth_profile
    storage.save_metadata(sess.id, meta)

    log.info(
        "[/recording/start] session %s — codegen started (PID=%d, output=%s, auth=%s)",
        sess.id, handle.pid, output_path, req.auth_profile or "-",
    )
    # Surface machine mismatch via header — UI shows the modal. Setting the header
    # on the FastAPI-injected Response keeps the response body model as RecordingStartResp.
    if machine_mismatch:
        response.headers["X-Auth-Machine-Mismatch"] = "1"
    return RecordingStartResp(
        id=sess.id,
        state=session.STATE_RECORDING,
        target_url=sess.target_url,
        output_path=str(output_path),
    )


@app.post("/recording/stop/{sid}", status_code=202)
def recording_stop(sid: str) -> dict:
    """Terminate codegen + verify the output file (TR.2).

    At TR.3 this endpoint is extended to also call docker exec --convert-only.
    At this stage we just terminate codegen + verify + mark state=converting (waiting to convert).
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")

    handle = _pop_handle(sid)
    if handle is None:
        # No handle — start failed previously, or stop was already called
        log.warning("[/recording/stop] %s no handle (state=%s)", sid, sess.state)
        raise HTTPException(
            status_code=409,
            detail=f"session {sid} has no active codegen handle. (current state={sess.state})",
        )

    import time as _time
    handle = _stop_codegen_impl(handle)
    output_size = codegen_runner.output_size_bytes(handle)
    action_count_estimate = _estimate_action_count(handle.output_path) if output_size > 0 else 0

    # P3.10 — replace the absolute storage path in codegen output .py with an env var (D3).
    # If we started with ``--load-storage=<abs>`` in the seed environment, codegen embeds
    # ``storage_state="<abs>"`` in the output. Leaving it would break replay on another machine.
    # If ``original.py`` has no match, silent no-op (a session recorded without auth).
    try:
        post_process.portabilize_storage_path(handle.output_path)
    except Exception as e:  # noqa: BLE001 — post-processing failure must not break the stop flow.
        log.warning("[/recording/stop] portabilize failed (continuing) — %s", e)

    if output_size == 0:
        msg = "0 actions recorded — codegen output file is empty."
        _registry.update(
            sid,
            state=session.STATE_ERROR,
            ended_at=_time.time(),
            action_count=0,
            error=msg,
        )
        log.warning("[/recording/stop] %s — %s", sid, msg)
        return {
            "id": sid,
            "state": session.STATE_ERROR,
            "error": msg,
            "returncode": handle.returncode,
        }

    if handle.timed_out:
        log.warning("[/recording/stop] %s — codegen timeout (elapsed > %ds)", sid, handle.timeout_sec)

    # Right before conversion — mark state=converting (TR.3)
    _registry.update(
        sid,
        state=session.STATE_CONVERTING,
        ended_at=_time.time(),
        action_count=action_count_estimate,
    )

    # TR.3 — docker exec delegated conversion
    container_dir = storage.container_path_for(sid)
    host_scenario = str(storage.scenario_path(sid))

    convert_error: Optional[str] = None
    convert_result = None
    try:
        convert_result = _run_convert_impl(
            container_session_dir=container_dir,
            host_scenario_path=host_scenario,
        )
    except ConverterProxyError as e:
        convert_error = str(e)
        log.error("[/recording/stop] %s — converter_proxy failed: %s", sid, e)

    if convert_error is not None:
        # docker missing / timeout — mark state=error
        _registry.update(sid, state=session.STATE_ERROR, error=convert_error)
        _save_metadata_preserving_auth(sid, {
            "id": sid,
            "state": session.STATE_ERROR,
            "error": convert_error,
            "ended_at": storage.now_iso(),
        })
        return {
            "id": sid,
            "state": session.STATE_ERROR,
            "error": convert_error,
        }

    if convert_result.returncode != 0 or not convert_result.scenario_exists:
        # Container conversion failed — surface stderr as is + preserve original .py
        msg = (
            f"convert failed (returncode={convert_result.returncode}). "
            "original.py is preserved. partial stderr — "
            + (convert_result.stderr[:500] if convert_result.stderr else "(no stderr)")
        )
        _registry.update(sid, state=session.STATE_ERROR, error=msg)
        _save_metadata_preserving_auth(sid, {
            "id": sid,
            "state": session.STATE_ERROR,
            "error": msg,
            "returncode": convert_result.returncode,
            "ended_at": storage.now_iso(),
        })
        log.warning("[/recording/stop] %s — convert failed (rc=%d)", sid, convert_result.returncode)
        return {
            "id": sid,
            "state": session.STATE_ERROR,
            "returncode": convert_result.returncode,
            "stderr": convert_result.stderr,
            "error": msg,
        }

    # Convert succeeded — load scenario.json and compute the exact step count
    scenario = storage.load_scenario(sid)
    final_step_count = len(scenario) if scenario else 0

    _registry.update(
        sid,
        state=session.STATE_DONE,
        action_count=final_step_count,
    )
    _save_metadata_preserving_auth(sid, {
        "id": sid,
        "target_url": sess.target_url,
        "state": session.STATE_DONE,
        "ended_at": storage.now_iso(),
        "output_size_bytes": output_size,
        "action_count_estimate": action_count_estimate,
        "step_count": final_step_count,
        "codegen_returncode": handle.returncode,
        "convert_returncode": convert_result.returncode,
        "convert_elapsed_ms": convert_result.elapsed_ms,
        "timed_out": handle.timed_out,
    })

    log.info(
        "[/recording/stop] %s — convert succeeded (%d steps, convert_elapsed=%.0fms)",
        sid, final_step_count, convert_result.elapsed_ms,
    )
    return {
        "id": sid,
        "state": session.STATE_DONE,
        "step_count": final_step_count,
        "scenario_path": host_scenario,
        "output_size_bytes": output_size,
        "convert_elapsed_ms": convert_result.elapsed_ms,
        "timed_out": handle.timed_out,
    }


def _estimate_action_count(py_path) -> int:
    """Very rough estimate of page-action lines in the codegen output .py.

    Not exact counting — only used to verify "actions != 0".
    """
    try:
        text = py_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    keywords = (
        ".click(", ".fill(", ".press(", ".select_option(", ".check(",
        ".hover(", ".goto(", ".set_input_files(", ".drag_to(", ".scroll_into_view",
    )
    return sum(1 for line in text.splitlines() if any(k in line for k in keywords))


def _session_to_resp(s) -> SessionResp:
    """Session → SessionResp. Lift ``extras['auth_profile']`` to top-level (P5.8)."""
    d = s.to_dict()
    extras = d.pop("extras", None) or {}
    if "auth_profile" in extras:
        d["auth_profile"] = extras["auth_profile"]
    return SessionResp(**d)


@app.get("/recording/sessions", response_model=list[SessionResp])
def list_sessions() -> list[SessionResp]:
    return [_session_to_resp(s) for s in _registry.list()]


@app.get("/recording/sessions/{sid}", response_model=SessionResp)
def get_session(sid: str) -> SessionResp:
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    return _session_to_resp(sess)


@app.get("/recording/sessions/{sid}/scenario", include_in_schema=False)
def get_session_scenario(sid: str, download: int = 0):
    """Return the session's converted 14-DSL scenario.json body (TR.4 / TR.4+.2).

    Args:
        download: 1 to attach via ``Content-Disposition: attachment``;
            0 returns the raw JSON body (default — for browser display).

    Lets the front-end UI show the DSL on the result panel so the user can
    review the structure before adding assertions. Only 200 when state=done
    and the file exists; 404 otherwise.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    p = storage.scenario_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"scenario.json missing (state={sess.state})",
        )
    if download:
        return FileResponse(
            str(p),
            media_type="application/json",
            filename=f"{sid}-scenario.json",
        )
    return storage.load_scenario(sid)


@app.get("/recording/sessions/{sid}/original", include_in_schema=False)
def get_session_original(sid: str, download: int = 0):
    """Return the original ``.py`` body codegen produced (TR.4+.1).

    Args:
        download: 1 = attachment download, 0 = ``text/x-python`` body.

    Works in all of {right before stop / done / error} — even on conversion
    failure the user can review the original and edit it manually.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    p = storage.original_py_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"original.py missing (state={sess.state})",
        )
    if download:
        return FileResponse(
            str(p),
            media_type="text/x-python",
            filename=f"{sid}-original.py",
        )
    # Use text/plain for browser display — safer (avoids browsers downloading .py).
    return FileResponse(str(p), media_type="text/plain")


# ── P1 (item 5) — Per-step result visualization: run_log + screenshots ──────

# Screenshot filename whitelist — prevents path traversal and matches the format
# the executor produces. Examples: step_1_pass.png / step_2_healed.png /
# step_3_fail.png / final_state.png.
_SCREENSHOT_NAME_RE = re.compile(r"^(step_\d+_[a-z_]+|final_state)\.png$")


@app.get("/recording/sessions/{sid}/run-log", include_in_schema=False)
def get_session_run_log(sid: str) -> list:
    """Parse ``run_log.jsonl`` after a Play run and return per-step results.

    Fills the ``screenshot`` field per step — only if ``step_<n>_<status>.png``
    exists on disk. The modal-zoom endpoint is ``/screenshot/{name}``.

    Sessions without a run-log (Play not run) → 404.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    p = storage.run_log_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail="run_log.jsonl missing — Play with LLM has not run",
        )
    sess_dir = storage.session_dir(sid)
    out: list = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                step_no = rec.get("step")
                status = (rec.get("status") or "").lower()
                # Match screenshots by the executor's _screenshot naming scheme
                # (step_<n>_pass.png / step_<n>_fail.png / step_<n>_healed.png, etc.).
                shot_name = None
                if step_no is not None and status:
                    candidate = f"step_{step_no}_{status}.png"
                    if (sess_dir / candidate).is_file():
                        shot_name = candidate
                rec["screenshot"] = shot_name
                out.append(rec)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"run_log read failed: {e}"
        ) from e
    return out


@app.get("/recording/sessions/{sid}/play-log/tail", include_in_schema=False)
def get_play_log_tail(
    sid: str,
    kind: str = "llm",
    from_: int = Query(0, alias="from"),
):
    """During a Play run, return the bytes after ``from`` from
    ``play-llm.log`` / ``play-codegen.log`` (P2 — progress streaming).

    The frontend polls every 1s for live progress. If the file does not exist yet
    (subprocess just started), return 200 + ``exists=false`` rather than 404 —
    the polling client legitimately encounters the pre-creation moment.

    Args:
        kind: ``llm`` (default) or ``codegen``.
        from: ``offset`` from the previous poll. 0 on the first call.
    """
    if kind not in ("llm", "codegen"):
        raise HTTPException(
            status_code=400, detail=f"kind must be llm/codegen — received {kind!r}",
        )
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    fname = "play-llm.log" if kind == "llm" else "play-codegen.log"
    p = storage.session_dir(sid) / fname
    if not p.is_file():
        return {"content": "", "offset": 0, "exists": False, "kind": kind}
    try:
        size = p.stat().st_size
        if from_ < 0 or from_ > size:
            from_ = 0
        with p.open("rb") as f:
            f.seek(from_)
            chunk = f.read()
        return {
            "content": chunk.decode("utf-8", errors="replace"),
            "offset": from_ + len(chunk),
            "exists": True,
            "kind": kind,
        }
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"log tail failed: {e}") from e


@app.get("/recording/sessions/{sid}/screenshot/{name}", include_in_schema=False)
def get_session_screenshot(sid: str, name: str):
    """Return a screenshot PNG from the session directory directly.

    Path-traversal guard — ``name`` is forced through a regex whitelist of
    ``step_<digit>_<word>.png`` or ``final_state.png``. Else 400.
    """
    if not _SCREENSHOT_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"disallowed screenshot name: {name!r}",
        )
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    p = storage.session_dir(sid) / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"screenshot missing: {name}")
    return FileResponse(str(p), media_type="image/png")


# ── Item 4 — LLM-healed regression .py download ─────────────────────────────

@app.get("/recording/sessions/{sid}/regression", include_in_schema=False)
def get_session_regression(sid: str, download: int = 0):
    """Return the body of the ``regression_test.py`` the executor auto-generated, or attach for download.

    Only exists after Play with LLM. The user diffs it against the codegen original
    and downloads from this endpoint when adopting it as a regression test.

    Args:
        download: 1 = ``Content-Disposition: attachment``, 0 = body.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    p = storage.regression_py_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail="regression_test.py missing — Play with LLM has not run",
        )
    if download:
        return FileResponse(
            str(p),
            media_type="text/x-python",
            filename=f"{sid}-regression_test.py",
        )
    return FileResponse(str(p), media_type="text/plain")


@app.delete("/recording/sessions/{sid}", status_code=204)
def delete_session(sid: str):
    """Delete the session memory + persistence directory. Stops active codegen first if any."""
    if not _registry.get(sid):
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")

    # If active codegen remains, SIGTERM it
    handle = _pop_handle(sid)
    if handle is not None:
        log.info("[/recording/sessions] %s deleting after stopping active codegen", sid)
        _stop_codegen_impl(handle)

    _registry.delete(sid)
    storage.delete_session(sid)
    log.info("[/recording/sessions] session %s deleted", sid)
    return None


# ── /recording/sessions/{id}/assertion (TR.4 — supplement actions codegen does not produce) ──

# Beyond verify / mock_*, codegen also doesn't record scroll / hover, which can be added
# via the same form. Lazy-render / Intersection Observer / GNB hover-only menus and
# similar user-intent actions can be added back into the scenario.
ASSERTION_ALLOWED_ACTIONS = {
    "verify", "mock_status", "mock_data", "scroll", "hover",
}
# Actions whose value may be empty — matches DSL's _VALUE_REQUIRED_ACTIONS (only hover).
_ASSERTION_VALUE_OPTIONAL = {"hover"}
# Whitelist for scroll value — matches zero_touch_qa.__main__._SCROLL_VALID_VALUES.
_ASSERTION_SCROLL_VALUES = {"into_view", "into-view", "into view"}


class AssertionAddReq(BaseModel):
    action: str = Field(
        ...,
        description="One of verify / mock_status / mock_data / scroll / hover. Lets the user manually add 14-DSL actions codegen does not emit.",
    )
    target: str = Field(..., description="CSS selector or URL pattern")
    value: str = Field("", description="Expected value / status code / JSON body / scroll mode (empty allowed for hover)")
    description: str = ""
    condition: Optional[str] = Field(
        None, description="Condition for verify (e.g. text / visible / url)",
    )


@app.post("/recording/sessions/{sid}/assertion", status_code=201)
def add_assertion(sid: str, req: AssertionAddReq) -> dict:
    """After recording, the user manually adds steps codegen does not produce
    (verify / mock_* / scroll / hover).

    Asymmetry compensation per PLAN §"TR.4 Assertion-add area". Since codegen does
    not emit page.route / expect / scroll / hover, the operator inputs them by hand
    to complete the full 14-DSL scenario.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"adding steps requires a converted (state=done) session. "
                f"current state={sess.state}"
            ),
        )
    if req.action not in ASSERTION_ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"action must be one of {sorted(ASSERTION_ALLOWED_ACTIONS)}. "
                f"received: {req.action!r}"
            ),
        )
    if not req.target.strip():
        raise HTTPException(status_code=400, detail="target is empty.")
    if req.action not in _ASSERTION_VALUE_OPTIONAL and not req.value.strip():
        raise HTTPException(status_code=400, detail="value is empty.")
    if req.action == "scroll":
        scroll_v = req.value.strip().lower()
        if scroll_v not in _ASSERTION_SCROLL_VALUES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"scroll value must be one of {sorted(_ASSERTION_SCROLL_VALUES)}. "
                    f"received: {req.value!r}"
                ),
            )

    scenario = storage.load_scenario(sid)
    if scenario is None:
        raise HTTPException(
            status_code=409,
            detail=f"session {sid} has no scenario.json. Convert it first.",
        )

    next_step = max((s.get("step", 0) for s in scenario), default=0) + 1
    new_step: dict = {
        "step": next_step,
        "action": req.action,
        "target": req.target,
        "value": req.value,
        "description": req.description or _default_description(req.action, req.target, req.value),
    }
    if req.action == "verify" and req.condition:
        new_step["condition"] = req.condition

    scenario.append(new_step)

    # Lightweight host-side sanity only — deeper _validate_scenario runs at the next convert/execute.
    import json as _json
    storage.scenario_path(sid).write_text(
        _json.dumps(scenario, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _registry.update(sid, action_count=len(scenario))

    log.info(
        "[/assertion] %s — added step %d (action=%s target=%s)",
        sid, next_step, req.action, req.target,
    )
    return {
        "id": sid,
        "step_added": next_step,
        "step_count": len(scenario),
        "added_step": new_step,
    }


def _default_description(action: str, target: str, value: str) -> str:
    if action == "verify":
        return f"verify {target} = {value}"
    if action == "mock_status":
        return f"{target} → status {value}"
    if action == "mock_data":
        return f"{target} → mock body"
    if action == "scroll":
        return f"scroll {target} into view"
    if action == "hover":
        return f"hover {target}"
    return ""


# ── Diagnostic / internal (test-friendly) ───────────────────────────────────

def _reset_for_tests() -> None:
    """For pytest fixtures. Do not call from production code."""
    _registry.clear()
    with _handles_lock:
        _handles.clear()


# ── Disk session absorption (TR.8 persistence) ──────────────────────────────

@app.on_event("startup")
def _absorb_disk_sessions() -> None:
    """On server restart, restore sessions from the host persistence root into the in-memory registry.

    State is whatever metadata.json's last state was. Active codegen handles are
    not restored (the subprocess died with the server). Sessions that were in
    state=recording are marked 'orphan' to make this explicit to the user.
    """
    import time as _time
    try:
        ids = storage.list_session_dirs()
    except Exception as e:  # noqa: BLE001
        log.warning("[startup] disk session absorption failed: %s", e)
        return

    absorbed = 0
    for sid in ids:
        if _registry.get(sid) is not None:
            continue  # already present (e.g. from a test)
        meta = storage.load_metadata(sid) or {}
        target_url = meta.get("target_url", "")
        state = meta.get("state", session.STATE_DONE)
        if state == session.STATE_RECORDING:
            # codegen has already died — mark as orphan
            state = session.STATE_ERROR
            error_msg = "codegen subprocess was severed by server restart (orphan)."
        else:
            error_msg = meta.get("error")

        sess = _registry.create(target_url=target_url)
        # uuid4 was just generated — force the id to match the disk sid
        with _registry._lock:
            del _registry._sessions[sess.id]
            sess.id = sid
            sess.state = state
            sess.created_at = meta.get("created_at_ts", _time.time())
            if error_msg:
                sess.error = error_msg
            sess.action_count = meta.get("step_count", meta.get("action_count_estimate", 0))
            # P5.8 — restore auth_profile metadata (kept across server restart in the session table).
            if "auth_profile" in meta:
                sess.extras["auth_profile"] = meta["auth_profile"]
            _registry._sessions[sid] = sess
        absorbed += 1

    if absorbed:
        log.info("[startup] absorbed %d disk sessions (host_root=%s)",
                 absorbed, storage.host_root())


# ── Static files / Web UI (TR.4) ────────────────────────────────────────────

_WEB_DIR = _Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
def root_index() -> FileResponse:
    """On `/` entry, return index.html (TR.4 Web UI)."""
    index_path = _WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "Web UI static files not found — recording_service/web/index.html is missing."
            ),
        )
    return FileResponse(str(index_path))


# Serve app.js / style.css etc. under /static/*. Separate from /healthz and /recording/* APIs.
if _WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


# R-Plus router — `/experimental/sessions/{sid}/replay|enrich|compare`.
# Per user request, the gate has been removed (TR.4+.4) — always active. The URL
# prefix `/experimental/` is kept for code organization (it makes clear that
# replay/enrich/compare are a separate track from R-MVP).
from .rplus.router import router as _rplus_router  # noqa: E402

app.include_router(_rplus_router)


# ─────────────────────────────────────────────────────────────────────────
# Auth Profile endpoints (P3.2 ~ P3.6)
# ─────────────────────────────────────────────────────────────────────────
#
# Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.6
#
# - GET    /auth/profiles                       — catalog list (for the dropdown)
# - POST   /auth/profiles/seed                  — start seeding (background thread)
# - GET    /auth/profiles/seed/{seed_sid}       — seed-progress polling
# - POST   /auth/profiles/{name}/verify         — explicit verify
# - DELETE /auth/profiles/{name}                — delete

import time as _time_auth  # noqa: E402  (aliased to avoid name clashes)
import uuid as _uuid_auth  # noqa: E402

from dataclasses import dataclass as _dataclass_auth, field as _field_auth  # noqa: E402


# ── Seed-progress tracking (P3.3 / P3.4) ────────────────────────────────────

@_dataclass_auth
class _SeedJob:
    """State tracking for the seed background thread."""
    seed_sid: str
    state: str                          # "running" / "ready" / "error"
    started_at: float
    timeout_sec: int
    phase: str = "starting"             # "starting" / "login_waiting" / "verifying" / "ready" / "error"
    message: str = "starting seed"
    profile_name: Optional[str] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None    # 'timeout' / 'subprocess' / 'validate' / 'verify' / 'unknown'


_seed_jobs: dict[str, _SeedJob] = {}
_seed_jobs_lock = threading.Lock()


class AuthProfileSummary(BaseModel):
    """Brief info for the dropdown / list."""
    name: str
    service_domain: str
    last_verified_at: Optional[str]
    ttl_hint_hours: int
    chips_supported: bool
    session_storage_warning: bool


class AuthProfileDetail(AuthProfileSummary):
    """Single-profile detail — used to prefill the [Re-seed] flow on the expiration modal (P2.1).

    Summary + verify spec (service_url / service_text / whether naver_probe is enabled).
    seed_url is not stored in the catalog, so the client infers it from the
    verify_service_url origin. The user can edit it.
    """
    verify_service_url: str
    verify_service_text: str
    naver_probe_enabled: bool


class AuthSeedReq(BaseModel):
    name: str
    seed_url: str = Field(
        ..., description="⚠️ entry URL of the *service* under test (not the Naver login URL)",
    )
    verify_service_url: str
    verify_service_text: str = ""
    naver_probe: bool = True
    service_domain: Optional[str] = None
    ttl_hint_hours: int = 12
    notes: str = ""
    timeout_sec: int = 600


class AuthSeedStartResp(BaseModel):
    seed_sid: str
    state: str


class AuthSeedPollResp(BaseModel):
    seed_sid: str
    state: str
    phase: str
    message: str
    profile_name: Optional[str] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None
    elapsed_sec: float
    timeout_sec: int


class AuthVerifyResp(BaseModel):
    ok: bool
    service_ms: Optional[int] = None
    naver_probe_ms: Optional[int] = None
    naver_ok: Optional[bool] = None
    fail_reason: Optional[str] = None


_AUTH_ERROR_KIND_MAP = {
    "SeedTimeoutError": "timeout",
    "SeedSubprocessError": "subprocess",
    "EmptyDumpError": "validate",
    "MissingDomainError": "validate",
    "SeedVerifyFailedError": "verify",
    "ChipsNotSupportedError": "chips",
    "InvalidProfileNameError": "input",
    "InvalidServiceDomainError": "input",
}


def _seed_worker(job: _SeedJob, req: AuthSeedReq) -> None:
    """Background thread — call auth_profiles.seed_profile + update state."""
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import (
        AuthProfileError, NaverProbeSpec, VerifySpec,
    )

    def _progress(phase: str, message: str) -> None:
        with _seed_jobs_lock:
            job.phase = phase
            job.message = message
        log.info(
            "[/auth/profiles/seed] phase=%s — seed_sid=%s msg=%s",
            phase, job.seed_sid, message,
        )

    try:
        verify = VerifySpec(
            service_url=req.verify_service_url,
            service_text=req.verify_service_text,
            naver_probe=NaverProbeSpec() if req.naver_probe else None,
        )
        prof = auth_profiles.seed_profile(
            name=req.name,
            seed_url=req.seed_url,
            verify=verify,
            service_domain=req.service_domain,
            ttl_hint_hours=req.ttl_hint_hours,
            notes=req.notes,
            timeout_sec=req.timeout_sec,
            progress_callback=_progress,
        )
        with _seed_jobs_lock:
            job.state = "ready"
            job.phase = "ready"
            job.message = f"seed done — profile '{prof.name}' has been saved."
            job.profile_name = prof.name
        log.info("[/auth/profiles/seed] done — seed_sid=%s name=%s", job.seed_sid, prof.name)
    except AuthProfileError as e:
        kind = _AUTH_ERROR_KIND_MAP.get(type(e).__name__, "auth_error")
        with _seed_jobs_lock:
            job.state = "error"
            job.phase = "error"
            job.message = f"seed failed — {e}"
            job.error = str(e)
            job.error_kind = kind
        log.warning(
            "[/auth/profiles/seed] failed (%s) — seed_sid=%s err=%s",
            kind, job.seed_sid, e,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("[/auth/profiles/seed] worker raised unexpected exception")
        with _seed_jobs_lock:
            job.state = "error"
            job.phase = "error"
            job.message = f"seed failed — {e!r}"
            job.error = repr(e)
            job.error_kind = "unknown"


@app.get("/auth/profiles", response_model=list[AuthProfileSummary])
def auth_profiles_list() -> list[AuthProfileSummary]:
    """Registered auth-profile list (for the dropdown)."""
    from zero_touch_qa import auth_profiles
    return [
        AuthProfileSummary(
            name=p.name,
            service_domain=p.service_domain,
            last_verified_at=p.last_verified_at,
            ttl_hint_hours=p.ttl_hint_hours,
            chips_supported=p.chips_supported,
            session_storage_warning=p.session_storage_warning,
        )
        for p in auth_profiles.list_profiles()
    ]


@app.get("/auth/profiles/{name}", response_model=AuthProfileDetail)
def auth_profile_get(name: str) -> AuthProfileDetail:
    """Single profile detail — used by the expiration modal's [Re-seed] prefill (P2.1).

    Returns Summary + verify spec. The UI uses it to prefill the seed modal.
    """
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import ProfileNotFoundError
    try:
        p = auth_profiles.get_profile(name)
    except ProfileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"reason": "profile_not_found", "name": name},
        )
    return AuthProfileDetail(
        name=p.name,
        service_domain=p.service_domain,
        last_verified_at=p.last_verified_at,
        ttl_hint_hours=p.ttl_hint_hours,
        chips_supported=p.chips_supported,
        session_storage_warning=p.session_storage_warning,
        verify_service_url=p.verify.service_url,
        verify_service_text=p.verify.service_text,
        naver_probe_enabled=p.verify.naver_probe is not None,
    )


@app.post("/auth/profiles/seed", response_model=AuthSeedStartResp, status_code=201)
def auth_profiles_seed_start(req: AuthSeedReq) -> AuthSeedStartResp:
    """Start seeding — runs ``playwright open --save-storage`` in a background thread.

    Returns immediately with a ``seed_sid`` for ``GET /auth/profiles/seed/{seed_sid}``
    polling. Once the user logs in + passes 2FA in a separate window and closes it,
    the thread finishes verify and transitions to ``state=ready``.
    """
    seed_sid = _uuid_auth.uuid4().hex[:12]
    job = _SeedJob(
        seed_sid=seed_sid,
        state="running",
        started_at=_time_auth.time(),
        timeout_sec=req.timeout_sec,
    )
    with _seed_jobs_lock:
        _seed_jobs[seed_sid] = job
    threading.Thread(target=_seed_worker, args=(job, req), daemon=True).start()
    log.info(
        "[/auth/profiles/seed] start — seed_sid=%s name=%s seed_url=%s",
        seed_sid, req.name, req.seed_url,
    )
    return AuthSeedStartResp(seed_sid=seed_sid, state=job.state)


@app.get("/auth/profiles/seed/{seed_sid}", response_model=AuthSeedPollResp)
def auth_profiles_seed_poll(seed_sid: str) -> AuthSeedPollResp:
    """Poll seed progress."""
    with _seed_jobs_lock:
        job = _seed_jobs.get(seed_sid)
    if job is None:
        raise HTTPException(status_code=404, detail=f"seed job not found: {seed_sid}")
    return AuthSeedPollResp(
        seed_sid=seed_sid,
        state=job.state,
        phase=job.phase,
        message=job.message,
        profile_name=job.profile_name,
        error=job.error,
        error_kind=job.error_kind,
        elapsed_sec=_time_auth.time() - job.started_at,
        timeout_sec=job.timeout_sec,
    )


@app.post("/auth/profiles/{name}/verify", response_model=AuthVerifyResp)
def auth_profile_verify(name: str, naver_probe: bool = True) -> AuthVerifyResp:
    """Explicit verify — the UI's ``↻ verify`` button calls this."""
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import AuthProfileError, ProfileNotFoundError
    try:
        prof = auth_profiles.get_profile(name)
        ok, detail = auth_profiles.verify_profile(prof, naver_probe=naver_probe)
    except ProfileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"reason": "profile_not_found", "name": name},
        )
    except AuthProfileError as e:
        raise HTTPException(
            status_code=400,
            detail={"reason": "profile_error", "message": str(e)},
        )
    return AuthVerifyResp(ok=ok, **detail)


@app.delete("/auth/profiles/{name}", status_code=204)
def auth_profile_delete(name: str) -> Response:
    """Delete an auth-profile (catalog + storage file)."""
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import AuthProfileError, ProfileNotFoundError
    try:
        auth_profiles.delete_profile(name)
    except ProfileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"reason": "profile_not_found", "name": name},
        )
    except AuthProfileError as e:
        raise HTTPException(
            status_code=400,
            detail={"reason": "delete_failed", "message": str(e)},
        )
    return Response(status_code=204)
