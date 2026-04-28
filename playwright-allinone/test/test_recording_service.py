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
    """server._start_codegen_impl, _stop_codegen_impl, _run_convert_impl 을 fake 로 대체.

    /stop 까지 가는 정상 흐름을 위해 변환도 함께 patch — 컨테이너 측 변환을
    시뮬레이션해 host_scenario_path 에 미니멀 scenario.json 을 만든다.
    """
    from recording_service import server as srv
    from recording_service.converter_proxy import ConvertResult

    captured: dict = {"started": [], "stopped": [], "converted": []}

    def fake_start(target_url, output_path, *, timeout_sec):
        captured["started"].append((target_url, str(output_path), timeout_sec))
        return _make_fake_handle(Path(output_path), write_actions=True)

    def fake_stop(handle):
        captured["stopped"].append(handle.pid)
        return handle

    def fake_convert(*, container_session_dir, host_scenario_path):
        # 컨테이너가 scenario.json 을 썼다고 시뮬레이션
        Path(host_scenario_path).parent.mkdir(parents=True, exist_ok=True)
        Path(host_scenario_path).write_text(
            '[{"step":1,"action":"navigate","target":"","value":"https://x.test"},'
            '{"step":2,"action":"click","target":"button","value":""}]',
            encoding="utf-8",
        )
        captured["converted"].append((container_session_dir, host_scenario_path))
        return ConvertResult(
            returncode=0,
            stdout="[convert-only] 2 스텝 변환 + 검증 완료",
            stderr="",
            scenario_path=host_scenario_path,
            scenario_exists=True,
            elapsed_ms=12.3,
        )

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    monkeypatch.setattr(srv, "_stop_codegen_impl", fake_stop)
    monkeypatch.setattr(srv, "_run_convert_impl", fake_convert)
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
    """start → stop 정상 흐름. TR.3 후 state=done, scenario.json 생성."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    body = r2.json()
    # TR.3: 변환까지 성공하면 state=done
    assert body["state"] == "done"
    assert body["step_count"] == 2  # fake_convert 가 2 스텝 시나리오 생성
    assert body["output_size_bytes"] > 0
    assert "scenario_path" in body
    assert len(patched_codegen["stopped"]) == 1
    assert len(patched_codegen["converted"]) == 1


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


# ── TR.3 — docker exec 위임 변환 ──────────────────────────────────────────────

def test_stop_with_convert_returncode_nonzero(client, monkeypatch):
    """변환이 exit code != 0 → state=error + stderr 노출 + 원본 .py 보존."""
    from recording_service import server as srv
    from recording_service.converter_proxy import ConvertResult

    def fake_start(target_url, output_path, *, timeout_sec):
        return _make_fake_handle(Path(output_path), write_actions=True)

    def fake_stop(handle):
        return handle

    def fake_convert(*, container_session_dir, host_scenario_path):
        return ConvertResult(
            returncode=1,
            stdout="",
            stderr="ScenarioValidationError: step[0].action 이 유효하지 않음",
            scenario_path=host_scenario_path,
            scenario_exists=False,
            elapsed_ms=8.0,
        )

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    monkeypatch.setattr(srv, "_stop_codegen_impl", fake_stop)
    monkeypatch.setattr(srv, "_run_convert_impl", fake_convert)

    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    body = r2.json()
    assert body["state"] == "error"
    assert body["returncode"] == 1
    assert "ScenarioValidationError" in body["stderr"]
    assert "원본 original.py 는 보존" in body["error"]


def test_stop_with_converter_proxy_error(client, monkeypatch):
    """docker 미설치 / timeout → ConverterProxyError → state=error."""
    from recording_service import server as srv
    from recording_service.converter_proxy import ConverterProxyError

    def fake_start(target_url, output_path, *, timeout_sec):
        return _make_fake_handle(Path(output_path), write_actions=True)

    def fake_stop(handle):
        return handle

    def fake_convert(*, container_session_dir, host_scenario_path):
        raise ConverterProxyError("docker 실행 파일을 찾을 수 없습니다.")

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    monkeypatch.setattr(srv, "_stop_codegen_impl", fake_stop)
    monkeypatch.setattr(srv, "_run_convert_impl", fake_convert)

    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    body = r2.json()
    assert body["state"] == "error"
    assert "docker" in body["error"]


def test_stop_after_codegen_empty_skips_conversion(client, monkeypatch):
    """codegen 출력 0 byte 시 변환 단계로 진입하지 않는다 (TR.2 가 먼저 가로챔)."""
    from recording_service import server as srv

    convert_calls = []

    def fake_start(target_url, output_path, *, timeout_sec):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("", encoding="utf-8")
        return _make_fake_handle(Path(output_path), write_actions=False)

    def fake_stop(handle):
        return handle

    def fake_convert(*, container_session_dir, host_scenario_path):
        convert_calls.append(1)
        return None

    monkeypatch.setattr(srv, "_start_codegen_impl", fake_start)
    monkeypatch.setattr(srv, "_stop_codegen_impl", fake_stop)
    monkeypatch.setattr(srv, "_run_convert_impl", fake_convert)

    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/recording/stop/{sid}")
    assert r2.status_code == 202
    assert r2.json()["state"] == "error"
    # 0 byte 가 먼저 가로채므로 변환은 호출 안 됨
    assert convert_calls == []


# ── converter_proxy 순수 함수 ────────────────────────────────────────────────

def test_converter_proxy_run_convert_when_docker_missing(monkeypatch):
    """docker 미설치 시 ConverterProxyError. subprocess 실행 안 함."""
    from recording_service import converter_proxy
    from recording_service.converter_proxy import ConverterProxyError

    monkeypatch.setattr(converter_proxy, "is_docker_available", lambda: False)
    with pytest.raises(ConverterProxyError) as excinfo:
        converter_proxy.run_convert(
            container_session_dir="/data/recordings/x",
            host_scenario_path="/tmp/nonexistent/scenario.json",
        )
    assert "docker" in str(excinfo.value)


def test_converter_proxy_run_convert_returns_result(monkeypatch, tmp_path):
    """subprocess.run 을 fake 로 대체해 ConvertResult 형식 검증."""
    from recording_service import converter_proxy

    monkeypatch.setattr(converter_proxy, "is_docker_available", lambda: True)

    fake_completed = SimpleNamespace(
        returncode=0,
        stdout=b"[convert-only] 5 \xec\x8a\xa4\xed\x85\x9d \xeb\xb3\x80\xed\x99\x98 \xec\x99\x84\xeb\xa3\x8c",
        stderr=b"",
    )
    monkeypatch.setattr(converter_proxy.subprocess, "run", lambda *a, **kw: fake_completed)

    # scenario.json 시뮬레이션
    scenario = tmp_path / "scenario.json"
    scenario.write_text("[]", encoding="utf-8")

    result = converter_proxy.run_convert(
        container_session_dir="/data/recordings/x",
        host_scenario_path=str(scenario),
    )
    assert result.returncode == 0
    assert result.scenario_exists is True
    assert "변환 완료" in result.stdout
    assert result.elapsed_ms >= 0


def test_converter_proxy_timeout_raises(monkeypatch):
    """subprocess.TimeoutExpired → ConverterProxyError."""
    from recording_service import converter_proxy
    from recording_service.converter_proxy import ConverterProxyError

    monkeypatch.setattr(converter_proxy, "is_docker_available", lambda: True)

    def fake_run(*a, **kw):
        raise converter_proxy.subprocess.TimeoutExpired(cmd="docker exec", timeout=1)

    monkeypatch.setattr(converter_proxy.subprocess, "run", fake_run)

    with pytest.raises(ConverterProxyError) as excinfo:
        converter_proxy.run_convert(
            container_session_dir="/data/recordings/x",
            host_scenario_path="/tmp/no.json",
        )
    assert "안에 끝나지 않았습니다" in str(excinfo.value)


# ── TR.4 — Web UI 정적 서빙 ─────────────────────────────────────────────────

def test_root_returns_html(client):
    """/ → index.html 반환."""
    r = client.get("/")
    assert r.status_code == 200
    assert "DSCORE Recording Service" in r.text or "Recording Service" in r.text
    assert "<html" in r.text.lower()


def test_static_app_js_served(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    # 키워드 한 두 개로 정합성만 확인
    assert "recording" in r.text.lower()


def test_static_style_css_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert ".card" in r.text or "state-pill" in r.text


# ── TR.4 — Assertion 추가 endpoint ──────────────────────────────────────────

def _create_done_session(client, patched_codegen):
    """assertion 테스트용 fixture — start → stop → state=done 까지."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    client.post(f"/recording/stop/{sid}")
    return sid


def test_assertion_404_for_unknown_session(client):
    r = client.post(
        "/recording/sessions/nope/assertion",
        json={"action": "verify", "target": "#x", "value": "y"},
    )
    assert r.status_code == 404


def test_assertion_409_when_session_not_done(client, patched_codegen):
    """state != done 인 세션엔 assertion 추가 불가."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    # stop 호출 안 함 → state=recording
    r2 = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "verify", "target": "#x", "value": "y"},
    )
    assert r2.status_code == 409


def test_assertion_400_invalid_action(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "click", "target": "#x", "value": "y"},
    )
    assert r.status_code == 400
    assert "verify" in r.json()["detail"]


def test_assertion_400_empty_target(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "verify", "target": "  ", "value": "y"},
    )
    assert r.status_code == 400


def test_assertion_400_empty_value(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "verify", "target": "#x", "value": ""},
    )
    assert r.status_code == 400


def test_assertion_appends_verify_step(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    # patched_codegen 의 fake_convert 가 2 스텝 시나리오를 만들었음
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={
            "action": "verify",
            "target": "#status",
            "value": "clicked",
            "condition": "text",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["step_added"] == 3
    assert body["step_count"] == 3
    added = body["added_step"]
    assert added["action"] == "verify"
    assert added["target"] == "#status"
    assert added["value"] == "clicked"
    assert added["condition"] == "text"
    assert added["step"] == 3


def test_assertion_appends_mock_status_and_mock_data(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r1 = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={
            "action": "mock_status",
            "target": "https://api.example.test/api/list",
            "value": "500",
        },
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={
            "action": "mock_data",
            "target": "https://api.example.test/api/list",
            "value": '{"items":[]}',
        },
    )
    assert r2.status_code == 201
    # 4번째 step 이 mock_data
    assert r2.json()["step_added"] == 4
    assert r2.json()["added_step"]["action"] == "mock_data"


def test_assertion_persists_to_scenario_json(client, patched_codegen, temp_host_root):
    """추가된 step 이 scenario.json 파일에 실제로 저장되는지."""
    import json
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "verify", "target": "#x", "value": "y"},
    )
    assert r.status_code == 201
    # 파일 직접 읽기
    scenario_file = Path(temp_host_root) / sid / "scenario.json"
    assert scenario_file.is_file()
    data = json.loads(scenario_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert any(
        s.get("action") == "verify" and s.get("target") == "#x"
        for s in data
    )


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
