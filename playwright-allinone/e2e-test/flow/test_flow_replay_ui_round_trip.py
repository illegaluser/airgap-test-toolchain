"""C group — Replay UI daemon round-trip 1건.

목적: Replay UI 의 HTTP API 표면 (POST /api/scripts → POST /api/runs/script →
GET /api/runs/{id} 폴링) 이 daemon spawn 후 정상 동작하는지 가드. 실제 UI
플럼빙 회귀 (마크업 변경, FastAPI 라우트 회귀 등) 의 첫 번째 신호.

본 슈트는 *최소* C 그룹 패턴 — 단일 round-trip 만 검증. 회귀 .py 자체는 fixture
HTML 위 1-line 시나리오로 단순화 (Replay UI 의 *daemon 흐름* 만 검증, executor
로직은 B 그룹이 가드).

설계 트레이드오프:
  - 비용: 20-30s (uvicorn 부팅 + chromium 실행).
  - pre-push 5분 한도에서 다른 슈트 + 자신 = OK.
  - PLAN §5 C 그룹 entry. 이 슈트가 통과해야 다른 C 그룹이 의미를 가짐.

설계 근거: PLAN_E2E_REWRITE.md §5 그룹 C.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_PLAYWRIGHT = Path(__file__).resolve().parent.parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


def _pick_free_port() -> int:
    """OS 가 할당한 free port — 18094-18098 e2e 영역 회수 후 영구 데몬 (18092/18093/18099) 회피."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, deadline_s: float) -> bool:
    """daemon 이 ``port`` 에서 listen 할 때까지 대기. deadline 초과 시 False."""
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def replay_ui_daemon(tmp_path_factory):
    """Replay UI daemon 1회 spawn. module-scoped — 같은 파일 내 테스트들 공유.

    환경 격리:
      - MONITOR_HOME = tmp 디렉토리 (scripts/, runs/, profiles/ 자동 생성)
      - PYTHONPATH 에 shared / replay-ui 추가
    """
    monitor_home = tmp_path_factory.mktemp("monitor_home_e2e")
    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["MONITOR_HOME"] = str(monitor_home)
    env["PYTHONPATH"] = (
        f"{REPO_PLAYWRIGHT / 'shared'}{os.pathsep}"
        f"{REPO_PLAYWRIGHT / 'replay-ui'}{os.pathsep}"
        f"{env.get('PYTHONPATH', '')}"
    )

    cmd = [
        VENV_PY, "-m", "uvicorn",
        "replay_service.server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--workers", "1",
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(REPO_PLAYWRIGHT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )

    try:
        if not _wait_for_port(port, deadline_s=15.0):
            proc.terminate()
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            pytest.fail(f"Replay UI daemon 부팅 실패 (port={port}):\n{stderr[:500]}")

        yield base_url, monitor_home
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.flow
def test_replay_ui_uploads_and_runs_minimal_script(replay_ui_daemon):
    """단일 round-trip — Replay UI 가 minimal .py 를 받아 실행 후 exit 0 보고."""
    base_url, _ = replay_ui_daemon

    # 1. minimal .py — chromium 띄우고 about:blank 갔다 닫기. 외부 의존 0.
    script_src = (
        "from playwright.sync_api import sync_playwright\n"
        "def test_minimal():\n"
        "    with sync_playwright() as p:\n"
        "        b = p.chromium.launch(headless=True)\n"
        "        ctx = b.new_context()\n"
        "        page = ctx.new_page()\n"
        "        page.goto('about:blank')\n"
        "        b.close()\n"
        "if __name__ == '__main__':\n"
        "    test_minimal()\n"
        "    print('OK')\n"
    )

    # 2. POST /api/scripts — 멀티파트 업로드 (field name "file" — FastAPI UploadFile 기대).
    script_name = "c_group_minimal.py"
    boundary = "----c-group-test-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{script_name}"\r\n'
        f"Content-Type: text/x-python\r\n\r\n"
        f"{script_src}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/scripts", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()  # 201 응답, 본문은 abstract

    # 3. POST /api/runs/script — RunScriptRequest 모델 그대로 (script_name + slow_mo_ms).
    start_body = json.dumps({
        "script_name": script_name,
        "headed": False,
        "slow_mo_ms": 0,
        "alias": None,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/runs/script", data=start_body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        start_resp = json.loads(r.read().decode("utf-8"))
    run_id = start_resp.get("run_id")
    assert run_id, f"run 시작 실패: {start_resp}"

    # 4. 폴링 — exit_code 보고까지 (최대 60s, 일반 chromium 부팅 + about:blank ~10s).
    deadline = time.time() + 60.0
    final = None
    while time.time() < deadline:
        with urllib.request.urlopen(f"{base_url}/api/runs/{run_id}", timeout=10) as r:
            run = json.loads(r.read().decode("utf-8"))
        if run.get("state") in ("done", "failed", "cancelled"):
            final = run
            break
        time.sleep(0.5)

    assert final is not None, f"run 미완료 (60s 초과). run_id={run_id}"
    assert final.get("state") == "done", (
        f"run state != done: {final}\n"
        f"Replay UI daemon 의 script 실행 path 회귀 가능"
    )
    assert final.get("exit_code") == 0, (
        f"run exit_code != 0: {final}"
    )
