"""Replay UI headed walkthrough — 라이브 18094 데몬에 대해 핵심 UI 동선을
브라우저 창을 띄워 회귀 검증.

이 스크립트는 dedicated pytest 슈트가 아닌 일회성 walkthrough 다. (E2E_COVERAGE
§알려진 공백 — Replay UI 자체의 클릭 회귀는 미작성). 외부 자원(시드된 인증
프로파일, 업로드용 .py 시나리오) 의존 흐름은 범위 밖.

검증 범위:
  1. 헤더: 제목 / cross-link (target=_blank, href=18092) / wizard 버튼
  2. 4개 섹션 카드 노출 (로그인 프로파일 / 시나리오 스크립트 / 실행 / 결과)
  3. 프로파일 테이블 — /api/profiles 응답 반영 (빈 목록 메시지 또는 row)
  4. 스크립트 테이블 — /api/scripts 응답 반영
  5. 실행 결과 테이블 — /api/runs 응답 반영
  6. + 새 프로파일 모달: btn-add-alias 클릭 → seed-input-modal 노출 → 취소로 닫힘
  7. 첫 사용 가이드 모달: btn-wizard 클릭 → wizard-modal 노출 → 시작하기로 닫힘
  8. cross-link 클릭 → 새 탭으로 18092 (Recording UI) 진입

Usage:
    /c/Users/csr68/.dscore.ttc.monitor/venv/Scripts/python.exe \\
        playwright-allinone/test/test_replay_ui_walkthrough_headed.py
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

REPLAY_URL = "http://127.0.0.1:18094/"
RECORDING_URL_PREFIX = "http://localhost:18092"

results: list[tuple[str, str, str]] = []


def step(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status:5}] {name} — {detail}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        ctx = browser.new_context()
        page = ctx.new_page()

        try:
            page.goto(REPLAY_URL, wait_until="domcontentloaded")
        except Exception as exc:
            step("page.goto", "FAIL", f"Replay UI 미응답: {exc}")
            browser.close()
            return 1

        # 1. 헤더
        try:
            expect(page).to_have_title("Replay UI", timeout=3000)
            expect(page.locator("header h1")).to_contain_text("Replay UI")
            step("title", "PASS", "Replay UI")
        except Exception as exc:
            step("title", "FAIL", str(exc))

        try:
            cross = page.locator("a.cross-link")
            expect(cross).to_be_visible(timeout=2000)
            href = cross.get_attribute("href")
            target = cross.get_attribute("target")
            assert href and href.startswith(RECORDING_URL_PREFIX), f"href={href}"
            assert target == "_blank", f"target={target}"
            step("cross-link", "PASS", f"href={href} target={target}")
        except Exception as exc:
            step("cross-link", "FAIL", str(exc))

        try:
            expect(page.locator("#btn-wizard")).to_be_visible(timeout=2000)
            step("wizard-btn", "PASS", "")
        except Exception as exc:
            step("wizard-btn", "FAIL", str(exc))

        # 2. 4개 섹션
        try:
            sections = page.locator("main > section.card")
            count = sections.count()
            assert count >= 4, f"section.card count={count} (<4)"
            step("4 sections", "PASS", f"count={count}")
        except Exception as exc:
            step("4 sections", "FAIL", str(exc))

        # 3. 프로파일 테이블
        try:
            # — 로딩 중 — 가 사라지거나 데이터가 들어올 때까지
            page.wait_for_function(
                "document.querySelector('#profiles-tbody').innerText.indexOf('로딩 중') < 0",
                timeout=5000,
            )
            tbody = page.locator("#profiles-tbody").inner_text()
            step("profiles loaded", "PASS", repr(tbody[:60]))
        except Exception as exc:
            step("profiles loaded", "FAIL", str(exc))

        # 4. 스크립트 테이블
        try:
            page.wait_for_function(
                "document.querySelector('#scripts-tbody').innerText.indexOf('로딩 중') < 0",
                timeout=5000,
            )
            step("scripts loaded", "PASS", "")
        except Exception as exc:
            step("scripts loaded", "FAIL", str(exc))

        # 5. 실행 결과 테이블
        try:
            page.wait_for_function(
                "document.querySelector('#runs-tbody').innerText.indexOf('로딩 중') < 0",
                timeout=5000,
            )
            step("runs loaded", "PASS", "")
        except Exception as exc:
            step("runs loaded", "FAIL", str(exc))

        # 6. + 새 프로파일 모달 열고 닫기
        try:
            page.locator("#btn-add-alias").click()
            modal = page.locator("#seed-input-modal")
            expect(modal).to_be_visible(timeout=2000)
            # 모달 닫기 — data-modal-close="seed-input-modal" 의 취소 버튼
            modal.locator("button.modal-cancel").first.click()
            expect(modal).to_be_hidden(timeout=2000)
            step("seed-input modal", "PASS", "open + cancel")
        except Exception as exc:
            step("seed-input modal", "FAIL", str(exc))

        # 7. 첫 사용 가이드 모달 열고 닫기
        try:
            page.locator("#btn-wizard").click()
            wizard = page.locator("#wizard-modal")
            expect(wizard).to_be_visible(timeout=2000)
            wizard.locator('button[data-modal-close="wizard-modal"]').first.click()
            expect(wizard).to_be_hidden(timeout=2000)
            step("wizard modal", "PASS", "open + close")
        except Exception as exc:
            step("wizard modal", "FAIL", str(exc))

        # 8. cross-link 클릭 → 새 탭 열림 + Recording UI 진입
        try:
            with ctx.expect_page(timeout=5000) as new_page_info:
                page.locator("a.cross-link").click()
            new_page = new_page_info.value
            new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            expect(new_page).to_have_title("Recording UI", timeout=3000)
            step("cross-link → new tab", "PASS", new_page.url)
            new_page.close()
        except Exception as exc:
            step("cross-link → new tab", "FAIL", str(exc))

        time.sleep(1.0)  # 사용자가 결과를 시각적으로 확인할 여유
        browser.close()

    # 요약
    print("\n=== 결과 요약 ===")
    fails = [r for r in results if r[1] != "PASS"]
    for name, status, detail in results:
        print(f"  {status:5} {name}")
    print(f"\nTotal: {len(results)} / Failed: {len(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
