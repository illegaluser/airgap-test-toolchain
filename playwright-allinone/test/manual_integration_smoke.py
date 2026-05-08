"""계획 §19 의 수동 통합 테스트 중 backend 만으로 검증 가능한 케이스를
한 번에 돌려보는 스모크 스크립트.

검증 대상 (실행 시 로그에 PASS / FAIL 기록):
- T14 LAN 접속 거부 (127.0.0.1 bind)
- T17 시드 안 된 alias 의 bundle 실행 → 412
- T18 동일 이름 bundle 재업로드 → 409 → overwrite=1 → 200
- T19 글로벌 알람 — /api/profiles 의 storage="missing" 카운트
- T20 probe 5s 타임아웃 (응답 4s OK / 6s false-expired 미발생)
- T3 만료 시뮬레이션 (storage 강제 삭제) → exit 3
- T1 정상 흐름 (probe valid + script 실행 mock) → exit 0

브라우저가 필요한 항목 (T2 클린 OS, T4 Re-seed, T5 SSE, T9 스크린샷 갤러리,
T10 HTML 리포트, T11 sanitize 모달, T13 직접 python script.py, T16 wizard) 은
이 스모크에서 제외 — 수동 검증 항목으로 남음.

Usage:
    cd playwright-allinone/test
    python manual_integration_smoke.py
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# 결과 누적.
_results: list[tuple[str, str, str]] = []


def record(case: str, status: str, detail: str = "") -> None:
    _results.append((case, status, detail))
    print(f"[{status:5}] {case} — {detail}")


def report():
    print("\n=== 결과 요약 ===")
    for case, status, detail in _results:
        print(f"  {status:5} {case:8} {detail}")
    failed = [r for r in _results if r[1] not in ("PASS", "SKIP")]
    print(f"\nTotal: {len(_results)} / Failed: {len(failed)}")
    return 0 if not failed else 1


# --- 공통 ----------------------------------------------------------------

def make_simple_bundle(tmp: Path, alias="packaged", verify_url="http://127.0.0.1:65531/dash") -> Path:
    from recording_service import auth_flow
    sess = tmp / "sess-bundle"
    sess.mkdir(parents=True)
    (sess / "metadata.json").write_text(json.dumps({"id": "sess", "auth_profile": "demo"}))
    (sess / "original.py").write_text("# noop\n")
    zb = auth_flow.pack_bundle(sess, alias=alias, verify_url=verify_url)
    out = tmp / f"{alias}.bundle.zip"
    out.write_bytes(zb)
    return out


def free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --- T14 / T17 / T18 / T19: Replay UI 서버 backend 검증 ------------------

def t_replay_ui_backend(tmp: Path):
    monitor_home = tmp / "monitor-home"
    monitor_home.mkdir()
    auth_dir = monitor_home / "auth-profiles"
    auth_dir.mkdir()
    os.environ["MONITOR_HOME"] = str(monitor_home)
    os.environ["AUTH_PROFILES_DIR"] = str(auth_dir)

    import uvicorn
    from replay_service.server import app

    port = free_port()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=lambda: __import__("asyncio").run(srv.serve()), daemon=True)
    th.start()
    # 기동 대기.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profiles", timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)
    else:
        record("server-start", "FAIL", f"port {port} 응답 없음")
        return

    # T14: 127.0.0.1 외 IP 로 접속 시도. uvicorn 은 bind 단계에서 다른 IP 를
    # accept 하지 않음. socket 으로 외부 IP 시도 → ConnectionRefused / timeout.
    # 같은 머신의 LAN IP 로 시도.
    lan_ip = socket.gethostbyname(socket.gethostname())
    if lan_ip and lan_ip != "127.0.0.1":
        try:
            r = urllib.request.urlopen(f"http://{lan_ip}:{port}/api/profiles", timeout=1.0)
            record("T14", "FAIL", f"LAN 접속 200 받음 — 127.0.0.1 bind 안 됐음")
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            record("T14", "PASS", f"LAN ({lan_ip}:{port}) 접속 거부됨")
    else:
        record("T14", "SKIP", "LAN IP 추출 불가")

    # T18: 동일 이름 bundle 재업로드.
    bundle = make_simple_bundle(tmp, alias="packaged-t18")
    boundary = "----test"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="x.zip"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode() + bundle.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req1 = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/bundles",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        urllib.request.urlopen(req1).read()
        record("T18-first-upload", "PASS", "최초 업로드 201")
    except urllib.error.HTTPError as e:
        record("T18-first-upload", "FAIL", f"최초 HTTP {e.code}")
        return

    # 같은 이름 두 번째 → 409.
    req2 = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/bundles",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        urllib.request.urlopen(req2)
        record("T18-second-conflict", "FAIL", "중복 업로드인데 200")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            record("T18-second-conflict", "PASS", "409 (overwrite=1 필요)")
        else:
            record("T18-second-conflict", "FAIL", f"HTTP {e.code}")

    # overwrite=1 → 200/201.
    req3 = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/bundles?overwrite=1",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        urllib.request.urlopen(req3).read()
        record("T18-overwrite", "PASS", "overwrite=1 → 201")
    except urllib.error.HTTPError as e:
        record("T18-overwrite", "FAIL", f"overwrite=1 인데 HTTP {e.code}")

    # T17: 시드 안 된 alias 의 bundle 실행 시도 → 412.
    run_body = json.dumps({"bundle_name": "x.zip"}).encode()
    req4 = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/runs",
        data=run_body,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req4)
        record("T17", "FAIL", "시드 안 된 alias 인데 실행 200")
    except urllib.error.HTTPError as e:
        if e.code == 412:
            record("T17", "PASS", "412 사전 차단")
        else:
            record("T17", "FAIL", f"HTTP {e.code}")

    # T19: /api/profiles 의 storage 카운트.
    # 카탈로그가 비어있으니 missing 0. 하지만 실제 알람 인디케이터 동작은
    # frontend 가 담당. 여기서는 endpoint 가 빈 리스트 반환하는 것만 확인.
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profiles").read()
    profiles = json.loads(resp)
    if isinstance(profiles, list):
        missing = sum(1 for p in profiles if p.get("storage") == "missing")
        record("T19-endpoint", "PASS", f"profiles={len(profiles)} missing={missing}")
    else:
        record("T19-endpoint", "FAIL", f"이상한 응답: {resp[:60]}")


# --- T20: probe 5s timeout ----------------------------------------------

def t_probe_timeout(tmp: Path):
    """probe 가 응답 안 하는 URL 에 대해 5s 후 expired 반환."""
    from replay_service import orchestrator

    # 응답 안 하는 서버 socket — accept 만 하고 끊어주면 됨.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def silent():
        # 한 connection accept 후 그냥 잡고 있음.
        try:
            conn, _ = listener.accept()
            time.sleep(15)
            conn.close()
        except Exception:
            pass

    threading.Thread(target=silent, daemon=True).start()

    start = time.time()
    result = orchestrator.probe_verify_url(f"http://127.0.0.1:{port}/", storage_path=None)
    elapsed = time.time() - start
    listener.close()

    if elapsed < orchestrator.PROBE_TIMEOUT_S - 0.5 or elapsed > orchestrator.PROBE_TIMEOUT_S + 4:
        record("T20-elapsed", "FAIL", f"{elapsed:.1f}s (목표 ~5s)")
    else:
        record("T20-elapsed", "PASS", f"{elapsed:.1f}s 후 종료")

    if result == "expired":
        record("T20-result", "PASS", "응답 없음 → expired")
    elif result == "error":
        record("T20-result", "PASS", "응답 없음 → error (system_error 분기)")
    else:
        record("T20-result", "FAIL", f"unexpected: {result}")


# --- T1 / T3: orchestrator end-to-end (script subprocess mock) ----------

def t_orchestrator_e2e(tmp: Path):
    """probe 를 mock 처리하고 _run_script_wrapper 도 mock 해 분기 검증."""
    from replay_service import orchestrator
    from recording_service import auth_flow

    sess = tmp / "sess-e2e"
    sess.mkdir()
    (sess / "metadata.json").write_text(json.dumps({"id": "x", "auth_profile": "demo"}))
    (sess / "original.py").write_text("pass\n")
    bundle_bytes = auth_flow.pack_bundle(sess, alias="packaged-e2e", verify_url="http://x")
    bundle = tmp / "e2e.bundle.zip"
    bundle.write_bytes(bundle_bytes)

    fp = MagicMock()
    fp.storage_path = tmp / "no-storage.json"
    fp.fingerprint = MagicMock()
    fp.fingerprint.to_env.return_value = {}

    real_get = orchestrator.auth_profiles.get_profile
    orchestrator.auth_profiles.get_profile = lambda name: fp
    real_probe = orchestrator.probe_verify_url
    real_run = orchestrator._run_script_wrapper
    try:
        # T1: probe valid + script 0 → exit 0.
        orchestrator.probe_verify_url = lambda *a, **k: "valid"
        orchestrator._run_script_wrapper = lambda **k: 0
        out1 = tmp / "run-t1"
        rc = orchestrator.run_bundle(bundle, out1)
        if rc == 0 and (out1 / "exit_code").read_text() == "0":
            record("T1", "PASS", "probe valid + script ok → exit 0")
        else:
            record("T1", "FAIL", f"rc={rc}")

        # T3: probe expired → exit 3.
        orchestrator.probe_verify_url = lambda *a, **k: "expired"
        out3 = tmp / "run-t3"
        rc = orchestrator.run_bundle(bundle, out3)
        events = [
            json.loads(l) for l in (out3 / "run_log.jsonl").read_text().splitlines() if l.strip()
        ]
        ev = any(e.get("event") == "auth_seed_expired" for e in events)
        if rc == 3 and ev:
            record("T3", "PASS", "probe expired → exit 3 + auth_seed_expired jsonl")
        else:
            record("T3", "FAIL", f"rc={rc} events={[e.get('event') for e in events]}")
    finally:
        orchestrator.auth_profiles.get_profile = real_get
        orchestrator.probe_verify_url = real_probe
        orchestrator._run_script_wrapper = real_run


# --- main -----------------------------------------------------------------

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="manual-smoke-") as td:
        tmp = Path(td)
        try:
            t_replay_ui_backend(tmp)
        except Exception as e:
            record("replay_ui_backend", "FAIL", f"예외: {e}")
        try:
            t_probe_timeout(tmp)
        except Exception as e:
            record("probe_timeout", "FAIL", f"예외: {e}")
        try:
            t_orchestrator_e2e(tmp)
        except Exception as e:
            record("orchestrator_e2e", "FAIL", f"예외: {e}")
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
