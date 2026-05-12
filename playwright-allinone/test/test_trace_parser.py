"""trace_parser 단위 테스트 — 합성 trace.zip 으로 검증.

실제 Playwright tracing 산출물 대신 미니멀한 trace.zip 을 직접 만들어
parser 의 핵심 동작 (action 추출, screencast-frame 매칭, screenshot 저장,
JSONL 직렬화) 을 검증한다. Playwright 버전 의존성 격리.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from recording_shared.trace_parser import parse_trace, _save_screenshot


def _build_trace_zip(
    dst: Path,
    *,
    events: list[dict],
    resources: dict[str, bytes],
) -> Path:
    """events → trace.trace JSONL, resources → resources/<sha1> 로 zip 생성."""
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "trace.trace",
            "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
        )
        for sha1, data in resources.items():
            zf.writestr(f"resources/{sha1}", data)
    return dst


def _jpeg_bytes() -> bytes:
    """최소 유효 JPEG (Pillow 가 해독 가능한 1x1 흰색)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(buf, format="JPEG")
    return buf.getvalue()


def test_parse_trace_extracts_actions_in_order(tmp_path: Path):
    trace = _build_trace_zip(
        tmp_path / "trace.zip",
        events=[
            {"type": "before", "callId": "1", "method": "page.goto",
             "params": {"url": "https://x.test/"}, "startTime": 100},
            {"type": "screencast-frame", "sha1": "S1", "timestamp": 105},
            {"type": "after", "callId": "1", "endTime": 110, "error": None},
            {"type": "before", "callId": "2", "method": "frame.click",
             "params": {"selector": "#login"}, "startTime": 120},
            {"type": "screencast-frame", "sha1": "S2", "timestamp": 125},
            {"type": "after", "callId": "2", "endTime": 130, "error": None},
        ],
        resources={"S1": _jpeg_bytes(), "S2": _jpeg_bytes()},
    )
    out_log = tmp_path / "codegen_run_log.jsonl"
    out_dir = tmp_path / "codegen_screenshots"
    n = parse_trace(trace, out_run_log=out_log, out_screenshots_dir=out_dir)
    assert n == 2
    lines = out_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1["step"] == 1
    assert rec1["action"] == "goto"
    assert rec1["target"] == "https://x.test/"
    assert rec1["status"] == "PASS"
    assert rec2["action"] == "click"
    assert rec2["target"] == "#login"
    # 스크린샷 파일이 디스크에 저장됐는지
    assert (out_dir / rec1["screenshot"]).is_file()
    assert (out_dir / rec2["screenshot"]).is_file()


def test_parse_trace_marks_fail_when_after_has_error(tmp_path: Path):
    trace = _build_trace_zip(
        tmp_path / "trace.zip",
        events=[
            {"type": "before", "callId": "1", "method": "frame.click",
             "params": {"selector": "#missing"}, "startTime": 100},
            {"type": "screencast-frame", "sha1": "S1", "timestamp": 105},
            {"type": "after", "callId": "1", "endTime": 130,
             "error": {"name": "TimeoutError", "message": "Timeout 30000ms exceeded"}},
        ],
        resources={"S1": _jpeg_bytes()},
    )
    out_log = tmp_path / "rl.jsonl"
    out_dir = tmp_path / "shots"
    n = parse_trace(trace, out_run_log=out_log, out_screenshots_dir=out_dir)
    assert n == 1
    rec = json.loads(out_log.read_text(encoding="utf-8").strip())
    assert rec["status"] == "FAIL"
    assert "Timeout" in rec["error"]
    # FAIL step 도 스크린샷 저장
    assert (out_dir / rec["screenshot"]).is_file()
    assert "fail" in rec["screenshot"]


def test_parse_trace_filters_noise_methods(tmp_path: Path):
    """newContext / newPage / close 등 internal 호출은 제외."""
    trace = _build_trace_zip(
        tmp_path / "trace.zip",
        events=[
            {"type": "before", "callId": "n1", "method": "browser.newContext",
             "params": {}, "startTime": 90},
            {"type": "after", "callId": "n1", "endTime": 91, "error": None},
            {"type": "before", "callId": "n2", "method": "browserContext.newPage",
             "params": {}, "startTime": 92},
            {"type": "after", "callId": "n2", "endTime": 93, "error": None},
            {"type": "before", "callId": "1", "method": "page.goto",
             "params": {"url": "https://x"}, "startTime": 100},
            {"type": "after", "callId": "1", "endTime": 110, "error": None},
            {"type": "before", "callId": "n3", "method": "browserContext.close",
             "params": {}, "startTime": 200},
            {"type": "after", "callId": "n3", "endTime": 201, "error": None},
        ],
        resources={},
    )
    out_log = tmp_path / "rl.jsonl"
    out_dir = tmp_path / "shots"
    n = parse_trace(trace, out_run_log=out_log, out_screenshots_dir=out_dir)
    assert n == 1
    rec = json.loads(out_log.read_text(encoding="utf-8").strip())
    assert rec["action"] == "goto"


def test_parse_trace_handles_missing_zip(tmp_path: Path):
    n = parse_trace(
        tmp_path / "nonexistent.zip",
        out_run_log=tmp_path / "rl.jsonl",
        out_screenshots_dir=tmp_path / "shots",
    )
    assert n == 0


def test_parse_trace_handles_empty_trace(tmp_path: Path):
    trace = _build_trace_zip(tmp_path / "trace.zip", events=[], resources={})
    n = parse_trace(
        trace,
        out_run_log=tmp_path / "rl.jsonl",
        out_screenshots_dir=tmp_path / "shots",
    )
    assert n == 0


def test_parse_trace_supports_action_event_format(tmp_path: Path):
    """Playwright 신버전의 단일 'action' 이벤트 형식."""
    trace = _build_trace_zip(
        tmp_path / "trace.zip",
        events=[
            {"type": "action", "id": "a1", "apiName": "page.fill",
             "params": {"selector": "#email", "value": "x@y"},
             "startTime": 100, "endTime": 105, "error": None},
            {"type": "screencast-frame", "sha1": "S1", "timestamp": 106},
        ],
        resources={"S1": _jpeg_bytes()},
    )
    out_log = tmp_path / "rl.jsonl"
    out_dir = tmp_path / "shots"
    n = parse_trace(trace, out_run_log=out_log, out_screenshots_dir=out_dir)
    assert n == 1
    rec = json.loads(out_log.read_text(encoding="utf-8").strip())
    assert rec["action"] == "fill"
    assert rec["target"] == "#email"
    assert rec["status"] == "PASS"


def test_save_screenshot_falls_back_to_jpeg_when_pil_fails(tmp_path: Path):
    """비-이미지 바이트가 들어와도 jpeg 로 떨어뜨림 (silent)."""
    saved = _save_screenshot(b"not-an-image", tmp_path / "shot", prefer_png=True)
    assert saved is not None
    assert saved.suffix in (".jpeg", ".png")
    assert saved.is_file()
