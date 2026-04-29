"""auth_profiles module unit tests.

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §7.1

This file covers P1.1 (directory/schema helpers + name sanitize + index
lock). P1.2 ~ P1.7 cases land in follow-up commits.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Optional

import pytest

from zero_touch_qa import auth_profiles as ap
from zero_touch_qa.auth_profiles import (
    INDEX_VERSION,
    AuthProfile,
    ChipsNotSupportedError,
    EmptyDumpError,
    FingerprintProfile,
    InvalidProfileNameError,
    InvalidServiceDomainError,
    MissingDomainError,
    NaverProbeSpec,
    ProfileNotFoundError,
    SeedSubprocessError,
    SeedTimeoutError,
    SeedVerifyFailedError,
    VerifySpec,
    _VERIFY_HISTORY_MAX,
    _VERSION_RE,
    _atomic_update,
    _domain_from_url,
    _domain_matches,
    _ensure_root,
    _file_mode,
    _index_lock,
    _index_path,
    _load_index,
    _parse_version,
    _record_verify,
    _root,
    _save_index,
    _storage_path,
    _upsert_profile,
    _validate_name,
    chips_supported_by_runtime,
    current_machine_id,
    current_playwright_version,
    delete_profile,
    detect_session_storage_use,
    get_profile,
    has_partitioned_cookies,
    list_profiles,
    seed_profile,
    validate_dump,
    verify_profile,
)
import re


# ─────────────────────────────────────────────────────────────────────────
# fixture — isolated ROOT directory per test
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch) -> Path:
    """Point the ``AUTH_PROFILES_DIR`` env at tmp_path.

    ``ap._root()`` / ``_index_path()`` etc. re-read env on every call, so
    the monkeypatch is reflected immediately.
    """
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path))
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — name sanitize
# ─────────────────────────────────────────────────────────────────────────

class TestSanitizeName:
    """``_validate_name`` cases."""

    @pytest.mark.parametrize("name", [
        "a",                       # 1 char
        "abc",
        "booking-via-naver",
        "naver_tester01",
        "A1B2C3",
        "x" * 64,                  # exactly the upper bound
    ])
    def test_valid_names_pass(self, name: str):
        """Names that pass the rules — return without exception."""
        _validate_name(name)

    @pytest.mark.parametrize("name", [
        "",                        # empty string
        "-foo",                    # leading -
        "_foo",                    # leading _
        ".foo",                    # leading .
        "../escape",               # path traversal
        "a/b",                     # path separator
        "a\\b",                    # Windows path separator
        "a.b",                     # dot
        "a b",                     # space
        "a;rm",                    # shell metachar
        "a$b",
        "한글이름",                  # non-ASCII
        "naver🚀",                  # emoji
        "x" * 65,                  # over upper bound
    ])
    def test_invalid_names_raise(self, name: str):
        """Rule violations → InvalidProfileNameError."""
        with pytest.raises(InvalidProfileNameError):
            _validate_name(name)

    def test_non_string_raises(self):
        """Reject non-string types too (defensive)."""
        with pytest.raises(InvalidProfileNameError):
            _validate_name(None)  # type: ignore[arg-type]
        with pytest.raises(InvalidProfileNameError):
            _validate_name(123)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — root directory + permissions
# ─────────────────────────────────────────────────────────────────────────

class TestRoot:
    """``_root`` / ``_ensure_root`` / permission bits."""

    def test_env_override(self, isolated_root: Path):
        """AUTH_PROFILES_DIR env reflects immediately."""
        assert _root() == isolated_root

    def test_default_when_env_unset(self, monkeypatch):
        """When env is unset, fall back to ~/ttc-allinone-data/auth-profiles."""
        monkeypatch.delenv("AUTH_PROFILES_DIR", raising=False)
        root = _root()
        assert root.name == "auth-profiles"
        assert root.parent.name == "ttc-allinone-data"

    def test_ensure_root_creates_with_0700(self, tmp_path: Path, monkeypatch):
        """First-time root creation has mode 0700."""
        new_root = tmp_path / "fresh"
        monkeypatch.setenv("AUTH_PROFILES_DIR", str(new_root))
        assert not new_root.exists()
        _ensure_root()
        assert new_root.is_dir()
        assert _file_mode(new_root) == 0o700

    def test_ensure_root_idempotent(self, isolated_root: Path):
        """Second call keeps permissions intact."""
        _ensure_root()
        _ensure_root()
        assert _file_mode(isolated_root) == 0o700


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — Index load/save (single-thread)
# ─────────────────────────────────────────────────────────────────────────

class TestIndexRoundTrip:
    """``_load_index`` / ``_save_index`` round-trip."""

    def test_load_empty_when_no_file(self, isolated_root: Path):
        """When the catalog file doesn't exist, return defaults."""
        data = _load_index()
        assert data == {"version": INDEX_VERSION, "profiles": []}

    def test_save_then_load_roundtrip(self, isolated_root: Path):
        """save → load yields the same dict."""
        original = {
            "version": INDEX_VERSION,
            "profiles": [
                {"name": "naver-tester01", "service_domain": "booking.example.com"},
            ],
        }
        with _index_lock():
            _save_index(original)
        reloaded = _load_index()
        assert reloaded == original

    def test_save_creates_0600(self, isolated_root: Path):
        """The catalog file has mode 0600."""
        with _index_lock():
            _save_index({"version": INDEX_VERSION, "profiles": []})
        assert _file_mode(_index_path()) == 0o600

    def test_corrupt_file_falls_back_to_empty(self, isolated_root: Path):
        """Corrupt JSON → log a warning and return an empty catalog."""
        _ensure_root()
        _index_path().write_text("not json {", encoding="utf-8")
        data = _load_index()
        assert data == {"version": INDEX_VERSION, "profiles": []}

    def test_load_missing_keys_filled(self, isolated_root: Path):
        """A dict without version/profiles is safely backfilled."""
        _ensure_root()
        _index_path().write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        data = _load_index()
        assert data["version"] == INDEX_VERSION
        assert data["profiles"] == []
        assert data["foo"] == "bar"  # existing keys preserved


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — atomic update + concurrency
# ─────────────────────────────────────────────────────────────────────────

class TestAtomicUpdate:
    """``_atomic_update`` concurrency / serialization."""

    def test_basic_update(self, isolated_root: Path):
        """The dict returned by updater is what gets saved."""
        def add_profile(d: dict) -> dict:
            d["profiles"].append({"name": "alpha"})
            return d

        result = _atomic_update(add_profile)
        assert result["profiles"] == [{"name": "alpha"}]
        # next load sees the same data.
        assert _load_index()["profiles"] == [{"name": "alpha"}]

    def test_updater_must_return_dict(self, isolated_root: Path):
        """When updater returns None, TypeError."""
        with pytest.raises(TypeError):
            _atomic_update(lambda d: None)  # type: ignore[arg-type,return-value]

    def test_concurrent_updates_serialized(self, isolated_root: Path):
        """The catalog stays intact even when many threads call _atomic_update concurrently.

        N threads each add 1 profile → all N preserved at the end.
        flock serializes, so no lost updates.
        """
        N = 20

        def add_one(i: int):
            def updater(d: dict) -> dict:
                d["profiles"].append({"name": f"p{i:02d}"})
                return d
            _atomic_update(updater)

        threads = [threading.Thread(target=add_one, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = _load_index()
        names = sorted(p["name"] for p in final["profiles"])
        assert names == [f"p{i:02d}" for i in range(N)]


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — path helpers
# ─────────────────────────────────────────────────────────────────────────

class TestPathHelpers:
    """Simple mapping for ``_index_path`` / ``_storage_path``."""

    def test_index_path_under_root(self, isolated_root: Path):
        assert _index_path() == isolated_root / "_index.json"

    def test_storage_path_format(self, isolated_root: Path):
        assert _storage_path("naver-tester01") == (
            isolated_root / "naver-tester01.storage.json"
        )

    def test_storage_path_does_not_validate(self, isolated_root: Path):
        """``_storage_path`` itself doesn't sanitize — callers must call _validate_name first.

        This test pins down the *current behavior* — call sites are
        contractually required to validate first.
        """
        # Doesn't block dangerous inputs like path traversal (validation is the caller's job).
        # Real call sites filter via _validate_name beforehand.
        result = _storage_path("anything-not-validated")
        assert result.parent == isolated_root


# ─────────────────────────────────────────────────────────────────────────
# P1.2 — dataclass serialization
# ─────────────────────────────────────────────────────────────────────────

class TestFingerprintProfile:
    """``FingerprintProfile`` round-trip + CLI/env conversion."""

    def test_default_values(self):
        """``default()`` returns the operational defaults."""
        fp = FingerprintProfile.default()
        assert fp.viewport_width == 1280
        assert fp.viewport_height == 800
        assert fp.locale == "ko-KR"
        assert fp.timezone_id == "Asia/Seoul"
        assert fp.color_scheme == "light"
        assert fp.playwright_channel == "chromium"

    def test_to_playwright_open_args_no_user_agent(self):
        """D10 — no UA option must appear."""
        fp = FingerprintProfile.default()
        args = fp.to_playwright_open_args()
        # no UA-related flag
        assert "--user-agent" not in args
        # viewport-size is comma-separated
        assert "--viewport-size" in args
        idx = args.index("--viewport-size")
        assert args[idx + 1] == "1280,800"
        # the rest of the flags are present
        assert "--lang" in args
        assert "--timezone" in args
        assert "--color-scheme" in args

    def test_to_browser_context_kwargs(self):
        fp = FingerprintProfile.default()
        kwargs = fp.to_browser_context_kwargs()
        assert kwargs["viewport"] == {"width": 1280, "height": 800}
        assert kwargs["locale"] == "ko-KR"
        assert kwargs["timezone_id"] == "Asia/Seoul"
        assert kwargs["color_scheme"] == "light"

    def test_to_env_format(self):
        """env var uses 'WxH' format (executor parses with split('x'))."""
        fp = FingerprintProfile.default()
        env = fp.to_env()
        assert env["PLAYWRIGHT_VIEWPORT"] == "1280x800"
        assert env["PLAYWRIGHT_LOCALE"] == "ko-KR"
        assert env["PLAYWRIGHT_TIMEZONE"] == "Asia/Seoul"
        assert env["PLAYWRIGHT_COLOR_SCHEME"] == "light"

    def test_roundtrip_full(self):
        """Round-trip with all fields populated."""
        original = FingerprintProfile(
            viewport_width=1920,
            viewport_height=1080,
            locale="en-US",
            timezone_id="America/Los_Angeles",
            color_scheme="dark",
            playwright_version="1.57.0",
            playwright_channel="chrome",
            captured_user_agent="Mozilla/5.0 ...",
        )
        loaded = FingerprintProfile.from_dict(original.to_dict())
        assert loaded == original

    def test_from_dict_partial_defaults(self):
        """Missing keys are backfilled with defaults."""
        fp = FingerprintProfile.from_dict({})
        assert fp == FingerprintProfile.default()

    def test_from_dict_partial_viewport(self):
        """If the viewport dict is missing, default to 1280x800."""
        fp = FingerprintProfile.from_dict({"locale": "en-US"})
        assert fp.viewport_width == 1280
        assert fp.viewport_height == 800
        assert fp.locale == "en-US"


class TestNaverProbeSpec:
    """``NaverProbeSpec`` round-trip."""

    def test_default(self):
        probe = NaverProbeSpec()
        assert probe.url == "https://nid.naver.com/"
        assert probe.kind == "login_form_negative"
        assert probe.selector == "input[name='id']"

    def test_roundtrip(self):
        original = NaverProbeSpec(
            url="https://example.com/probe",
            kind="custom",
            selector="#test",
        )
        loaded = NaverProbeSpec.from_dict(original.to_dict())
        assert loaded == original

    def test_from_dict_empty_uses_defaults(self):
        probe = NaverProbeSpec.from_dict({})
        assert probe == NaverProbeSpec()


class TestVerifySpec:
    """``VerifySpec`` round-trip + optional naver_probe."""

    def test_service_only_roundtrip(self):
        """Round-trip without naver_probe."""
        original = VerifySpec(
            service_url="https://booking.example.com/mypage",
            service_text="김QA님 환영합니다",
        )
        d = original.to_dict()
        # naver_probe key must be absent in serialization (since it's None).
        assert "naver_probe" not in d
        loaded = VerifySpec.from_dict(d)
        assert loaded == original
        assert loaded.naver_probe is None

    def test_with_naver_probe_roundtrip(self):
        original = VerifySpec(
            service_url="https://booking.example.com/mypage",
            service_text="김QA님 환영합니다",
            naver_probe=NaverProbeSpec(),
        )
        loaded = VerifySpec.from_dict(original.to_dict())
        assert loaded == original
        assert loaded.naver_probe is not None

    def test_from_dict_with_invalid_naver_probe(self):
        """If naver_probe isn't a dict, treat as None (defensive)."""
        d = {
            "service_url": "https://booking.example.com/mypage",
            "service_text": "김QA님 환영합니다",
            "naver_probe": "not-a-dict",
        }
        loaded = VerifySpec.from_dict(d)
        assert loaded.naver_probe is None

    def test_service_text_optional(self):
        """Missing service_text loads as weak-verify mode (only URL access checked)."""
        loaded = VerifySpec.from_dict({"service_url": "https://example.com/mypage"})
        assert loaded.service_url == "https://example.com/mypage"
        assert loaded.service_text == ""

    def test_service_text_none_loads_as_empty(self):
        """Existing/external catalogs that put null are also handled as weak-verify."""
        loaded = VerifySpec.from_dict({
            "service_url": "https://example.com/mypage",
            "service_text": None,
        })
        assert loaded.service_text == ""

    def test_service_url_required(self):
        """service_url is still required."""
        with pytest.raises(KeyError):
            VerifySpec.from_dict({"service_text": "x"})


class TestAuthProfile:
    """``AuthProfile`` round-trip + storage_path portability."""

    def _make_profile(self, root: Path) -> AuthProfile:
        return AuthProfile(
            name="booking-via-naver",
            service_domain="booking.example.com",
            storage_path=root / "booking-via-naver.storage.json",
            created_at="2026-04-29T17:30:00+09:00",
            last_verified_at="2026-04-29T17:35:12+09:00",
            ttl_hint_hours=12,
            verify=VerifySpec(
                service_url="https://booking.example.com/mypage",
                service_text="김QA님 환영합니다",
                naver_probe=NaverProbeSpec(),
            ),
            fingerprint=FingerprintProfile.default(),
            host_machine_id="ALPHA-MAC:abcd1234",
            chips_supported=True,
            session_storage_warning=False,
            verify_history=[
                {"at": "2026-04-29T17:35:12+09:00", "ok": True, "service_ms": 230},
            ],
            notes="first seed",
        )

    def test_roundtrip(self, isolated_root: Path):
        original = self._make_profile(isolated_root)
        loaded = AuthProfile.from_dict(original.to_dict())
        assert loaded == original

    def test_storage_path_serialized_as_filename_only(self, isolated_root: Path):
        """The catalog stores storage_path as the *filename only* (portability, D3)."""
        prof = self._make_profile(isolated_root)
        d = prof.to_dict()
        assert d["storage_path"] == "booking-via-naver.storage.json"
        # No absolute path embedded.
        assert "/" not in d["storage_path"]

    def test_storage_path_resolved_with_current_root(self, tmp_path: Path, monkeypatch):
        """storage_path resolves against AUTH_PROFILES_DIR at from_dict time.

        Scenario: catalog created on machine A → synced to machine B with
        a different AUTH_PROFILES_DIR. storage_path auto-maps to the new
        path.
        """
        # Build under env A.
        env_a = tmp_path / "a"
        monkeypatch.setenv("AUTH_PROFILES_DIR", str(env_a))
        prof = self._make_profile(env_a)
        d = prof.to_dict()

        # switch to env B and load.
        env_b = tmp_path / "b"
        monkeypatch.setenv("AUTH_PROFILES_DIR", str(env_b))
        loaded = AuthProfile.from_dict(d)
        # storage_path resolves under env B's root.
        assert loaded.storage_path == env_b / "booking-via-naver.storage.json"

    def test_from_dict_strips_absolute_path(self, isolated_root: Path):
        """If the catalog ever holds an absolute path, extract the filename and rejoin against root (defensive)."""
        prof = self._make_profile(isolated_root)
        d = prof.to_dict()
        # Simulate a catalog that somehow contains an absolute path.
        d["storage_path"] = "/some/other/path/booking-via-naver.storage.json"
        loaded = AuthProfile.from_dict(d)
        assert loaded.storage_path == isolated_root / "booking-via-naver.storage.json"

    def test_partial_dict_missing_optional_fields(self, isolated_root: Path):
        """A dict missing optional fields still loads safely."""
        minimal = {
            "name": "alpha",
            "storage_path": "alpha.storage.json",
            "verify": {
                "service_url": "https://example.com/me",
                "service_text": "hello",
            },
        }
        loaded = AuthProfile.from_dict(minimal)
        assert loaded.name == "alpha"
        assert loaded.service_domain == ""
        assert loaded.last_verified_at is None
        assert loaded.ttl_hint_hours == 12
        assert loaded.fingerprint == FingerprintProfile.default()
        assert loaded.verify_history == []
        assert loaded.chips_supported is False
        assert loaded.session_storage_warning is False


# ─────────────────────────────────────────────────────────────────────────
# P1.3 — CRUD (list / get / delete / upsert)
# ─────────────────────────────────────────────────────────────────────────

def _build_profile(name: str, root: Path) -> AuthProfile:
    """Test profile factory."""
    return AuthProfile(
        name=name,
        service_domain=f"{name}.example.com",
        storage_path=root / f"{name}.storage.json",
        created_at="2026-04-29T17:30:00+09:00",
        last_verified_at=None,
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


class TestList:
    """``list_profiles`` cases."""

    def test_empty_returns_empty_list(self, isolated_root: Path):
        assert list_profiles() == []

    def test_single_profile(self, isolated_root: Path):
        _upsert_profile(_build_profile("alpha", isolated_root))
        result = list_profiles()
        assert len(result) == 1
        assert result[0].name == "alpha"

    def test_multiple_sorted_by_name(self, isolated_root: Path):
        for n in ["charlie", "alpha", "bravo"]:
            _upsert_profile(_build_profile(n, isolated_root))
        result = list_profiles()
        assert [p.name for p in result] == ["alpha", "bravo", "charlie"]

    def test_corrupt_entry_skipped(self, isolated_root: Path):
        """One corrupt entry doesn't break the whole list."""
        _upsert_profile(_build_profile("alpha", isolated_root))
        # inject a broken entry directly into the catalog.
        with _index_lock():
            data = _load_index()
            data["profiles"].append({"name": "broken"})  # missing verify key
            _save_index(data)
        # only alpha survives; broken is skipped.
        result = list_profiles()
        assert [p.name for p in result] == ["alpha"]


class TestGet:
    """``get_profile`` cases."""

    def test_existing_profile(self, isolated_root: Path):
        _upsert_profile(_build_profile("alpha", isolated_root))
        prof = get_profile("alpha")
        assert prof.name == "alpha"
        assert prof.service_domain == "alpha.example.com"

    def test_missing_raises(self, isolated_root: Path):
        with pytest.raises(ProfileNotFoundError):
            get_profile("ghost")

    def test_invalid_name_raises_before_lookup(self, isolated_root: Path):
        """Sanitize violations raise InvalidProfileNameError immediately (blocks path traversal)."""
        with pytest.raises(InvalidProfileNameError):
            get_profile("../../etc/passwd")

    def test_returns_independent_instance(self, isolated_root: Path):
        """Two gets return equal but separate instances (mutation isolation)."""
        _upsert_profile(_build_profile("alpha", isolated_root))
        a = get_profile("alpha")
        b = get_profile("alpha")
        assert a == b
        a.notes = "mutated"
        assert b.notes != "mutated"


class TestDelete:
    """``delete_profile`` cases."""

    def test_removes_from_index_and_storage_file(self, isolated_root: Path):
        prof = _build_profile("alpha", isolated_root)
        _upsert_profile(prof)
        # create a fake storage file (so unlink runs without an actual seed).
        prof.storage_path.write_text("{}", encoding="utf-8")
        assert prof.storage_path.exists()

        delete_profile("alpha")

        assert list_profiles() == []
        assert not prof.storage_path.exists()

    def test_idempotent_when_storage_file_missing(self, isolated_root: Path):
        """Handles cases where only the catalog entry exists and the storage file is already gone."""
        _upsert_profile(_build_profile("alpha", isolated_root))
        # don't create the storage file.
        delete_profile("alpha")
        assert list_profiles() == []

    def test_missing_raises(self, isolated_root: Path):
        with pytest.raises(ProfileNotFoundError):
            delete_profile("ghost")

    def test_invalid_name_raises_before_lookup(self, isolated_root: Path):
        with pytest.raises(InvalidProfileNameError):
            delete_profile("../escape")

    def test_does_not_affect_others(self, isolated_root: Path):
        """Other profiles are unaffected."""
        for n in ["alpha", "bravo", "charlie"]:
            _upsert_profile(_build_profile(n, isolated_root))
        delete_profile("bravo")
        assert [p.name for p in list_profiles()] == ["alpha", "charlie"]


class TestUpsert:
    """``_upsert_profile`` — re-seed overwrites same name."""

    def test_insert_new(self, isolated_root: Path):
        _upsert_profile(_build_profile("alpha", isolated_root))
        assert [p.name for p in list_profiles()] == ["alpha"]

    def test_overwrite_existing_same_name(self, isolated_root: Path):
        prof1 = _build_profile("alpha", isolated_root)
        prof1.notes = "first"
        _upsert_profile(prof1)

        prof2 = _build_profile("alpha", isolated_root)
        prof2.notes = "second"
        _upsert_profile(prof2)

        result = list_profiles()
        assert len(result) == 1
        assert result[0].notes == "second"

    def test_invalid_name_raises(self, isolated_root: Path):
        prof = _build_profile("alpha", isolated_root)
        prof.name = "../escape"
        with pytest.raises(InvalidProfileNameError):
            _upsert_profile(prof)


# ─────────────────────────────────────────────────────────────────────────
# P1.4 — Identity helpers (machine_id / playwright version / chips gate)
# ─────────────────────────────────────────────────────────────────────────

class TestMachineId:
    """``current_machine_id`` stability + format."""

    def test_returns_non_empty_string(self):
        mid = current_machine_id()
        assert isinstance(mid, str)
        assert mid

    def test_stable_across_calls(self):
        """Same value when called twice in the same process."""
        assert current_machine_id() == current_machine_id()

    def test_starts_with_hostname(self):
        """The identifier starts with hostname."""
        import socket as _socket
        hostname = _socket.gethostname() or "unknown-host"
        assert current_machine_id().startswith(hostname)

    def test_format_with_uuid_hash(self, monkeypatch):
        """When UUID extraction succeeds, format is hostname:hash8."""
        monkeypatch.setattr(ap, "_read_machine_uuid", lambda: "AAAA-BBBB-CCCC")
        mid = current_machine_id()
        # exactly 8 chars after ":".
        assert ":" in mid
        suffix = mid.rsplit(":", 1)[1]
        assert len(suffix) == 8
        # hex string.
        assert re.match(r"^[0-9a-f]{8}$", suffix)

    def test_format_without_uuid(self, monkeypatch):
        """When UUID extraction fails, hostname only (no colon)."""
        monkeypatch.setattr(ap, "_read_machine_uuid", lambda: "")
        mid = current_machine_id()
        assert ":" not in mid

    def test_uuid_not_exposed_directly(self, monkeypatch):
        """Raw UUID must not be embedded in the identifier (hash only)."""
        sentinel_uuid = "AAAA-UUID-SENTINEL-XYZ"
        monkeypatch.setattr(ap, "_read_machine_uuid", lambda: sentinel_uuid)
        mid = current_machine_id()
        assert sentinel_uuid not in mid


class TestPlaywrightVersion:
    """``current_playwright_version`` + ``_parse_version`` + ``chips_supported_by_runtime``."""

    def test_parse_version_valid(self):
        assert _parse_version("Version 1.57.0") == (1, 57, 0)
        assert _parse_version("1.54.2") == (1, 54, 2)
        assert _parse_version("playwright 2.0.0 build") == (2, 0, 0)

    def test_parse_version_invalid(self):
        assert _parse_version("") is None
        assert _parse_version("no version here") is None
        assert _parse_version("1.x.y") is None

    def test_current_version_format_or_empty(self):
        """Real call — if Playwright is on PATH, get 'X.Y.Z' form; else empty string."""
        v = current_playwright_version()
        if v:
            assert _VERSION_RE.match(v) or _VERSION_RE.search(v)
        else:
            assert v == ""

    def test_version_via_subprocess_mock(self, monkeypatch):
        """Confirm the call path with a subprocess mock."""
        class FakeResult:
            returncode = 0
            stdout = "Version 1.57.0\n"
            stderr = ""

        monkeypatch.setattr(
            ap.subprocess,
            "run",
            lambda *a, **kw: FakeResult(),
        )
        assert current_playwright_version() == "1.57.0"

    def test_version_subprocess_failure_returns_empty(self, monkeypatch):
        """If subprocess raises OSError, return empty string."""
        def boom(*a, **kw):
            raise FileNotFoundError("no playwright")

        monkeypatch.setattr(ap.subprocess, "run", boom)
        assert current_playwright_version() == ""

    def test_version_nonzero_returncode_returns_empty(self, monkeypatch):
        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = "error"

        monkeypatch.setattr(ap.subprocess, "run", lambda *a, **kw: FakeResult())
        assert current_playwright_version() == ""

    def test_chips_supported_at_154(self, monkeypatch):
        """Playwright 1.54.0 → CHIPS supported."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.54.0")
        assert chips_supported_by_runtime() is True

    def test_chips_supported_at_157(self, monkeypatch):
        """Playwright 1.57.0 → CHIPS supported."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.57.0")
        assert chips_supported_by_runtime() is True

    def test_chips_not_supported_below_154(self, monkeypatch):
        """Playwright 1.53.x → CHIPS not supported."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.53.5")
        assert chips_supported_by_runtime() is False

    def test_chips_not_supported_at_150(self, monkeypatch):
        """Playwright 1.50.0 → CHIPS not supported."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.50.0")
        assert chips_supported_by_runtime() is False

    def test_chips_not_supported_when_version_missing(self, monkeypatch):
        """Playwright CLI missing (empty string) → conservative False."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "")
        assert chips_supported_by_runtime() is False

    def test_chips_supported_at_2_0_0(self, monkeypatch):
        """Playwright 2.0.0 (hypothetical) → CHIPS supported."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "2.0.0")
        assert chips_supported_by_runtime() is True


# ─────────────────────────────────────────────────────────────────────────
# P1.5 — dump validation / partitioned / sessionStorage detection
# ─────────────────────────────────────────────────────────────────────────

def _write_dump(path: Path, data: dict) -> None:
    """Test helper — write a dump JSON."""
    path.write_text(json.dumps(data), encoding="utf-8")


class TestDomainMatch:
    """``_domain_matches`` — match across the same domain tree (self/child/parent)."""

    @pytest.mark.parametrize("cookie_domain,expected,result", [
        # basic matches
        (".naver.com", "naver.com", True),
        ("naver.com", "naver.com", True),
        ("accounts.naver.com", "naver.com", True),
        ("nid.naver.com", "naver.com", True),
        # case-insensitive
        ("NAVER.COM", "naver.com", True),
        # partial matches rejected (different domain tree)
        ("evilnaver.com", "naver.com", False),
        ("naver.com.evil.com", "naver.com", False),
        # parent-domain cookie → child host match (RFC 6265: parent cookies are sent to children).
        # supports the pattern where an SSO gateway issues sessions on the parent domain.
        ("naver.com", "api.naver.com", True),
        ("koreaconnect.kr", "portal.koreaconnect.kr", True),
        (".koreaconnect.kr", "portal.koreaconnect.kr", True),
        # empty-input guard
        ("", "naver.com", False),
        ("naver.com", "", False),
        # subdomain comparison
        (".booking.example.com", "booking.example.com", True),
        ("booking.example.com", "example.com", True),
        ("not-booking.example.com", "booking.example.com", False),
    ])
    def test_match_cases(self, cookie_domain, expected, result):
        assert _domain_matches(cookie_domain, expected) is result


class TestValidateDump:
    """``validate_dump`` cases."""

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(EmptyDumpError):
            validate_dump(tmp_path / "missing.json", ["naver.com"])

    def test_corrupt_json_raises(self, tmp_path: Path):
        p = tmp_path / "corrupt.json"
        p.write_text("not json {", encoding="utf-8")
        with pytest.raises(EmptyDumpError):
            validate_dump(p, ["naver.com"])

    def test_empty_dump_raises(self, tmp_path: Path):
        """When both cookies + origins are empty → EmptyDumpError."""
        p = tmp_path / "empty.json"
        _write_dump(p, {"cookies": [], "origins": []})
        with pytest.raises(EmptyDumpError):
            validate_dump(p, ["naver.com"])

    def test_only_origins_present_passes_empty_check(self, tmp_path: Path):
        """0 cookies but non-empty origins is not an empty dump (domain check is separate)."""
        p = tmp_path / "origins-only.json"
        _write_dump(p, {
            "cookies": [],
            "origins": [{"origin": "https://x.example.com", "localStorage": []}],
        })
        # No domain match → MissingDomainError. But not EmptyDumpError.
        with pytest.raises(MissingDomainError):
            validate_dump(p, ["x.example.com"])

    def test_two_domains_present(self, tmp_path: Path):
        p = tmp_path / "good.json"
        _write_dump(p, {
            "cookies": [
                {"name": "NID_AUT", "domain": ".naver.com", "value": "a"},
                {"name": "session", "domain": "booking.example.com", "value": "b"},
            ],
            "origins": [],
        })
        # passes without exception.
        validate_dump(p, ["naver.com", "booking.example.com"])

    def test_one_domain_missing(self, tmp_path: Path):
        p = tmp_path / "missing-one.json"
        _write_dump(p, {
            "cookies": [
                {"name": "NID_AUT", "domain": ".naver.com", "value": "a"},
                # no booking cookie
            ],
            "origins": [],
        })
        with pytest.raises(MissingDomainError) as excinfo:
            validate_dump(p, ["naver.com", "booking.example.com"])
        assert excinfo.value.missing == ["booking.example.com"]

    def test_subdomain_match_counts(self, tmp_path: Path):
        """When expected="naver.com", cookie="accounts.naver.com" matches."""
        p = tmp_path / "subdomain.json"
        _write_dump(p, {
            "cookies": [
                {"name": "x", "domain": "accounts.naver.com", "value": "v"},
            ],
            "origins": [],
        })
        validate_dump(p, ["naver.com"])

    def test_parent_domain_cookie_counts_for_sso(self, tmp_path: Path):
        """SSO regression — a parent-domain cookie (``.koreaconnect.kr``)
        must match an expected child host (``portal.koreaconnect.kr``).

        Failure case (2026-04-29): the SSO gateway issued sessions on
        ``.koreaconnect.kr`` but expected="portal.koreaconnect.kr"
        mismatched → false-fail.
        """
        p = tmp_path / "parent-sso.json"
        _write_dump(p, {
            "cookies": [
                {"name": "NID_AUT", "domain": ".naver.com", "value": "a"},
                {"name": "SSO_SESSION", "domain": ".koreaconnect.kr", "value": "s"},
            ],
            "origins": [],
        })
        validate_dump(p, ["naver.com", "portal.koreaconnect.kr"])

    def test_empty_expected_skipped(self, tmp_path: Path):
        """An empty string in expected_domains is skipped (defensive)."""
        p = tmp_path / "ok.json"
        _write_dump(p, {
            "cookies": [{"name": "x", "domain": "naver.com", "value": "v"}],
            "origins": [],
        })
        # "" doesn't trigger missing-domain handling.
        validate_dump(p, ["naver.com", ""])


class TestHasPartitionedCookies:
    """``has_partitioned_cookies`` cases (D14)."""

    def test_no_cookies(self, tmp_path: Path):
        p = tmp_path / "empty.json"
        _write_dump(p, {"cookies": [], "origins": []})
        assert has_partitioned_cookies(p) is False

    def test_cookies_without_partition(self, tmp_path: Path):
        p = tmp_path / "no-partition.json"
        _write_dump(p, {
            "cookies": [
                {"name": "a", "domain": ".naver.com", "value": "1"},
                {"name": "b", "domain": "x.com", "value": "2"},
            ],
            "origins": [],
        })
        assert has_partitioned_cookies(p) is False

    def test_partitionkey_camelcase(self, tmp_path: Path):
        """Match the JSON dump's standard 'partitionKey' key."""
        p = tmp_path / "chips-camel.json"
        _write_dump(p, {
            "cookies": [
                {"name": "a", "domain": ".naver.com", "value": "1",
                 "partitionKey": "https://example.com"},
            ],
            "origins": [],
        })
        assert has_partitioned_cookies(p) is True

    def test_partition_key_snake(self, tmp_path: Path):
        """Also recognize the Python API-style 'partition_key' (defensive)."""
        p = tmp_path / "chips-snake.json"
        _write_dump(p, {
            "cookies": [
                {"name": "a", "domain": ".naver.com", "value": "1",
                 "partition_key": "https://example.com"},
            ],
            "origins": [],
        })
        assert has_partitioned_cookies(p) is True

    def test_empty_partitionkey_string(self, tmp_path: Path):
        """An empty ``partitionKey`` is the same as unset."""
        p = tmp_path / "empty-pkey.json"
        _write_dump(p, {
            "cookies": [
                {"name": "a", "domain": ".naver.com", "value": "1", "partitionKey": ""},
            ],
            "origins": [],
        })
        assert has_partitioned_cookies(p) is False

    def test_missing_file_returns_false(self, tmp_path: Path):
        """Missing file → False (no exception — soft check)."""
        assert has_partitioned_cookies(tmp_path / "missing.json") is False


class TestDetectSessionStorageUse:
    """``detect_session_storage_use`` cases (D16, Q4)."""

    def test_empty_dict(self):
        assert detect_session_storage_use({}) is False

    def test_non_dict(self):
        assert detect_session_storage_use(None) is False  # type: ignore[arg-type]
        assert detect_session_storage_use([]) is False  # type: ignore[arg-type]

    def test_no_suspicious_keys(self):
        data = {
            "https://example.com": [
                {"name": "lastVisited", "value": "2026-04-29"},
                {"name": "theme", "value": "dark"},
            ],
        }
        assert detect_session_storage_use(data) is False

    @pytest.mark.parametrize("key", [
        "auth_token", "AUTH_TOKEN", "AuthToken",
        "session_id", "session-data",
        "jwt", "JWT_TOKEN",
        "bearer_token",
        "accessToken", "refresh_token",
        "user_credential",
    ])
    def test_suspicious_key_names(self, key):
        data = {"https://example.com": [{"name": key, "value": "anything"}]}
        assert detect_session_storage_use(data) is True

    def test_suspicious_base64_value(self):
        """JWT-like long base64 values."""
        data = {
            "https://example.com": [
                {"name": "harmless_name", "value": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ"},
            ],
        }
        assert detect_session_storage_use(data) is True

    def test_short_value_not_flagged(self):
        """Short values (<20 chars) aren't flagged even if base64-like."""
        data = {
            "https://example.com": [
                {"name": "harmless", "value": "short123"},
            ],
        }
        assert detect_session_storage_use(data) is False

    def test_value_with_spaces_not_base64(self):
        """Values with spaces never match the base64 pattern."""
        data = {
            "https://example.com": [
                {"name": "harmless", "value": "this is a long human readable sentence"},
            ],
        }
        assert detect_session_storage_use(data) is False

    def test_multi_origin(self):
        """If any single origin has a suspicious entry, return True."""
        data = {
            "https://safe.com": [{"name": "theme", "value": "dark"}],
            "https://app.com": [{"name": "auth_token", "value": "x"}],
        }
        assert detect_session_storage_use(data) is True

    def test_malformed_entries_skipped(self):
        """Non-list values / non-dict entries are silently skipped."""
        data = {
            "https://safe.com": "not-a-list",
            "https://other.com": [
                "not-a-dict",
                {"name": "auth_token", "value": "x"},
            ],
        }
        assert detect_session_storage_use(data) is True


# ─────────────────────────────────────────────────────────────────────────
# P1.6 — verify_profile (orchestrator)
# ─────────────────────────────────────────────────────────────────────────
#
# Real Playwright calls are encapsulated in ``_verify_service_side`` /
# ``_verify_naver_probe`` and replaced via monkeypatch. This class only
# verifies *orchestration + result recording* logic.

class _StubVerify:
    """Stub result container for ``_verify_service_side`` / ``_verify_naver_probe``."""

    def __init__(self, ok: bool, elapsed_ms: float, fail_reason: Optional[str] = None):
        self.ok = ok
        self.elapsed_ms = elapsed_ms
        self.fail_reason = fail_reason
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return (self.ok, self.elapsed_ms, self.fail_reason)


class TestVerifyProfileOrchestrator:
    """``verify_profile`` service+probe combination logic + result persistence."""

    def _profile(self, root: Path, with_probe: bool = True) -> AuthProfile:
        return AuthProfile(
            name="alpha",
            service_domain="x.example.com",
            storage_path=root / "alpha.storage.json",
            created_at="2026-04-29T10:00:00+09:00",
            last_verified_at=None,
            ttl_hint_hours=12,
            verify=VerifySpec(
                service_url="https://x.example.com/mypage",
                service_text="hello",
                naver_probe=NaverProbeSpec() if with_probe else None,
            ),
            fingerprint=FingerprintProfile.default(),
            host_machine_id="MAC:test",
            chips_supported=True,
            session_storage_warning=False,
        )

    def test_service_ok_probe_ok(self, isolated_root: Path, monkeypatch):
        prof = self._profile(isolated_root)
        _upsert_profile(prof)

        svc_stub = _StubVerify(ok=True, elapsed_ms=200.0)
        probe_stub = _StubVerify(ok=True, elapsed_ms=150.0)
        monkeypatch.setattr(ap, "_verify_service_side", svc_stub)
        monkeypatch.setattr(ap, "_verify_naver_probe", probe_stub)

        ok, detail = verify_profile(prof)
        assert ok is True
        assert detail["service_ms"] == 200
        assert detail["naver_probe_ms"] == 150
        assert detail["naver_ok"] is True
        assert "fail_reason" not in detail
        assert svc_stub.calls == 1
        assert probe_stub.calls == 1

    def test_service_ok_probe_fail_still_ok(self, isolated_root: Path, monkeypatch):
        """probe is best-effort — failure still yields ok=True."""
        prof = self._profile(isolated_root)
        _upsert_profile(prof)

        monkeypatch.setattr(
            ap, "_verify_service_side", _StubVerify(ok=True, elapsed_ms=200.0),
        )
        monkeypatch.setattr(
            ap,
            "_verify_naver_probe",
            _StubVerify(ok=False, elapsed_ms=300.0, fail_reason="login_form_visible"),
        )

        ok, detail = verify_profile(prof)
        assert ok is True
        assert detail["naver_ok"] is False
        assert detail["naver_probe_ms"] == 300
        # When ok=True, fail_reason is not stamped onto detail.
        assert "fail_reason" not in detail

    def test_service_fail(self, isolated_root: Path, monkeypatch):
        prof = self._profile(isolated_root)
        _upsert_profile(prof)

        svc_stub = _StubVerify(
            ok=False, elapsed_ms=180.0, fail_reason="service_text_not_found",
        )
        probe_stub = _StubVerify(ok=True, elapsed_ms=100.0)
        monkeypatch.setattr(ap, "_verify_service_side", svc_stub)
        monkeypatch.setattr(ap, "_verify_naver_probe", probe_stub)

        ok, detail = verify_profile(prof)
        assert ok is False
        assert detail["fail_reason"] == "service_text_not_found"
        assert detail["service_ms"] == 180
        # On service failure, probe isn't called (saves time).
        assert probe_stub.calls == 0
        assert detail["naver_ok"] is None

    def test_naver_probe_disabled(self, isolated_root: Path, monkeypatch):
        """naver_probe=False → don't call probe."""
        prof = self._profile(isolated_root)
        _upsert_profile(prof)

        svc_stub = _StubVerify(ok=True, elapsed_ms=200.0)
        probe_stub = _StubVerify(ok=True, elapsed_ms=100.0)
        monkeypatch.setattr(ap, "_verify_service_side", svc_stub)
        monkeypatch.setattr(ap, "_verify_naver_probe", probe_stub)

        ok, detail = verify_profile(prof, naver_probe=False)
        assert ok is True
        assert probe_stub.calls == 0
        assert detail["naver_ok"] is None

    def test_no_probe_in_profile(self, isolated_root: Path, monkeypatch):
        """If the profile itself has naver_probe=None, don't call probe."""
        prof = self._profile(isolated_root, with_probe=False)
        _upsert_profile(prof)

        svc_stub = _StubVerify(ok=True, elapsed_ms=200.0)
        probe_stub = _StubVerify(ok=True, elapsed_ms=100.0)
        monkeypatch.setattr(ap, "_verify_service_side", svc_stub)
        monkeypatch.setattr(ap, "_verify_naver_probe", probe_stub)

        ok, _ = verify_profile(prof)
        assert ok is True
        assert probe_stub.calls == 0


class TestRecordVerify:
    """``_record_verify`` — result persistence + history cap."""

    def _profile(self, root: Path) -> AuthProfile:
        return AuthProfile(
            name="alpha",
            service_domain="x.example.com",
            storage_path=root / "alpha.storage.json",
            created_at="2026-04-29T10:00:00+09:00",
            last_verified_at=None,
            ttl_hint_hours=12,
            verify=VerifySpec(
                service_url="https://x.example.com/mypage",
                service_text="hello",
            ),
            fingerprint=FingerprintProfile.default(),
            host_machine_id="MAC:test",
            chips_supported=True,
            session_storage_warning=False,
        )

    def test_success_sets_last_verified_at_and_appends_history(
        self, isolated_root: Path,
    ):
        prof = self._profile(isolated_root)
        _upsert_profile(prof)
        _record_verify(prof, ok=True, detail={"service_ms": 200})

        loaded = get_profile("alpha")
        assert loaded.last_verified_at is not None
        assert len(loaded.verify_history) == 1
        assert loaded.verify_history[0]["ok"] is True
        assert loaded.verify_history[0]["service_ms"] == 200

    def test_failure_appends_history_but_not_last_verified_at(
        self, isolated_root: Path,
    ):
        prof = self._profile(isolated_root)
        prof.last_verified_at = "2026-04-28T10:00:00+09:00"  # prior success
        _upsert_profile(prof)
        _record_verify(prof, ok=False, detail={
            "service_ms": 100, "fail_reason": "service_text_not_found",
        })

        loaded = get_profile("alpha")
        # Failure leaves last_verified_at unchanged (keeps prior value).
        assert loaded.last_verified_at == "2026-04-28T10:00:00+09:00"
        assert len(loaded.verify_history) == 1
        assert loaded.verify_history[0]["ok"] is False
        assert loaded.verify_history[0]["fail_reason"] == "service_text_not_found"

    def test_history_capped(self, isolated_root: Path):
        """Anything beyond ``_VERIFY_HISTORY_MAX`` is dropped oldest-first."""
        prof = self._profile(isolated_root)
        _upsert_profile(prof)

        # cap + 5 calls — only the last cap entries survive.
        total = _VERIFY_HISTORY_MAX + 5
        for i in range(total):
            _record_verify(prof, ok=True, detail={"service_ms": i})

        loaded = get_profile("alpha")
        assert len(loaded.verify_history) == _VERIFY_HISTORY_MAX
        # The oldest (i < 5) drop, only the last _VERIFY_HISTORY_MAX entries remain.
        first_kept = total - _VERIFY_HISTORY_MAX
        assert loaded.verify_history[0]["service_ms"] == first_kept
        assert loaded.verify_history[-1]["service_ms"] == total - 1


# ─────────────────────────────────────────────────────────────────────────
# P1.7 — seed_profile lifecycle
# ─────────────────────────────────────────────────────────────────────────
#
# Both the real ``playwright open`` call and the verify Playwright call
# are mocked. This class only verifies orchestration + failure-path cleanup.

def _make_dump_with_domains(domains: list[str]) -> dict:
    """Synthetic storage dump with one cookie per supplied domain."""
    return {
        "cookies": [
            {"name": f"c_{i}", "value": "v", "domain": d, "path": "/"}
            for i, d in enumerate(domains)
        ],
        "origins": [],
    }


def _seed_subprocess_writes(storage_path: Path, dump: dict):
    """Stub factory that intercepts only the ``playwright open`` call and pretends to write the storage file.

    Other subprocess calls (e.g. ``ioreg`` for ``current_machine_id``)
    pass through — a global monkeypatch on every call has too much
    side-effect blast radius.
    """
    real_run = subprocess.run

    class _FakeResult:
        returncode = 0
        stderr = b""

    def _stub(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        # only intercept playwright calls.
        if isinstance(cmd, list) and cmd and cmd[0] == "playwright":
            if "--save-storage" in cmd:
                idx = cmd.index("--save-storage")
                target = Path(cmd[idx + 1])
                target.write_text(json.dumps(dump), encoding="utf-8")
            return _FakeResult()
        return real_run(*args, **kwargs)

    return _stub


class TestSeedHelpers:
    """seed helpers — _domain_from_url etc."""

    @pytest.mark.parametrize("url,expected", [
        ("https://booking.example.com/", "booking.example.com"),
        ("https://booking.example.com/path?q=1", "booking.example.com"),
        ("http://localhost:8080/x", "localhost"),
        ("HTTPS://Booking.Example.COM/", "booking.example.com"),
        ("not-a-url", ""),
        ("", ""),
    ])
    def test_domain_from_url(self, url, expected):
        assert _domain_from_url(url) == expected


class TestSeedProfile:
    """``seed_profile`` happy and failure paths."""

    def _verify_spec(self) -> VerifySpec:
        return VerifySpec(
            service_url="https://booking.example.com/mypage",
            service_text="환영합니다",
            naver_probe=NaverProbeSpec(),
        )

    def _patch_chips_ok(self, monkeypatch):
        monkeypatch.setattr(ap, "chips_supported_by_runtime", lambda: True)
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.57.0")

    def _patch_verify_ok(self, monkeypatch):
        monkeypatch.setattr(
            ap, "_verify_service_side",
            lambda *a, **kw: (True, 200.0, None),
        )
        monkeypatch.setattr(
            ap, "_verify_naver_probe",
            lambda *a, **kw: (True, 150.0, None),
        )

    def _patch_verify_fail_service(self, monkeypatch):
        monkeypatch.setattr(
            ap, "_verify_service_side",
            lambda *a, **kw: (False, 100.0, "service_text_not_found"),
        )
        monkeypatch.setattr(
            ap, "_verify_naver_probe",
            lambda *a, **kw: (True, 150.0, None),
        )

    def test_happy_path(self, isolated_root: Path, monkeypatch):
        """Happy path — chips OK, subprocess clean exit, dump valid, verify passes."""
        self._patch_chips_ok(monkeypatch)
        self._patch_verify_ok(monkeypatch)
        dump = _make_dump_with_domains([".naver.com", "booking.example.com"])
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, dump),
        )

        prof = seed_profile(
            "alpha",
            "https://booking.example.com/",
            self._verify_spec(),
        )

        assert prof.name == "alpha"
        assert prof.service_domain == "booking.example.com"
        assert prof.storage_path.exists()
        assert prof.fingerprint.playwright_version == "1.57.0"
        assert prof.chips_supported is True
        assert prof.session_storage_warning is False  # P1 limitation
        # confirm the catalog was updated.
        assert get_profile("alpha").name == "alpha"
        # storage file mode is 0600.
        assert _file_mode(prof.storage_path) == 0o600

    def test_invalid_name_rejected_early(self, isolated_root: Path, monkeypatch):
        self._patch_chips_ok(monkeypatch)
        with pytest.raises(InvalidProfileNameError):
            seed_profile("../escape", "https://x.com/", self._verify_spec())

    def test_chips_not_supported(self, isolated_root: Path, monkeypatch):
        monkeypatch.setattr(ap, "chips_supported_by_runtime", lambda: False)
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.50.0")
        with pytest.raises(ChipsNotSupportedError):
            seed_profile("alpha", "https://x.example.com/", self._verify_spec())

    def test_invalid_service_domain(self, isolated_root: Path, monkeypatch):
        """No hostname extractable from seed_url and no explicit override."""
        self._patch_chips_ok(monkeypatch)
        with pytest.raises(InvalidServiceDomainError):
            seed_profile("alpha", "not-a-url", self._verify_spec())

    def test_explicit_service_domain_overrides(self, isolated_root: Path, monkeypatch):
        """If service_domain is explicit, don't extract from seed_url."""
        self._patch_chips_ok(monkeypatch)
        self._patch_verify_ok(monkeypatch)
        dump = _make_dump_with_domains([".naver.com", "actual-target.io"])
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, dump),
        )

        prof = seed_profile(
            "alpha",
            "https://something-else.com/login",
            self._verify_spec(),
            service_domain="actual-target.io",
        )
        assert prof.service_domain == "actual-target.io"

    def test_subprocess_timeout(self, isolated_root: Path, monkeypatch):
        """If playwright open doesn't finish before timeout → SeedTimeoutError."""
        self._patch_chips_ok(monkeypatch)
        real_run = subprocess.run

        def _maybe_timeout(*a, **kw):
            cmd = a[0] if a else kw.get("args", [])
            if isinstance(cmd, list) and cmd and cmd[0] == "playwright":
                raise subprocess.TimeoutExpired(cmd, 600)
            return real_run(*a, **kw)

        monkeypatch.setattr(ap.subprocess, "run", _maybe_timeout)

        with pytest.raises(SeedTimeoutError):
            seed_profile("alpha", "https://x.example.com/", self._verify_spec())
        # storage file not created (timeout fired before write).
        assert not _storage_path("alpha").exists()

    def test_subprocess_nonzero_exit(self, isolated_root: Path, monkeypatch):
        """If subprocess returns a non-zero return code → SeedSubprocessError."""
        self._patch_chips_ok(monkeypatch)
        real_run = subprocess.run

        class _FailResult:
            returncode = 1
            stderr = b"some error"

        def _maybe_fail(*a, **kw):
            cmd = a[0] if a else kw.get("args", [])
            if isinstance(cmd, list) and cmd and cmd[0] == "playwright":
                return _FailResult()
            return real_run(*a, **kw)

        monkeypatch.setattr(ap.subprocess, "run", _maybe_fail)

        with pytest.raises(SeedSubprocessError):
            seed_profile("alpha", "https://x.example.com/", self._verify_spec())

    def test_empty_dump_cleanup(self, isolated_root: Path, monkeypatch):
        """User closes the window without logging in → dump is empty → EmptyDumpError + file cleanup."""
        self._patch_chips_ok(monkeypatch)
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, {"cookies": [], "origins": []}),
        )

        with pytest.raises(EmptyDumpError):
            seed_profile("alpha", "https://booking.example.com/", self._verify_spec())
        # cleaned up.
        assert not storage_target.exists()
        # not registered in catalog either.
        assert list_profiles() == []

    def test_missing_naver_domain_cleanup(self, isolated_root: Path, monkeypatch):
        """No naver.com cookie — user logged in with a different OAuth (Google etc.) or didn't complete OAuth."""
        self._patch_chips_ok(monkeypatch)
        # only service domain present, no naver.com.
        dump = _make_dump_with_domains(["booking.example.com"])
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, dump),
        )

        with pytest.raises(MissingDomainError) as excinfo:
            seed_profile("alpha", "https://booking.example.com/", self._verify_spec())
        assert "naver.com" in excinfo.value.missing
        assert not storage_target.exists()
        assert list_profiles() == []

    def test_verify_fail_rollback(self, isolated_root: Path, monkeypatch):
        """Dump passes but verify fails — roll back both catalog and storage."""
        self._patch_chips_ok(monkeypatch)
        self._patch_verify_fail_service(monkeypatch)
        dump = _make_dump_with_domains([".naver.com", "booking.example.com"])
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, dump),
        )

        with pytest.raises(SeedVerifyFailedError):
            seed_profile("alpha", "https://booking.example.com/", self._verify_spec())
        # confirm rollback.
        assert not storage_target.exists()
        assert list_profiles() == []

    def test_reseed_overwrites(self, isolated_root: Path, monkeypatch):
        """Reseed with the same name — clean stale storage and update the catalog."""
        self._patch_chips_ok(monkeypatch)
        self._patch_verify_ok(monkeypatch)
        dump1 = _make_dump_with_domains([".naver.com", "booking.example.com"])
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, dump1),
        )

        prof1 = seed_profile(
            "alpha", "https://booking.example.com/",
            self._verify_spec(), notes="first",
        )
        assert prof1.notes == "first"

        # reseed — different dump.
        dump2 = _make_dump_with_domains([".naver.com", "booking.example.com"])
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, dump2),
        )
        prof2 = seed_profile(
            "alpha", "https://booking.example.com/",
            self._verify_spec(), notes="second",
        )
        assert prof2.notes == "second"
        # the catalog has exactly one entry (overwritten).
        assert len(list_profiles()) == 1
