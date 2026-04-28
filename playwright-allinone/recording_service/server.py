"""Recording 서비스 FastAPI 엔트리포인트 (Phase R-MVP TR.1/TR.2).

기동:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

엔드포인트 표는 PLAN_GROUNDING_RECORDING_AGENT.md §"TR.1" 참조.
TR.2 단계에서 /start /stop 이 실 codegen subprocess 와 연동된다.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from . import codegen_runner, converter_proxy, session, storage
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
    """서비스 헬스체크. codegen 가용 여부도 함께 반환."""
    return HealthResp(
        ok=True,
        version=__version__,
        codegen_available=codegen_runner.is_codegen_available(),
        host_root=str(storage.host_root()),
    )


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


@app.post("/recording/sessions/{sid}/replay")
def replay_session(sid: str):
    """R-Plus only — TR.7 에서 활성화. R-MVP 단계는 503 반환."""
    raise HTTPException(
        status_code=503,
        detail="Replay 는 R-Plus 트랙 (TR.7) 입니다. R-MVP 단계에서는 비활성화.",
    )


# ── 진단/내부용 (테스트 친화) ────────────────────────────────────────────────

def _reset_for_tests() -> None:
    """pytest fixture 용. 운영 코드에서 호출 금지."""
    _registry.clear()
    with _handles_lock:
        _handles.clear()
