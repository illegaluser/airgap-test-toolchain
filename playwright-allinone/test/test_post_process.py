"""recording_service.post_process — portabilize_storage_path 단위 테스트 (P3.9).

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.7 (D3)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recording_service.post_process import portabilize_storage_path


# ── 합성 codegen 출력 샘플 ──────────────────────────────────────────────

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


# ── 테스트 ──────────────────────────────────────────────────────────────

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
        """인증 없이 녹화한 codegen 출력은 변경되지 않는다."""
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_NO_STORAGE, encoding="utf-8")
        assert portabilize_storage_path(p) is False
        # 원본 그대로.
        assert p.read_text(encoding="utf-8") == _CODEGEN_NO_STORAGE

    def test_already_portable_idempotent(self, tmp_path: Path):
        """이미 env var 형태면 멱등성 — False 반환 + 파일 미수정."""
        p = tmp_path / "original.py"
        p.write_text(_ALREADY_PORTABLE, encoding="utf-8")
        assert portabilize_storage_path(p) is False
        assert p.read_text(encoding="utf-8") == _ALREADY_PORTABLE

    def test_missing_file_returns_false(self, tmp_path: Path):
        assert portabilize_storage_path(tmp_path / "missing.py") is False

    def test_import_os_not_duplicated(self, tmp_path: Path):
        """이미 ``import os`` 가 있으면 prepend 안 함."""
        p = tmp_path / "original.py"
        p.write_text(
            "import os\nimport sys\n" + _CODEGEN_DOUBLE_QUOTE,
            encoding="utf-8",
        )
        assert portabilize_storage_path(p) is True
        text = p.read_text(encoding="utf-8")
        # ``import os`` 한 번만 등장.
        assert text.count("import os\n") == 1

    def test_idempotent_double_run(self, tmp_path: Path):
        """portabilize 두 번 실행 — 두 번째는 no-op."""
        p = tmp_path / "original.py"
        p.write_text(_CODEGEN_DOUBLE_QUOTE, encoding="utf-8")
        assert portabilize_storage_path(p) is True
        first = p.read_text(encoding="utf-8")
        assert portabilize_storage_path(p) is False
        assert p.read_text(encoding="utf-8") == first
