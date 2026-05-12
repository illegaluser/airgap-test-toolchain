"""codegen_trace_wrapper 통합 테스트 — 진짜 Chromium 실행으로 trace.zip 생성 검증.

local file:// fixture HTML 한 페이지를 navigate 하는 최소 original.py 를 만들고,
``python -m recording_shared.codegen_trace_wrapper`` 로 실행한 뒤 trace.zip 이
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


def test_inject_auth_and_fingerprint_applies_storage_state_from_env(monkeypatch):
    """``AUTH_STORAGE_STATE_IN`` env 가 ``new_context()`` kwargs 의 ``storage_state``로 주입된다.

    회귀 가드: 이 주입이 빠지면 imported tour script 가 빈 컨텍스트로 시작해
    로그인 필요 페이지가 모두 로그인 안내로 바운스된다 (사용자 보고 사례).
    """
    from recording_shared.codegen_trace_wrapper import _inject_auth_and_fingerprint

    monkeypatch.setenv("AUTH_STORAGE_STATE_IN", "/tmp/dpg.storage.json")
    kwargs: dict = {}
    _inject_auth_and_fingerprint(kwargs)
    assert kwargs.get("storage_state") == "/tmp/dpg.storage.json"


def test_inject_auth_and_fingerprint_does_not_override_user_storage_state(monkeypatch):
    """사용자가 직접 명시한 ``storage_state`` 는 env 로 덮어쓰지 않음 (의도 보존)."""
    from recording_shared.codegen_trace_wrapper import _inject_auth_and_fingerprint

    monkeypatch.setenv("AUTH_STORAGE_STATE_IN", "/tmp/from-env.json")
    kwargs = {"storage_state": "/user/explicit.json"}
    _inject_auth_and_fingerprint(kwargs)
    assert kwargs["storage_state"] == "/user/explicit.json"


def test_inject_auth_and_fingerprint_applies_viewport_locale_timezone(monkeypatch):
    """fingerprint env 4종이 그대로 매핑된다."""
    from recording_shared.codegen_trace_wrapper import _inject_auth_and_fingerprint

    monkeypatch.setenv("PLAYWRIGHT_VIEWPORT", "1280x800")
    monkeypatch.setenv("PLAYWRIGHT_LOCALE", "ko-KR")
    monkeypatch.setenv("PLAYWRIGHT_TIMEZONE", "Asia/Seoul")
    monkeypatch.setenv("PLAYWRIGHT_COLOR_SCHEME", "dark")
    kwargs: dict = {}
    _inject_auth_and_fingerprint(kwargs)
    assert kwargs["viewport"] == {"width": 1280, "height": 800}
    assert kwargs["locale"] == "ko-KR"
    assert kwargs["timezone_id"] == "Asia/Seoul"
    assert kwargs["color_scheme"] == "dark"


def test_install_launch_overrides_injects_slow_mo(monkeypatch):
    """``CODEGEN_SLOW_MO_MS`` env 가 ``BrowserType.launch()`` kwargs 에 ``slow_mo`` 로 주입된다."""
    from playwright.sync_api import BrowserType

    captured: dict = {}

    def fake_launch(self, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(BrowserType, "launch", fake_launch)
    monkeypatch.setenv("CODEGEN_SLOW_MO_MS", "1500")
    monkeypatch.delenv("CODEGEN_HEADLESS", raising=False)

    from recording_shared.codegen_trace_wrapper import _install_launch_overrides
    _install_launch_overrides()

    # 패치 후 launch 호출 — 우리 패치가 fake_launch 를 감싸 slow_mo 주입.
    BrowserType.launch(None, headless=False)
    assert captured.get("slow_mo") == 1500


def test_install_launch_overrides_respects_user_slow_mo(monkeypatch):
    """사용자가 ``slow_mo`` 를 명시했으면 env 가 덮어쓰지 않는다."""
    from playwright.sync_api import BrowserType

    captured: dict = {}

    def fake_launch(self, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(BrowserType, "launch", fake_launch)
    monkeypatch.setenv("CODEGEN_SLOW_MO_MS", "9999")
    monkeypatch.delenv("CODEGEN_HEADLESS", raising=False)

    from recording_shared.codegen_trace_wrapper import _install_launch_overrides
    _install_launch_overrides()
    BrowserType.launch(None, slow_mo=200)
    assert captured["slow_mo"] == 200


def test_install_launch_overrides_skips_when_no_env(monkeypatch):
    """관련 env 둘 다 없으면 patch 자체를 설치하지 않음 (정상 케이스 비용 0)."""
    from playwright.sync_api import BrowserType

    monkeypatch.delenv("CODEGEN_HEADLESS", raising=False)
    monkeypatch.delenv("CODEGEN_SLOW_MO_MS", raising=False)

    real_launch = BrowserType.launch
    from recording_shared.codegen_trace_wrapper import _install_launch_overrides
    _install_launch_overrides()
    assert BrowserType.launch is real_launch


def test_inject_auth_and_fingerprint_skips_when_no_env(monkeypatch):
    """관련 env 가 없으면 kwargs 를 변형하지 않음."""
    from recording_shared.codegen_trace_wrapper import _inject_auth_and_fingerprint

    for k in ("AUTH_STORAGE_STATE_IN", "PLAYWRIGHT_VIEWPORT", "PLAYWRIGHT_LOCALE",
             "PLAYWRIGHT_TIMEZONE", "PLAYWRIGHT_COLOR_SCHEME"):
        monkeypatch.delenv(k, raising=False)
    kwargs: dict = {"already": "here"}
    _inject_auth_and_fingerprint(kwargs)
    assert kwargs == {"already": "here"}


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
        [VENV_PY, "-m", "recording_shared.codegen_trace_wrapper"],
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
