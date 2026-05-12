"""실 subprocess smoke — `python -m zero_touch_qa` 와 codegen wrapper 가 한 번이라도 실제로 실행되는지.

배경 (개선책 #1):
    그동안 e2e 가 ``_run_codegen_replay_impl`` / ``_run_llm_play_impl`` 을 fake 로
    monkeypatch 해서 *subprocess 자체가 한 번도 실행되지 않았음*. 그래서 사용자가
    실제 사용한 첫 시도에서 ``FrozenInstanceError`` 같은 argparse → Config 흐름의
    버그가 즉시 터졌고 110+ e2e 통과한 것과 무관했음.

본 모듈은 mock 없이 진짜 subprocess 를 한 번 띄워 다음을 보장한다:
    - argparse 가 새 인자(`--slow-mo`, `--storage-state-in`, `--headless`) 를 정상 수용
    - Config(frozen) 와의 상호작용에서 deprecated assign 회귀 없음
    - executor 가 최소 시나리오를 실행하고 정상 종료
    - codegen wrapper(``recording_shared.codegen_trace_wrapper``) 가 _install_*
      patch + 사용자 스크립트 실행 흐름이 깨지지 않음

빠른 회귀 가드만 목표 — 깊은 동작 검증은 별도 e2e 가 담당.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


@pytest.fixture
def authn_site():
    """본 smoke 의 navigation 대상 — 작은 인증 fixture 사이트."""
    from _authn_fixture_site import start_authn_site
    httpd, base_url = start_authn_site()
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()


def _env_with_pythonpath() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    )
    return env


@pytest.mark.e2e
def test_zero_touch_qa_executor_subprocess_runs_with_minimal_scenario(
    tmp_path: Path, authn_site: str,
):
    """`python -m zero_touch_qa --mode execute --scenario ... --headless` 가 정상 종료.

    회귀 가드: ``--slow-mo`` 인자를 함께 줘서 frozen Config 직접 assign 사고 재발 시
    즉시 fail. 사용자 보고(2026-05-02)와 동일 인자 셋.
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": authn_site,
         "description": "로그인 폼 진입"},
    ]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")

    cmd = [
        VENV_PY, "-m", "zero_touch_qa",
        "--mode", "execute",
        "--scenario", str(scenario_path),
        "--headless",
        "--slow-mo", "100",
    ]
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    env = _env_with_pythonpath()
    env["ARTIFACTS_DIR"] = str(artifacts)

    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=60)
    if proc.returncode != 0:
        msg = (
            f"executor subprocess 비정상 종료 (rc={proc.returncode}).\n"
            f"stderr 마지막 600자:\n{proc.stderr.decode('utf-8', 'replace')[-600:]}"
        )
        pytest.fail(msg)
    # frozen Config 회귀가 살아있다면 stderr 에 FrozenInstanceError 가 찍힘 — 명시 가드.
    stderr = proc.stderr.decode("utf-8", "replace")
    assert "FrozenInstanceError" not in stderr, stderr[-600:]


@pytest.mark.e2e
def test_codegen_trace_wrapper_subprocess_runs_minimal_script(
    tmp_path: Path, authn_site: str,
):
    """``recording_shared.codegen_trace_wrapper`` 가 1 step 짜리 codegen 스크립트를 실행.

    회귀 가드: wrapper 의 ``_install_launch_overrides`` / 트레이싱 patch 사슬이
    깨지면 즉시 fail. tour 스크립트의 codegen 패턴(``def run(playwright):``) 도 동일
    경로라 같이 보호됨.
    """
    sess = tmp_path / "session"
    sess.mkdir()
    script = sess / "original.py"
    script.write_text(
        "from playwright.sync_api import sync_playwright\n"
        "\n"
        "def run(playwright):\n"
        "    browser = playwright.chromium.launch(headless=True)\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        f"    page.goto({authn_site!r})\n"
        "    context.close()\n"
        "    browser.close()\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    with sync_playwright() as p:\n"
        "        run(p)\n",
        encoding="utf-8",
    )

    env = _env_with_pythonpath()
    env["CODEGEN_SESSION_DIR"] = str(sess)
    env["CODEGEN_SCRIPT"] = "original.py"
    env["CODEGEN_HEADLESS"] = "1"

    proc = subprocess.run(
        [VENV_PY, "-m", "recording_shared.codegen_trace_wrapper"],
        env=env, capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail(
            "wrapper subprocess 비정상 종료 "
            f"(rc={proc.returncode}). stderr:\n"
            f"{proc.stderr.decode('utf-8', 'replace')[-600:]}"
        )
    # trace.zip 이 정상 생성됐는지 — wrapper 핵심 부산물.
    trace_zip = sess / "trace.zip"
    assert trace_zip.is_file(), "trace.zip 미생성 — wrapper 트레이싱 깨짐"
