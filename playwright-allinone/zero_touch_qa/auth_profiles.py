"""Auth Profile (E2E for services that authenticate via Naver-OAuth) — seeded storageState catalog.

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md

This module is responsible for:

- Managing the ``~/ttc-allinone-data/auth-profiles/`` directory + ``_index.json`` catalog
- Storing the storageState file produced by a one-time human-driven OAuth roundtrip
  *by name + metadata* so that subsequent recording/replay can reuse it
- service-side authoritative + naver-side optional weak probe verification
- fingerprint pinning (excluding UA — viewport/locale/timezone/color_scheme +
  Playwright version/channel)

This module is separate from the *existing* ``zero_touch_qa.auth`` (form/TOTP/OAuth DSL
actions). The auth-profile here is a fallback for cases where the IdP screen itself
cannot be automated end-to-end (Naver, etc.). The auth-profile design is not for
testing Naver directly — it is for testing external services that authenticate via
Naver.

Phase: P1.1 (directory/schema helpers + name sanitize + index lock) is included in
this commit. P1.2~P1.7 (dataclass, CRUD, verify, seed) follow in later commits.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Constants / directories
# ─────────────────────────────────────────────────────────────────────────

# Default location — folded into the user's ttc-allinone-data data directory (D4).
# Can be overridden via env for test / isolated environments.
_DEFAULT_ROOT = "~/ttc-allinone-data/auth-profiles"

INDEX_VERSION = 1

# Catalog file / lock file / storage file extension.
_INDEX_FILENAME = "_index.json"
_LOCK_FILENAME = "_index.lock"
_STORAGE_SUFFIX = ".storage.json"

# Permissions — storage and catalog are user read/write only.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# Name sanitize — block path traversal + filesystem safe + avoid being mistaken
# for a CLI option. The first character must be alphanumeric (so a leading `-`
# does not look like a CLI flag). Length 64 (most filesystems allow 255, but
# this is a reasonable cap).
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _root() -> Path:
    """auth-profiles root directory for the current execution context.

    Can be overridden via ``AUTH_PROFILES_DIR`` env (for test / isolation).
    Re-reads env on every call, so monkeypatching is reflected immediately.
    """
    raw = os.environ.get("AUTH_PROFILES_DIR") or _DEFAULT_ROOT
    return Path(raw).expanduser()


def _index_path() -> Path:
    return _root() / _INDEX_FILENAME


def _lock_path() -> Path:
    return _root() / _LOCK_FILENAME


def _storage_path(name: str) -> Path:
    """Profile name → storage file path. ``name`` must have already passed ``_validate_name``."""
    return _root() / f"{name}{_STORAGE_SUFFIX}"


def _ensure_root() -> Path:
    """Create the root directory with mode 0700 if missing. If it already exists, fix the mode.

    Equivalent to ``mkdir -p`` but enforces permissions. Does not touch the
    parent directory (``ttc-allinone-data`` itself depends on the user environment).
    """
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    # mkdir's mode can be affected by umask, so chmod explicitly.
    try:
        os.chmod(root, _DIR_MODE)
    except OSError as e:
        # Externally mounted directories etc. may not allow chmod — warn only.
        log.warning("[auth-profiles] failed to set root mode 0700 (%s): %s", root, e)
    return root


# ─────────────────────────────────────────────────────────────────────────
# Name validation (block path traversal / special characters)
# ─────────────────────────────────────────────────────────────────────────

class AuthProfileError(Exception):
    """Base class for all user-visible errors in the auth-profile module."""


class InvalidProfileNameError(AuthProfileError, ValueError):
    """Profile name violates the sanitize rules."""


def _validate_name(name: str) -> None:
    """Validate a profile name. Raises ``InvalidProfileNameError`` on violation.

    Allowed: ``^[a-zA-Z0-9][a-zA-Z0-9_\\-]{0,63}$``

    Blocked:
    - empty string / None
    - first character not alphanumeric (avoid CLI flag confusion)
    - path separators such as ``/`` ``.`` ``\\``
    - non-ASCII characters such as Hangul / emoji
    - longer than 64 characters
    """
    if not isinstance(name, str) or not name:
        raise InvalidProfileNameError("profile name is empty")
    if not _NAME_RE.match(name):
        raise InvalidProfileNameError(
            f"invalid profile name: {name!r} "
            "(allowed: letters/digits/_/- only, first char alphanumeric, 1~64 chars)"
        )


# ─────────────────────────────────────────────────────────────────────────
# Index lock + atomic read-modify-write
# ─────────────────────────────────────────────────────────────────────────

@contextmanager
def _index_lock() -> Iterator[None]:
    """Hold an advisory exclusive lock on the ``_index.lock`` file.

    Uses POSIX ``fcntl.flock``. Serializes both multiple threads in the same
    process and other processes. Calling ``_load_index`` / ``_save_index``
    inside a ``with _index_lock():`` block makes the read-modify-write cycle
    safe.

    Note: supported on macOS / Linux. Windows lacks fcntl, so this module is
    POSIX-only. (Host daemon assumption — same operating model as the
    Recording UI.)
    """
    _ensure_root()
    lock_p = _lock_path()
    # The lock file itself can stay empty — we just acquire and release the flock.
    fd = os.open(str(lock_p), os.O_RDWR | os.O_CREAT, _FILE_MODE)
    try:
        os.chmod(lock_p, _FILE_MODE)
    except OSError:
        pass
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _empty_index() -> dict:
    """Default shape for an empty catalog."""
    return {"version": INDEX_VERSION, "profiles": []}


def _load_index() -> dict:
    """Load ``_index.json`` as a dict. Returns an empty catalog if missing.

    This function does not acquire the lock itself — callers must call it
    inside ``with _index_lock():`` for the read-modify-write cycle to be safe.
    """
    p = _index_path()
    if not p.exists():
        return _empty_index()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # If the catalog file is corrupted, fall back to an empty catalog. The
        # storage files remain on disk so the user can recover manually. Log
        # only as a warning.
        log.warning("[auth-profiles] failed to load _index.json — proceeding with empty catalog (%s)", e)
        return _empty_index()
    # Repair minimum structure (in case it was edited externally).
    if not isinstance(data, dict):
        return _empty_index()
    data.setdefault("version", INDEX_VERSION)
    data.setdefault("profiles", [])
    if not isinstance(data["profiles"], list):
        data["profiles"] = []
    return data


def _save_index(data: dict) -> None:
    """Atomically save dict to ``_index.json``. Enforces 0600 permissions.

    This function also does not acquire the lock — callers must call it inside
    ``with _index_lock():``. Atomicity is guaranteed via the tmp + ``os.replace``
    pattern.
    """
    _ensure_root()
    p = _index_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    # Set new file permissions to 0600 from the start — avoid umask interference.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        # Remove traces of a partial write.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.chmod(tmp, _FILE_MODE)
    os.replace(tmp, p)


def _atomic_update(updater: Callable[[dict], dict]) -> dict:
    """Run ``load → updater(data) → save`` in one shot while holding the lock.

    ``updater`` receives a dict and must return *the modified dict* (in-place
    mutation is allowed — just don't forget to return). The return value is
    the final saved data.

    Concurrency: when multiple processes / threads on the same host all update
    the catalog through this helper, updates are serialized.
    """
    with _index_lock():
        data = _load_index()
        new_data = updater(data)
        if not isinstance(new_data, dict):
            raise TypeError("_atomic_update: updater must return a dict")
        _save_index(new_data)
        return new_data


# ─────────────────────────────────────────────────────────────────────────
# Permission inspection helpers (for tests + operational diagnostics)
# ─────────────────────────────────────────────────────────────────────────

def _file_mode(p: Path) -> int:
    """File permission bits (S_IMODE)."""
    return stat.S_IMODE(p.stat().st_mode)


# ─────────────────────────────────────────────────────────────────────────
# Dataclass — Fingerprint / Verify / AuthProfile (P1.2)
# ─────────────────────────────────────────────────────────────────────────
#
# Serialization rules:
#   - Every dataclass has a ``to_dict()`` / ``from_dict()`` pair.
#   - storage_path is stored in _index.json as a *relative path (filename only)*.
#     The ``storage_path`` on an AuthProfile instance is always an absolute Path
#     — at load time it is resolved as ``_root() / filename``. This way the
#     catalog is not tied to a specific environment, and changing
#     ``AUTH_PROFILES_DIR`` alone is enough for it to work.
#   - For JSON compatibility, every to_dict returns plain dict (no Path / dataclass).

# Operational defaults captured into the fingerprint at seed time
# (D10 — UA is capture-only).
_DEFAULT_VIEWPORT_W = 1280
_DEFAULT_VIEWPORT_H = 800
_DEFAULT_LOCALE = "ko-KR"
_DEFAULT_TIMEZONE = "Asia/Seoul"
_DEFAULT_COLOR_SCHEME = "light"
_DEFAULT_PLAYWRIGHT_CHANNEL = "chromium"


@dataclass
class FingerprintProfile:
    """Browser fingerprint that must be consistent across all 4 phases of recording/replay (D10).

    We do not arbitrarily spoof UA — disagreement with the sec-ch-ua Client Hints
    *increases* bot suspicion. With the same Playwright version/channel the UA
    matches naturally, so this profile keeps UA as *capture-only* (informational).
    """

    viewport_width: int
    viewport_height: int
    locale: str
    timezone_id: str
    color_scheme: str = _DEFAULT_COLOR_SCHEME
    playwright_version: str = ""
    playwright_channel: str = _DEFAULT_PLAYWRIGHT_CHANNEL
    captured_user_agent: str = ""

    @classmethod
    def default(cls) -> "FingerprintProfile":
        """Operational defaults — 1280x800 / ko-KR / Asia/Seoul / light / chromium."""
        return cls(
            viewport_width=_DEFAULT_VIEWPORT_W,
            viewport_height=_DEFAULT_VIEWPORT_H,
            locale=_DEFAULT_LOCALE,
            timezone_id=_DEFAULT_TIMEZONE,
        )

    def to_playwright_open_args(self) -> list[str]:
        """CLI options for ``playwright open`` / ``codegen``. UA option not included (D10).

        viewport-size is comma-separated (the actual format used by the Playwright CLI).
        """
        return [
            "--viewport-size", f"{self.viewport_width},{self.viewport_height}",
            "--lang", self.locale,
            "--timezone", self.timezone_id,
            "--color-scheme", self.color_scheme,
        ]

    def to_browser_context_kwargs(self) -> dict:
        """kwargs for Playwright Python ``browser.new_context()`` (used by replay/verify)."""
        return {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
        }

    def to_env(self) -> dict:
        """Convert to env vars (replay_proxy → executor context option overrides)."""
        return {
            "PLAYWRIGHT_VIEWPORT": f"{self.viewport_width}x{self.viewport_height}",
            "PLAYWRIGHT_LOCALE": self.locale,
            "PLAYWRIGHT_TIMEZONE": self.timezone_id,
            "PLAYWRIGHT_COLOR_SCHEME": self.color_scheme,
        }

    def to_dict(self) -> dict:
        return {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
            "playwright_version": self.playwright_version,
            "playwright_channel": self.playwright_channel,
            "captured_user_agent": self.captured_user_agent,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FingerprintProfile":
        """Catalog dict → FingerprintProfile. Missing keys fall back to defaults."""
        viewport = d.get("viewport") or {}
        return cls(
            viewport_width=int(viewport.get("width", _DEFAULT_VIEWPORT_W)),
            viewport_height=int(viewport.get("height", _DEFAULT_VIEWPORT_H)),
            locale=str(d.get("locale", _DEFAULT_LOCALE)),
            timezone_id=str(d.get("timezone_id", _DEFAULT_TIMEZONE)),
            color_scheme=str(d.get("color_scheme", _DEFAULT_COLOR_SCHEME)),
            playwright_version=str(d.get("playwright_version", "")),
            playwright_channel=str(d.get("playwright_channel", _DEFAULT_PLAYWRIGHT_CHANNEL)),
            captured_user_agent=str(d.get("captured_user_agent", "")),
        )


@dataclass
class NaverProbeSpec:
    """Specification for the naver-side weak negative check (D13).

    ``kind="login_form_negative"`` — if ``selector`` is *visible*, treat it as
    logged out. This is a safe approach that does not depend on the fragile
    silent-refresh endpoint.
    """

    url: str = "https://nid.naver.com/"
    kind: str = "login_form_negative"
    selector: str = "input[name='id']"

    def to_dict(self) -> dict:
        return {"url": self.url, "kind": self.kind, "selector": self.selector}

    @classmethod
    def from_dict(cls, d: dict) -> "NaverProbeSpec":
        return cls(
            url=str(d.get("url", "https://nid.naver.com/")),
            kind=str(d.get("kind", "login_form_negative")),
            selector=str(d.get("selector", "input[name='id']")),
        )


@dataclass
class VerifySpec:
    """Profile verify specification (D13).

    service_url is required. service_text is optional — if set, verification is
    strong (the text must be found); if empty, verification is weak (just check
    that the protected URL is reachable). naver_probe is optional weak — its
    failure does not affect the OK verdict (warn-only).
    """

    service_url: str
    service_text: str = ""
    naver_probe: Optional[NaverProbeSpec] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "service_url": self.service_url,
            "service_text": self.service_text,
        }
        if self.naver_probe is not None:
            d["naver_probe"] = self.naver_probe.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "VerifySpec":
        probe_raw = d.get("naver_probe")
        probe = NaverProbeSpec.from_dict(probe_raw) if isinstance(probe_raw, dict) else None
        return cls(
            service_url=str(d["service_url"]),
            service_text=str(d.get("service_text") or ""),
            naver_probe=probe,
        )


@dataclass
class AuthProfile:
    """A single entry in the catalog.

    ``storage_path`` is an absolute path (composed with ``_root()`` at load
    time). The catalog JSON stores *only the filename* — for environment
    portability (D3).
    """

    name: str
    service_domain: str
    storage_path: Path
    created_at: str               # ISO 8601
    last_verified_at: Optional[str]
    ttl_hint_hours: int
    verify: VerifySpec
    fingerprint: FingerprintProfile
    host_machine_id: str
    chips_supported: bool
    session_storage_warning: bool
    verify_history: list[dict] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        """Serialize for the catalog — embed only the *filename* of storage_path (portability)."""
        return {
            "name": self.name,
            "service_domain": self.service_domain,
            "storage_path": self.storage_path.name,
            "created_at": self.created_at,
            "last_verified_at": self.last_verified_at,
            "ttl_hint_hours": self.ttl_hint_hours,
            "verify": self.verify.to_dict(),
            "fingerprint": self.fingerprint.to_dict(),
            "host_machine_id": self.host_machine_id,
            "chips_supported": self.chips_supported,
            "session_storage_warning": self.session_storage_warning,
            "verify_history": list(self.verify_history),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AuthProfile":
        """Catalog dict → AuthProfile.

        ``storage_path`` is resolved as ``_root() / <filename>``. That means
        the current ``AUTH_PROFILES_DIR`` env affects the result — this is
        intentional (the same catalog can be reused across different
        environments).
        """
        storage_filename = str(d["storage_path"])
        # Safety net — if the catalog accidentally contains an absolute path,
        # extract just the filename.
        storage_basename = Path(storage_filename).name
        return cls(
            name=str(d["name"]),
            service_domain=str(d.get("service_domain", "")),
            storage_path=_root() / storage_basename,
            created_at=str(d.get("created_at", "")),
            last_verified_at=d.get("last_verified_at"),
            ttl_hint_hours=int(d.get("ttl_hint_hours", 12)),
            verify=VerifySpec.from_dict(d["verify"]),
            fingerprint=FingerprintProfile.from_dict(d.get("fingerprint") or {}),
            host_machine_id=str(d.get("host_machine_id", "")),
            chips_supported=bool(d.get("chips_supported", False)),
            session_storage_warning=bool(d.get("session_storage_warning", False)),
            verify_history=list(d.get("verify_history") or []),
            notes=str(d.get("notes", "")),
        )


# ─────────────────────────────────────────────────────────────────────────
# CRUD — list / get / delete / upsert (P1.3)
# ─────────────────────────────────────────────────────────────────────────

class ProfileNotFoundError(AuthProfileError, KeyError):
    """Looked up by name, but the catalog has no such entry."""


def list_profiles() -> list["AuthProfile"]:
    """Return all profiles in the catalog, sorted ascending by ``name``.

    If the catalog file is missing, returns an empty list. Corrupt entries are
    silently skipped with a warning log.
    """
    with _index_lock():
        data = _load_index()

    out: list[AuthProfile] = []
    for raw in data.get("profiles", []):
        if not isinstance(raw, dict):
            log.warning("[auth-profiles] malformed catalog entry (not a dict) — skipping")
            continue
        try:
            out.append(AuthProfile.from_dict(raw))
        except (KeyError, TypeError, ValueError) as e:
            # Don't break the entire list because a single entry is corrupt.
            log.warning(
                "[auth-profiles] failed to load catalog entry — skipping: name=%r err=%s",
                raw.get("name"), e,
            )
    out.sort(key=lambda p: p.name)
    return out


def get_profile(name: str) -> "AuthProfile":
    """Look up a profile by name. Raises ``ProfileNotFoundError`` if missing.

    The name is validated up-front by ``_validate_name`` — blocks path traversal.
    """
    _validate_name(name)
    with _index_lock():
        data = _load_index()
    for raw in data.get("profiles", []):
        if isinstance(raw, dict) and raw.get("name") == name:
            return AuthProfile.from_dict(raw)
    raise ProfileNotFoundError(f"profile '{name}' not found")


def delete_profile(name: str) -> None:
    """Delete a profile — remove the catalog entry + unlink the storage file.

    If the profile is not in the catalog, raises ``ProfileNotFoundError``. If
    the storage file is already missing, silently passes (idempotency). Both
    steps run while holding the lock to avoid races.
    """
    _validate_name(name)

    found_holder = {"hit": False, "filename": ""}

    def updater(d: dict) -> dict:
        kept = []
        for raw in d.get("profiles", []):
            if isinstance(raw, dict) and raw.get("name") == name:
                found_holder["hit"] = True
                # Read the storage filename from the catalog — robust against
                # external changes.
                found_holder["filename"] = str(raw.get("storage_path") or "")
                continue
            kept.append(raw)
        d["profiles"] = kept
        return d

    # Update the catalog under the lock → unlink the storage file after the lock
    # is released. The unlink could be done under the lock too, but there is no
    # reason to extend the time other processes are blocked while we do disk IO.
    _atomic_update(updater)

    if not found_holder["hit"]:
        raise ProfileNotFoundError(f"profile '{name}' not found")

    storage_filename = found_holder["filename"] or f"{name}{_STORAGE_SUFFIX}"
    # Safety net — even if the catalog accidentally contains an absolute path,
    # use only the filename.
    storage_basename = Path(storage_filename).name
    storage_p = _root() / storage_basename
    try:
        storage_p.unlink()
        log.info("[auth-profiles] deleted — name=%s storage=%s", name, storage_p)
    except FileNotFoundError:
        # Idempotent — if the file is already missing, only the catalog cleanup ran.
        log.info("[auth-profiles] deleted (storage file already missing) — name=%s", name)
    except OSError as e:
        # The catalog has already been updated but unlinking the file failed.
        # The user must clean up manually.
        log.warning(
            "[auth-profiles] failed to unlink storage file — name=%s storage=%s err=%s",
            name, storage_p, e,
        )


def _upsert_profile(profile: "AuthProfile") -> None:
    """Insert or update a profile in the catalog (overwrite same name on re-seed).

    Internal — used by ``seed_profile`` (P1.7). External callers should go
    through ``seed_profile`` so that fingerprint capture / dump validation /
    verify all run consistently.
    """
    _validate_name(profile.name)
    serialized = profile.to_dict()

    def updater(d: dict) -> dict:
        kept = [
            raw for raw in d.get("profiles", [])
            if not (isinstance(raw, dict) and raw.get("name") == profile.name)
        ]
        kept.append(serialized)
        d["profiles"] = kept
        return d

    _atomic_update(updater)


# ─────────────────────────────────────────────────────────────────────────
# Identity helpers — machine_id / Playwright version / CHIPS gate (P1.4)
# ─────────────────────────────────────────────────────────────────────────
#
# Host info helpers that underpin D11 (machine binding) + D14 (CHIPS version
# gate). All functions are *side-effect-free read-only* and fall back to an
# empty string / False on failure (the caller branches on it).

_MACHINE_ID_HASH_LEN = 8        # Length of the hash appended after hostname — avoid leaking the raw UUID.
_CHIPS_MIN_VERSION = (1, 54)    # Playwright version that introduced partition_key.
_PLAYWRIGHT_VERSION_TIMEOUT_SEC = 10.0
_MACHINE_UUID_TIMEOUT_SEC = 5.0
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _read_machine_uuid_macos() -> str:
    """macOS — extract IOPlatformUUID via ``ioreg``. Empty string on failure."""
    try:
        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=_MACHINE_UUID_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("[auth-profiles] ioreg call failed — %s", e)
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "IOPlatformUUID" in line:
            # Example format: `    "IOPlatformUUID" = "ABCDEF12-..."`
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"')
    return ""


def _read_machine_uuid_linux() -> str:
    """Linux — ``/etc/machine-id`` or dbus fallback. Empty string if neither exists."""
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return content
        except OSError:
            continue
    return ""


def _read_machine_uuid() -> str:
    """Per-platform machine UUID. Empty string for unsupported OS / extraction failure."""
    if sys.platform == "darwin":
        return _read_machine_uuid_macos()
    if sys.platform.startswith("linux"):
        return _read_machine_uuid_linux()
    return ""


def current_machine_id() -> str:
    """Stable machine identifier (D11). Format: ``hostname:hash8`` or ``hostname``.

    Returns the same value for repeated calls on the same machine. We do not
    expose the raw UUID — only the first 8 chars of its sha256 — so even if it
    leaks into the catalog / logs, it is hard to reverse-engineer the machine
    fingerprint.
    """
    hostname = socket.gethostname() or "unknown-host"
    uuid_str = _read_machine_uuid()
    if not uuid_str:
        # Failed to extract the machine UUID — fall back to hostname only.
        # In this case different machines with the same hostname cannot be
        # distinguished — we assume the user does not create hostname conflicts
        # (personal QA / single-machine scenarios are the premise).
        return hostname
    digest = hashlib.sha256(uuid_str.encode("utf-8")).hexdigest()[:_MACHINE_ID_HASH_LEN]
    return f"{hostname}:{digest}"


def current_playwright_version() -> str:
    """The ``X.Y.Z`` portion of ``playwright --version``. Empty string on failure.

    Returns an empty string if the Playwright CLI is not on PATH or the call
    itself fails, so the caller can branch into a fallback.
    """
    try:
        result = subprocess.run(
            ["playwright", "--version"],
            capture_output=True,
            text=True,
            timeout=_PLAYWRIGHT_VERSION_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("[auth-profiles] playwright --version call failed — %s", e)
        return ""
    if result.returncode != 0:
        return ""
    # Example output: "Version 1.57.0"
    m = _VERSION_RE.search(result.stdout or "")
    if not m:
        return ""
    return m.group(0)


def _parse_version(v: str) -> Optional[tuple[int, int, int]]:
    """semver-like string → ``(major, minor, patch)``. None on failure."""
    m = _VERSION_RE.search(v or "")
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except ValueError:
        return None


def chips_supported_by_runtime() -> bool:
    """Does the Playwright on PATH support CHIPS (Partitioned cookie) preservation? (D14)

    From Playwright 1.54+, ``partition_key`` is included in the
    ``storage_state`` dump. Below that, Partitioned cookies are silently
    dropped, so we must reject seeding.
    """
    parsed = _parse_version(current_playwright_version())
    if parsed is None:
        return False
    return parsed[:2] >= _CHIPS_MIN_VERSION


# ─────────────────────────────────────────────────────────────────────────
# Dump validation / auxiliary checks (P1.5)
# ─────────────────────────────────────────────────────────────────────────
#
# Format of the JSON produced by Playwright ``BrowserContext.storage_state(path=...)``:
#
# {
#   "cookies": [
#     {"name": ..., "value": ..., "domain": ".naver.com", "path": "/", ...,
#      "partitionKey": "..."},   // 1.54+ only
#     ...
#   ],
#   "origins": [
#     {"origin": "https://booking.example.com",
#      "localStorage": [{"name": "...", "value": "..."}, ...]},
#     ...
#   ]
# }
#
# Playwright does not auto-preserve sessionStorage (D16 limitation), so this
# module takes capture data collected separately (gathered by P1.7 seed_profile)
# as an argument and analyzes that.

class EmptyDumpError(AuthProfileError, ValueError):
    """The storage dump is empty (cookies + origins both 0)."""


class MissingDomainError(AuthProfileError, ValueError):
    """The storage dump has no cookies for the expected domain(s)."""

    def __init__(self, missing: list[str]):
        super().__init__(f"missing domain cookies in storage dump: {missing}")
        self.missing = list(missing)


class ChipsNotSupportedError(AuthProfileError, RuntimeError):
    """The current Playwright does not support CHIPS (Partitioned) cookie preservation (<1.54)."""


# Suspicious sessionStorage key patterns (Q4 — both regex + base64-like length).
_SESSION_STORAGE_SUSPICIOUS_KEY_RE = re.compile(
    r"(token|auth|session|jwt|bearer|access|refresh|credential)",
    re.IGNORECASE,
)
# base64-like = a string of length ≥20 made up only of [A-Za-z0-9_+/=-].
_BASE64_LIKE_RE = re.compile(r"^[A-Za-z0-9_+/=\-]{20,}$")
# JWT — 2~3 base64url segments joined with dots (header.payload[.signature]).
# Strong signature: header begins with ``eyJ`` (= base64 of ``{"``).
_JWT_LIKE_RE = re.compile(r"^eyJ[A-Za-z0-9_=\-]+(\.[A-Za-z0-9_=\-]+){1,2}$")


def _load_storage_dump(storage_path: Path) -> dict:
    """Load the storage JSON. Corrupt / missing → ``EmptyDumpError``."""
    if not storage_path.exists():
        raise EmptyDumpError(f"storage file missing: {storage_path}")
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise EmptyDumpError(f"failed to load storage file: {storage_path} ({e})") from e
    if not isinstance(data, dict):
        raise EmptyDumpError(f"storage file format error (not a dict): {storage_path}")
    return data


def _normalize_domain(d: str) -> str:
    """Normalize a cookie domain — strip leading dot + lowercase. For matching comparisons."""
    return (d or "").lstrip(".").lower()


def _domain_matches(cookie_domain: str, expected: str) -> bool:
    """Does the cookie domain belong to the same domain tree as expected? (C8).

    Match rule — True if any of the following:
        1. cookie_domain == expected                    (exact)
        2. cookie_domain.endsWith('.' + expected)        (cookie is a subdomain of expected)
        3. expected.endsWith('.' + cookie_domain)        (cookie is a parent of expected — per
                                                         RFC 6265, a cookie set on the parent
                                                         domain is sent to child hosts)

    Rule 3 is needed for the common pattern where an SSO gateway issues a
    session cookie on the parent domain (e.g. ``.koreaconnect.kr``) and the
    child page (``portal.koreaconnect.kr``) authenticates with that cookie.
    Without it, seeding succeeds but validate_dump false-fails.

    Examples:
        cookie=".naver.com",         expected="naver.com"             → True  (1/2)
        cookie="accounts.naver.com", expected="naver.com"             → True  (2)
        cookie="naver.com",          expected="naver.com"             → True  (1)
        cookie="naver.com",          expected="api.naver.com"         → True  (3 — parent cookie)
        cookie=".koreaconnect.kr",   expected="portal.koreaconnect.kr"→ True  (3)
        cookie="evilnaver.com",      expected="naver.com"             → False
        cookie="naver.com.evil.com", expected="naver.com"             → False
    """
    cd = _normalize_domain(cookie_domain)
    exp = _normalize_domain(expected)
    if not cd or not exp:
        return False
    return (
        cd == exp
        or cd.endswith("." + exp)
        or exp.endswith("." + cd)
    )


def validate_dump(storage_path: Path, expected_domains: list[str]) -> None:
    """D12 — storage dump is non-empty and contains cookies in every expected domain tree.

    See ``_domain_matches`` for "domain tree" matching — a cookie at the
    expected name itself, any subdomain, or any parent counts as a match
    (covers the SSO parent-domain cookie case).

    Raises:
        EmptyDumpError: cookies + origins are both 0
        MissingDomainError: no matching cookie for at least one of the expected domains
    """
    data = _load_storage_dump(storage_path)
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    if not cookies and not origins:
        raise EmptyDumpError(f"storage is empty (cookies + origins both 0): {storage_path}")

    missing: list[str] = []
    for exp in expected_domains:
        if not exp:
            continue
        hit = any(
            _domain_matches(c.get("domain", ""), exp)
            for c in cookies
            if isinstance(c, dict)
        )
        if not hit:
            missing.append(exp)
    if missing:
        raise MissingDomainError(missing)


def has_partitioned_cookies(storage_path: Path) -> bool:
    """True if the dump has at least one cookie with ``partitionKey`` populated (D14).

    Only meaningful for files dumped by Playwright 1.54+. Below that the field
    is absent from the dump, so this always returns False.
    """
    try:
        data = _load_storage_dump(storage_path)
    except EmptyDumpError:
        return False
    for c in data.get("cookies", []):
        if not isinstance(c, dict):
            continue
        # Playwright keys — Python API uses partition_key, JSON dump usually
        # uses partitionKey. Check both (defensive).
        pkey = c.get("partitionKey") or c.get("partition_key")
        if pkey:
            return True
    return False


def _is_suspicious_session_storage_key(key: str) -> bool:
    """Q4 policy — suspicious key name?"""
    return bool(_SESSION_STORAGE_SUSPICIOUS_KEY_RE.search(key or ""))


def _is_suspicious_session_storage_value(value: str) -> bool:
    """Q4 policy — suspicious value? base64-like 20+ chars or JWT pattern."""
    if not value:
        return False
    return bool(_BASE64_LIKE_RE.match(value)) or bool(_JWT_LIKE_RE.match(value))


def _entry_is_suspicious(entry: object) -> bool:
    """Is one sessionStorage entry (``{"name": ..., "value": ...}``) suspicious?"""
    if not isinstance(entry, dict):
        return False
    name = entry.get("name", "")
    value = entry.get("value", "")
    if not isinstance(name, str) or not isinstance(value, str):
        return False
    return _is_suspicious_session_storage_key(name) or _is_suspicious_session_storage_value(value)


def _iter_session_storage_entries(session_storage: dict) -> Iterator[object]:
    """Flatten ``{origin: [entries...]}`` and yield every entry."""
    for entries in session_storage.values():
        if not isinstance(entries, list):
            continue
        yield from entries


def detect_session_storage_use(session_storage: dict) -> bool:
    """D16 — detect suspicious auth keys/values in captured sessionStorage data (Q4).

    Args:
        session_storage: dict of the form
            ``{"origin": [{"name": "k", "value": "v"}, ...], ...}``.
            At seed time this is collected per origin via
            ``page.evaluate("() => Object.fromEntries(...)")`` and merged
            (P1.7's responsibility).

    Returns:
        True — at least one suspicious key name OR suspicious value
        (base64-like 20+ chars) was found.
        False — empty data or no suspicious entries.

    Suspicion policy (Q4 — both regex + base64-like):
        - Key name contains ``token`` / ``auth`` / ``session`` / ``jwt`` / ``bearer`` etc.
        - Value is base64-like (alnum+/=- only, length ≥20)
    """
    if not isinstance(session_storage, dict) or not session_storage:
        return False
    return any(_entry_is_suspicious(e) for e in _iter_session_storage_entries(session_storage))


# ─────────────────────────────────────────────────────────────────────────
# verify_profile — service authoritative + naver weak probe (P1.6)
# ─────────────────────────────────────────────────────────────────────────
#
# Applies D9 (replay is also headed) + D10 (fingerprint pinning) + D13
# (dual-domain verify).
#
# Structure:
#   verify_profile (orchestrator)
#     ├─ _verify_service_side  ← authoritative; navigate to service_url after applying storage
#     └─ _verify_naver_probe   ← optional weak; navigate to probe_url after applying storage
#
# The two IO functions encapsulate the Playwright calls — tests can swap them
# via monkeypatch, and the real Playwright integration tests are split out into
# ``test/test_auth_profile_verify_pw.py``.

_VERIFY_HISTORY_MAX = 20
_VERIFY_NAV_TIMEOUT_MS = 30_000

# Operational default is headed (D9 — fingerprint stability). For e2e / CI,
# headless can be forced via env override — this is a test affordance and the
# user opts in explicitly.
_E2E_HEADLESS_ENV = "AUTH_PROFILE_VERIFY_HEADLESS"
_VERIFY_SLOW_MO_ENV = "AUTH_PROFILE_VERIFY_SLOW_MO_MS"
_VERIFY_HOLD_ENV = "AUTH_PROFILE_VERIFY_HOLD_MS"


def _verify_headless() -> bool:
    """If ``AUTH_PROFILE_VERIFY_HEADLESS=1``, run verify headless. Default is False (D9)."""
    return os.environ.get(_E2E_HEADLESS_ENV, "0") == "1"


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _verify_slow_mo_ms() -> int:
    """Playwright slow_mo so a human can follow along during headed seed verify."""
    return _env_int(_VERIFY_SLOW_MO_ENV, 500)


def _verify_hold_ms() -> int:
    """How long to keep the window open after reaching the verification page before closing."""
    return _env_int(_VERIFY_HOLD_ENV, 4_000)


def _now_iso() -> str:
    """ISO 8601 string close to KST (timezone-aware)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _verify_service_side(
    storage_path: Path,
    fingerprint: "FingerprintProfile",
    service_url: str,
    service_text: str,
    timeout_sec: int,
    visual_pause: bool = False,
) -> tuple[bool, float, Optional[str]]:
    """service-side authoritative verify (D13).

    Open a fresh headed context with storage applied and navigate to service_url.

    - If service_text is set, the page text must contain it for a pass.
    - If service_text is empty, pass requires HTTP < 400 + the final URL host
      to be in the same family as service_url's host. This is a weak mode that
      uses reaching the protected page itself as the verification signal.

    Returns:
        (ok, elapsed_ms, fail_reason) — fail_reason is None when ok=True.
    """
    from playwright.sync_api import sync_playwright

    started = time.time()
    fail_reason: Optional[str] = None
    ok = False
    headless = _verify_headless()
    launch_kwargs: dict[str, Any] = {"headless": headless}
    if visual_pause and not headless:
        launch_kwargs["slow_mo"] = _verify_slow_mo_ms()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            try:
                ctx_kwargs = fingerprint.to_browser_context_kwargs()
                if storage_path.exists():
                    ctx_kwargs["storage_state"] = str(storage_path)
                context = browser.new_context(**ctx_kwargs)
                try:
                    page = context.new_page()
                    response = page.goto(
                        service_url,
                        wait_until="load",
                        timeout=min(timeout_sec * 1000, _VERIFY_NAV_TIMEOUT_MS),
                    )
                    if service_text:
                        body_text = page.inner_text("body", timeout=5_000)
                        ok = service_text in body_text
                        if not ok:
                            fail_reason = "service_text_not_found"
                    else:
                        status = response.status if response is not None else 200
                        expected_host = _domain_from_url(service_url)
                        final_host = _domain_from_url(page.url)
                        ok = status < 400 and (
                            not expected_host
                            or final_host == expected_host
                            or _domain_matches(final_host, expected_host)
                        )
                        if not ok:
                            fail_reason = (
                                "service_page_not_reachable"
                                if status >= 400
                                else "service_url_redirected"
                            )
                    if visual_pause and not headless:
                        page.wait_for_timeout(_verify_hold_ms())
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as e:
        fail_reason = f"service_side_error: {type(e).__name__}: {e}"
        ok = False
    elapsed_ms = (time.time() - started) * 1000
    return ok, elapsed_ms, fail_reason


def _verify_naver_probe(
    storage_path: Path,
    fingerprint: "FingerprintProfile",
    probe: "NaverProbeSpec",
    timeout_sec: int,
) -> tuple[bool, float, Optional[str]]:
    """naver-side weak probe (D13). best-effort — failure does not flip the ok verdict.

    ``kind="login_form_negative"``: navigate to probe.url; if probe.selector is
    *visible*, treat it as logged out (False). If not visible, treat as
    logged in (True).

    Returns:
        (ok, elapsed_ms, fail_reason)
    """
    from playwright.sync_api import sync_playwright

    started = time.time()
    fail_reason: Optional[str] = None
    ok = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=_verify_headless())
            try:
                ctx_kwargs = fingerprint.to_browser_context_kwargs()
                if storage_path.exists():
                    ctx_kwargs["storage_state"] = str(storage_path)
                context = browser.new_context(**ctx_kwargs)
                try:
                    page = context.new_page()
                    page.goto(
                        probe.url,
                        wait_until="load",
                        timeout=min(timeout_sec * 1000, _VERIFY_NAV_TIMEOUT_MS),
                    )
                    if probe.kind == "login_form_negative":
                        # If the selector is *not* visible, treat as logged in.
                        try:
                            visible = page.locator(probe.selector).first.is_visible(
                                timeout=3_000,
                            )
                        except Exception:
                            visible = False
                        ok = not visible
                        if not ok:
                            fail_reason = "login_form_visible"
                    else:
                        fail_reason = f"unknown_probe_kind: {probe.kind}"
                        ok = False
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as e:
        fail_reason = f"naver_probe_error: {type(e).__name__}: {e}"
        ok = False
    elapsed_ms = (time.time() - started) * 1000
    return ok, elapsed_ms, fail_reason


def _record_verify(profile: "AuthProfile", *, ok: bool, detail: dict) -> None:
    """Persist the result of ``verify_profile`` into the catalog.

    - profile.last_verified_at = now (only on success)
    - append an entry to profile.verify_history (capped at _VERIFY_HISTORY_MAX)
    - update the catalog via _upsert_profile
    """
    now_iso = _now_iso()
    entry = {
        "at": now_iso,
        "ok": ok,
        "service_ms": detail.get("service_ms"),
        "naver_probe_ms": detail.get("naver_probe_ms"),
        "naver_ok": detail.get("naver_ok"),
    }
    if not ok and detail.get("fail_reason"):
        entry["fail_reason"] = detail["fail_reason"]
    history = list(profile.verify_history)
    history.append(entry)
    # Keep only the latest N entries — prevent the catalog from bloating.
    if len(history) > _VERIFY_HISTORY_MAX:
        history = history[-_VERIFY_HISTORY_MAX:]
    profile.verify_history = history
    if ok:
        profile.last_verified_at = now_iso
    try:
        _upsert_profile(profile)
    except Exception as e:
        # A catalog update failure does not affect the verify result itself — warn only.
        log.warning("[auth-profiles] failed to persist verify result to catalog — %s", e)


def verify_profile(
    profile: "AuthProfile",
    *,
    timeout_sec: int = 30,
    naver_probe: bool = True,
    visual_pause: bool = False,
) -> tuple[bool, dict]:
    """Verify a profile (D5, D13).

    service-side is *authoritative* — it must pass for ok=True. naver_probe is
    best-effort — its failure does not affect ok (only recorded in detail).

    Args:
        profile: the profile to verify.
        timeout_sec: navigation timeout for a single phase (not total — applied
            to service + optional probe each).
        naver_probe: if False, skip the naver probe phase entirely.

    Returns:
        ``(ok, detail)`` — detail keys:
            - ``service_ms``      : service verify elapsed ms
            - ``naver_probe_ms``  : probe verify elapsed ms (when run)
            - ``naver_ok``        : probe result (when run)
            - ``fail_reason``     : human-readable reason when ok=False
    """
    detail: dict[str, Any] = {
        "service_ms": None,
        "naver_probe_ms": None,
        "naver_ok": None,
    }

    svc_ok, svc_ms, svc_err = _verify_service_side(
        profile.storage_path,
        profile.fingerprint,
        profile.verify.service_url,
        profile.verify.service_text,
        timeout_sec,
        visual_pause=visual_pause,
    )
    detail["service_ms"] = round(svc_ms)
    if not svc_ok:
        detail["fail_reason"] = svc_err or "service_text_not_found"
        _record_verify(profile, ok=False, detail=detail)
        return (False, detail)

    if naver_probe and profile.verify.naver_probe is not None:
        probe_ok, probe_ms, probe_err = _verify_naver_probe(
            profile.storage_path,
            profile.fingerprint,
            profile.verify.naver_probe,
            timeout_sec,
        )
        detail["naver_probe_ms"] = round(probe_ms)
        detail["naver_ok"] = probe_ok
        if not probe_ok and probe_err:
            # best-effort — log it and keep going.
            log.info(
                "[auth-profiles] naver probe failed (best-effort, ok preserved) — %s",
                probe_err,
            )

    _record_verify(profile, ok=True, detail=detail)
    return (True, detail)


# ─────────────────────────────────────────────────────────────────────────
# seed_profile — one-time manual login lifecycle (P1.7)
# ─────────────────────────────────────────────────────────────────────────
#
# Flow:
#   1. Pre-checks — name sanitize + Playwright version gate (D14)
#   2. fingerprint resolve — defaults + capture runtime version (D10)
#   3. service_domain resolve — extract host from seed_url
#   4. Run ``playwright open <seed_url> --save-storage=<path> + fingerprint args``
#      (the user logs in directly + passes 2FA → closes the window)
#   5. Apply 0600 permissions + validate dump (cookies for both domains exist) — D12
#   6. Build a temporary AuthProfile object
#   7. verify_profile (service authoritative + naver weak probe) — D13
#   8. On pass, register in the catalog (verify does this automatically via
#      _record_verify). On failure, unlink the storage file + roll back the catalog.
#
# Limitations (P1):
#   - sessionStorage detection cannot run — ``playwright open`` has no capture
#     hook. ``session_storage_warning=False`` is fixed. A custom seed wrapper in
#     P1.7 v2 / a separate track could add this.

class SeedTimeoutError(AuthProfileError, TimeoutError):
    """``playwright open`` did not exit within ``timeout_sec`` (user did not close the window)."""


class SeedSubprocessError(AuthProfileError, RuntimeError):
    """The ``playwright open`` subprocess exited abnormally or could not be launched."""


class SeedVerifyFailedError(AuthProfileError, RuntimeError):
    """Post-seed ``verify_profile`` failed — not registered in the catalog."""


class InvalidServiceDomainError(AuthProfileError, ValueError):
    """Could not extract service_domain from seed_url and it was not specified explicitly."""


def _domain_from_url(url: str) -> str:
    """URL → hostname (lowercase). Empty string if extraction fails."""
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return ""
    return (parsed.hostname or "").lower()


def _capture_runtime_fingerprint(base: "FingerprintProfile") -> "FingerprintProfile":
    """Fill the fingerprint with the Playwright version currently on PATH.

    UA capture is expensive, so we defer it (verify can capture it during real
    execution); at seed time we only embed *playwright_version* + *channel*.
    """
    return FingerprintProfile(
        viewport_width=base.viewport_width,
        viewport_height=base.viewport_height,
        locale=base.locale,
        timezone_id=base.timezone_id,
        color_scheme=base.color_scheme,
        playwright_version=current_playwright_version(),
        playwright_channel=base.playwright_channel or _DEFAULT_PLAYWRIGHT_CHANNEL,
        captured_user_agent=base.captured_user_agent,
    )


def _run_seed_subprocess(cmd: list[str], timeout_sec: int) -> None:
    """Run the ``playwright open`` subprocess + wait for the user to close it.

    Raises:
        SeedTimeoutError: did not exit within ``timeout_sec`` (user did not close the window).
        SeedSubprocessError: failed to launch the subprocess or it returned a non-zero rc.
    """
    log.info("[auth-profiles] running subprocess — %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise SeedTimeoutError(
            f"seed timed out after {timeout_sec}s — user did not close the window"
        ) from e
    except (OSError, subprocess.SubprocessError) as e:
        raise SeedSubprocessError(f"failed to run playwright open: {e}") from e

    # `playwright open` exits with returncode=0 on normal exit. Even when the
    # user force-closes it, returncode is usually 0. Non-zero means a real error.
    if result.returncode != 0:
        stderr_tail = (result.stderr or b"").decode("utf-8", errors="replace")[-512:]
        raise SeedSubprocessError(
            f"playwright open exited abnormally (rc={result.returncode}): {stderr_tail}"
        )


def _safe_unlink(p: Path) -> None:
    """Unlink if it exists. Silent on failure (best-effort cleanup)."""
    try:
        p.unlink()
    except OSError:
        pass


def _resolve_seed_inputs(
    name: str,
    seed_url: str,
    service_domain: Optional[str],
    fingerprint: Optional["FingerprintProfile"],
) -> tuple[str, "FingerprintProfile"]:
    """Seed pre-checks + resolve service_domain / fingerprint.

    Raises:
        InvalidProfileNameError: name violates the sanitize rules.
        ChipsNotSupportedError: Playwright <1.54.
        InvalidServiceDomainError: failed to extract service_domain.
    """
    _validate_name(name)
    if not chips_supported_by_runtime():
        raise ChipsNotSupportedError(
            "Playwright >=1.54 required (CHIPS partition_key preservation). "
            f"current version: {current_playwright_version() or 'unknown'}"
        )
    base_fp = fingerprint if fingerprint is not None else FingerprintProfile.default()
    final_fp = _capture_runtime_fingerprint(base_fp)
    resolved_domain = service_domain or _domain_from_url(seed_url)
    if not resolved_domain:
        raise InvalidServiceDomainError(
            f"could not extract service_domain from seed_url: {seed_url!r}. "
            "specify it explicitly via the service_domain argument."
        )
    return resolved_domain, final_fp


def _do_seed_io(
    seed_url: str,
    storage_path: Path,
    fingerprint: "FingerprintProfile",
    expected_domains: list[str],
    timeout_sec: int,
) -> None:
    """Run ``playwright open`` + lock down permissions + validate the dump.

    On failure, clean up the storage file. On success, returns with storage_path
    in a guaranteed state.
    """
    _safe_unlink(storage_path)  # Clean up any stale leftovers.
    cmd = [
        "playwright", "open", seed_url,
        "--save-storage", str(storage_path),
    ]
    cmd += fingerprint.to_playwright_open_args()
    try:
        _run_seed_subprocess(cmd, timeout_sec)
    except (SeedTimeoutError, SeedSubprocessError):
        _safe_unlink(storage_path)
        raise

    # Lock down permissions — avoid umask interference on the file Playwright created.
    if storage_path.exists():
        try:
            os.chmod(storage_path, _FILE_MODE)
        except OSError as e:
            log.warning("[auth-profiles] failed to set storage mode 0600 — %s", e)

    # D12 — validate dump.
    try:
        validate_dump(storage_path, expected_domains)
    except (EmptyDumpError, MissingDomainError):
        _safe_unlink(storage_path)
        raise


def _verify_seeded_profile_or_rollback(profile: "AuthProfile") -> None:
    """Verify after seeding (D13). On failure, roll back catalog + storage and raise."""
    ok, detail = verify_profile(profile, naver_probe=True, visual_pause=True)
    if ok:
        return
    # _record_verify may have already inserted a failure entry into the catalog — roll it back.
    try:
        delete_profile(profile.name)
    except ProfileNotFoundError:
        _safe_unlink(profile.storage_path)
    raise SeedVerifyFailedError(
        f"verify after seed failed: {detail.get('fail_reason') or 'unknown'}"
    )


def seed_profile(
    name: str,
    seed_url: str,
    verify: "VerifySpec",
    *,
    service_domain: Optional[str] = None,
    fingerprint: Optional["FingerprintProfile"] = None,
    ttl_hint_hours: int = 12,
    notes: str = "",
    timeout_sec: int = 600,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> "AuthProfile":
    """One-time manual login → save storageState → verify → register in catalog (D2).

    Args:
        name: profile identifier name (must pass sanitize).
        seed_url: WARNING — *the service entry URL* (not the Naver login URL — D6 mental model).
        verify: service-side authoritative + naver-side optional weak probe spec.
        service_domain: if not specified, extracted from seed_url's host.
        fingerprint: if not specified, ``FingerprintProfile.default()`` is used.
            In either case the runtime Playwright version is captured into it.
        ttl_hint_hours: estimated expiry value for UI display. Actual expiry is determined by verify.
        notes: free-form notes.
        timeout_sec: cap on user-input wait. Exceeding raises ``SeedTimeoutError``.
        progress_callback: progress hook for UI polling. Receives ``(phase, message)``.

    Returns:
        the registered ``AuthProfile``.

    Raises:
        InvalidProfileNameError: name violation.
        ChipsNotSupportedError: Playwright <1.54 (D14).
        InvalidServiceDomainError: failed to extract service_domain.
        SeedTimeoutError / SeedSubprocessError: failure in the subprocess phase.
        EmptyDumpError / MissingDomainError: dump validation failure (D12).
        SeedVerifyFailedError: post-seed verify failed (D13).
    """
    resolved_domain, final_fp = _resolve_seed_inputs(
        name, seed_url, service_domain, fingerprint,
    )

    storage_path = _storage_path(name)
    _ensure_root()

    log.info(
        "[auth-profiles] seed start — name=%s seed_url=%s service_domain=%s",
        name, seed_url, resolved_domain,
    )

    expected_domains = ["naver.com"]
    if resolved_domain and resolved_domain not in expected_domains:
        expected_domains.append(resolved_domain)

    if progress_callback is not None:
        progress_callback(
            "login_waiting",
            "Waiting for the login window — once you confirm the post-login screen, close the open browser window to save.",
        )
    _do_seed_io(seed_url, storage_path, final_fp, expected_domains, timeout_sec)
    if progress_callback is not None:
        progress_callback(
            "verifying",
            "Session saved — opening the verification target page slowly to confirm.",
        )

    profile = AuthProfile(
        name=name,
        service_domain=resolved_domain,
        storage_path=storage_path,
        created_at=_now_iso(),
        last_verified_at=None,
        ttl_hint_hours=ttl_hint_hours,
        verify=verify,
        fingerprint=final_fp,
        host_machine_id=current_machine_id(),
        chips_supported=True,                # gate already passed
        session_storage_warning=False,       # P1 limitation — playwright open lacks a capture hook
        verify_history=[],
        notes=notes,
    )

    _verify_seeded_profile_or_rollback(profile)

    log.info(
        "[auth-profiles] seed complete — name=%s service_domain=%s verify=ok",
        name, resolved_domain,
    )
    return profile
