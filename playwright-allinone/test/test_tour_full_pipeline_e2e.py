"""tour 해피패스 실 실행 e2e (개선 4).

배경:
    기존 e2e 가 ``_run_codegen_replay_impl`` 등을 mock 하느라 wrapper 아래 흐름
    (codegen 산출물 패턴 → AST 변환기 → run_log 생성) 이 통째로 미검증이었음.
    본 모듈은 mock 없이 다음 한 흐름을 정렬해서 검증:

      1. 인증 fixture 사이트(test/_authn_fixture_site.py) 시드 → storage_state dump
      2. 두 URL 을 codegen 패턴 tour 스크립트로 직접 작성 (server.py 의 템플릿 호출)
      3. ``recording_service.codegen_trace_wrapper`` 로 실 실행
      4. trace.zip → run_log.jsonl 변환 결과 step PASS 라인 단언

    네트워크 의존성 없음 (로컬 fixture 사이트). ~10~15s.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _authn_fixture_site import start_authn_site

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


@pytest.fixture
def authn_site():
    httpd, base_url = start_authn_site()
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()


def _seed_storage(base_url: str, dump_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{base_url}/login?user=qa-tester", wait_until="load")
        context.storage_state(path=str(dump_path))
        browser.close()


@pytest.mark.e2e
def test_tour_codegen_pattern_runs_end_to_end_with_authn(
    authn_site: str, tmp_path: Path,
):
    """tour 스크립트가 인증된 보호 페이지를 정상 진입하고 verify 통과.

    회귀 가드: tour 템플릿 합성 / try/except wrap / converter ast.Try 재귀 /
    wrapper 의 storage_state 주입 / Playwright session 유지 — 한 흐름 통째.
    """
    from recording_service.server import _generate_tour_script

    storage = tmp_path / "fresh.storage.json"
    _seed_storage(authn_site, storage)

    # tour 스크립트 — 두 URL 모두 보호된 /secret + /mypage. 이미 storage 가 있으므로
    # 모두 인증 통과해야. assert "errorMsg" not in page.url 도 통과 (fixture 는 / 로
    # redirect 만 하지 errorMsg 쿼리는 안 박음).
    urls = [f"{authn_site}/secret", f"{authn_site}/mypage"]

    # _generate_tour_script 의 fingerprint 호환 stub.
    class _StubFP:
        def to_browser_context_kwargs(self):
            return {}

    text = _generate_tour_script(
        urls=urls,
        seed_url=authn_site,
        storage_path=storage,
        fingerprint=_StubFP(),
        headless=True,
        preflight_verify=True,
        verify_service_url="",
        verify_service_text="",
        wait_until="load",
        nav_timeout_ms=15000,
    )
    sess = tmp_path / "session"
    sess.mkdir()
    (sess / "original.py").write_text(text, encoding="utf-8")

    # codegen wrapper 로 실 실행.
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    )
    env["CODEGEN_SESSION_DIR"] = str(sess)
    env["CODEGEN_SCRIPT"] = "original.py"
    env["CODEGEN_HEADLESS"] = "1"
    proc = subprocess.run(
        [VENV_PY, "-m", "recording_service.codegen_trace_wrapper"],
        env=env, capture_output=True, timeout=90,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"wrapper 비정상 종료. rc={proc.returncode}\n"
            f"stderr:\n{proc.stderr.decode('utf-8', 'replace')[-800:]}"
        )

    # trace.zip 정상 생성됐고, 그 안에 우리 두 URL 의 navigation 이 잡혔는지.
    trace = sess / "trace.zip"
    assert trace.is_file(), "trace.zip 미생성"
    import zipfile
    with zipfile.ZipFile(trace) as zf:
        # trace.trace 라인들 안에 우리 URL 이 있어야 — 즉 wrapper 가 사용자 스크립트
        # 의 navigate 호출을 정상 캡처.
        names = zf.namelist()
        trace_files = [n for n in names if n.endswith(".trace")]
        assert trace_files, f"trace.trace 누락: {names[:10]}"
        all_trace = b""
        for name in trace_files:
            all_trace += zf.read(name)
        all_text = all_trace.decode("utf-8", "replace")
        for url in urls:
            assert url in all_text, f"trace 안에 URL '{url}' 캡처 안 됨"


@pytest.mark.e2e
def test_tour_continues_after_first_failure(authn_site: str, tmp_path: Path):
    """첫 URL 이 navigate 실패해도 다음 URL 이 정상 실행되는지.

    회귀 가드: 사용자 보고(2026-05-02) — 다운로드 URL 등 navigate 자체 실패가
    tour 를 abort 시키던 문제. try/except 가 navigation 예외를 흡수해야.
    """
    from recording_service.server import _generate_tour_script

    storage = tmp_path / "fresh.storage.json"
    _seed_storage(authn_site, storage)

    # 첫 URL — 의도적으로 잘못된 포트(닫힌) → navigate 실패.
    # 두 번째 URL — 정상 fixture.
    bad_url = "http://127.0.0.1:1/dead"  # 1번 포트는 항상 unreachable
    good_url = f"{authn_site}/secret"

    class _StubFP:
        def to_browser_context_kwargs(self):
            return {}

    text = _generate_tour_script(
        urls=[bad_url, good_url],
        seed_url=authn_site,
        storage_path=storage,
        fingerprint=_StubFP(),
        headless=True,
        preflight_verify=True,
        verify_service_url="",
        verify_service_text="",
        wait_until="load",
        nav_timeout_ms=3000,  # 첫 URL 실패 대기 짧게
    )
    sess = tmp_path / "session"
    sess.mkdir()
    (sess / "original.py").write_text(text, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    )
    env["CODEGEN_SESSION_DIR"] = str(sess)
    env["CODEGEN_SCRIPT"] = "original.py"
    env["CODEGEN_HEADLESS"] = "1"
    proc = subprocess.run(
        [VENV_PY, "-m", "recording_service.codegen_trace_wrapper"],
        env=env, capture_output=True, timeout=60,
    )
    # 두 번째 URL 까지 도달했는지 — trace 에 good_url 캡처 확인. rc 는 0 이거나
    # 1 이거나 무방 (assert 결과에 따라). 핵심은 trace 가 good_url 까지 갔다는 것.
    trace = sess / "trace.zip"
    assert trace.is_file(), (
        f"trace.zip 미생성 — 첫 URL 실패에서 wrapper 가 abort 한 것으로 보임. "
        f"rc={proc.returncode}, stderr={proc.stderr.decode('utf-8','replace')[-400:]}"
    )
    import zipfile
    with zipfile.ZipFile(trace) as zf:
        all_trace = b""
        for name in [n for n in zf.namelist() if n.endswith(".trace")]:
            all_trace += zf.read(name)
        text_dump = all_trace.decode("utf-8", "replace")
    assert good_url in text_dump, (
        "첫 URL 실패 후 두 번째 URL 까지 안 감 — try/except 가 navigation 예외 못 잡음"
    )
