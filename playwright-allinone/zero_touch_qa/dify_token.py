"""Dify API token 자동 조회 — 컨테이너 DB 에서 fresh token 을 가져온다.

폐쇄망 다중 PC 배포 환경에서는 사용자가 매번 ``DIFY_API_KEY`` 를 export 할
수 없다. 또한 컨테이너가 재 provision 되면 token 이 새로 발급되므로 한 번
export 한 값도 무효가 된다. 따라서 env 가 비어 있으면 컨테이너 DB 에서 직접
조회해 매 호출마다 fresh token 을 사용한다.

본 모듈은 Config / DifyClient / recording_service 등 어느 호출 경로에서도
재사용 가능하도록 외부 의존 (다른 zero_touch_qa 모듈) 을 두지 않는다.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_CONTAINER = "dscore.ttc.playwright"
_DEFAULT_APP_NAME = "ZeroTouch QA Brain"


def fetch_token_from_container(
    container_name: str = _DEFAULT_CONTAINER,
    app_name: str = _DEFAULT_APP_NAME,
    timeout_sec: float = 5.0,
) -> Optional[str]:
    """컨테이너의 Dify Postgres 에서 chatflow API token 을 조회해 반환.

    매 호출마다 fresh — provision 이 chatflow 를 재 import 해 token 이
    재발급되어도 자동 동기화된다. host shell env / .env 의존을 없애 폐쇄망
    배포에서 PC 마다 별도 설정 작업이 필요 없게 한다.

    실패 (컨테이너 미실행 / docker CLI 부재 / DB 응답 비정상) 는 None.
    호출 측은 기존 env 값을 그대로 사용하는 graceful degrade 흐름을 유지한다.
    """
    sql = (
        "SELECT t.token FROM api_tokens t "
        "JOIN apps a ON t.app_id = a.id "
        f"WHERE a.name = '{app_name}' "
        "ORDER BY t.created_at DESC LIMIT 1"
    )
    cmd = [
        "docker", "exec", container_name,
        "bash", "-lc",
        f"PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d dify "
        f"-t -A -c \"{sql}\"",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    token = (result.stdout or "").strip()
    return token if token.startswith("app-") else None
