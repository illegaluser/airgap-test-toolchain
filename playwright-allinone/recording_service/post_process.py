"""Post-processing helpers for recording artifacts (P3.9).

Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.7 (D3 — storage path portability)

This module *replaces the seeded storage path with an env var* in the
``original.py`` produced by ``codegen``. A codegen output produced under
``--load-storage=<path>`` contains:

    context = browser.new_context(storage_state="/abs/path/to/booking.storage.json")

If the absolute path remains, replay breaks on a different machine or with a
different ``AUTH_PROFILES_DIR``. This module rewrites that line to:

    import os
    context = browser.new_context(storage_state=os.environ["AUTH_STORAGE_STATE_IN"])

On replay the wrapper (``replay_proxy.run_codegen_replay``) injects the
``AUTH_STORAGE_STATE_IN`` env var → portability across machines.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


# Match storage_state="..." or storage_state='...' or raw string r"..."
# Playwright codegen output may use single or double quotes; handle both.
_STORAGE_STATE_RE = re.compile(
    r'storage_state\s*=\s*r?(["\'])([^"\']+)(["\'])'
)

_ENV_REF = 'os.environ["AUTH_STORAGE_STATE_IN"]'


def portabilize_storage_path(py_path: Path) -> bool:
    """Replace ``storage_state="<abs>"`` in ``original.py`` with the env var ref.

    Args:
        py_path: the .py file produced by codegen.

    Returns:
        True — at least one match was replaced (or an import had to be added).
        False — no match / file missing / already in env var form.

    Safety:
        - No file changes if there are no matches.
        - If ``import os`` is missing, we add it as the first line.
        - If the ``os.environ`` form is already present we no-op (idempotent).
    """
    if not py_path.is_file():
        log.debug("[post-process] portabilize skipped — file missing: %s", py_path)
        return False

    try:
        text = py_path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("[post-process] portabilize read failed — %s: %s", py_path, e)
        return False

    # Already in env form → no-op (idempotency).
    if _ENV_REF in text:
        log.debug("[post-process] portabilize skipped — already env ref: %s", py_path)
        return False

    if not _STORAGE_STATE_RE.search(text):
        return False

    new_text = _STORAGE_STATE_RE.sub(
        f"storage_state={_ENV_REF}",
        text,
    )

    # Add ``import os`` if missing — codegen output typically only has
    # `from playwright.sync_api import ...` and no `os`.
    if "import os" not in new_text:
        new_text = "import os\n" + new_text

    try:
        py_path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        log.warning("[post-process] portabilize write failed — %s: %s", py_path, e)
        return False

    log.info("[post-process] portabilize done — %s", py_path)
    return True
