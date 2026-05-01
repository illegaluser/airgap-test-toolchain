"""Playwright ``trace.zip`` 을 읽어 *액션별 결과 + 스크린샷* 으로 변환.

목적: codegen 원본 재생 후 ``trace.zip`` (Playwright tracing 산출물) 을
LLM 모드의 ``run_log.jsonl`` 과 동일한 형식으로 normalize. UI 의 Run Log
카드가 두 모드를 같은 표 컴포넌트로 노출할 수 있게 한다.

trace.zip 구조 (Playwright 1.5x 기준):
    trace.trace             — JSONL (action 이벤트 + screencast-frame 이벤트)
    trace.network           — 네트워크 이벤트 (본 모듈 미사용)
    resources/<sha1>        — 바이너리 (screencast frame JPEG / 다운로드 등)

본 모듈 출력:
    <session_dir>/codegen_run_log.jsonl
    <session_dir>/codegen_screenshots/step_<n>_<status>.png   (또는 .jpeg)

각 record 스키마는 LLM 모드의 ``run_log.jsonl`` 과 동일:
    {step, action, target, status, ts, [screenshot]}
heal_stage 는 codegen 에서 항상 없음 (none) — frontend 가 표 렌더 시 default.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# Pillow 미설치 환경 fallback. screencast frame 은 원래 JPEG 라 PNG 변환은 선택.
try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover — 환경 의존
    _PIL_AVAILABLE = False


@dataclass
class _Action:
    """단일 액션 (before+after 페어). callId 또는 단일 action id 로 식별."""
    call_id: str
    method: str
    target: str
    start_time: float
    end_time: float
    error: Optional[str]

    @property
    def status(self) -> str:
        return "FAIL" if self.error else "PASS"


def _extract_target(method: str, params: dict[str, Any] | None) -> str:
    """method/params 에서 사람이 보기 좋은 target 문자열 추출.

    - goto/url-like: params['url']
    - click/fill/etc: params['selector']
    - 그 외: 첫 번째 문자열 값 또는 빈 문자열
    """
    if not params:
        return ""
    for k in ("url", "selector", "name", "key", "text"):
        v = params.get(k)
        if isinstance(v, str):
            return v
    # fallback — 첫 string value
    for v in params.values():
        if isinstance(v, str):
            return v
    return ""


def _normalize_method(raw: str) -> str:
    """Playwright trace 의 method 표기를 사용자 친화적으로 정리.

    예: 'frame.click' → 'click', 'page.goto' → 'goto', 'browserContext.newPage'
    → 'newPage'.
    """
    if not raw:
        return ""
    if "." in raw:
        return raw.rsplit(".", 1)[-1]
    return raw


# 사용자 에게 의미 없는 internal 호출은 run-log 에서 제외 (잡음 감소).
_NOISE_METHODS = frozenset({
    "newcontext",
    "newpage",
    "close",
    "setviewportsize",
    "setdefaulttimeout",
    "setdefaultnavigationtimeout",
    "tracingstart",
    "tracingstop",
})


def _is_noise(method_norm: str) -> bool:
    return method_norm.lower() in _NOISE_METHODS


def _read_trace_events(zf: zipfile.ZipFile) -> list[dict]:
    """trace.zip 안의 ``trace.trace`` 를 JSONL 로 파싱."""
    candidates = [n for n in zf.namelist() if n.endswith("trace.trace") or n == "trace.trace"]
    if not candidates:
        return []
    events: list[dict] = []
    with zf.open(candidates[0]) as f:
        for raw in f.read().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                events.append(ev)
    return events


def _collect_actions(events: list[dict]) -> list[_Action]:
    """트레이스 이벤트에서 액션 리스트를 추출.

    Playwright trace 포맷은 버전에 따라 (a) before/after 페어 또는 (b) 단일
    'action' 이벤트로 변형됨 — 둘 다 처리.
    """
    by_call: dict[str, dict] = {}
    actions: list[_Action] = []

    for ev in events:
        ty = ev.get("type")
        if ty == "before":
            cid = ev.get("callId") or ""
            by_call[cid] = ev
        elif ty == "after":
            cid = ev.get("callId") or ""
            before = by_call.pop(cid, None)
            if before is None:
                continue
            method = _normalize_method(
                before.get("method") or before.get("apiName") or ""
            )
            if _is_noise(method):
                continue
            target = _extract_target(method, before.get("params"))
            err_obj = ev.get("error")
            err_msg: Optional[str] = None
            if isinstance(err_obj, dict):
                err_msg = err_obj.get("message") or err_obj.get("name")
            elif isinstance(err_obj, str) and err_obj:
                err_msg = err_obj
            actions.append(_Action(
                call_id=cid,
                method=method,
                target=target,
                start_time=float(before.get("startTime") or 0),
                end_time=float(ev.get("endTime") or 0),
                error=err_msg,
            ))
        elif ty == "action":
            # newer single-event format
            method = _normalize_method(
                ev.get("apiName") or ev.get("method") or ""
            )
            if _is_noise(method):
                continue
            target = _extract_target(method, ev.get("params"))
            err_obj = ev.get("error")
            err_msg = None
            if isinstance(err_obj, dict):
                err_msg = err_obj.get("message") or err_obj.get("name")
            actions.append(_Action(
                call_id=str(ev.get("id") or ev.get("callId") or len(actions)),
                method=method,
                target=target,
                start_time=float(ev.get("startTime") or 0),
                end_time=float(ev.get("endTime") or 0),
                error=err_msg,
            ))

    actions.sort(key=lambda a: (a.end_time, a.start_time))
    return actions


def _collect_screencast_frames(events: list[dict]) -> list[tuple[float, str]]:
    """screencast-frame 이벤트의 (timestamp, sha1) 리스트 반환 (시간순)."""
    frames: list[tuple[float, str]] = []
    for ev in events:
        if ev.get("type") != "screencast-frame":
            continue
        sha1 = ev.get("sha1")
        ts = ev.get("timestamp")
        if isinstance(sha1, str) and isinstance(ts, (int, float)):
            frames.append((float(ts), sha1))
    frames.sort(key=lambda x: x[0])
    return frames


def _pick_frame_after(
    frames: list[tuple[float, str]], end_time: float
) -> Optional[str]:
    """end_time 직후의 frame sha1 반환. 없으면 가장 최근 frame, 그것도 없으면 None."""
    if not frames:
        return None
    for ts, sha1 in frames:
        if ts >= end_time:
            return sha1
    # end_time 이후 frame 없으면 마지막 frame fallback
    return frames[-1][1]


def _read_resource(zf: zipfile.ZipFile, sha1: str) -> Optional[bytes]:
    """resources/<sha1> 또는 resources/sha1.{ext} 패턴으로 검색."""
    for name in zf.namelist():
        if not name.startswith("resources/"):
            continue
        base = name.split("/", 1)[1]
        if base == sha1 or base.startswith(sha1 + ".") or base == sha1 + ".jpeg":
            with zf.open(name) as f:
                return f.read()
    return None


def _save_screenshot(
    img_bytes: bytes, dst: Path, *, prefer_png: bool
) -> Optional[Path]:
    """이미지 바이트를 dst 에 저장. Pillow 가 있고 prefer_png 면 PNG 로 변환."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if prefer_png and _PIL_AVAILABLE:
        try:
            img = Image.open(io.BytesIO(img_bytes))
            png_path = dst.with_suffix(".png")
            img.save(png_path, format="PNG")
            return png_path
        except Exception as e:  # noqa: BLE001
            log.warning("[trace-parser] PNG 변환 실패 — JPEG 그대로 저장: %s", e)
    # PIL 없거나 변환 실패 — 원본 JPEG 그대로
    jpeg_path = dst.with_suffix(".jpeg")
    jpeg_path.write_bytes(img_bytes)
    return jpeg_path


def parse_trace(
    trace_zip: Path,
    *,
    out_run_log: Path,
    out_screenshots_dir: Path,
    prefer_png: bool = True,
) -> int:
    """``trace.zip`` 을 파싱해 codegen_run_log.jsonl + 스크린샷 디렉토리 생성.

    Returns:
        성공적으로 기록된 step 수. 0 이면 trace 가 비어있거나 파싱 실패.
    """
    if not trace_zip.is_file():
        log.warning("[trace-parser] trace.zip 없음: %s", trace_zip)
        return 0

    out_run_log.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(trace_zip, "r") as zf:
            events = _read_trace_events(zf)
            if not events:
                log.warning("[trace-parser] trace.trace 가 비었거나 누락됨")
                return 0
            actions = _collect_actions(events)
            frames = _collect_screencast_frames(events)
            written = 0
            with out_run_log.open("w", encoding="utf-8") as f_out:
                for idx, act in enumerate(actions, start=1):
                    shot_field: Optional[str] = None
                    sha1 = _pick_frame_after(frames, act.end_time)
                    if sha1 is not None:
                        img = _read_resource(zf, sha1)
                        if img is not None:
                            shot_path = out_screenshots_dir / (
                                f"step_{idx}_{act.status.lower()}"
                            )
                            saved = _save_screenshot(
                                img, shot_path, prefer_png=prefer_png
                            )
                            if saved is not None:
                                shot_field = saved.name
                    rec = {
                        "step": idx,
                        "action": act.method,
                        "target": act.target,
                        "status": act.status,
                        "ts": act.end_time,
                    }
                    if act.error:
                        rec["error"] = act.error
                    if shot_field:
                        rec["screenshot"] = shot_field
                    f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
            return written
    except (zipfile.BadZipFile, OSError) as e:
        log.exception("[trace-parser] trace.zip 파싱 실패: %s", e)
        return 0
