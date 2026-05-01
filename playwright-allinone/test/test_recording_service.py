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

# 의존성 미설치 환경에서 collection 실패 안 하도록 graceful skip.
# starlette.testclient 는 httpx 미설치 시 ImportError 가 아닌 RuntimeError 를
# 던져 importorskip 의 ImportError 핸들링을 우회한다 → httpx 를 먼저 게이트해야
# fastapi.testclient import 가 실제로 안전하게 skip.
pytest.importorskip("httpx")
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
def rplus_on():
    """TR.4+.4 — R-Plus 게이트 폐기 후 no-op. 기존 테스트 시그니처 호환용."""
    yield


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

    def fake_start(target_url, output_path, *, timeout_sec, extra_args=None):
        # P3.1 (auth-profile) 후 _start_codegen_impl 시그니처에 extra_args 가 추가됨.
        captured["started"].append((target_url, str(output_path), timeout_sec, extra_args))
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
    # TR.4+.4 — rplus_enabled 필드 제거 (게이트 폐기). 회귀: 응답에 없어야 한다.
    assert "rplus_enabled" not in body


# 메인 UI 가 R-Plus 섹션을 항상 노출하므로 별도 experimental SPA index 없음.
# `/experimental/` GET 라우트 미정의 → FastAPI 자동 404. (Recording stop 후
# state=done 일 때 메인 결과 화면에 R-Plus 섹션이 함께 노출됨.)


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


def test_get_session_scenario_returns_dsl(client, patched_codegen):
    """state=done 세션의 scenario.json 본문을 그대로 반환 (TR.4 #4)."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    client.post(f"/recording/stop/{sid}")

    r2 = client.get(f"/recording/sessions/{sid}/scenario")
    assert r2.status_code == 200
    body = r2.json()
    assert isinstance(body, list)
    # patched_codegen 의 fake_convert 가 만들어 둔 2-스텝 시나리오 형태 확인.
    assert all(isinstance(step, dict) and "action" in step for step in body)


def test_get_session_scenario_download_returns_attachment(client, patched_codegen):
    """TR.4+.2 — ?download=1 일 때 Content-Disposition: attachment 응답."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    client.post(f"/recording/stop/{sid}")

    r2 = client.get(f"/recording/sessions/{sid}/scenario?download=1")
    assert r2.status_code == 200
    cd = r2.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f"{sid}-scenario.json" in cd


def test_get_session_original_returns_python_source(client, patched_codegen):
    """TR.4+.1 — codegen 원본 .py 본문을 그대로 반환."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    client.post(f"/recording/stop/{sid}")

    r2 = client.get(f"/recording/sessions/{sid}/original")
    assert r2.status_code == 200
    # patched_codegen 의 fake_start 가 써 둔 내용
    assert "page.click('button')" in r2.text


def test_get_session_original_download_returns_attachment(client, patched_codegen):
    """TR.4+.1 — ?download=1 일 때 attachment 응답."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    client.post(f"/recording/stop/{sid}")

    r2 = client.get(f"/recording/sessions/{sid}/original?download=1")
    assert r2.status_code == 200
    cd = r2.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f"{sid}-original.py" in cd


def test_get_session_original_404_unknown_session(client):
    r = client.get("/recording/sessions/nonexistent/original")
    assert r.status_code == 404


def test_get_session_scenario_404_unknown_session(client):
    r = client.get("/recording/sessions/nonexistent/scenario")
    assert r.status_code == 404


def test_get_session_scenario_404_when_state_recording(client, patched_codegen):
    """녹화 중 (state=recording) 에는 scenario.json 미존재 → 404."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    # stop 호출 안 함 → recording 상태 유지

    r2 = client.get(f"/recording/sessions/{sid}/scenario")
    assert r2.status_code == 404


def test_stop_with_empty_output(client, monkeypatch):
    """codegen 출력 0 byte → state=error + action_count=0 + 명확한 메시지."""
    from recording_service import server as srv
    from pathlib import Path

    def fake_start(target_url, output_path, *, timeout_sec, extra_args=None):
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

    def fake_start(target_url, output_path, *, timeout_sec, extra_args=None):
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

    def fake_start(target_url, output_path, *, timeout_sec, extra_args=None):
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

    def fake_start(target_url, output_path, *, timeout_sec, extra_args=None):
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
    assert "Recording UI" in r.text
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


# ── codegen 미녹화 액션 (scroll / hover) 보충 ──────────────────────────────


def test_assertion_appends_scroll_step_with_into_view(client, patched_codegen):
    """scroll 액션 — value=into_view 가 14-DSL executor 의 화이트리스트와 정합."""
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "scroll", "target": "#footer", "value": "into_view"},
    )
    assert r.status_code == 201
    body = r.json()
    added = body["added_step"]
    assert added["action"] == "scroll"
    assert added["target"] == "#footer"
    assert added["value"] == "into_view"
    assert "into view" in added["description"]


def test_assertion_scroll_400_when_value_not_in_whitelist(client, patched_codegen):
    """scroll 의 value 는 화이트리스트 (into_view 등) 외엔 거부 — DSL validator 와 정합."""
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "scroll", "target": "#footer", "value": "down"},
    )
    assert r.status_code == 400
    assert "into_view" in r.json()["detail"]


def test_assertion_appends_hover_step_with_empty_value(client, patched_codegen):
    """hover 액션 — value 비어도 허용 (DSL _VALUE_REQUIRED_ACTIONS 와 정합)."""
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "hover", "target": "role=link, name=회사소개", "value": ""},
    )
    assert r.status_code == 201
    added = r.json()["added_step"]
    assert added["action"] == "hover"
    assert added["target"] == "role=link, name=회사소개"
    assert added["value"] == ""
    assert "hover" in added["description"]


def test_assertion_hover_400_when_target_empty(client, patched_codegen):
    """hover 도 target 은 필수 — value 만 optional."""
    sid = _create_done_session(client, patched_codegen)
    r = client.post(
        f"/recording/sessions/{sid}/assertion",
        json={"action": "hover", "target": "  ", "value": ""},
    )
    assert r.status_code == 400


# ── TR.8 — 영속화 / 마운트 / 디스크 세션 흡수 ────────────────────────────────

def test_storage_container_path_for_default_is_recordings(monkeypatch):
    """nested bind 위험 회피 — default 가 /recordings 이어야 함."""
    monkeypatch.delenv("RECORDING_CONTAINER_ROOT", raising=False)
    from recording_service import storage
    assert storage.container_path_for("abc") == "/recordings/abc"


def test_storage_container_path_for_env_override(monkeypatch):
    monkeypatch.setenv("RECORDING_CONTAINER_ROOT", "/custom/path")
    from recording_service import storage
    assert storage.container_path_for("abc") == "/custom/path/abc"


def test_storage_list_session_dirs(temp_host_root):
    from pathlib import Path
    from recording_service import storage
    # 임시 루트 안에 가짜 세션 디렉토리 3개
    for sid in ("aaa", "bbb", "ccc"):
        (Path(temp_host_root) / sid).mkdir()
    # 정렬된 목록
    assert storage.list_session_dirs() == ["aaa", "bbb", "ccc"]


def test_storage_list_session_dirs_when_root_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("RECORDING_HOST_ROOT", str(tmp_path / "missing"))
    from recording_service import storage
    assert storage.list_session_dirs() == []


def test_absorb_disk_sessions_recovers_done_state(temp_host_root, monkeypatch):
    """server 재시작 시뮬레이션 — metadata.json 만 있어도 done 세션 복원."""
    import json
    from pathlib import Path
    from recording_service import server as srv
    from recording_service import session as sess_mod

    # 가짜 디스크 세션 만들기
    sid = "diskonly42xy"
    sdir = Path(temp_host_root) / sid
    sdir.mkdir()
    (sdir / "metadata.json").write_text(json.dumps({
        "id": sid,
        "target_url": "https://recovered.test",
        "state": "done",
        "step_count": 7,
    }), encoding="utf-8")
    (sdir / "scenario.json").write_text("[]", encoding="utf-8")

    # registry 비우고 startup hook 직접 호출
    srv._reset_for_tests()
    srv._absorb_disk_sessions()

    sess = srv._registry.get(sid)
    assert sess is not None
    assert sess.state == "done"
    assert sess.target_url == "https://recovered.test"
    assert sess.action_count == 7


def test_absorb_disk_sessions_marks_orphan_recording(temp_host_root):
    """state=recording 세션은 orphan(error) 으로 표시 (codegen 끊겼음)."""
    import json
    from pathlib import Path
    from recording_service import server as srv

    sid = "orphan9999ab"
    sdir = Path(temp_host_root) / sid
    sdir.mkdir()
    (sdir / "metadata.json").write_text(json.dumps({
        "id": sid,
        "target_url": "https://x.test",
        "state": "recording",
    }), encoding="utf-8")

    srv._reset_for_tests()
    srv._absorb_disk_sessions()

    sess = srv._registry.get(sid)
    assert sess is not None
    assert sess.state == "error"
    assert "orphan" in (sess.error or "").lower()


# ── TR.5 R-Plus — enricher (Recording → IEEE 829-lite 역추정) ───────────────

def test_enrich_404_for_unknown_session(client, rplus_on):
    r = client.post("/experimental/sessions/nope/enrich", json={})
    assert r.status_code == 404


def test_enrich_409_when_session_not_done(client, patched_codegen, rplus_on):
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/experimental/sessions/{sid}/enrich", json={})
    assert r2.status_code == 409


def test_enrich_success_writes_doc_enriched_md(client, monkeypatch, temp_host_root, rplus_on):
    """fake Ollama 응답을 monkeypatch 로 주입해 흐름 검증."""
    import time
    from pathlib import Path
    from recording_service import server as srv
    from recording_service.enricher import EnrichResult

    # done 세션 직접 주입 (codegen·convert 우회)
    srv._reset_for_tests()
    sess = srv._registry.create("https://app.example.com/login")
    sess.state = "done"
    Path(temp_host_root, sess.id).mkdir(parents=True, exist_ok=True)
    Path(temp_host_root, sess.id, "scenario.json").write_text(
        '[{"step":1,"action":"navigate","target":"","value":"https://app.example.com/login"}]',
        encoding="utf-8",
    )

    fake_md = (
        "## 목적\n로그인 흐름.\n\n"
        "## 범위\n인증 페이지.\n\n"
        "## 사전조건\n- 자격증명.\n\n"
        "## 단계\n1. 진입.\n\n"
        "## 예상 결과\n진입 성공.\n\n"
        "## 검증 기준\nURL 매칭.\n"
    )

    def fake_enrich(*, scenario, target_url, page_title=None, inventory_block=None):
        return EnrichResult(
            markdown=fake_md, elapsed_ms=42.0, model="gemma4:26b",
            prompt_tokens_estimate=420,
        )

    from recording_service.rplus import router as rplus_router  # P0.1 #5
    monkeypatch.setattr(rplus_router, "_run_enrich_impl", fake_enrich)

    r = client.post(f"/experimental/sessions/{sess.id}/enrich", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["model"] == "gemma4:26b"
    assert body["markdown"] == fake_md
    assert body["char_count"] == len(fake_md)
    # 디스크에 저장됐는지
    enriched = Path(temp_host_root) / sess.id / "doc_enriched.md"
    assert enriched.is_file()
    assert enriched.read_text(encoding="utf-8") == fake_md


def test_enrich_502_when_ollama_fails(client, monkeypatch, temp_host_root, rplus_on):
    from pathlib import Path
    from recording_service import server as srv
    from recording_service.enricher import EnrichError

    srv._reset_for_tests()
    sess = srv._registry.create("https://x.test")
    sess.state = "done"
    Path(temp_host_root, sess.id).mkdir(parents=True, exist_ok=True)
    Path(temp_host_root, sess.id, "scenario.json").write_text("[{}]", encoding="utf-8")

    def fake_enrich(*a, **kw):
        raise EnrichError("Ollama 호출이 180s 안에 끝나지 않았습니다.")

    from recording_service.rplus import router as rplus_router  # P0.1 #5
    monkeypatch.setattr(rplus_router, "_run_enrich_impl", fake_enrich)
    r = client.post(f"/experimental/sessions/{sess.id}/enrich", json={})
    assert r.status_code == 502
    assert "Ollama" in r.json()["detail"]


def test_enricher_module_few_shot_count():
    from recording_service.enricher import FEW_SHOT_EXAMPLES
    assert len(FEW_SHOT_EXAMPLES) == 3


def test_enricher_module_system_prompt_contains_required_sections():
    from recording_service.enricher import _build_system_prompt
    sp = _build_system_prompt()
    # 6 섹션 명이 시스템 프롬프트에 포함되어야 LLM 이 따름
    for section in ("목적", "범위", "사전조건", "단계", "예상 결과", "검증 기준"):
        assert section in sp


def test_enricher_module_raises_on_empty_scenario():
    from recording_service import enricher
    from recording_service.enricher import EnrichError
    with pytest.raises(EnrichError):
        enricher.enrich_recording(scenario=[], target_url="https://x.test")


# ── TR.6 R-Plus — comparator (Doc ↔ Recording 의미 비교) ────────────────────

def _setup_done_session(client_or_temp, sid_prefix="doc", scenario=None):
    """compare/enrich 테스트용 done 세션 직접 주입."""
    from pathlib import Path
    from recording_service import server as srv
    srv._reset_for_tests()
    sess = srv._registry.create("https://app.example.com")
    sess.state = "done"
    if scenario is not None:
        Path(client_or_temp, sess.id).mkdir(parents=True, exist_ok=True)
        import json as _j
        Path(client_or_temp, sess.id, "scenario.json").write_text(
            _j.dumps(scenario), encoding="utf-8",
        )
    return sess.id


def test_comparator_split_alignable_separates_intent():
    from recording_service.comparator import normalize, split_alignable

    sc = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "click", "target": "#btn", "value": ""},
        {"action": "verify", "target": "#status", "value": "OK"},
        {"action": "mock_status", "target": "https://api.test/x", "value": "500"},
    ]
    aligned, intent = split_alignable(normalize(sc))
    assert [s.action for s in aligned] == ["navigate", "click"]
    assert {s.action for s in intent} == {"verify", "mock_status"}


def test_comparator_exact_match():
    from recording_service.comparator import compare

    seq = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "click", "target": "#btn", "value": ""},
    ]
    res = compare(seq, seq)
    assert res.counts["exact"] == 2
    assert res.counts["missing"] == 0
    assert res.counts["extra"] == 0


def test_comparator_value_diff():
    from recording_service.comparator import compare

    doc = [{"action": "fill", "target": "#email", "value": "user@a.com"}]
    rec = [{"action": "fill", "target": "#email", "value": "user@b.com"}]
    res = compare(doc, rec)
    assert res.counts["value_diff"] == 1
    assert res.counts["exact"] == 0


def test_comparator_missing_and_extra():
    from recording_service.comparator import compare

    doc = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "click", "target": "#btn-A", "value": ""},
        {"action": "fill", "target": "#email", "value": "u@x"},
    ]
    rec = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "fill", "target": "#email", "value": "u@x"},
        {"action": "press", "target": "#email", "value": "Enter"},
    ]
    res = compare(doc, rec)
    assert res.counts["missing"] == 1   # click #btn-A 만 doc 에
    assert res.counts["extra"] == 1     # press 만 recording 에
    assert res.counts["exact"] >= 2     # navigate + fill


def test_comparator_intent_only_doc_verify_separated():
    from recording_service.comparator import compare

    doc = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "click", "target": "#btn", "value": ""},
        {"action": "verify", "target": "#status", "value": "OK"},
        {"action": "mock_status", "target": "https://api.test/x", "value": "500"},
    ]
    rec = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "click", "target": "#btn", "value": ""},
    ]
    res = compare(doc, rec)
    # navigate + click 정확 일치
    assert res.counts["exact"] == 2
    # verify / mock_status 는 intent_only
    assert res.counts["intent_only"] == 2
    # missing 0 (verify/mock 은 정렬 대상 외)
    assert res.counts["missing"] == 0


def test_comparator_html_renders_5_categories():
    from recording_service.comparator import compare, render_html

    doc = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "verify", "target": "#x", "value": "OK"},
    ]
    rec = [
        {"action": "navigate", "target": "", "value": "https://x.test"},
        {"action": "click", "target": "#extra", "value": ""},
    ]
    res = compare(doc, rec)
    html = render_html(res)
    assert "<table>" in html
    # 5분류 라벨 노출
    assert "정확" in html
    assert "녹화 외 의도" in html
    # 비대칭 안내
    assert "verify / mock_*" in html


# ── /recording/sessions/{id}/compare endpoint ───────────────────────────────

def test_compare_404_unknown(client, rplus_on):
    r = client.post(
        "/experimental/sessions/nope/compare",
        json={"doc_dsl": [{"action": "navigate", "target": "", "value": "x"}]},
    )
    assert r.status_code == 404


def test_compare_400_empty_doc_dsl(client, temp_host_root, rplus_on):
    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])
    r = client.post(f"/experimental/sessions/{sid}/compare", json={"doc_dsl": []})
    assert r.status_code == 400


def test_compare_writes_html_and_returns_counts(client, temp_host_root, rplus_on):
    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    doc = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
        {"step": 3, "action": "verify", "target": "#status", "value": "OK"},
    ]
    r = client.post(f"/experimental/sessions/{sid}/compare", json={"doc_dsl": doc})
    assert r.status_code == 201
    body = r.json()
    assert body["counts"]["exact"] == 2
    assert body["counts"]["intent_only"] == 1
    # HTML 리포트 파일 존재
    from pathlib import Path
    html_path = Path(temp_host_root) / sid / "doc_comparison.html"
    assert html_path.is_file()
    assert "<table>" in html_path.read_text(encoding="utf-8")


def test_compare_html_endpoint_serves_file(client, temp_host_root, rplus_on):
    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])
    doc = [{"action": "navigate", "target": "", "value": "https://x.test"}]
    r = client.post(f"/experimental/sessions/{sid}/compare", json={"doc_dsl": doc})
    assert r.status_code == 201
    r2 = client.get(f"/experimental/sessions/{sid}/comparison.html")
    assert r2.status_code == 200
    assert "<html" in r2.text.lower()


def test_compare_html_endpoint_404_when_no_report(client, temp_host_root, rplus_on):
    sid = _setup_done_session(temp_host_root, scenario=[{"action": "navigate", "target": "", "value": "x"}])
    r = client.get(f"/experimental/sessions/{sid}/comparison.html")
    assert r.status_code == 404


# ── TR.7 R-Plus — Play (codegen output / LLM 두 모드, headed) ────────────────

def test_play_codegen_success(client, monkeypatch, temp_host_root, rplus_on):
    """codegen output replay — host 에서 original.py 직접 실행."""
    from recording_service.replay_proxy import PlayResult

    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])

    def fake_play(*, host_session_dir):
        return PlayResult(returncode=0, stdout="ok\n", stderr="", elapsed_ms=2345.0)

    from recording_service.rplus import router as rplus_router
    monkeypatch.setattr(rplus_router, "_run_codegen_replay_impl", fake_play)
    r = client.post(f"/experimental/sessions/{sid}/play-codegen")
    assert r.status_code == 201
    body = r.json()
    assert body["returncode"] == 0
    assert body["elapsed_ms"] == 2345.0
    assert "ok" in body["stdout_tail"]


def test_play_llm_success(client, monkeypatch, temp_host_root, rplus_on):
    """LLM play — 14-DSL scenario.json 을 zero_touch_qa executor 로 실행."""
    from recording_service.replay_proxy import PlayResult

    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])

    captured = {}

    def fake_play(*, host_session_dir, project_root):
        captured["project_root"] = project_root
        captured["host_session_dir"] = host_session_dir
        return PlayResult(returncode=0, stdout="PASS: 1\n", stderr="", elapsed_ms=4567.0)

    from recording_service.rplus import router as rplus_router
    monkeypatch.setattr(rplus_router, "_run_llm_play_impl", fake_play)
    r = client.post(f"/experimental/sessions/{sid}/play-llm")
    assert r.status_code == 201
    body = r.json()
    assert body["returncode"] == 0
    assert "PASS: 1" in body["stdout_tail"]
    # router 가 project_root 를 자동으로 주입
    assert captured["project_root"]
    assert captured["host_session_dir"].endswith(sid)


def test_play_codegen_502_when_proxy_error(client, monkeypatch, temp_host_root, rplus_on):
    from recording_service.replay_proxy import ReplayProxyError

    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])

    def fake_play(*a, **kw):
        raise ReplayProxyError("original.py 없음: ...")

    from recording_service.rplus import router as rplus_router
    monkeypatch.setattr(rplus_router, "_run_codegen_replay_impl", fake_play)
    r = client.post(f"/experimental/sessions/{sid}/play-codegen")
    assert r.status_code == 502


def test_play_llm_502_when_proxy_error(client, monkeypatch, temp_host_root, rplus_on):
    from recording_service.replay_proxy import ReplayProxyError

    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])

    def fake_play(*a, **kw):
        raise ReplayProxyError("scenario.json 없음: ...")

    from recording_service.rplus import router as rplus_router
    monkeypatch.setattr(rplus_router, "_run_llm_play_impl", fake_play)
    r = client.post(f"/experimental/sessions/{sid}/play-llm")
    assert r.status_code == 502


def test_play_codegen_proxy_raises_when_original_py_missing(tmp_path):
    """original.py 가 없으면 ReplayProxyError."""
    from recording_service import replay_proxy
    from recording_service.replay_proxy import ReplayProxyError

    with pytest.raises(ReplayProxyError, match="original.py"):
        replay_proxy.run_codegen_replay(host_session_dir=str(tmp_path))


def test_play_llm_proxy_raises_when_scenario_missing(tmp_path):
    from recording_service import replay_proxy
    from recording_service.replay_proxy import ReplayProxyError

    with pytest.raises(ReplayProxyError, match="scenario.json"):
        replay_proxy.run_llm_play(
            host_session_dir=str(tmp_path),
            project_root="/fake/project",
        )


def test_play_codegen_invokes_python_on_original(monkeypatch, tmp_path):
    """codegen 재생은 codegen_trace_wrapper 모듈을 통해 실행되며, 실제 스크립트
    경로는 ``CODEGEN_SCRIPT`` env 로 전달된다 (Playwright tracing 자동 주입)."""
    from recording_service import replay_proxy
    from types import SimpleNamespace

    (tmp_path / "original.py").write_text("print('hi')", encoding="utf-8")

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        captured["env"] = kw.get("env") or {}
        return SimpleNamespace(returncode=0, stdout=b"hi\n", stderr=b"")

    monkeypatch.setattr(replay_proxy.subprocess, "run", fake_run)

    res = replay_proxy.run_codegen_replay(
        host_session_dir=str(tmp_path), venv_py="/fake/python",
    )
    assert res.returncode == 0
    assert captured["cmd"][0] == "/fake/python"
    assert captured["cmd"][1] == "-m"
    assert captured["cmd"][2] == "recording_service.codegen_trace_wrapper"
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"]["CODEGEN_SESSION_DIR"] == str(tmp_path)
    assert captured["env"]["CODEGEN_SCRIPT"] == "original.py"


def test_play_llm_invokes_zero_touch_qa_with_scenario(monkeypatch, tmp_path):
    from recording_service import replay_proxy
    from types import SimpleNamespace

    (tmp_path / "scenario.json").write_text("[]", encoding="utf-8")

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        captured["env"] = kw.get("env")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(replay_proxy.subprocess, "run", fake_run)

    res = replay_proxy.run_llm_play(
        host_session_dir=str(tmp_path),
        project_root="/fake/project",
        venv_py="/fake/python",
    )
    assert res.returncode == 0
    assert captured["cmd"][0] == "/fake/python"
    assert "-m" in captured["cmd"]
    assert "zero_touch_qa" in captured["cmd"]
    assert "--mode" in captured["cmd"] and "execute" in captured["cmd"]
    # PYTHONPATH 와 ARTIFACTS_DIR 주입 확인
    assert "/fake/project" in captured["env"]["PYTHONPATH"]
    assert captured["env"]["ARTIFACTS_DIR"] == str(tmp_path)
    # --headless 미지정 (default: headed)
    assert "--headless" not in captured["cmd"]


def test_play_llm_dumps_subprocess_log_to_session_dir(monkeypatch, tmp_path):
    """play-llm subprocess 의 stdout/stderr 가 세션 디렉토리에 떨어진다.

    데몬 log 에는 자식 프로세스 출력이 안 들어가서, 시나리오 실행과 실제
    healer 동작 사이 연결고리가 끊겼던 문제 (visibility-healer 의 cascade
    hover 로그 추적 불가) 를 보강.
    """
    from recording_service import replay_proxy
    from types import SimpleNamespace

    (tmp_path / "scenario.json").write_text("[]", encoding="utf-8")

    fake_stdout = b"[Step 2] visibility-healer cascade hover\n"
    fake_stderr = b"INFO:zero_touch_qa.executor:done\n"

    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr=fake_stderr)

    monkeypatch.setattr(replay_proxy.subprocess, "run", fake_run)

    replay_proxy.run_llm_play(
        host_session_dir=str(tmp_path),
        project_root="/fake/project",
        venv_py="/fake/python",
    )

    log_path = tmp_path / "play-llm.log"
    assert log_path.is_file(), "play-llm.log 가 세션 디렉토리에 안 떨어짐"
    content = log_path.read_text(encoding="utf-8")
    assert "visibility-healer cascade hover" in content
    assert "executor:done" in content
    assert "# returncode: 0" in content


def test_play_codegen_dumps_subprocess_log_to_session_dir(monkeypatch, tmp_path):
    """play-codegen 도 동일 정책 — original.py 실행 출력을 별도 파일로 보존."""
    from recording_service import replay_proxy
    from types import SimpleNamespace

    (tmp_path / "original.py").write_text("print('hi')", encoding="utf-8")

    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout=b"hi\n", stderr=b"")

    monkeypatch.setattr(replay_proxy.subprocess, "run", fake_run)

    replay_proxy.run_codegen_replay(
        host_session_dir=str(tmp_path), venv_py="/fake/python",
    )

    log_path = tmp_path / "play-codegen.log"
    assert log_path.is_file()
    content = log_path.read_text(encoding="utf-8")
    assert "hi" in content


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

def test_play_codegen_auto_annotates_before_running(
    client, monkeypatch, temp_host_root, rplus_on,
):
    """play-codegen 이 실행 직전 자동 annotate 를 수행하고, 응답에 annotate 결과 포함."""
    from pathlib import Path
    from recording_service.replay_proxy import PlayResult

    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])
    # original.py — nav chain → annotate 1건 주입 기대.
    original = Path(temp_host_root) / sid / "original.py"
    original.write_text(
        "from playwright.sync_api import Playwright, sync_playwright\n"
        "def run(playwright: Playwright) -> None:\n"
        "    browser = playwright.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        "    page.locator('nav#gnb').get_by_role('link', name='About').click()\n"
        "    context.close()\n    browser.close()\n"
        "with sync_playwright() as p:\n    run(p)\n",
        encoding="utf-8",
    )

    def fake_play(*, host_session_dir):
        return PlayResult(returncode=0, stdout="", stderr="", elapsed_ms=10.0)

    from recording_service.rplus import router as rplus_router
    monkeypatch.setattr(rplus_router, "_run_codegen_replay_impl", fake_play)
    r = client.post(f"/experimental/sessions/{sid}/play-codegen")
    assert r.status_code == 201
    body = r.json()
    # play 실행 결과 + annotate 결과 동봉.
    assert body["returncode"] == 0
    assert "annotate" in body
    assert body["annotate"]["examined_clicks"] == 1
    assert body["annotate"]["injected"] == 1
    # annotated 파일도 디스크에 생성됐어야.
    annotated = Path(temp_host_root) / sid / "original_annotated.py"
    assert annotated.is_file()


def test_play_codegen_annotate_summary_zero_injection(
    client, monkeypatch, temp_host_root, rplus_on,
):
    """flat selector 만 있는 codegen 출력 — annotate injected 0 / examined N."""
    from pathlib import Path
    from recording_service.replay_proxy import PlayResult

    sid = _setup_done_session(temp_host_root, scenario=[
        {"step": 1, "action": "navigate", "target": "", "value": "https://x.test"},
    ])
    original = Path(temp_host_root) / sid / "original.py"
    original.write_text(
        "from playwright.sync_api import Playwright, sync_playwright\n"
        "def run(playwright: Playwright) -> None:\n"
        "    browser = playwright.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        "    page.get_by_role('link', name='회사소개').click()\n"
        "    page.get_by_role('button', name='Submit').click()\n"
        "    context.close()\n    browser.close()\n"
        "with sync_playwright() as p:\n    run(p)\n",
        encoding="utf-8",
    )

    def fake_play(*, host_session_dir):
        return PlayResult(returncode=0, stdout="", stderr="", elapsed_ms=10.0)

    from recording_service.rplus import router as rplus_router
    monkeypatch.setattr(rplus_router, "_run_codegen_replay_impl", fake_play)
    r = client.post(f"/experimental/sessions/{sid}/play-codegen")
    body = r.json()
    assert body["annotate"]["examined_clicks"] == 2
    assert body["annotate"]["injected"] == 0


def test_play_codegen_404_unknown(client, rplus_on):
    r = client.post("/experimental/sessions/nope/play-codegen")
    assert r.status_code == 404


def test_play_llm_404_unknown(client, rplus_on):
    r = client.post("/experimental/sessions/nope/play-llm")
    assert r.status_code == 404


def test_play_codegen_409_when_session_recording(client, patched_codegen, rplus_on):
    """state=recording 중에는 codegen replay 불가 (original.py 가 아직 쓰이는 중)."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/experimental/sessions/{sid}/play-codegen")
    assert r2.status_code == 409


def test_play_llm_409_when_session_recording(client, patched_codegen, rplus_on):
    """state=recording 중에는 LLM play 불가 (codegen 이 stop 안 된 상태)."""
    r = client.post("/recording/start", json={"target_url": "https://x.test"})
    sid = r.json()["id"]
    r2 = client.post(f"/experimental/sessions/{sid}/play-llm")
    assert r2.status_code == 409


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


# ── P1 (항목 5) — Run-log + 스크린샷 endpoint ──────────────────────────────

def test_get_run_log_404_when_no_log_file(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.get(f"/recording/sessions/{sid}/run-log")
    assert r.status_code == 404
    assert "run-log" in r.json()["detail"] or "run_log" in r.json()["detail"]


def test_get_run_log_returns_parsed_steps_with_screenshot_field(
    client, patched_codegen, temp_host_root,
):
    """run_log.jsonl + step_*.png 가 있으면 screenshot 필드 자동 채움.

    응답 스키마: {mode: "llm", records: [...]} (codegen 모드 추가에 따른 wrap).
    """
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    # run_log.jsonl + step_1_pass.png 만 만들고 step_2 는 안 만듦.
    (sess_dir / "run_log.jsonl").write_text(
        '{"step": 1, "action": "click", "target": "#a", "status": "PASS", "heal_stage": "none"}\n'
        '{"step": 2, "action": "click", "target": "#b", "status": "HEALED", "heal_stage": "local"}\n',
        encoding="utf-8",
    )
    (sess_dir / "step_1_pass.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # 헤더만 — 충분
    r = client.get(f"/recording/sessions/{sid}/run-log")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mode"] == "llm"
    data = payload["records"]
    assert len(data) == 2
    assert data[0]["status"] == "PASS"
    assert data[0]["screenshot"] == "step_1_pass.png"
    assert data[1]["heal_stage"] == "local"
    assert data[1]["screenshot"] is None  # step_2_healed.png 없음


def test_get_run_log_codegen_mode_reads_codegen_run_log(
    client, patched_codegen, temp_host_root,
):
    """mode=codegen → codegen_run_log.jsonl + codegen_screenshots/ 사용."""
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    (sess_dir / "codegen_run_log.jsonl").write_text(
        '{"step": 1, "action": "goto", "target": "https://x", "status": "PASS",'
        ' "screenshot": "step_1_pass.jpeg"}\n'
        '{"step": 2, "action": "click", "target": "#missing", "status": "FAIL",'
        ' "error": "Timeout 30000ms exceeded"}\n',
        encoding="utf-8",
    )
    cg_shots = sess_dir / "codegen_screenshots"
    cg_shots.mkdir()
    (cg_shots / "step_1_pass.jpeg").write_bytes(b"\xff\xd8\xff")  # JPEG 헤더만
    r = client.get(f"/recording/sessions/{sid}/run-log?mode=codegen")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mode"] == "codegen"
    data = payload["records"]
    assert len(data) == 2
    assert data[0]["screenshot"] == "step_1_pass.jpeg"
    # parser 가 박은 screenshot 필드의 디스크 부재 시 None 정정
    assert data[1].get("screenshot") is None
    assert data[1]["status"] == "FAIL"


def test_get_run_log_auto_prefers_llm_when_both_present(
    client, patched_codegen, temp_host_root,
):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    (sess_dir / "run_log.jsonl").write_text(
        '{"step": 1, "action": "click", "status": "PASS"}\n', encoding="utf-8",
    )
    (sess_dir / "codegen_run_log.jsonl").write_text(
        '{"step": 1, "action": "goto", "status": "PASS"}\n', encoding="utf-8",
    )
    r = client.get(f"/recording/sessions/{sid}/run-log")
    assert r.status_code == 200
    assert r.json()["mode"] == "llm"


def test_get_run_log_auto_falls_back_to_codegen_when_only_codegen(
    client, patched_codegen, temp_host_root,
):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    (sess_dir / "codegen_run_log.jsonl").write_text(
        '{"step": 1, "action": "goto", "status": "PASS"}\n', encoding="utf-8",
    )
    r = client.get(f"/recording/sessions/{sid}/run-log")
    assert r.status_code == 200
    assert r.json()["mode"] == "codegen"


def test_screenshot_endpoint_serves_png(client, patched_codegen, temp_host_root):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    (Path(temp_host_root) / sid / "step_1_pass.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.get(f"/recording/sessions/{sid}/screenshot/step_1_pass.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_screenshot_endpoint_codegen_mode_uses_subdir(
    client, patched_codegen, temp_host_root,
):
    """mode=codegen → codegen_screenshots/<name> 에서 검색."""
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    cg_dir = Path(temp_host_root) / sid / "codegen_screenshots"
    cg_dir.mkdir()
    (cg_dir / "step_1_pass.jpeg").write_bytes(b"\xff\xd8\xff")
    r = client.get(
        f"/recording/sessions/{sid}/screenshot/step_1_pass.jpeg?mode=codegen"
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_screenshot_endpoint_rejects_path_traversal(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.get(f"/recording/sessions/{sid}/screenshot/..%2Fmetadata.json")
    # path traversal 또는 화이트리스트 외 — 400 또는 404. 둘 다 안전.
    assert r.status_code in (400, 404)


def test_screenshot_endpoint_rejects_arbitrary_filename(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.get(f"/recording/sessions/{sid}/screenshot/foo.png")
    assert r.status_code == 400
    assert "허용되지 않는" in r.json()["detail"]


# ── P2 (항목 6) — Play log tail endpoint ──────────────────────────────────

def test_play_log_tail_returns_new_bytes_only(
    client, patched_codegen, temp_host_root,
):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    log_path = Path(temp_host_root) / sid / "play-llm.log"
    log_path.write_text("first\nsecond\n", encoding="utf-8")
    r = client.get(f"/recording/sessions/{sid}/play-log/tail?kind=llm&from=0")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    assert body["content"] == "first\nsecond\n"
    assert body["offset"] == len("first\nsecond\n")

    # offset 으로 incremental 폴링
    log_path.write_text("first\nsecond\nthird\n", encoding="utf-8")
    r2 = client.get(
        f"/recording/sessions/{sid}/play-log/tail?kind=llm&from={body['offset']}"
    )
    assert r2.json()["content"] == "third\n"


def test_play_log_tail_when_file_absent_returns_exists_false(
    client, patched_codegen,
):
    sid = _create_done_session(client, patched_codegen)
    r = client.get(f"/recording/sessions/{sid}/play-log/tail?kind=llm")
    assert r.status_code == 200
    assert r.json()["exists"] is False
    assert r.json()["content"] == ""


def test_play_log_tail_400_for_invalid_kind(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.get(f"/recording/sessions/{sid}/play-log/tail?kind=bogus")
    assert r.status_code == 400


# ── 항목 4 — regression .py + diff endpoint ───────────────────────────────

def test_get_regression_returns_python_text(
    client, patched_codegen, temp_host_root,
):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    (Path(temp_host_root) / sid / "regression_test.py").write_text(
        "# regression\nprint('ok')\n", encoding="utf-8",
    )
    r = client.get(f"/recording/sessions/{sid}/regression")
    assert r.status_code == 200
    assert "regression" in r.text


def test_get_regression_with_download_query_sets_attachment(
    client, patched_codegen, temp_host_root,
):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    (Path(temp_host_root) / sid / "regression_test.py").write_text(
        "x", encoding="utf-8",
    )
    r = client.get(f"/recording/sessions/{sid}/regression?download=1")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd.lower()
    assert "regression_test.py" in cd


def test_get_regression_404_when_missing(client, patched_codegen):
    sid = _create_done_session(client, patched_codegen)
    r = client.get(f"/recording/sessions/{sid}/regression")
    assert r.status_code == 404


def test_diff_endpoint_returns_unified_diff(
    client, patched_codegen, temp_host_root, rplus_on,
):
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    (sess_dir / "regression_test.py").write_text(
        "page.click('#different')\n", encoding="utf-8",
    )
    # original.py 는 patched_codegen fixture 가 만들어 줌 (시작 시점에)
    r = client.get(f"/experimental/sessions/{sid}/diff-codegen-vs-llm")
    assert r.status_code == 200
    body = r.json()
    assert body["left_exists"] is True
    assert body["right_exists"] is True
    # unified diff 헤더 + +/- 라인 포함
    assert "original.py" in body["unified_diff"]
    assert "regression_test.py" in body["unified_diff"]


def test_diff_endpoint_404_when_neither_file_exists(client, rplus_on):
    # 존재하지 않는 세션 — 두 파일 다 없음
    r = client.get("/experimental/sessions/nope/diff-codegen-vs-llm")
    assert r.status_code == 404


def test_diff_endpoint_partial_when_only_one_file_exists(
    client, patched_codegen, temp_host_root, rplus_on,
):
    """regression_test.py 만 있고 original.py 없을 때 — left_exists=False, 200."""
    from pathlib import Path

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    # original.py 삭제 + regression_test.py 만 만듦
    (sess_dir / "original.py").unlink(missing_ok=True)
    (sess_dir / "regression_test.py").write_text("x = 1\n", encoding="utf-8")
    r = client.get(f"/experimental/sessions/{sid}/diff-codegen-vs-llm")
    assert r.status_code == 200
    body = r.json()
    assert body["left_exists"] is False
    assert body["right_exists"] is True


# ── 항목 4 (UI 개선) — LLM diff 분석 endpoint ─────────────────────────────

def test_diff_analysis_404_when_no_regression(client, patched_codegen, rplus_on):
    """regression_test.py 가 없으면 404 — Play with LLM 미실행 케이스."""
    sid = _create_done_session(client, patched_codegen)
    r = client.post(f"/experimental/sessions/{sid}/diff-analysis")
    assert r.status_code == 404


def test_diff_analysis_returns_markdown(
    client, patched_codegen, temp_host_root, monkeypatch, rplus_on,
):
    """fake _run_diff_analysis_impl 로 Ollama 우회 — markdown 반환 확인."""
    from pathlib import Path

    from recording_service.enricher import DiffAnalysisResult
    from recording_service.rplus import router as rplus_router

    captured = {}

    def fake_analyze(*, original_py, regression_py, unified_diff):
        captured["orig"] = original_py
        captured["reg"] = regression_py
        captured["diff"] = unified_diff
        return DiffAnalysisResult(
            markdown="### 1. 핵심 변경 요약\n- selector swap 1건\n",
            elapsed_ms=2500.0,
            model="gemma4:26b",
        )

    monkeypatch.setattr(rplus_router, "_run_diff_analysis_impl", fake_analyze)

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    (sess_dir / "regression_test.py").write_text(
        "page.click('#healed')\n", encoding="utf-8",
    )
    r = client.post(f"/experimental/sessions/{sid}/diff-analysis")
    assert r.status_code == 200
    body = r.json()
    assert "selector swap" in body["markdown"]
    assert body["model"] == "gemma4:26b"
    assert body["elapsed_ms"] >= 0
    # fake 가 입력 받은 파일 본문 확인
    assert "page.click('#healed')" in captured["reg"]


# ── /recording/import-script — 사용자 .py 업로드 ─────────────────────────


def test_import_script_creates_session_and_persists_original(
    client, temp_host_root,
):
    from pathlib import Path

    src = (
        b"from playwright.sync_api import sync_playwright\n"
        b"with sync_playwright() as p:\n"
        b"    page = p.chromium.launch().new_context().new_page()\n"
        b"    page.goto('https://example.com/')\n"
        b"    page.click('#btn')\n"
    )
    files = {"file": ("uploaded.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 201
    body = r.json()
    sid = body["id"]
    assert body["imported_filename"] == "uploaded.py"
    assert body["step_count"] >= 2  # goto + click

    # session 등록 + state=done
    r2 = client.get(f"/recording/sessions/{sid}")
    assert r2.status_code == 200
    assert r2.json()["state"] == "done"

    # original.py 디스크에 저장
    p = Path(temp_host_root) / sid / "original.py"
    assert p.is_file()
    content = p.read_text(encoding="utf-8")
    assert "example.com" in content
    assert "page.click" in content


def test_import_script_rejects_non_py_extension(client):
    files = {"file": ("evil.sh", b"rm -rf /\n", "application/x-shellscript")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 400
    assert ".py" in r.json()["detail"]


def test_import_script_rejects_invalid_python_syntax(client):
    src = b"def \xff invalid syntax !!!\n"
    files = {"file": ("bad.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 400


def test_import_script_rejects_non_playwright(client):
    src = b"print('hello world')\n"
    files = {"file": ("simple.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 400
    assert "playwright" in r.json()["detail"].lower()


def test_import_script_rejects_empty_file(client):
    files = {"file": ("empty.py", b"   \n", "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 400


def test_import_script_estimates_step_count(client):
    src = (
        b"from playwright.sync_api import sync_playwright\n"
        b"def run(p):\n"
        b"    page = p.chromium.launch().new_context().new_page()\n"
        b"    page.goto('https://x')\n"
        b"    page.click('#a')\n"
        b"    page.fill('#b', 'x')\n"
        b"    page.press('#c', 'Enter')\n"
        b"    page.hover('#d')\n"
        b"    page.check('#e')\n"
    )
    files = {"file": ("multi.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 201
    # goto + click + fill + press + hover + check = 6
    assert r.json()["step_count"] == 6


def test_import_script_listed_in_sessions(client, temp_host_root):
    src = b"from playwright.sync_api import sync_playwright\npass\n"
    files = {"file": ("imp.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    sid = r.json()["id"]
    sessions = client.get("/recording/sessions").json()
    assert any(s["id"] == sid for s in sessions)
    target = next(s["target_url"] for s in sessions if s["id"] == sid)
    assert "imported" in target.lower()


def test_import_script_filename_sanitized(client, temp_host_root):
    """파일명 path traversal / 특수문자 제거."""
    src = b"from playwright.sync_api import sync_playwright\npass\n"
    files = {"file": ("../../etc/passwd.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 201
    body = r.json()
    # 슬래시는 _ 로 변환되지만 .py 는 보존
    assert "/" not in body["imported_filename"]
    assert body["imported_filename"].endswith(".py")


def test_import_script_runs_converter_to_produce_scenario_json(
    client, temp_host_root, monkeypatch,
):
    """업로드 후 convert 호출 → scenario.json 생성 (codegen 세션과 동일 흐름).

    convert 실패는 silent — 그래도 세션 등록은 성공 (테스트코드 원본 실행 가능).
    """
    from pathlib import Path

    from recording_service import server as srv
    from recording_service.converter_proxy import ConvertResult

    # convert 결과 fake — scenario.json 을 디스크에 직접 생성
    def fake_convert(*, container_session_dir, host_scenario_path):
        Path(host_scenario_path).write_text(
            '[{"step":1,"action":"navigate","target":"","value":"https://x","fallback_targets":[]}]',
            encoding="utf-8",
        )
        return ConvertResult(
            returncode=0, stdout="", stderr="",
            scenario_path=host_scenario_path,
            scenario_exists=True, elapsed_ms=42.0,
        )

    monkeypatch.setattr(srv, "_run_convert_impl", fake_convert)

    src = (
        b"from playwright.sync_api import sync_playwright\n"
        b"with sync_playwright() as p:\n"
        b"    page = p.chromium.launch().new_context().new_page()\n"
        b"    page.goto('https://x')\n"
    )
    files = {"file": ("user.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 201
    body = r.json()
    sid = body["id"]
    assert body["convert"]["ok"] is True
    assert body["convert"]["scenario_exists"] is True

    # scenario.json 디스크에 실제 생성됨 → Play with LLM 가능
    scenario_path = Path(temp_host_root) / sid / "scenario.json"
    assert scenario_path.is_file()
    assert "navigate" in scenario_path.read_text(encoding="utf-8")


def test_import_script_silent_fails_when_converter_errors(
    client, temp_host_root, monkeypatch,
):
    """converter 실패해도 import 자체는 성공 — 사용자가 테스트코드 원본 실행 으로 재생 가능."""
    from recording_service import server as srv
    from recording_service.converter_proxy import ConverterProxyError

    def fake_convert_fails(*, container_session_dir, host_scenario_path):
        raise ConverterProxyError("docker 미설치 (test stub)")

    monkeypatch.setattr(srv, "_run_convert_impl", fake_convert_fails)

    src = b"from playwright.sync_api import sync_playwright\npass\n"
    files = {"file": ("user.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 201  # 여전히 성공
    body = r.json()
    assert body["convert"]["ok"] is False
    assert "docker" in body["convert"].get("error", "").lower()


def test_imported_session_play_codegen_skips_annotator(
    client, temp_host_root, monkeypatch, rplus_on,
):
    """업로드 스크립트는 annotator 우회 — 사용자 의도된 코드 변형 방지.

    사용자 시나리오: 사용자가 직접 작성한 .py 를 업로드 → annotator 가 hover
    추가하면 의도와 다른 동작. 본 테스트는 imported_filename 메타가 있을 때
    annotator.annotate_script 가 호출되지 않음을 검증.
    """
    from pathlib import Path

    from recording_service import annotator
    from recording_service.replay_proxy import PlayResult
    from recording_service.rplus import router as rplus_router

    src = (
        b"from playwright.sync_api import sync_playwright\n"
        b"with sync_playwright() as p:\n"
        b"    page = p.chromium.launch().new_context().new_page()\n"
        b"    page.goto('https://x')\n"
        b"    page.click('#a')\n"
    )
    files = {"file": ("user_script.py", src, "text/x-python")}
    r = client.post("/recording/import-script", files=files)
    assert r.status_code == 201
    sid = r.json()["id"]

    # annotator 호출 추적
    called = {"count": 0}

    def fake_annotate(*args, **kwargs):
        called["count"] += 1
        return annotator.AnnotateResult(
            src_path="x", dst_path="y", injected=99, examined_clicks=99, triggers=[],
        )

    def fake_replay(*, host_session_dir):
        return PlayResult(returncode=0, stdout="", stderr="", elapsed_ms=10.0)

    monkeypatch.setattr(annotator, "annotate_script", fake_annotate)
    monkeypatch.setattr(rplus_router, "_run_codegen_replay_impl", fake_replay)

    # play-codegen 호출 — annotator 호출 0 + skipped 메시지
    r2 = client.post(f"/experimental/sessions/{sid}/play-codegen")
    assert r2.status_code == 201
    summary = r2.json()["annotate"]
    assert summary["injected"] == 0
    assert summary["examined_clicks"] == 0
    assert "imported" in (summary.get("skipped") or "").lower()
    assert called["count"] == 0  # annotate_script 호출 안 됨


def test_codegen_session_play_still_runs_annotator(
    client, patched_codegen, monkeypatch, rplus_on,
):
    """일반 codegen 세션은 annotator 그대로 호출 — 회귀 가드."""
    from recording_service import annotator
    from recording_service.replay_proxy import PlayResult
    from recording_service.rplus import router as rplus_router

    sid = _create_done_session(client, patched_codegen)
    called = {"count": 0}

    def fake_annotate(src_path, dst_path):
        called["count"] += 1
        return annotator.AnnotateResult(
            src_path=src_path, dst_path=dst_path,
            injected=2, examined_clicks=4, triggers=["x", "y"],
        )

    def fake_replay(*, host_session_dir):
        return PlayResult(returncode=0, stdout="", stderr="", elapsed_ms=10.0)

    monkeypatch.setattr(annotator, "annotate_script", fake_annotate)
    monkeypatch.setattr(rplus_router, "_run_codegen_replay_impl", fake_replay)

    r = client.post(f"/experimental/sessions/{sid}/play-codegen")
    assert r.status_code == 201
    assert called["count"] == 1  # codegen 세션은 annotator 호출됨
    assert r.json()["annotate"]["injected"] == 2


def test_diff_analysis_502_when_ollama_fails(
    client, patched_codegen, temp_host_root, monkeypatch, rplus_on,
):
    """Ollama 호출 실패 시 502 — UI 가 사용자에게 명확한 에러 노출."""
    from pathlib import Path

    from recording_service.enricher import EnrichError
    from recording_service.rplus import router as rplus_router

    def fake_fail(**kw):
        raise EnrichError("Ollama 미가동")

    monkeypatch.setattr(rplus_router, "_run_diff_analysis_impl", fake_fail)

    sid = _create_done_session(client, patched_codegen)
    sess_dir = Path(temp_host_root) / sid
    (sess_dir / "regression_test.py").write_text("x", encoding="utf-8")
    r = client.post(f"/experimental/sessions/{sid}/diff-analysis")
    assert r.status_code == 502
    assert "Ollama" in r.json()["detail"]
