"""Recording 서비스 FastAPI 엔트리포인트 (Phase R-MVP TR.1).

기동:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

엔드포인트 표는 PLAN_GROUNDING_RECORDING_AGENT.md §"TR.1" 참조.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from . import codegen_runner, session, storage

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
    """새 녹화 세션 생성 (TR.1 단계는 codegen 미실행 stub).

    TR.2 에서 실 subprocess 시작·codegen_runner.start_codegen 호출 추가.
    """
    sess = _registry.create(
        target_url=req.target_url,
        planning_doc_ref=req.planning_doc_ref,
    )
    # 영속화 디렉토리 사전 생성 + 메타데이터 초기 저장
    storage.save_metadata(sess.id, {
        "id": sess.id,
        "target_url": sess.target_url,
        "planning_doc_ref": sess.planning_doc_ref,
        "created_at": storage.now_iso(),
        "state": sess.state,
    })

    output_path = storage.original_py_path(sess.id)
    log.info(
        "[/recording/start] 세션 %s 생성 (target=%s, output=%s)",
        sess.id, sess.target_url, output_path,
    )
    return RecordingStartResp(
        id=sess.id,
        state=sess.state,
        target_url=sess.target_url,
        output_path=str(output_path),
    )


@app.post("/recording/stop/{sid}", status_code=202)
def recording_stop(sid: str) -> dict:
    """녹화 종료 + 변환 (TR.1 단계는 stub — 상태 전환만).

    TR.2/TR.3 에서 codegen SIGTERM → docker exec --convert-only 추가.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    _registry.update(sid, state=session.STATE_DONE, ended_at_iso=None)
    log.info("[/recording/stop] 세션 %s — TR.1 stub (실 변환은 TR.2/3 에서)", sid)
    return {"id": sid, "state": session.STATE_DONE, "note": "TR.1 stub"}


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
    """세션 메모리 + 영속화 디렉토리 삭제."""
    if not _registry.delete(sid):
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
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
