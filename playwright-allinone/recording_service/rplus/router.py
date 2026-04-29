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


def _is_imported_session(sid: str) -> bool:
    """metadata 의 ``imported_filename`` 필드로 사용자 업로드 세션 판별."""
    meta = storage.load_metadata(sid) or {}
    return bool(meta.get("imported_filename"))


def _annotate_for_session(sid: str) -> dict:
    """play-codegen 진입 직전 자동 호출용 — annotate 결과 dict 반환.

    실패는 silent (annotation 없이도 codegen 원본 그대로 실행 가능). 호출자가
    응답에 합쳐 사용자에게 노출.

    **Imported 세션 (사용자 .py 업로드) 은 annotate 스킵** — 사용자의 의도된
    스크립트를 휴리스틱이 변형하는 부수 효과 차단. stale `original_annotated.py`
    존재 시 제거.
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
        r = annotator.annotate_script(str(src), str(dst))
        return {
            "injected": r.injected,
            "examined_clicks": r.examined_clicks,
            "triggers": r.triggers,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("[annotate auto] %s — %s", sid, e)
        return {"injected": 0, "examined_clicks": 0, "triggers": [], "skipped": str(e)}


@router.post("/sessions/{sid}/play-codegen", status_code=201)
def play_codegen(sid: str) -> dict:
    """codegen 원본 ``original.py`` 를 호스트에서 그대로 실행 (TR.7, headed).

    실행 직전 자동으로 annotate 를 수행해 hover-needing click 앞에 hover 라인을
    주입한 ``original_annotated.py`` 를 만들고 그것을 우선 실행 (prefer_annotated).
    Annotate 결과 (injected 수 + trigger 목록) 도 응답에 함께 노출.
    """
    _ensure_session_not_recording(sid, "play-codegen")
    host_dir = str(storage.session_dir(sid))

    # (β) annotate 자동 — 정적 휴리스틱으로 hover 주입.
    annotate_summary = _annotate_for_session(sid)

    try:
        result = _run_codegen_replay_impl(host_session_dir=host_dir)
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
            detail="original.py / regression_test.py 둘 다 없음",
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
            detail="regression_test.py 없음 — Play with LLM 미실행",
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
            detail=f"LLM 분석 호출 실패: {e}",
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
