"""recording_service.post_process — portabilize_storage_path unit tests (P3.9).

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.7 (D3)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recording_service.post_process import portabilize_storage_path


# ── synthetic codegen output samples ────────────────────────────────────

_CODEGEN_DOUBLE_QUOTE = '''from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(storage_state="/Users/test/auth-profiles/booking.storage.json")
    page = context.new_page()
    page.goto("https://booking.example.com/")
'''

_CODEGEN_SINGLE_QUOTE = '''from playwright.sync_api import sync_playwright


def run(p):
    ctx = p.chromium.launch().new_context(storage_state='/abs/path/x.storage.json')
'''

_CODEGEN_RAW_STRING = '''from playwright.sync_api import sync_playwright


def run(p):
    ctx = p.chromium.launch().new_context(storage_state=r"/Users/x/abs/win-style.json")
'''

_CODEGEN_NO_STORAGE = '''from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/")
'''

_ALREADY_PORTABLE = '''import os
from playwright.sync_api import sync_playwright


def run(p):
    ctx = p.chromium.launch().new_context(storage_state=os.environ["AUTH_STORAGE_STATE_IN"])
'''


# ── tests ───────────────────────────────────────────────────────────────

class TestPortabilize:
    def test_double_quote_replaced(self, tmp_path: Path):
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_DOUBLE_QUOTE, encoding="utf-8")
        assert portabilize_storage_path(p) is True
        text = p.read_text(encoding="utf-8")
        assert 'storage_state=os.environ["AUTH_STORAGE_STATE_IN"]' in text
        assert "/Users/test/auth-profiles/booking.storage.json" not in text
        assert text.startswith("import os\n")

    def test_single_quote_replaced(self, tmp_path: Path):
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_SINGLE_QUOTE, encoding="utf-8")
        assert portabilize_storage_path(p) is True
        text = p.read_text(encoding="utf-8")
        assert 'os.environ["AUTH_STORAGE_STATE_IN"]' in text
        assert "/abs/path/x.storage.json" not in text

    def test_raw_string_replaced(self, tmp_path: Path):
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_RAW_STRING, encoding="utf-8")
        assert portabilize_storage_path(p) is True
        text = p.read_text(encoding="utf-8")
        assert "/Users/x/abs/win-style.json" not in text

    def test_no_storage_state_no_op(self, tmp_path: Path):
        """codegen output recorded without auth is left unchanged."""
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_NO_STORAGE, encoding="utf-8")
        assert portabilize_storage_path(p) is False
        # untouched.
        assert p.read_text(encoding="utf-8") == _CODEGEN_NO_STORAGE

    def test_already_portable_idempotent(self, tmp_path: Path):
        """If already env-var form, idempotent — returns False and file unchanged."""
        p = tmp_path / "original.py"
        p.write_text(_ALREADY_PORTABLE, encoding="utf-8")
        assert portabilize_storage_path(p) is False
        assert p.read_text(encoding="utf-8") == _ALREADY_PORTABLE

    def test_missing_file_returns_false(self, tmp_path: Path):
        assert portabilize_storage_path(tmp_path / "missing.py") is False

    def test_import_os_not_duplicated(self, tmp_path: Path):
        """Don't prepend when ``import os`` is already present."""
        p = tmp_path / "original.py"
        p.write_text(
            "import os\nimport sys\n" + _CODEGEN_DOUBLE_QUOTE,
            encoding="utf-8",
        )
        assert portabilize_storage_path(p) is True
        text = p.read_text(encoding="utf-8")
        # ``import os`` appears exactly once.
        assert text.count("import os\n") == 1

    def test_idempotent_double_run(self, tmp_path: Path):
        """Run portabilize twice — second call is a no-op."""
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_DOUBLE_QUOTE, encoding="utf-8")
        assert portabilize_storage_path(p) is True
        first = p.read_text(encoding="utf-8")
        assert portabilize_storage_path(p) is False
        assert p.read_text(encoding="utf-8") == first
