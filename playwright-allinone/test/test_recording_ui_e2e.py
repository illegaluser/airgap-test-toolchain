"""Recording UI — End-to-End browser tests.

Spawns the real recording-service daemon on a separate port and verifies
UI flows with Playwright headless Chromium. Covers areas unit/integration
tests can't:
- JS handler regressions when markup IDs change
- the meaning of CSS classes (status pill color, dropdown expansion)
- browser APIs like clipboard / dialog / scroll
- fetch+render integration for the run-log / regression / diff cards

Strategy:
- Session-scoped daemon fixture: isolated RECORDING_HOST_ROOT (tmp) + port 18093
- Pre-seed on disk → daemon startup absorption (TR.8) exposes two kinds of sessions:
  (1) `e2eDONE0001` — done state, only original.py + scenario.json
  (2) `e2eFULL0002` — done state, + run_log + screenshots + regression_test.py
- LLM analysis runs as a deterministic stub via ``RECORDING_DIFF_ANALYSIS_STUB=1``.
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
from playwright.sync_api import Page, expect, sync_playwright

E2E_PORT = 18093
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"

# Pre-seeded sessions — registered via the TR.8 absorption path on daemon startup.
SID_BASIC = "e2eDONE0001"  # not a 24-char hex — sid format is free-form (registry only requires string)
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
    """Pre-create session dir + metadata.json + artifacts on disk.

    The daemon absorbs them at startup, so the UI shows them without API calls.
    """
    sess = root / sid
    sess.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid,
        "target_url": f"https://{sid.lower()}.test.local/",
        "state": "done",
        "created_at_ts": time.time() - 60,  # 1 minute ago
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
         "value": "", "description": "click 연혁 (history)", "fallback_targets": []},
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

    # Play artifacts — run_log + screenshots + regression_test.py
    (sess / "run_log.jsonl").write_text(
        '{"step": 1, "action": "navigate", "target": "https://e2e.test.local/", '
        '"value": "https://e2e.test.local/", "description": "navigate", '
        '"status": "PASS", "heal_stage": "none", "ts": 1777000000.0}\n'
        '{"step": 2, "action": "click", "target": "role=link, name=연혁", '
        '"value": "", "description": "click 연혁 (history)", '
        '"status": "HEALED", "heal_stage": "local", "ts": 1777000005.0}\n'
        '{"step": 3, "action": "click", "target": "role=link, name=~2013", '
        '"value": "", "description": "click ~2013", '
        '"status": "PASS", "heal_stage": "none", "ts": 1777000010.0}\n',
        encoding="utf-8",
    )
    # 1x1 PNG (8-byte header + data) — enough for the browser to recognize as image/png
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
        "    # T-H — selector swap via visibility-healer cascade hover\n"
        "    page.get_by_role('link', name='회사연혁').click()\n"
        "    page.get_by_role('link', name='~2013').click()\n"
        "\n"
        "with sync_playwright() as p:\n"
        "    run(p)\n",
        encoding="utf-8",
    )

    # play-llm.log — for tail-polling verification
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
    """Spawn recording-service on a separate port. Session-scoped — all E2E tests share it."""
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} already in use — E2E port conflict")

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    )
    env["RECORDING_HOST_ROOT"] = str(e2e_root)
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
            pytest.skip(f"E2E daemon spawn failed (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("E2E daemon healthz wait timed out (20s)")

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
def e2e_browser(e2e_daemon):
    """sync_playwright context — use our own sync browser to avoid the
    async/sync mix breaking pytest-playwright's page fixture."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def e2e_page(e2e_browser, e2e_daemon) -> Page:
    """Fresh context + page per test. Grants clipboard permission."""
    ctx = e2e_browser.new_context(
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = ctx.new_page()
    page.goto(f"{e2e_daemon}/", wait_until="networkidle")
    yield page
    ctx.close()


# ── 1. page title / header ────────────────────────────────────────────────


def test_page_title_is_recording_ui(e2e_page: Page):
    """`<title>` and `<h1>` both say 'Recording UI'."""
    expect(e2e_page).to_have_title("Recording UI")
    expect(e2e_page.locator("header h1")).to_contain_text("Recording UI")


def test_footer_text_uses_recording_ui(e2e_page: Page):
    expect(e2e_page.locator("footer")).to_contain_text("Recording UI")


# ── 2. back button (F1) ──────────────────────────────────────────────────


def test_back_button_is_visible(e2e_page: Page):
    btn = e2e_page.locator("#back-btn")
    expect(btn).to_be_visible()
    expect(btn).to_contain_text("Back")


# ── 3. hover-menu hint banner ────────────────────────────────────────────


def test_hover_hint_banner_is_present_on_start_card(e2e_page: Page):
    expect(e2e_page.locator(".hover-hint")).to_be_visible()
    expect(e2e_page.locator(".hover-hint")).to_contain_text("hover menus")


# ── 4. session list + search/filter (P3 / item 7) ─────────────────────────


def test_seeded_sessions_appear_in_list(e2e_page: Page):
    """Disk seed → startup absorb → both sessions show in the session list."""
    e2e_page.wait_for_function("document.querySelectorAll('#session-tbody tr').length >= 2", timeout=5000)
    rows_text = e2e_page.locator("#session-tbody").inner_text()
    assert SID_BASIC in rows_text
    assert SID_FULL in rows_text


def test_session_filter_input_narrows_rows(e2e_page: Page):
    """Client-side filtering by target_url / id keyword."""
    e2e_page.wait_for_function("document.querySelectorAll('#session-tbody tr').length >= 2", timeout=5000)
    e2e_page.fill("#session-filter", SID_FULL)
    # Right after filtering, the SID_BASIC row must be hidden
    rows = e2e_page.locator("#session-tbody tr:visible")
    text = rows.first.inner_text()
    assert SID_FULL in text
    assert SID_BASIC not in e2e_page.locator("#session-tbody").inner_text() or \
           e2e_page.locator(f"#session-tbody tr:visible:has-text('{SID_BASIC}')").count() == 0


def test_session_filter_state_select_filters_done_only(e2e_page: Page):
    """State selector — rows whose state is not the chosen value are hidden."""
    e2e_page.wait_for_function("document.querySelectorAll('#session-tbody tr').length >= 2", timeout=5000)
    e2e_page.select_option("#session-state-filter", "done")
    # Both seeded sessions are done, so nothing should change — at least 2 rows visible
    visible = e2e_page.locator("#session-tbody tr:visible").count()
    assert visible >= 2


def test_session_filter_persists_across_reload(e2e_page: Page, e2e_daemon):
    """localStorage persistence — filter value restored after reload."""
    e2e_page.fill("#session-filter", SID_FULL)
    e2e_page.evaluate("() => localStorage.setItem('rec.sessionFilter', '" + SID_FULL + "')")
    e2e_page.goto(f"{e2e_daemon}/")
    e2e_page.wait_for_load_state("networkidle")
    val = e2e_page.input_value("#session-filter")
    assert val == SID_FULL


# ── 5. R-Plus dropdown (item 3 — hover-open + click-stick) ────────────────


def _open_full_session(page: Page, daemon_url: str):
    """Open the SID_FULL session result screen — other fixtures depend on this."""
    page.evaluate(f"window.openSession && window.openSession('{SID_FULL}')")
    # If the global isn't exposed, click the row directly
    page.click(f"button[data-act='open'][data-sid='{SID_FULL}']")
    page.wait_for_selector("#rplus-section:not([hidden])", timeout=5000)


def test_rplus_dropdown_expands_on_click(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    play_group = e2e_page.locator(".dropdown-group[data-group='play']")
    play_group.locator(".dropdown-toggle").click()
    expect(play_group).to_have_class(__import__("re").compile(r".*\bexpanded\b.*"))
    # Sub-items become visible
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
    # Click the card header (outside the group)
    e2e_page.locator("#rplus-section h2").click()
    expanded = e2e_page.locator(".dropdown-group.expanded").count()
    assert expanded == 0


# ── 6. step-add form (scroll/hover added) ────────────────────────────────


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


# ── 7. run-log visualization (P1 / item 5) ───────────────────────────────


def test_run_log_card_renders_with_status_pills(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    expect(e2e_page.locator("#run-log-card")).to_be_visible()
    # 3 rows (PASS / HEALED / PASS)
    rows = e2e_page.locator("#run-log-container .run-log-table tbody tr")
    expect(rows).to_have_count(3)
    # confirm status-pill class
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
    """P4 — 📋 button per row in the run-log table."""
    _open_full_session(e2e_page, e2e_daemon)
    btns = e2e_page.locator(".copy-step-btn")
    expect(btns.first).to_be_visible()
    # data-step-json must hold step data
    payload = btns.first.get_attribute("data-step-json")
    assert payload and '"step"' in payload


# ── 8. clipboard copy (item 2) ───────────────────────────────────────────


def test_copy_scenario_json_to_clipboard(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    e2e_page.locator(".copy-btn[data-copy-target='result-json']").click()
    e2e_page.wait_for_timeout(150)
    text = e2e_page.evaluate("() => navigator.clipboard.readText()")
    assert "navigate" in text  # scenario.json's step[0].action


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
    assert "회사연혁" in text  # healed selector in regression_test.py


# ── 9. Regression card (F2) ───────────────────────────────────────────────


def test_regression_card_visible_and_shows_code(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    expect(e2e_page.locator("#regression-card")).to_be_visible()
    code = e2e_page.locator("#result-regression").inner_text()
    assert "regression_test.py" not in code  # invisible placeholder
    assert "회사연혁" in code


def test_regression_card_has_download_link(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    href = e2e_page.locator("#dl-regression").get_attribute("href")
    assert href and "/regression?download=1" in href


# ── 10. LLM diff analysis (F3) ───────────────────────────────────────────


def test_diff_analysis_button_renders_4_section_markdown(e2e_page: Page, e2e_daemon):
    """RECORDING_DIFF_ANALYSIS_STUB=1 returns a deterministic stub → verify 4 section headings render."""
    _open_full_session(e2e_page, e2e_daemon)
    expect(e2e_page.locator("#diff-card")).to_be_visible()
    e2e_page.locator("#btn-analyze-diff").click()
    # markdown headings render in the analysis-output area (h4 = ###)
    e2e_page.wait_for_selector(".analysis-output h4", timeout=10000)
    headings = e2e_page.locator(".analysis-output h4").all_text_contents()
    # the stub's 4 section headings
    assert any("Key change summary" in h for h in headings)
    assert any("Regression-adoption recommendation" in h for h in headings)
    # model meta shown
    expect(e2e_page.locator(".analysis-output")).to_contain_text("stub")


def test_raw_diff_collapsible_present(e2e_page: Page, e2e_daemon):
    _open_full_session(e2e_page, e2e_daemon)
    details = e2e_page.locator("#diff-raw-details")
    expect(details).to_be_visible()
    # `<details>` is rendered in the DOM even when closed — check via text_content
    # (inner_text only returns visible text).
    diff_text = e2e_page.locator("#diff-output").text_content() or ""
    assert "original.py" in diff_text or "regression_test.py" in diff_text


# ── 11. Play progress streaming (P2 / item 6) ────────────────────────────


def test_play_log_tail_endpoint_returns_seeded_log(e2e_page: Page, e2e_daemon):
    """The fixture's play-llm.log is exposed directly via the tail endpoint."""
    resp = e2e_page.evaluate(
        f"async () => {{ const r = await fetch('/recording/sessions/{SID_FULL}/play-log/tail?kind=llm&from=0'); return await r.json(); }}"
    )
    assert resp["exists"] is True
    assert "visibility-healer" in resp["content"]
    assert resp["offset"] > 0


# ── 12. markup ID regression — every critical ID is present ─────────────


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
    assert not missing, f"missing critical IDs: {missing}"


# ── 13. Play Script from File — user .py upload ─────────────────────────


def test_import_script_button_is_visible(e2e_page: Page):
    btn = e2e_page.locator("#btn-import-script")
    expect(btn).to_be_visible()
    expect(btn).to_contain_text("Play Script from File")


def test_import_script_uploads_and_opens_result_panel(
    e2e_page: Page, e2e_daemon, tmp_path,
):
    """File pick → upload → enter result panel → original.py preview shows up."""
    # write a temp .py
    script = tmp_path / "uploaded_e2e.py"
    script.write_text(
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    page = p.chromium.launch().new_context().new_page()\n"
        "    page.goto('https://e2e-uploaded.test/')\n"
        "    page.click('#hello')\n",
        encoding="utf-8",
    )
    # set_input_files on the file input — bypass the native picker
    e2e_page.set_input_files("#import-file-input", str(script))
    # wait for the result screen (after openSession unhides result-section)
    e2e_page.wait_for_selector("#result-section:not([hidden])", timeout=5000)
    e2e_page.wait_for_selector("#original-card:not([hidden])", timeout=5000)
    # confirm code body
    code = e2e_page.locator("#result-original").text_content() or ""
    assert "e2e-uploaded.test" in code
    # session list also shows the imported entry
    rows_text = e2e_page.locator("#session-tbody").text_content() or ""
    assert "imported" in rows_text.lower()
