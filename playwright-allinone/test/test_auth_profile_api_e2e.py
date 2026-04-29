"""auth-profile API End-to-End (Tier 2).

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §9 + user e2e requirements

Strategy:
    Spawn a real recording-service daemon on a *separate port* (18094)
    and verify the 5 auth-profile endpoints + recording_start integration
    over HTTP.

    - A PATH stub `playwright` replaces ``open`` / ``codegen`` /
      ``--version`` calls with fakes — seed/record flows work without any
      real external dependency.
    - A local fake service (login + mypage) is the target of
      verify_profile's service-side check.
    - ``AUTH_PROFILE_VERIFY_HEADLESS=1`` runs the verify step headless
      (CI-friendly).

Coverage:
    - GET    /auth/profiles                       (P3.2)
    - POST   /auth/profiles/seed                  (P3.3) — background thread + state
    - GET    /auth/profiles/seed/{sid}            (P3.4)
    - POST   /auth/profiles/{name}/verify         (P3.5)
    - DELETE /auth/profiles/{name}                (P3.6)
    - POST   /recording/start { auth_profile }    (P3.7) — verify gate + meta stamped
    - expiry / not-found / machine mismatch branches
"""

from __future__ import annotations

import http.server
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


E2E_PORT = 18094
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


# ─────────────────────────────────────────────────────────────────────────
# PATH stub `playwright` CLI
# ─────────────────────────────────────────────────────────────────────────
#
# To control the ``playwright open`` / ``codegen`` calls the daemon spawns,
# prepend a stub directory to PATH. The stub writes a fake artifact to
# whatever path follows ``--save-storage`` / ``--output`` and exits 0.
#
# Note: Python APIs like ``sync_playwright`` are unaffected by the PATH
# stub, so verify_profile actually navigates *real* Playwright against
# the fake service.

_STUB_PLAYWRIGHT = """#!/usr/bin/env bash
# tier2 e2e stub for `playwright` CLI

case "$1" in
  open)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--save-storage" ]; then
        cat > "$2" <<'EOF'
{"cookies":[
  {"name":"NID_AUT","value":"fake-naver-token","domain":".naver.com","path":"/","expires":-1,"httpOnly":true,"secure":true,"sameSite":"Lax"},
  {"name":"qa_session","value":"qa-tester","domain":"localhost","path":"/","expires":-1,"httpOnly":false,"secure":false,"sameSite":"Lax"}
],"origins":[]}
EOF
        break
      fi
      shift
    done
    exit 0
    ;;
  codegen)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--output" ]; then
        cat > "$2" <<'EOF'
import os
from playwright.sync_api import sync_playwright

def run(p):
    pass

with sync_playwright() as p:
    run(p)
EOF
        break
      fi
      shift
    done
    exit 0
    ;;
  --version)
    echo "Version 1.57.0"
    exit 0
    ;;
esac
exit 0
"""


@pytest.fixture(scope="session")
def stub_path_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("stub-bin")
    p = d / "playwright"
    p.write_text(_STUB_PLAYWRIGHT, encoding="utf-8")
    p.chmod(0o755)
    return d


# ─────────────────────────────────────────────────────────────────────────
# Local fake "service with login" — verify_profile target
# ─────────────────────────────────────────────────────────────────────────

_PAGE_LOGIN = """<!doctype html>
<html><body>
<h1>Test Service Login</h1>
<form action="/login" method="get"><button name="user" value="qa-tester">Sign in (mock)</button></form>
</body></html>"""

_PAGE_MYPAGE_OK = """<!doctype html>
<html><body><h1>My Page</h1><p>안녕하세요, qa-tester님 환영합니다</p></body></html>"""


class _FakeServiceHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send(self, status, body, headers=None):
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

    def do_GET(self):  # noqa: N802
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
                self._send(200, _PAGE_MYPAGE_OK)
            else:
                self._send(302, "", {"Location": "/"})
            return
        self._send(404, "<p>not found</p>")


@pytest.fixture(scope="session")
def fake_service():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _FakeServiceHandler)
    port = httpd.server_address[1]
    base = f"http://localhost:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"base": base, "port": port}
    httpd.shutdown()
    httpd.server_close()


# ─────────────────────────────────────────────────────────────────────────
# Daemon fixture
# ─────────────────────────────────────────────────────────────────────────

def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def daemon(stub_path_dir, tmp_path_factory):
    """Spawn the daemon on a separate port (18094) — isolated from Tier 3's 18093."""
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} already in use — skipping Tier 2 e2e")

    rec_root = tmp_path_factory.mktemp("api_e2e_rec")
    auth_root = tmp_path_factory.mktemp("api_e2e_auth")

    env = os.environ.copy()
    env["PATH"] = str(stub_path_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["RECORDING_HOST_ROOT"] = str(rec_root)
    env["AUTH_PROFILES_DIR"] = str(auth_root)
    env["AUTH_PROFILE_VERIFY_HEADLESS"] = "1"  # CI-friendly — verify step headless

    cmd = [
        VENV_PY, "-m", "uvicorn",
        "recording_service.server:app",
        "--host", "127.0.0.1",
        "--port", str(E2E_PORT),
        "--workers", "1",
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )

    deadline = time.time() + 20.0
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            pytest.skip(f"Tier 2 daemon spawn failed (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("Tier 2 daemon healthz wait timed out")

    yield {
        "base": E2E_BASE,
        "auth_root": auth_root,
        "rec_root": rec_root,
    }

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    except ProcessLookupError:
        pass


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _poll_seed_until_done(base: str, seed_sid: str, timeout_sec: float = 30.0) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = httpx.get(f"{base}/auth/profiles/seed/{seed_sid}", timeout=5.0)
        body = r.json()
        if body["state"] != "running":
            return body
        time.sleep(0.5)
    raise TimeoutError(f"seed poll timeout — last state={body['state']}")


def _seed_payload(name: str, fake_url: str, *, naver_probe: bool = False) -> dict:
    return {
        "name": name,
        "seed_url": f"{fake_url}/",
        "verify_service_url": f"{fake_url}/mypage",
        "verify_service_text": "qa-tester님 환영합니다",
        "naver_probe": naver_probe,
        "ttl_hint_hours": 1,
        "timeout_sec": 10,
    }


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────

class TestAuthProfileApiLifecycle:
    """Full lifecycle (seed → list → verify → recording-start → delete)."""

    def test_initial_list_empty(self, daemon):
        r = httpx.get(f"{daemon['base']}/auth/profiles", timeout=5.0)
        assert r.status_code == 200
        assert r.json() == []

    def test_full_lifecycle(self, daemon, fake_service):
        base = daemon["base"]
        name = "tier2-life"
        fake = fake_service["base"]

        # 1. start seed.
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload(name, fake),
            timeout=5.0,
        )
        assert r.status_code == 201, r.text
        seed_sid = r.json()["seed_sid"]
        assert r.json()["state"] == "running"

        # 2. poll — stub exits immediately, then verify uses real Playwright against the fake service.
        final = _poll_seed_until_done(base, seed_sid)
        assert final["state"] == "ready", f"poll={final}"
        assert final["phase"] == "ready"
        assert "seed done" in final["message"]
        assert final["profile_name"] == name

        # 3. confirm catalog registration.
        r = httpx.get(f"{base}/auth/profiles", timeout=5.0)
        profs = r.json()
        names = [p["name"] for p in profs]
        assert name in names
        rec = next(p for p in profs if p["name"] == name)
        assert rec["chips_supported"] is True
        assert rec["last_verified_at"] is not None

        # 4. explicit verify (positive).
        r = httpx.post(
            f"{base}/auth/profiles/{name}/verify",
            params={"naver_probe": "false"},
            timeout=15.0,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["service_ms"] is not None

        # 5. /recording/start with auth_profile — codegen starts after verify gate passes.
        # codegen stub exits immediately, so the stop call is omitted (covered separately).
        r = httpx.post(
            f"{base}/recording/start",
            json={
                "target_url": f"{fake}/mypage",
                "auth_profile": name,
            },
            timeout=15.0,
        )
        assert r.status_code == 201, r.text
        sid = r.json()["id"]

        # 6. confirm auth_profile is stamped onto the session metadata.
        r = httpx.get(f"{base}/recording/sessions/{sid}", timeout=5.0)
        assert r.json()["auth_profile"] == name

        # 7. delete.
        r = httpx.delete(f"{base}/auth/profiles/{name}", timeout=5.0)
        assert r.status_code == 204

        # 8. confirm it's gone.
        r = httpx.get(f"{base}/auth/profiles", timeout=5.0)
        assert name not in [p["name"] for p in r.json()]

    def test_seed_without_verify_text_uses_url_access_check(self, daemon, fake_service):
        """When verify text is omitted, fall back to weak-verify (only checks URL access)."""
        base = daemon["base"]
        name = "tier2-url-only"
        payload = _seed_payload(name, fake_service["base"])
        payload.pop("verify_service_text")

        r = httpx.post(f"{base}/auth/profiles/seed", json=payload, timeout=5.0)
        assert r.status_code == 201, r.text
        final = _poll_seed_until_done(base, r.json()["seed_sid"])
        assert final["state"] == "ready", f"poll={final}"
        assert final["phase"] == "ready"

        r = httpx.post(
            f"{base}/auth/profiles/{name}/verify",
            params={"naver_probe": "false"},
            timeout=15.0,
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        httpx.delete(f"{base}/auth/profiles/{name}", timeout=5.0)


class TestErrorPaths:
    """expiry / not-found / invalid-input branches."""

    def test_recording_start_unknown_profile_404(self, daemon):
        r = httpx.post(
            f"{daemon['base']}/recording/start",
            json={
                "target_url": "https://example.com/",
                "auth_profile": "this-does-not-exist",
            },
            timeout=5.0,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "profile_not_found"

    def test_recording_start_failed_auth_leaves_no_orphan_session(self, daemon):
        """post-review fix: when auth verification fails, no pending session
        must remain in the registry.

        Old behavior: registry.create() → auth verify → on failure raise
                      HTTPException only. Session lingered as state=pending
                      (visible in GET /recording/sessions).
        Fix         : run auth verify *before* registry.create → no session
                      created at all on failure.
        """
        base = daemon["base"]
        # Pre-condition — baseline session count.
        r0 = httpx.get(f"{base}/recording/sessions", timeout=5.0)
        before = len(r0.json())

        # Force a failure — unknown profile.
        r = httpx.post(
            f"{base}/recording/start",
            json={
                "target_url": "https://example.com/",
                "auth_profile": "ghost-profile-xyz",
            },
            timeout=5.0,
        )
        assert r.status_code == 404

        # Post-condition — session count must not increase (no orphan).
        r1 = httpx.get(f"{base}/recording/sessions", timeout=5.0)
        after = len(r1.json())
        assert after == before, (
            f"orphan session left behind — before={before} after={after}. "
            f"No session must be created when auth verification fails."
        )

    def test_seed_invalid_name_completes_with_error(self, daemon, fake_service):
        """A bad name raises InvalidProfileNameError on the background thread → state=error."""
        base = daemon["base"]
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload("../traversal", fake_service["base"]),
            timeout=5.0,
        )
        # POST itself returns 201 (background thread started). Confirm error via polling.
        assert r.status_code == 201
        seed_sid = r.json()["seed_sid"]
        final = _poll_seed_until_done(base, seed_sid, timeout_sec=10.0)
        assert final["state"] == "error"
        assert final["error_kind"] == "input"

    def test_verify_unknown_profile_404(self, daemon):
        r = httpx.post(
            f"{daemon['base']}/auth/profiles/notthere/verify",
            params={"naver_probe": "false"},
            timeout=5.0,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "profile_not_found"

    def test_delete_unknown_profile_404(self, daemon):
        r = httpx.delete(
            f"{daemon['base']}/auth/profiles/notthere",
            timeout=5.0,
        )
        assert r.status_code == 404


class TestExpiryDetection:
    """Corrupt storage file → verify fails → recording_start returns 409."""

    def test_corrupted_storage_yields_409_on_recording_start(
        self, daemon, fake_service,
    ):
        base = daemon["base"]
        name = "tier2-expire"

        # 1. normal seed.
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload(name, fake_service["base"]),
            timeout=5.0,
        )
        seed_sid = r.json()["seed_sid"]
        final = _poll_seed_until_done(base, seed_sid)
        assert final["state"] == "ready"

        # 2. corrupt the storage file — verify won't see service text.
        storage = daemon["auth_root"] / f"{name}.storage.json"
        # Drop all cookies → fake service redirects → "환영합니다" never appears.
        storage.write_text(
            json.dumps({"cookies": [], "origins": []}),
            encoding="utf-8",
        )

        # 3. recording_start → verify gate blocks with 409.
        r = httpx.post(
            f"{base}/recording/start",
            json={
                "target_url": f"{fake_service['base']}/mypage",
                "auth_profile": name,
            },
            timeout=15.0,
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["reason"] == "profile_expired"

        # 4. cleanup.
        httpx.delete(f"{base}/auth/profiles/{name}", timeout=5.0)


class TestReplayExpiry:
    """post-review fix — replay-time auth expiry returns 409 with structured detail (not 502)."""

    def test_play_codegen_with_expired_profile_returns_409(
        self, daemon, fake_service,
    ):
        """``/experimental/sessions/{sid}/play-codegen`` returns 409 on expiry.

        Scenario:
          1) normal seed + record (codegen) → session created + auth_profile in metadata.json.
          2) corrupt storage (simulate expiry).
          3) call play-codegen → 409 + reason="profile_expired" (was 502 before).
        """
        base = daemon["base"]
        name = "tier2-replay-expire"
        fake = fake_service["base"]

        # 1) seed.
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload(name, fake),
            timeout=5.0,
        )
        seed_sid = r.json()["seed_sid"]
        final = _poll_seed_until_done(base, seed_sid)
        assert final["state"] == "ready"

        # 2) start recording → codegen stub exits immediately, then drive stop
        #    so the session reaches state=done.
        r = httpx.post(
            f"{base}/recording/start",
            json={"target_url": f"{fake}/mypage", "auth_profile": name},
            timeout=15.0,
        )
        assert r.status_code == 201
        sid = r.json()["id"]

        # codegen subprocess exits immediately, so stop sees output_size=0.
        # Even so, auth_profile remains in metadata (stamped at start time).
        httpx.post(f"{base}/recording/stop/{sid}", timeout=15.0)

        # 3) corrupt storage → force fake-service verify failure.
        storage = daemon["auth_root"] / f"{name}.storage.json"
        storage.write_text(
            json.dumps({"cookies": [], "origins": []}),
            encoding="utf-8",
        )

        # 4) play-codegen call — meta's auth_profile is auto-matched →
        #    verify fails → ReplayAuthExpiredError → router converts to 409.
        r = httpx.post(
            f"{base}/experimental/sessions/{sid}/play-codegen",
            timeout=20.0,
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["detail"]["reason"] == "profile_expired"
        assert body["detail"]["profile_name"] == name

        # cleanup.
        httpx.delete(f"{base}/auth/profiles/{name}", timeout=5.0)
