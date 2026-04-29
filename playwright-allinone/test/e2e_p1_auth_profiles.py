#!/usr/bin/env python3
"""P1 E2E — full auth_profiles pipeline (runs end-to-end without human input).

Goal:
    seed_profile's ``playwright open`` subprocess needs human interaction,
    so for e2e we use the Playwright Python API to perform the *equivalent
    storage-creation flow* directly, then verify the rest of the pipeline
    (validate_dump → verify_profile → _record_verify → list/get/delete) for
    real.

Coverage:
    1. P1.4  current_playwright_version / chips_supported_by_runtime
    2. P1.7-equivalent  Playwright login simulation, then storage_state dump
    3. P1.5  validate_dump (requires both naver.com and the service domain)
    4. P1.3  _upsert_profile / list_profiles / get_profile
    5. P1.6  verify_profile (real service-side check via headed Playwright)
    6. P1.6  expiry simulation (drop the cookie) → verify failure detection
    7. P1.3  delete_profile cleanup

Requirements:
    - Playwright >=1.54 + Chromium installed
    - No internet access required (only a local http.server)
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


# ── local HTTP fixture — simulates a "service that signs in via naver" ─────

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
        pass  # keep test output clean.

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

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler convention)
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


# ── helpers ─────────────────────────────────────────────────────────────

def _step(label: str) -> None:
    print(f"\n→ {label}")


def _ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"  ✓ {label}{suffix}")


def _fail(label: str, detail: str = "") -> int:
    suffix = f" — {detail}" if detail else ""
    print(f"  ✗ {label}{suffix}")
    return 1


# ── e2e body ────────────────────────────────────────────────────────────

def run() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="ttc-e2e-p1-"))
    auth_dir = workdir / "auth-profiles"
    os.environ["AUTH_PROFILES_DIR"] = str(auth_dir)
    print(f"# E2E P1 auth_profiles  (workdir={workdir})")

    # import after env is set (auth_profiles re-reads _root() each call but cleaner this way).
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

    # 1. environment sanity.
    _step("environment sanity")
    pw_ver = current_playwright_version()
    mid = current_machine_id()
    chips = chips_supported_by_runtime()
    print(f"  playwright={pw_ver}  machine_id={mid}  chips={chips}")
    if not pw_ver:
        return _fail("Playwright not installed — abort e2e")
    if not chips:
        return _fail("Playwright <1.54 — abort e2e (D14)")

    # 2. start local service.
    _step("start local HTTP service")
    httpd, port = _start_local_service()
    base = f"http://localhost:{port}"
    print(f"  base={base}")

    rc = 0
    try:
        # 3. simulate user login with Playwright + save storage.
        _step("Playwright login simulation + storage_state dump")
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
        _ok("storage_state saved", str(storage_target))

        # 4. inject fake naver cookie (satisfies validate_dump's naver.com requirement).
        #    equivalent to a real OAuth round trip — naver cookies dumped alongside.
        _step("inject fake naver.com cookie (OAuth round-trip simulation)")
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
        _ok("naver cookie injected")

        # 5. validate_dump (P1.5).
        _step("validate_dump — confirm cookies for both domains exist (D12)")
        validate_dump(storage_target, ["naver.com", "localhost"])
        _ok("validate_dump passed")

        # 6. AuthProfile build + upsert (P1.3).
        _step("build AuthProfile + register in catalog")
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
                naver_probe=None,  # e2e doesn't hit real naver
            ),
            fingerprint=fp,
            host_machine_id=current_machine_id(),
            chips_supported=True,
            session_storage_warning=False,
            verify_history=[],
            notes="P1 e2e",
        )
        _upsert_profile(prof)
        _ok("upsert complete")

        # 7. list_profiles / get_profile.
        _step("list_profiles / get_profile")
        listed = list_profiles()
        if not any(p_.name == "e2e-tester" for p_ in listed):
            return _fail("not visible in list")
        _ok("list_profiles", f"{len(listed)} entries")

        loaded = get_profile("e2e-tester")
        _ok("get_profile", f"name={loaded.name}, service_domain={loaded.service_domain}")

        # 8. verify_profile — Playwright actually navigates to /mypage and confirms text (P1.6, D9).
        _step("verify_profile (logged-in) — real Playwright headed launch")
        ok, detail = verify_profile(loaded, naver_probe=False, timeout_sec=15)
        if not ok:
            return _fail("verify failed", json.dumps(detail))
        _ok("verify passed", f"service_ms={detail.get('service_ms')}")

        # 9. confirm last_verified_at update + verify_history append.
        _step("confirm verify result was persisted")
        reloaded = get_profile("e2e-tester")
        if not reloaded.last_verified_at:
            return _fail("last_verified_at not updated")
        if len(reloaded.verify_history) < 1:
            return _fail("verify_history not appended")
        _ok("last_verified_at", reloaded.last_verified_at)
        _ok("verify_history", f"{len(reloaded.verify_history)} entries")

        # 10. expiry simulation — remove the service cookie and re-verify → failure detected.
        _step("expiry simulation (drop qa_session cookie, then re-verify)")
        with open(storage_target, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["cookies"] = [c for c in data["cookies"] if c.get("name") != "qa_session"]
        with open(storage_target, "w", encoding="utf-8") as f:
            json.dump(data, f)
        _ok("qa_session cookie removed")

        ok2, detail2 = verify_profile(reloaded, naver_probe=False, timeout_sec=15)
        if ok2:
            return _fail("verify passed after expiry — wrong behavior", json.dumps(detail2))
        if detail2.get("fail_reason") != "service_text_not_found":
            return _fail("fail_reason inaccurate", json.dumps(detail2))
        _ok("expiry detected", f"fail_reason={detail2.get('fail_reason')}")

        # 11. delete_profile.
        _step("delete_profile cleanup")
        delete_profile("e2e-tester")
        try:
            get_profile("e2e-tester")
            return _fail("get_profile still works after delete — wrong behavior")
        except ProfileNotFoundError:
            pass
        if storage_target.exists():
            return _fail("storage file not unlinked")
        _ok("catalog + storage both cleaned")

        print("\n✅ P1 E2E full cycle PASS")
    except Exception as e:  # intentionally broad — catch every operational pipeline issue
        import traceback
        traceback.print_exc()
        rc = _fail("exception during e2e", repr(e))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return rc


if __name__ == "__main__":
    sys.exit(run())
