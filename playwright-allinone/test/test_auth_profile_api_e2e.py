"""auth-profile API End-to-End (Tier 2).

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §9 + 사용자 e2e 요구

전략:
    실제 recording-service 데몬을 *별도 포트* (18094) 로 spawn 한 뒤 HTTP 로
    auth-profile 5개 엔드포인트 + recording_start 통합을 검증.

    - PATH stub `playwright` 가 ``open`` / ``codegen`` / ``--version`` 호출을
      가짜 동작으로 대체 — 실 외부 의존성 없이도 시드/녹화 흐름 동작.
    - 로컬 fake 서비스 (login + mypage) 가 verify_profile 의 service-side
      검증 대상.
    - ``AUTH_PROFILE_VERIFY_HEADLESS=1`` 로 verify 단계 headless (CI 친화).

검증 영역:
    - GET    /auth/profiles                       (P3.2)
    - POST   /auth/profiles/seed                  (P3.3) — background thread + state
    - GET    /auth/profiles/seed/{sid}            (P3.4)
    - POST   /auth/profiles/{name}/verify         (P3.5)
    - DELETE /auth/profiles/{name}                (P3.6)
    - POST   /recording/start { auth_profile }    (P3.7) — verify gate + 메타 박힘
    - 만료 / not-found / 머신 불일치 분기
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
# 데몬이 spawn 하는 ``playwright open`` / ``codegen`` 을 우리가 통제하기 위해
# PATH 에 stub 디렉토리를 prepend. stub 은 ``--save-storage`` / ``--output``
# 다음 인자에 지정된 경로에 fake 산출물을 쓰고 즉시 exit 0.
#
# Note: ``sync_playwright`` 같은 Python API 는 PATH stub 의 영향을 받지 않으므로
# verify_profile 단계는 *실제* Playwright 가 fake 서비스로 navigate.

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
# Local fake "service with login" — verify_profile 대상
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
    """별도 포트 (18094) 로 데몬 spawn — Tier 3 의 18093 과 격리."""
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} 가 이미 사용 중 — Tier 2 e2e 스킵")

    rec_root = tmp_path_factory.mktemp("api_e2e_rec")
    auth_root = tmp_path_factory.mktemp("api_e2e_auth")

    env = os.environ.copy()
    env["PATH"] = str(stub_path_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["RECORDING_HOST_ROOT"] = str(rec_root)
    env["AUTH_PROFILES_DIR"] = str(auth_root)
    env["AUTH_PROFILE_VERIFY_HEADLESS"] = "1"  # CI 친화 — verify 단계 headless

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
            pytest.skip(f"Tier 2 daemon spawn 실패 (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("Tier 2 daemon healthz 대기 timeout")

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
    """전체 라이프사이클 (seed → list → verify → recording-start → delete)."""

    def test_initial_list_empty(self, daemon):
        r = httpx.get(f"{daemon['base']}/auth/profiles", timeout=5.0)
        assert r.status_code == 200
        assert r.json() == []

    def test_full_lifecycle(self, daemon, fake_service):
        base = daemon["base"]
        name = "tier2-life"
        fake = fake_service["base"]

        # 1. 시드 시작.
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload(name, fake),
            timeout=5.0,
        )
        assert r.status_code == 201, r.text
        seed_sid = r.json()["seed_sid"]
        assert r.json()["state"] == "running"

        # 2. 폴링 — stub 이 즉시 종료, verify 가 실 Playwright 로 fake 서비스 검증.
        final = _poll_seed_until_done(base, seed_sid)
        assert final["state"] == "ready", f"poll={final}"
        assert final["phase"] == "ready"
        assert "seed done" in final["message"]
        assert final["profile_name"] == name

        # 3. 카탈로그에 등록되었는지.
        r = httpx.get(f"{base}/auth/profiles", timeout=5.0)
        profs = r.json()
        names = [p["name"] for p in profs]
        assert name in names
        rec = next(p for p in profs if p["name"] == name)
        assert rec["chips_supported"] is True
        assert rec["last_verified_at"] is not None

        # 4. 명시적 verify (positive).
        r = httpx.post(
            f"{base}/auth/profiles/{name}/verify",
            params={"naver_probe": "false"},
            timeout=15.0,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["service_ms"] is not None

        # 5. /recording/start with auth_profile — verify gate 통과 후 codegen 시작.
        # codegen stub 은 즉시 exit 라 stop 호출은 생략 (분리 검증 대상).
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

        # 6. 세션 메타에 auth_profile 박혔는지.
        r = httpx.get(f"{base}/recording/sessions/{sid}", timeout=5.0)
        assert r.json()["auth_profile"] == name

        # 7. 삭제.
        r = httpx.delete(f"{base}/auth/profiles/{name}", timeout=5.0)
        assert r.status_code == 204

        # 8. 사라진 것 확인.
        r = httpx.get(f"{base}/auth/profiles", timeout=5.0)
        assert name not in [p["name"] for p in r.json()]

    def test_seed_without_verify_text_uses_url_access_check(self, daemon, fake_service):
        """검증 텍스트 생략 시 검증 URL 접근 성공만으로 약한 verify 를 수행."""
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
    """만료 / 미발견 / 잘못된 입력 분기."""

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
        """post-review fix: auth 검증 실패 시 pending 세션이 registry 에 남지 않아야 함.

        이전 동작: registry.create() → auth 검증 → 실패하면 HTTPException 만 raise.
                  세션은 state=pending 으로 남음 (GET /recording/sessions 에 노출).
        수정 후 : auth 검증을 registry.create *전에* 수행 → 실패시 세션 생성 자체 안 함.
        """
        base = daemon["base"]
        # 사전 상태 — 세션 카운트 baseline.
        r0 = httpx.get(f"{base}/recording/sessions", timeout=5.0)
        before = len(r0.json())

        # 실패 유도 — unknown profile.
        r = httpx.post(
            f"{base}/recording/start",
            json={
                "target_url": "https://example.com/",
                "auth_profile": "ghost-profile-xyz",
            },
            timeout=5.0,
        )
        assert r.status_code == 404

        # 사후 — 세션 카운트 증가하지 않아야 함 (orphan 없음).
        r1 = httpx.get(f"{base}/recording/sessions", timeout=5.0)
        after = len(r1.json())
        assert after == before, (
            f"orphan 세션 잔존 — before={before} after={after}. "
            f"auth 검증 실패 시 세션이 생성되면 안 됨."
        )

    def test_seed_invalid_name_completes_with_error(self, daemon, fake_service):
        """잘못된 이름은 background thread 에서 InvalidProfileNameError → state=error."""
        base = daemon["base"]
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload("../traversal", fake_service["base"]),
            timeout=5.0,
        )
        # POST 자체는 201 (background thread 시작). 폴링으로 error 확인.
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
    """storage 파일 손상 → verify 가 실패 → recording_start 가 409."""

    def test_corrupted_storage_yields_409_on_recording_start(
        self, daemon, fake_service,
    ):
        base = daemon["base"]
        name = "tier2-expire"

        # 1. 정상 시드.
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload(name, fake_service["base"]),
            timeout=5.0,
        )
        seed_sid = r.json()["seed_sid"]
        final = _poll_seed_until_done(base, seed_sid)
        assert final["state"] == "ready"

        # 2. storage 파일 손상 — verify 시 service text 안 보이게.
        storage = daemon["auth_root"] / f"{name}.storage.json"
        # 쿠키 모두 제거 → fake 서비스가 redirect 함 → "환영합니다" 미노출.
        storage.write_text(
            json.dumps({"cookies": [], "origins": []}),
            encoding="utf-8",
        )

        # 3. recording_start → verify 게이트가 막아 409.
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
    """post-review fix — 재생 시 auth 만료 → 502 가 아니라 409 + 구조화 detail."""

    def test_play_codegen_with_expired_profile_returns_409(
        self, daemon, fake_service,
    ):
        """``/experimental/sessions/{sid}/play-codegen`` 가 만료 시 409 반환.

        시나리오:
          1) 정상 시드 + 녹화 (codegen) → 세션 생성 + metadata.json 에 auth_profile.
          2) storage 손상 (만료 시뮬레이션).
          3) play-codegen 호출 → 502 (이전) 가 아니라 409 + reason="profile_expired".
        """
        base = daemon["base"]
        name = "tier2-replay-expire"
        fake = fake_service["base"]

        # 1) 시드.
        r = httpx.post(
            f"{base}/auth/profiles/seed",
            json=_seed_payload(name, fake),
            timeout=5.0,
        )
        seed_sid = r.json()["seed_sid"]
        final = _poll_seed_until_done(base, seed_sid)
        assert final["state"] == "ready"

        # 2) recording 시작 → codegen stub 이 즉시 종료, stop 까지 가서 세션이
        #    state=done 으로 마감되도록 한다.
        r = httpx.post(
            f"{base}/recording/start",
            json={"target_url": f"{fake}/mypage", "auth_profile": name},
            timeout=15.0,
        )
        assert r.status_code == 201
        sid = r.json()["id"]

        # codegen subprocess 가 즉시 exit 하므로 stop 호출 시 output_size=0.
        # 그래도 메타에 auth_profile 은 남는다 (start 시점에 박힌 것).
        httpx.post(f"{base}/recording/stop/{sid}", timeout=15.0)

        # 3) storage 손상 → fake 서비스 verify 실패 유도.
        storage = daemon["auth_root"] / f"{name}.storage.json"
        storage.write_text(
            json.dumps({"cookies": [], "origins": []}),
            encoding="utf-8",
        )

        # 4) play-codegen 호출 — 메타의 auth_profile 자동 매칭 → verify 실패 →
        #    ReplayAuthExpiredError → router 의 409 변환.
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
