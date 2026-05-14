"""Recording UI headed walkthrough — 라이브 18092 데몬에 대해 핵심 UI 동선을
브라우저 창을 띄워 회귀 검증.

이 스크립트는 dedicated pytest 슈트가 아닌 일회성 walkthrough 다. test_recording_ui_e2e.py
(슈트 1) 는 27→29 개 함수로 세분화돼 있고 ephemeral 데몬을 띄운다. 본 walkthrough 는
**라이브 18092 데몬** 에 직접 접속해, 최근 추가/변경된 부분 (cross-link, 뒤로 버튼
조건부 노출) 까지 포함해 한 흐름으로 회귀 검증한다.

검증 범위:
  1. 헤더: title / h1 / 뒤로 버튼 (직접 진입 시 hidden) / cross-link → 18099
  2. health-badge 가 'checking…' 외 상태로 갱신됨 (서버 healthz 응답)
  3. 5개 collapsible 섹션 (Login Profile / Discover / Recording / Play & more
     / 결과 확인) 각각 expand/collapse 동작
  4. 6번째 카드 — 최근 세션 (table) 노출 + filter input 동작
  5. Login Profile: auth-profile-select 노출 + seed 다이얼로그 열림 + 취소로 닫힘
  6. Discover URLs 폼: btn-discover-start 노출 + auth-profile 셀렉트
  7. Recording 폼: target_url 입력 + btn-start 노출
  8. Play & more: R+ 드롭다운 토글 + import-script 버튼
  9. 최근 세션 표: filter 타이핑 → row 수 변동
  10. 푸터 문구
  11. cross-link 클릭 → 새 탭에 Replay UI 진입

Usage:
    "$HOME/.dscore.ttc.playwright-agent/venv/Scripts/python.exe" \\
        playwright-allinone/test/test_recording_ui_walkthrough_headed.py
"""

from __future__ import annotations

import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from playwright.sync_api import sync_playwright, expect

RECORDING_URL = "http://127.0.0.1:18092/"
REPLAY_URL_PREFIX = "http://localhost:18099"

results: list[tuple[str, str, str]] = []


def step(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status:5}] {name} — {detail}")


def expand_details(page, details_id: str) -> bool:
    """details 요소를 click summary 로 열고 open 상태가 됐는지 반환."""
    locator = page.locator(f"#{details_id}")
    is_open = locator.evaluate("el => el.open")
    if not is_open:
        locator.locator("> summary").click()
    return locator.evaluate("el => el.open")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        ctx = browser.new_context()
        page = ctx.new_page()

        try:
            page.goto(RECORDING_URL, wait_until="domcontentloaded")
        except Exception as exc:
            step("page.goto", "FAIL", f"Recording UI 미응답: {exc}")
            browser.close()
            return 1

        # 1. 헤더 — 제목 / h1
        try:
            expect(page).to_have_title("Recording UI", timeout=3000)
            expect(page.locator("header h1")).to_contain_text("Recording UI")
            step("title", "PASS", "Recording UI")
        except Exception as exc:
            step("title", "FAIL", str(exc))

        # 1-b. 뒤로 버튼 — 직접 진입(referrer 없음) 이므로 hidden 이어야 함
        try:
            back_btn = page.locator("#back-btn")
            is_hidden = back_btn.evaluate("el => el.hidden")
            assert is_hidden, "back-btn 이 직접 진입에도 노출됨"
            step("back-btn hidden", "PASS", "referrer 없을 때 숨김")
        except Exception as exc:
            step("back-btn hidden", "FAIL", str(exc))

        # 1-c. cross-link → 18099 / target=_blank
        try:
            cross = page.locator("a.cross-link")
            expect(cross).to_be_visible(timeout=2000)
            href = cross.get_attribute("href")
            target = cross.get_attribute("target")
            assert href and href.startswith(REPLAY_URL_PREFIX), f"href={href}"
            assert target == "_blank", f"target={target}"
            step("cross-link", "PASS", f"href={href} target={target}")
        except Exception as exc:
            step("cross-link", "FAIL", str(exc))

        # 2. health-badge — 데몬 ping
        try:
            badge = page.locator("#health-badge")
            page.wait_for_function(
                "document.querySelector('#health-badge').innerText.indexOf('checking') < 0",
                timeout=5000,
            )
            txt = badge.inner_text()
            step("health-badge", "PASS", txt)
        except Exception as exc:
            step("health-badge", "FAIL", str(exc))

        # 3. 5개 collapsible 섹션 expand 확인
        section_ids = [
            "login-profile-section",
            "discover-section",
            "new-recording-section",
            "rplus-toggle",
            "result-area-toggle",
        ]
        for sid in section_ids:
            try:
                opened = expand_details(page, sid)
                assert opened, f"{sid} 미오픈"
                step(f"expand {sid}", "PASS", "")
            except Exception as exc:
                step(f"expand {sid}", "FAIL", str(exc))

        # 4. Login Profile — auth-profile-select 노출 + seed 다이얼로그
        try:
            expect(page.locator("#auth-profile-select")).to_be_visible(timeout=2000)
            # seed 다이얼로그 열기
            page.locator("#btn-auth-seed").click()
            dialog = page.locator("#auth-seed-dialog")
            expect(dialog).to_be_visible(timeout=2000)
            # 취소로 닫기
            page.locator("#btn-auth-seed-cancel-input").click()
            expect(dialog).to_be_hidden(timeout=2000)
            step("seed dialog", "PASS", "open + cancel")
        except Exception as exc:
            step("seed dialog", "FAIL", str(exc))

        # 5. Discover URLs 폼
        try:
            expect(page.locator("#btn-discover-start")).to_be_visible(timeout=2000)
            expect(page.locator("#discover-auth-profile")).to_be_visible()
            step("discover form", "PASS", "")
        except Exception as exc:
            step("discover form", "FAIL", str(exc))

        # 6. Recording 폼
        try:
            expect(page.locator("#btn-start")).to_be_visible(timeout=2000)
            expect(page.locator("#start-form input[name=target_url]")).to_be_visible()
            step("recording form", "PASS", "")
        except Exception as exc:
            step("recording form", "FAIL", str(exc))

        # 7. Play & more — import-script 노출 + 드롭다운 disabled 회귀
        #    (활성 세션이 없을 때 dropdown-toggle 은 disabled. 본 walkthrough 는
        #     활성 세션을 만들지 않으므로 disabled 상태 자체를 검증.)
        try:
            expect(page.locator("#btn-import-script")).to_be_visible(timeout=2000)
            area = page.locator("#rplus-session-area")
            assert area.get_attribute("aria-disabled") == "true", \
                "활성 세션 없음에도 rplus-session-area 가 enabled"
            toggle = page.locator("#rplus-section .dropdown-toggle").first
            is_disabled = toggle.evaluate("el => el.disabled")
            assert is_disabled, "dropdown-toggle 이 활성 세션 없음에도 enabled"
            step("R+ dropdown gating", "PASS", "활성 세션 없을 때 disabled")
        except Exception as exc:
            step("R+ dropdown gating", "FAIL", str(exc))

        # 8. 최근 세션 — filter 입력 → row 변동 검증
        try:
            session_table = page.locator("#session-table")
            expect(session_table).to_be_visible(timeout=2000)
            # 데이터 로딩 대기
            page.wait_for_function(
                "document.querySelector('#session-tbody').innerText.indexOf('— 세션 없음 —') >= 0 ||"
                "document.querySelectorAll('#session-tbody tr').length >= 1",
                timeout=5000,
            )
            initial_rows = page.locator("#session-tbody tr").count()
            # 매우 unlikely 한 문자열로 필터 → 0 또는 줄어든 row
            filt = page.locator("#session-filter")
            filt.fill("xxxxx_no_match_zzzzz")
            time.sleep(0.5)
            filtered_rows = page.locator("#session-tbody tr").count()
            assert filtered_rows <= initial_rows, "필터 후 row 가 늘어남"
            filt.fill("")
            time.sleep(0.3)
            step("session filter", "PASS", f"rows {initial_rows}→{filtered_rows}→복원")
        except Exception as exc:
            step("session filter", "FAIL", str(exc))

        # 9. 푸터
        try:
            expect(page.locator("footer")).to_contain_text("Recording UI")
            step("footer", "PASS", "")
        except Exception as exc:
            step("footer", "FAIL", str(exc))

        # 10. cross-link 클릭 → 새 탭
        try:
            with ctx.expect_page(timeout=5000) as new_page_info:
                page.locator("a.cross-link").click()
            new_page = new_page_info.value
            new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            expect(new_page).to_have_title("Replay UI", timeout=3000)
            step("cross-link → new tab", "PASS", new_page.url)
            new_page.close()
        except Exception as exc:
            step("cross-link → new tab", "FAIL", str(exc))

        time.sleep(1.0)
        browser.close()

    print("\n=== 결과 요약 ===")
    fails = [r for r in results if r[1] != "PASS"]
    for name, status, _detail in results:
        print(f"  {status:5} {name}")
    print(f"\nTotal: {len(results)} / Failed: {len(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
