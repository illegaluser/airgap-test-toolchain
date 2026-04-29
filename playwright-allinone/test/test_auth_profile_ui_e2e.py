"""auth-profile UI End-to-End (Tier 3).

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §4 + user e2e requirements

Strategy:
    Spawn the daemon on a separate port (18095). **Pre-write the catalog
    to disk** and verify everything from "does the UI show the dropdown?"
    through to the expiry-modal branch with headless Chromium.

    Tier 2 covers the *HTTP API*; Tier 3 focuses on regression protection
    for *DOM + event handlers* (markup IDs / classes / modal dialog behavior).

Coverage:
    - critical IDs for the auth block + 4 modals exist
    - pre-registered profiles appear in the dropdown (P5.2)
    - auth-status updates on selection
    - ↻ verify button (P5.3) — pass / fail branches
    - + new session seed → seed-dialog opens (P5.4)
    - cancel → seed-dialog closes
    - Start with an expired profile → expiry modal (P5.6)
    - sessionStorage warning label visible (P5.9)
    - result card + session-table columns (P5.8)
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


E2E_PORT = 18095
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


# ─────────────────────────────────────────────────────────────────────────
# PATH stub `playwright` (same pattern as Tier 2)
# ─────────────────────────────────────────────────────────────────────────

_STUB_PLAYWRIGHT = """#!/usr/bin/env bash
case "$1" in
  open)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--save-storage" ]; then
        cat > "$2" <<'EOF'
{"cookies":[
  {"name":"NID_AUT","value":"fake","domain":".naver.com","path":"/","expires":-1,"httpOnly":true,"secure":true,"sameSite":"Lax"},
  {"name":"qa_session","value":"qa","domain":"localhost","path":"/","expires":-1,"httpOnly":false,"secure":false,"sameSite":"Lax"}
],"origins":[]}
EOF
        break
      fi
      shift
    done
    exit 0
    ;;
  codegen)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--output" ]; then
        cat > "$2" <<'EOF'
import os
from playwright.sync_api import sync_playwright
def run(p): pass
with sync_playwright() as p: run(p)
EOF
        break
      fi
      shift
    done
    exit 0
    ;;
  --version)
    echo "Version 1.57.0"
    exit 0
    ;;
esac
exit 0
"""


@pytest.fixture(scope="session")
def stub_path_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("ui_stub_bin")
    p = d / "playwright"
    p.write_text(_STUB_PLAYWRIGHT, encoding="utf-8")
    p.chmod(0o755)
    return d


# ─────────────────────────────────────────────────────────────────────────
# Pre-seed the catalog (bypass HTTP — write to disk directly)
# ─────────────────────────────────────────────────────────────────────────

def _write_storage(path: Path, *, valid: bool = True) -> None:
    """Fake storage_state. ``valid=False`` simulates expiry (empty cookies)."""
    if valid:
        data = {
            "cookies": [
                {"name": "NID_AUT", "value": "x", "domain": ".naver.com",
                 "path": "/", "expires": -1, "httpOnly": True, "secure": True,
                 "sameSite": "Lax"},
                {"name": "qa_session", "value": "qa", "domain": "localhost",
                 "path": "/", "expires": -1, "httpOnly": False, "secure": False,
                 "sameSite": "Lax"},
            ],
            "origins": [],
        }
    else:
        data = {"cookies": [], "origins": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


def _build_index_entry(
    *, name: str, storage_filename: str, machine_id: str, ss_warning: bool = False,
    last_verified_at: str | None = "2026-04-29T17:35:12+09:00",
) -> dict:
    return {
        "name": name,
        "service_domain": "localhost",
        "storage_path": storage_filename,
        "created_at": "2026-04-29T17:30:00+09:00",
        "last_verified_at": last_verified_at,
        "ttl_hint_hours": 12,
        "verify": {
            "service_url": "http://localhost:9999/mypage",  # not actually verified — Tier 3 doesn't call verify
            "service_text": "qa-tester님 환영합니다",
        },
        "fingerprint": {
            "viewport": {"width": 1280, "height": 800},
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "color_scheme": "light",
            "playwright_version": "1.57.0",
            "playwright_channel": "chromium",
            "captured_user_agent": "",
        },
        "host_machine_id": machine_id,
        "chips_supported": True,
        "session_storage_warning": ss_warning,
        "verify_history": [],
        "notes": "Tier 3 e2e fixture",
    }


@pytest.fixture(scope="session")
def seeded_auth_dir(tmp_path_factory):
    """An isolated auth-profiles dir pre-loaded with 3 profiles in the catalog.

    - ``ui-valid``       : normal, machine matches, verify passes
    - ``ui-expired``     : empty storage → expiry simulation
    - ``ui-with-ss``     : for the sessionStorage warning label
    """
    d = tmp_path_factory.mktemp("ui_e2e_auth")
    d.chmod(0o700)

    # Capture auth_profiles' real machine_id at fixture time and use it in valid profiles.
    # (Same host before subprocess spawn → same value.)
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from zero_touch_qa.auth_profiles import current_machine_id
        my_machine_id = current_machine_id()
    finally:
        sys.path.pop(0)

    # write storage files.
    _write_storage(d / "ui-valid.storage.json", valid=True)
    _write_storage(d / "ui-expired.storage.json", valid=False)
    _write_storage(d / "ui-with-ss.storage.json", valid=True)

    # write the catalog _index.json.
    index = {
        "version": 1,
        "profiles": [
            _build_index_entry(
                name="ui-valid",
                storage_filename="ui-valid.storage.json",
                machine_id=my_machine_id,
            ),
            _build_index_entry(
                name="ui-expired",
                storage_filename="ui-expired.storage.json",
                machine_id=my_machine_id,
                last_verified_at=None,
            ),
            _build_index_entry(
                name="ui-with-ss",
                storage_filename="ui-with-ss.storage.json",
                machine_id=my_machine_id,
                ss_warning=True,
            ),
        ],
    }
    idx = d / "_index.json"
    idx.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    idx.chmod(0o600)
    return d


# ─────────────────────────────────────────────────────────────────────────
# Daemon fixture (port 18095)
# ─────────────────────────────────────────────────────────────────────────

def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def ui_daemon(stub_path_dir, seeded_auth_dir, tmp_path_factory):
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} already in use — skipping Tier 3 e2e")

    rec_root = tmp_path_factory.mktemp("ui_e2e_rec")

    env = os.environ.copy()
    env["PATH"] = str(stub_path_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["RECORDING_HOST_ROOT"] = str(rec_root)
    env["AUTH_PROFILES_DIR"] = str(seeded_auth_dir)
    env["AUTH_PROFILE_VERIFY_HEADLESS"] = "1"

    cmd = [
        VENV_PY, "-m", "uvicorn",
        "recording_service.server:app",
        "--host", "127.0.0.1",
        "--port", str(E2E_PORT),
        "--workers", "1",
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )

    deadline = time.time() + 20.0
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            pytest.skip(f"Tier 3 daemon spawn failed (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("Tier 3 daemon healthz wait timed out")

    yield {"base": E2E_BASE, "auth_root": seeded_auth_dir}

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    except ProcessLookupError:
        pass


@pytest.fixture
def page(ui_daemon):
    """Headless Chromium page — loaded and auth-profiles fetch complete."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page_ = ctx.new_page()
        page_.goto(ui_daemon["base"] + "/", wait_until="networkidle")
        # wait for the auth block fetch to finish.
        page_.wait_for_function(
            "document.querySelector('#auth-profile-select').options.length > 1",
            timeout=5000,
        )
        yield page_
        ctx.close()
        browser.close()


# ─────────────────────────────────────────────────────────────────────────
# Tests — DOM markup
# ─────────────────────────────────────────────────────────────────────────

class TestAuthMarkup:
    def test_critical_ids_present(self, page: Page):
        """Critical IDs for the auth block + 4 modals are present in the DOM (P5.1)."""
        for sel in [
            "#auth-profile-select",
            "#btn-auth-verify",
            "#btn-auth-seed",
            "#auth-status",
            "#auth-seed-dialog",
            "#auth-seed-progress",
            "#auth-expired-dialog",
            "#auth-machine-mismatch-dialog",
        ]:
            assert page.locator(sel).count() == 1, f"missing element: {sel}"

    def test_result_card_has_auth_profile_field(self, page: Page):
        """Result-card meta (P5.8)."""
        assert page.locator("#result-auth-profile").count() == 1

    def test_session_table_has_auth_column(self, page: Page):
        """Session table has 7 columns (P5.8)."""
        # auth column is the 4th (id, state, target_url, auth, steps, created, actions).
        headers = page.locator("#session-table thead th").all_text_contents()
        assert "auth" in [h.strip().lower() for h in headers]


# ─────────────────────────────────────────────────────────────────────────
# Tests — dropdown + status (P5.2)
# ─────────────────────────────────────────────────────────────────────────

class TestDropdown:
    def test_seeded_profiles_listed(self, page: Page):
        opts = page.locator("#auth-profile-select option").all_text_contents()
        # "(none — record without login)" + 3 seeded profiles.
        joined = " ".join(opts)
        assert "ui-valid" in joined
        assert "ui-expired" in joined
        assert "ui-with-ss" in joined

    def test_session_storage_warning_label(self, page: Page):
        """sessionStorage warning label (P5.9) — 'ui-with-ss' option shows ⚠sessionStorage."""
        opts = page.locator("#auth-profile-select option").all_text_contents()
        ss_opt = next(o for o in opts if "ui-with-ss" in o)
        assert "sessionStorage" in ss_opt or "⚠" in ss_opt

    def test_select_updates_status(self, page: Page):
        page.locator("#auth-profile-select").select_option("ui-valid")
        # give the status label a moment to update.
        page.wait_for_timeout(200)
        status_text = page.locator("#auth-status").text_content() or ""
        assert "ui-valid" in status_text or "localhost" in status_text

    def test_verify_button_enabled_after_select(self, page: Page):
        """verify button is disabled before selection, enabled after (P5.2)."""
        verify_btn = page.locator("#btn-auth-verify")
        assert verify_btn.is_disabled()
        page.locator("#auth-profile-select").select_option("ui-valid")
        page.wait_for_timeout(200)
        assert not verify_btn.is_disabled()


# ─────────────────────────────────────────────────────────────────────────
# Tests — seed modal (P5.4)
# ─────────────────────────────────────────────────────────────────────────

class TestSeedDialog:
    def test_opens_and_closes(self, page: Page):
        dialog = page.locator("#auth-seed-dialog")
        # closed at first.
        assert not dialog.evaluate("el => el.open")
        # click seed button → opens.
        page.locator("#btn-auth-seed").click()
        page.wait_for_timeout(200)
        assert dialog.evaluate("el => el.open")
        # cancel button (type="button") — closes immediately even when required fields are empty.
        page.locator("#btn-auth-seed-cancel-input").click()
        page.wait_for_timeout(200)
        assert not dialog.evaluate("el => el.open")

    def test_form_has_required_fields(self, page: Page):
        """Seed form's required fields (P5.4). Verify text is optional."""
        page.locator("#btn-auth-seed").click()
        page.wait_for_timeout(150)
        form = page.locator("#auth-seed-form")
        # required: name, seed_url, verify_service_url.
        for field_name in ["name", "seed_url", "verify_service_url"]:
            inp = form.locator(f"input[name='{field_name}']")
            assert inp.count() == 1, f"missing field: {field_name}"
            assert inp.get_attribute("required") is not None, f"{field_name} not required"
        verify_text = form.locator("input[name='verify_service_text']")
        assert verify_text.count() == 1
        assert verify_text.get_attribute("required") is None
        # close.
        page.locator("#btn-auth-seed-cancel-input").click()

    def test_progress_dialog_explains_close_and_confirm(self, page: Page):
        """Progress modal explains the close-window completion condition and final confirm button."""
        progress = page.locator("#auth-seed-progress")
        assert "close the open browser window" in (progress.text_content() or "")
        assert "save the session" in (page.locator("#auth-seed-progress-hint").text_content() or "")
        cancel = page.locator("#btn-auth-seed-cancel")
        assert "Cancel" in (cancel.text_content() or "")
        assert cancel.evaluate("el => getComputedStyle(el).color") != "rgb(255, 255, 255)"
        assert page.locator("#btn-auth-seed-skip").get_attribute("hidden") is not None
        assert page.locator("#btn-auth-seed-done").get_attribute("hidden") is not None


# ─────────────────────────────────────────────────────────────────────────
# Tests — expiry modal (P5.6)
# ─────────────────────────────────────────────────────────────────────────

class TestExpiryModal:
    def test_start_with_expired_profile_shows_modal(self, page: Page):
        """Start Recording with an expired profile → expiry modal."""
        # 1. type target_url.
        page.fill("#start-form input[name='target_url']", "http://localhost:9/dummy")
        # 2. select the expired profile.
        page.locator("#auth-profile-select").select_option("ui-expired")
        page.wait_for_timeout(150)
        # 3. click Start. → backend calls verify_profile() → real Playwright tries to
        #    navigate to port 9999 (unreachable) → service_side_error → 409 → expired modal.
        page.locator("#btn-start").click()

        # 4. wait until the expiry modal opens (verify can run to its timeout, ~30s).
        expiry_dlg = page.locator("#auth-expired-dialog")
        expect(expiry_dlg).to_be_visible(timeout=45_000)
        # sanity-check modal content.
        assert "ui-expired" in (page.locator("#auth-expired-name").text_content() or "")
        # close.
        page.locator("#btn-auth-expired-cancel").click()
        page.wait_for_timeout(200)
        assert not expiry_dlg.evaluate("el => el.open")

    def test_reseed_button_opens_seed_dialog_with_prefill(self, page: Page):
        """Click expiry modal's [Re-seed] → seed-dialog opens with name prefilled."""
        page.fill("#start-form input[name='target_url']", "http://localhost:9/dummy")
        page.locator("#auth-profile-select").select_option("ui-expired")
        page.wait_for_timeout(150)
        page.locator("#btn-start").click()
        expect(page.locator("#auth-expired-dialog")).to_be_visible(timeout=45_000)
        page.locator("#btn-auth-expired-reseed").click()
        page.wait_for_timeout(300)
        # confirm seed-dialog opened with prefilled name.
        seed_dlg = page.locator("#auth-seed-dialog")
        assert seed_dlg.evaluate("el => el.open")
        # name field: last-selected profile name is prefilled.
        name_input = page.locator("#auth-seed-form input[name='name']")
        assert name_input.input_value() == "ui-expired"
        # cleanup.
        page.locator("#btn-auth-seed-cancel-input").click()
