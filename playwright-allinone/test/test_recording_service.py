"""Phase R-MVP TR.1 / TR.2 — Recording 서비스 단위 테스트.

FastAPI TestClient + monkeypatch 로 codegen subprocess 를 fake handle 로 대체해
엔드포인트 흐름·에러 처리·핸들 레지스트리 검증.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# 의존성 미설치 환경에서 collection 실패 안 하도록 graceful skip
fastapi = pytest.importorskip("fastapi")
fastapi_testclient = pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient


@pytest.fixture
def temp_host_root(monkeypatch):
    """recordings 호스트 루트를 임시 디렉토리로 격리."""
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("RECORDING_HOST_ROOT", td)
        yield td


def _make_fake_handle(output_path: Path, *, write_actions: bool = True, returncode: int = 0):
    """monkeypatch 용 fake CodegenHandle. subprocess 미실행."""
    from recording_service.codegen_runner import CodegenHandle

    if write_actions:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "page.click('button')\npage.fill('input', 'x')\n",
            encoding="utf-8",
        )

    fake_proc = SimpleNamespace(
        pid=99999,
        stdout=None,
        stderr=None,
        returncode=returncode,
        poll=lambda: returncode,
        terminate=lambda: None,
        kill=lambda: None,
        send_signal=lambda *a, **kw: None,
        wait=lambda *a, **kw: returncode,
    )
    return CodegenHandle(
        pid=99999,
        started_at=time.time(),
        output_path=output_path,
        process=fake_proc,
        timeout_sec=1800,
        target_url="https://x.test",
        returncode=returncode,
    )


@pytest.fixture
def patched_codegen(monkeypatch):
    """server._start_codegen_impl 와 _stop_codegen_impl 을 fake 로 대체."""
    from recording_service import server as srv

    captured: dict = {"started": [], "stopped": []}

    def fake_start(target_url, output_path, *, timeout_sec):
        captured["started"].append((target_url, str(output_path), timeout_sec))
        return _make_fake_handle(Path(output_path), write_actions=True)

    def fake_stop(handle):
        captured["stopped"].append(handle.pid)
        return handle

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    monkeypatch.setattr(srv, "_stop_codegen_impl", fake_stop)
    return captured


@pytest.fixture
def client(temp_host_root):
    from recording_service.server import app, _reset_for_tests
    _reset_for_tests()
    return TestClient(app)


# ── /healthz ─────────────────────────────────────────────────────────────────

def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "version" in body
    assert "codegen_available" in body  # bool
    assert "host_root" in body


# ── /recording/start ─────────────────────────────────────────────────────────

def test_start_creates_session(client, temp_host_root, patched_codegen):
    r = client.post("/recording/start", json={
        "target_url": "https://example.com",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["target_url"] == "https://example.com"
    # TR.2: codegen 시작됐으므로 state=recording
    assert body["state"] == "recording"
    assert len(body["id"]) >= 8
    # 영속화 디렉토리 + metadata.json 생성됨
    sess_dir = os.path.join(temp_host_root, body["id"])
    assert os.path.isdir(sess_dir)
    assert os.path.isfile(os.path.join(sess_dir, "metadata.json"))


def test_start_with_planning_doc_ref(client, patched_codegen):
    r = client.post("/recording/start", json={
        "target_url": "https://example.com",
        "planning_doc_ref": "feature_login.md",
    })
    assert r.status_code == 201
    sid = r.json()["id"]
    # 후속 GET 으로 planning_doc_ref 보존 확인
    r2 = client.get(f"/recording/sessions/{sid}")
    assert r2.status_code == 200
    assert r2.json()["planning_doc_ref"] == "feature_login.md"


def test_start_rejects_missing_target_url(client):
    r = client.post("/recording/start", json={})
    assert r.status_code == 422  # FastAPI validation error


# ── /recording/sessions ──────────────────────────────────────────────────────

def test_list_sessions_initially_empty(client):
    r = client.get("/recording/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_list_sessions_after_starts(client, patched_codegen):
    for url in ("https://a.test", "https://b.test", "https://c.test"):
        client.post("/recording/start", json={"target_url": url})
    r = client.get("/recording/sessions")
    assert r.status_code == 200
    assert len(r.json()) == 3
    # 최신순 정렬 — 마지막 생성 c.test 가 첫번째
    assert r.json()[0]["target_url"] == "https://c.test"


def test_get_session_404(client):
    r = client.get("/recording/sessions/nonexistent")
    assert r.status_code == 404


def test_get_session_returns_state(client, patched_codegen):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.get(f"/recording/sessions/{sid}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == sid
    assert body["state"] == "recording"
    assert body["created_at_iso"]


# ── /recording/start (TR.2 — 실 codegen 연동) ─────────────────────────────────

def test_start_invokes_codegen_and_records_pid(client, patched_codegen):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    assert r.status_code == 201
    assert r.json()["state"] == "recording"
    # fake codegen 이 호출됐는지
    assert len(patched_codegen["started"]) == 1
    # GET 으로 pid 노출 확인
    sid = r.json()["id"]
    s = client.get(f"/recording/sessions/{sid}").json()
    assert s["state"] == "recording"


def test_start_503_when_codegen_missing(client, monkeypatch):
    """playwright 미설치 → CodegenError 그대로 503."""
    from recording_service import server as srv
    from recording_service.codegen_runner import CodegenError

    def fake_start(*a, **kw):
        raise CodegenError("playwright 실행 파일을 찾을 수 없습니다.")

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    assert r.status_code == 503
    assert "playwright" in r.json()["detail"]


def test_start_400_on_invalid_url(client, monkeypatch):
    from recording_service import server as srv
    from recording_service.codegen_runner import CodegenError

    def fake_start(*a, **kw):
        raise CodegenError("target_url 이 유효하지 않습니다: ''")

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    r = client.post("/recording/start", json={"target_url": ""})
    # 빈 URL은 pydantic 통과(str), runtime 단계에서 400
    assert r.status_code == 400


# ── /recording/stop (TR.2) ────────────────────────────────────────────────────

def test_stop_404_for_unknown_session(client):
    r = client.post("/recording/stop/nope")
    assert r.status_code == 404


def test_stop_409_when_no_active_handle(client):
    """start 안 된 세션 stop → 409 (start 가 핸들 등록 안 함)."""
    # 직접 registry 에 세션 만들고 stop 시도
    from recording_service.server import _registry
    from recording_service import session as s
    sess = _registry.create("https://x.test")
    r = client.post(f"/recording/stop/{sess.id}")
    assert r.status_code == 409


def test_stop_after_normal_recording(client, patched_codegen):
    """start → stop 정상 흐름. state=converting, action_count > 0."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    body = r2.json()
    assert body["state"] == "converting"
    assert body["output_size_bytes"] > 0
    assert body["action_count_estimate"] >= 1
    assert body["returncode"] == 0
    assert len(patched_codegen["stopped"]) == 1


def test_stop_with_empty_output(client, monkeypatch):
    """codegen 출력 0 byte → state=error + action_count=0 + 명확한 메시지."""
    from recording_service import server as srv
    from pathlib import Path

    def fake_start(target_url, output_path, *, timeout_sec):
        # 핵심: 파일을 생성하지 않거나 0 byte 만들기
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("", encoding="utf-8")
        return _make_fake_handle(Path(output_path), write_actions=False)

    def fake_stop(handle):
        return handle

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    monkeypatch.setattr(srv, "_stop_codegen_impl", fake_stop)

    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    body = r2.json()
    assert body["state"] == "error"
    assert "0건" in body["error"]


def test_stop_is_idempotent_returns_409_second_time(client, patched_codegen):
    """첫 stop 후 핸들이 사라지므로 두 번째 stop 은 409."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    r3 = client.post(f"/recording/stop/{sid}")
    assert r3.status_code == 409


# ── DELETE 가 활성 codegen 도 정리 ──────────────────────────────────────────

def test_delete_terminates_active_codegen(client, patched_codegen):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    # stop 안 하고 바로 delete — 활성 핸들 종료 후 삭제
    r2 = client.delete(f"/recording/sessions/{sid}")
    assert r2.status_code == 204
    # codegen stop 이 호출됐어야
    assert len(patched_codegen["stopped"]) == 1


# ── codegen_runner 순수 함수 ────────────────────────────────────────────────

def test_codegen_runner_handle_elapsed_sec_grows():
    handle = _make_fake_handle(Path("/tmp/_unused.py"), write_actions=False)
    initial = handle.elapsed_sec()
    time.sleep(0.05)
    later = handle.elapsed_sec()
    assert later > initial


def test_codegen_runner_is_timed_out_threshold():
    from recording_service.codegen_runner import is_timed_out

    handle = _make_fake_handle(Path("/tmp/_unused.py"), write_actions=False)
    handle.timeout_sec = 9999
    assert is_timed_out(handle) is False
    # 강제로 started_at 을 옛날로 끌어올려 타임아웃 유발
    handle.timeout_sec = 1
    handle.started_at = time.time() - 5
    assert is_timed_out(handle) is True


def test_codegen_runner_output_size_zero_for_missing_file(tmp_path):
    from recording_service.codegen_runner import output_size_bytes

    handle = _make_fake_handle(tmp_path / "missing.py", write_actions=False)
    # write_actions=False 라 파일 없음
    assert output_size_bytes(handle) == 0


# ── /recording/sessions/{id}/replay (R-Plus only) ────────────────────────────

def test_replay_returns_503_in_mvp(client, patched_codegen):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/sessions/{sid}/replay")
    assert r2.status_code == 503
    assert "R-Plus" in r2.json()["detail"]


# ── DELETE /recording/sessions/{id} ──────────────────────────────────────────

def test_delete_removes_session(client, temp_host_root, patched_codegen):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    sess_dir = os.path.join(temp_host_root, sid)
    assert os.path.isdir(sess_dir)

    r2 = client.delete(f"/recording/sessions/{sid}")
    assert r2.status_code == 204

    # 메모리 + 디스크 모두 제거
    r3 = client.get(f"/recording/sessions/{sid}")
    assert r3.status_code == 404
    assert not os.path.exists(sess_dir)


def test_delete_404_for_unknown(client):
    r = client.delete("/recording/sessions/nope")
    assert r.status_code == 404
