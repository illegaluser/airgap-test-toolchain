"""인증 (auth_login DSL 액션) 헬퍼 모듈 — T-D / P0.1 본체.

설계: PLAN_PRODUCTION_READINESS.md §"T-D — 인증 (form + OAuth + TOTP)"

본 모듈은 executor 의 `_execute_auth_login` 에서 사용된다. 책임:

- credential alias → env var lookup (`AUTH_CRED_<ALIAS>_USER` / `_PASS` / `_TOTP_SECRET`)
- 로그 마스킹 (`mask_secret` — 평문 password / TOTP 시크릿 노출 방지)
- TOTP 코드 생성 (`pyotp` 위임)
- target 옵션 파싱 (`mode=form, email_field=#x, password_field=#y, submit=#z`)
- field 자동 탐지 (convention-based selectors — explicit selector 가 없을 때 fallback)

본 모듈은 Playwright Page 객체 자체는 다루지 않는다 — page 측 동작은 executor
가 수행. 본 모듈은 *credential / 옵션 파싱 / TOTP 계산 / selector 후보 제공* 만.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Credential alias resolution
# ─────────────────────────────────────────────────────────────────────────

ENV_PREFIX = "AUTH_CRED"


@dataclass
class Credential:
    """alias 로부터 resolve 한 자격 증명 묶음."""
    alias: str
    user: str = ""
    password: str = ""
    totp_secret: str = ""
    # 추가 메타 — 향후 OAuth provider id 등
    extra: dict = field(default_factory=dict)

    def has_password(self) -> bool:
        return bool(self.password)

    def has_totp(self) -> bool:
        return bool(self.totp_secret)


class CredentialError(RuntimeError):
    """credential resolve 실패. alias 가 env 에 등록 안 됐거나 필수 필드 누락."""


def resolve_credential(alias: str) -> Credential:
    """alias 를 env var lookup 으로 Credential 객체로 변환.

    검색 키:
      - ``AUTH_CRED_<ALIAS_NORM>_USER`` (선택 — 비어 있어도 됨)
      - ``AUTH_CRED_<ALIAS_NORM>_PASS`` (선택)
      - ``AUTH_CRED_<ALIAS_NORM>_TOTP_SECRET`` (선택)

    ALIAS_NORM 은 대문자 + 영문/숫자/언더스코어 외 문자 → ``_``.

    셋 다 비어 있으면 CredentialError. 적어도 하나는 있어야 의미 있는 alias.
    """
    if not alias:
        raise CredentialError("auth_login 의 value (credential alias) 가 비어 있음")
    norm = re.sub(r"[^A-Z0-9_]", "_", alias.upper())
    user = os.environ.get(f"{ENV_PREFIX}_{norm}_USER", "")
    pwd = os.environ.get(f"{ENV_PREFIX}_{norm}_PASS", "")
    totp = os.environ.get(f"{ENV_PREFIX}_{norm}_TOTP_SECRET", "")
    if not (user or pwd or totp):
        raise CredentialError(
            f"alias '{alias}' 의 credential 이 환경변수에 없음. "
            f"필요 키: {ENV_PREFIX}_{norm}_USER / _PASS / _TOTP_SECRET"
        )
    return Credential(alias=alias, user=user, password=pwd, totp_secret=totp)


# ─────────────────────────────────────────────────────────────────────────
# 로그 마스킹
# ─────────────────────────────────────────────────────────────────────────


def mask_secret(value: str, *, keep: int = 2) -> str:
    """비밀 값을 마스킹. 끝 ``keep`` 자만 평문, 나머지는 ``*``.

    빈 문자열은 ``<empty>``, ``keep`` 보다 짧거나 ``keep<=0`` 이면 전체 마스킹.
    """
    if not value:
        return "<empty>"
    if keep <= 0 or len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


# ─────────────────────────────────────────────────────────────────────────
# auth_login target 옵션 파싱
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class AuthOptions:
    """auth_login 의 target 옵션 파싱 결과."""
    mode: str = "form"           # form / oauth / totp
    email_field: Optional[str] = None    # form 모드 — email/username field selector
    password_field: Optional[str] = None # form 모드 — password field selector
    submit: Optional[str] = None         # form/totp 공통 — submit 버튼 selector
    totp_field: Optional[str] = None     # totp 모드 — OTP 입력 field selector
    provider: Optional[str] = None       # oauth 모드 — provider id (google / github / mock)


def parse_auth_target(target: str) -> AuthOptions:
    """auth_login 의 target 문자열을 AuthOptions 로 파싱.

    문법:

    - ``form``                                          (default mode=form)
    - ``form, email_field=#email, password_field=#pw``  (explicit selectors)
    - ``totp``                                           (default totp 입력 자동 탐지)
    - ``totp, totp_field=#code``                        (explicit)
    - ``oauth, provider=mock``                          (mock OAuth provider — Phase 5 예정)
    """
    target = (target or "").strip()
    if not target:
        return AuthOptions()  # default = form, 자동 탐지

    parts = [p.strip() for p in target.split(",")]
    opts = AuthOptions()

    # 첫 토큰은 mode (key 없음) 또는 ``key=value`` 형태
    first = parts[0]
    if "=" not in first:
        opts.mode = first.lower()
        parts = parts[1:]

    for p in parts:
        if "=" not in p:
            log.warning("[auth_login] 알 수 없는 target 토큰 무시: %r", p)
            continue
        key, _, value = p.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key == "mode":
            opts.mode = value.lower()
        elif key == "email_field":
            opts.email_field = value
        elif key == "password_field":
            opts.password_field = value
        elif key == "submit":
            opts.submit = value
        elif key == "totp_field":
            opts.totp_field = value
        elif key == "provider":
            opts.provider = value.lower()
        else:
            log.warning("[auth_login] 알 수 없는 옵션 키 무시: %s=%s", key, value)

    return opts


# ─────────────────────────────────────────────────────────────────────────
# Field 자동 탐지 — convention-based selector 후보
# ─────────────────────────────────────────────────────────────────────────

# 우선순위 높은 순. 각 selector 는 Playwright `page.locator(...)` 와 호환.
# 첫 매치 (count > 0) 사용.

EMAIL_FIELD_CANDIDATES = (
    'input[type="email"]',
    'input[autocomplete="username"]',
    'input[autocomplete="email"]',
    'input[name*="email" i]',
    'input[name*="user" i]',
    'input[id*="email" i]',
    'input[id*="user" i]',
)

PASSWORD_FIELD_CANDIDATES = (
    'input[type="password"]',
    'input[autocomplete*="password" i]',
    'input[autocomplete="current-password"]',
    'input[name*="password" i]',
    'input[name*="pass" i]',
    'input[id*="password" i]',
)

TOTP_FIELD_CANDIDATES = (
    'input[autocomplete="one-time-code"]',
    'input[name*="otp" i]',
    'input[name*="code" i]',
    'input[name*="verify" i]',
    'input[name*="totp" i]',
    'input[id*="otp" i]',
    'input[id*="code" i]',
    'input[inputmode="numeric"]',
)

SUBMIT_BUTTON_CANDIDATES = (
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("로그인")',
    'button:has-text("Verify")',
    'button:has-text("확인")',
    'button:has-text("Continue")',
    'button:has-text("Submit")',
)


# ─────────────────────────────────────────────────────────────────────────
# TOTP 코드 생성
# ─────────────────────────────────────────────────────────────────────────


def generate_totp_code(secret: str) -> str:
    """``pyotp`` 로 현재 시각 기준 6자리 TOTP 코드 생성.

    pyotp 미설치 시 명확한 에러 — agent setup 의 REQ_PKGS 에 포함되어야 한다.
    """
    if not secret:
        raise CredentialError("TOTP 시크릿이 비어 있음")
    try:
        import pyotp
    except ImportError as e:
        raise CredentialError(
            "pyotp 미설치 — mac/wsl-agent-setup.sh 의 REQ_PKGS 에 포함되어야 합니다."
        ) from e
    return pyotp.TOTP(secret).now()
