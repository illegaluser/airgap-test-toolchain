"""Auth Profile (Naver-OAuth 연동 서비스 E2E) — 시드된 storageState 카탈로그.

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md

본 모듈은 다음을 담당한다:

- ``~/ttc-allinone-data/auth-profiles/`` 디렉토리 + ``_index.json`` 카탈로그 관리
- 사람이 1회 통과한 OAuth 라운드트립 결과 storageState 파일을 *이름 + 메타* 로
  보관 → 이후 녹화/재생에서 재사용
- service-side authoritative + naver-side optional weak probe 검증
- fingerprint pinning (UA 제외 — viewport/locale/timezone/color_scheme +
  Playwright 버전·채널)

이 모듈은 *기존* ``zero_touch_qa.auth`` (form/TOTP/OAuth DSL 액션) 와 별개. 본
모듈의 auth-profile 은 IdP 화면 자체를 자동화로 통과시키는 게 불가능한 케이스
(네이버 등) 의 보완책이다.

Phase: P1.1 (디렉토리/스키마 헬퍼 + 이름 sanitize + index 락) 까지 본 커밋에
포함. P1.2~P1.7 (dataclass, CRUD, verify, seed) 은 후속 커밋.
"""

from __future__ import annotations

import portalocker  # cross-platform exclusive file lock (Windows + POSIX)
import hashlib
import json
import logging
import os
import re
import socket
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# 상수 / 디렉토리
# ─────────────────────────────────────────────────────────────────────────

# 기본 위치 — 사용자 홈의 ttc-allinone-data 데이터 디렉토리에 흡수 (D4).
# env override 로 테스트 / 격리 환경 분리 가능.
_DEFAULT_ROOT = "~/ttc-allinone-data/auth-profiles"

INDEX_VERSION = 1

# 카탈로그 파일 / 락 파일 / storage 파일 확장자.
_INDEX_FILENAME = "_index.json"
_LOCK_FILENAME = "_index.lock"
_STORAGE_SUFFIX = ".storage.json"

# 권한 — storage 와 카탈로그는 사용자 read/write 전용.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# 이름 sanitize — path traversal 방지 + 파일시스템 안전 + 명령행 옵션 오인 회피.
# 첫 글자는 alphanumeric (앞 `-` 가 CLI 플래그처럼 보이는 것 차단).
# 길이 64 자 (대부분의 FS 가 255 허용하지만 합리적 상한).
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _root() -> Path:
    """현재 실행 컨텍스트의 auth-profiles 루트 디렉토리.

    env ``AUTH_PROFILES_DIR`` 로 override 가능 (테스트 / 격리용).
    매 호출마다 env 를 새로 읽으므로 monkeypatch 가 즉시 반영된다.
    """
    raw = os.environ.get("AUTH_PROFILES_DIR") or _DEFAULT_ROOT
    return Path(raw).expanduser()


def _index_path() -> Path:
    return _root() / _INDEX_FILENAME


def _lock_path() -> Path:
    return _root() / _LOCK_FILENAME


def _storage_path(name: str) -> Path:
    """프로파일 이름 → storage 파일 경로. ``name`` 은 사전에 ``_validate_name`` 통과해야 함."""
    return _root() / f"{name}{_STORAGE_SUFFIX}"


def _ensure_root() -> Path:
    """루트 디렉토리가 없으면 0700 으로 생성. 이미 있으면 권한만 보정.

    ``mkdir -p`` 와 동일하지만 권한을 강제한다. 부모 디렉토리는 손대지 않는다
    (``ttc-allinone-data`` 자체는 사용자 환경에 의존).
    """
    root = _root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        # 부모 디렉토리에 쓰기 권한이 없어 root 생성 실패. 흔한 원인:
        # 다른 사용자(root 등)로 한 번 만들었던 ``ttc-allinone-data`` 가 그대로
        # 남아 현재 사용자가 그 안에 서브디렉토리를 못 만드는 케이스.
        # raw PermissionError 가 워커까지 올라가면 UI 에 repr 만 떠 진단 어려움.
        raise AuthProfileError(
            f"auth-profile 루트 생성 실패 — '{root}' 에 쓰기 권한 없음. "
            f"부모 디렉토리 소유권/권한을 확인하세요 ({e})"
        ) from e
    # 부모가 만들어진 직후 mode 가 umask 영향 받을 수 있어 명시적으로 chmod.
    try:
        os.chmod(root, _DIR_MODE)
    except OSError as e:
        # 외부에서 마운트한 디렉토리 등 chmod 안 되는 환경 — warn 만.
        log.warning("[auth-profiles] failed to set root perms 0700 (%s): %s", root, e)
    return root


# ─────────────────────────────────────────────────────────────────────────
# 이름 검증 (path traversal / 특수문자 차단)
# ─────────────────────────────────────────────────────────────────────────

class AuthProfileError(Exception):
    """auth-profile 모듈의 모든 사용자 가시 에러의 베이스."""


class InvalidProfileNameError(AuthProfileError, ValueError):
    """프로파일 이름이 sanitize 규칙을 위반."""


def _validate_name(name: str) -> None:
    """프로파일 이름 검증. 위반 시 ``InvalidProfileNameError``.

    허용: ``^[a-zA-Z0-9][a-zA-Z0-9_\\-]{0,63}$``

    차단:
    - 빈 문자열 / None
    - 첫 글자가 alphanumeric 이 아닌 경우 (CLI flag 오인 차단)
    - ``/`` ``.`` ``\\`` 등 path 구분자
    - 한글 / 이모지 등 ASCII 외 문자
    - 64 자 초과
    """
    if not isinstance(name, str) or not name:
        raise InvalidProfileNameError("Profile name is empty")
    if not _NAME_RE.match(name):
        raise InvalidProfileNameError(
            f"Profile name is invalid: {name!r} "
            "(allowed: letters/digits/_/- only, first char alphanumeric, 1–64 chars)"
        )


# ─────────────────────────────────────────────────────────────────────────
# Index 락 + 원자적 read-modify-write
# ─────────────────────────────────────────────────────────────────────────

# 같은 프로세스 안 스레드 직렬화용 — portalocker 만으로는 부족.
# Windows 는 같은 프로세스의 다른 스레드가 같은 파일 락을 시도하면 EDEADLK 로
# 거절한다 (msvcrt.locking 의 동작). POSIX 도 fcntl.flock 의 의미가 (process,
# fd) 단위라 같은 파일을 여러 fd 로 열어 잡으면 서로 무시되는 구간이 생길 수
# 있다. 모든 플랫폼에서 동일 프로세스 내 스레드 직렬화를 보장하려면 process-
# wide threading.Lock 으로 한 겹 더 감싸야 한다.
_INDEX_THREAD_LOCK = threading.Lock()


@contextmanager
def _index_lock() -> Iterator[None]:
    """``_index.lock`` 파일에 대한 advisory exclusive lock 보유.

    portalocker 로 cross-platform exclusive lock — Linux/macOS 는 fcntl.flock,
    Windows 는 msvcrt.locking 으로 자동 분기. ``with _index_lock():`` 블록 안에서
    ``_load_index`` / ``_save_index`` 를 호출하면 read-modify-write 사이클이 안전하다.
    동일 프로세스 내 여러 스레드 / 다른 프로세스 모두 직렬화.
    """
    _ensure_root()
    lock_p = _lock_path()
    # 1) 같은 프로세스 내 스레드 직렬화 (in-process).
    _INDEX_THREAD_LOCK.acquire()
    try:
        # 2) 다른 프로세스 직렬화 (inter-process).
        # 락 파일 자체는 비어있어도 됨 — lock 만 잡고 풀면 끝.
        # 0600 으로 만들기 위해 os.open 으로 fd 확보 후 portalocker 로 lock.
        fd = os.open(str(lock_p), os.O_RDWR | os.O_CREAT, _FILE_MODE)
        try:
            os.chmod(lock_p, _FILE_MODE)
        except OSError:
            pass
        f = os.fdopen(fd, "r+b")
        try:
            portalocker.lock(f, portalocker.LOCK_EX)
            try:
                yield
            finally:
                portalocker.unlock(f)
        finally:
            f.close()
    finally:
        _INDEX_THREAD_LOCK.release()


def _empty_index() -> dict:
    """빈 카탈로그의 기본 형태."""
    return {"version": INDEX_VERSION, "profiles": []}


def _load_index() -> dict:
    """``_index.json`` 을 dict 로 로드. 없으면 빈 카탈로그 반환.

    이 함수 자체는 락을 잡지 않는다 — 호출자가 ``with _index_lock():`` 안에서
    호출해야 read-modify-write 사이클이 안전.
    """
    p = _index_path()
    if not p.exists():
        return _empty_index()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # 카탈로그 파일이 깨졌으면 빈 카탈로그로 fallback. 데이터 손실은 storage
        # 파일이 남아 있으면 사용자가 수동 복구 가능. 로그로만 경고.
        log.warning("[auth-profiles] _index.json load failed — proceeding with empty catalog (%s)", e)
        return _empty_index()
    # 최소 구조 보정 (외부에서 손댄 경우).
    if not isinstance(data, dict):
        return _empty_index()
    data.setdefault("version", INDEX_VERSION)
    data.setdefault("profiles", [])
    if not isinstance(data["profiles"], list):
        data["profiles"] = []
    return data


def _save_index(data: dict) -> None:
    """``_index.json`` 에 dict 를 atomic 하게 저장. 0600 권한 강제.

    이 함수도 자체 락을 잡지 않는다 — 호출자가 ``with _index_lock():`` 안에서
    호출해야 한다. atomic 보장은 tmp + ``os.replace`` 패턴.
    """
    _ensure_root()
    p = _index_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    # 신규 파일 권한을 처음부터 0600 으로 — umask 영향 회피.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        # 부분 쓰기 흔적 제거.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.chmod(tmp, _FILE_MODE)
    os.replace(tmp, p)


def _atomic_update(updater: Callable[[dict], dict]) -> dict:
    """락 보유 상태에서 ``load → updater(data) → save`` 한 번에 수행.

    ``updater`` 는 dict 를 받아 *수정된 dict* 를 반환해야 한다 (in-place 수정도
    허용 — return 만 잊지 말 것). 반환값은 저장된 최종 데이터.

    동시성: 같은 호스트의 여러 프로세스 / 스레드가 모두 본 헬퍼를 통해
    카탈로그를 갱신하면 직렬화된다.
    """
    with _index_lock():
        data = _load_index()
        new_data = updater(data)
        if not isinstance(new_data, dict):
            raise TypeError("_atomic_update: updater 가 dict 를 반환해야 함")
        _save_index(new_data)
        return new_data


# ─────────────────────────────────────────────────────────────────────────
# 권한 점검 헬퍼 (테스트 + 운영 진단용)
# ─────────────────────────────────────────────────────────────────────────

def _file_mode(p: Path) -> int:
    """파일 권한 비트 (S_IMODE)."""
    return stat.S_IMODE(p.stat().st_mode)


# ─────────────────────────────────────────────────────────────────────────
# Dataclass — Fingerprint / Verify / AuthProfile (P1.2)
# ─────────────────────────────────────────────────────────────────────────
#
# 직렬화 규칙:
#   - 모든 dataclass 는 ``to_dict()`` / ``from_dict()`` 쌍을 가진다.
#   - storage_path 는 _index.json 에 *상대 경로 (파일명만)* 로 저장된다. AuthProfile
#     instance 의 ``storage_path`` 는 항상 절대 경로 (Path) — load 시점에
#     ``_root() / filename`` 으로 resolve 한다. 이로 인해 카탈로그가 환경에 종속되지
#     않고 ``AUTH_PROFILES_DIR`` 만 바뀌어도 그대로 작동.
#   - JSON 호환성을 위해 모든 to_dict 는 plain dict (Path / dataclass 없음).

# 시드 시 캡처되는 fingerprint 의 운영 기본값 (D10 — UA 는 capture-only).
_DEFAULT_VIEWPORT_W = 1280
_DEFAULT_VIEWPORT_H = 800
_DEFAULT_LOCALE = "ko-KR"
_DEFAULT_TIMEZONE = "Asia/Seoul"
_DEFAULT_COLOR_SCHEME = "light"
_DEFAULT_PLAYWRIGHT_CHANNEL = "chromium"


@dataclass
class FingerprintProfile:
    """녹화/재생 4단계에서 통일되어야 하는 브라우저 fingerprint (D10).

    UA 는 임의 spoof 하지 않는다 — sec-ch-ua Client Hints 와 어긋나면 *오히려*
    봇 의심을 키우기 때문. 같은 Playwright 버전·채널을 사용하면 UA 는 자연
    일치하므로, 본 프로파일은 UA 를 *capture-only* 로 보관 (informational).
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
        """운영 기본값 — 1280x800 / ko-KR / Asia/Seoul / light / chromium."""
        return cls(
            viewport_width=_DEFAULT_VIEWPORT_W,
            viewport_height=_DEFAULT_VIEWPORT_H,
            locale=_DEFAULT_LOCALE,
            timezone_id=_DEFAULT_TIMEZONE,
        )

    def to_playwright_open_args(self) -> list[str]:
        """``playwright open`` / ``codegen`` CLI 옵션. UA 옵션 미포함 (D10).

        viewport-size 는 콤마 구분 형식 (Playwright CLI 실 형식).
        """
        return [
            "--viewport-size", f"{self.viewport_width},{self.viewport_height}",
            "--lang", self.locale,
            "--timezone", self.timezone_id,
            "--color-scheme", self.color_scheme,
        ]

    def to_browser_context_kwargs(self) -> dict:
        """Playwright Python ``browser.new_context()`` kwargs (재생/verify 용)."""
        return {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
        }

    def to_env(self) -> dict:
        """env var 변환 (replay_proxy → executor 의 컨텍스트 옵션 override)."""
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
        """카탈로그 dict → FingerprintProfile. 누락 키는 기본값."""
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
    """naver-side weak negative check 의 명세 (D13).

    ``kind="login_form_negative"`` — ``selector`` 가 *보이면* 로그아웃 상태로 판정.
    이는 가공 silent-refresh 엔드포인트에 의존하지 않는 안전한 방식이다.
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
    """프로파일 verify 명세 (D13).

    service_url 은 필수. service_text 는 선택 — 값이 있으면 해당 텍스트까지
    확인하는 강한 검증, 비어 있으면 보호 URL 접근 성공만 확인하는 약한 검증.
    naver_probe 는 optional weak — 실패해도 OK 판정에는 영향 없음 (warn-only).
    idp_domain 은 시드 storage 에 *반드시 존재해야 하는* IdP 도메인 (validate_dump).
    네이버 OAuth 가 아닌 다른 IdP (카카오/구글/사내 SSO) 도 지원하기 위한 파라메터.
    None 으로 두면 IdP 검증 자체를 skip — 순수 ID/PW 사이트용.
    하위 호환을 위해 default 는 "naver.com".
    """

    service_url: str
    service_text: str = ""
    naver_probe: Optional[NaverProbeSpec] = None
    idp_domain: Optional[str] = "naver.com"

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "service_url": self.service_url,
            "service_text": self.service_text,
            "idp_domain": self.idp_domain,
        }
        if self.naver_probe is not None:
            d["naver_probe"] = self.naver_probe.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "VerifySpec":
        probe_raw = d.get("naver_probe")
        probe = NaverProbeSpec.from_dict(probe_raw) if isinstance(probe_raw, dict) else None
        # 카탈로그 하위 호환 — idp_domain 키가 없는 v1 entry 는 "naver.com" 으로 fallback.
        if "idp_domain" in d:
            idp_raw = d.get("idp_domain")
            idp = str(idp_raw) if idp_raw else None
        else:
            idp = "naver.com"
        return cls(
            service_url=str(d["service_url"]),
            service_text=str(d.get("service_text") or ""),
            naver_probe=probe,
            idp_domain=idp,
        )


@dataclass
class AuthProfile:
    """카탈로그의 한 항목.

    ``storage_path`` 는 절대 경로 (load 시점에 ``_root()`` 와 합성). 카탈로그
    JSON 에는 *파일명만* 저장된다 — 환경 이식성 확보 (D3).
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
        """카탈로그 직렬화 — storage_path 는 *파일명만* 박는다 (이식성)."""
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
        """카탈로그 dict → AuthProfile.

        ``storage_path`` 는 ``_root() / <filename>`` 로 resolve. 즉 본 메서드
        호출 시 현재 ``AUTH_PROFILES_DIR`` env 가 영향을 준다 — 의도적 동작
        (서로 다른 환경에서 같은 카탈로그 재사용 가능).
        """
        storage_filename = str(d["storage_path"])
        # 안전망 — 카탈로그가 어쩌다 절대 경로를 담고 있으면 파일명만 추출.
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
    """이름으로 lookup 했는데 카탈로그에 없음."""


def list_profiles() -> list["AuthProfile"]:
    """카탈로그의 모든 프로파일 목록. ``name`` 오름차순 정렬.

    카탈로그 파일이 없으면 빈 리스트. 손상된 항목은 silent-skip 하고 경고 로그.
    """
    with _index_lock():
        data = _load_index()

    out: list[AuthProfile] = []
    for raw in data.get("profiles", []):
        if not isinstance(raw, dict):
            log.warning("[auth-profiles] non-dict entry in catalog — skipping")
            continue
        try:
            out.append(AuthProfile.from_dict(raw))
        except (KeyError, TypeError, ValueError) as e:
            # 일부 손상된 항목 때문에 list 전체가 깨지지 않게.
            log.warning(
                "[auth-profiles] 카탈로그 항목 로드 실패 — 스킵: name=%r err=%s",
                raw.get("name"), e,
            )
    out.sort(key=lambda p: p.name)
    return out


def get_profile(name: str) -> "AuthProfile":
    """이름으로 프로파일 조회. 없으면 ``ProfileNotFoundError``.

    이름은 사전에 ``_validate_name`` 으로 검증됨 — path traversal 차단.
    """
    _validate_name(name)
    with _index_lock():
        data = _load_index()
    for raw in data.get("profiles", []):
        if isinstance(raw, dict) and raw.get("name") == name:
            return AuthProfile.from_dict(raw)
    raise ProfileNotFoundError(f"Profile '{name}' not found")


def delete_profile(name: str) -> None:
    """프로파일 삭제 — 카탈로그 항목 제거 + storage 파일 unlink.

    카탈로그에 없으면 ``ProfileNotFoundError``. storage 파일이 이미 없으면
    silent-pass (멱등성). 락 보유 상태에서 둘 다 처리해 race 회피.
    """
    _validate_name(name)

    found_holder = {"hit": False, "filename": ""}

    def updater(d: dict) -> dict:
        kept = []
        for raw in d.get("profiles", []):
            if isinstance(raw, dict) and raw.get("name") == name:
                found_holder["hit"] = True
                # storage 파일명도 카탈로그에서 가져온다 — 외부 변경에 강한 방식.
                found_holder["filename"] = str(raw.get("storage_path") or "")
                continue
            kept.append(raw)
        d["profiles"] = kept
        return d

    # 락 안에서 카탈로그 갱신 → 락 풀린 후 storage 파일 unlink.
    # storage 파일 unlink 가 락 안에 있어도 무방하지만, 디스크 IO 동안 다른
    # 프로세스 차단 시간을 늘릴 이유가 없다.
    _atomic_update(updater)

    if not found_holder["hit"]:
        raise ProfileNotFoundError(f"Profile '{name}' not found")

    storage_filename = found_holder["filename"] or f"{name}{_STORAGE_SUFFIX}"
    # 안전망 — 카탈로그가 어쩌다 절대 경로를 담고 있어도 파일명만 사용.
    storage_basename = Path(storage_filename).name
    storage_p = _root() / storage_basename
    try:
        storage_p.unlink()
        log.info("[auth-profiles] deleted — name=%s storage=%s", name, storage_p)
    except FileNotFoundError:
        # 멱등성 — 파일이 이미 없으면 카탈로그 정리만 한 셈.
        log.info("[auth-profiles] deleted (storage file already missing) — name=%s", name)
    except OSError as e:
        # 카탈로그는 이미 갱신됐는데 파일 unlink 가 실패. 사용자가 수동 정리 필요.
        log.warning(
            "[auth-profiles] storage 파일 unlink 실패 — name=%s storage=%s err=%s",
            name, storage_p, e,
        )


def _upsert_profile(profile: "AuthProfile") -> None:
    """프로파일 카탈로그에 등록 또는 갱신 (re-seed 시 동일 name 덮어쓰기).

    내부용 — P1.7 ``seed_profile`` 가 사용. 외부 호출자는 ``seed_profile`` 통과해야
    fingerprint capture / dump 검증 / verify 가 일관되게 수행됨.
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
# Identity helpers — machine_id / Playwright 버전 / CHIPS gate (P1.4)
# ─────────────────────────────────────────────────────────────────────────
#
# D11 (머신 결속) + D14 (CHIPS 버전 게이트) 의 근간이 되는 호스트 정보 헬퍼.
# 모든 함수는 *side-effect 없는 read-only* 이며 실패 시 빈 문자열 / False 로
# fallback 한다 (호출자가 분기).

_MACHINE_ID_HASH_LEN = 8        # hostname 뒤에 붙는 해시 길이 — UUID 자체 노출 회피.
_CHIPS_MIN_VERSION = (1, 54)    # Playwright partition_key 도입 버전.
_PLAYWRIGHT_VERSION_TIMEOUT_SEC = 10.0
_MACHINE_UUID_TIMEOUT_SEC = 5.0
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _read_machine_uuid_macos() -> str:
    """macOS — ``ioreg`` 로 IOPlatformUUID 추출. 실패 시 빈 문자열."""
    try:
        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=_MACHINE_UUID_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("[auth-profiles] ioreg invocation failed — %s", e)
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "IOPlatformUUID" in line:
            # 형식 예: `    "IOPlatformUUID" = "ABCDEF12-..."`
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"')
    return ""


def _read_machine_uuid_linux() -> str:
    """Linux — ``/etc/machine-id`` 또는 dbus fallback. 둘 다 없으면 빈 문자열."""
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
    """플랫폼별 머신 UUID. 미지원 OS / 추출 실패 시 빈 문자열."""
    if sys.platform == "darwin":
        return _read_machine_uuid_macos()
    if sys.platform.startswith("linux"):
        return _read_machine_uuid_linux()
    return ""


def current_machine_id() -> str:
    """안정적 머신 식별자 (D11). 형식: ``hostname:hash8`` 또는 ``hostname``.

    동일 머신에서 호출 시마다 같은 값. UUID 자체는 노출하지 않고 sha256 의 앞 8자만
    사용 — 카탈로그 / 로그에 박혀도 머신 지문 추정 어렵게.
    """
    hostname = socket.gethostname() or "unknown-host"
    uuid_str = _read_machine_uuid()
    if not uuid_str:
        # 머신 UUID 추출 실패 — hostname 만으로 fallback.
        # 이 경우 동일 hostname 의 다른 머신을 구별 못 함 — 사용자가 hostname
        # 충돌을 안 만든다는 가정 (개인 QA / 단일 머신 시나리오 전제).
        return hostname
    digest = hashlib.sha256(uuid_str.encode("utf-8")).hexdigest()[:_MACHINE_ID_HASH_LEN]
    return f"{hostname}:{digest}"


def current_playwright_version() -> str:
    """``playwright --version`` 의 ``X.Y.Z`` 부분. 실패 시 빈 문자열.

    Playwright CLI 가 PATH 에 없거나 호출 자체가 실패하면 빈 문자열을 반환해
    호출자가 fallback 분기 가능하게 한다.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True,
            text=True,
            timeout=_PLAYWRIGHT_VERSION_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("[auth-profiles] playwright --version invocation failed — %s", e)
        return ""
    if result.returncode != 0:
        return ""
    # 출력 예: "Version 1.57.0"
    m = _VERSION_RE.search(result.stdout or "")
    if not m:
        return ""
    return m.group(0)


def _parse_version(v: str) -> Optional[tuple[int, int, int]]:
    """semver-like 문자열 → ``(major, minor, patch)``. 실패 시 None."""
    m = _VERSION_RE.search(v or "")
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except ValueError:
        return None


def chips_supported_by_runtime() -> bool:
    """현재 PATH 의 Playwright 가 CHIPS (Partitioned 쿠키) 보존 지원? (D14)

    Playwright 1.54+ 부터 ``partition_key`` 가 ``storage_state`` dump 에 포함됨.
    그 미만이면 Partitioned 쿠키가 silent-drop 되므로 시드 단계에서 거절해야
    한다.
    """
    parsed = _parse_version(current_playwright_version())
    if parsed is None:
        return False
    return parsed[:2] >= _CHIPS_MIN_VERSION


# ─────────────────────────────────────────────────────────────────────────
# Dump 검증 / 부수 검사 (P1.5)
# ─────────────────────────────────────────────────────────────────────────
#
# Playwright ``BrowserContext.storage_state(path=...)`` 가 만든 JSON 의 형식:
#
# {
#   "cookies": [
#     {"name": ..., "value": ..., "domain": ".naver.com", "path": "/", ...,
#      "partitionKey": "..."},   // 1.54+ 에서만
#     ...
#   ],
#   "origins": [
#     {"origin": "https://booking.example.com",
#      "localStorage": [{"name": "...", "value": "..."}, ...]},
#     ...
#   ]
# }
#
# sessionStorage 는 Playwright 가 자동 보존하지 않아 (D16 한계) 본 모듈에서
# 별도 캡처 데이터 (P1.7 seed_profile 가 수집) 를 인자로 받아 분석한다.

class EmptyDumpError(AuthProfileError, ValueError):
    """storage dump 가 비어있음 (cookies + origins 모두 0건)."""


class MissingDomainError(AuthProfileError, ValueError):
    """storage dump 에 expected 도메인의 쿠키가 1개도 없음."""

    def __init__(self, missing: list[str]):
        super().__init__(f"storage dump is missing cookies for domains: {missing}")
        self.missing = list(missing)


class ChipsNotSupportedError(AuthProfileError, RuntimeError):
    """현재 Playwright 가 CHIPS (Partitioned) 쿠키 보존을 지원 안 함 (<1.54)."""


# sessionStorage 의심 키 패턴 (Q4 — 정규식 + base64-like 길이 둘 다).
_SESSION_STORAGE_SUSPICIOUS_KEY_RE = re.compile(
    r"(token|auth|session|jwt|bearer|access|refresh|credential)",
    re.IGNORECASE,
)
# base64-like = [A-Za-z0-9_+/=-] 로만 구성된 길이 ≥20 의 문자열.
_BASE64_LIKE_RE = re.compile(r"^[A-Za-z0-9_+/=\-]{20,}$")
# JWT — base64url 세그먼트 2~3 개를 dot 으로 join (header.payload[.signature]).
# header 가 ``eyJ`` (= base64 of ``{"``) 로 시작하는 강한 시그니처.
_JWT_LIKE_RE = re.compile(r"^eyJ[A-Za-z0-9_=\-]+(\.[A-Za-z0-9_=\-]+){1,2}$")


def _load_storage_dump(storage_path: Path) -> dict:
    """storage JSON 로드. 손상 / 부재는 ``EmptyDumpError``."""
    if not storage_path.exists():
        raise EmptyDumpError(f"storage file missing: {storage_path}")
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise EmptyDumpError(f"storage file failed to load: {storage_path} ({e})") from e
    if not isinstance(data, dict):
        raise EmptyDumpError(f"storage file has wrong format (not a dict): {storage_path}")
    return data


def _normalize_domain(d: str) -> str:
    """쿠키 도메인 normalize — 앞 dot 제거 + lowercase. 매칭 비교용."""
    return (d or "").lstrip(".").lower()


def _domain_matches(cookie_domain: str, expected: str) -> bool:
    """쿠키 도메인이 expected 와 같은 도메인 트리에 속하나? (C8).

    매칭 규칙 — 다음 중 하나라도 참이면 True:
        1. cookie_domain == expected                    (자기 자신)
        2. cookie_domain.endsWith('.' + expected)        (cookie 가 expected 의 하위)
        3. expected.endsWith('.' + cookie_domain)        (cookie 가 expected 의 부모 — RFC 6265
                                                         상 부모 도메인 쿠키는 자식 호스트로 전송)

    규칙 3 은 SSO 게이트웨이가 부모 도메인(예: ``.koreaconnect.kr``)에 세션 쿠키를
    발급하고 자식(``portal.koreaconnect.kr``) 페이지에서 그 쿠키로 인증하는 흔한
    패턴을 위해 필요. 빠지면 시드는 성공해도 validate_dump 가 false-fail.

    예:
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
    """D12 — storage dump 가 비어있지 않고 expected 도메인 트리 안의 쿠키를 모두 포함.

    "도메인 트리" 매칭은 ``_domain_matches`` 참조 — expected 의 자기/하위/부모
    어느 한 곳의 쿠키라도 있으면 매칭으로 본다 (SSO 부모 도메인 쿠키 케이스 포함).

    Raises:
        EmptyDumpError: cookies + origins 둘 다 0건
        MissingDomainError: expected 중 하나라도 매칭 쿠키 없음
    """
    data = _load_storage_dump(storage_path)
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    if not cookies and not origins:
        raise EmptyDumpError(f"storage is empty (0 cookies and 0 origins): {storage_path}")

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
    """dump 에 ``partitionKey`` 가 채워진 쿠키가 1개 이상이면 True (D14).

    Playwright 1.54+ 가 dump 한 파일에서만 의미있는 결과 — 그 미만이면 항상
    False (필드 자체가 dump 에 없음).
    """
    try:
        data = _load_storage_dump(storage_path)
    except EmptyDumpError:
        return False
    for c in data.get("cookies", []):
        if not isinstance(c, dict):
            continue
        # Playwright 의 키 — Python API 는 partition_key, JSON dump 는 보통
        # partitionKey. 둘 다 본다 (방어적).
        pkey = c.get("partitionKey") or c.get("partition_key")
        if pkey:
            return True
    return False


def _is_suspicious_session_storage_key(key: str) -> bool:
    """Q4 정책 — 의심 키 이름?"""
    return bool(_SESSION_STORAGE_SUSPICIOUS_KEY_RE.search(key or ""))


def _is_suspicious_session_storage_value(value: str) -> bool:
    """Q4 정책 — 의심 값? base64-like 20자+ 또는 JWT 패턴."""
    if not value:
        return False
    return bool(_BASE64_LIKE_RE.match(value)) or bool(_JWT_LIKE_RE.match(value))


def _entry_is_suspicious(entry: object) -> bool:
    """sessionStorage 한 항목 (``{"name": ..., "value": ...}``) 이 의심스럽나?"""
    if not isinstance(entry, dict):
        return False
    name = entry.get("name", "")
    value = entry.get("value", "")
    if not isinstance(name, str) or not isinstance(value, str):
        return False
    return _is_suspicious_session_storage_key(name) or _is_suspicious_session_storage_value(value)


def _iter_session_storage_entries(session_storage: dict) -> Iterator[object]:
    """``{origin: [entries...]}`` 구조에서 모든 entry 를 평탄화해 yield."""
    for entries in session_storage.values():
        if not isinstance(entries, list):
            continue
        yield from entries


def detect_session_storage_use(session_storage: dict) -> bool:
    """D16 — 캡처된 sessionStorage 데이터에서 인증 의심 키/값 감지 (Q4).

    Args:
        session_storage: ``{"origin": [{"name": "k", "value": "v"}, ...], ...}``
            형식. 시드 시 ``page.evaluate("() => Object.fromEntries(...)")`` 로
            origin 마다 캡처해 모은 dict (P1.7 책임).

    Returns:
        True — 의심 키 이름 OR 의심 값 (base64-like 20자+) 1개라도 발견.
        False — 빈 데이터 또는 의심 항목 없음.

    의심 정책 (Q4 — 정규식 + base64-like 둘 다):
        - 키 이름에 ``token`` / ``auth`` / ``session`` / ``jwt`` / ``bearer`` 등 포함
        - 값이 base64-like (영숫자+/=- 만, 길이 ≥20)
    """
    if not isinstance(session_storage, dict) or not session_storage:
        return False
    return any(_entry_is_suspicious(e) for e in _iter_session_storage_entries(session_storage))


# ─────────────────────────────────────────────────────────────────────────
# verify_profile — service authoritative + naver weak probe (P1.6)
# ─────────────────────────────────────────────────────────────────────────
#
# D9 (재생도 headed) + D10 (fingerprint pinning) + D13 (dual-domain verify) 적용.
#
# 구조:
#   verify_profile (orchestrator)
#     ├─ _verify_service_side  ← authoritative; storage 적용 후 service_url 이동
#     └─ _verify_naver_probe   ← optional weak; storage 적용 후 probe_url 이동
#
# 두 IO 함수는 Playwright 호출을 캡슐화 — 테스트는 monkeypatch 로 바꿔치울 수
# 있고, 실제 Playwright 통합 테스트는 ``test/test_auth_profile_verify_pw.py``
# 수준으로 분리.

_VERIFY_HISTORY_MAX = 20
_VERIFY_NAV_TIMEOUT_MS = 30_000

# 운영 기본은 headed (D9 — fingerprint 안정성). e2e / CI 에서는 env override 로
# headless 강제 가능 — 이건 테스트 affordance 이고, 사용자가 명시적으로 opt-in.
_E2E_HEADLESS_ENV = "AUTH_PROFILE_VERIFY_HEADLESS"
_VERIFY_SLOW_MO_ENV = "AUTH_PROFILE_VERIFY_SLOW_MO_MS"
_VERIFY_HOLD_ENV = "AUTH_PROFILE_VERIFY_HOLD_MS"


def _verify_headless() -> bool:
    """``AUTH_PROFILE_VERIFY_HEADLESS=1`` 면 verify 단계 headless. 운영 기본은 False (D9)."""
    return os.environ.get(_E2E_HEADLESS_ENV, "0") == "1"


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _verify_slow_mo_ms() -> int:
    """headed seed verify 를 사람이 따라볼 수 있게 하는 Playwright slow_mo."""
    return _env_int(_VERIFY_SLOW_MO_ENV, 500)


def _verify_hold_ms() -> int:
    """검증 대상 페이지 도착 후 창을 닫기 전 유지 시간."""
    return _env_int(_VERIFY_HOLD_ENV, 4_000)


def _now_iso() -> str:
    """KST 가까운 ISO 8601 문자열 (timezone-aware)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _storage_alive_cookie_count_for_host(
    storage_path: Path,
    host: str,
) -> tuple[int, int]:
    """storage_state JSON 의 ``host`` 도메인 매칭 쿠키 중 만료되지 않은 개수.

    Returns:
        ``(alive, total)`` — alive 는 ``expires`` 가 -1(=세션 쿠키) 이거나
        현재 시각보다 미래인 쿠키. total 은 host 매칭된 모든 쿠키.

    Playwright 가 컨텍스트 로드 시 만료 쿠키를 자동 폐기하므로, alive==0 이면
    그 도메인의 인증 토큰이 모두 사라진 상태 — 사실상 만료된 storage.

    파일이 없거나 JSON 깨졌으면 ``(0, 0)``.
    """
    if not host:
        return 0, 0
    try:
        data = json.loads(storage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        return 0, 0
    now = time.time()
    alive = 0
    total = 0
    for c in cookies:
        if not isinstance(c, dict):
            continue
        dom = (c.get("domain") or "").lstrip(".").lower()
        h = host.lstrip(".").lower()
        # host 자체이거나 host 의 부모(suffix) 도메인이면 매칭.
        if not (dom == h or h.endswith("." + dom)):
            continue
        total += 1
        exp = c.get("expires", -1)
        # -1 이거나 0 이하면 세션 쿠키 (브라우저 닫히면 사라지지만 storage_state
        # 로드 시점엔 살아있음). expires 가 미래면 살아있음.
        if not isinstance(exp, (int, float)) or exp <= 0 or exp > now:
            alive += 1
    return alive, total


def _body_looks_unauthenticated(body_text: str) -> bool:
    """페이지 본문이 비로그인 상태 신호를 보이는지 휴리스틱 검사.

    "로그아웃" / "Logout" / "Sign out" 가 *없고*, "로그인" / "Login" / "Sign in"
    이 *있으면* 비로그인으로 판정. 두 키워드 모두 있으면 (예: "최근 로그인"
    안내 + 로그아웃 버튼) 로그인 상태로 간주 (보수적).
    """
    if not body_text:
        return False
    has_logout = any(k in body_text for k in ("로그아웃", "Logout", "Sign out", "Sign Out"))
    if has_logout:
        return False
    has_login = any(k in body_text for k in ("로그인", "Login", "Sign in", "Sign In"))
    return has_login


def _check_status_and_host(
    response: object,
    page_url: str,
    service_url: str,
) -> tuple[bool, Optional[str]]:
    """status < 400 + 최종 URL host 가 service_url host 계열인지 검사.

    Returns:
        (ok, fail_reason). ok=True 면 fail_reason=None.
    """
    status = getattr(response, "status", 200) if response is not None else 200
    if status >= 400:
        return False, "service_page_not_reachable"
    expected_host = _domain_from_url(service_url)
    final_host = _domain_from_url(page_url)
    host_ok = (
        not expected_host
        or final_host == expected_host
        or _domain_matches(final_host, expected_host)
    )
    if not host_ok:
        return False, "service_url_redirected"
    return True, None


def _evaluate_service_response(page, response, service_url: str, service_text: str) -> tuple[bool, Optional[str]]:
    """service-side 응답을 검사해 (ok, fail_reason) 반환.

    - service_text 가 있으면 body 에 해당 문구 포함 검사 (authoritative).
    - 비어 있으면 status+host 검사 + body unauth 휴리스틱 (보강).
    """
    if service_text:
        try:
            body_text = page.inner_text("body", timeout=5_000)
        except Exception:
            body_text = ""
        if service_text in body_text:
            return True, None
        return False, "service_text_not_found"

    ok, reason = _check_status_and_host(response, page.url, service_url)
    if not ok:
        return False, reason
    # status+host 통과해도 body 가 비로그인 신호를 내면 뒤집음 (만료 storage 가
    # 보호 페이지를 정상 응답으로 받지만 본문은 로그인 안내인 케이스).
    try:
        body_text = page.inner_text("body", timeout=5_000)
    except Exception:
        body_text = ""
    if _body_looks_unauthenticated(body_text):
        return False, "body_indicates_unauthenticated"
    return True, None


def _verify_service_side(
    storage_path: Path,
    fingerprint: "FingerprintProfile",
    service_url: str,
    service_text: str,
    timeout_sec: int,
    visual_pause: bool = False,
) -> tuple[bool, float, Optional[str]]:
    """service-side authoritative verify (D13).

    storage 를 적용한 새 headed 컨텍스트에서 service_url 로 이동한다.

    - service_text 가 있으면 page text 에 해당 문구가 포함되어야 통과.
    - service_text 가 비어 있으면 HTTP < 400 + 최종 URL host 가 service_url host
      와 같은 계열이면 통과. 보호 페이지 진입 자체를 검증 신호로 쓰는 약한 모드다.

    Returns:
        (ok, elapsed_ms, fail_reason) — fail_reason 은 ok=True 일 때 None.
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
                    ok, fail_reason = _evaluate_service_response(
                        page, response, service_url, service_text,
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
    """naver-side weak probe (D13). best-effort — 실패가 ok 판정 뒤집지 않음.

    ``kind="login_form_negative"``: probe.url 로 이동 후 probe.selector 가 *보이면*
    로그아웃 상태로 판정 (False). 안 보이면 로그인 추정 (True).

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
                        # selector 가 *보이지 않으면* 로그인 상태로 추정.
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
    """``verify_profile`` 결과를 카탈로그에 영속화.

    - profile.last_verified_at = 지금 (성공일 때만)
    - profile.verify_history 에 entry append (cap _VERIFY_HISTORY_MAX)
    - _upsert_profile 로 카탈로그 갱신
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
    # 최신 N 개만 보존 — 카탈로그 비대화 방지.
    if len(history) > _VERIFY_HISTORY_MAX:
        history = history[-_VERIFY_HISTORY_MAX:]
    profile.verify_history = history
    if ok:
        profile.last_verified_at = now_iso
    try:
        _upsert_profile(profile)
    except Exception as e:
        # 카탈로그 갱신 실패는 verify 결과 자체에 영향 주지 않음 — warn 만.
        log.warning("[auth-profiles] failed to update catalog with verify result — %s", e)


def verify_profile(
    profile: "AuthProfile",
    *,
    timeout_sec: int = 30,
    naver_probe: bool = True,
    visual_pause: bool = False,
) -> tuple[bool, dict]:
    """프로파일 검증 (D5, D13).

    service-side 가 *authoritative* — 통과해야 ok=True. naver_probe 는 best-effort
    — 실패해도 ok 에 영향 없음 (detail 에 기록만).

    Args:
        profile: 검증 대상.
        timeout_sec: 단일 단계 navigation timeout (총 시간이 아님 — service +
            optional probe 각각 적용).
        naver_probe: False 면 naver probe 단계 자체를 건너뛴다.

    Returns:
        ``(ok, detail)`` — detail 키:
            - ``service_ms``      : service 검증 elapsed ms
            - ``naver_probe_ms``  : probe 검증 elapsed ms (수행 시)
            - ``naver_ok``        : probe 결과 (수행 시)
            - ``fail_reason``     : ok=False 일 때 사람 읽을 사유
    """
    detail: dict[str, Any] = {
        "service_ms": None,
        "naver_probe_ms": None,
        "naver_ok": None,
    }

    # 사전 검사 — service_url 도메인의 살아있는 쿠키가 0 개면 즉시 실패 처리.
    # 사용자 보고: 핵심 인증 쿠키 (예: piolb) 가 어제 만료되어 Playwright 가
    # storage_state 로드 시 자동 폐기 → 모든 페이지가 비로그인 진입했는데도
    # _verify_service_side 가 status+host 만 보고 통과시켰던 회귀.
    service_host = _domain_from_url(profile.verify.service_url)
    alive, total = _storage_alive_cookie_count_for_host(profile.storage_path, service_host)
    if total > 0 and alive == 0:
        detail["fail_reason"] = "storage_cookies_expired"
        detail["storage_alive_cookies"] = 0
        detail["storage_total_cookies"] = total
        _record_verify(profile, ok=False, detail=detail)
        return (False, detail)

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
            # best-effort — log 에 남기고 계속.
            log.info(
                "[auth-profiles] naver probe 실패 (best-effort, ok 유지) — %s",
                probe_err,
            )

    _record_verify(profile, ok=True, detail=detail)
    return (True, detail)


# ─────────────────────────────────────────────────────────────────────────
# seed_profile — 1회 수동 로그인 라이프사이클 (P1.7)
# ─────────────────────────────────────────────────────────────────────────
#
# 흐름:
#   1. 사전검사 — 이름 sanitize + Playwright 버전 게이트 (D14)
#   2. fingerprint resolve — 기본값 + 런타임 버전 capture (D10)
#   3. service_domain resolve — seed_url 에서 호스트 추출
#   4. ``playwright open <seed_url> --save-storage=<path> + fingerprint args`` 실행
#      (사용자가 직접 로그인 + 2중 확인 통과 → 창 닫음)
#   5. 0600 권한 적용 + dump 검증 (양 도메인 쿠키 존재) — D12
#   6. 임시 AuthProfile 객체 빌드
#   7. verify_profile (service authoritative + naver weak probe) — D13
#   8. 통과 시 카탈로그 등록 (verify 가 _record_verify 통해 자동 수행).
#      실패 시 storage 파일 unlink + 카탈로그 rollback.
#
# 한계 (P1):
#   - sessionStorage detection 은 ``playwright open`` 의 캡처 hook 이 없어
#     수행 불가. ``session_storage_warning=False`` 고정. P1.7 v2 / 별 트랙에서
#     custom 시드 wrapper 로 보강 가능.

class SeedTimeoutError(AuthProfileError, TimeoutError):
    """``playwright open`` 이 ``timeout_sec`` 안에 종료되지 않음 (사용자가 창 미종료)."""


class SeedSubprocessError(AuthProfileError, RuntimeError):
    """``playwright open`` subprocess 가 비정상 종료 또는 실행 자체가 실패."""


class SeedVerifyFailedError(AuthProfileError, RuntimeError):
    """시드 후 ``verify_profile`` 이 실패 — 카탈로그에 등록되지 않음."""


class InvalidServiceDomainError(AuthProfileError, ValueError):
    """seed_url 에서 service_domain 을 추출 못 했고 명시도 안 됨."""


def _domain_from_url(url: str) -> str:
    """URL → hostname (lowercase). 추출 실패 시 빈 문자열."""
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return ""
    return (parsed.hostname or "").lower()


def _capture_runtime_fingerprint(base: "FingerprintProfile") -> "FingerprintProfile":
    """현재 PATH 의 Playwright 버전을 fingerprint 에 채운다.

    UA capture 는 비용이 커서 미루고 (verify 가 실 실행 시 캡처할 여지), 시드
    시점에는 *playwright_version* + *channel* 만 박는다.
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
    """``playwright open`` subprocess 실행 + 사용자 종료 대기.

    Raises:
        SeedTimeoutError: ``timeout_sec`` 안에 종료 안 됨 (사용자가 창 미종료).
        SeedSubprocessError: subprocess 실행 자체 실패 또는 비정상 returncode.
    """
    log.info("[auth-profiles] subprocess running — %s", " ".join(cmd))
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
            f"시드 timeout {timeout_sec}s — 사용자가 창을 닫지 않음"
        ) from e
    except (OSError, subprocess.SubprocessError) as e:
        raise SeedSubprocessError(f"playwright open failed to run: {e}") from e

    # `playwright open` 은 정상 종료 시 returncode=0. 사용자가 강제 종료해도
    # 보통 0. non-zero 면 진짜 에러.
    if result.returncode != 0:
        stderr_tail = (result.stderr or b"").decode("utf-8", errors="replace")[-512:]
        raise SeedSubprocessError(
            f"playwright open 비정상 종료 (rc={result.returncode}): {stderr_tail}"
        )


def _safe_unlink(p: Path) -> None:
    """존재하면 unlink. 실패해도 silent (cleanup 베스트 에포트)."""
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
    """seed 사전검사 + service_domain / fingerprint resolve.

    Raises:
        InvalidProfileNameError: name sanitize 위반.
        ChipsNotSupportedError: Playwright <1.54.
        InvalidServiceDomainError: service_domain 추출 실패.
    """
    _validate_name(name)
    if not chips_supported_by_runtime():
        raise ChipsNotSupportedError(
            "Playwright >=1.54 필요 (CHIPS partition_key 보존). "
            f"현재 버전: {current_playwright_version() or 'unknown'}"
        )
    base_fp = fingerprint if fingerprint is not None else FingerprintProfile.default()
    final_fp = _capture_runtime_fingerprint(base_fp)
    resolved_domain = service_domain or _domain_from_url(seed_url)
    if not resolved_domain:
        raise InvalidServiceDomainError(
            f"service_domain 을 seed_url 에서 추출 불가: {seed_url!r}. "
            "service_domain 인자로 명시하세요."
        )
    return resolved_domain, final_fp


def _do_seed_io(
    seed_url: str,
    storage_path: Path,
    fingerprint: "FingerprintProfile",
    expected_domains: list[str],
    timeout_sec: int,
) -> None:
    """``playwright open`` 실행 + 권한 잠금 + dump 검증.

    실패 시 storage 파일 cleanup. 성공 시 storage_path 가 보장된 상태로 리턴.
    """
    _safe_unlink(storage_path)  # 이전 stale 잔재 정리.
    cmd = [
        sys.executable, "-m", "playwright", "open", seed_url,
        "--save-storage", str(storage_path),
    ]
    cmd += fingerprint.to_playwright_open_args()
    try:
        _run_seed_subprocess(cmd, timeout_sec)
    except (SeedTimeoutError, SeedSubprocessError):
        _safe_unlink(storage_path)
        raise

    # 권한 잠금 — Playwright 가 만든 파일의 umask 영향 회피.
    if storage_path.exists():
        try:
            os.chmod(storage_path, _FILE_MODE)
        except OSError as e:
            log.warning("[auth-profiles] failed to set storage perms 0600 — %s", e)

    # D12 — dump 검증.
    try:
        validate_dump(storage_path, expected_domains)
    except (EmptyDumpError, MissingDomainError):
        _safe_unlink(storage_path)
        raise


def _verify_seeded_profile_or_rollback(profile: "AuthProfile") -> None:
    """시드 후 verify (D13). 실패 시 카탈로그 + storage rollback 후 raise."""
    ok, detail = verify_profile(profile, naver_probe=True, visual_pause=True)
    if ok:
        return
    # _record_verify 가 실패 entry 와 함께 카탈로그에 박았을 수 있음 — rollback.
    try:
        delete_profile(profile.name)
    except ProfileNotFoundError:
        _safe_unlink(profile.storage_path)
    raise SeedVerifyFailedError(
        f"post-seed verify failed: {detail.get('fail_reason') or 'unknown'}"
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
    """1회 수동 로그인 → storageState 저장 → 검증 → 카탈로그 등록 (D2).

    Args:
        name: 프로파일 식별 이름 (sanitize 통과 필수).
        seed_url: ⚠️ *서비스 진입 URL* (네이버 로그인 URL 이 아님 — D6 멘탈 모델).
        verify: service-side authoritative + naver-side optional weak probe 명세.
        service_domain: 명시 안 하면 seed_url 에서 호스트 추출.
        fingerprint: 명시 안 하면 ``FingerprintProfile.default()`` 사용. 어느 경우든
            런타임 Playwright 버전이 capture 되어 박힌다.
        ttl_hint_hours: UI 표시용 만료 추정값. 실제 만료는 verify 가 결정.
        notes: 자유 메모.
        timeout_sec: 사용자 입력 대기 한도. 초과 시 ``SeedTimeoutError``.
        progress_callback: UI polling 용 진행 상태 hook. ``(phase, message)`` 를 받는다.

    Returns:
        등록된 ``AuthProfile``.

    Raises:
        InvalidProfileNameError: name 위반.
        ChipsNotSupportedError: Playwright <1.54 (D14).
        InvalidServiceDomainError: service_domain 추출 실패.
        SeedTimeoutError / SeedSubprocessError: subprocess 단계 실패.
        EmptyDumpError / MissingDomainError: dump 검증 실패 (D12).
        SeedVerifyFailedError: 시드 후 verify 실패 (D13).
    """
    resolved_domain, final_fp = _resolve_seed_inputs(
        name, seed_url, service_domain, fingerprint,
    )

    storage_path = _storage_path(name)
    _ensure_root()

    log.info(
        "[auth-profiles] 시드 시작 — name=%s seed_url=%s service_domain=%s",
        name, seed_url, resolved_domain,
    )

    # IdP 도메인은 VerifySpec.idp_domain 으로 결정 — None 이면 IdP 검증 skip.
    expected_domains: list[str] = []
    if verify.idp_domain:
        expected_domains.append(verify.idp_domain)
    if resolved_domain and resolved_domain not in expected_domains:
        expected_domains.append(resolved_domain)

    if progress_callback is not None:
        progress_callback(
            "login_waiting",
            "Waiting for login window — close the opened browser after you see the post-login screen and the session will be saved.",
        )
    _do_seed_io(seed_url, storage_path, final_fp, expected_domains, timeout_sec)
    if progress_callback is not None:
        progress_callback(
            "verifying",
            "Session saved — opening the verify URL slowly to confirm login state.",
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
        chips_supported=True,                # 게이트 통과 보장
        session_storage_warning=False,       # P1 한계 — playwright open 캡처 hook 부재
        verify_history=[],
        notes=notes,
    )

    _verify_seeded_profile_or_rollback(profile)

    log.info(
        "[auth-profiles] 시드 완료 — name=%s service_domain=%s verify=ok",
        name, resolved_domain,
    )
    return profile
