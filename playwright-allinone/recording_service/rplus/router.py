"""R-Plus endpoints — ``/experimental/sessions/{sid}/...``.

`recording_service.server` only includes this router when ``RPLUS_ENABLED=1``.
The handler bodies are identical to the code that lived in `server.py` during
R-MVP, but the hook names (``_run_replay_impl`` etc.) have been moved into this
module, so monkeypatch targets are now ``recording_service.rplus.router``.
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
# Branch points exposed as module-level functions so tests can inject deterministic
# results without actually calling docker exec / Ollama. These are the same hooks
# that used to live in server.py — relocated here, so tests now monkeypatch
# `recording_service.rplus.router` instead.

def _run_enrich_impl(
    *, scenario: list[dict], target_url: str,
    page_title: Optional[str] = None, inventory_block: Optional[str] = None,
):
    """TR.5 monkeypatch hook — unit tests return a fake result without calling Ollama."""
    return enricher.enrich_recording(
        scenario=scenario,
        target_url=target_url,
        page_title=page_title,
        inventory_block=inventory_block,
    )


def _run_diff_analysis_impl(
    *, original_py: str, regression_py: str, unified_diff: str,
):
    """Item 4 (UI improvement) monkeypatch hook — branch point for unit tests."""
    return enricher.analyze_codegen_vs_regression(
        original_py=original_py,
        regression_py=regression_py,
        unified_diff=unified_diff,
    )


def _run_codegen_replay_impl(*, host_session_dir: str):
    """Hook to run the codegen original ``original.py`` on the host (monkeypatch target)."""
    return replay_proxy.run_codegen_replay(host_session_dir=host_session_dir)


def _run_llm_play_impl(*, host_session_dir: str, project_root: str):
    """Hook to run the 14-DSL ``scenario.json`` through the zero_touch_qa executor."""
    return replay_proxy.run_llm_play(
        host_session_dir=host_session_dir,
        project_root=project_root,
    )


def _project_root() -> str:
    """Project root containing the zero_touch_qa package — recording_service's parent directory."""
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent.parent)


def _registry_lookup(sid: str):
    """Look up the in-memory registry of the server module lazily.

    Imported inside the function so the R-Plus router does not hard-couple to
    server. Avoids the server.py → rplus.router → server circular import.
    """
    from ..server import _registry
    return _registry.get(sid)


# ── Request / response models ───────────────────────────────────────────────

class EnrichReq(BaseModel):
    page_title: Optional[str] = Field(None, description="Page title (when present, strengthens the context)")
    inventory_block: Optional[str] = Field(
        None,
        description="Phase 1 grounding inventory marker block (optional). Same pattern as srs_text prepend.",
    )


class CompareReq(BaseModel):
    doc_dsl: list[dict] = Field(..., description="Doc-DSL to compare against (chat-mode output or hand-written).")
    threshold: float = Field(
        comparator.DEFAULT_FUZZY_THRESHOLD,
        description="Fuzzy match threshold (0-1). Default 0.7.",
    )
    doc_label: str = Field("doc-DSL", description="Column label in the HTML report")
    rec_label: str = Field("recording-DSL", description="Column label in the HTML report")


# ── Endpoints ───────────────────────────────────────────────────────────────

def _ensure_session_not_recording(sid: str, kind: str):
    """Shared guard — session must exist and not be recording."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state == session.STATE_RECORDING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{kind} not allowed while recording (state={sess.state}). "
                f"Stop codegen first, then try again."
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
    """Detect a user-uploaded session via the metadata's ``imported_filename`` field."""
    meta = storage.load_metadata(sid) or {}
    return bool(meta.get("imported_filename"))


def _annotate_for_session(sid: str) -> dict:
    """Helper called automatically right before play-codegen — returns the annotate result dict.

    Failures are silent (codegen original can run as is without annotation). The caller
    merges the result into the response so it surfaces to the user.

    **Imported sessions (user .py uploads) skip annotate** — heuristics must not
    mutate the user's intended script. Removes any stale `original_annotated.py`.
    """
    from .. import annotator
    host_dir = storage.session_dir(sid)
    src = host_dir / "original.py"
    if not src.is_file():
        return {"injected": 0, "examined_clicks": 0, "triggers": [], "skipped": "no original.py"}
    dst = host_dir / "original_annotated.py"
    if _is_imported_session(sid):
        # Run the uploaded script as is — clear any stale annotated copy
        if dst.is_file():
            try:
                dst.unlink()
            except OSError:  # noqa: BLE001
                pass
        return {
            "injected": 0,
            "examined_clicks": 0,
            "triggers": [],
            "skipped": "imported script — annotator bypassed (preserve user intent)",
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
    """Run the codegen original ``original.py`` directly on the host (TR.7, headed).

    Right before running, automatically annotate to inject hover lines before
    hover-needing clicks, producing ``original_annotated.py`` and preferring it
    (prefer_annotated). The annotate result (injected count + trigger list) is
    surfaced in the response too.
    """
    _ensure_session_not_recording(sid, "play-codegen")
    host_dir = str(storage.session_dir(sid))

    # (β) auto annotate — static heuristic injects hover.
    annotate_summary = _annotate_for_session(sid)

    try:
        result = _run_codegen_replay_impl(host_session_dir=host_dir)
    except ReplayAuthExpiredError as e:
        # Post-review fix — auth-profile expired/missing returns 409 with a structured
        # detail (not 502), so the UI can branch into the expiration modal + [Re-seed].
        log.warning("[/experimental/play-codegen] %s — auth expired: %s", sid, e)
        # ``e.detail`` may already include a ``reason`` key, so the spread order matters.
        # Put our key last so ``reason="profile_expired"`` is not overwritten by an
        # inner detail's reason (e.g. ``verify_failed``).
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
    """Run the converted 14-DSL ``scenario.json`` through the zero_touch_qa executor (headed).

    Full 14-DSL functionality (3-stage healing / fallback_targets / verify /
    mock_status / mock_data, etc.) plus headed playback. More robust against
    selector drift than the codegen original (LocalHealer + Dify LLM healing) —
    but requires a converted scenario.json, so state=done is mandatory.
    """
    _ensure_session_not_recording(sid, "play-llm")
    sess = _registry_lookup(sid)
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"play-llm requires a converted (state=done) session. "
                f"current state={sess.state}"
            ),
        )
    host_dir = str(storage.session_dir(sid))
    try:
        result = _run_llm_play_impl(
            host_session_dir=host_dir,
            project_root=_project_root(),
        )
    except ReplayAuthExpiredError as e:
        # Post-review fix — auth-profile expired/missing → 409 with structured detail.
        # Lets the UI branch into the expiration modal + [Re-seed].
        log.warning("[/experimental/play-llm] %s — auth expired: %s", sid, e)
        # ``e.detail`` may already include a ``reason`` key, so the spread order matters.
        # Put our key last so ``reason="profile_expired"`` is not overwritten by an
        # inner detail's reason (e.g. ``verify_failed``).
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
    """Back-infer the recorded scenario into IEEE 829-lite Markdown (TR.5 R-Plus)."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"back-inference requires a converted (state=done) session. current state={sess.state}",
        )

    scenario = storage.load_scenario(sid)
    if not scenario:
        raise HTTPException(
            status_code=409,
            detail=f"session {sid} scenario.json is empty or missing.",
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
    """Compare the recorded 14-DSL with the user-supplied doc-DSL across 5 categories (TR.6 R-Plus)."""
    sess = _registry_lookup(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    if sess.state != session.STATE_DONE:
        raise HTTPException(
            status_code=409,
            detail=f"compare requires a converted (state=done) session. current state={sess.state}",
        )
    if not req.doc_dsl:
        raise HTTPException(status_code=400, detail="doc_dsl is empty.")

    rec_dsl = storage.load_scenario(sid)
    if not rec_dsl:
        raise HTTPException(status_code=409, detail=f"session {sid} scenario.json is missing.")

    if not (0.0 <= req.threshold <= 1.0):
        raise HTTPException(status_code=400, detail="threshold must be in 0.0-1.0.")

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
    """Serve the compare result HTML report directly (for the UI's new-tab entry)."""
    p = storage.session_dir(sid) / "doc_comparison.html"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="comparison report not generated")
    return FileResponse(str(p), media_type="text/html")


# ── Item 4 — codegen original ↔ LLM healed regression diff ──────────────────

@router.get("/sessions/{sid}/diff-codegen-vs-llm", include_in_schema=False)
def get_diff_codegen_vs_llm(sid: str) -> dict:
    """Compare codegen ``original.py`` with the LLM-healed ``regression_test.py``.

    User flow: after Play with LLM, diff the auto-generated regression_test.py
    against the original → verify intent matches → download → adopt into the
    regression suite.

    Response:
        - left_path / right_path / left_content / right_content
        - unified_diff: difflib.unified_diff result text
        - left_exists / right_exists
        - 404 if neither file exists.
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
    """Use the LLM (Ollama) to semantically analyze the codegen original ↔ regression_test.py diff.

    Provides information more useful than a one-dimensional unified diff
    (inferred selector swap intent / risk assessment / regression-adoption
    recommendation). Returns markdown.

    POST because the Ollama call has side effects (tens of seconds of time/cost) —
    inconsistent with GET caching semantics.
    """
    import difflib

    left_p = storage.original_py_path(sid)
    right_p = storage.regression_py_path(sid)
    if not right_p.is_file():
        raise HTTPException(
            status_code=404,
            detail="regression_test.py missing — Play with LLM has not run",
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
