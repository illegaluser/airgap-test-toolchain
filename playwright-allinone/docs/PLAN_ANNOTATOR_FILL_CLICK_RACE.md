# Dynamic annotator — fill→click 자동완성 dropdown race 보강

## 배경

### 사례
2026-05-05, recording `af692a413e92` execute 모드 — Step 5 FAIL.

녹화 코드:
```python
page.get_by_role("textbox", name="키워드 입력").fill("요기요")          # step 4
page.get_by_role("button", name="요기요 계정검증조회").click()          # step 5
```

의도: 검색창에 "요기요" 입력 → 자동완성 dropdown 의 추천 항목 클릭.

재생 시: fill 즉시 click 시도 → dropdown 표시 race → 1차 시도 실패 → fallback / action_alternatives / local_healer 모두 못 풀음 → Dify LLM 호출 → 401 → FAIL.

### 문제
- dynamic annotator 의 `_probe_click_trigger` 는 click target 이 hidden 일 때 ancestor hover trigger 만 식별. 자동완성 dropdown 의 ancestor 는 hover-trigger 가 아니므로 식별 실패 → hover line prepend 안 됨 → race 그대로.
- 본질: fill 직후 dropdown 표시까지의 visibility race.

### 사용자 결정
> A 로 가

→ dynamic annotator 의 sandbox 가 "fill 직후 click target visibility race" 패턴을 인식하고 src 에 wait line 자동 prepend.

## 의사결정

### 1. `_WaitVisibleMarker` 신규 dataclass
**채택**: hover trigger 와 별도로, "click target 자체를 visible 될 때까지 기다리라" 는 의도를 표현하는 marker.

```python
@dataclass
class _WaitVisibleMarker:
    """fill→click 같은 visibility race 패턴 — click target 자체를 visible 까지 wait."""
    target: str           # 14-DSL target (그대로 expect 안에 사용)
    timeout_ms: int = 5000
    reason: str = ""
```

`triggers_by_lineno: dict[int, _HoverTrigger | _WaitVisibleMarker]` 로 union.

**기각된 대안**: `_HoverTrigger` 재사용 (`css_path=""` 으로 race 표시) — 의미 모호 + isinstance 분기 보다 dirty.

### 2. `_probe_click_trigger` 의 2차 분기

기존 (1차): hover trigger 식별. trigger 발견 → `_HoverTrigger` 반환.

신규 (2차): hover trigger 못 찾고 **직전 action 이 fill** 인 경우 — fill→dropdown race 의심.
- click target locator 의 visibility 를 `wait_for(state="visible", timeout=N)` 로 polling
- timeout 내 visible 되면 → `_WaitVisibleMarker(target=action.target)` 반환
- 안 되면 None (기존 흐름)

호출 측 (`_run_dynamic_pass_inproc`) 이 prev_action 추적해서 함수 인자로 전달.

### 3. write 단계 — marker 별 line prepend

`_write_annotated_with_triggers`:
- `_HoverTrigger` → 기존 hover line (`page.locator(<css>).first.hover()`)
- `_WaitVisibleMarker` → expect 라인 (`expect(<click_locator>).to_be_visible(timeout=...)`)

`_WaitVisibleMarker` 의 expect 라인은 src 의 click 라인에서 `.click(...)` 만 떼고 `expect(<rest>).to_be_visible(timeout=N)` 으로 wrap. converter 가 `to_be_visible` 패턴을 verify(visible) step 으로 변환 → executor 가 element visible 까지 polling → race 해소.

전제: codegen src 에 `from playwright.sync_api import expect` 가 있어야 함 (codegen 출력 표준 — 본 케이스 src 에서 확인).

### 4. fail-safe
- 2차 검사가 추가 ~5s wait 비용 → 모든 click 에 적용 금지. **prev_action.kind == "fill"** 한정.
- 검사 자체 실패 (예외) 시 None 반환 → 정상 흐름 (static fallback).

## 구현 범위

| # | 파일 | 변경 |
|---|---|---|
| 1 | `recording-ui/recording_service/annotator.py` | `_WaitVisibleMarker` 추가, `_probe_click_trigger` 보강, `_run_dynamic_pass_inproc` 의 prev_action 추적, `_write_annotated_with_triggers` 의 marker 분기 |
| 2 | `test/fixtures/fill_dropdown_race.html` | 신규 — input fill 후 setTimeout 으로 dropdown 표시 |
| 3 | `test/test_annotator_dynamic.py` | 신규 케이스 추가 |

코드 줄 수: ~80줄 추가, ~10줄 수정.

## 검증

- 기존 회귀: test_annotator_dynamic.py 5건 그대로 PASS
- 신규: fill→dropdown fixture 에서 annotate_script_dynamic 실행 → injected=1 + dst 파일에 expect-visible 라인 prepend 확인
- 사용자 시나리오 (af692a413e92): annotator 가 step 5 직전에 verify-visible step 추가 → executor 가 dropdown 대기 → step 5 정상 클릭 (실 사이트 검증은 사용자 환경에서)
