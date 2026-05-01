"""Recording UI — End-to-End 브라우저 테스트.

실제 recording-service 데몬을 별도 포트로 spawn 하고 Playwright headless
Chromium 으로 UI 흐름을 검증. unit/integration 테스트가 못 잡는 영역:
- 마크업 ID 변경 시 JS 핸들러 회귀
- CSS class 의 의미 (status pill 색상, dropdown 펼침)
- Clipboard / dialog / scroll 같은 브라우저 API
- run-log / regression / diff 카드의 fetch+render 통합

전략:
- Session-scoped daemon fixture: 격리된 RECORDING_HOST_ROOT (tmp) + 18093 포트
- 디스크 사전 시드 → 데몬 startup 흡수 (TR.8) 로 두 종류 세션 노출:
  (1) `e2eDONE0001` — done state, original.py + scenario.json 만
  (2) `e2eFULL0002` — done state, + run_log + 스크린샷 + regression_test.py
- LLM 분석은 ``RECORDING_DIFF_ANALYSIS_STUB=1`` 으로 결정론 stub 동작.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

E2E_PORT = 18093
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"

# 사전 시드 세션 — 데몬 startup 시 TR.8 흡수 경로로 등록됨.
SID_BASIC = "e2eDONE0001"  # 24자리 hex 가 아님 — sid 형식 자유 (registry 가 string 만 요구)
SID_FULL = "e2eFULL0002"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _seed_session(root: Path, sid: str, *, with_play: bool) -> None:
    """디스크에 세션 디렉토리 + metadata.json + 산출물 사전 생성.

    데몬이 startup 에서 흡수하므로 API 호출 없이도 UI 가 노출.
    """
    sess = root / sid
    sess.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid,
        "target_url": f"https://{sid.lower()}.test.local/",
        "state": "done",
        "created_at_ts": time.time() - 60,  # 1분 전
        "step_count": 3 if with_play else 2,
    }
    (sess / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    (sess / "original.py").write_text(
        "import re\n"
        "from playwright.sync_api import Playwright, sync_playwright\n\n"
        "def run(playwright: Playwright) -> None:\n"
        "    browser = playwright.chromium.launch(headless=False)\n"
        "    ctx = browser.new_context()\n"
        "    page = ctx.new_page()\n"
        f"    page.goto('https://{sid.lower()}.test.local/')\n"
        "    page.get_by_role('link', name='연혁').click()\n"
        "    page.get_by_role('link', name='~2013').click()\n"
        "\n"
        "with sync_playwright() as p:\n"
        "    run(p)\n",
        encoding="utf-8",
    )

    scenario = [
        {"step": 1, "action": "navigate", "target": "",
         "value": f"https://{sid.lower()}.test.local/", "description": "navigate",
         "fallback_targets": []},
        {"step": 2, "action": "click", "target": "role=link, name=연혁",
         "value": "", "description": "click 연혁", "fallback_targets": []},
    ]
    if with_play:
        scenario.append({
            "step": 3, "action": "click", "target": "role=link, name=~2013",
            "value": "", "description": "click ~2013", "fallback_targets": [],
        })
    (sess / "scenario.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    if not with_play:
        return

    # Play 산출물 — run_log + 스크린샷 + regression_test.py
    (sess / "run_log.jsonl").write_text(
        '{"step": 1, "action": "navigate", "target": "https://e2e.test.local/", '
        '"value": "https://e2e.test.local/", "description": "navigate", '
        '"status": "PASS", "heal_stage": "none", "ts": 1777000000.0}\n'
        '{"step": 2, "action": "click", "target": "role=link, name=연혁", '
        '"value": "", "description": "click 연혁", '
        '"status": "HEALED", "heal_stage": "local", "ts": 1777000005.0}\n'
        '{"step": 3, "action": "click", "target": "role=link, name=~2013", '
        '"value": "", "description": "click ~2013", '
        '"status": "PASS", "heal_stage": "none", "ts": 1777000010.0}\n',
        encoding="utf-8",
    )
    # 1x1 PNG (8-byte header + 데이터) — 브라우저가 image/png 로 인식하기에 충분
    minimal_png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xc0\xc0\x00\x00\x00\x05\x00\x01"
        b"\x0d\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (sess / "step_1_pass.png").write_bytes(minimal_png)
    (sess / "step_2_healed.png").write_bytes(minimal_png)
    (sess / "step_3_pass.png").write_bytes(minimal_png)

    (sess / "regression_test.py").write_text(
        "import re\n"
        "from playwright.sync_api import Playwright, sync_playwright\n\n"
        "def run(playwright: Playwright) -> None:\n"
        "    browser = playwright.chromium.launch(headless=False)\n"
        "    ctx = browser.new_context()\n"
        "    page = ctx.new_page()\n"
        f"    page.goto('https://{sid.lower()}.test.local/')\n"
        "    # T-H — visibility healer cascade hover 거쳐 selector swap\n"
        "    page.get_by_role('link', name='회사연혁').click()\n"
        "    page.get_by_role('link', name='~2013').click()\n"
        "\n"
        "with sync_playwright() as p:\n"
        "    run(p)\n",
        encoding="utf-8",
    )

    # play-llm.log — tail polling 검증용
    (sess / "play-llm.log").write_text(
        "# cmd: python -m zero_touch_qa --mode execute ...\n"
        "# returncode: 0\n"
        "# elapsed_ms: 32000\n"
        "# ── stdout ──\n"
        "[Step 1] PASS\n"
        "[Step 2] visibility-healer → LocalHealer swap (heal_stage=local)\n"
        "[Step 3] PASS\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="session")
def e2e_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e_recordings")
    _seed_session(root, SID_BASIC, with_play=False)
    _seed_session(root, SID_FULL, with_play=True)
    return root


@pytest.fixture(scope="session")
def e2e_daemon(e2e_root):
    """별도 포트로 recording-service spawn. session-scoped — 모든 E2E 가 공유."""
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} 가 이미 사용 중 — E2E 전용 포트 충돌")

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    )
    env["RECORDING_HOST_ROOT"] = str(e2e_root)
    # Discover URLs 결과 디렉토리도 격리 — 사용자 홈 오염 방지.
    env["DISCOVERY_HOST_ROOT"] = str(e2e_root.parent / "e2e-discoveries")
    env["RECORDING_DIFF_ANALYSIS_STUB"] = "1"

    cmd = [
        VENV_PY, "-m", "uvicorn",
        "recording_service.server:app",
        "--host", "127.0.0.1",
        "--port", str(E2E_PORT),
        "--workers", "1",
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    deadline = time.time() + 20.0
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            pytest.skip(f"E2E daemon spawn 실패 (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("E2E daemon healthz 대기 timeout (20s)")

    yield E2E_BASE

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(scope="session")
def e2e_browser(e2e_chromium, e2e_daemon):
    """공유 Chromium browser (conftest.e2e_chromium 위임).

    한 프로세스에서 ``with sync_playwright()`` 를 두 번 열면 두 번째가 asyncio
    loop 충돌을 일으키므로, 본 fixture 는 conftest 의 단일 인스턴스를 그대로
    파이프한다. 여전히 e2e_daemon 의존성으로 daemon 시작 후 사용 보장."""
    yield e2e_chromium


@pytest.fixture
def e2e_page(e2e_browser, e2e_daemon) -> Page:
    """매 테스트 fresh context + page. clipboard 권한 grant."""
    ctx = e2e_browser.new_context(
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = ctx.new_page()
    page.goto(f"{e2e_daemon}/", wait_until="networkidle")
    # 메인 카드 토글들은 default 닫힘 — 테스트는 펼친 상태에서 진행.
    page.evaluate(
        "document.querySelectorAll('details').forEach((d) => { d.open = true; });"
    )
    yield page
    ctx.close()


# ── 1. 페이지 명칭 / 헤더 ──────────────────────────────────────────────────


def test_page_title_is_recording_ui(e2e_page: Page):
    """`<title>` 과 `<h1>` 이 'Recording UI' 로 통일."""
    expect(e2e_page).to_have_title("Recording UI")
    expect(e2e_page.locator("header h1")).to_contain_text("Recording UI")


def test_footer_text_uses_recording_ui(e2e_page: Page):
    expect(e2e_page.locator("footer")).to_contain_text("Recording UI")


# ── 2. 뒤로가기 버튼 (F1) ──────────────────────────────────────────────────


def test_back_button_is_visible(e2e_page: Page):
    btn = e2e_page.locator("#back-btn")
    expect(btn).to_be_visible()
    expect(btn).to_contain_text("뒤로")


# ── 3. 호버 메뉴 안내 배너 ────────────────────────────────────────────────


def test_hover_hint_banner_is_present_on_start_card(e2e_page: Page):
    expect(e2e_page.locator(".hover-hint")).to_be_visible()
    expect(e2e_page.locator(".hover-hint")).to_contain_text("호버 메뉴")


# ── 4. 세션 목록 + 검색/필터 (P3 / 항목 7) ──────────────────────────────────


def test_seeded_sessions_appear_in_list(e2e_page: Page):
    """디스크 시드 → startup 흡수 → 세션 목록에 두 세션 노출."""
    e2e_page.wait_for_function("document.querySelectorAll('#session-tbody tr').length >= 2", timeout=5000)
    rows_text = e2e_page.locator("#session-tbody").inner_text()
    assert SID_BASIC in rows_text
    assert SID_FULL in rows_text


def test_session_filter_input_narrows_rows(e2e_page: Page):
    """target_url / id 키워드로 클라이언트 필터링."""
    e2e_page.wait_for_function("document.querySelectorAll('#session-tbody tr').length >= 2", timeout=5000)
    e2e_page.fill("#session-filter", SID_FULL)
    # 필터링 직후 SID_BASIC 행은 안 보여야 한다
    rows = e2e_page.locator("#session-tbody tr:visible")
    text = rows.first.inner_text()
    assert SID_FULL in text
    assert SID_BASIC not in e2e_page.locator("#session-tbody").inner_text() or \
           e2e_page.locator(f"#session-tbody tr:visible:has-text('{SID_BASIC}')").count() == 0


def test_session_filter_state_select_filters_done_only(e2e_page: Page):
    """state 셀렉터 — 선택값 외 state 의 행은 필터링."""
    e2e_page.wait_for_function("document.querySelectorAll('#session-tbody tr').length >= 2", timeout=5000)
    e2e_page.select_option("#session-state-filter", "done")
    # 두 시드 세션 모두 done 이라 변화 없어야 — 적어도 두 행 visible
    visible = e2e_page.locator("#session-tbody tr:visible").count()
    assert visible >= 2


def test_session_filter_persists_across_reload(e2e_page: Page, e2e_daemon):
    """localStorage 영속 — 새로고침 후 filter 값 복원."""
    e2e_page.fill("#session-filter", SID_FULL)
    e2e_page.evaluate("() => localStorage.setItem('rec.sessionFilter', '" + SID_FULL + "')")
    e2e_page.goto(f"{e2e_daemon}/")
    e2e_page.wait_for_load_state("networkidle")
    e2e_page.evaluate(
        "document.querySelectorAll('details').forEach((d) => { d.open = true; });"
    )
    val = e2e_page.input_value("#session-filter")
    assert val == SID_FULL


# ── 5. R-Plus dropdown (항목 3 — hover-open + click-stick) ─────────────────


def _open_full_session(page: Page, daemon_url: str):
    """SID_FULL 세션 결과 화면 진입 — 다른 fixture 가 의존."""
    page.evaluate(f"window.openSession && window.openSession('{SID_FULL}')")
    # 글로벌 노출이 안 됐으면 직접 행 클릭으로
    page.click(f"button[data-act='open'][data-sid='{SID_FULL}']")
    page.wait_for_selector("#rplus-section:not([hidden])", timeout=5000)


def test_rplus_dropdown_expands_on_click(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    play_group = e2e_page.locator(".dropdown-group[data-group='play']")
    play_group.locator(".dropdown-toggle").click()
    expect(play_group).to_have_class(__import__("re").compile(r".*\bexpanded\b.*"))
    # 서브 항목이 노출
    expect(e2e_page.locator("#btn-play-codegen")).to_be_visible()
    expect(e2e_page.locator("#btn-play-llm")).to_be_visible()


def test_rplus_dropdown_closes_on_escape(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".dropdown-group[data-group='doc'] .dropdown-toggle").click()
    e2e_page.keyboard.press("Escape")
    expanded = e2e_page.locator(".dropdown-group.expanded").count()
    assert expanded == 0


def test_rplus_dropdown_closes_on_outside_click(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".dropdown-group[data-group='play'] .dropdown-toggle").click()
    # 카드 헤더 (그룹 외부) 클릭
    e2e_page.locator("#rplus-section h2").click()
    expanded = e2e_page.locator(".dropdown-group.expanded").count()
    assert expanded == 0


# ── 6. Step 추가 폼 (scroll/hover 추가) ────────────────────────────────────


def test_step_add_form_has_scroll_and_hover_options(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    options = e2e_page.locator("#assertion-form select[name='action'] option").all_text_contents()
    assert "scroll" in options
    assert "hover" in options


def test_selecting_scroll_auto_fills_value(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.select_option("#assertion-form select[name='action']", "scroll")
    val = e2e_page.input_value("#assertion-form input[name='value']")
    assert val == "into_view"


# ── 7. Run-log 시각화 (P1 / 항목 5) ────────────────────────────────────────


def test_run_log_card_renders_with_status_pills(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    expect(e2e_page.locator("#run-log-card")).to_be_visible()
    # 3 행 (PASS / HEALED / PASS)
    rows = e2e_page.locator("#run-log-container .run-log-table tbody tr")
    expect(rows).to_have_count(3)
    # status pill 클래스 확인
    expect(rows.nth(0).locator(".status-pass")).to_be_visible()
    expect(rows.nth(1).locator(".status-healed")).to_be_visible()
    expect(rows.nth(1).locator(".heal-local")).to_be_visible()


def test_screenshot_modal_opens_on_camera_click(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".shot-link").first.click()
    dlg = e2e_page.locator("#shot-dialog")
    expect(dlg).to_have_attribute("open", "")
    img_src = e2e_page.locator("#shot-img").get_attribute("src")
    assert "/screenshot/" in img_src
    assert ".png" in img_src


def test_per_step_json_copy_button_exists(e2e_page: Page, e2e_daemon):
    """P4 — run-log 표 행마다 📋 버튼."""
    _open_full_session(e2e_page, e2e_daemon)
    btns = e2e_page.locator(".copy-step-btn")
    expect(btns.first).to_be_visible()
    # data-step-json 에 step 데이터 들어 있어야
    payload = btns.first.get_attribute("data-step-json")
    assert payload and '"step"' in payload


# ── 8. 클립보드 복사 (항목 2) ──────────────────────────────────────────────


def test_copy_scenario_json_to_clipboard(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".copy-btn[data-copy-target='result-json']").click()
    e2e_page.wait_for_timeout(150)
    text = e2e_page.evaluate("() => navigator.clipboard.readText()")
    assert "navigate" in text  # scenario.json 의 step[0].action


def test_copy_original_py_to_clipboard(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".copy-btn[data-copy-target='result-original']").click()
    e2e_page.wait_for_timeout(150)
    text = e2e_page.evaluate("() => navigator.clipboard.readText()")
    assert "playwright.chromium" in text


def test_copy_regression_py_to_clipboard(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".copy-btn[data-copy-target='result-regression']").click()
    e2e_page.wait_for_timeout(150)
    text = e2e_page.evaluate("() => navigator.clipboard.readText()")
    assert "회사연혁" in text  # regression_test.py 의 healed selector


# ── 9. Regression card (F2) ───────────────────────────────────────────────


def test_regression_card_visible_and_shows_code(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    expect(e2e_page.locator("#regression-card")).to_be_visible()
    code = e2e_page.locator("#result-regression").inner_text()
    assert "regression_test.py" not in code  # 안 보이는 placeholder
    assert "회사연혁" in code


def test_regression_card_has_download_link(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    href = e2e_page.locator("#dl-regression").get_attribute("href")
    assert href and "/regression?download=1" in href


# ── 10. LLM diff 분석 (F3) ────────────────────────────────────────────────


def test_diff_analysis_button_renders_4_section_markdown(e2e_page: Page, e2e_daemon):
    """RECORDING_DIFF_ANALYSIS_STUB=1 로 결정론 stub 반환 → 4 섹션 헤딩 렌더 검증."""
    _open_full_session(e2e_page, e2e_daemon)
    expect(e2e_page.locator("#diff-card")).to_be_visible()
    e2e_page.locator("#btn-analyze-diff").click()
    # 분석 결과 영역에 markdown 헤딩 렌더 (h4 = ###)
    e2e_page.wait_for_selector(".analysis-output h4", timeout=10000)
    headings = e2e_page.locator(".analysis-output h4").all_text_contents()
    # stub 의 4 섹션 헤딩
    assert any("핵심 변경 요약" in h for h in headings)
    assert any("회귀 채택 권고" in h for h in headings)
    # 모델 메타 표시
    expect(e2e_page.locator(".analysis-output")).to_contain_text("stub")


def test_raw_diff_collapsible_present(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    details = e2e_page.locator("#diff-raw-details")
    expect(details).to_be_visible()
    # `<details>` 가 닫혀있어도 DOM 안에는 렌더되어 있음 — text_content 로 확인
    # (inner_text 는 화면에 보이는 것만 반환).
    diff_text = e2e_page.locator("#diff-output").text_content() or ""
    assert "original.py" in diff_text or "regression_test.py" in diff_text


# ── 11. Play 진행 스트리밍 (P2 / 항목 6) ───────────────────────────────────


def test_play_log_tail_endpoint_returns_seeded_log(e2e_page: Page, e2e_daemon):
    """fixture 가 만든 play-llm.log 가 tail endpoint 로 바로 노출."""
    resp = e2e_page.evaluate(
        f"async () => {{ const r = await fetch('/recording/sessions/{SID_FULL}/play-log/tail?kind=llm&from=0'); return await r.json(); }}"
    )
    assert resp["exists"] is True
    assert "visibility-healer" in resp["content"]
    assert resp["offset"] > 0


# ── 12. 마크업 ID 회귀 — 핵심 ID 가 모두 존재 ──────────────────────────────


CRITICAL_IDS = [
    "back-btn", "start-form", "active-session", "result-section",
    "scenario-card", "original-card", "regression-card", "diff-card",
    "run-log-card", "rplus-section", "assertion-form", "assertion-section",
    "session-filter", "session-state-filter", "btn-play-codegen", "btn-play-llm",
    "btn-enrich", "btn-compare-open", "btn-analyze-diff",
    "play-progress-details", "shot-dialog",
]


def test_all_critical_ids_present_in_dom(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    missing = []
    for elem_id in CRITICAL_IDS:
        if e2e_page.locator(f"#{elem_id}").count() == 0:
            missing.append(elem_id)
    assert not missing, f"누락된 핵심 ID: {missing}"


# ── 13. Play Script from File — 사용자 .py 업로드 ─────────────────────────


def test_import_script_button_is_visible(e2e_page: Page):
    btn = e2e_page.locator("#btn-import-script")
    expect(btn).to_be_visible()
    expect(btn).to_contain_text("Play Script from File")


def test_import_script_uploads_and_opens_result_panel(
    e2e_page: Page, e2e_daemon, tmp_path,
):
    """파일 선택 → 업로드 → 결과 패널 진입 → original.py 미리보기 노출."""
    # 임시 .py 작성
    script = tmp_path / "uploaded_e2e.py"
    script.write_text(
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    page = p.chromium.launch().new_context().new_page()\n"
        "    page.goto('https://e2e-uploaded.test/')\n"
        "    page.click('#hello')\n",
        encoding="utf-8",
    )
    # 파일 input 에 set_input_files — 네이티브 picker 우회
    e2e_page.set_input_files("#import-file-input", str(script))
    # 결과 화면 진입 대기 (openSession 후 result-section unhide)
    e2e_page.wait_for_selector("#result-section:not([hidden])", timeout=5000)
    e2e_page.wait_for_selector("#original-card:not([hidden])", timeout=5000)
    # 코드 본문 확인
    code = e2e_page.locator("#result-original").text_content() or ""
    assert "e2e-uploaded.test" in code
    # 세션 목록에도 imported 표시 진입
    rows_text = e2e_page.locator("#session-tbody").text_content() or ""
    assert "imported" in rows_text.lower()


# ── 14. Discover URLs — 폼 + 결과 표 회귀 ─────────────────────────────────


def test_discover_section_is_visible(e2e_page: Page):
    """Discover URLs 섹션이 새 녹화 카드 다음에 노출되고 핵심 입력이 보인다."""
    expect(e2e_page.locator("#discover-section")).to_be_visible()
    expect(e2e_page.locator("#discover-form input[name='seed_url']")).to_be_visible()
    expect(e2e_page.locator("#discover-auth-profile")).to_be_visible()
    expect(e2e_page.locator("#btn-discover-start")).to_be_visible()


def test_discover_submits_and_renders_result_table(e2e_page: Page, e2e_daemon, tmp_path):
    """폼 제출 → 폴링 → 결과 표 + 체크박스 + CSV 링크 노출.

    임시 HTTP 서버에 3페이지 fixture 를 띄우고 그 seed URL 을 입력한다.
    """
    import http.server
    import socketserver
    import threading

    site_root = tmp_path / "discover-fixture"
    site_root.mkdir()
    (site_root / "index.html").write_text(
        '<a href="a.html">A</a><a href="b.html">B</a>',
        encoding="utf-8",
    )
    (site_root / "a.html").write_text("<title>A page</title>", encoding="utf-8")
    (site_root / "b.html").write_text("<title>B page</title>", encoding="utf-8")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(site_root), **kw)

        def log_message(self, *a, **k):  # noqa: D401
            return

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        seed = f"http://127.0.0.1:{port}/index.html"
        e2e_page.fill("#discover-form input[name='seed_url']", seed)
        e2e_page.fill("#discover-form input[name='max_pages']", "10")
        e2e_page.fill("#discover-form input[name='max_depth']", "2")
        e2e_page.click("#btn-discover-start")

        # 결과 표가 렌더되어 체크박스가 보일 때까지 대기 (최대 30초)
        e2e_page.wait_for_selector(
            "#discover-result .discover-url-check", timeout=30_000
        )
        # 액션 바 노출
        expect(e2e_page.locator("#discover-actions")).to_be_visible()
        # CSV 링크에 href 가 박힘
        href = e2e_page.locator("#discover-csv-link").get_attribute("href")
        assert href and "/discover/" in href and href.endswith("/csv")
        # 체크박스 ≥3 (index + a + b)
        checks = e2e_page.locator(".discover-url-check").count()
        assert checks >= 3, f"체크박스 수가 부족: {checks}"
        # 전체 선택 → 선택 카운트 갱신
        e2e_page.click("#btn-discover-select-all")
        cnt_text = e2e_page.locator("#discover-selected-count").text_content() or ""
        assert "0개 선택" not in cnt_text
        # Tour Script 버튼 활성화
        expect(e2e_page.locator("#btn-discover-tour-script")).to_be_enabled()
    finally:
        httpd.shutdown()
        httpd.server_close()
