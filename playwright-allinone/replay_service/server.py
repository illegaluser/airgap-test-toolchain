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


class ProfileSummary(BaseModel):
    alias: str
    storage: str  # "ok" | "missing"
    last_verified_at: Optional[str]
    service_domain: str


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
            )
        )
    return out


# --- 시드 (수동, subprocess 추적) -------------------------------------------


_seed_lock = threading.Lock()
_seed_state: dict[str, dict] = {}  # alias → {"pid": int, "phase": str, "message": str, "finished": bool}


class SeedRequest(BaseModel):
    target_url: str


@app.post("/api/profiles/{alias}/seed")
def api_seed_start(alias: str, req: SeedRequest):
    """수동 시드 시작 — playwright open --save-storage subprocess 띄움.

    UI 가 GET /seed/status 로 진행 폴링.
    """
    alias = _safe_name(alias)
    target = req.target_url
    if not target:
        raise HTTPException(status_code=400, detail="target_url 필수")

    storage_dir = auth_profiles._root() if hasattr(auth_profiles, "_root") else (_monitor_home() / "auth-profiles")
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_file = storage_dir / f"{alias}.storage.json"

    # subprocess: playwright open <url> --save-storage <file>
    cmd = [
        sys.executable, "-m", "playwright", "open",
        target,
        "--save-storage", str(storage_file),
    ]
    try:
        proc = subprocess.Popen(cmd)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"playwright 호출 실패: {e}")

    with _seed_lock:
        _seed_state[alias] = {
            "pid": proc.pid,
            "phase": "running",
            "message": "브라우저에서 직접 로그인 후 창을 닫아 주세요",
            "started_at": _utc_iso(),
            "finished": False,
            "storage_file": str(storage_file),
            "_proc": proc,  # 객체는 응답에 포함 안 함.
        }
    return {"alias": alias, "phase": "running", "pid": proc.pid}


@app.get("/api/profiles/{alias}/seed/status")
def api_seed_status(alias: str):
    alias = _safe_name(alias)
    with _seed_lock:
        st = _seed_state.get(alias)
    if st is None:
        return {"alias": alias, "phase": "idle"}
    proc = st.get("_proc")
    if proc is not None:
        rc = proc.poll()
        if rc is not None:
            # 종료됨.
            storage_file = Path(st["storage_file"])
            saved = storage_file.is_file() and storage_file.stat().st_size > 0
            with _seed_lock:
                st["finished"] = True
                st["phase"] = "saved" if saved else "aborted"
                st["message"] = (
                    "storage 저장 완료" if saved else "브라우저 종료 — 저장 실패 (파일 없음)"
                )
                st["return_code"] = rc

                # 카탈로그에 등록 (이미 등록되어 있으면 skip).
                if saved:
                    try:
                        existing = auth_profiles.get_profile(alias)
                    except auth_profiles.AuthProfileError:
                        existing = None
                    if existing is None:
                        # seed 후 카탈로그 수동 등록을 단순화 — 여기서는 storage 파일만
                        # 두고 등록은 별도 API/CLI 흐름으로 위임 (1차 단순화).
                        pass
    public = {k: v for k, v in st.items() if not k.startswith("_")}
    public["alias"] = alias
    return public


@app.delete("/api/profiles/{alias}")
def api_delete_profile(alias: str):
    alias = _safe_name(alias)
    try:
        auth_profiles.delete_profile(alias)
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
        raise HTTPException(status_code=404, detail=f"bundle 미존재: {name}")

    # 사전 차단 (B4) — alias 미시드면 실행 거부.
    meta = _read_bundle_meta(bundle_path)
    ab = (meta or {}).get("auth_bundle") or {}
    alias = ab.get("alias", "")
    if not _is_alias_seeded(alias):
        raise HTTPException(
            status_code=412,
            detail=f"alias '{alias}' 시드 필요 (Login Profile 카드에서 시드 후 재시도)",
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
