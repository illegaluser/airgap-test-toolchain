#!/usr/bin/env python3
"""P1 E2E — auth_profiles 전체 파이프라인 (사람 입력 없이 자동 실행).

목적:
    seed_profile 의 ``playwright open`` subprocess 부분은 사람 인터랙션이 필요
    하므로 e2e 에선 Playwright Python API 로 *등가의 storage 생성 행위* 를 직접
    수행한 뒤, 나머지 파이프라인 (validate_dump → verify_profile →
    _record_verify → list/get/delete) 의 실 동작을 검증한다.

검증 항목:
    1. P1.4  current_playwright_version / chips_supported_by_runtime
    2. P1.7-등가  Playwright 로 로그인 시뮬레이션 후 storage_state dump
    3. P1.5  validate_dump (naver.com + service 도메인 둘 다 요구)
    4. P1.3  _upsert_profile / list_profiles / get_profile
    5. P1.6  verify_profile (서비스 측 실 검증, headed Playwright)
    6. P1.6  만료 시뮬레이션 (쿠키 제거) → verify 실패 감지
    7. P1.3  delete_profile cleanup

전제:
    - Playwright >=1.54 + Chromium 설치
    - 인터넷 연결 불필요 (로컬 http.server 만 사용)
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse


# ── 로컬 HTTP fixture — "네이버로 로그인되는 서비스" 시뮬레이션 ────────────

_PAGE_LOGIN = """<!doctype html>
<html><body>
<h1>Test Service Login</h1>
<form action="/login" method="get">
  <button name="user" value="qa-tester">Sign in (mock)</button>
</form>
</body></html>"""

_PAGE_MYPAGE_LOGGED_IN = """<!doctype html>
<html><body>
<h1>My Page</h1>
<p>안녕하세요, qa-tester님 환영합니다</p>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass  # 테스트 출력 깔끔하게.

    def _send(self, status: int, body: str, headers: dict | None = None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        body_b = body.encode("utf-8")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_b)))
        self.end_headers()
        self.wfile.write(body_b)

    def _has_session(self) -> bool:
        return "qa_session=" in self.headers.get("Cookie", "")

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler 규약)
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, _PAGE_LOGIN)
            return
        if url.path == "/login":
            qs = parse_qs(url.query)
            user = qs.get("user", ["unknown"])[0]
            self._send(302, "", {
                "Set-Cookie": f"qa_session={user}; Path=/; HttpOnly",
                "Location": "/mypage",
            })
            return
        if url.path == "/mypage":
            if self._has_session():
                self._send(200, _PAGE_MYPAGE_LOGGED_IN)
            else:
                self._send(302, "", {"Location": "/"})
            return
        self._send(404, "<p>not found</p>")


def _start_local_service() -> tuple[socketserver.TCPServer, int]:
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


# ── 헬퍼 ────────────────────────────────────────────────────────────────

def _step(label: str) -> None:
    print(f"\n→ {label}")


def _ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"  ✓ {label}{suffix}")


def _fail(label: str, detail: str = "") -> int:
    suffix = f" — {detail}" if detail else ""
    print(f"  ✗ {label}{suffix}")
    return 1


# ── e2e 본체 ────────────────────────────────────────────────────────────

def run() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="ttc-e2e-p1-"))
    auth_dir = workdir / "auth-profiles"
    os.environ["AUTH_PROFILES_DIR"] = str(auth_dir)
    print(f"# E2E P1 auth_profiles  (workdir={workdir})")

    # env 설정 후 import (auth_profiles 가 _root() 를 매 호출 새로 읽지만 깔끔하게).
    from zero_touch_qa import auth_profiles as ap
    from zero_touch_qa.auth_profiles import (
        AuthProfile,
        FingerprintProfile,
        ProfileNotFoundError,
        VerifySpec,
        _upsert_profile,
        chips_supported_by_runtime,
        current_machine_id,
        current_playwright_version,
        delete_profile,
        get_profile,
        list_profiles,
        validate_dump,
        verify_profile,
    )
    from playwright.sync_api import sync_playwright

    # 1. 환경 sanity.
    _step("환경 sanity")
    pw_ver = current_playwright_version()
    mid = current_machine_id()
    chips = chips_supported_by_runtime()
    print(f"  playwright={pw_ver}  machine_id={mid}  chips={chips}")
    if not pw_ver:
        return _fail("Playwright 미설치 — e2e 중단")
    if not chips:
        return _fail("Playwright <1.54 — e2e 중단 (D14)")

    # 2. 로컬 서비스 기동.
    _step("로컬 HTTP 서비스 기동")
    httpd, port = _start_local_service()
    base = f"http://localhost:{port}"
    print(f"  base={base}")

    rc = 0
    try:
        # 3. Playwright 로 사용자 로그인 시뮬레이션 + storage 저장.
        _step("Playwright 로 로그인 시뮬레이션 + storage_state dump")
        auth_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(auth_dir, 0o700)
        storage_target = auth_dir / "e2e-tester.storage.json"

        fp = FingerprintProfile.default()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(**fp.to_browser_context_kwargs())
            page = ctx.new_page()
            page.goto(f"{base}/")
            page.click("button[name='user']")
            page.wait_for_url(f"{base}/mypage")
            assert "qa-tester님 환영합니다" in page.content()
            ctx.storage_state(path=str(storage_target), indexed_db=True)
            ctx.close()
            browser.close()
        os.chmod(storage_target, 0o600)
        _ok("storage_state 저장", str(storage_target))

        # 4. fake naver 쿠키 주입 (validate_dump 의 naver.com 요건 충족).
        #    실 OAuth 라운드트립 등가물 — naver 쿠키가 함께 dump 되는 시뮬레이션.
        _step("fake naver.com 쿠키 주입 (OAuth 라운드트립 시뮬레이션)")
        with open(storage_target, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["cookies"].append({
            "name": "NID_AUT",
            "value": "fake_naver_session_token",
            "domain": ".naver.com",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        })
        with open(storage_target, "w", encoding="utf-8") as f:
            json.dump(data, f)
        _ok("naver 쿠키 주입 완료")

        # 5. validate_dump (P1.5).
        _step("validate_dump — 양 도메인 쿠키 존재 확인 (D12)")
        validate_dump(storage_target, ["naver.com", "localhost"])
        _ok("validate_dump 통과")

        # 6. AuthProfile 빌드 + upsert (P1.3).
        _step("AuthProfile 빌드 + 카탈로그 등록")
        prof = AuthProfile(
            name="e2e-tester",
            service_domain="localhost",
            storage_path=storage_target,
            created_at=ap._now_iso(),
            last_verified_at=None,
            ttl_hint_hours=12,
            verify=VerifySpec(
                service_url=f"{base}/mypage",
                service_text="qa-tester님 환영합니다",
                naver_probe=None,  # e2e 에서는 실 naver 안 침
            ),
            fingerprint=fp,
            host_machine_id=current_machine_id(),
            chips_supported=True,
            session_storage_warning=False,
            verify_history=[],
            notes="P1 e2e",
        )
        _upsert_profile(prof)
        _ok("upsert 완료")

        # 7. list_profiles / get_profile.
        _step("list_profiles / get_profile")
        listed = list_profiles()
        if not any(p_.name == "e2e-tester" for p_ in listed):
            return _fail("list 에 등록 안 보임")
        _ok("list_profiles", f"{len(listed)}건")

        loaded = get_profile("e2e-tester")
        _ok("get_profile", f"name={loaded.name}, service_domain={loaded.service_domain}")

        # 8. verify_profile — 실 Playwright 가 /mypage 로 이동 + 텍스트 확인 (P1.6, D9).
        _step("verify_profile (logged-in) — 실 Playwright headed launch")
        ok, detail = verify_profile(loaded, naver_probe=False, timeout_sec=15)
        if not ok:
            return _fail("verify 실패", json.dumps(detail))
        _ok("verify 통과", f"service_ms={detail.get('service_ms')}")

        # 9. last_verified_at 갱신 + verify_history append 확인.
        _step("verify 결과 영속화 확인")
        reloaded = get_profile("e2e-tester")
        if not reloaded.last_verified_at:
            return _fail("last_verified_at 미갱신")
        if len(reloaded.verify_history) < 1:
            return _fail("verify_history append 안 됨")
        _ok("last_verified_at", reloaded.last_verified_at)
        _ok("verify_history", f"{len(reloaded.verify_history)}건")

        # 10. 만료 시뮬레이션 — 서비스 쿠키 제거 후 재검증 → 실패 감지.
        _step("만료 시뮬레이션 (qa_session 쿠키 제거 후 재검증)")
        with open(storage_target, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["cookies"] = [c for c in data["cookies"] if c.get("name") != "qa_session"]
        with open(storage_target, "w", encoding="utf-8") as f:
            json.dump(data, f)
        _ok("qa_session 쿠키 제거")

        ok2, detail2 = verify_profile(reloaded, naver_probe=False, timeout_sec=15)
        if ok2:
            return _fail("만료 후 verify 가 통과 — 잘못된 동작", json.dumps(detail2))
        if detail2.get("fail_reason") != "service_text_not_found":
            return _fail("fail_reason 부정확", json.dumps(detail2))
        _ok("만료 감지", f"fail_reason={detail2.get('fail_reason')}")

        # 11. delete_profile.
        _step("delete_profile cleanup")
        delete_profile("e2e-tester")
        try:
            get_profile("e2e-tester")
            return _fail("삭제 후에도 get_profile 성공 — 잘못된 동작")
        except ProfileNotFoundError:
            pass
        if storage_target.exists():
            return _fail("storage 파일이 unlink 안 됨")
        _ok("카탈로그 + storage 모두 정리됨")

        print("\n✅ P1 E2E 풀 사이클 PASS")
    except Exception as e:  # 의도적 광범위 — 운영 파이프라인 모든 사고 잡기
        import traceback
        traceback.print_exc()
        rc = _fail("e2e 중 예외", repr(e))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return rc


if __name__ == "__main__":
    sys.exit(run())
