"""replay_proxy auth-profile auto-matching (P4.2 ~ P4.4).

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.8

Coverage:
- when metadata.json has auth_profile:
  - run_llm_play  → injects --storage-state-in into cmd + PLAYWRIGHT_* into env
  - run_codegen_replay → injects AUTH_STORAGE_STATE_IN + PLAYWRIGHT_* into env
- when metadata.json has no auth_profile — original behavior (no env injection)
- on verify failure → ReplayAuthExpiredError (P4.4) — UI can branch to expired modal
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from recording_service import replay_proxy
from recording_service.replay_proxy import (
    PlayResult,
    ReplayAuthExpiredError,
    ReplayProxyError,
    _resolve_auth_for_replay,
    run_codegen_replay,
    run_llm_play,
)


# ─────────────────────────────────────────────────────────────────────────
# fixture
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    return tmp_path


@pytest.fixture
def session_dir(isolated_root: Path) -> Path:
    """Recording session dir + empty metadata.json + scenario.json + original.py."""
    sd = isolated_root / "session-abc"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "metadata.json").write_text("{}", encoding="utf-8")
    (sd / "scenario.json").write_text("[]", encoding="utf-8")
    (sd / "original.py").write_text("# stub\n", encoding="utf-8")
    return sd


def _seed_profile_in_catalog(name: str, isolated_root: Path) -> Path:
    """Register a synthetic profile in the auth_profiles catalog + create a fake storage file."""
    from zero_touch_qa.auth_profiles import (
        AuthProfile, FingerprintProfile, NaverProbeSpec, VerifySpec,
        _ensure_root, _upsert_profile,
    )
    _ensure_root()
    storage_p = isolated_root / "auth-profiles" / f"{name}.storage.json"
    storage_p.parent.mkdir(parents=True, exist_ok=True)
    storage_p.write_text(
        json.dumps({"cookies": [{"name": "x", "domain": ".example.com", "value": "v"}],
                    "origins": []}),
        encoding="utf-8",
    )
    prof = AuthProfile(
        name=name,
        service_domain="example.com",
        storage_path=storage_p,
        created_at="2026-04-29T10:00:00+09:00",
        last_verified_at=None,
        ttl_hint_hours=12,
        verify=VerifySpec(
            service_url="https://example.com/mypage",
            service_text="hello",
        ),
        fingerprint=FingerprintProfile(
            viewport_width=1280,
            viewport_height=800,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            color_scheme="light",
            playwright_version="1.57.0",
        ),
        host_machine_id="MAC:test",
        chips_supported=True,
        session_storage_warning=False,
    )
    _upsert_profile(prof)
    return storage_p


def _stub_subprocess_capture(monkeypatch):
    """Intercept subprocess.run and capture cmd/env. Simulates a clean exit."""
    captured = {"cmd": None, "env": None, "cwd": None}

    class _FakeProc:
        returncode = 0
        stdout = b""
        stderr = b""

    def _stub(*a, **kw):
        cmd = a[0] if a else kw.get("args", [])
        captured["cmd"] = list(cmd)
        captured["env"] = kw.get("env")
        captured["cwd"] = kw.get("cwd")
        return _FakeProc()

    monkeypatch.setattr(replay_proxy.subprocess, "run", _stub)
    return captured


# ─────────────────────────────────────────────────────────────────────────
# P4.2 — run_llm_play auto-matching
# ─────────────────────────────────────────────────────────────────────────

class TestRunLlmPlayAuth:
    def test_no_metadata_no_auth_injection(self, session_dir: Path, monkeypatch):
        """No auth_profile key in metadata.json → original behavior."""
        captured = _stub_subprocess_capture(monkeypatch)
        run_llm_play(host_session_dir=str(session_dir), project_root=".")
        assert "--storage-state-in" not in captured["cmd"]
        env = captured["env"] or {}
        assert "AUTH_STORAGE_STATE_IN" not in env
        assert "PLAYWRIGHT_VIEWPORT" not in env

    def test_auth_profile_injects_storage_and_env(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        """auth_profile meta + verify passes → cmd gets --storage-state-in + env injected."""
        storage_p = _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        # mock verify pass.
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {"service_ms": 100}),
        )
        captured = _stub_subprocess_capture(monkeypatch)
        run_llm_play(host_session_dir=str(session_dir), project_root=".")

        cmd = captured["cmd"]
        assert "--storage-state-in" in cmd
        idx = cmd.index("--storage-state-in")
        assert cmd[idx + 1] == str(storage_p)

        env = captured["env"] or {}
        assert env["PLAYWRIGHT_VIEWPORT"] == "1280x800"
        assert env["PLAYWRIGHT_LOCALE"] == "ko-KR"
        assert env["PLAYWRIGHT_TIMEZONE"] == "Asia/Seoul"
        assert env["PLAYWRIGHT_COLOR_SCHEME"] == "light"

    def test_verify_failure_raises_expired(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        """verify fails → ReplayAuthExpiredError + profile_name + detail."""
        _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {"fail_reason": "service_text_not_found"}),
        )
        with pytest.raises(ReplayAuthExpiredError) as ei:
            run_llm_play(host_session_dir=str(session_dir), project_root=".")
        assert ei.value.profile_name == "alpha"
        assert ei.value.detail.get("reason") == "verify_failed"
        assert ei.value.detail.get("fail_reason") == "service_text_not_found"

    def test_profile_not_found_raises_expired(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        """A profile referenced in metadata but missing from the catalog is also treated as expired (UI prompts re-seed)."""
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "ghost"}), encoding="utf-8",
        )
        with pytest.raises(ReplayAuthExpiredError) as ei:
            run_llm_play(host_session_dir=str(session_dir), project_root=".")
        assert ei.value.profile_name == "ghost"
        assert ei.value.detail.get("reason") == "profile_not_found"


# ─────────────────────────────────────────────────────────────────────────
# P4.3 — run_codegen_replay auto-matching
# ─────────────────────────────────────────────────────────────────────────

class TestRunCodegenReplayAuth:
    def test_no_metadata_no_env_injection(self, session_dir: Path, monkeypatch):
        captured = _stub_subprocess_capture(monkeypatch)
        run_codegen_replay(host_session_dir=str(session_dir))
        # codegen replay defaults to env=None — without auth, it stays None.
        assert captured["env"] is None

    def test_auth_profile_injects_env(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        storage_p = _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {}),
        )
        captured = _stub_subprocess_capture(monkeypatch)
        run_codegen_replay(host_session_dir=str(session_dir))

        env = captured["env"] or {}
        assert env["AUTH_STORAGE_STATE_IN"] == str(storage_p)
        assert env["PLAYWRIGHT_VIEWPORT"] == "1280x800"
        assert env["PLAYWRIGHT_LOCALE"] == "ko-KR"

    def test_codegen_replay_verify_failure_raises_expired(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {"fail_reason": "x"}),
        )
        with pytest.raises(ReplayAuthExpiredError):
            run_codegen_replay(host_session_dir=str(session_dir))


# ─────────────────────────────────────────────────────────────────────────
# P4.4 — ReplayAuthExpiredError is a subclass of ReplayProxyError
# ─────────────────────────────────────────────────────────────────────────

class TestExpiredErrorHierarchy:
    def test_is_replay_proxy_error(self):
        """The rplus router can catch via ReplayProxyError and still see expired."""
        e = ReplayAuthExpiredError("alpha", {"reason": "x"})
        assert isinstance(e, ReplayProxyError)

    def test_carries_profile_name_and_detail(self):
        e = ReplayAuthExpiredError("booking-via-naver", {"reason": "verify_failed", "service_ms": 100})
        assert e.profile_name == "booking-via-naver"
        assert e.detail["reason"] == "verify_failed"
        assert e.detail["service_ms"] == 100
