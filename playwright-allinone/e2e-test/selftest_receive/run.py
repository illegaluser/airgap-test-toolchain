"""E 그룹 receiving-PC selftest — 받는 PC 첫 실행 시 1회.

가드 대상 (P0):
  - 435ccf6: 휴대용에서 브라우저 안 뜨던 회귀 — Chromium 바이너리 / 권한.
  - 4ccc736: Windows 휴대용 로그인 프로파일 카드 안 뜨던 회귀 — replay_service import.
  - 4969092: Windows 콘솔 인코딩 (recording_service 안정성 측면).
  - 48d1ccd: 받는 PC 차단 5건 — 자산 누락 / 권한 / 경로.

실행 시점: ``Launch-ReplayUI.{bat,command}`` 가 첫 실행 시 호출. 통과 후
``$ROOT/.selftest_done`` 마커 파일을 남겨 다음 실행에선 skip (수동 삭제로 재실행).

환경: 휴대용 bundle context.
  - PYTHONHOME=""
  - PYTHONPATH = $ROOT:$ROOT/site-packages
  - PLAYWRIGHT_BROWSERS_PATH = $ROOT/chromium
  - 실행 파이썬: $ROOT/python/bin/python3 (Mac) 또는 $ROOT/python/python.exe (Windows)

결과: stdout 의 ``[OK] ...`` / ``[FAIL] ...`` 라인. exit 0 = 모두 통과,
non-zero = 적어도 한 가지 실패. launcher 는 실패 시 사용자에게 알림.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _check_python() -> tuple[bool, str]:
    try:
        v = sys.version_info
        return True, f"Python {v.major}.{v.minor}.{v.micro}"
    except Exception as e:
        return False, f"Python check 실패: {e}"


def _check_playwright_module() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True, "playwright module import OK"
    except Exception as e:
        return False, f"playwright import 실패: {e}"


def _check_chromium_binary() -> tuple[bool, str]:
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if not browsers_path:
        return False, "PLAYWRIGHT_BROWSERS_PATH 미설정 — launcher 환경 누락"
    p = Path(browsers_path)
    if not p.exists():
        return False, f"chromium 디렉토리 부재: {p}"
    # chromium-* 또는 chrome 실행파일 흔적 확인.
    has_chrome_dir = any(
        c.name.startswith("chromium") or c.name.startswith("chrome")
        for c in p.iterdir()
    )
    if not has_chrome_dir:
        return False, f"chromium 바이너리 디렉토리 부재 ({p} 안)"
    return True, f"chromium present at {p}"


def _check_replay_service_import() -> tuple[bool, str]:
    try:
        from replay_service import server  # noqa: F401
        # 라우트 1개 이상 등록 확인 — FastAPI app 살아있음.
        routes = [r for r in server.app.routes if str(r.path).startswith("/api/")]
        if not routes:
            return False, "replay_service.server 의 /api routes 0건"
        return True, f"replay_service import OK ({len(routes)} api routes)"
    except Exception as e:
        return False, f"replay_service import 실패: {e}"


def _check_writable_data_dir() -> tuple[bool, str]:
    monitor_home = os.environ.get("MONITOR_HOME", "")
    if not monitor_home:
        return False, "MONITOR_HOME 미설정"
    p = Path(monitor_home)
    try:
        p.mkdir(parents=True, exist_ok=True)
        test_file = p / ".selftest_write_probe"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True, f"data dir 쓰기 OK: {p}"
    except Exception as e:
        return False, f"data dir 쓰기 실패 ({p}): {e}"


def _check_chromium_can_launch() -> tuple[bool, str]:
    """Chromium 을 짧게 launch → 닫기. 회귀 435ccf6 의 직접 가드."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto("about:blank", timeout=10000)
            browser.close()
        return True, "chromium launch + about:blank + close OK"
    except Exception as e:
        return False, f"chromium launch 실패 (휴대용 회귀): {e}"


CHECKS = [
    ("python",            _check_python),
    ("playwright_module", _check_playwright_module),
    ("chromium_binary",   _check_chromium_binary),
    ("replay_service",    _check_replay_service_import),
    ("writable_data_dir", _check_writable_data_dir),
    ("chromium_launch",   _check_chromium_can_launch),
]


def main() -> int:
    # stdout/stderr UTF-8 강제 (Windows CP949 회귀 차단 — 4969092).
    for _stream in (sys.stdout, sys.stderr):
        reconfig = getattr(_stream, "reconfigure", None)
        if reconfig is not None:
            try:
                reconfig(encoding="utf-8", errors="replace")
            except Exception:
                pass

    print("=" * 60)
    print("[selftest_receive] Replay UI 휴대용 — 받는 PC 첫 실행 자가진단")
    print("=" * 60)

    fail_count = 0
    for name, check in CHECKS:
        try:
            ok, msg = check()
        except Exception as e:
            ok, msg = False, f"selftest 예외: {e}"
        prefix = "[OK]  " if ok else "[FAIL]"
        print(f"{prefix} {name}: {msg}")
        if not ok:
            fail_count += 1

    print("=" * 60)
    if fail_count == 0:
        print("[selftest_receive] 통과 — Replay UI 정상 기동 가능")
        return 0
    print(f"[selftest_receive] {fail_count}개 항목 실패 — Replay UI 가 정상 작동 안 할 수 있습니다.")
    print("[selftest_receive] 문제 해결:")
    print("  - chromium 누락 / 권한 문제 → 휴대용 zip 다시 다운로드 + 풀기")
    print("  - python import 실패 → zip 무결성 확인 (다시 받기)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
