"""보조(auxiliary) / 의도(terminal) step 분리 회귀.

근본 문제: carousel "다음 슬라이드" 같은 보조 이동 step 이 끝 도달 등으로 disabled
가 되면, 그 step 의 fatal 실패가 후속 의도 step (예: "사용신청") 의 도달까지
막아버린다. converter 가 step["kind"] 를 분류하고 executor 가 보조 step 의 실패
를 graceful skip 하면 의도 step 까지 도달 가능.

본 모듈은 다음을 검증:
  1. aux + disabled target → fast-path 즉시 skip, 후속 terminal PASS.
  2. step_kind 미지정(legacy 시나리오) → 모두 terminal 로 간주, 기존 동작 유지.
  3. classify_step_kind 의 키워드 매칭 정확성 (unit).
"""

from __future__ import annotations

from helpers.scenarios import click, navigate, verify

from zero_touch_qa.step_kind import (
    KIND_AUXILIARY, KIND_TERMINAL, classify_step_kind,
)


# ─────────────────────────────────────────────────────────────────────
# Unit — classify_step_kind
# ─────────────────────────────────────────────────────────────────────

def test_classify_carousel_korean_next_slide_is_auxiliary():
    assert classify_step_kind(
        "click", "role=button, name=다음 슬라이드, nth=0",
    ) == KIND_AUXILIARY


def test_classify_carousel_korean_prev_slide_is_auxiliary():
    assert classify_step_kind(
        "click", "role=button, name=이전 슬라이드",
    ) == KIND_AUXILIARY


def test_classify_carousel_english_next_slide_is_auxiliary():
    assert classify_step_kind(
        "click", "role=button, name=Next slide",
    ) == KIND_AUXILIARY


def test_classify_normal_button_is_terminal():
    assert classify_step_kind(
        "click", "role=button, name=사용신청",
    ) == KIND_TERMINAL


def test_classify_substring_named_aux_keyword_in_other_label_is_terminal():
    """'슬라이드' 라는 단어만 들어간 다른 의도 element 는 terminal — 보수적 분류."""
    assert classify_step_kind(
        "click", "role=button, name=슬라이드 다운로드",
    ) == KIND_TERMINAL


def test_classify_non_click_action_is_always_terminal():
    assert classify_step_kind(
        "fill", "role=textbox, name=다음 슬라이드",
    ) == KIND_TERMINAL


def test_classify_alarm_carousel_named_button_is_terminal():
    """'알림존 다음 슬라이드' 처럼 carousel-specific name 은 키워드 부분 매치
    (`다음 슬라이드`) 로 auxiliary. 의도된 동작."""
    assert classify_step_kind(
        "click", "role=button, name=알림존 다음 슬라이드",
    ) == KIND_AUXILIARY


def test_classify_role_alert_click_is_auxiliary():
    assert classify_step_kind("click", "role=alert") == KIND_AUXILIARY


def test_classify_role_alert_with_modifier_is_auxiliary():
    assert classify_step_kind("click", "role=alert, nth=0") == KIND_AUXILIARY


# ─────────────────────────────────────────────────────────────────────
# Integration — aux disabled fast-path 후 terminal 도달
# ─────────────────────────────────────────────────────────────────────

def test_aux_disabled_skip_then_terminal_passes(
    make_executor, run_scenario, fixture_url,
):
    """보조 이동 step 이 disabled 상태이면 즉시 skip, 후속 의도 step 정상 실행."""
    executor = make_executor()
    page = fixture_url("carousel_disabled.html")
    scenario = [
        navigate(page, step=1),
        # 보조 이동 (kind=auxiliary) — disabled 상태 → fast-path skip
        click("role=button, name=다음 슬라이드", step=2,
              description="다음 슬라이드 (carousel)", kind="auxiliary"),
        click("role=button, name=다음 슬라이드", step=3,
              description="다음 슬라이드 (carousel)", kind="auxiliary"),
        # 의도 클릭 (kind=terminal default)
        click("role=button, name=사용신청", step=4, description="사용신청"),
        verify("#status", step=5, condition="contains_text", value="applied"),
    ]
    results, _, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패한 step: {statuses}"

    # aux step 들은 heal_stage="aux_skip" 으로 마킹.
    aux_results = [r for r in results if r.step_id in (2, 3)]
    assert len(aux_results) == 2
    for r in aux_results:
        assert r.heal_stage == "aux_skip", (
            f"step {r.step_id} 가 aux_skip 으로 마킹되지 않음: {r.heal_stage}"
        )

    # terminal step (4) 은 정상 PASS, heal_stage 는 none.
    terminal_result = next(r for r in results if r.step_id == 4)
    assert terminal_result.status == "PASS"
    assert terminal_result.heal_stage == "none"


def test_absent_transient_alert_skip_then_terminal_passes(
    make_executor, run_scenario, fixture_url,
):
    """녹화 시점의 일회성 alert click 이 재생 시 없으면 즉시 skip."""
    executor = make_executor()
    page = fixture_url("carousel_disabled.html")
    scenario = [
        navigate(page, step=1),
        click("role=alert", step=2, description="일회성 안내 닫기", kind="auxiliary"),
        click("role=button, name=사용신청", step=3, description="사용신청"),
        verify("#status", step=4, condition="contains_text", value="applied"),
    ]
    results, _, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패한 step: {statuses}"
    alert_result = next(r for r in results if r.step_id == 2)
    assert alert_result.heal_stage == "aux_skip"


def test_legacy_scenario_without_kind_field_unchanged_behavior(
    make_executor, run_scenario, fixture_url,
):
    """kind 필드가 없는 legacy 시나리오는 모두 terminal 로 간주 — 보조 이동 분기 미발동.

    fixture 의 정상 활성 버튼만 클릭하므로 모두 PASS. 호환성 검증.
    """
    executor = make_executor()
    page = fixture_url("carousel_disabled.html")
    scenario = [
        navigate(page, step=1),
        # kind 미지정 — legacy 형식
        click("role=button, name=사용신청", step=2, description="사용신청"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패한 step: {statuses}"
    # 어떤 step 도 aux_skip 마커가 붙지 않음.
    assert all(r.heal_stage != "aux_skip" for r in results)
