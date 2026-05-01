"""codegen_trace_wrapper 통합 테스트 — 진짜 Chromium 실행으로 trace.zip 생성 검증.

local file:// fixture HTML 한 페이지를 navigate 하는 최소 original.py 를 만들고,
``python -m recording_service.codegen_trace_wrapper`` 로 실행한 뒤 trace.zip 이
생성됐는지 확인한다. headless 로 돌려야 CI 환경에서도 통과.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


@pytest.mark.e2e
def test_wrapper_generates_trace_zip(tmp_path: Path):
    """원본 스크립트 → 래퍼 실행 → trace.zip 생성."""
    fixture_html = tmp_path / "page.html"
    fixture_html.write_text(
        "<!doctype html><html><body><h1>hello</h1></body></html>",
        encoding="utf-8",
    )

    sess_dir = tmp_path / "session"
    sess_dir.mkdir()
    (sess_dir / "original.py").write_text(
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    browser = p.chromium.launch(headless=True)\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        f"    page.goto('file://{fixture_html}')\n"
        "    page.wait_for_load_state('domcontentloaded')\n"
        "    context.close()\n"
        "    browser.close()\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    )
    env["CODEGEN_SESSION_DIR"] = str(sess_dir)
    env["CODEGEN_SCRIPT"] = "original.py"

    proc = subprocess.run(
        [VENV_PY, "-m", "recording_service.codegen_trace_wrapper"],
        env=env, capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"래퍼 실패: stderr={proc.stderr.decode('utf-8', 'replace')[-500:]}"
    )
    trace = sess_dir / "trace.zip"
    assert trace.is_file(), "trace.zip 미생성"
    # zip 이 비어있지 않은지 — 최소 trace.trace 한 파일은 있어야 함
    import zipfile
    with zipfile.ZipFile(trace, "r") as zf:
        names = zf.namelist()
    assert any(n.endswith("trace.trace") for n in names), \
        f"trace.trace 누락. 파일들: {names[:10]}"
