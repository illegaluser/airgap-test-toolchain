"""R-Plus 엔드포인트 — ``/experimental/sessions/{sid}/...``.

`recording_service.server` 가 ``RPLUS_ENABLED=1`` 일 때만 이 router 를 include
한다. 핸들러 본체는 R-MVP 시절 `server.py` 에 있던 코드와 동일하지만, hook
이름 (``_run_replay_impl`` 등) 은 이 모듈로 이전됐으므로 monkeypatch 대상도
``recording_service.rplus.router`` 로 바뀐다.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import comparator, enricher, replay_proxy, session, storage
from ..enricher import EnrichError, EnrichResult
from ..replay_proxy import ReplayAuthExpiredError, ReplayProxyError

log = logging.getLogger("recording_service.rplus")


router = APIRouter(prefix="/experimental", tags=["rplus"])


# ── monkeypatch hook ────────────────────────────────────────────────────────
# 테스트가 실제 docker exec / Ollama 호출 없이 결정론적 결과를 주입할 수 있도록
# 분기점을 모듈 레벨 함수로 노출. server.py 에 있던 같은 이름의 hook 들을 이
# 모듈로 옮긴 것 — 테스트에서는 `recording_service.rplus.router` 를 monkeypatch.

def _run_enrich_impl(
    *, scenario: list[dict], target_url: str,
    page_title: Optional[str] = None, inventory_block: Optional[str] = None,
):
    """TR.5 monkeypatch hook — 단위 테스트가 Ollama 호출 없이 fake 결과 반환."""
    return enricher.enrich_recording(
        scenario=scenario,
        target_url=target_url,
        page_title=page_title,
        inventory_block=inventory_block,
    )


def _run_diff_analysis_impl(
    *, original_py: str, regression_py: str, unified_diff: str,
):
    """항목 4 (UI 개선) monkeypatch hook — 단위 테스트용 분기점."""
    return enricher.analyze_codegen_vs_regression(
        original_py=original_py,
        regression_py=regression_py,
        unified_diff=unified_diff,
    )


def _run_codegen_replay_impl(
    *,
    host_session_dir: str,
    auth_profile_override: Optional[str] = None,
    headed: bool = True,
    slow_mo_ms: Optional[int] = None,
):
    """codegen 원본 ``original.py`` 호스트 실행 hook (monkeypatch 대상)."""
    return replay_proxy.run_codegen_replay(
        host_session_dir=host_session_dir,
        auth_profile_override=auth_profile_override,
        headed=headed,
        slow_mo_ms=slow_mo_ms,
    )


def _run_llm_play_impl(
    *,
    host_session_dir: str,
    project_root: str,
    auth_profile_override: Optional[str] = None,
    headed: bool = True,
    slow_mo_ms: Optional[int] = None,
):
    """14-DSL ``scenario.json`` 의 zero_touch_qa executor 실행 hook."""
    return replay_proxy.run_llm_play(
        host_session_dir=host_session_dir,
        project_root=project_root,
        auth_profile_override=auth_profile_override,
        headed=headed,
        slow_mo_ms=slow_mo_ms,
    )


def _project_root() -> str:
    """zero_touch_qa 패키지가 있는 프로젝트 루트 — recording_service 의 부모 디렉토리."""
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent.parent)


def _registry_lookup(sid: str):
    """server module 의 in-memory registry 를 lazy 참조한다.

    R-Plus router 가 server import 를 강결합하지 않도록 함수 내부에서 import.
    server.py → rplus.router → server 의 순환 import 우회.
    """
    from ..server import _registry
    return _registry.get(sid)


# ── 응답/요청 모델 ─────────────────────────────────────────────────────────

class EnrichReq(BaseModel):
    page_title: Optional[str] = Field(None, description="페이지 타이틀 (있으면 컨텍스트 강화)")
    inventory_block: Optional[str] = Field(
        None,
        description="Phase 1 grounding 인벤토리 마커 블록 (선택). srs_text prepend 패턴과 동일.",
    )


class PlayReq(BaseModel):
    """R-Plus Play 요청 옵션 (선택). body 가 없으면 모두 기본값.

    - ``auth_profile``: ``None`` 이면 세션 metadata 의 프로파일 사용. 빈 문자열
      이면 인증 없이 재생. 이름이면 그 프로파일로 override.
    - ``headed``: False 면 헤드리스 실행 (codegen 은 wrapper monkey-patch,
      LLM 은 ``--headless`` 플래그).
    - ``slow_mo_ms``: 0/None 이면 꺼짐. >0 이면 Playwright ``slow_mo`` 로 각
      액션 후 N 밀리초 지연 — 사람이 눈으로 따라가며 디버깅할 때 사용.
    - ``annotate_dynamic``: True (기본) 면 play-codegen 직전 annotate 단계가
      실 페이지 visibility probe 로 ancestor hover trigger 식별. dropdown /
      메뉴 (single segment selector) 에서 정적 휴리스틱이 못 잡는 케이스
      보강. sandbox replay 가 30s 내외 추가되지만 정확도 우선 — sandbox 실패
      / 후보 0 시 정적 annotate 로 graceful fallback (회귀 0). False 로 명시
      해 정적만 사용 가능.
    """
    auth_profile: Optional[str] = Field(
        None,
        description="auth-profile override. None=세션 metadata 사용, ''=인증 없이, '<name>'=override.",
    )
    headed: bool = Field(True, description="False 면 헤드리스 실행.")
    slow_mo_ms: Optional[int] = Field(
        None,
        ge=0,
        description="각 액션 후 지연 (ms). 0/None 이면 끔.",
    )
    annotate_dynamic: bool = Field(
        True,
        description="True (기본) 면 dynamic annotate. False 면 정적 annotate 만.",
    )


class CompareReq(BaseModel):
    doc_dsl: list[dict] = Field(..., description="비교 대상 doc-DSL (chat 모드 출력 또는 손작성).")
    threshold: float = Field(
        comparator.DEFAULT_FUZZY_THRESHOLD,
        description="fuzzy 매칭 임계값 (0~1). 기본 0.7.",
    )
    doc_label: str = Field("doc-DSL", description="HTML 리포트 컬럼 라벨")
    rec_label: str = Field("recording-DSL", description="HTML 리포트 컬럼 라벨")


# ── 엔드포인트 ─────────────────────────────────────────────────────────────

def _ensure_session_not_recording(sid: str, kind: str):
    """공용 가드 — 세션이 존재하고 녹화 중이 아닐 것."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state == session.STATE_RECORDING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"녹화 중에는 {kind} 불가 (state={sess.state}). "
                f"먼저 stop 으로 codegen 을 종료한 뒤 시도하세요."
            ),
        )


def _play_response(sid: str, result) -> dict:
    return {
        "id": sid,
        "returncode": result.returncode,
        "elapsed_ms": result.elapsed_ms,
        "stdout_tail": result.stdout[-500:] if result.stdout else "",
        "stderr_tail": result.stderr[-500:] if result.stderr else "",
    }


def _is_imported_session(sid: str) -> bool:
    """metadata 의 ``imported_filename`` 필드로 사용자 업로드 세션 판별."""
    meta = storage.load_metadata(sid) or {}
    return bool(meta.get("imported_filename"))


def _resolve_storage_state(sid: str, override: Optional[str]) -> Optional[str]:
    """auth-profile 의 storage_state 경로를 best-effort 로 반환.

    play-codegen 의 정식 verify 흐름은 ``_run_codegen_replay_impl`` 안에서
    다시 일어나므로, annotate 단계에선 verify 실패 / 프로파일 미존재 등을
    silent 로 swallow → None 반환 (dynamic annotate 가 정적 fallback 으로 회귀).

    Args:
        sid: 세션 ID.
        override: PlayReq.auth_profile — None=metadata, ""=비인증, "<name>"=강제.
    """
    try:
        from ..replay_proxy import _resolve_auth_for_replay

        host_dir = str(storage.session_dir(sid))
        storage_path, _, _ = _resolve_auth_for_replay(host_dir, override)
        return storage_path
    except Exception:  # noqa: BLE001
        return None


def _annotate_for_session(sid: str, *, dynamic: bool = False, storage_state_in: Optional[str] = None) -> dict:
    """play-codegen 진입 직전 자동 호출용 — annotate 결과 dict 반환.

    실패는 silent (annotation 없이도 codegen 원본 그대로 실행 가능). 호출자가
    응답에 합쳐 사용자에게 노출.

    **Imported 세션 (사용자 .py 업로드) 은 annotate 스킵** — 사용자의 의도된
    스크립트를 휴리스틱이 변형하는 부수 효과 차단. stale `original_annotated.py`
    존재 시 제거.

    Args:
        sid: 세션 ID.
        dynamic: True 면 정적 휴리스틱 대신 실 페이지 visibility probe 로
            ancestor hover trigger 식별 (annotate_script_dynamic). 기본 False.
        storage_state_in: dynamic=True 시 인증 storage_state JSON 경로. 비공개
            페이지의 메뉴/dropdown 보려면 필요. None 이면 인증 없이 sandbox 기동.
    """
    from .. import annotator
    host_dir = storage.session_dir(sid)
    src = host_dir / "original.py"
    if not src.is_file():
        return {"injected": 0, "examined_clicks": 0, "triggers": [], "skipped": "no original.py"}
    dst = host_dir / "original_annotated.py"
    if _is_imported_session(sid):
        # 업로드 스크립트는 그대로 실행 — stale annotated 제거
        if dst.is_file():
            try:
                dst.unlink()
            except OSError:  # noqa: BLE001
                pass
        return {
            "injected": 0,
            "examined_clicks": 0,
            "triggers": [],
            "skipped": "imported script — annotator 우회 (사용자 의도 보존)",
        }
    try:
        if dynamic:
            r = annotator.annotate_script_dynamic(
                str(src), str(dst), storage_state_in=storage_state_in,
            )
        else:
            r = annotator.annotate_script(str(src), str(dst))
        return {
            "injected": r.injected,
            "examined_clicks": r.examined_clicks,
            "triggers": r.triggers,
            "mode": "dynamic" if dynamic else "static",
        }
    except Exception as e:  # noqa: BLE001
        log.warning("[annotate auto] %s — %s", sid, e)
        return {"injected": 0, "examined_clicks": 0, "triggers": [], "skipped": str(e)}


@router.post("/sessions/{sid}/play-codegen", status_code=201)
def play_codegen(sid: str, req: Optional[PlayReq] = None) -> dict:
    """codegen 원본 ``original.py`` 를 호스트에서 그대로 실행 (TR.7).

    실행 직전 자동으로 annotate 를 수행해 hover-needing click 앞에 hover 라인을
    주입한 ``original_annotated.py`` 를 만들고 그것을 우선 실행 (prefer_annotated).
    Annotate 결과 (injected 수 + trigger 목록) 도 응답에 함께 노출.

    Body (선택): :class:`PlayReq` — auth_profile override, headed 토글.
    """
    _ensure_session_not_recording(sid, "play-codegen")
    host_dir = str(storage.session_dir(sid))

    opts = req or PlayReq()
    # (β) annotate 자동 — 정적 (default) 또는 dynamic (opts.annotate_dynamic).
    # dynamic 면 auth-profile 의 storage_state 를 sandbox 에 그대로 주입해
    # 인증 필요 페이지의 메뉴 / dropdown 도 식별 가능.
    annotate_summary = _annotate_for_session(
        sid,
        dynamic=opts.annotate_dynamic,
        storage_state_in=_resolve_storage_state(sid, opts.auth_profile),
    )
    # E2E 슈트가 spawn 한 데몬에서 RECORDING_FORCE_HEADLESS=1 셋이면 headless 강제.
    # 운영 데몬은 이 env 를 셋하지 않아 사용자 UI 체크박스 동작 영향 없음.
    if os.environ.get("RECORDING_FORCE_HEADLESS") == "1":
        opts.headed = False
    try:
        result = _run_codegen_replay_impl(
            host_session_dir=host_dir,
            auth_profile_override=opts.auth_profile,
            headed=opts.headed,
            slow_mo_ms=opts.slow_mo_ms,
        )
    except ReplayAuthExpiredError as e:
        # post-review fix — auth-profile 만료/미존재는 502 가 아니라 409 +
        # 구조화된 detail 로 반환. UI 가 만료 모달 + [재시드] 분기로 가도록.
        log.warning("[/experimental/play-codegen] %s — auth expired: %s", sid, e)
        # ``e.detail`` 에 ``reason`` 키가 있을 수 있어 spread 순서가 중요.
        # router 의 ``reason="profile_expired"`` 가 inner detail 의 reason
        # (예: ``verify_failed``) 에 덮이지 않도록 우리 키를 뒤에 둔다.
        raise HTTPException(
            status_code=409,
            detail={
                "profile_name": e.profile_name,
                **e.detail,
                "reason": "profile_expired",
            },
        )
    except ReplayProxyError as e:
        log.error("[/experimental/play-codegen] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))
    log.info(
        "[/experimental/play-codegen] %s — rc=%d (%.0fms) annotate=%s",
        sid, result.returncode, result.elapsed_ms, annotate_summary,
    )
    response = _play_response(sid, result)
    response["annotate"] = annotate_summary
    return response


@router.post("/sessions/{sid}/play-llm", status_code=201)
def play_llm(sid: str, req: Optional[PlayReq] = None) -> dict:
    """변환된 14-DSL ``scenario.json`` 을 zero_touch_qa executor 로 실행.

    healing 3-stage / fallback_targets / verify / mock_status / mock_data 등
    14-DSL 의 풀 기능 동작 + 화면에 재생. codegen 원본보다 selector 변동에
    강함 (LocalHealer + Dify LLM 치유) — 단, scenario.json 이 변환된 상태여야
    하므로 state=done 필수.

    Body (선택): :class:`PlayReq` — auth_profile override, headed 토글.
    """
    _ensure_session_not_recording(sid, "play-llm")
    sess = _registry_lookup(sid)
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"play-llm 은 변환 완료(state=done) 세션만 가능합니다. "
                f"현재 state={sess.state}"
            ),
        )
    host_dir = str(storage.session_dir(sid))
    opts = req or PlayReq()
    # E2E 슈트가 spawn 한 데몬에서 RECORDING_FORCE_HEADLESS=1 셋이면 headless 강제.
    # 운영 데몬은 이 env 를 셋하지 않아 사용자 UI 체크박스 동작 영향 없음.
    if os.environ.get("RECORDING_FORCE_HEADLESS") == "1":
        opts.headed = False
    try:
        result = _run_llm_play_impl(
            host_session_dir=host_dir,
            project_root=_project_root(),
            auth_profile_override=opts.auth_profile,
            headed=opts.headed,
            slow_mo_ms=opts.slow_mo_ms,
        )
    except ReplayAuthExpiredError as e:
        # post-review fix — auth-profile 만료/미존재 → 409 + 구조화 detail.
        # UI 가 만료 모달 + [재시드] 분기로 갈 수 있게.
        log.warning("[/experimental/play-llm] %s — auth expired: %s", sid, e)
        # ``e.detail`` 에 ``reason`` 키가 있을 수 있어 spread 순서가 중요.
        # router 의 ``reason="profile_expired"`` 가 inner detail 의 reason
        # (예: ``verify_failed``) 에 덮이지 않도록 우리 키를 뒤에 둔다.
        raise HTTPException(
            status_code=409,
            detail={
                "profile_name": e.profile_name,
                **e.detail,
                "reason": "profile_expired",
            },
        )
    except ReplayProxyError as e:
        log.error("[/experimental/play-llm] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))
    log.info(
        "[/experimental/play-llm] %s — rc=%d (%.0fms)",
        sid, result.returncode, result.elapsed_ms,
    )
    return _play_response(sid, result)


@router.post("/sessions/{sid}/enrich", status_code=201)
def enrich_session(sid: str, req: EnrichReq) -> dict:
    """녹화된 시나리오를 IEEE 829-lite Markdown 으로 역추정 (TR.5 R-Plus)."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"reverse-inference is only available for converted (state=done) sessions. current state={sess.state}",
        )

    scenario = storage.load_scenario(sid)
    if not scenario:
        # imported 세션이 codegen 산출물 패턴이 아니면 변환기가 0 step 을 내고
        # tour 합성 fallback 도 마커 부재로 적용 안 됨. 사용자가 시도조차 안
        # 하도록 UI 가 막아야 정상이지만, 직접 호출/우회 케이스에 대비해 명확한
        # 사유 + 해결 가이드를 detail 로 제공.
        meta = storage.load_metadata(sid) or {}
        is_imported = bool(meta.get("imported_filename"))
        if is_imported:
            msg = (
                f"역추정 불가 — 임포트한 스크립트 '{meta.get('imported_filename')}' "
                "가 시나리오 형태로 변환되지 않습니다. codegen 산출물(직선적 "
                "page.click/fill 호출) 또는 tour 스크립트(URLS = [...] 패턴) 만 "
                "현재 변환기/합성기가 인식합니다."
            )
        else:
            msg = f"세션 {sid} 의 scenario.json 이 비어있거나 누락됨."
        raise HTTPException(
            status_code=409,
            detail={"reason": "scenario_empty", "message": msg, "imported": is_imported},
        )

    try:
        result: EnrichResult = _run_enrich_impl(
            scenario=scenario,
            target_url=sess.target_url,
            page_title=req.page_title,
            inventory_block=req.inventory_block,
        )
    except EnrichError as e:
        log.error("[/experimental/enrich] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))

    enriched_path = storage.session_dir(sid) / "doc_enriched.md"
    enriched_path.write_text(result.markdown, encoding="utf-8")

    log.info(
        "[/experimental/enrich] %s — %d chars (%s, %.0fms)",
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


@router.post("/sessions/{sid}/compare", status_code=201)
def compare_session(sid: str, req: CompareReq) -> dict:
    """녹화된 14-DSL 과 사용자가 제공한 doc-DSL 을 5분류로 비교 (TR.6 R-Plus)."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"compare is only available for converted (state=done) sessions. current state={sess.state}",
        )
    if not req.doc_dsl:
        raise HTTPException(status_code=400, detail="doc_dsl is empty.")

    rec_dsl = storage.load_scenario(sid)
    if not rec_dsl:
        raise HTTPException(status_code=409, detail=f"session {sid} has no scenario.json.")

    if not (0.0 <= req.threshold <= 1.0):
        raise HTTPException(status_code=400, detail="threshold must be within 0.0–1.0.")

    result = comparator.compare(req.doc_dsl, rec_dsl, threshold=req.threshold)
    html = comparator.render_html(result, doc_label=req.doc_label, rec_label=req.rec_label)

    out_path = storage.session_dir(sid) / "doc_comparison.html"
    out_path.write_text(html, encoding="utf-8")

    log.info(
        "[/experimental/compare] %s — counts=%s, doc=%d steps, rec=%d steps",
        sid, result.counts, len(req.doc_dsl), len(rec_dsl),
    )
    return {
        "id": sid,
        "counts": result.counts,
        "threshold_used": result.threshold_used,
        "doc_step_count": len(req.doc_dsl),
        "rec_step_count": len(rec_dsl),
        "saved_to": str(out_path),
        "report_html_url": f"/experimental/sessions/{sid}/comparison.html",
    }


@router.get("/sessions/{sid}/comparison.html", response_class=FileResponse, include_in_schema=False)
def get_comparison_html(sid: str) -> FileResponse:
    """compare 결과 HTML 리포트를 직접 서빙 (UI 의 새 탭 진입 용)."""
    p = storage.session_dir(sid) / "doc_comparison.html"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="compare report not generated")
    return FileResponse(str(p), media_type="text/html")


# ── 항목 4 — codegen 원본 ↔ LLM healed regression 비교 ────────────────────

@router.get("/sessions/{sid}/diff-codegen-vs-llm", include_in_schema=False)
def get_diff_codegen_vs_llm(sid: str) -> dict:
    """codegen ``original.py`` 와 LLM healed ``regression_test.py`` 를 비교.

    사용자 흐름: Play with LLM 후 자동 생성된 regression_test.py 를 원본과
    diff 로 비교 → 의도 일치 확인 → 다운로드 → 회귀 슈트로 채택.

    Response:
        - left_path / right_path / left_content / right_content
        - unified_diff: difflib.unified_diff 결과 텍스트
        - left_exists / right_exists
        - 양쪽 파일 모두 없으면 404.
    """
    import difflib

    left_p = storage.original_py_path(sid)
    right_p = storage.regression_py_path(sid)
    left_exists = left_p.is_file()
    right_exists = right_p.is_file()
    if not left_exists and not right_exists:
        raise HTTPException(
            status_code=404,
            detail="neither original.py nor regression_test.py exists",
        )
    left_content = left_p.read_text(encoding="utf-8") if left_exists else ""
    right_content = right_p.read_text(encoding="utf-8") if right_exists else ""
    unified = "".join(difflib.unified_diff(
        left_content.splitlines(keepends=True),
        right_content.splitlines(keepends=True),
        fromfile="original.py (codegen)",
        tofile="regression_test.py (LLM healed)",
        n=3,
    ))
    return {
        "left_path": "original.py",
        "right_path": "regression_test.py",
        "left_content": left_content,
        "right_content": right_content,
        "unified_diff": unified,
        "left_exists": left_exists,
        "right_exists": right_exists,
    }


@router.post("/sessions/{sid}/diff-analysis", include_in_schema=False)
def post_diff_analysis(sid: str) -> dict:
    """codegen 원본 ↔ regression_test.py 차이를 LLM (Ollama) 으로 의미 분석.

    1차원 unified diff 보다 사용자에게 유용한 정보 (selector swap 의도 추정 /
    위험 평가 / 회귀 채택 권고) 를 제공. 분석 결과는 markdown 으로 반환.

    POST 인 이유: Ollama 호출이 부수효과 (수십 초 시간/비용) 를 가짐 — GET
    캐싱 의미론과 충돌.
    """
    import difflib

    left_p = storage.original_py_path(sid)
    right_p = storage.regression_py_path(sid)
    if not right_p.is_file():
        raise HTTPException(
            status_code=404,
            detail="regression_test.py not found — Play with LLM has not been run",
        )
    left_content = left_p.read_text(encoding="utf-8") if left_p.is_file() else ""
    right_content = right_p.read_text(encoding="utf-8")
    unified = "".join(difflib.unified_diff(
        left_content.splitlines(keepends=True),
        right_content.splitlines(keepends=True),
        fromfile="original.py (codegen)",
        tofile="regression_test.py (LLM healed)",
        n=3,
    ))
    try:
        result = _run_diff_analysis_impl(
            original_py=left_content,
            regression_py=right_content,
            unified_diff=unified,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"LLM analysis call failed: {e}",
        ) from e
    log.info(
        "[/experimental/diff-analysis] %s — model=%s elapsed=%.0fms",
        sid, result.model, result.elapsed_ms,
    )
    return {
        "id": sid,
        "markdown": result.markdown,
        "model": result.model,
        "elapsed_ms": result.elapsed_ms,
    }
