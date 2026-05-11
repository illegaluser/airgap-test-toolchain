"""Compatibility re-export — 본 모듈은 ``zero_touch_qa.visibility_heal`` 로 이동.

기존에 ``from recording_service.visibility_heal import ...`` 로 import 하던 호출처
호환을 위해 그대로 re-export 한다. 신규 import 는 ``zero_touch_qa.visibility_heal``
을 직접 사용 (zero_touch_qa 패키지 self-containment 보장 — 2026-05-11 회귀 차단:
컨테이너 안에 recording_service 가 미배포라 옛 위치로 두면 zero_touch_qa import
체인이 ModuleNotFoundError 로 깨짐).
"""
from zero_touch_qa.visibility_heal import *  # noqa: F401,F403
from zero_touch_qa.visibility_heal import (  # noqa: F401
    VISIBILITY_HEALER_JS,
)
