"""Replay UI 를 실제로 운전해 한 건 업로드 → 실행 → 결과 확인까지 풀 동선.

흐름:
  1. 라이브 18094 Replay UI 를 헤디드 Chromium 으로 연다.
  2. ⬆ 업로드 (.py) 버튼을 통해 example_com_recorded.py (Recording UI 가 만든 codegen 산출물)
     를 #script-file 인풋에 set_input_files 로 주입 → 자동 POST /api/scripts.
  3. scripts-tbody 에 row 가 나타나면 그 row 의 ▶ 실행 버튼 클릭.
     기본값: headed 체크됨, 비로그인 (storage_state 미주입), slowmo off.
  4. Replay UI 서버가 별도 Chromium subprocess 를 띄워 .py 를 실행.
  5. run-stream 에 로그가 흘러 들어오고, runs-tbody 에 새 row 가 추가됨.

준비물:
  - Recording UI 가 8b7edc29483b 세션에서 만든 original.py 를
    /tmp/example_com_recorded.py 로 미리 복사해 둠.

Usage:
    /c/Users/csr68/.dscore.ttc.monitor/venv/Scripts/python.exe \\
        playwright-allinone/test/test_replay_ui_real_run_headed.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from playwright.sync_api import sync_playwright, expect

REPLAY_URL = "http://127.0.0.1:18094/"
API_BASE = "http://127.0.0.1:18094"
SCRIPT_PATH = r"C:\Users\csr68\AppData\Local\Temp\example_com_recorded.py"
SCRIPT_NAME = "example_com_recorded.py"

# .py 실행 후 완료까지 폴링 한도 — Chromium 기동 + navigate + 종료까지 충분히.
RUN_POLL_SECONDS = 90


def http_get_json(path: str):
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        # confirm 다이얼로그 자동 처리 (overwrite 시).
        page.on("dialog", lambda d: d.accept())

        print(f"[step] Replay UI 열기 — {REPLAY_URL}")
        page.goto(REPLAY_URL, wait_until="domcontentloaded")

        # 1. 스크립트 업로드 — set_input_files 직접 (UI 의 hidden file input)
        print(f"[step] 스크립트 업로드: {SCRIPT_PATH}")
        page.locator("#script-file").set_input_files(SCRIPT_PATH)

        # 2. scripts-tbody 에 row 가 생길 때까지 대기
        print(f"[step] scripts-tbody 에 row 노출 대기 (name={SCRIPT_NAME})")
        row_selector = f"#scripts-tbody tr:has-text('{SCRIPT_NAME}')"
        try:
            expect(page.locator(row_selector)).to_be_visible(timeout=10000)
            print("[ok  ] 스크립트 row 노출됨")
        except Exception as exc:
            print(f"[FAIL] 업로드 후 row 미노출: {exc}")
            browser.close()
            return 1

        # 3. headed 토글 확인 (이미 checked 기본값) + 프로파일 = 비로그인 (default)
        headed_checked = page.locator("#run-script-headed-toggle").is_checked()
        profile_val = page.locator("#run-script-profile-select").input_value()
        print(f"[step] 실행 옵션 — headed={headed_checked}, profile={profile_val!r}")

        # 4. ▶ 클릭 직전에 기존 run_id 스냅샷 (반드시 클릭 전 — 클릭 직후 서버가
        #    새 run 을 만들면 "이미 알던 ID" 로 오분류됨)
        # /api/runs 응답: list of {run_id, script, alias, state, exit_code, ...}
        before = http_get_json("/api/runs")
        before_ids = {r["run_id"] for r in before if "run_id" in r}

        # 5. ▶ 실행 클릭
        print("[step] ▶ 실행 클릭")
        page.locator(f"button.run-script-btn[data-name='{SCRIPT_NAME}']").click()

        # run-status 갱신 대기
        run_status = page.locator("#run-status")
        page.wait_for_function(
            "document.querySelector('#run-status').innerText.indexOf('대기') < 0",
            timeout=10000,
        )
        print(f"[ok  ] run-status: {run_status.inner_text()!r}")

        # 6. 별도 Chromium 창이 열리고 .py 가 실행된다. 완료까지 폴링.
        print("[step] /api/runs 폴링 — 실행 완료까지")

        new_run = None
        deadline = time.time() + RUN_POLL_SECONDS
        last_state = None
        while time.time() < deadline:
            try:
                runs = http_get_json("/api/runs")
                for r in runs:
                    rid = r.get("run_id")
                    if not rid or rid in before_ids:
                        continue
                    if r.get("script") and SCRIPT_NAME in r["script"]:
                        new_run = r
                        st = r.get("state")
                        if st != last_state:
                            print(f"        state={st!r} exit_code={r.get('exit_code')!r}")
                            last_state = st
                        if st in ("done", "error", "cancelled"):
                            break
                if new_run and new_run.get("state") in ("done", "error", "cancelled"):
                    break
            except Exception as exc:
                print(f"        poll error: {exc}")
            time.sleep(2)

        print("")
        print("=== 결과 ===")
        if new_run:
            for k in ("run_id", "script", "alias", "state", "exit_code",
                      "started_at", "finished_at", "out_dir"):
                if k in new_run:
                    print(f"  {k:14}: {new_run[k]}")
        else:
            print("  /api/runs 에 새 run 미발견")

        # run-stream 마지막 일부 캡처
        try:
            stream = page.locator("#run-stream").inner_text()
            tail = "\n".join(stream.splitlines()[-10:])
            print("")
            print("--- run-stream tail ---")
            print(tail)
        except Exception:
            pass

        time.sleep(2.0)
        browser.close()

        # 성공 기준: state=done AND exit_code=0
        ok_result = (
            new_run is not None
            and new_run.get("state") == "done"
            and new_run.get("exit_code") == 0
        )
        return 0 if ok_result else 1


if __name__ == "__main__":
    sys.exit(main())
