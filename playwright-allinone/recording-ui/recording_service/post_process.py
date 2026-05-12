"""Recording 산출물의 후처리 헬퍼 (P3.9).

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.7 (D3 — storage 경로 이식성)

본 모듈은 ``codegen`` 이 생성한 ``original.py`` 의 *시드된 storage 경로를 env
var 로 치환* 하는 역할을 한다. ``--load-storage=<path>`` 로 시드 환경에서 만든
codegen 출력은 다음과 같은 구문이 박혀있다:

    context = browser.new_context(storage_state="/abs/path/to/booking.storage.json")

이 절대 경로가 그대로 남으면 다른 머신 / 다른 ``AUTH_PROFILES_DIR`` 환경에서
재생할 때 깨진다. 본 모듈은 위 라인을 다음과 같이 치환한다:

    import os
    context = browser.new_context(storage_state=os.environ["AUTH_STORAGE_STATE_IN"])

재생 시 wrapper (``replay_proxy.run_codegen_replay``) 가 ``AUTH_STORAGE_STATE_IN``
env 를 주입 → 머신 이식성 확보.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


# storage_state="..." 또는 storage_state='...' 또는 raw string r"..." 매칭.
# Playwright codegen 출력은 단일/이중 따옴표 둘 다 가능하므로 둘 다 처리.
_STORAGE_STATE_RE = re.compile(
    r'storage_state\s*=\s*r?(["\'])([^"\']+)(["\'])'
)

_ENV_REF = 'os.environ["AUTH_STORAGE_STATE_IN"]'


def portabilize_storage_path(py_path: Path) -> bool:
    """``original.py`` 의 ``storage_state="<abs>"`` 를 env var ref 로 치환.

    Args:
        py_path: codegen 이 생성한 .py 파일.

    Returns:
        True — 한 군데 이상 치환됨 (또는 import 보강이 필요했음).
        False — 매칭 없음 / 파일 없음 / 이미 env var 형태.

    안전 동작:
        - 매칭이 없으면 파일 미수정.
        - ``import os`` 가 없으면 파일 첫 줄에 추가.
        - 이미 ``os.environ`` 형태로 박혀있으면 (재진입) no-op.
    """
    if not py_path.is_file():
        log.debug("[post-process] portabilize 스킵 — 파일 없음: %s", py_path)
        return False

    try:
        text = py_path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("[post-process] portabilize 읽기 실패 — %s: %s", py_path, e)
        return False

    # 이미 env 형태면 no-op (멱등성).
    if _ENV_REF in text:
        log.debug("[post-process] portabilize 스킵 — 이미 env ref: %s", py_path)
        return False

    if not _STORAGE_STATE_RE.search(text):
        return False

    new_text = _STORAGE_STATE_RE.sub(
        f"storage_state={_ENV_REF}",
        text,
    )

    # ``import os`` 보강 — codegen 출력은 보통 from playwright.sync_api import
    # ... 만 있어 os 가 없다.
    if "import os" not in new_text:
        new_text = "import os\n" + new_text

    try:
        py_path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        log.warning("[post-process] portabilize 쓰기 실패 — %s: %s", py_path, e)
        return False

    log.info("[post-process] portabilize 완료 — %s", py_path)
    return True
