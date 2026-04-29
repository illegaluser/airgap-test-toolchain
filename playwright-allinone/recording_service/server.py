"""Recording 서비스 FastAPI 엔트리포인트 (Phase R-MVP TR.1/TR.2).

기동:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

엔드포인트 표는 PLAN_GROUNDING_RECORDING_AGENT.md §"TR.1" 참조.
TR.2 단계에서 /start /stop 이 실 codegen subprocess 와 연동된다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Optional

from pathlib import Path as _Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import (
    codegen_runner, converter_proxy, session, storage,
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
    description="Phase R-MVP: 사용자 행동 → 14-DSL 자동 변환 (호스트 GUI + 컨테이너 CLI 위임)",
)

# CORS — Web UI 가 호스트 브라우저에서 호출하므로 동일 출처가 아님.
# 운영자가 신뢰하는 호스트 환경 한정 데몬이니 와일드카드 허용 (R-MVP 단계).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 단일 worker 가정. 프로세스-로컬 레지스트리.
_registry = session.SessionRegistry()

# sid → CodegenHandle 맵 (TR.2). Popen 이 직렬화 안 되므로 Session 본체에는
# 안 넣고 별도 dict 로 관리. 단일 worker 전제.
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


# 테스트가 실 subprocess 대신 fake handle 을 반환하도록 patch 할 hook.
# server module 의 _start_codegen_impl 을 monkeypatch 하면 됨.
def _start_codegen_impl(target_url: str, output_path, *, timeout_sec: int) -> CodegenHandle:
    return codegen_runner.start_codegen(
        target_url, output_path, timeout_sec=timeout_sec,
    )


def _stop_codegen_impl(handle: CodegenHandle) -> CodegenHandle:
    return codegen_runner.stop_codegen(handle)


def _run_convert_impl(
    *, container_session_dir: str, host_scenario_path: str,
):
    """TR.3 변환 단계 monkeypatch hook."""
    return converter_proxy.run_convert(
        container_session_dir=container_session_dir,
        host_scenario_path=host_scenario_path,
    )




# ── 요청/응답 모델 ────────────────────────────────────────────────────────────

class RecordingStartReq(BaseModel):
    target_url: str = Field(..., description="녹화 시작 시 codegen 이 로드할 URL")
    planning_doc_ref: Optional[str] = Field(
        None,
        description="기획서 참조 (Phase R-Plus 시나리오 A 에서 사용. R-MVP 는 메타데이터만)",
    )


class RecordingStartResp(BaseModel):
    id: str
    state: str
    target_url: str
    output_path: str = Field(..., description="codegen 이 .py 를 쓰는 호스트 경로")


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


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@app.get("/healthz", response_model=HealthResp)
def healthz() -> HealthResp:
    """서비스 헬스체크. codegen 가용 여부 반환."""
    return HealthResp(
        ok=True,
        version=__version__,
        codegen_available=codegen_runner.is_codegen_available(),
        host_root=str(storage.host_root()),
    )


# ── 항목 (import-script) — 사용자 제공 .py 업로드 + 세션 등록 ──────────────

# 업로드 검증 — sanity 수준만. 신뢰 모델: 사용자 본인이 신뢰하는 스크립트만
# 업로드 (host venv 직접 실행 = python script.py 와 동일 위험). localhost-only
# 데몬 가정. 크기 제한 없음 — 사용자가 자신의 스크립트 크기를 안다.
_IMPORT_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_STEP_HINT_RE = re.compile(
    r"\.(click|fill|press|select_option|check|hover|goto|drag_to|set_input_files)\s*\(",
)


def _estimate_import_step_count(text: str) -> int:
    """업로드 스크립트의 step 수 거친 추정 — Playwright API 호출 개수."""
    return len(_STEP_HINT_RE.findall(text))


@app.post("/recording/import-script", status_code=201)
async def import_script(file: UploadFile = File(...)) -> dict:
    """Playwright Python 스크립트 업로드 → 새 세션 디렉토리 등록.

    이후 결과 화면의 ``▶ Codegen 녹화코드 실행`` 으로 host venv 에서 직접 실행.
    "Start Recording" 의 대안 진입점 — 이미 작성된 스크립트를 codegen 녹화 없이
    바로 재생.

    검증 (sanity 수준):
      - 확장자 ``.py``
      - UTF-8 디코딩 + Python AST 파싱 통과
      - 본문에 ``playwright`` 토큰 존재 (오타 방지)

    Raises:
        400: 검증 실패
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename 필수")
    safe_name = _IMPORT_FILENAME_SAFE_RE.sub("_", file.filename)
    if not safe_name.endswith(".py"):
        raise HTTPException(status_code=400, detail=".py 파일만 업로드 가능")

    body = await file.read()
    if not body.strip():
        raise HTTPException(status_code=400, detail="빈 파일")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="UTF-8 디코딩 실패")

    import ast as _ast
    try:
        _ast.parse(text)
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Python 구문 오류: {e}")

    if "playwright" not in text:
        raise HTTPException(
            status_code=400,
            detail="`playwright` import 미발견 — Playwright 스크립트가 아닌 것 같습니다",
        )

    # 세션 등록 — uuid 그대로 사용. target_url 은 imported 표시.
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

    # 변환 시도 — codegen 세션과 동일하게 14-DSL scenario.json 생성. 실패는
    # silent (Play with LLM 만 미사용 — 테스트코드 원본 실행 은 그대로 가능).
    convert_summary = _convert_imported_script(sid)

    log.info(
        "[/recording/import-script] %s — '%s' 업로드 (%d bytes, ~%d step) convert=%s",
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
    """업로드된 ``original.py`` → ``scenario.json`` 변환 (silent fail).

    docker 미설치 / 컨테이너 미가동 / converter 실패 모두 silent — 사용자는
    여전히 ``테스트코드 원본 실행`` (host venv 직접) 으로 재생 가능. 결과
    summary 만 응답에 포함.
    """
    container_dir = storage.container_path_for(sid)
    host_scenario = str(storage.scenario_path(sid))
    try:
        result = _run_convert_impl(
            container_session_dir=container_dir,
            host_scenario_path=host_scenario,
        )
    except ConverterProxyError as e:
        log.warning("[/recording/import-script] %s — converter 실패: %s", sid, e)
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
def recording_start(req: RecordingStartReq) -> RecordingStartResp:
    """새 녹화 세션 생성 + codegen subprocess 시작 (TR.2)."""
    sess = _registry.create(
        target_url=req.target_url,
        planning_doc_ref=req.planning_doc_ref,
    )

    output_path = storage.original_py_path(sess.id)

    # codegen subprocess 시작. 실패 시 세션을 error 상태로 마감 + 4xx 반환.
    try:
        handle = _start_codegen_impl(
            req.target_url,
            output_path,
            timeout_sec=codegen_runner.DEFAULT_TIMEOUT_SEC,
        )
    except CodegenError as e:
        _registry.update(sess.id, state=session.STATE_ERROR, error=str(e))
        log.error("[/recording/start] codegen 시작 실패 — %s", e)
        # playwright 미설치는 운영자 문제 → 503. 외 입력은 400.
        if "찾을 수 없습니다" in str(e):
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

    # 영속화 디렉토리 + 메타데이터 (recording 상태로)
    storage.save_metadata(sess.id, {
        "id": sess.id,
        "target_url": sess.target_url,
        "planning_doc_ref": sess.planning_doc_ref,
        "created_at": storage.now_iso(),
        "state": session.STATE_RECORDING,
        "pid": handle.pid,
    })

    log.info(
        "[/recording/start] 세션 %s — codegen 시작 (PID=%d, output=%s)",
        sess.id, handle.pid, output_path,
    )
    return RecordingStartResp(
        id=sess.id,
        state=session.STATE_RECORDING,
        target_url=sess.target_url,
        output_path=str(output_path),
    )


@app.post("/recording/stop/{sid}", status_code=202)
def recording_stop(sid: str) -> dict:
    """codegen 종료 + 출력 파일 검증 (TR.2).

    TR.3 에서 이 endpoint 가 docker exec --convert-only 까지 호출하도록 확장.
    현 단계는 codegen 종료 + 검증 + state=converting (변환 대기) 으로 마감.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")

    handle = _pop_handle(sid)
    if handle is None:
        # 핸들 없음 — start 에서 실패 마감됐거나, 이미 stop 호출됨
        log.warning("[/recording/stop] %s 핸들 없음 (state=%s)", sid, sess.state)
        raise HTTPException(
            status_code=409,
            detail=f"세션 {sid} 의 활성 codegen 핸들이 없습니다. (현재 state={sess.state})",
        )

    import time as _time
    handle = _stop_codegen_impl(handle)
    output_size = codegen_runner.output_size_bytes(handle)
    action_count_estimate = _estimate_action_count(handle.output_path) if output_size > 0 else 0

    if output_size == 0:
        msg = "녹화 액션 0건 — codegen 출력 파일이 비어있습니다."
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

    # 변환 직전 — state=converting 마킹 (TR.3)
    _registry.update(
        sid,
        state=session.STATE_CONVERTING,
        ended_at=_time.time(),
        action_count=action_count_estimate,
    )

    # TR.3 — docker exec 위임 변환
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
        log.error("[/recording/stop] %s — converter_proxy 실패: %s", sid, e)

    if convert_error is not None:
        # docker 미설치 / timeout — state=error 마감
        _registry.update(sid, state=session.STATE_ERROR, error=convert_error)
        storage.save_metadata(sid, {
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
        # 컨테이너 변환 실패 — stderr 그대로 노출 + 원본 .py 보존
        msg = (
            f"변환 실패 (returncode={convert_result.returncode}). "
            "원본 original.py 는 보존됩니다. stderr 일부 — "
            + (convert_result.stderr[:500] if convert_result.stderr else "(stderr 없음)")
        )
        _registry.update(sid, state=session.STATE_ERROR, error=msg)
        storage.save_metadata(sid, {
            "id": sid,
            "state": session.STATE_ERROR,
            "error": msg,
            "returncode": convert_result.returncode,
            "ended_at": storage.now_iso(),
        })
        log.warning("[/recording/stop] %s — 변환 실패 (rc=%d)", sid, convert_result.returncode)
        return {
            "id": sid,
            "state": session.STATE_ERROR,
            "returncode": convert_result.returncode,
            "stderr": convert_result.stderr,
            "error": msg,
        }

    # 변환 성공 — scenario.json 로드해 step 수 정확히 산정
    scenario = storage.load_scenario(sid)
    final_step_count = len(scenario) if scenario else 0

    _registry.update(
        sid,
        state=session.STATE_DONE,
        action_count=final_step_count,
    )
    storage.save_metadata(sid, {
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
        "[/recording/stop] %s — 변환 성공 (%d 스텝, convert_elapsed=%.0fms)",
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
    """codegen 출력 .py 의 페이지 액션 라인 수를 매우 거칠게 추정.

    완벽한 카운팅이 아니라 'action 0' 이 아닌지 정도 검증용.
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


@app.get("/recording/sessions", response_model=list[SessionResp])
def list_sessions() -> list[SessionResp]:
    return [SessionResp(**s.to_dict()) for s in _registry.list()]


@app.get("/recording/sessions/{sid}", response_model=SessionResp)
def get_session(sid: str) -> SessionResp:
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    return SessionResp(**sess.to_dict())


@app.get("/recording/sessions/{sid}/scenario", include_in_schema=False)
def get_session_scenario(sid: str, download: int = 0):
    """세션의 변환된 14-DSL scenario.json 본문을 반환 (TR.4 / TR.4+.2).

    Args:
        download: 1 이면 ``Content-Disposition: attachment`` 로 파일 첨부.
            0 이면 JSON 본문 그대로 (브라우저 표시용 — 기본).

    프론트 UI 가 결과 패널에 DSL 을 표시해 사용자가 assertion 추가 전에
    구조를 검토할 수 있게 한다. state=done 이고 파일이 존재할 때만 200,
    그 외 404.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    p = storage.scenario_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"scenario.json 없음 (state={sess.state})",
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
    """codegen 이 생성한 원본 ``.py`` 본문을 반환 (TR.4+.1).

    Args:
        download: 1 이면 첨부 다운로드, 0 이면 ``text/x-python`` 본문 표시.

    state ∈ {recording 도중 stop 직전 / done / error} 모두에서 동작 — 변환에
    실패해도 사용자가 원본을 검토하여 수동 수정 가능하게 한다.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    p = storage.original_py_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"original.py 없음 (state={sess.state})",
        )
    if download:
        return FileResponse(
            str(p),
            media_type="text/x-python",
            filename=f"{sid}-original.py",
        )
    # 브라우저 표시용 — text/plain 이 안전 (브라우저가 .py 를 다운로드로 처리하는 것 회피).
    return FileResponse(str(p), media_type="text/plain")


# ── P1 (항목 5) — Step 결과 시각화: run_log + 스크린샷 ─────────────────────

# 스크린샷 파일명 화이트리스트 — path traversal 방지 + executor 가 만드는 형식만.
# 예: step_1_pass.png / step_2_healed.png / step_3_fail.png / final_state.png
_SCREENSHOT_NAME_RE = re.compile(r"^(step_\d+_[a-z_]+|final_state)\.png$")


@app.get("/recording/sessions/{sid}/run-log", include_in_schema=False)
def get_session_run_log(sid: str) -> list:
    """Play 실행 후 ``run_log.jsonl`` 을 파싱해 step 별 결과 list 반환.

    각 step 에 ``screenshot`` 필드를 채움 — ``step_<n>_<status>.png`` 이
    디스크에 있을 때만. 모달 확대용 endpoint 는 ``/screenshot/{name}``.

    Run-log 가 없는 세션 (Play 미실행) 은 404.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    p = storage.run_log_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail="run_log.jsonl 없음 — Play with LLM 미실행",
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
                # screenshot 매칭 — executor 의 _screenshot 명명 규칙
                # (step_<n>_pass.png / step_<n>_fail.png / step_<n>_healed.png 등).
                shot_name = None
                if step_no is not None and status:
                    candidate = f"step_{step_no}_{status}.png"
                    if (sess_dir / candidate).is_file():
                        shot_name = candidate
                rec["screenshot"] = shot_name
                out.append(rec)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"run_log 읽기 실패: {e}"
        ) from e
    return out


@app.get("/recording/sessions/{sid}/play-log/tail", include_in_schema=False)
def get_play_log_tail(
    sid: str,
    kind: str = "llm",
    from_: int = Query(0, alias="from"),
):
    """Play 실행 중 ``play-llm.log`` / ``play-codegen.log`` 의 ``from`` 바이트
    이후 내용을 반환 (P2 — 진행 스트리밍).

    Frontend 가 1s 간격으로 polling 하면서 진행 상황을 실시간 표시. 파일이
    아직 없으면 (subprocess 가 막 시작) 200 + ``exists=false`` — 404 가 아닌
    이유는 polling 클라이언트가 정상 흐름에서 파일 생성 전을 만나기 때문.

    Args:
        kind: ``llm`` (default) 또는 ``codegen``.
        from: 이전 polling 에서 받은 ``offset``. 처음은 0.
    """
    if kind not in ("llm", "codegen"):
        raise HTTPException(
            status_code=400, detail=f"kind 는 llm/codegen — received {kind!r}",
        )
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
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
        raise HTTPException(status_code=500, detail=f"log tail 실패: {e}") from e


@app.get("/recording/sessions/{sid}/screenshot/{name}", include_in_schema=False)
def get_session_screenshot(sid: str, name: str):
    """세션 디렉토리의 스크린샷 PNG 를 직접 반환.

    Path traversal 방지 — ``name`` 은 ``step_<digit>_<word>.png`` 또는
    ``final_state.png`` 정규식 화이트리스트로 강제. 그 외 400.
    """
    if not _SCREENSHOT_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않는 스크린샷 이름: {name!r}",
        )
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    p = storage.session_dir(sid) / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"스크린샷 없음: {name}")
    return FileResponse(str(p), media_type="image/png")


# ── 항목 4 — LLM healed regression .py 다운로드 ───────────────────────────

@app.get("/recording/sessions/{sid}/regression", include_in_schema=False)
def get_session_regression(sid: str, download: int = 0):
    """executor 가 자동 생성한 ``regression_test.py`` 본문 또는 첨부 다운로드.

    Play with LLM 실행 후에만 존재. 사용자가 codegen 원본과 diff 검토 후
    회귀 테스트로 채택할 때 이 endpoint 로 다운로드.

    Args:
        download: 1 이면 ``Content-Disposition: attachment``, 0 이면 본문.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    p = storage.regression_py_path(sid)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail="regression_test.py 없음 — Play with LLM 미실행",
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
    """세션 메모리 + 영속화 디렉토리 삭제. 활성 codegen 이 있으면 먼저 종료."""
    if not _registry.get(sid):
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")

    # 활성 codegen 이 남아 있으면 SIGTERM
    handle = _pop_handle(sid)
    if handle is not None:
        log.info("[/recording/sessions] %s 활성 codegen 종료 후 삭제", sid)
        _stop_codegen_impl(handle)

    _registry.delete(sid)
    storage.delete_session(sid)
    log.info("[/recording/sessions] 세션 %s 삭제", sid)
    return None


# ── /recording/sessions/{id}/assertion (TR.4 — codegen 미생성 액션 보충) ───

# verify / mock_* 외에 codegen 이 기록 안 하는 scroll / hover 도 같은 폼으로
# 추가 가능. lazy-render / Intersection Observer / GNB hover-only 메뉴 같이
# 사용자가 명시 의도를 가진 행동을 시나리오에 보충.
ASSERTION_ALLOWED_ACTIONS = {
    "verify", "mock_status", "mock_data", "scroll", "hover",
}
# value 비어도 되는 action — DSL 의 _VALUE_REQUIRED_ACTIONS 와 정합 (hover 만).
_ASSERTION_VALUE_OPTIONAL = {"hover"}
# scroll value 화이트리스트 — zero_touch_qa.__main__._SCROLL_VALID_VALUES 와 동일.
_ASSERTION_SCROLL_VALUES = {"into_view", "into-view", "into view"}


class AssertionAddReq(BaseModel):
    action: str = Field(
        ...,
        description="verify / mock_status / mock_data / scroll / hover 중 하나. codegen 이 emit 하지 않는 14-DSL 액션을 사용자가 수동 추가.",
    )
    target: str = Field(..., description="CSS 셀렉터 또는 URL 패턴")
    value: str = Field("", description="기대값 / status code / JSON body / scroll mode (hover 는 빈값 허용)")
    description: str = ""
    condition: Optional[str] = Field(
        None, description="verify 의 조건 (예: text / visible / url)",
    )


@app.post("/recording/sessions/{sid}/assertion", status_code=201)
def add_assertion(sid: str, req: AssertionAddReq) -> dict:
    """녹화 후 사용자가 codegen 미생성 step (verify/mock_*/scroll/hover) 을 수동 추가.

    PLAN §"TR.4 Assertion 추가 영역" 의 비대칭 보완. codegen 은 page.route /
    expect / scroll / hover 를 emit 하지 않으므로 운영자가 직접 입력해 14-DSL
    풀 시나리오로 완성한다.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Step 추가는 변환 완료(state=done) 세션만 가능합니다. "
                f"현재 state={sess.state}"
            ),
        )
    if req.action not in ASSERTION_ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"action 은 {sorted(ASSERTION_ALLOWED_ACTIONS)} 중 하나여야 합니다. "
                f"받은 값: {req.action!r}"
            ),
        )
    if not req.target.strip():
        raise HTTPException(status_code=400, detail="target 이 비어있습니다.")
    if req.action not in _ASSERTION_VALUE_OPTIONAL and not req.value.strip():
        raise HTTPException(status_code=400, detail="value 가 비어있습니다.")
    if req.action == "scroll":
        scroll_v = req.value.strip().lower()
        if scroll_v not in _ASSERTION_SCROLL_VALUES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"scroll 의 value 는 {sorted(_ASSERTION_SCROLL_VALUES)} "
                    f"중 하나여야 합니다. 받은 값: {req.value!r}"
                ),
            )

    scenario = storage.load_scenario(sid)
    if scenario is None:
        raise HTTPException(
            status_code=409,
            detail=f"세션 {sid} 의 scenario.json 이 없습니다. 먼저 변환을 완료하세요.",
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

    # 호스트 측 가벼운 sanity 만 — 깊은 _validate_scenario 는 다음 변환/실행 시점에.
    import json as _json
    storage.scenario_path(sid).write_text(
        _json.dumps(scenario, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _registry.update(sid, action_count=len(scenario))

    log.info(
        "[/assertion] %s — step %d 추가 (action=%s target=%s)",
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


# ── 진단/내부용 (테스트 친화) ────────────────────────────────────────────────

def _reset_for_tests() -> None:
    """pytest fixture 용. 운영 코드에서 호출 금지."""
    _registry.clear()
    with _handles_lock:
        _handles.clear()


# ── 디스크 세션 흡수 (TR.8 영속화) ──────────────────────────────────────────

@app.on_event("startup")
def _absorb_disk_sessions() -> None:
    """server 재시작 시 호스트 영속화 루트의 세션을 in-memory 레지스트리로 복원.

    상태는 metadata.json 의 마지막 state 그대로. 활성 codegen 핸들은 복원하지
    않음 (subprocess 가 server 와 함께 죽었으므로). state=recording 이었던
    세션은 'orphan' 으로 마킹해 사용자에게 명시.
    """
    import time as _time
    try:
        ids = storage.list_session_dirs()
    except Exception as e:  # noqa: BLE001
        log.warning("[startup] 디스크 세션 흡수 실패: %s", e)
        return

    absorbed = 0
    for sid in ids:
        if _registry.get(sid) is not None:
            continue  # 테스트 등에서 이미 있음
        meta = storage.load_metadata(sid) or {}
        target_url = meta.get("target_url", "")
        state = meta.get("state", session.STATE_DONE)
        if state == session.STATE_RECORDING:
            # codegen 이 이미 죽었음 — orphan 으로 마킹
            state = session.STATE_ERROR
            error_msg = "server 재시작으로 codegen subprocess 가 끊겼습니다 (orphan)."
        else:
            error_msg = meta.get("error")

        sess = _registry.create(target_url=target_url)
        # uuid4 가 새로 생성됐으므로 id 를 디스크 sid 로 강제 교체
        with _registry._lock:
            del _registry._sessions[sess.id]
            sess.id = sid
            sess.state = state
            sess.created_at = meta.get("created_at_ts", _time.time())
            if error_msg:
                sess.error = error_msg
            sess.action_count = meta.get("step_count", meta.get("action_count_estimate", 0))
            _registry._sessions[sid] = sess
        absorbed += 1

    if absorbed:
        log.info("[startup] 디스크 세션 %d개 흡수 (host_root=%s)",
                 absorbed, storage.host_root())


# ── 정적 파일 / Web UI (TR.4) ────────────────────────────────────────────────

_WEB_DIR = _Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
def root_index() -> FileResponse:
    """`/` 진입 시 index.html 반환 (TR.4 Web UI)."""
    index_path = _WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "Web UI 정적 파일을 찾을 수 없습니다 — recording_service/web/index.html 누락."
            ),
        )
    return FileResponse(str(index_path))


# /static/* 로 app.js / style.css 등 서빙. /healthz 와 /recording/* API 와 분리.
if _WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


# R-Plus router — `/experimental/sessions/{sid}/replay|enrich|compare`.
# 사용자 요구로 게이트 폐기 (TR.4+.4) — 항상 활성. URL prefix `/experimental/` 는
# 코드 조직상 의미 보존 (replay/enrich/compare 가 R-MVP 와 별개 트랙임을 명시).
from .rplus.router import router as _rplus_router  # noqa: E402

app.include_router(_rplus_router)
