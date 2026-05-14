"""FLOW-USR-001.py 풀 동선 — Recording UI 업로드 → LLM 적용 실행 →
생성된 regression_test.py 가 팝업 흐름 보존하는지 검증.

본 walkthrough 는 1회성 라이브 검증 (dedicated pytest 슈트 아님).

흐름:
  1. FLOW-USR-001.py (팝업 3개 포함) 를 /recording/import-script 로 업로드 → 세션 ID 획득
  2. 헤디드 Chromium 으로 Recording UI 열기 → 신규 세션 row 의 ▶ Codegen 녹화코드 실행
     (또는 직접 API 호출로 LLM 적용 실행)
  3. 실행 완료 polling — state=done | error
  4. 산출물 검증:
     - scenario.json 의 popup_to / page 필드 보존
     - regression_test.py 의 `with X.expect_popup() as Y_info:` / `Y = Y_info.value` 출력
     - 메인 외 page1/page2/page3 의 동작 라인 존재
  5. 결과 보고

준비:
  - portal.koreaconnect.kr 에 시드된 dpg 프로파일 (이미 있음)
  - Recording UI 데몬 18092 기동 중
  - Ollama qwen3.5:9b 활성 (LLM healing 대비)

Usage:
    "$HOME/.dscore.ttc.playwright-agent/venv/Scripts/python.exe" \\
        playwright-allinone/test/test_flow_usr_001_popup_e2e.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import requests

REC_BASE = "http://127.0.0.1:18092"
SCRIPT_PATH = Path(r"C:\Users\csr68\AppData\Local\Temp\FLOW-USR-001-popup-test.py")
AUTH_PROFILE = "dpg"
POLL_DEADLINE = 600  # LLM healing 포함 풀 실행 한도 — 어차피 5분 강제중단은 풀려 있음


def step(msg: str) -> None:
    print(f"[step] {msg}")


def upload_script() -> str:
    """import-script — 새 세션 ID 반환."""
    step(f"업로드 {SCRIPT_PATH.name} (auth_profile={AUTH_PROFILE})")
    with SCRIPT_PATH.open("rb") as f:
        files = {"file": (SCRIPT_PATH.name, f, "text/x-python")}
        data = {"auth_profile": AUTH_PROFILE}
        r = requests.post(f"{REC_BASE}/recording/import-script", files=files, data=data, timeout=60)
    r.raise_for_status()
    body = r.json()
    sid = body.get("session_id") or body.get("id")
    print(f"  → sid={sid}, step_count={body.get('step_count','?')}")
    return sid


def find_play_endpoint(sid: str) -> str:
    """LLM 적용 실행 엔드포인트 탐색 — server.py 의 R+ router 경로."""
    # R+ 의 play-llm 엔드포인트
    return f"{REC_BASE}/recording/sessions/{sid}/play-llm"


def trigger_llm_play(sid: str) -> dict:
    step(f"▶ LLM 적용 실행 트리거 — sid={sid}")
    r = requests.post(find_play_endpoint(sid), json={"headed": False, "slow_mo_ms": 0}, timeout=30)
    if r.status_code >= 400:
        print(f"  ✗ {r.status_code} {r.text[:300]}")
    r.raise_for_status()
    return r.json()


def poll_until_done(sid: str, deadline_sec: int) -> dict:
    """세션 state 가 done/error 가 될 때까지 폴링."""
    deadline = time.time() + deadline_sec
    last_state = None
    while time.time() < deadline:
        r = requests.get(f"{REC_BASE}/recording/sessions/{sid}", timeout=10)
        if r.status_code == 200:
            d = r.json()
            state = d.get("state")
            if state != last_state:
                print(f"  state={state}  ({int(time.time()-deadline+deadline_sec)}s)")
                last_state = state
            if state in ("done", "error", "failed"):
                return d
        time.sleep(3)
    raise TimeoutError(f"poll deadline exceeded ({deadline_sec}s)")


def verify_artifacts(sid: str) -> None:
    """세션 디렉토리의 scenario.json / regression_test.py 검사."""
    sess_dir = Path(rf"C:\Users\csr68\.dscore.ttc.playwright-agent\recordings\{sid}")
    scenario_p = sess_dir / "scenario.json"
    reg_p = sess_dir / "regression_test.py"

    print()
    print("=== scenario.json popup_to 보존 ===")
    if not scenario_p.exists():
        print(f"  ✗ {scenario_p} 없음")
        return
    scenario = json.loads(scenario_p.read_text(encoding="utf-8"))
    popup_steps = [(s["step"], s.get("popup_to"), s.get("page")) for s in scenario if s.get("popup_to")]
    page_vars = sorted({s.get("page", "page") for s in scenario})
    print(f"  총 {len(scenario)} step, page vars: {page_vars}")
    for step_no, p_to, page_v in popup_steps:
        print(f"    step={step_no} page={page_v} popup_to={p_to}")

    print()
    print("=== regression_test.py popup wrap 존재 ===")
    if not reg_p.exists():
        print(f"  ✗ {reg_p} 없음 (모든 step PASS/HEALED 아니면 미생성)")
        return
    text = reg_p.read_text(encoding="utf-8")
    wraps = [l for l in text.splitlines() if "expect_popup" in l]
    assigns = [l for l in text.splitlines() if "_info.value" in l]
    print(f"  expect_popup wrap 라인: {len(wraps)}")
    for l in wraps:
        print(f"    {l.strip()}")
    print(f"  _info.value 할당 라인: {len(assigns)}")
    for l in assigns:
        print(f"    {l.strip()}")


def main() -> int:
    if not SCRIPT_PATH.exists():
        print(f"✗ {SCRIPT_PATH} 없음")
        return 1
    try:
        sid = upload_script()
    except Exception as e:
        print(f"✗ 업로드 실패: {e}")
        return 1

    try:
        trigger_llm_play(sid)
    except Exception as e:
        print(f"✗ 실행 트리거 실패: {e}")
        # 실행 트리거 실패해도 검증은 시도 (시나리오 변환은 끝남)
        verify_artifacts(sid)
        return 1

    try:
        final = poll_until_done(sid, POLL_DEADLINE)
        print(f"  최종 state: {final.get('state')}")
    except Exception as e:
        print(f"✗ polling 실패: {e}")

    verify_artifacts(sid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
