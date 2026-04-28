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

from pathlib import Path as _Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import (
    codegen_runner, comparator, converter_proxy, enricher,
    replay_proxy, session, storage,
)
from .codegen_runner import CodegenError, CodegenHandle
from .converter_proxy import ConverterProxyError
from .enricher import EnrichError, EnrichResult
from .replay_proxy import ReplayProxyError

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


def _run_enrich_impl(
    *, scenario: list[dict], target_url: str, page_title=None, inventory_block=None,
):
    """TR.5 monkeypatch hook — 단위 테스트가 Ollama 호출 없이 fake 결과 반환."""
    return enricher.enrich_recording(
        scenario=scenario,
        target_url=target_url,
        page_title=page_title,
        inventory_block=inventory_block,
    )


def _run_replay_impl(
    *, container_session_dir: str, host_session_dir: str,
):
    """TR.7 monkeypatch hook — fake docker exec 결과."""
    return replay_proxy.run_replay(
        container_session_dir=container_session_dir,
        host_session_dir=host_session_dir,
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


@app.post("/recording/sessions/{sid}/replay", status_code=201)
def replay_session(sid: str) -> dict:
    """녹화된 14-DSL 을 컨테이너 측 executor 로 재실행 (TR.7 R-Plus).

    검증된 변환 결과(scenario.json) 가 실 브라우저 환경에서도 동작하는지
    round-trip 으로 확인. 결과는 `<host_root>/<sid>/run_log.json` 에 저장.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"Replay 는 변환 완료(state=done) 세션만 가능합니다. 현재 state={sess.state}",
        )

    scenario = storage.load_scenario(sid)
    if not scenario:
        raise HTTPException(
            status_code=409,
            detail=f"세션 {sid} 의 scenario.json 이 누락됨.",
        )

    container_dir = storage.container_path_for(sid)
    host_dir = str(storage.session_dir(sid))

    try:
        result = _run_replay_impl(
            container_session_dir=container_dir,
            host_session_dir=host_dir,
        )
    except ReplayProxyError as e:
        log.error("[/replay] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))

    log.info(
        "[/replay] %s — rc=%d pass=%d fail=%d healed=%d (%.0fms)",
        sid, result.returncode, result.pass_count, result.fail_count,
        result.healed_count, result.elapsed_ms,
    )
    return {
        "id": sid,
        "returncode": result.returncode,
        "pass_count": result.pass_count,
        "fail_count": result.fail_count,
        "healed_count": result.healed_count,
        "step_count": len(scenario),
        "run_log_exists": result.run_log_exists,
        "run_log_path": result.run_log_path,
        "elapsed_ms": result.elapsed_ms,
        "stderr_tail": result.stderr[-500:] if result.stderr else "",
    }


# ── /recording/sessions/{id}/assertion (TR.4 — codegen 미생성 액션 보충) ───

ASSERTION_ALLOWED_ACTIONS = {"verify", "mock_status", "mock_data"}


class AssertionAddReq(BaseModel):
    action: str = Field(
        ...,
        description="verify / mock_status / mock_data 중 하나. codegen 이 emit 하지 않는 14-DSL 액션을 사용자가 수동 추가.",
    )
    target: str = Field(..., description="CSS 셀렉터 또는 URL 패턴")
    value: str = Field(..., description="기대값 / status code / JSON body")
    description: str = ""
    condition: Optional[str] = Field(
        None, description="verify 의 조건 (예: text / visible / url)",
    )


@app.post("/recording/sessions/{sid}/assertion", status_code=201)
def add_assertion(sid: str, req: AssertionAddReq) -> dict:
    """녹화 후 사용자가 verify / mock_status / mock_data step 을 수동 추가.

    PLAN §"TR.4 Assertion 추가 영역" 의 비대칭 보완. codegen 은 page.route /
    expect 를 emit 하지 않으므로 운영자가 직접 입력해 14-DSL 풀 시나리오로
    완성한다.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Assertion 추가는 변환 완료(state=done) 세션만 가능합니다. "
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
    if not req.value.strip():
        raise HTTPException(status_code=400, detail="value 가 비어있습니다.")

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
    return ""


# ── /recording/sessions/{id}/enrich (TR.5 R-Plus — Recording → Doc 역추정) ──

class EnrichReq(BaseModel):
    page_title: Optional[str] = Field(
        None, description="페이지 타이틀 (있으면 컨텍스트 강화)",
    )
    inventory_block: Optional[str] = Field(
        None,
        description="Phase 1 grounding 인벤토리 마커 블록 (선택). srs_text prepend 패턴과 동일.",
    )


@app.post("/recording/sessions/{sid}/enrich", status_code=201)
def enrich_session(sid: str, req: EnrichReq) -> dict:
    """녹화된 시나리오를 IEEE 829-lite Markdown 으로 역추정 (TR.5 R-Plus).

    출력은 `<host_root>/<sid>/doc_enriched.md` 에 영속화. UI 가 응답의
    markdown 을 즉시 미리보기로 표시.
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"역추정은 변환 완료(state=done) 세션만 가능합니다. 현재 state={sess.state}",
        )

    scenario = storage.load_scenario(sid)
    if not scenario:
        raise HTTPException(
            status_code=409,
            detail=f"세션 {sid} 의 scenario.json 이 비어있거나 누락됨.",
        )

    try:
        result: EnrichResult = _run_enrich_impl(
            scenario=scenario,
            target_url=sess.target_url,
            page_title=req.page_title,
            inventory_block=req.inventory_block,
        )
    except EnrichError as e:
        log.error("[/enrich] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))

    enriched_path = storage.session_dir(sid) / "doc_enriched.md"
    enriched_path.write_text(result.markdown, encoding="utf-8")

    log.info(
        "[/enrich] %s — %d chars (%s, %.0fms)",
        sid, len(result.markdown), result.model, result.elapsed_ms,
    )
    return {
        "id": sid,
        "model": result.model,
        "markdown": result.markdown,
        "char_count": len(result.markdown),
        "prompt_tokens_estimate": result.prompt_tokens_estimate,
        "elapsed_ms": result.elapsed_ms,
        "saved_to": str(enriched_path),
    }


# ── /recording/sessions/{id}/compare (TR.6 R-Plus — Doc ↔ Recording 비교) ───

class CompareReq(BaseModel):
    doc_dsl: list[dict] = Field(
        ...,
        description="비교 대상 doc-DSL (chat 모드 출력 또는 손작성). 14-DSL 리스트.",
    )
    threshold: float = Field(
        comparator.DEFAULT_FUZZY_THRESHOLD,
        description="fuzzy 매칭 임계값 (0~1). 기본 0.7.",
    )
    doc_label: str = Field("doc-DSL", description="HTML 리포트 컬럼 라벨")
    rec_label: str = Field("recording-DSL", description="HTML 리포트 컬럼 라벨")


@app.post("/recording/sessions/{sid}/compare", status_code=201)
def compare_session(sid: str, req: CompareReq) -> dict:
    """녹화된 14-DSL 과 사용자가 제공한 doc-DSL 을 5분류로 비교 (TR.6 R-Plus).

    - 정렬 대상 액션은 LCS 정렬
    - doc 의 verify/mock_* 는 codegen 비대칭으로 인한 'intent_only' 분류
    - HTML 리포트는 `<host_root>/<sid>/doc_comparison.html` 에 영속화
    """
    sess = _registry.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"비교는 변환 완료(state=done) 세션만 가능합니다. 현재 state={sess.state}",
        )
    if not req.doc_dsl:
        raise HTTPException(status_code=400, detail="doc_dsl 이 비어있습니다.")

    rec_dsl = storage.load_scenario(sid)
    if not rec_dsl:
        raise HTTPException(
            status_code=409,
            detail=f"세션 {sid} 의 scenario.json 이 누락됨.",
        )

    if not (0.0 <= req.threshold <= 1.0):
        raise HTTPException(status_code=400, detail="threshold 는 0.0~1.0 범위.")

    result = comparator.compare(req.doc_dsl, rec_dsl, threshold=req.threshold)
    html = comparator.render_html(result, doc_label=req.doc_label, rec_label=req.rec_label)

    out_path = storage.session_dir(sid) / "doc_comparison.html"
    out_path.write_text(html, encoding="utf-8")

    log.info(
        "[/compare] %s — counts=%s, doc=%d steps, rec=%d steps",
        sid, result.counts, len(req.doc_dsl), len(rec_dsl),
    )
    return {
        "id": sid,
        "counts": result.counts,
        "threshold_used": result.threshold_used,
        "doc_step_count": len(req.doc_dsl),
        "rec_step_count": len(rec_dsl),
        "saved_to": str(out_path),
        "report_html_url": f"/recording/sessions/{sid}/comparison.html",
    }


@app.get("/recording/sessions/{sid}/comparison.html", response_class=FileResponse, include_in_schema=False)
def get_comparison_html(sid: str) -> FileResponse:
    """compare 결과 HTML 리포트를 직접 서빙 (UI 의 새 탭 진입 용)."""
    p = storage.session_dir(sid) / "doc_comparison.html"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="비교 리포트 미생성")
    return FileResponse(str(p), media_type="text/html")


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
