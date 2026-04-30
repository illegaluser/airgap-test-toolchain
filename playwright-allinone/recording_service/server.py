"""Recording 서비스 FastAPI 엔트리포인트 (Phase R-MVP TR.1/TR.2).

기동:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

엔드포인트 표는 docs/PLAN_GROUNDING_RECORDING_AGENT.md §"TR.1" 참조.
TR.2 단계에서 /start /stop 이 실 codegen subprocess 와 연동된다.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal, Optional

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
def _start_codegen_impl(
    target_url: str,
    output_path,
    *,
    timeout_sec: int,
    extra_args: Optional[list[str]] = None,
) -> CodegenHandle:
    """codegen 시작 hook. ``extra_args`` 는 ``playwright codegen`` 에 그대로 전달
    (예: ``--load-storage <path>`` + fingerprint 옵션 — P3.7).
    """
    return codegen_runner.start_codegen(
        target_url, output_path, timeout_sec=timeout_sec, extra_args=extra_args,
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


def _save_metadata_preserving_auth(sid: str, new_meta: dict) -> None:
    """``recording_stop`` 의 metadata 갱신이 ``auth_profile`` 키를 잃지 않도록 보존.

    post-review fix — ``save_metadata`` 가 *전체 덮어쓰기* 라서 stop 의 done/error
    분기들이 start 시점에 박은 auth_profile 을 silent-drop 했음. 결과: 재생 시
    ``_resolve_auth_for_replay`` 가 메타에서 auth_profile 을 못 찾아 verify 게이트
    스킵 → 만료 감지 안 됨.

    호출자가 새 메타 dict 를 넘기면, 기존 메타에서 auth_profile 만 lift 해 머지.
    """
    existing = storage.load_metadata(sid) or {}
    auth = existing.get("auth_profile")
    if auth and "auth_profile" not in new_meta:
        new_meta = dict(new_meta)
        new_meta["auth_profile"] = auth
    storage.save_metadata(sid, new_meta)


# ── auth-profile 통합 헬퍼 (P3.7) ────────────────────────────────────────

def _load_profile_for_browser(
    profile_name: Optional[str],
) -> tuple[Optional[_Path], object, bool]:
    """auth_profile 이름 → (storage_path, fingerprint, machine_mismatch).

    codegen extra args 가 필요 없는 호출자(예: discover 워커)가 storageState
    파일 경로 + fingerprint 객체만 받기 위한 공통 헬퍼. 기존
    ``_resolve_auth_profile_extras`` 도 이 헬퍼를 거쳐 extra_args 를 조립한다.

    Returns:
        (storage_path, fingerprint, machine_mismatch). profile 이 None 이면
        ``(None, None, False)``. 두 번째 원소는 ``FingerprintProfile`` 인스턴스.

    Raises:
        HTTPException(404): 프로파일 미발견.
        HTTPException(409): verify 실패 (만료) — UI 가 재시드 모달로 분기.
        HTTPException(503): CHIPS 미지원 등 환경 문제.
    """
    if not profile_name:
        return None, None, False
    # auth_profiles 는 fcntl 등 POSIX 의존성이 있어 lazy import.
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

    # 머신 결속 (D11) — 차단 안 함, 헤더로만 알림. 결정 책임은 본 헬퍼 한 곳.
    machine_mismatch = (prof.host_machine_id != auth_profiles.current_machine_id())

    return prof.storage_path, prof.fingerprint, machine_mismatch


def _resolve_auth_profile_extras(
    profile_name: Optional[str],
) -> tuple[Optional[list[str]], bool]:
    """auth_profile 이름 → codegen extra_args + machine_mismatch 플래그.

    Returns:
        (extra_args, machine_mismatch). extra_args 는 ``--load-storage <path> +
        fingerprint 옵션``. profile 이 None 이면 ``(None, False)``.

    Raises:
        HTTPException(404): 프로파일 미발견.
        HTTPException(409): verify 실패 (만료) — UI 가 재시드 모달로 분기.
        HTTPException(503): CHIPS 미지원 등 환경 문제.
    """
    storage_path, fingerprint, machine_mismatch = _load_profile_for_browser(profile_name)
    if storage_path is None:
        return None, False

    extra_args: list[str] = ["--load-storage", str(storage_path)]
    extra_args += fingerprint.to_playwright_open_args()  # type: ignore[union-attr]
    return extra_args, machine_mismatch




# ── 요청/응답 모델 ────────────────────────────────────────────────────────────

class RecordingStartReq(BaseModel):
    target_url: str = Field(..., description="녹화 시작 시 codegen 이 로드할 URL")
    planning_doc_ref: Optional[str] = Field(
        None,
        description="기획서 참조 (Phase R-Plus 시나리오 A 에서 사용. R-MVP 는 메타데이터만)",
    )
    auth_profile: Optional[str] = Field(
        None,
        description=(
            "(P3.7) auth_profiles 카탈로그의 프로파일 이름. 지정 시 codegen 시작 전 "
            "verify_profile 통과 강제 + storage_state + fingerprint 옵션 자동 주입."
        ),
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
    auth_profile: Optional[str] = None


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
def recording_start(req: RecordingStartReq, response: Response) -> RecordingStartResp:
    """새 녹화 세션 생성 + codegen subprocess 시작 (TR.2).

    P3.7 — auth_profile 지정 시 verify 게이트 + extra_args 자동 주입. 머신 불일치
    검출 시 ``X-Auth-Machine-Mismatch: 1`` 응답 헤더로 UI 에 경고 시그널.

    순서 (post-review fix): auth 검증을 ``registry.create`` *전에* 수행.
    검증 실패 시 orphan pending 세션이 메모리에 남는 회귀를 차단.
    """
    # P3.7 — auth_profile 지정 시 verify 게이트 + extra_args 빌드.
    # 세션 생성 전에 호출 — 검증 실패가 곧 4xx 이므로 orphan 세션이 남지 않게.
    extra_args, machine_mismatch = _resolve_auth_profile_extras(req.auth_profile)

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
            extra_args=extra_args,
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
    # P3.8 — auth_profile 메타는 metadata.json 에만 박힘 (D15: scenario.json 미수정).
    # in-memory session.extras 에도 동일 값 보존 → SessionResp 응답에 노출.
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
        "[/recording/start] 세션 %s — codegen 시작 (PID=%d, output=%s, auth=%s)",
        sess.id, handle.pid, output_path, req.auth_profile or "-",
    )
    # 머신 불일치 경고는 헤더로 — UI 가 모달 표시. FastAPI 가 주입한 Response 에
    # 직접 헤더를 set 하면 응답 모델은 그대로 RecordingStartResp 으로 유지된다.
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

    # P3.10 — codegen 출력 .py 의 storage 절대 경로를 env var 로 치환 (D3).
    # 시드 환경에서 ``--load-storage=<abs>`` 로 시작했으면 codegen 이 출력에
    # ``storage_state="<abs>"`` 를 박아둔다. 그대로 두면 다른 머신에서 재생 불가.
    # ``original.py`` 에 매칭이 없으면 silent no-op (인증 없이 녹화한 세션).
    try:
        post_process.portabilize_storage_path(handle.output_path)
    except Exception as e:  # noqa: BLE001 — 후처리 실패가 stop 흐름을 깨뜨리지 않게.
        log.warning("[/recording/stop] portabilize 실패 (계속 진행) — %s", e)

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
        # 컨테이너 변환 실패 — stderr 그대로 노출 + 원본 .py 보존
        msg = (
            f"변환 실패 (returncode={convert_result.returncode}). "
            "원본 original.py 는 보존됩니다. stderr 일부 — "
            + (convert_result.stderr[:500] if convert_result.stderr else "(stderr 없음)")
        )
        _registry.update(sid, state=session.STATE_ERROR, error=msg)
        _save_metadata_preserving_auth(sid, {
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


def _session_to_resp(s) -> SessionResp:
    """Session → SessionResp. ``extras['auth_profile']`` 를 top-level 로 lift (P5.8)."""
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
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    return _session_to_resp(sess)


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
            # P5.8 — auth_profile 메타 복원 (서버 재시작 시 세션 테이블에 유지).
            if "auth_profile" in meta:
                sess.extras["auth_profile"] = meta["auth_profile"]
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


# ─────────────────────────────────────────────────────────────────────────
# Auth Profile 엔드포인트 (P3.2 ~ P3.6)
# ─────────────────────────────────────────────────────────────────────────
#
# 설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.6
#
# - GET    /auth/profiles                       — 카탈로그 목록 (드롭다운용)
# - POST   /auth/profiles/seed                  — 시드 시작 (background thread)
# - GET    /auth/profiles/seed/{seed_sid}       — 시드 진행 폴링
# - POST   /auth/profiles/{name}/verify         — 명시적 verify
# - DELETE /auth/profiles/{name}                — 삭제

import time as _time_auth  # noqa: E402  (별칭으로 격리 — 동명 변수 회피)
import uuid as _uuid_auth  # noqa: E402

from dataclasses import dataclass as _dataclass_auth, field as _field_auth  # noqa: E402


# ── 시드 진행 트래킹 (P3.3 / P3.4) ──────────────────────────────────────

@_dataclass_auth
class _SeedJob:
    """시드 background thread 의 상태 추적."""
    seed_sid: str
    state: str                          # "running" / "ready" / "error"
    started_at: float
    timeout_sec: int
    phase: str = "starting"             # "starting" / "login_waiting" / "verifying" / "ready" / "error"
    message: str = "시드 시작 중"
    profile_name: Optional[str] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None    # 'timeout' / 'subprocess' / 'validate' / 'verify' / 'unknown'


_seed_jobs: dict[str, _SeedJob] = {}
_seed_jobs_lock = threading.Lock()


class AuthProfileSummary(BaseModel):
    """드롭다운/리스트용 간략 정보."""
    name: str
    service_domain: str
    last_verified_at: Optional[str]
    ttl_hint_hours: int
    chips_supported: bool
    session_storage_warning: bool


class AuthProfileDetail(AuthProfileSummary):
    """단일 프로파일 detail — 만료 모달의 [재시드] prefill 에 사용 (P2.1).

    Summary + verify spec (service_url / service_text / naver_probe 활성 여부).
    seed_url 은 카탈로그에 저장 안 되므로 클라이언트가 verify_service_url 의
    origin 으로 추정. 사용자가 수정 가능.
    """
    verify_service_url: str
    verify_service_text: str
    naver_probe_enabled: bool


class AuthSeedReq(BaseModel):
    name: str
    seed_url: str = Field(
        ..., description="⚠️ 테스트 대상 *서비스* 진입 URL (네이버 로그인 URL 이 아님)",
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
    """background thread — auth_profiles.seed_profile 호출 + 상태 업데이트."""
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
            job.message = f"시드 완료 — 프로파일 '{prof.name}' 이 저장되었습니다."
            job.profile_name = prof.name
        log.info("[/auth/profiles/seed] 완료 — seed_sid=%s name=%s", job.seed_sid, prof.name)
    except AuthProfileError as e:
        kind = _AUTH_ERROR_KIND_MAP.get(type(e).__name__, "auth_error")
        with _seed_jobs_lock:
            job.state = "error"
            job.phase = "error"
            job.message = f"시드 실패 — {e}"
            job.error = str(e)
            job.error_kind = kind
        log.warning(
            "[/auth/profiles/seed] 실패 (%s) — seed_sid=%s err=%s",
            kind, job.seed_sid, e,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("[/auth/profiles/seed] worker 예기치 못한 예외")
        with _seed_jobs_lock:
            job.state = "error"
            job.phase = "error"
            job.message = f"시드 실패 — {e!r}"
            job.error = repr(e)
            job.error_kind = "unknown"


@app.get("/auth/profiles", response_model=list[AuthProfileSummary])
def auth_profiles_list() -> list[AuthProfileSummary]:
    """등록된 auth-profile 목록 (드롭다운용)."""
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
    """단일 프로파일 detail — 만료 모달의 [재시드] prefill 용 (P2.1).

    Summary + verify spec 노출. UI 가 시드 모달 prefill 시 사용.
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
    """시드 시작 — background thread 에서 ``playwright open --save-storage`` 실행.

    Returns immediately with a ``seed_sid`` for ``GET /auth/profiles/seed/{seed_sid}``
    polling. 사용자가 별도 창에서 로그인 + 2중 확인을 통과하고 창을 닫으면 thread
    가 verify 까지 마치고 ``state=ready`` 로 전환.
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
        "[/auth/profiles/seed] 시작 — seed_sid=%s name=%s seed_url=%s",
        seed_sid, req.name, req.seed_url,
    )
    return AuthSeedStartResp(seed_sid=seed_sid, state=job.state)


@app.get("/auth/profiles/seed/{seed_sid}", response_model=AuthSeedPollResp)
def auth_profiles_seed_poll(seed_sid: str) -> AuthSeedPollResp:
    """시드 진행 상태 폴링."""
    with _seed_jobs_lock:
        job = _seed_jobs.get(seed_sid)
    if job is None:
        raise HTTPException(status_code=404, detail=f"seed job 미발견: {seed_sid}")
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
    """명시적 verify — UI 의 ``↻ verify`` 버튼이 호출."""
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
    """auth-profile 삭제 (카탈로그 + storage 파일)."""
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


# ─────────────────────────────────────────────────────────────────────────
# Discover URLs (URL 자동 수집) — docs/PLAN_URL_DISCOVERY.md 참조
# ─────────────────────────────────────────────────────────────────────────

# 1차 정책: 동시 실행 상한 2. 운영 사이트 부하 + 로컬 Chromium 프로세스 수 가드.
DISCOVER_MAX_CONCURRENT = 2


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiscoverReq(BaseModel):
    seed_url: str = Field(..., description="크롤 시작 URL")
    auth_profile: Optional[str] = None
    max_pages: int = Field(200, ge=1, le=2000)
    max_depth: int = Field(3, ge=0, le=10)
    # 커버리지 보강 옵션 (PLAN_URL_DISCOVERY_COVERAGE.md)
    use_sitemap: bool = True
    capture_requests: bool = True
    spa_selectors: bool = False
    ignore_query: bool = False
    include_subdomains: bool = False


class TourScriptReq(BaseModel):
    # NOTE: 일부러 list[str]. Pydantic v2 HttpUrl 은 끝슬래시/host case 등을
    # 정규화해 urls.json 의 원본 문자열과 매칭이 깨진다. 검증은
    # url_discovery.normalize_url() 로 양쪽을 정규화한 set 비교로 수행.
    urls: list[str] = Field(..., min_length=1, max_length=500)
    auth_profile: Optional[str] = None
    headless: bool = True
    preflight_verify: bool = True
    wait_until: Literal["domcontentloaded", "load", "networkidle"] = "domcontentloaded"
    nav_timeout_ms: int = Field(15000, ge=1000, le=120000)


class DiscoverJob(BaseModel):
    job_id: str
    state: Literal["running", "cancelling", "done", "failed", "cancelled"]
    seed_url: str
    auth_profile: Optional[str] = None
    machine_mismatch: bool = False
    started_at: str
    finished_at: Optional[str] = None
    count: int = 0
    last_url: Optional[str] = None
    result_dir: Optional[str] = None
    error: Optional[str] = None
    aborted_reason: Optional[str] = None  # "auth_drift" 등


_discover_jobs: dict[str, DiscoverJob] = {}
_discover_cancel_events: dict[str, threading.Event] = {}
_discover_lock = threading.Lock()


def _discover_worker(
    job_id: str,
    storage_path: Optional[_Path],
    fingerprint: object,
    max_pages: int,
    max_depth: int,
    cancel_event: threading.Event,
    *,
    use_sitemap: bool = True,
    capture_requests: bool = True,
    spa_selectors: bool = False,
    ignore_query: bool = False,
    include_subdomains: bool = False,
) -> None:
    """discover 백그라운드 워커. Playwright sync API 를 thread 에서 직접 호출."""
    from zero_touch_qa.url_discovery import DiscoverConfig, discover_urls

    with _discover_lock:
        job = _discover_jobs[job_id]

    out_dir = storage.discoveries_root() / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        fp_kwargs = fingerprint.to_browser_context_kwargs() if fingerprint else {}
        cfg = DiscoverConfig(
            seed_url=job.seed_url,
            storage_state_path=storage_path,
            fingerprint_kwargs=fp_kwargs,
            max_pages=max_pages,
            max_depth=max_depth,
            use_sitemap=use_sitemap,
            capture_requests=capture_requests,
            spa_selectors=spa_selectors,
            ignore_query=ignore_query,
            include_subdomains=include_subdomains,
        )

        def _progress(count: int, last_url: str) -> None:
            with _discover_lock:
                j = _discover_jobs.get(job_id)
                if j is not None:
                    j.count = count
                    j.last_url = last_url

        results, abort_reason = discover_urls(
            cfg, on_progress=_progress, cancel_event=cancel_event
        )

        finished_at = _now_iso_utc()
        cancelled_by_user = cancel_event.is_set()
        meta = {
            "seed_url": job.seed_url,
            "auth_profile": job.auth_profile,
            "machine_mismatch": job.machine_mismatch,
            "max_pages": max_pages,
            "max_depth": max_depth,
            "started_at": job.started_at,
            "finished_at": finished_at,
            "count": len(results),
            "aborted_reason": abort_reason,
            "cancelled_by_user": cancelled_by_user,
            "options": {
                "use_sitemap": use_sitemap,
                "capture_requests": capture_requests,
                "spa_selectors": spa_selectors,
                "ignore_query": ignore_query,
                "include_subdomains": include_subdomains,
            },
        }
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "urls.json").write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # utf-8-sig: Excel 호환 BOM (한국어 사용자 다수).
        with (out_dir / "urls.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["url", "status", "title", "depth", "source", "found_at"],
            )
            writer.writeheader()
            for r in results:
                writer.writerow(asdict(r))

        with _discover_lock:
            j = _discover_jobs.get(job_id)
            if j is not None:
                j.state = "cancelled" if cancelled_by_user else "done"
                j.aborted_reason = abort_reason
                j.finished_at = finished_at
                j.count = len(results)
                j.result_dir = str(out_dir)
    except Exception as e:  # noqa: BLE001 — worker 최상위 가드
        log.exception("[discover] job=%s 워커 실패", job_id)
        with _discover_lock:
            j = _discover_jobs.get(job_id)
            if j is not None:
                j.state = "failed"
                j.error = repr(e)
                j.finished_at = _now_iso_utc()
    finally:
        with _discover_lock:
            _discover_cancel_events.pop(job_id, None)


from string import Template as _StrTemplate

_TOUR_SCRIPT_TEMPLATE = _StrTemplate('''"""Auto-generated tour script — pytest 기반 회귀 검증.

선택한 URL 들이 *정상 화면을 노출하는지* 자동 확인한다. URL 별로 4가지를 본다:
  1. navigation 자체 성공 (예외 없음)
  2. HTTP status < 400
  3. 최종 URL host 가 seed host 와 같은 도메인 계열 (로그인 페이지로 빠지지 않음)
  4. <title> 비어있지 않음 + body 텍스트 길이 >= MIN_BODY_TEXT_LEN

실행 (Recording UI venv 안에서):
    pytest tour_selected.py -v
    pytest tour_selected.py -v -k "/user/co"               # 특정 URL 서브셋
    TOUR_HEADLESS=0 python tour_selected.py                # 브라우저 창 띄움
    TOUR_HEADLESS=1 python tour_selected.py                # 헤드리스 강제 (CI 등)
    TOUR_SCREENSHOTS_FAILED_ONLY=1 pytest tour_selected.py # 실패한 URL 만 PNG (기본: 전체)
    python tour_selected.py                                # Recording UI 'Play Script from File' 호환

결과 (스크립트와 같은 디렉토리에 생성):
    tour_results.jsonl   — URL 별 결과 (status, title, body_len, ok, error, screenshot 경로)
    tour_screenshots/    — 모든 URL PNG (full_page=True). 옵션으로 실패만 저장 가능.

의존성: pytest, playwright (Recording UI venv 에 이미 설치).
같은 머신/사용자 환경 실행 전제 — STORAGE_STATE 절대경로가 박혀 있다.
공유 시 auth_profile 없이 재생성 권장.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright


# ── 설정 (서버에서 박힘) ──────────────────────────────────────────────────
URLS = $urls_block
SEED_HOST = $seed_host_literal
OUT_DIR = Path(__file__).resolve().parent
RESULTS = OUT_DIR / "tour_results.jsonl"
SCREENSHOT_DIR = OUT_DIR / "tour_screenshots"
STORAGE_STATE = $storage_state_literal    # auth_profile 없으면 None
CONTEXT_KWARGS_JSON = $context_kwargs_json_literal
HEADLESS = $headless_literal
PREFLIGHT_VERIFY = $preflight_verify_literal
VERIFY_SERVICE_URL = $verify_url_literal
VERIFY_SERVICE_TEXT = $verify_text_literal
WAIT_UNTIL = $wait_until_literal
NAV_TIMEOUT_MS = $nav_timeout_ms_literal
MIN_BODY_TEXT_LEN = 50  # body inner_text 최소 길이 — 빈 화면/스피너만 도는 페이지 차단
SETTLE_TIMEOUT_MS = 1500  # screenshot 찍기 직전 networkidle best-effort 대기 (부분 렌더 방지)
# 기본: 모든 URL 의 PNG 저장. opt-out: TOUR_SCREENSHOTS_FAILED_ONLY=1 (CI 등 디스크 절약).
SCREENSHOTS_FAILED_ONLY = os.environ.get("TOUR_SCREENSHOTS_FAILED_ONLY", "0") == "1"

# 환경변수로 즉시 override 가능 (스크립트 재생성 없이):
#   TOUR_HEADLESS=0 ...   → 브라우저 창이 뜸 (사용자가 직접 확인)
#   TOUR_HEADLESS=1 ...   → headless 강제 (CI/배경 실행)
_headless_env = os.environ.get("TOUR_HEADLESS")
if _headless_env is not None:
    HEADLESS = _headless_env not in ("0", "false", "False", "no", "")


# ── 유틸 ─────────────────────────────────────────────────────────────────
def _same_or_related_host(actual: str, expected: str) -> bool:
    if not expected:
        return True
    return actual == expected or actual.endswith("." + expected)


def _shot_path(idx: int, url: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", url)[:80]
    return SCREENSHOT_DIR / f"{idx:03d}_{safe}.png"


def _record(rec: dict) -> None:
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\\n")


# ── fixtures ────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def _reset_results():
    """매 세션 시작 시 jsonl 리셋, 스크린샷 디렉토리 보장."""
    if RESULTS.exists():
        RESULTS.unlink()
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    yield


@pytest.fixture(scope="session")
def _storage_state_path():
    """STORAGE_STATE 가 지정됐는데 파일이 없으면 친절한 메시지로 즉시 종료."""
    if STORAGE_STATE is None:
        return None
    p = Path(STORAGE_STATE).expanduser()
    if not p.is_file():
        pytest.exit(
            "\\n[tour] STORAGE_STATE 파일을 찾을 수 없습니다:\\n"
            f"  {p}\\n"
            "  같은 머신에서 시드한 storageState 가 필요합니다.\\n"
            "  Recording UI 의 'Auth Profile' 영역에서 다시 시드한 뒤,\\n"
            "  Discover URLs 화면에서 tour script 를 재생성하세요.\\n",
            returncode=2,
        )
    return p


@pytest.fixture(scope="session")
def browser_context(_storage_state_path):
    """세션 단위 1개 컨텍스트. 각 테스트는 fresh page 를 새로 연다."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        ctx_kwargs = json.loads(CONTEXT_KWARGS_JSON)
        if _storage_state_path is not None:
            ctx_kwargs["storage_state"] = str(_storage_state_path)
        context = browser.new_context(**ctx_kwargs)
        try:
            yield context
        finally:
            context.close()
            browser.close()


@pytest.fixture(scope="session")
def preflight(browser_context):
    """auth_profile 의 verify_url 로 미리 한 번 이동해 세션 만료를 사전 검출.

    실패 시 모든 URL 테스트를 skip (사유와 함께).
    """
    rec = {"phase": "preflight", "url": VERIFY_SERVICE_URL}
    if not PREFLIGHT_VERIFY or not VERIFY_SERVICE_URL:
        rec["skipped"] = "preflight disabled or no verify_url"
        rec["ok"] = True
        _record(rec)
        return
    page = browser_context.new_page()
    try:
        try:
            response = page.goto(VERIFY_SERVICE_URL, wait_until=WAIT_UNTIL, timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            rec["ok"] = False
            rec["error"] = repr(e)
            _record(rec)
            pytest.skip(f"preflight failed (navigation error): {e!r}")
            return
        status = response.status if response else None
        rec["status"] = status
        rec["final_url"] = page.url
        if VERIFY_SERVICE_TEXT:
            try:
                body = page.inner_text("body", timeout=5000)
            except Exception:
                body = ""
            ok = (status is None or status < 400) and (VERIFY_SERVICE_TEXT in body)
            if not ok:
                rec["error"] = "verify_service_text_not_found_or_status_failed"
        else:
            expected_host = urlparse(VERIFY_SERVICE_URL).hostname or ""
            final_host = urlparse(page.url).hostname or ""
            ok = (status is None or status < 400) and _same_or_related_host(final_host, expected_host)
            if not ok:
                rec["error"] = "verify_service_not_reachable_or_redirected"
        rec["ok"] = ok
        _record(rec)
        if not ok:
            pytest.skip(
                f"preflight failed — auth_profile 만료 가능성. "
                f"status={status}, final_url={page.url}. "
                f"Recording UI 에서 프로파일 재시드 후 tour script 재생성하세요."
            )
    finally:
        page.close()


# ── 테스트 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", URLS, ids=lambda u: u)
def test_url_renders_normally(url, browser_context, preflight):
    """선택 URL 이 정상 화면을 노출하는지 검증 (검증 항목 1~4)."""
    idx = URLS.index(url) + 1
    rec = {"phase": "tour", "index": idx, "url": url, "ok": False}
    page = browser_context.new_page()
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    try:
        # 1. navigation 자체 성공
        try:
            response = page.goto(url, wait_until=WAIT_UNTIL, timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            rec["error"] = repr(e)
            pytest.fail(f"navigation 실패: {e!r}")

        status = response.status if response else None
        final_url = page.url
        rec["status"] = status
        rec["final_url"] = final_url

        # 2. HTTP status < 400
        assert status is None or status < 400, \\
            f"HTTP {status} ({final_url})"

        # 3. seed host 같은 도메인 계열
        final_host = urlparse(final_url).hostname or ""
        assert _same_or_related_host(final_host, SEED_HOST), \\
            f"세션 만료 또는 외부 redirect 의심 — final_host={final_host}, expected={SEED_HOST}"

        # 4. title + body 길이
        title = (page.title() or "").strip()
        rec["title"] = title
        try:
            body_text = page.inner_text("body", timeout=5000)
        except Exception:
            body_text = ""
        body_len = len(body_text.strip())
        rec["body_len"] = body_len

        assert title, "<title> 이 비어 있음"
        assert body_len >= MIN_BODY_TEXT_LEN, \\
            f"body 텍스트 길이 {body_len} < {MIN_BODY_TEXT_LEN}자 — 빈 화면 의심"

        rec["ok"] = True
    except AssertionError as e:
        rec.setdefault("error", str(e))
        raise
    finally:
        failed = not rec.get("ok", False)
        # 기본: 모든 URL 저장. SCREENSHOTS_FAILED_ONLY=1 일 때만 실패 URL 만 저장.
        should_shot = failed or (not SCREENSHOTS_FAILED_ONLY)
        if should_shot:
            # screenshot 직전 networkidle best-effort settle — 부분 렌더 방지.
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
            except Exception:
                pass
            try:
                shot = _shot_path(idx, url)
                page.screenshot(path=str(shot), full_page=True)
                rec["screenshot"] = str(shot)
            except Exception:
                pass
        _record(rec)
        page.close()


# ── Recording UI 'Play Script from File' 호환 ───────────────────────────
# 이 파일은 pytest 테스트 파일이지만, Recording UI 가 Play 시 단순
# `python <script.py>` 로 실행하므로 같은 결과를 얻기 위해 pytest 를
# 라이브러리로 호출. rc=0(전부 PASS) / 1(하나 이상 FAIL) / 2(skip 뿐) 등.
if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))
''')


def _shrink_home_path(path: _Path) -> str:
    """절대 경로를 가능하면 ~ prefix 로 축약. 다른 경우 절대 경로 유지."""
    try:
        rel = path.relative_to(_Path.home())
        return f"~/{rel}"
    except ValueError:
        return str(path)


def _format_urls_block(urls: list[str]) -> str:
    """URL 리스트를 한 줄당 하나씩 들여쓴 Python 리터럴로 직렬화 (가독성)."""
    if not urls:
        return "[]"
    indent = "    "
    body = ",\n".join(f"{indent}{repr(u)}" for u in urls)
    return f"[\n{body},\n]"


def _generate_tour_script(
    *,
    urls: list[str],
    seed_url: str,
    storage_path: Optional[_Path],
    fingerprint: object,
    headless: bool,
    preflight_verify: bool,
    verify_service_url: str,
    verify_service_text: str,
    wait_until: str,
    nav_timeout_ms: int,
) -> str:
    """선택 URL tour script (pytest 형식) 의 Python 소스 텍스트 생성.

    `CONTEXT_KWARGS` 는 JSON 직렬화로 안전 복원. 비호환 객체가 끼어 있으면
    HTTPException(500) 으로 거부.
    """
    fp_kwargs = fingerprint.to_browser_context_kwargs() if fingerprint else {}
    try:
        context_kwargs_json = json.dumps(fp_kwargs, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=500,
            detail={
                "reason": "fingerprint_not_serializable",
                "message": f"Fingerprint kwargs 가 JSON 으로 직렬화되지 않습니다: {e}",
            },
        )

    storage_literal: str
    if storage_path is None:
        storage_literal = "None"
    else:
        storage_literal = repr(_shrink_home_path(storage_path))

    from urllib.parse import urlparse as _urlparse
    seed_host = (_urlparse(seed_url).hostname or "").lower()

    return _TOUR_SCRIPT_TEMPLATE.safe_substitute(
        urls_block=_format_urls_block(list(urls)),
        seed_host_literal=repr(seed_host),
        storage_state_literal=storage_literal,
        context_kwargs_json_literal=repr(context_kwargs_json),
        headless_literal=repr(bool(headless)),
        preflight_verify_literal=repr(bool(preflight_verify)),
        verify_url_literal=repr(verify_service_url or ""),
        verify_text_literal=repr(verify_service_text or ""),
        wait_until_literal=repr(wait_until),
        nav_timeout_ms_literal=repr(int(nav_timeout_ms)),
    )


@app.post("/discover", status_code=202)
def discover_start(req: DiscoverReq, response: Response) -> dict:
    """discover job 을 백그라운드 worker 로 시작.

    동시 실행 상한 초과 시 429. auth_profile 검증 실패는
    `_load_profile_for_browser()` 가 던지는 HTTPException 에 위임.
    """
    with _discover_lock:
        running = sum(
            1 for j in _discover_jobs.values()
            if j.state in ("running", "cancelling")
        )
    if running >= DISCOVER_MAX_CONCURRENT:
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "too_many_running_discover_jobs",
                "limit": DISCOVER_MAX_CONCURRENT,
            },
        )

    storage_path, fingerprint, machine_mismatch = _load_profile_for_browser(req.auth_profile)

    job_id = uuid.uuid4().hex[:12]
    job = DiscoverJob(
        job_id=job_id,
        state="running",
        seed_url=req.seed_url,
        auth_profile=req.auth_profile,
        machine_mismatch=machine_mismatch,
        started_at=_now_iso_utc(),
    )
    cancel_event = threading.Event()
    with _discover_lock:
        _discover_jobs[job_id] = job
        _discover_cancel_events[job_id] = cancel_event

    threading.Thread(
        target=_discover_worker,
        args=(job_id, storage_path, fingerprint, req.max_pages, req.max_depth, cancel_event),
        kwargs={
            "use_sitemap": req.use_sitemap,
            "capture_requests": req.capture_requests,
            "spa_selectors": req.spa_selectors,
            "ignore_query": req.ignore_query,
            "include_subdomains": req.include_subdomains,
        },
        daemon=True,
    ).start()

    if machine_mismatch:
        response.headers["X-Auth-Machine-Mismatch"] = "1"
    return {
        "job_id": job_id,
        "state": "running",
        "machine_mismatch": machine_mismatch,
    }


@app.get("/discover/{job_id}")
def discover_status(job_id: str) -> DiscoverJob:
    with _discover_lock:
        job = _discover_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"reason": "job_not_found"})
    return job


@app.post("/discover/{job_id}/cancel")
def discover_cancel(job_id: str) -> dict:
    with _discover_lock:
        job = _discover_jobs.get(job_id)
        ev = _discover_cancel_events.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"reason": "job_not_found"})
        if job.state not in ("running", "cancelling"):
            raise HTTPException(
                status_code=409,
                detail={"reason": "job_not_cancellable", "state": job.state},
            )
        if ev is not None:
            ev.set()
        job.state = "cancelling"
    return {"job_id": job_id, "state": "cancelling"}


def _require_finished_job(job_id: str) -> DiscoverJob:
    with _discover_lock:
        job = _discover_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"reason": "job_not_found"})
    if job.state not in ("done", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail={"reason": "job_not_finished", "state": job.state},
        )
    return job


@app.get("/discover/{job_id}/csv")
def discover_csv(job_id: str):
    job = _require_finished_job(job_id)
    if not job.result_dir:
        raise HTTPException(status_code=409, detail={"reason": "job_not_finished"})
    csv_path = _Path(job.result_dir) / "urls.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail={"reason": "csv_not_found"})
    return FileResponse(
        path=str(csv_path),
        media_type="text/csv; charset=utf-8",
        filename=f"discover-{job_id}.csv",
    )


@app.get("/discover/{job_id}/json")
def discover_json(job_id: str):
    job = _require_finished_job(job_id)
    if not job.result_dir:
        raise HTTPException(status_code=409, detail={"reason": "job_not_finished"})
    json_path = _Path(job.result_dir) / "urls.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail={"reason": "json_not_found"})
    return FileResponse(
        path=str(json_path),
        media_type="application/json; charset=utf-8",
        filename=f"discover-{job_id}.json",
    )


@app.post("/discover/{job_id}/tour-script")
def discover_tour_script(job_id: str, req: TourScriptReq):
    """선택 URL 만 순회하는 Python Playwright tour script 를 생성/반환."""
    from zero_touch_qa.url_discovery import DiscoverConfig, normalize_url

    job = _require_finished_job(job_id)
    if not job.result_dir:
        raise HTTPException(status_code=409, detail={"reason": "job_not_finished"})

    urls_json_path = _Path(job.result_dir) / "urls.json"
    if not urls_json_path.exists():
        raise HTTPException(status_code=404, detail={"reason": "urls_json_not_found"})

    discovered = json.loads(urls_json_path.read_text(encoding="utf-8"))
    trash = DiscoverConfig.__dataclass_fields__["trash_query_params"].default
    # discover 실행 시 사용된 ignore_query 와 동일한 정규화 정책으로 매칭한다.
    strip_q = False
    meta_path = _Path(job.result_dir) / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            strip_q = bool(meta.get("options", {}).get("ignore_query", False))
        except Exception:
            strip_q = False
    norm_to_original: dict[str, str] = {}
    for rec in discovered:
        u = rec.get("url")
        if isinstance(u, str):
            norm_to_original.setdefault(
                normalize_url(u, trash_query_params=trash, strip_all_query=strip_q),
                u,
            )

    selected_originals: list[str] = []
    missing: list[str] = []
    for raw in req.urls:
        key = normalize_url(raw, trash_query_params=trash, strip_all_query=strip_q)
        original = norm_to_original.get(key)
        if original is None:
            missing.append(raw)
        else:
            selected_originals.append(original)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"reason": "urls_not_in_discovery", "missing": missing},
        )

    storage_path: Optional[_Path] = None
    fingerprint: object = None
    verify_url = ""
    verify_text = ""
    if req.auth_profile:
        storage_path, fingerprint, _ = _load_profile_for_browser(req.auth_profile)
        # verify spec 도 다시 가져와 script 에 박는다.
        from zero_touch_qa import auth_profiles
        prof = auth_profiles.get_profile(req.auth_profile)
        verify_url = prof.verify.service_url
        verify_text = prof.verify.service_text or ""

    script_text = _generate_tour_script(
        urls=selected_originals,
        seed_url=job.seed_url,
        storage_path=storage_path,
        fingerprint=fingerprint,
        headless=req.headless,
        preflight_verify=req.preflight_verify,
        verify_service_url=verify_url,
        verify_service_text=verify_text,
        wait_until=req.wait_until,
        nav_timeout_ms=req.nav_timeout_ms,
    )

    out_path = _Path(job.result_dir) / "tour_selected.py"
    out_path.write_text(script_text, encoding="utf-8")
    return FileResponse(
        path=str(out_path),
        media_type="text/x-python",
        filename="tour_selected.py",
    )
