"""Step 의도 분류 — auxiliary(보조 이동) vs terminal(사용자 의도).

녹화 시점(converter)에서 분류하여 scenario.json 에 박는다. 재생 시점(executor)
은 이 분류를 읽어 보조 step 의 실패를 graceful 처리한다.

분류 규칙(보수적):
- carousel navigation 의 의도가 명백한 경우만 auxiliary 로 분류.
  ("다음 슬라이드" / "이전 슬라이드" / "next slide" / "prev slide" / "previous slide")
- ``role=alert`` 클릭은 auxiliary 로 분류. codegen 이 일회성 안내/경고 영역
  닫기 동작을 클릭으로 캡처하는 경우가 있으며, 재생 시 이미 사라져 있어도
  본 사용자 의도 진행을 막으면 안 된다.
- 매치 안 되면 모두 terminal — default 가 안전(시나리오 진행 막지 않음).

향후 다른 보조 패턴(자동완성 dropdown 닫기 등) 발견 시 본 모듈에 키워드 추가.
"""

from __future__ import annotations

import re

KIND_AUXILIARY = "auxiliary"
KIND_TERMINAL = "terminal"

# carousel navigation 의 명백한 신호 — 본 키워드만 auxiliary 로.
_AUX_NAME_PATTERNS: list[re.Pattern] = [
    re.compile(r"다음\s*슬라이드"),
    re.compile(r"이전\s*슬라이드"),
    re.compile(r"\bnext\s+slide\b", re.IGNORECASE),
    re.compile(r"\b(?:prev|previous)\s+slide\b", re.IGNORECASE),
]


def classify_step_kind(action: str, target: str) -> str:
    """(action, target) 으로 step 의도 분류 반환.

    Args:
        action: DSL 액션명. ``click`` 만 분류 대상; 그 외는 항상 terminal.
        target: DSL target 문자열. ``role=..., name=Y`` / ``text=Y`` / ``aria-label`` 등.

    Returns:
        ``"auxiliary"`` 또는 ``"terminal"``. 매칭 안 되면 terminal.
    """
    if (action or "").lower() != "click":
        return KIND_TERMINAL
    if _is_transient_alert_target(target or ""):
        return KIND_AUXILIARY
    name = _extract_name(target or "")
    if not name:
        return KIND_TERMINAL
    for pat in _AUX_NAME_PATTERNS:
        if pat.search(name):
            return KIND_AUXILIARY
    return KIND_TERMINAL


def is_transient_auxiliary_target(target: str) -> bool:
    """재생 시 사라져 있으면 바로 skip 해도 되는 보조 target 여부."""
    return _is_transient_alert_target(target or "")


def _is_transient_alert_target(target: str) -> bool:
    s = target.strip()
    if " >> " in s:
        s = s.split(" >> ")[-1].strip()
    s = re.sub(r",\s*(nth|has_text|exact)=.*$", "", s).strip()
    return bool(re.match(r"role=alert(?:\s*,.*)?$", s, re.IGNORECASE))


def _extract_name(target: str) -> str:
    """target 문자열에서 사람이 읽는 이름 부분만 추출.

    - ``role=button, name=Y`` → ``Y``
    - ``text=Y`` / ``label=Y`` / ``placeholder=Y`` / ``title=Y`` / ``alt=Y`` → ``Y``
    - ``frame=... >> ...`` chain 이면 leaf segment 만 검사.
    - 후미 modifier (``, nth=N`` / ``, has_text=T`` / ``, exact=true``) 는 제거.
    """
    s = target.strip()
    if " >> " in s:
        s = s.split(" >> ")[-1]
    s = re.sub(r",\s*(nth|has_text|exact)=.*$", "", s).strip()
    m = re.match(r"role=[^,]+,\s*name=(.+)$", s)
    if m:
        return m.group(1).strip()
    m = re.match(r"(?:text|label|placeholder|title|alt)=(.+)$", s)
    if m:
        return m.group(1).strip()
    return ""
