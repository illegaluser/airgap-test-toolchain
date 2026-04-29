"""auth subcommand CLI (P2) unit tests.

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §9 P2.1 ~ P2.4

Coverage:
- argparse tree (required args, SystemExit when missing)
- list / verify / delete handlers — success/failure return codes
- ``--json`` output shape (list / verify)
- seed handler wiring (mocks auth_profiles.seed_profile)
- no conflict with the legacy ``--mode`` CLI

Real ``playwright open`` calls are mocked. auth_profiles' seed_profile
itself was already verified in P1, so this file only checks the *CLI
wiring*.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from zero_touch_qa import auth_profiles as ap
from zero_touch_qa.__main__ import (
    _auth_handle_delete,
    _auth_handle_list,
    _auth_handle_seed,
    _auth_handle_verify,
    _build_auth_parser,
    _run_auth_cli,
)
from zero_touch_qa.auth_profiles import (
    AuthProfile,
    FingerprintProfile,
    NaverProbeSpec,
    ProfileNotFoundError,
    SeedVerifyFailedError,
    VerifySpec,
    _upsert_profile,
)


# ─────────────────────────────────────────────────────────────────────────
# fixture
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path))
    return tmp_path


def _make_profile(name: str, root: Path, *, last_verified: str | None = None) -> AuthProfile:
    return AuthProfile(
        name=name,
        service_domain=f"{name}.example.com",
        storage_path=root / f"{name}.storage.json",
        created_at="2026-04-29T17:30:00+09:00",
        last_verified_at=last_verified,
        ttl_hint_hours=12,
        verify=VerifySpec(
            service_url=f"https://{name}.example.com/mypage",
            service_text="hello",
        ),
        fingerprint=FingerprintProfile.default(),
        host_machine_id="MAC:test",
        chips_supported=True,
        session_storage_warning=False,
    )


def _capture(handler, args_ns: SimpleNamespace) -> tuple[int, str]:
    """Call the handler and capture stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = handler(args_ns, ap)
    return rc, buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# P2.1 — argparse tree
# ─────────────────────────────────────────────────────────────────────────

class TestArgparse:
    def test_help_runs(self):
        """--help exits with SystemExit(0)."""
        parser = _build_auth_parser()
        with pytest.raises(SystemExit) as ei:
            parser.parse_args(["--help"])
        assert ei.value.code == 0

    def test_missing_action(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_seed_requires_name(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "seed",
                "--seed-url", "https://x.com/",
                "--verify-service-url", "https://x.com/mypage",
                "--verify-service-text", "hi",
            ])

    def test_seed_full_args(self):
        parser = _build_auth_parser()
        args = parser.parse_args([
            "seed",
            "--name", "alpha",
            "--seed-url", "https://x.com/",
            "--verify-service-url", "https://x.com/mypage",
            "--verify-service-text", "hi",
            "--no-naver-probe",
            "--ttl-hint-hours", "24",
            "--notes", "test",
            "--timeout-sec", "300",
        ])
        assert args.action == "seed"
        assert args.name == "alpha"
        assert args.no_naver_probe is True
        assert args.ttl_hint_hours == 24
        assert args.timeout_sec == 300

    def test_seed_verify_text_optional(self):
        """Omitting verify text → weak-verify mode that only checks protected-URL access."""
        parser = _build_auth_parser()
        args = parser.parse_args([
            "seed",
            "--name", "alpha",
            "--seed-url", "https://x.com/",
            "--verify-service-url", "https://x.com/mypage",
        ])
        assert args.verify_service_text == ""

    def test_list_json_flag(self):
        parser = _build_auth_parser()
        args = parser.parse_args(["list", "--json"])
        assert args.action == "list"
        assert args.as_json is True

    def test_verify_required_name(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["verify"])

    def test_delete_required_name(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["delete"])


# ─────────────────────────────────────────────────────────────────────────
# P2.3 — list handler
# ─────────────────────────────────────────────────────────────────────────

class TestListHandler:
    def test_empty_human_readable(self, isolated_root: Path):
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=False))
        assert rc == 0
        assert "(no profiles registered)" in out

    def test_empty_json(self, isolated_root: Path):
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=True))
        assert rc == 0
        assert json.loads(out) == []

    def test_populated_human_readable(self, isolated_root: Path):
        _upsert_profile(_make_profile("alpha", isolated_root))
        _upsert_profile(_make_profile("bravo", isolated_root, last_verified="2026-04-29T18:00:00+09:00"))
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=False))
        assert rc == 0
        assert "alpha" in out
        assert "bravo" in out
        assert "2 profiles" in out

    def test_populated_json_shape(self, isolated_root: Path):
        _upsert_profile(_make_profile("alpha", isolated_root, last_verified="2026-04-29T17:35:12+09:00"))
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=True))
        assert rc == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "alpha"
        assert data[0]["service_domain"] == "alpha.example.com"
        assert data[0]["last_verified_at"] == "2026-04-29T17:35:12+09:00"
        assert data[0]["ttl_hint_hours"] == 12
        assert data[0]["chips_supported"] is True


# ─────────────────────────────────────────────────────────────────────────
# P2.3 — verify handler
# ─────────────────────────────────────────────────────────────────────────

class TestVerifyHandler:
    def _args(self, **kwargs) -> SimpleNamespace:
        defaults = {
            "name": "alpha",
            "no_naver_probe": False,
            "timeout_sec": 10,
            "as_json": False,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_missing_profile_returns_1(self, isolated_root: Path):
        rc, _ = _capture(_auth_handle_verify, self._args(name="ghost"))
        assert rc == 1

    def test_pass_through_naver_probe_flag(self, isolated_root: Path, monkeypatch):
        """--no-naver-probe is passed through to verify_profile as naver_probe=False."""
        _upsert_profile(_make_profile("alpha", isolated_root))
        captured = {}

        def fake_verify(prof, **kwargs):
            captured.update(kwargs)
            return True, {"service_ms": 100, "naver_probe_ms": None, "naver_ok": None}

        monkeypatch.setattr(ap, "verify_profile", fake_verify)
        rc, _ = _capture(_auth_handle_verify, self._args(no_naver_probe=True, timeout_sec=42))
        assert rc == 0
        assert captured["naver_probe"] is False
        assert captured["timeout_sec"] == 42

    def test_success_human(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {"service_ms": 200, "naver_probe_ms": 150, "naver_ok": True}),
        )
        rc, out = _capture(_auth_handle_verify, self._args())
        assert rc == 0
        assert "✓ OK" in out
        assert "200" in out

    def test_failure_human(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {
                "service_ms": 100, "naver_probe_ms": None, "naver_ok": None,
                "fail_reason": "service_text_not_found",
            }),
        )
        rc, out = _capture(_auth_handle_verify, self._args())
        assert rc == 1
        assert "✗ FAIL" in out
        assert "service_text_not_found" in out

    def test_success_json(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {"service_ms": 200, "naver_probe_ms": 150, "naver_ok": True}),
        )
        rc, out = _capture(_auth_handle_verify, self._args(as_json=True))
        assert rc == 0
        data = json.loads(out)
        assert data["ok"] is True
        assert data["service_ms"] == 200
        assert data["naver_ok"] is True

    def test_failure_json(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {"fail_reason": "x"}),
        )
        rc, out = _capture(_auth_handle_verify, self._args(as_json=True))
        assert rc == 1
        data = json.loads(out)
        assert data["ok"] is False
        assert data["fail_reason"] == "x"


# ─────────────────────────────────────────────────────────────────────────
# P2.3 — delete handler
# ─────────────────────────────────────────────────────────────────────────

class TestDeleteHandler:
    def test_missing_returns_1(self, isolated_root: Path):
        rc, _ = _capture(_auth_handle_delete, SimpleNamespace(name="ghost"))
        assert rc == 1

    def test_existing_removed(self, isolated_root: Path):
        prof = _make_profile("alpha", isolated_root)
        _upsert_profile(prof)
        # also create the storage file.
        prof.storage_path.write_text("{}", encoding="utf-8")

        rc, out = _capture(_auth_handle_delete, SimpleNamespace(name="alpha"))
        assert rc == 0
        assert "delete complete" in out
        with pytest.raises(ProfileNotFoundError):
            ap.get_profile("alpha")
        assert not prof.storage_path.exists()


# ─────────────────────────────────────────────────────────────────────────
# P2.2 — seed handler (mocks auth_profiles.seed_profile)
# ─────────────────────────────────────────────────────────────────────────

class TestSeedHandler:
    def _args(self, **overrides) -> SimpleNamespace:
        defaults = {
            "name": "booking-via-naver",
            "seed_url": "https://booking.example.com/",
            "verify_service_url": "https://booking.example.com/mypage",
            "verify_service_text": "환영합니다",
            "no_naver_probe": False,
            "service_domain": None,
            "ttl_hint_hours": 12,
            "notes": "",
            "timeout_sec": 600,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_calls_seed_profile_with_verify_spec(self, isolated_root: Path, monkeypatch):
        captured = {}

        def fake_seed(**kwargs):
            captured.update(kwargs)
            return _make_profile("booking-via-naver", isolated_root, last_verified="now")

        monkeypatch.setattr(ap, "seed_profile", fake_seed)
        rc, out = _capture(_auth_handle_seed, self._args(notes="first"))
        assert rc == 0
        assert "✅ seed complete" in out
        assert captured["name"] == "booking-via-naver"
        assert captured["seed_url"] == "https://booking.example.com/"
        assert captured["notes"] == "first"
        # verify spec's naver_probe is set to NaverProbeSpec (no --no-naver-probe).
        verify_spec = captured["verify"]
        assert verify_spec.service_url == "https://booking.example.com/mypage"
        assert verify_spec.service_text == "환영합니다"
        assert isinstance(verify_spec.naver_probe, NaverProbeSpec)

    def test_no_naver_probe_flag_drops_probe(self, isolated_root: Path, monkeypatch):
        captured = {}

        def fake_seed(**kwargs):
            captured.update(kwargs)
            return _make_profile("alpha", isolated_root)

        monkeypatch.setattr(ap, "seed_profile", fake_seed)
        rc, _ = _capture(_auth_handle_seed, self._args(name="alpha", no_naver_probe=True))
        assert rc == 0
        assert captured["verify"].naver_probe is None

    def test_seed_failure_returns_1(self, isolated_root: Path, monkeypatch):
        def fake_seed(**kwargs):
            raise SeedVerifyFailedError("verify failed: x")

        monkeypatch.setattr(ap, "seed_profile", fake_seed)
        rc, _ = _capture(_auth_handle_seed, self._args())
        assert rc == 1


# ─────────────────────────────────────────────────────────────────────────
# Integration — _run_auth_cli entry point (argparse + dispatch)
# ─────────────────────────────────────────────────────────────────────────

class TestRunAuthCli:
    def test_list_via_run_auth_cli(self, isolated_root: Path):
        rc = _run_auth_cli(["list"])
        assert rc == 0

    def test_unknown_action_argparse_rejects(self, isolated_root: Path):
        with pytest.raises(SystemExit):
            _run_auth_cli(["unknown-action"])


# ─────────────────────────────────────────────────────────────────────────
# Compat — confirm the existing ``--mode`` CLI is unaffected (subprocess check)
# ─────────────────────────────────────────────────────────────────────────

class TestLegacyModeCompat:
    def test_legacy_mode_execute_still_errors(self, tmp_path: Path):
        """``--mode execute`` (missing args) still returns rc=1 with the existing error message."""
        # call the real entry point via subprocess to verify argv routing.
        # Use sys.executable so the interpreter sees venv deps (requests, etc.).
        result = subprocess.run(
            [sys.executable, "-m", "zero_touch_qa", "--mode", "execute"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "AUTH_PROFILES_DIR": str(tmp_path)},
        )
        assert result.returncode == 1, f"stderr: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "execute mode requires the --scenario argument" in combined

    def test_auth_subcommand_does_not_trigger_mode_arg(self, tmp_path: Path):
        """``auth list`` must not raise the required-``--mode`` error."""
        result = subprocess.run(
            [sys.executable, "-m", "zero_touch_qa", "auth", "list"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "AUTH_PROFILES_DIR": str(tmp_path)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        combined = result.stdout + result.stderr
        # the empty-catalog message must appear, not a --mode error.
        assert "(no profiles registered)" in combined
        assert "--mode" not in combined
