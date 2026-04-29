"""Auth (auth_login DSL action) helper module — T-D / P0.1 core.

Design: docs/PLAN_PRODUCTION_READINESS.md §"T-D — Auth (form + OAuth + TOTP)"

Used by the executor's `_execute_auth_login`. Responsibilities:

- credential alias → env var lookup (`AUTH_CRED_<ALIAS>_USER` / `_PASS` / `_TOTP_SECRET`)
- log masking (`mask_secret` — prevent plaintext password / TOTP secret leaks)
- TOTP code generation (delegated to `pyotp`)
- target option parsing (`mode=form, email_field=#x, password_field=#y, submit=#z`)
- auto field detection (convention-based selectors — fallback when no explicit selector)

This module does not touch the Playwright Page object — page-side actions are
performed by the executor. This module handles only *credential / option parsing /
TOTP computation / selector candidates*.
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
    """Bundle of credentials resolved from an alias."""
    alias: str
    user: str = ""
    password: str = ""
    totp_secret: str = ""
    # Extra metadata — e.g. OAuth provider id later
    extra: dict = field(default_factory=dict)

    def has_password(self) -> bool:
        return bool(self.password)

    def has_totp(self) -> bool:
        return bool(self.totp_secret)


class CredentialError(RuntimeError):
    """Credential resolve failed. Either the alias is not registered in env or required fields are missing."""


def resolve_credential(alias: str) -> Credential:
    """Resolve an alias to a Credential via env var lookup.

    Lookup keys:
      - ``AUTH_CRED_<ALIAS_NORM>_USER`` (optional — may be empty)
      - ``AUTH_CRED_<ALIAS_NORM>_PASS`` (optional)
      - ``AUTH_CRED_<ALIAS_NORM>_TOTP_SECRET`` (optional)

    ALIAS_NORM = uppercase + non-alphanumeric/underscore chars → ``_``.

    If all three are empty, raise CredentialError. At least one must be set
    for the alias to be meaningful.
    """
    if not alias:
        raise CredentialError("auth_login value (credential alias) is empty")
    norm = re.sub(r"[^A-Z0-9_]", "_", alias.upper())
    user = os.environ.get(f"{ENV_PREFIX}_{norm}_USER", "")
    pwd = os.environ.get(f"{ENV_PREFIX}_{norm}_PASS", "")
    totp = os.environ.get(f"{ENV_PREFIX}_{norm}_TOTP_SECRET", "")
    if not (user or pwd or totp):
        raise CredentialError(
            f"credential for alias '{alias}' not in environment. "
            f"required keys: {ENV_PREFIX}_{norm}_USER / _PASS / _TOTP_SECRET"
        )
    return Credential(alias=alias, user=user, password=pwd, totp_secret=totp)


# ─────────────────────────────────────────────────────────────────────────
# Log masking
# ─────────────────────────────────────────────────────────────────────────


def mask_secret(value: str, *, keep: int = 2) -> str:
    """Mask a secret value. Show only the last ``keep`` chars; the rest become ``*``.

    Empty string returns ``<empty>``. If shorter than ``keep`` or if ``keep<=0``, mask everything.
    """
    if not value:
        return "<empty>"
    if keep <= 0 or len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


# ─────────────────────────────────────────────────────────────────────────
# auth_login target option parsing
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class AuthOptions:
    """Result of parsing the target options for auth_login."""
    mode: str = "form"           # form / oauth / totp
    email_field: Optional[str] = None    # form mode — email/username field selector
    password_field: Optional[str] = None # form mode — password field selector
    submit: Optional[str] = None         # form/totp shared — submit button selector
    totp_field: Optional[str] = None     # totp mode — OTP input field selector
    provider: Optional[str] = None       # oauth mode — provider id (google / github / mock)


def parse_auth_target(target: str) -> AuthOptions:
    """Parse the auth_login target string into AuthOptions.

    Grammar:

    - ``form``                                           (default mode=form)
    - ``form, email_field=#email, password_field=#pw``   (explicit selectors)
    - ``totp``                                           (default totp input auto-detection)
    - ``totp, totp_field=#code``                         (explicit)
    - ``oauth, provider=mock``                           (mock OAuth provider — planned in Phase 5)
    """
    target = (target or "").strip()
    if not target:
        return AuthOptions()  # default = form, auto-detect

    parts = [p.strip() for p in target.split(",")]
    opts = AuthOptions()

    # The first token is either the mode (no key) or ``key=value`` form
    first = parts[0]
    if "=" not in first:
        opts.mode = first.lower()
        parts = parts[1:]

    for p in parts:
        if "=" not in p:
            log.warning("[auth_login] ignoring unknown target token: %r", p)
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
            log.warning("[auth_login] ignoring unknown option key: %s=%s", key, value)

    return opts


# ─────────────────────────────────────────────────────────────────────────
# Field auto-detection — convention-based selector candidates
# ─────────────────────────────────────────────────────────────────────────

# Highest priority first. Each selector is compatible with Playwright `page.locator(...)`.
# Use the first match (count > 0).

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
# TOTP code generation
# ─────────────────────────────────────────────────────────────────────────


def generate_totp_code(secret: str) -> str:
    """Generate a 6-digit TOTP code at the current time using ``pyotp``.

    Clear error if pyotp is not installed — must be in REQ_PKGS in agent setup.
    """
    if not secret:
        raise CredentialError("TOTP secret is empty")
    try:
        import pyotp
    except ImportError as e:
        raise CredentialError(
            "pyotp not installed — must be in REQ_PKGS of mac/wsl-agent-setup.sh."
        ) from e
    return pyotp.TOTP(secret).now()
