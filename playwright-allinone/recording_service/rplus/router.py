"""R-Plus 엔드포인트 — ``/experimental/sessions/{sid}/...``.

`recording_service.server` 가 ``RPLUS_ENABLED=1`` 일 때만 이 router 를 include
한다. 핸들러 본체는 R-MVP 시절 `server.py` 에 있던 코드와 동일하지만, hook
이름 (``_run_replay_impl`` 등) 은 이 모듈로 이전됐으므로 monkeypatch 대상도
``recording_service.rplus.router`` 로 바뀐다.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import comparator, enricher, replay_proxy, session, storage
from ..enricher import EnrichError, EnrichResult
from ..replay_proxy import ReplayProxyError

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


def _run_codegen_replay_impl(*, host_session_dir: str):
    """codegen 원본 ``original.py`` 호스트 실행 hook (monkeypatch 대상)."""
    return replay_proxy.run_codegen_replay(host_session_dir=host_session_dir)


def _run_llm_play_impl(*, host_session_dir: str, project_root: str):
    """14-DSL ``scenario.json`` 의 zero_touch_qa executor 실행 hook."""
    return replay_proxy.run_llm_play(
        host_session_dir=host_session_dir,
        project_root=project_root,
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
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
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


@router.post("/sessions/{sid}/annotate", status_code=201)
def annotate_session(sid: str) -> dict:
    """codegen 원본 ``original.py`` 를 정적 휴리스틱으로 분석해 hidden-click 우려가
    있는 click 앞에 ``<ancestor>.hover()`` 를 자동 주입한 ``original_annotated.py`` 를 생성.

    이후 play-codegen 은 (prefer_annotated=True) 자동으로 annotated 본을 우선 실행.
    """
    from .. import annotator
    _ensure_session_not_recording(sid, "annotate")
    host_dir = storage.session_dir(sid)
    src = host_dir / "original.py"
    dst = host_dir / "original_annotated.py"
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"original.py 없음: {src}")
    try:
        result = annotator.annotate_script(str(src), str(dst))
    except Exception as e:  # noqa: BLE001
        log.error("[/experimental/annotate] %s — %s", sid, e)
        raise HTTPException(status_code=500, detail=str(e))
    log.info(
        "[/experimental/annotate] %s — examined=%d injected=%d",
        sid, result.examined_clicks, result.injected,
    )
    return {
        "id": sid,
        "examined_clicks": result.examined_clicks,
        "injected": result.injected,
        "triggers": result.triggers,
        "annotated_path": str(dst),
    }


@router.post("/sessions/{sid}/play-codegen", status_code=201)
def play_codegen(sid: str) -> dict:
    """codegen 원본 ``original.py`` 를 호스트에서 그대로 실행 (TR.7, headed).

    녹화한 동작이 화면에 그대로 재현. 14-DSL 변환과 무관한 평범한 Playwright
    스크립트 실행이라 healing/verify 같은 14-DSL 풀 기능은 동작하지 않는다.
    원본 selector 가 화면 변경으로 깨지면 그대로 실패.
    """
    _ensure_session_not_recording(sid, "play-codegen")
    host_dir = str(storage.session_dir(sid))
    try:
        result = _run_codegen_replay_impl(host_session_dir=host_dir)
    except ReplayProxyError as e:
        log.error("[/experimental/play-codegen] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))
    log.info(
        "[/experimental/play-codegen] %s — rc=%d (%.0fms)",
        sid, result.returncode, result.elapsed_ms,
    )
    return _play_response(sid, result)


@router.post("/sessions/{sid}/play-llm", status_code=201)
def play_llm(sid: str) -> dict:
    """변환된 14-DSL ``scenario.json`` 을 zero_touch_qa executor 로 실행 (headed).

    healing 3-stage / fallback_targets / verify / mock_status / mock_data 등
    14-DSL 의 풀 기능 동작 + 화면에 재생. codegen 원본보다 selector 변동에
    강함 (LocalHealer + Dify LLM 치유) — 단, scenario.json 이 변환된 상태여야
    하므로 state=done 필수.
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
    try:
        result = _run_llm_play_impl(
            host_session_dir=host_dir,
            project_root=_project_root(),
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
        raise HTTPException(status_code=409, detail=f"세션 {sid} 의 scenario.json 이 누락됨.")

    if not (0.0 <= req.threshold <= 1.0):
        raise HTTPException(status_code=400, detail="threshold 는 0.0~1.0 범위.")

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
        raise HTTPException(status_code=404, detail="비교 리포트 미생성")
    return FileResponse(str(p), media_type="text/html")
