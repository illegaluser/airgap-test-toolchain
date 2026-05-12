"""replay_service.orchestrator 회귀 — D17 (2026-05-11) .py 일원화 이후의 신규
진입점 ``run_script`` 와 만료 감지 helper ``probe_verify_url`` 의 핵심 분기.

D17 이전의 ``run_bundle`` / bundle 흐름은 제거됨. 본 테스트는 *현재 살아있는*
함수의 분기 중 외부 의존 없이 검증 가능한 영역만 다룬다:

- ``run_script`` script 파일 부재 → EXIT_SYS_ERROR
- ``probe_verify_url`` Playwright import 실패 → "error"

실제 브라우저 동작 (probe 의 expired/valid 판정, run_script 의 subprocess 호출)
은 e2e/integration 테스트 영역.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from replay_service import orchestrator
from replay_service.orchestrator import (
    EXIT_SEED_EXPIRED,
    EXIT_SYS_ERROR,
    probe_verify_url,
    run_script,
)


# ─────────────────────────────────────────────────────────────────────────
# run_script
# ─────────────────────────────────────────────────────────────────────────


def test_run_script_missing_file_returns_sys_error(tmp_path: Path):
    """script_path 가 실재 파일 아니면 EXIT_SYS_ERROR 반환 + run_log 에 사유 기록."""
    out_dir = tmp_path / "out"
    code = run_script(
        script_path=tmp_path / "does_not_exist.py",
        out_dir=out_dir,
    )
    assert code == EXIT_SYS_ERROR

    # run_log.jsonl 에 system_error 이벤트 기록 확인.
    log_path = out_dir / "run_log.jsonl"
    assert log_path.is_file()
    lines = [json.loads(L) for L in log_path.read_text(encoding="utf-8").splitlines() if L.strip()]
    assert any(rec.get("event") == "system_error" for rec in lines)


def test_run_script_writes_exit_code_file(tmp_path: Path):
    """out_dir 에 exit code 파일이 기록되어 외부 호출자가 종료 코드 확인 가능."""
    out_dir = tmp_path / "out"
    run_script(script_path=tmp_path / "missing.py", out_dir=out_dir)
    # _write_exit 의 출력 파일이 정확히 어떤 이름인지 확인.
    candidates = list(out_dir.glob("exit*"))
    assert candidates, f"out_dir 에 exit 관련 파일 없음 — {list(out_dir.iterdir())}"


def test_run_script_unknown_profile_returns_seed_expired(tmp_path: Path, monkeypatch):
    """alias 지정인데 카탈로그에 없으면 EXIT_SEED_EXPIRED — script 존재 여부와 무관."""
    script = tmp_path / "noop.py"
    script.write_text("# minimal", encoding="utf-8")

    # AUTH_PROFILES_DIR 를 빈 디렉토리로 격리 — 어떤 alias 도 매치 안 됨.
    profiles_dir = tmp_path / "empty-profiles"
    profiles_dir.mkdir()
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(profiles_dir))

    out_dir = tmp_path / "out"
    code = run_script(
        script_path=script,
        out_dir=out_dir,
        alias="not-registered",
    )
    assert code == EXIT_SEED_EXPIRED


# ─────────────────────────────────────────────────────────────────────────
# probe_verify_url
# ─────────────────────────────────────────────────────────────────────────


def test_probe_returns_error_when_playwright_import_fails(monkeypatch):
    """Playwright import 자체가 실패하면 'error' — 호출자가 EXIT_SYS_ERROR 분기."""
    # sync_playwright import 시 ImportError 던지도록 sys.modules 조작.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    result = probe_verify_url("https://example.com/dashboard", storage_path=None)
    assert result == "error"


def test_probe_returns_error_on_unexpected_runtime_exception(monkeypatch):
    """sync_playwright() 가 예외를 던지면 'error' — 함수 외부로 leak 안 됨."""
    class _Boom:
        def __enter__(self):
            raise RuntimeError("test-induced playwright failure")
        def __exit__(self, *a):
            return False

    def _fake_sync_playwright():
        return _Boom()

    # playwright.sync_api 의 sync_playwright 를 fake 로 패치.
    import playwright.sync_api as pwsync
    monkeypatch.setattr(pwsync, "sync_playwright", _fake_sync_playwright)
    result = probe_verify_url("https://example.com/dashboard", storage_path=None)
    assert result == "error"


# ─────────────────────────────────────────────────────────────────────────
# 보조 헬퍼 — regex / utc_iso / 비로그인 분기
# ─────────────────────────────────────────────────────────────────────────


def test_login_path_pattern_matches_common_login_urls():
    """probe 가 final_url 에서 인증 페이지 redirect 를 감지하는 패턴."""
    p = orchestrator._LOGIN_PATH_PATTERN
    for url in (
        "https://x/login",
        "https://x/signin?next=/d",
        "https://x/auth/sso",
        "https://x/SSO/start",
    ):
        assert p.search(url), f"{url!r} 매치 실패"


def test_login_path_pattern_does_not_match_dashboard_or_loginhint():
    """false positive 방지 — /loginhint 같은 단어 경계 케이스."""
    p = orchestrator._LOGIN_PATH_PATTERN
    for url in ("https://x/dashboard", "https://x/loginhint", "https://x/home"):
        assert not p.search(url), f"{url!r} 가 잘못 매치됨"


def test_utc_iso_returns_z_terminated_iso8601():
    """``_utc_iso()`` 형식 = YYYY-MM-DDTHH:MM:SSZ (Z 끝)."""
    import re
    s = orchestrator._utc_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", s), s


def test_run_script_no_alias_skips_auth_probe(tmp_path: Path, monkeypatch):
    """alias 가 빈 문자열이면 비로그인 분기 — auth_probe 이벤트 발생 안 함."""
    script = tmp_path / "noop.py"
    script.write_text("# minimal", encoding="utf-8")
    out_dir = tmp_path / "out"

    # subprocess wrapper 만 격리 — 비로그인 분기 통과 확인이 목적.
    monkeypatch.setattr(orchestrator, "_run_script_wrapper", lambda **kw: 0)

    rc = run_script(script, out_dir, alias="")  # 빈 문자열 = 비로그인
    assert rc == orchestrator.EXIT_OK

    log_text = (out_dir / "run_log.jsonl").read_text(encoding="utf-8")
    assert "auth_probe" not in log_text, "비로그인인데 auth_probe 이벤트 발생"
