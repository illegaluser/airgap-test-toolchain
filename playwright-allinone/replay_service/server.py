"""Replay UI — FastAPI 서비스 (모니터링 PC 의 GUI).

기동::

    python -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094

설계:
- bind 는 **127.0.0.1 전용** (계획 D10).
- 사용자 startup task 로 기동 (D9 — OS 서비스 X).
- 데이터 루트 = ``~/.dscore.ttc.monitor/`` 또는 ``MONITOR_HOME`` env.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from zero_touch_qa import auth_profiles


app = FastAPI(
    title="DSCORE Replay UI",
    description="모니터링 PC 용 — bundle 실행 + 시각 결과 검증 (계획 §11)",
)


# --- 데이터 루트 ------------------------------------------------------------


def _monitor_home() -> Path:
    """모니터링 PC 데이터 루트. install-monitor 가 만든 디렉토리."""
    raw = os.environ.get("MONITOR_HOME") or "~/.dscore.ttc.monitor"
    root = Path(raw).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bundles_dir() -> Path:
    p = _monitor_home() / "scenarios"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _runs_dir() -> Path:
    p = _monitor_home() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def _safe_name(name: str) -> str:
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"안전하지 않은 이름: {name}")
    return name


# --- /api/profiles ----------------------------------------------------------
#
# 시드 / verify / 카탈로그 등록 흐름은 Recording UI 의 /auth/profiles/* 와
# 동일 구조로 미러링 (분석 결과 한 줄 요약):
#   1) POST /api/profiles/seed       — background thread, auth_profiles.seed_profile() 호출
#   2) GET  /api/profiles/seed/{sid} — phase / message / elapsed / state 폴링
#   3) GET  /api/profiles/{name}     — re-seed 모달 prefill 용 detail
#   4) GET  /api/profiles            — list (Login Profile 카드)
#   5) DELETE /api/profiles/{name}   — 삭제
#
# 단계 (phase): starting → login_waiting → verifying → ready (또는 error)
# 사용자는 브라우저 직접 닫기 → seed_profile 이 verify_url 도달 + 텍스트 확인 후 카탈로그 등록.


class ProfileSummary(BaseModel):
    alias: str                 # 프로파일 이름 (UI 카드 컬럼명과 일치)
    storage: str               # "ok" | "missing"
    last_verified_at: Optional[str]
    service_domain: str
    ttl_hint_hours: int = 12
    session_storage_warning: bool = False


class ProfileDetail(ProfileSummary):
    """re-seed 모달의 입력 prefill 용 detail."""
    verify_service_url: str
    verify_service_text: str
    naver_probe_enabled: bool


class AuthSeedReq(BaseModel):
    """시드 시작 입력 — Recording UI 의 AuthSeedReq 와 동일 형태."""
    name: str
    seed_url: str
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
    state: str          # running / ready / error
    phase: str          # starting / login_waiting / verifying / ready / error
    message: str
    profile_name: Optional[str] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None
    elapsed_sec: float
    timeout_sec: int


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


class _SeedJob:
    """시드 background thread 의 상태 추적 (Recording UI 의 _SeedJob 미러)."""
    __slots__ = (
        "seed_sid", "state", "started_at", "timeout_sec",
        "phase", "message", "profile_name", "error", "error_kind",
    )

    def __init__(self, seed_sid: str, timeout_sec: int):
        self.seed_sid = seed_sid
        self.state = "running"
        self.started_at = time.time()
        self.timeout_sec = timeout_sec
        self.phase = "starting"
        self.message = "시드 시작 중"
        self.profile_name: Optional[str] = None
        self.error: Optional[str] = None
        self.error_kind: Optional[str] = None


_seed_jobs: dict[str, _SeedJob] = {}
_seed_jobs_lock = threading.Lock()


def _seed_worker(job: _SeedJob, req: AuthSeedReq) -> None:
    """background thread — auth_profiles.seed_profile() 호출 + 상태 갱신."""
    from zero_touch_qa.auth_profiles import (
        AuthProfileError, NaverProbeSpec, VerifySpec,
    )

    def _progress(phase: str, message: str) -> None:
        with _seed_jobs_lock:
            job.phase = phase
            job.message = message

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
            job.message = f"시드 완료 — 프로파일 '{prof.name}' 이 저장되었습니다."
            job.profile_name = prof.name
    except AuthProfileError as e:
        kind = _AUTH_ERROR_KIND_MAP.get(type(e).__name__, "auth_error")
        with _seed_jobs_lock:
            job.state = "error"
            job.phase = "error"
            job.message = f"시드 실패 — {e}"
            job.error = str(e)
            job.error_kind = kind
    except Exception as e:  # noqa: BLE001
        with _seed_jobs_lock:
            job.state = "error"
            job.phase = "error"
            job.message = f"시드 실패 — {e!r}"
            job.error = repr(e)
            job.error_kind = "unknown"


@app.get("/api/profiles", response_model=list[ProfileSummary])
def api_list_profiles() -> list[ProfileSummary]:
    out: list[ProfileSummary] = []
    for p in auth_profiles.list_profiles():
        out.append(
            ProfileSummary(
                alias=p.name,
                storage="ok" if p.storage_path.is_file() else "missing",
                last_verified_at=p.last_verified_at,
                service_domain=p.service_domain,
                ttl_hint_hours=p.ttl_hint_hours,
                session_storage_warning=p.session_storage_warning,
            )
        )
    return out


@app.get("/api/profiles/{name}", response_model=ProfileDetail)
def api_profile_detail(name: str) -> ProfileDetail:
    """단일 프로파일 detail — re-seed 모달의 prefill 에 사용."""
    name = _safe_name(name)
    try:
        p = auth_profiles.get_profile(name)
    except auth_profiles.AuthProfileError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ProfileDetail(
        alias=p.name,
        storage="ok" if p.storage_path.is_file() else "missing",
        last_verified_at=p.last_verified_at,
        service_domain=p.service_domain,
        ttl_hint_hours=p.ttl_hint_hours,
        session_storage_warning=p.session_storage_warning,
        verify_service_url=p.verify.service_url,
        verify_service_text=p.verify.service_text,
        naver_probe_enabled=p.verify.naver_probe is not None,
    )


@app.post("/api/profiles/seed", response_model=AuthSeedStartResp, status_code=201)
def api_seed_start(req: AuthSeedReq) -> AuthSeedStartResp:
    """시드 시작 — background thread 에서 auth_profiles.seed_profile() 호출.

    사용자는 별도 창에서 직접 로그인 + 인증 통과 후 *수동으로 창을 닫는다*. 워커
    스레드가 그 후 verify (verify_service_url 접근 + 텍스트 확인) 를 마치면 카탈로그에
    프로파일을 등록하고 state=ready 로 전환한다. UI 는 GET /api/profiles/seed/{sid}
    로 phase/message 를 폴링한다.
    """
    seed_sid = uuid.uuid4().hex[:12]
    job = _SeedJob(seed_sid=seed_sid, timeout_sec=req.timeout_sec)
    with _seed_jobs_lock:
        _seed_jobs[seed_sid] = job
    threading.Thread(target=_seed_worker, args=(job, req), daemon=True).start()
    return AuthSeedStartResp(seed_sid=seed_sid, state=job.state)


@app.get("/api/profiles/seed/{seed_sid}", response_model=AuthSeedPollResp)
def api_seed_poll(seed_sid: str) -> AuthSeedPollResp:
    """시드 진행 상태 폴링."""
    with _seed_jobs_lock:
        job = _seed_jobs.get(seed_sid)
    if job is None:
        raise HTTPException(status_code=404, detail=f"시드 작업을 찾을 수 없음: {seed_sid}")
    return AuthSeedPollResp(
        seed_sid=seed_sid,
        state=job.state,
        phase=job.phase,
        message=job.message,
        profile_name=job.profile_name,
        error=job.error,
        error_kind=job.error_kind,
        elapsed_sec=time.time() - job.started_at,
        timeout_sec=job.timeout_sec,
    )


@app.delete("/api/profiles/{name}")
def api_delete_profile(name: str):
    name = _safe_name(name)
    try:
        auth_profiles.delete_profile(name)
    except auth_profiles.AuthProfileError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(status_code=204)


# --- /api/bundles -----------------------------------------------------------


class BundleSummary(BaseModel):
    name: str          # 파일명
    alias: str         # bundle 안 metadata 의 alias
    verify_url: str
    seeded: bool       # alias 가 카탈로그에 시드되어 있는지 (B4)
    size: int
    uploaded_at: str


def _read_bundle_meta(zip_path: Path) -> dict:
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as z:
            return json.loads(z.read("metadata.json").decode("utf-8"))
    except Exception:
        return {}


def _is_alias_seeded(alias: str) -> bool:
    if not alias:
        return False
    try:
        prof = auth_profiles.get_profile(alias)
    except auth_profiles.AuthProfileError:
        return False
    return prof.storage_path.is_file()


@app.get("/api/bundles", response_model=list[BundleSummary])
def api_list_bundles() -> list[BundleSummary]:
    out: list[BundleSummary] = []
    for p in sorted(_bundles_dir().glob("*.zip")):
        meta = _read_bundle_meta(p)
        ab = (meta or {}).get("auth_bundle") or {}
        alias = ab.get("alias", "")
        out.append(
            BundleSummary(
                name=p.name,
                alias=alias,
                verify_url=ab.get("verify_url", ""),
                seeded=_is_alias_seeded(alias),
                size=p.stat().st_size,
                uploaded_at=datetime.fromtimestamp(
                    p.stat().st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    return out


@app.post("/api/bundles", status_code=201)
async def api_upload_bundle(
    file: UploadFile = File(...),
    overwrite: int = Query(0, description="1 이면 동일 이름 덮어쓰기"),
):
    name = file.filename or ""
    if not name.endswith(".zip"):
        raise HTTPException(status_code=400, detail=".zip 만 허용")
    safe = _safe_name(name)
    target = _bundles_dir() / safe
    if target.exists() and not overwrite:
        raise HTTPException(
            status_code=409, detail=f"이미 존재: {safe}. overwrite=1 으로 재시도",
        )
    content = await file.read()
    target.write_bytes(content)
    meta = _read_bundle_meta(target)
    ab = (meta or {}).get("auth_bundle") or {}
    return {"name": safe, "size": len(content), "alias": ab.get("alias", "")}


@app.delete("/api/bundles/{name}")
def api_delete_bundle(name: str):
    name = _safe_name(name)
    target = _bundles_dir() / name
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"미존재: {name}")
    target.unlink()
    return Response(status_code=204)


# --- /api/runs --------------------------------------------------------------


_run_lock = threading.Lock()
_runs: dict[str, dict] = {}  # run_id → {state, bundle, alias, out_dir, started_at, _proc}


class RunRequest(BaseModel):
    bundle_name: str


def _make_run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _run_subprocess_target(run_id: str, bundle_path: Path, out_dir: Path) -> None:
    """별도 스레드에서 monitor replay subprocess 실행 + 종료 시 상태 갱신."""
    cmd = [
        sys.executable, "-m", "monitor", "replay",
        str(bundle_path), "--out", str(out_dir),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
        )
    except Exception as e:
        with _run_lock:
            _runs[run_id]["state"] = "error"
            _runs[run_id]["error"] = str(e)
        return

    with _run_lock:
        _runs[run_id]["_proc"] = proc

    # stdout 을 줄 단위로 모아 _runs 의 stdout_log 에 누적 (SSE 가 폴링).
    stdout_log: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            stdout_log.append(line.rstrip("\n"))
            with _run_lock:
                _runs[run_id]["stdout_log"] = list(stdout_log)
    rc = proc.wait()
    with _run_lock:
        _runs[run_id]["state"] = "done"
        _runs[run_id]["exit_code"] = rc
        _runs[run_id]["finished_at"] = _utc_iso()


@app.post("/api/runs", status_code=201)
def api_start_run(req: RunRequest):
    name = _safe_name(req.bundle_name)
    bundle_path = _bundles_dir() / name
    if not bundle_path.is_file():
        raise HTTPException(status_code=404, detail=f"시나리오 묶음을 찾을 수 없음: {name}")

    # 사전 차단 — 로그인 프로파일이 등록 안 됐으면 실행 거부.
    meta = _read_bundle_meta(bundle_path)
    ab = (meta or {}).get("auth_bundle") or {}
    alias = ab.get("alias", "")
    if not _is_alias_seeded(alias):
        raise HTTPException(
            status_code=412,
            detail=f"로그인 프로파일 '{alias}' 등록 필요 (로그인 프로파일 카드에서 등록 후 재시도)",
        )

    run_id = _make_run_id()
    out_dir = _runs_dir() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with _run_lock:
        _runs[run_id] = {
            "run_id": run_id,
            "bundle": name,
            "alias": alias,
            "out_dir": str(out_dir),
            "state": "running",
            "started_at": _utc_iso(),
            "stdout_log": [],
        }
    threading.Thread(
        target=_run_subprocess_target,
        args=(run_id, bundle_path, out_dir),
        daemon=True,
    ).start()
    return {"run_id": run_id, "state": "running", "bundle": name, "alias": alias}


@app.get("/api/runs")
def api_list_runs():
    """최근 실행 목록 — 메모리에 있는 것 + 디스크에서 발견한 것 합산."""
    seen: dict[str, dict] = {}
    with _run_lock:
        for r in _runs.values():
            seen[r["run_id"]] = {
                k: v for k, v in r.items() if not k.startswith("_") and k != "stdout_log"
            }
    # 디스크에서도 추가 — 메모리에 없는 과거 run.
    for d in sorted(_runs_dir().iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("run-") and d.name not in seen:
            meta_p = d / "meta.json"
            if meta_p.is_file():
                try:
                    seen[d.name] = json.loads(meta_p.read_text(encoding="utf-8"))
                    seen[d.name]["run_id"] = d.name
                    seen[d.name]["state"] = "done"
                except Exception:
                    pass
    return sorted(seen.values(), key=lambda r: r.get("started_at", ""), reverse=True)


@app.get("/api/runs/{run_id}")
def api_run_meta(run_id: str):
    run_id = _safe_name(run_id)
    out_dir = _runs_dir() / run_id
    if not out_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run 미존재: {run_id}")
    meta_p = out_dir / "meta.json"
    base: dict = {"run_id": run_id, "out_dir": str(out_dir)}
    if meta_p.is_file():
        try:
            base.update(json.loads(meta_p.read_text(encoding="utf-8")))
        except Exception:
            pass
    with _run_lock:
        if run_id in _runs:
            base.update(
                {
                    k: v
                    for k, v in _runs[run_id].items()
                    if not k.startswith("_") and k != "stdout_log"
                }
            )
    return base


@app.get("/api/runs/{run_id}/stream")
def api_run_stream(run_id: str):
    run_id = _safe_name(run_id)

    def gen():
        last_idx = 0
        # 30 분 timeout — 스트림 제한.
        deadline = time.time() + 1800
        while time.time() < deadline:
            with _run_lock:
                rec = _runs.get(run_id)
                if rec is None:
                    yield f"event: error\ndata: run not found\n\n"
                    return
                log = list(rec.get("stdout_log", []))
                state = rec.get("state")
            new_lines = log[last_idx:]
            last_idx = len(log)
            for line in new_lines:
                yield f"data: {line}\n\n"
            if state == "done":
                yield f"event: done\ndata: {state}\n\n"
                return
            time.sleep(0.5)
        yield f"event: timeout\ndata: timeout\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/steps")
def api_run_steps(run_id: str):
    run_id = _safe_name(run_id)
    out_dir = _runs_dir() / run_id
    if not out_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run 미존재: {run_id}")
    log_p = out_dir / "codegen_run_log.jsonl"
    if not log_p.is_file():
        return {"steps": []}
    steps: list[dict] = []
    for line in log_p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        rec.setdefault("step", len(steps) + 1)
        steps.append(rec)
    return {"steps": steps}


@app.get("/api/runs/{run_id}/screenshot/{name}")
def api_run_screenshot(run_id: str, name: str):
    run_id = _safe_name(run_id)
    name = _safe_name(name)
    p = _runs_dir() / run_id / "screenshots" / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="스크린샷 미존재")
    media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(str(p), media_type=media)


@app.get("/api/runs/{run_id}/report.html")
def api_run_report(run_id: str):
    """self-contained HTML 리포트 — 기존 report_export 재사용."""
    run_id = _safe_name(run_id)
    sess_dir = _runs_dir() / run_id
    if not sess_dir.is_dir():
        raise HTTPException(status_code=404, detail="run 미존재")
    try:
        from recording_service.report_export import build_self_contained_report
    except Exception:
        raise HTTPException(status_code=500, detail="리포트 모듈 미사용 가능")
    body = build_self_contained_report(sess_dir)
    if body is None:
        raise HTTPException(status_code=404, detail="리포트 산출물 부족")
    return Response(
        content=body,
        media_type="text/html",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}-report.html"'
        },
    )


# --- 정적 파일 (UI) ---------------------------------------------------------

_WEB_DIR = Path(__file__).parent / "web"
if _WEB_DIR.is_dir() and (_WEB_DIR / "index.html").is_file():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(_WEB_DIR / "index.html"))

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")
