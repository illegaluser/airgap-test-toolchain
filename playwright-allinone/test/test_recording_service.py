"""Phase R-MVP TR.1 — Recording 서비스 골격 단위 테스트.

FastAPI TestClient 로 엔드포인트 7종 동작 검증. codegen subprocess 는 TR.2 에서
실 구현 — 본 테스트는 stub 응답 / 세션 상태 전환 / CRUD 위주.
"""

from __future__ import annotations

import os
import tempfile

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

def test_start_creates_session(client, temp_host_root):
    r = client.post("/recording/start", json={
        "target_url": "https://example.com",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["target_url"] == "https://example.com"
    assert body["state"] == "pending"
    assert len(body["id"]) >= 8
    # 영속화 디렉토리 + metadata.json 생성됨
    sess_dir = os.path.join(temp_host_root, body["id"])
    assert os.path.isdir(sess_dir)
    assert os.path.isfile(os.path.join(sess_dir, "metadata.json"))


def test_start_with_planning_doc_ref(client):
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


def test_list_sessions_after_starts(client):
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


def test_get_session_returns_state(client):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.get(f"/recording/sessions/{sid}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == sid
    assert body["state"] == "pending"
    assert body["created_at_iso"]


# ── /recording/stop ──────────────────────────────────────────────────────────

def test_stop_404_for_unknown_session(client):
    r = client.post("/recording/stop/nope")
    assert r.status_code == 404


def test_stop_transitions_to_done_in_tr1_stub(client):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    body = r2.json()
    assert body["state"] == "done"
    # TR.1 단계는 stub — TR.2/3 에서 실 변환 추가
    assert "TR.1 stub" in body.get("note", "")


# ── /recording/sessions/{id}/replay (R-Plus only) ────────────────────────────

def test_replay_returns_503_in_mvp(client):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/sessions/{sid}/replay")
    assert r2.status_code == 503
    assert "R-Plus" in r2.json()["detail"]


# ── DELETE /recording/sessions/{id} ──────────────────────────────────────────

def test_delete_removes_session(client, temp_host_root):
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
