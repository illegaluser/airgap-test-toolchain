"""Recording UI 를 실제로 운전해 한 건 녹화 → 변환까지 풀 동선 수행.

흐름:
  1. 라이브 18092 Recording UI 를 헤디드 Chromium 으로 연다.
  2. 🎬 Recording 섹션 expand → target_url=https://example.com 입력.
  3. ▶ Start Recording 클릭 → 서버가 별도 codegen Chromium subprocess 를 띄움
     (Inspector + 브라우저 창). 이 안에서 사용자가 직접 클릭하면 녹화에 포함.
  4. 일정 대기 (사용자 수동 입력 여유) 후 ■ Stop & Convert 클릭.
  5. /recording/sessions 폴링 → state=done 확인 → 결과(steps 수 / .py 경로) 출력.

준비된 인증 프로파일이나 시드는 사용하지 않는다 (비로그인 녹화).

Usage:
    "$HOME/.dscore.ttc.playwright-agent/venv/Scripts/python.exe" \\
        playwright-allinone/test/test_recording_ui_real_recording_headed.py
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

RECORDING_URL = "http://127.0.0.1:18092/"
API_BASE = "http://127.0.0.1:18092"
TARGET_URL = "https://example.com/"

# codegen 브라우저가 열린 뒤 사용자가 직접 클릭할 시간 (초).
# 클릭이 없어도 navigation 만으로 최소 1-step 녹화는 남는다.
INTERACTION_SECONDS = 20

# stop 후 변환 완료까지 폴링 한도.
CONVERT_POLL_SECONDS = 60


def http_get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        ctx = browser.new_context()
        page = ctx.new_page()

        print(f"[step] Recording UI 열기 — {RECORDING_URL}")
        page.goto(RECORDING_URL, wait_until="domcontentloaded")

        # 1. Recording 섹션 expand
        print("[step] 🎬 Recording 섹션 expand")
        rec_section = page.locator("#new-recording-section")
        if not rec_section.evaluate("el => el.open"):
            rec_section.locator("> summary").click()

        # 2. target_url 입력
        print(f"[step] target_url 입력: {TARGET_URL}")
        target_input = page.locator("#start-form input[name=target_url]")
        target_input.fill(TARGET_URL)

        # 3. Start Recording
        print("[step] ▶ Start Recording 클릭")
        page.locator("#btn-start").click()

        # active-session 블록이 노출될 때까지 대기
        print("[step] active-session 블록 노출 대기")
        try:
            expect(page.locator("#active-session")).to_be_visible(timeout=15000)
        except Exception as exc:
            print(f"[FAIL] active-session 미노출: {exc}")
            # 디버깅: 페이지 에러 메시지 캡처
            try:
                err = page.locator(".form-error, .error, [class*=error]").first.inner_text(timeout=1000)
                print(f"        page error hint: {err!r}")
            except Exception:
                pass
            browser.close()
            return 1

        sid = page.locator("#active-id").inner_text()
        print(f"[ok  ] 세션 시작 — sid={sid}")

        # 4. 사용자 수동 입력 여유. codegen Inspector + 브라우저 창이 떴을 것.
        print(
            f"[wait] {INTERACTION_SECONDS}초 대기 — 열린 codegen Chromium 에서 "
            "원하는 만큼 클릭하세요 (안 해도 navigation 1-step 은 남음)"
        )
        for remaining in range(INTERACTION_SECONDS, 0, -5):
            print(f"        남은 {remaining}초 …")
            time.sleep(5)

        # 5. Stop & Convert
        print("[step] ■ Stop & Convert 클릭")
        page.locator("#btn-stop").click()

        # state=done 또는 error 가 될 때까지 폴링
        print("[step] 변환 완료 폴링")
        final_state = None
        steps_count = None
        target_recorded = None
        deadline = time.time() + CONVERT_POLL_SECONDS
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{API_BASE}/recording/sessions", timeout=10) as r:
                    rows = json.loads(r.read().decode("utf-8"))
                this = next((s for s in rows if s.get("id") == sid), None)
                if this:
                    state = this.get("state")
                    if state and state != final_state:
                        print(f"        state={state}")
                        final_state = state
                    if state in ("done", "error"):
                        steps_count = this.get("action_count")
                        target_recorded = this.get("target_url")
                        break
            except Exception as exc:
                print(f"        poll error: {exc}")
            time.sleep(2)

        # 6. 결과 출력
        print("")
        print("=== 결과 ===")
        print(f"  세션 ID         : {sid}")
        print(f"  최종 state      : {final_state}")
        print(f"  녹화된 target   : {target_recorded}")
        print(f"  변환된 steps    : {steps_count}")

        # 최근 세션 표에 새 세션 row 가 노출됐는지 확인
        try:
            row = page.locator(f"#session-tbody tr").filter(has_text=sid)
            expect(row).to_be_visible(timeout=5000)
            print(f"  세션 표 노출    : OK (sid={sid} row)")
        except Exception as exc:
            print(f"  세션 표 노출    : FAIL — {exc}")

        time.sleep(2.0)
        browser.close()

        success = final_state == "done" and (steps_count or 0) >= 1
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
