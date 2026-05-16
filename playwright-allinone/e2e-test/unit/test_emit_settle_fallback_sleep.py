"""Regression guard for e5a1e54 — modal/자동완성 안정성 위한 settle fallback sleep.

증상:
  - LLM 도움으로 통과한 회귀 코드를 일반 실행 시 자동완성 목록이 뜨기 전 클릭 →
    30s 대기 후 실패.
  - 모달 [확인] 한 번에 안 닫혀 다음 단계가 모달 뒤에 가려져 실패.

원인: SPA 사이트는 ``networkidle`` 에 영영 도달 못 해 ``_settle`` 가 사실상 0s
대기로 떨어짐. 자동완성 panel 이 뜨거나 모달 dismiss 가 끝날 시간이 없음.

조치: ``_settle`` 가 networkidle 미도달 시 ``REGRESSION_SETTLE_FALLBACK_MS``
(기본 1500ms) fallback sleep 으로 비동기 반응 완료 시간 보장.

본 슈트는 setup 의 fallback env var declaration + ``_settle`` 헬퍼의 fallback
sleep 경로 emit 을 가드.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_emit_declares_settle_fallback_env_var(emit_regression):
    """setup 에 ``REGRESSION_SETTLE_FALLBACK_MS`` 환경변수 read emit."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    assert "REGRESSION_SETTLE_FALLBACK_MS" in src, (
        f"settle fallback 환경변수 emit 누락: {src[:400]!r}"
    )
    assert "_settle_fallback_ms" in src, "_settle_fallback_ms 변수 누락"


@pytest.mark.unit
def test_settle_helper_falls_back_to_sleep_when_networkidle_misses(emit_regression):
    """``_settle`` 가 networkidle 실패 시 ``wait_for_timeout(_settle_fallback_ms)`` 로 fallback."""
    src = emit_regression([
        {"step": 1, "action": "navigate", "target": "", "value": "about:blank"},
        {"step": 2, "action": "click", "target": "#btn", "value": ""},
    ])
    assert src is not None
    assert "wait_for_timeout(_settle_fallback_ms)" in src, (
        f"_settle 의 fallback sleep 경로 emit 누락 — SPA 사이트에서 networkidle "
        f"미도달 시 0s 대기 회귀 가능: {src!r}"
    )
