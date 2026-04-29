"""auth_profiles 모듈 단위 테스트.

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §7.1

본 파일은 P1.1 (디렉토리/스키마 헬퍼 + 이름 sanitize + index 락) 까지의 케이스
만 포함. P1.2~P1.7 케이스는 후속 커밋에서 추가.
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
# fixture — 테스트마다 격리된 ROOT 디렉토리
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch) -> Path:
    """``AUTH_PROFILES_DIR`` env 를 tmp_path 로 가리킨다.

    ``ap._root()`` / ``_index_path()`` 등은 매 호출 env 를 새로 읽으므로
    monkeypatch 가 즉시 반영된다.
    """
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path))
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — 이름 sanitize
# ─────────────────────────────────────────────────────────────────────────

class TestSanitizeName:
    """``_validate_name`` 케이스."""

    @pytest.mark.parametrize("name", [
        "a",                       # 1자
        "abc",
        "booking-via-naver",
        "naver_tester01",
        "A1B2C3",
        "x" * 64,                  # 상한 정확히
    ])
    def test_valid_names_pass(self, name: str):
        """규칙 통과 케이스 — 예외 없이 반환."""
        _validate_name(name)

    @pytest.mark.parametrize("name", [
        "",                        # 빈 문자열
        "-foo",                    # 첫 글자 -
        "_foo",                    # 첫 글자 _
        ".foo",                    # 첫 글자 .
        "../escape",               # path traversal
        "a/b",                     # path 구분자
        "a\\b",                    # Windows path 구분자
        "a.b",                     # dot
        "a b",                     # space
        "a;rm",                    # shell metachar
        "a$b",
        "한글이름",                  # non-ASCII
        "naver🚀",                  # emoji
        "x" * 65,                  # 상한 초과
    ])
    def test_invalid_names_raise(self, name: str):
        """규칙 위반 → InvalidProfileNameError."""
        with pytest.raises(InvalidProfileNameError):
            _validate_name(name)

    def test_non_string_raises(self):
        """문자열 아닌 타입도 거절 (defensive)."""
        with pytest.raises(InvalidProfileNameError):
            _validate_name(None)  # type: ignore[arg-type]
        with pytest.raises(InvalidProfileNameError):
            _validate_name(123)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — 루트 디렉토리 + 권한
# ─────────────────────────────────────────────────────────────────────────

class TestRoot:
    """``_root`` / ``_ensure_root`` / 권한 비트."""

    def test_env_override(self, isolated_root: Path):
        """AUTH_PROFILES_DIR env 가 즉시 반영된다."""
        assert _root() == isolated_root

    def test_default_when_env_unset(self, monkeypatch):
        """env 미설정 시 ~/ttc-allinone-data/auth-profiles 로 fallback."""
        monkeypatch.delenv("AUTH_PROFILES_DIR", raising=False)
        root = _root()
        assert root.name == "auth-profiles"
        assert root.parent.name == "ttc-allinone-data"

    def test_ensure_root_creates_with_0700(self, tmp_path: Path, monkeypatch):
        """루트 신규 생성 시 권한 0700."""
        new_root = tmp_path / "fresh"
        monkeypatch.setenv("AUTH_PROFILES_DIR", str(new_root))
        assert not new_root.exists()
        _ensure_root()
        assert new_root.is_dir()
        assert _file_mode(new_root) == 0o700

    def test_ensure_root_idempotent(self, isolated_root: Path):
        """두 번 호출해도 권한 유지."""
        _ensure_root()
        _ensure_root()
        assert _file_mode(isolated_root) == 0o700


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — Index 로드/저장 (단일 스레드)
# ─────────────────────────────────────────────────────────────────────────

class TestIndexRoundTrip:
    """``_load_index`` / ``_save_index`` round-trip."""

    def test_load_empty_when_no_file(self, isolated_root: Path):
        """카탈로그 파일이 없으면 기본값 반환."""
        data = _load_index()
        assert data == {"version": INDEX_VERSION, "profiles": []}

    def test_save_then_load_roundtrip(self, isolated_root: Path):
        """저장 → 로드 시 동일 dict."""
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
        """카탈로그 파일 권한이 0600."""
        with _index_lock():
            _save_index({"version": INDEX_VERSION, "profiles": []})
        assert _file_mode(_index_path()) == 0o600

    def test_corrupt_file_falls_back_to_empty(self, isolated_root: Path):
        """손상된 JSON 이면 경고만 로그하고 빈 카탈로그 반환."""
        _ensure_root()
        _index_path().write_text("not json {", encoding="utf-8")
        data = _load_index()
        assert data == {"version": INDEX_VERSION, "profiles": []}

    def test_load_missing_keys_filled(self, isolated_root: Path):
        """version/profiles 가 없는 dict 도 안전하게 보정."""
        _ensure_root()
        _index_path().write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        data = _load_index()
        assert data["version"] == INDEX_VERSION
        assert data["profiles"] == []
        assert data["foo"] == "bar"  # 기존 키는 보존


# ─────────────────────────────────────────────────────────────────────────
# P1.1 — Atomic update + 동시성
# ─────────────────────────────────────────────────────────────────────────

class TestAtomicUpdate:
    """``_atomic_update`` 의 동시성 / 직렬화."""

    def test_basic_update(self, isolated_root: Path):
        """updater 가 반환한 dict 가 저장된다."""
        def add_profile(d: dict) -> dict:
            d["profiles"].append({"name": "alpha"})
            return d

        result = _atomic_update(add_profile)
        assert result["profiles"] == [{"name": "alpha"}]
        # 다음 load 가 같은 데이터 보임.
        assert _load_index()["profiles"] == [{"name": "alpha"}]

    def test_updater_must_return_dict(self, isolated_root: Path):
        """updater 가 None 반환 시 TypeError."""
        with pytest.raises(TypeError):
            _atomic_update(lambda d: None)  # type: ignore[arg-type,return-value]

    def test_concurrent_updates_serialized(self, isolated_root: Path):
        """여러 스레드가 _atomic_update 를 동시 호출해도 카탈로그가 깨지지 않는다.

        스레드 N개가 각자 1개씩 프로파일을 추가 → 최종 N개 모두 보존.
        flock 으로 직렬화되므로 lost-update 가 없어야 함.
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
# P1.1 — 경로 헬퍼
# ─────────────────────────────────────────────────────────────────────────

class TestPathHelpers:
    """``_index_path`` / ``_storage_path`` 의 단순 매핑."""

    def test_index_path_under_root(self, isolated_root: Path):
        assert _index_path() == isolated_root / "_index.json"

    def test_storage_path_format(self, isolated_root: Path):
        assert _storage_path("naver-tester01") == (
            isolated_root / "naver-tester01.storage.json"
        )

    def test_storage_path_does_not_validate(self, isolated_root: Path):
        """``_storage_path`` 자체는 sanitize 안 함 — 호출자가 _validate_name 선행 책임.

        이 테스트는 *현재 동작* 을 못박는 회귀 — 사용처에서 항상 _validate_name 을
        먼저 호출해야 한다는 계약을 명시.
        """
        # path traversal 같은 위험한 입력도 막지 않는다 (계약상 사전 검증 필요).
        # 실제 사용처는 호출 전 _validate_name 으로 거른다.
        result = _storage_path("anything-not-validated")
        assert result.parent == isolated_root


# ─────────────────────────────────────────────────────────────────────────
# P1.2 — Dataclass 직렬화
# ─────────────────────────────────────────────────────────────────────────

class TestFingerprintProfile:
    """``FingerprintProfile`` round-trip + CLI/env 변환."""

    def test_default_values(self):
        """``default()`` 가 운영 기본값을 반환."""
        fp = FingerprintProfile.default()
        assert fp.viewport_width == 1280
        assert fp.viewport_height == 800
        assert fp.locale == "ko-KR"
        assert fp.timezone_id == "Asia/Seoul"
        assert fp.color_scheme == "light"
        assert fp.playwright_channel == "chromium"

    def test_to_playwright_open_args_no_user_agent(self):
        """D10 — UA 옵션이 들어가면 안 된다."""
        fp = FingerprintProfile.default()
        args = fp.to_playwright_open_args()
        # UA 관련 플래그 미존재
        assert "--user-agent" not in args
        # viewport-size 콤마 구분
        assert "--viewport-size" in args
        idx = args.index("--viewport-size")
        assert args[idx + 1] == "1280,800"
        # 나머지 플래그 존재
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
        """env var 는 'WxH' 형식 (executor 가 split('x') 으로 파싱)."""
        fp = FingerprintProfile.default()
        env = fp.to_env()
        assert env["PLAYWRIGHT_VIEWPORT"] == "1280x800"
        assert env["PLAYWRIGHT_LOCALE"] == "ko-KR"
        assert env["PLAYWRIGHT_TIMEZONE"] == "Asia/Seoul"
        assert env["PLAYWRIGHT_COLOR_SCHEME"] == "light"

    def test_roundtrip_full(self):
        """모든 필드가 채워진 round-trip."""
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
        """누락된 키는 기본값으로 채워진다."""
        fp = FingerprintProfile.from_dict({})
        assert fp == FingerprintProfile.default()

    def test_from_dict_partial_viewport(self):
        """viewport dict 가 빠져도 기본 1280x800."""
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
    """``VerifySpec`` round-trip + naver_probe optional."""

    def test_service_only_roundtrip(self):
        """naver_probe 없이도 round-trip."""
        original = VerifySpec(
            service_url="https://booking.example.com/mypage",
            service_text="김QA님 환영합니다",
        )
        d = original.to_dict()
        # naver_probe 키가 직렬화에서 빠져있어야 함 (None 인 경우).
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
        """naver_probe 가 dict 가 아니면 None 으로 처리 (방어적)."""
        d = {
            "service_url": "https://booking.example.com/mypage",
            "service_text": "김QA님 환영합니다",
            "naver_probe": "not-a-dict",
        }
        loaded = VerifySpec.from_dict(d)
        assert loaded.naver_probe is None

    def test_service_text_optional(self):
        """service_text 누락은 URL 접근만 확인하는 약한 검증 모드로 로드된다."""
        loaded = VerifySpec.from_dict({"service_url": "https://example.com/mypage"})
        assert loaded.service_url == "https://example.com/mypage"
        assert loaded.service_text == ""

    def test_service_text_none_loads_as_empty(self):
        """기존/외부 카탈로그가 null 을 넣어도 약검증 모드로 처리한다."""
        loaded = VerifySpec.from_dict({
            "service_url": "https://example.com/mypage",
            "service_text": None,
        })
        assert loaded.service_text == ""

    def test_service_url_required(self):
        """service_url 은 여전히 필수."""
        with pytest.raises(KeyError):
            VerifySpec.from_dict({"service_text": "x"})


class TestAuthProfile:
    """``AuthProfile`` round-trip + storage_path 이식성."""

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
            notes="첫 시드",
        )

    def test_roundtrip(self, isolated_root: Path):
        original = self._make_profile(isolated_root)
        loaded = AuthProfile.from_dict(original.to_dict())
        assert loaded == original

    def test_storage_path_serialized_as_filename_only(self, isolated_root: Path):
        """storage_path 는 카탈로그에서 *파일명만* 저장 (이식성, D3)."""
        prof = self._make_profile(isolated_root)
        d = prof.to_dict()
        assert d["storage_path"] == "booking-via-naver.storage.json"
        # 절대 경로가 박혀있으면 안 됨.
        assert "/" not in d["storage_path"]

    def test_storage_path_resolved_with_current_root(self, tmp_path: Path, monkeypatch):
        """from_dict 시점의 AUTH_PROFILES_DIR 로 storage_path 가 resolve 된다.

        시나리오: 카탈로그를 머신 A 에서 생성 → 머신 B 로 동기화 후 다른
        AUTH_PROFILES_DIR 을 가리키면, storage_path 가 새 경로로 자동 매핑.
        """
        # A 환경에서 생성.
        env_a = tmp_path / "a"
        monkeypatch.setenv("AUTH_PROFILES_DIR", str(env_a))
        prof = self._make_profile(env_a)
        d = prof.to_dict()

        # B 환경으로 전환 후 load.
        env_b = tmp_path / "b"
        monkeypatch.setenv("AUTH_PROFILES_DIR", str(env_b))
        loaded = AuthProfile.from_dict(d)
        # storage_path 가 B 환경의 root 아래로 resolve.
        assert loaded.storage_path == env_b / "booking-via-naver.storage.json"

    def test_from_dict_strips_absolute_path(self, isolated_root: Path):
        """카탈로그에 절대 경로가 박혀있어도 파일명만 추출해 root 와 합성 (방어적)."""
        prof = self._make_profile(isolated_root)
        d = prof.to_dict()
        # 카탈로그가 어쩌다 절대 경로를 담고 있는 상황 시뮬레이션.
        d["storage_path"] = "/some/other/path/booking-via-naver.storage.json"
        loaded = AuthProfile.from_dict(d)
        assert loaded.storage_path == isolated_root / "booking-via-naver.storage.json"

    def test_partial_dict_missing_optional_fields(self, isolated_root: Path):
        """선택 필드들이 누락된 dict 도 안전하게 로드."""
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
    """테스트용 프로파일 팩토리."""
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
    """``list_profiles`` 케이스."""

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
        """손상된 한 항목 때문에 리스트 전체가 깨지지 않는다."""
        _upsert_profile(_build_profile("alpha", isolated_root))
        # 카탈로그에 깨진 항목 직접 주입.
        with _index_lock():
            data = _load_index()
            data["profiles"].append({"name": "broken"})  # verify 키 누락
            _save_index(data)
        # alpha 만 보존되고 broken 은 스킵.
        result = list_profiles()
        assert [p.name for p in result] == ["alpha"]


class TestGet:
    """``get_profile`` 케이스."""

    def test_existing_profile(self, isolated_root: Path):
        _upsert_profile(_build_profile("alpha", isolated_root))
        prof = get_profile("alpha")
        assert prof.name == "alpha"
        assert prof.service_domain == "alpha.example.com"

    def test_missing_raises(self, isolated_root: Path):
        with pytest.raises(ProfileNotFoundError):
            get_profile("ghost")

    def test_invalid_name_raises_before_lookup(self, isolated_root: Path):
        """sanitize 위반은 즉시 InvalidProfileNameError (path traversal 차단)."""
        with pytest.raises(InvalidProfileNameError):
            get_profile("../../etc/passwd")

    def test_returns_independent_instance(self, isolated_root: Path):
        """두 번 get 하면 동등한 별도 인스턴스 반환 (mutation 격리)."""
        _upsert_profile(_build_profile("alpha", isolated_root))
        a = get_profile("alpha")
        b = get_profile("alpha")
        assert a == b
        a.notes = "mutated"
        assert b.notes != "mutated"


class TestDelete:
    """``delete_profile`` 케이스."""

    def test_removes_from_index_and_storage_file(self, isolated_root: Path):
        prof = _build_profile("alpha", isolated_root)
        _upsert_profile(prof)
        # 가짜 storage 파일 생성 (실제 시드 없이도 unlink 동작 확인).
        prof.storage_path.write_text("{}", encoding="utf-8")
        assert prof.storage_path.exists()

        delete_profile("alpha")

        assert list_profiles() == []
        assert not prof.storage_path.exists()

    def test_idempotent_when_storage_file_missing(self, isolated_root: Path):
        """카탈로그 항목만 있고 storage 파일이 이미 없는 경우도 정상 처리."""
        _upsert_profile(_build_profile("alpha", isolated_root))
        # storage 파일은 만들지 않음.
        delete_profile("alpha")
        assert list_profiles() == []

    def test_missing_raises(self, isolated_root: Path):
        with pytest.raises(ProfileNotFoundError):
            delete_profile("ghost")

    def test_invalid_name_raises_before_lookup(self, isolated_root: Path):
        with pytest.raises(InvalidProfileNameError):
            delete_profile("../escape")

    def test_does_not_affect_others(self, isolated_root: Path):
        """다른 프로파일은 영향 받지 않는다."""
        for n in ["alpha", "bravo", "charlie"]:
            _upsert_profile(_build_profile(n, isolated_root))
        delete_profile("bravo")
        assert [p.name for p in list_profiles()] == ["alpha", "charlie"]


class TestUpsert:
    """``_upsert_profile`` — re-seed 시 같은 이름 덮어쓰기."""

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
    """``current_machine_id`` 안정성 + 형식."""

    def test_returns_non_empty_string(self):
        mid = current_machine_id()
        assert isinstance(mid, str)
        assert mid

    def test_stable_across_calls(self):
        """같은 프로세스에서 두 번 호출 시 동일 값."""
        assert current_machine_id() == current_machine_id()

    def test_starts_with_hostname(self):
        """식별자 앞부분에 hostname 이 들어간다."""
        import socket as _socket
        hostname = _socket.gethostname() or "unknown-host"
        assert current_machine_id().startswith(hostname)

    def test_format_with_uuid_hash(self, monkeypatch):
        """UUID 추출 성공 시 hostname:hash8 형식."""
        monkeypatch.setattr(ap, "_read_machine_uuid", lambda: "AAAA-BBBB-CCCC")
        mid = current_machine_id()
        # ":" 뒤 정확히 8자.
        assert ":" in mid
        suffix = mid.rsplit(":", 1)[1]
        assert len(suffix) == 8
        # 16진 문자열.
        assert re.match(r"^[0-9a-f]{8}$", suffix)

    def test_format_without_uuid(self, monkeypatch):
        """UUID 추출 실패 시 hostname 만 (콜론 없음)."""
        monkeypatch.setattr(ap, "_read_machine_uuid", lambda: "")
        mid = current_machine_id()
        assert ":" not in mid

    def test_uuid_not_exposed_directly(self, monkeypatch):
        """원본 UUID 가 식별자에 그대로 박히면 안 된다 (해시만)."""
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
        """실제 호출 — Playwright 가 PATH 에 있으면 'X.Y.Z' 형태, 없으면 빈 문자열."""
        v = current_playwright_version()
        if v:
            assert _VERSION_RE.match(v) or _VERSION_RE.search(v)
        else:
            assert v == ""

    def test_version_via_subprocess_mock(self, monkeypatch):
        """subprocess 모킹으로 호출 경로 확인."""
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
        """subprocess 가 OSError 던지면 빈 문자열."""
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
        """Playwright 1.54.0 → CHIPS 지원."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.54.0")
        assert chips_supported_by_runtime() is True

    def test_chips_supported_at_157(self, monkeypatch):
        """Playwright 1.57.0 → CHIPS 지원."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.57.0")
        assert chips_supported_by_runtime() is True

    def test_chips_not_supported_below_154(self, monkeypatch):
        """Playwright 1.53.x → CHIPS 미지원."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.53.5")
        assert chips_supported_by_runtime() is False

    def test_chips_not_supported_at_150(self, monkeypatch):
        """Playwright 1.50.0 → CHIPS 미지원."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "1.50.0")
        assert chips_supported_by_runtime() is False

    def test_chips_not_supported_when_version_missing(self, monkeypatch):
        """Playwright CLI 미설치 (빈 문자열) → 보수적으로 False."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "")
        assert chips_supported_by_runtime() is False

    def test_chips_supported_at_2_0_0(self, monkeypatch):
        """Playwright 2.0.0 (가상) → CHIPS 지원."""
        monkeypatch.setattr(ap, "current_playwright_version", lambda: "2.0.0")
        assert chips_supported_by_runtime() is True


# ─────────────────────────────────────────────────────────────────────────
# P1.5 — Dump 검증 / Partitioned / sessionStorage detection
# ─────────────────────────────────────────────────────────────────────────

def _write_dump(path: Path, data: dict) -> None:
    """테스트 헬퍼 — dump JSON 작성."""
    path.write_text(json.dumps(data), encoding="utf-8")


class TestDomainMatch:
    """``_domain_matches`` — 같은 도메인 트리(자기/하위/부모) 매칭."""

    @pytest.mark.parametrize("cookie_domain,expected,result", [
        # 기본 매칭
        (".naver.com", "naver.com", True),
        ("naver.com", "naver.com", True),
        ("accounts.naver.com", "naver.com", True),
        ("nid.naver.com", "naver.com", True),
        # 대소문자 무시
        ("NAVER.COM", "naver.com", True),
        # 부분 매칭은 거부 (다른 도메인 트리)
        ("evilnaver.com", "naver.com", False),
        ("naver.com.evil.com", "naver.com", False),
        # 부모 도메인 쿠키 → 자식 호스트 매칭 (RFC 6265: 부모 cookie 는 자식으로 전송).
        # SSO 게이트웨이가 부모 도메인에 세션 발급하는 패턴 지원.
        ("naver.com", "api.naver.com", True),
        ("koreaconnect.kr", "portal.koreaconnect.kr", True),
        (".koreaconnect.kr", "portal.koreaconnect.kr", True),
        # 빈 입력 방어
        ("", "naver.com", False),
        ("naver.com", "", False),
        # subdomain 비교
        (".booking.example.com", "booking.example.com", True),
        ("booking.example.com", "example.com", True),
        ("not-booking.example.com", "booking.example.com", False),
    ])
    def test_match_cases(self, cookie_domain, expected, result):
        assert _domain_matches(cookie_domain, expected) is result


class TestValidateDump:
    """``validate_dump`` 케이스."""

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(EmptyDumpError):
            validate_dump(tmp_path / "missing.json", ["naver.com"])

    def test_corrupt_json_raises(self, tmp_path: Path):
        p = tmp_path / "corrupt.json"
        p.write_text("not json {", encoding="utf-8")
        with pytest.raises(EmptyDumpError):
            validate_dump(p, ["naver.com"])

    def test_empty_dump_raises(self, tmp_path: Path):
        """cookies + origins 모두 비어있으면 EmptyDumpError."""
        p = tmp_path / "empty.json"
        _write_dump(p, {"cookies": [], "origins": []})
        with pytest.raises(EmptyDumpError):
            validate_dump(p, ["naver.com"])

    def test_only_origins_present_passes_empty_check(self, tmp_path: Path):
        """cookies 는 0건이지만 origins 는 있으면 빈 dump 가 아니다 (도메인 검사 별개)."""
        p = tmp_path / "origins-only.json"
        _write_dump(p, {
            "cookies": [],
            "origins": [{"origin": "https://x.example.com", "localStorage": []}],
        })
        # 도메인 매칭이 없으니 MissingDomainError. 단 EmptyDumpError 는 아님.
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
        # 예외 없이 통과.
        validate_dump(p, ["naver.com", "booking.example.com"])

    def test_one_domain_missing(self, tmp_path: Path):
        p = tmp_path / "missing-one.json"
        _write_dump(p, {
            "cookies": [
                {"name": "NID_AUT", "domain": ".naver.com", "value": "a"},
                # booking 쿠키 없음
            ],
            "origins": [],
        })
        with pytest.raises(MissingDomainError) as excinfo:
            validate_dump(p, ["naver.com", "booking.example.com"])
        assert excinfo.value.missing == ["booking.example.com"]

    def test_subdomain_match_counts(self, tmp_path: Path):
        """expected="naver.com" 일 때 cookie="accounts.naver.com" 매칭됨."""
        p = tmp_path / "subdomain.json"
        _write_dump(p, {
            "cookies": [
                {"name": "x", "domain": "accounts.naver.com", "value": "v"},
            ],
            "origins": [],
        })
        validate_dump(p, ["naver.com"])

    def test_parent_domain_cookie_counts_for_sso(self, tmp_path: Path):
        """SSO 회귀 — 부모 도메인 쿠키(``.koreaconnect.kr``)가 자식 호스트
        (``portal.koreaconnect.kr``) expected 에 매칭되어야 한다.

        실패 사례 (2026-04-29): SSO 게이트웨이가 ``.koreaconnect.kr`` 에 세션
        발급하는데 expected="portal.koreaconnect.kr" 로 미스매칭 → false-fail.
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
        """expected_domains 에 빈 문자열이 끼어있으면 건너뛴다 (방어적)."""
        p = tmp_path / "ok.json"
        _write_dump(p, {
            "cookies": [{"name": "x", "domain": "naver.com", "value": "v"}],
            "origins": [],
        })
        # "" 가 끼어도 누락 처리 안 함.
        validate_dump(p, ["naver.com", ""])


class TestHasPartitionedCookies:
    """``has_partitioned_cookies`` 케이스 (D14)."""

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
        """JSON dump 의 표준 키 'partitionKey' 매칭."""
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
        """Python API 스타일 키 'partition_key' 도 인식 (방어적)."""
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
        """``partitionKey`` 가 빈 문자열이면 미설정과 같다."""
        p = tmp_path / "empty-pkey.json"
        _write_dump(p, {
            "cookies": [
                {"name": "a", "domain": ".naver.com", "value": "1", "partitionKey": ""},
            ],
            "origins": [],
        })
        assert has_partitioned_cookies(p) is False

    def test_missing_file_returns_false(self, tmp_path: Path):
        """파일 없음 → False (예외 안 던짐 — soft check)."""
        assert has_partitioned_cookies(tmp_path / "missing.json") is False


class TestDetectSessionStorageUse:
    """``detect_session_storage_use`` 케이스 (D16, Q4)."""

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
        """JWT 같은 base64-like 긴 값."""
        data = {
            "https://example.com": [
                {"name": "harmless_name", "value": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ"},
            ],
        }
        assert detect_session_storage_use(data) is True

    def test_short_value_not_flagged(self):
        """짧은 값 (<20자) 은 base64-like 라도 의심 안 함."""
        data = {
            "https://example.com": [
                {"name": "harmless", "value": "short123"},
            ],
        }
        assert detect_session_storage_use(data) is False

    def test_value_with_spaces_not_base64(self):
        """공백 포함 값은 base64 패턴 안 매치."""
        data = {
            "https://example.com": [
                {"name": "harmless", "value": "this is a long human readable sentence"},
            ],
        }
        assert detect_session_storage_use(data) is False

    def test_multi_origin(self):
        """여러 origin 중 하나라도 의심 항목 있으면 True."""
        data = {
            "https://safe.com": [{"name": "theme", "value": "dark"}],
            "https://app.com": [{"name": "auth_token", "value": "x"}],
        }
        assert detect_session_storage_use(data) is True

    def test_malformed_entries_skipped(self):
        """list 가 아닌 값 / dict 아닌 entry 는 silent-skip."""
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
# 실제 Playwright 호출은 ``_verify_service_side`` / ``_verify_naver_probe`` 에
# 캡슐화되어 있어 monkeypatch 로 대체. 본 클래스는 *오케스트레이션 + 결과 기록*
# 로직만 검증.

class _StubVerify:
    """``_verify_service_side`` / ``_verify_naver_probe`` 의 stub 결과 컨테이너."""

    def __init__(self, ok: bool, elapsed_ms: float, fail_reason: Optional[str] = None):
        self.ok = ok
        self.elapsed_ms = elapsed_ms
        self.fail_reason = fail_reason
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return (self.ok, self.elapsed_ms, self.fail_reason)


class TestVerifyProfileOrchestrator:
    """``verify_profile`` 의 service+probe 결합 로직 + 결과 영속화."""

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
        """probe 는 best-effort — 실패해도 ok=True."""
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
        # ok=True 일 때 fail_reason 은 detail 에 안 박힘.
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
        # service 실패 시 probe 는 호출하지 않는다 (시간 절약).
        assert probe_stub.calls == 0
        assert detail["naver_ok"] is None

    def test_naver_probe_disabled(self, isolated_root: Path, monkeypatch):
        """naver_probe=False 면 probe 호출 안 함."""
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
        """프로파일 자체에 naver_probe 가 None 이면 probe 호출 안 함."""
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
    """``_record_verify`` — 결과 영속화 + history cap."""

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
        prof.last_verified_at = "2026-04-28T10:00:00+09:00"  # 이전 성공 기록
        _upsert_profile(prof)
        _record_verify(prof, ok=False, detail={
            "service_ms": 100, "fail_reason": "service_text_not_found",
        })

        loaded = get_profile("alpha")
        # 실패는 last_verified_at 갱신 안 함 (이전 값 유지).
        assert loaded.last_verified_at == "2026-04-28T10:00:00+09:00"
        assert len(loaded.verify_history) == 1
        assert loaded.verify_history[0]["ok"] is False
        assert loaded.verify_history[0]["fail_reason"] == "service_text_not_found"

    def test_history_capped(self, isolated_root: Path):
        """``_VERIFY_HISTORY_MAX`` 초과분은 오래된 것부터 drop."""
        prof = self._profile(isolated_root)
        _upsert_profile(prof)

        # cap + 5 회 호출 — 마지막 cap 개만 남아야 함.
        total = _VERIFY_HISTORY_MAX + 5
        for i in range(total):
            _record_verify(prof, ok=True, detail={"service_ms": i})

        loaded = get_profile("alpha")
        assert len(loaded.verify_history) == _VERIFY_HISTORY_MAX
        # 가장 오래된 것 (i < 5) 이 drop, 마지막 _VERIFY_HISTORY_MAX 개만 남음.
        first_kept = total - _VERIFY_HISTORY_MAX
        assert loaded.verify_history[0]["service_ms"] == first_kept
        assert loaded.verify_history[-1]["service_ms"] == total - 1


# ─────────────────────────────────────────────────────────────────────────
# P1.7 — seed_profile 라이프사이클
# ─────────────────────────────────────────────────────────────────────────
#
# 실 ``playwright open`` 호출과 verify Playwright 호출은 모두 모킹. 본 클래스는
# 오케스트레이션 + 실패 경로 cleanup 만 검증.

def _make_dump_with_domains(domains: list[str]) -> dict:
    """주어진 도메인 각각에 쿠키 1개씩 가진 합성 storage dump."""
    return {
        "cookies": [
            {"name": f"c_{i}", "value": "v", "domain": d, "path": "/"}
            for i, d in enumerate(domains)
        ],
        "origins": [],
    }


def _seed_subprocess_writes(storage_path: Path, dump: dict):
    """``playwright open`` 호출만 가로채 storage 파일을 쓴 척하는 stub factory.

    ``current_machine_id`` 등의 다른 subprocess 호출 (``ioreg``) 은 그대로
    passthrough — 글로벌 monkeypatch 가 모든 호출을 가로채면 부작용이 크다.
    """
    real_run = subprocess.run

    class _FakeResult:
        returncode = 0
        stderr = b""

    def _stub(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        # playwright 호출만 가로채기.
        if isinstance(cmd, list) and cmd and cmd[0] == "playwright":
            if "--save-storage" in cmd:
                idx = cmd.index("--save-storage")
                target = Path(cmd[idx + 1])
                target.write_text(json.dumps(dump), encoding="utf-8")
            return _FakeResult()
        return real_run(*args, **kwargs)

    return _stub


class TestSeedHelpers:
    """seed 의 헬퍼들 — _domain_from_url 등."""

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
    """``seed_profile`` 의 정상 / 실패 경로."""

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
        """정상 흐름 — chips OK, subprocess 정상 종료, dump valid, verify 통과."""
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
        assert prof.session_storage_warning is False  # P1 한계
        # 카탈로그에 등록되었는지.
        assert get_profile("alpha").name == "alpha"
        # storage 파일 권한 0600.
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
        """seed_url 에서 hostname 추출 안 되고 명시도 없음."""
        self._patch_chips_ok(monkeypatch)
        with pytest.raises(InvalidServiceDomainError):
            seed_profile("alpha", "not-a-url", self._verify_spec())

    def test_explicit_service_domain_overrides(self, isolated_root: Path, monkeypatch):
        """service_domain 이 명시되면 seed_url 에서 추출 안 함."""
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
        """playwright open 이 timeout 안에 종료 안 되면 SeedTimeoutError."""
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
        # storage 파일 안 만들어짐 (timeout 전).
        assert not _storage_path("alpha").exists()

    def test_subprocess_nonzero_exit(self, isolated_root: Path, monkeypatch):
        """subprocess 가 실패 returncode 면 SeedSubprocessError."""
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
        """사용자가 로그인 없이 창만 닫으면 dump 가 비어 EmptyDumpError + 파일 cleanup."""
        self._patch_chips_ok(monkeypatch)
        storage_target = _storage_path("alpha")
        monkeypatch.setattr(
            ap.subprocess, "run",
            _seed_subprocess_writes(storage_target, {"cookies": [], "origins": []}),
        )

        with pytest.raises(EmptyDumpError):
            seed_profile("alpha", "https://booking.example.com/", self._verify_spec())
        # cleanup 됨.
        assert not storage_target.exists()
        # 카탈로그에도 등록 안 됨.
        assert list_profiles() == []

    def test_missing_naver_domain_cleanup(self, isolated_root: Path, monkeypatch):
        """naver.com 쿠키 누락 — 사용자가 다른 OAuth (구글 등) 로 로그인했거나 OAuth 자체 미완료."""
        self._patch_chips_ok(monkeypatch)
        # service 도메인만 있고 naver.com 없음.
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
        """dump 는 통과했지만 verify 실패 — 카탈로그 + storage 모두 rollback."""
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
        # rollback 검증.
        assert not storage_target.exists()
        assert list_profiles() == []

    def test_reseed_overwrites(self, isolated_root: Path, monkeypatch):
        """같은 name 으로 재시드 — 기존 storage stale 정리 + 카탈로그 갱신."""
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

        # 재시드 — 다른 dump.
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
        # 카탈로그에는 한 번만 등록되어야 함 (덮어쓰기).
        assert len(list_profiles()) == 1
