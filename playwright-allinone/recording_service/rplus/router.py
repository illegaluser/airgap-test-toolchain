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


def _run_replay_impl(
    *, container_session_dir: str, host_session_dir: str,
):
    """TR.7 monkeypatch hook — fake docker exec 결과."""
    return replay_proxy.run_replay(
        container_session_dir=container_session_dir,
        host_session_dir=host_session_dir,
    )


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

@router.post("/sessions/{sid}/replay", status_code=201)
def replay_session(sid: str) -> dict:
    """녹화된 14-DSL 을 컨테이너 측 executor 로 재실행 (TR.7 R-Plus)."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"세션 미발견: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"Replay 는 변환 완료(state=done) 세션만 가능합니다. 현재 state={sess.state}",
        )

    scenario = storage.load_scenario(sid)
    if not scenario:
        raise HTTPException(status_code=409, detail=f"세션 {sid} 의 scenario.json 이 누락됨.")

    container_dir = storage.container_path_for(sid)
    host_dir = str(storage.session_dir(sid))

    try:
        result = _run_replay_impl(
            container_session_dir=container_dir,
            host_session_dir=host_dir,
        )
    except ReplayProxyError as e:
        log.error("[/experimental/replay] %s — %s", sid, e)
        raise HTTPException(status_code=502, detail=str(e))

    log.info(
        "[/experimental/replay] %s — rc=%d pass=%d fail=%d healed=%d (%.0fms)",
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
