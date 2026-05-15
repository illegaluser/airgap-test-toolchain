# Regression script — iframe chain preservation in stable_selector

## 배경

### 사례
2026-05-15, recording `e19bba2ecd24` (portal.koreaconnect.kr SmartEditor 문의글 등록 시나리오).

- LLM healer (`zero_touch_qa --mode execute`) 19/19 PASS.
- 같은 recording 에서 생성된 `regression_test.py` 를 호스트 Replay UI(18093) 가 돌렸을 때 step 33 (`click #keditor_body`) 에서 15s timeout 으로 FAIL.

### 원인

scenario.healed.json step 9·10 의 target 은 **두 단계 iframe** 안의 leaf:

```text
iframe[title="에디터 전체 영역"] >> iframe[title="편집 모드 영역 ..."] >> #keditor_body
```

executor 의 `locator_resolver._descend_segment` 는 bare `iframe[...]` segment 를 자동으로 `frame_locator()` 로 라우팅해서 LLM healer 실행은 통과했다. 그러나 통과 직후 executor 가 **leaf element 의 stable_selector 만 캡처** (`#keditor_body`) 하면서 frame context 가 사라진다.

`regression_generator` 는 stable_selector 를 `r.target`/`scen_target` 보다 우선시하므로 standalone 회귀 .py 에 emit 된 코드가 `page.locator("#keditor_body")` 가 되고, 메인 DOM 에는 그 셀렉터가 없어 timeout 으로 깨진다.

거기에 `_chain_to_playwright_code` 자체가 `frame=` 접두만 `frame_locator()` 로 변환할 줄 알고, codegen 이 emit 한 bare `iframe[...]` chain 은 plain `.locator()` 로 fall-through 시키는 두 번째 결함이 누적되어 있었다.

## 의사결정

### 1. stable_selector 채택 시 frame chain prefix 보존
**채택**: scen_target (또는 r.target) 이 `iframe[...] >> ... >> leaf` 형태면 frame entry segments 를 추출해 stable_selector 앞에 붙인다. chain 이 없는 평상시는 그대로 stable_selector 단독 사용 (회귀 없음).

```python
frame_prefix = _frame_chain_prefix(scen_target) or _frame_chain_prefix(r.target)
target = (frame_prefix + " >> " + stable) if frame_prefix else stable
```

### 2. _chain_to_playwright_code 가 bare iframe[...] 도 frame_locator 로
**채택**: locator_resolver 와 동일 의미로, segment 가 `iframe[...]` 형태이면 `.frame_locator(seg)` 로 진입. `frame=` 명시 형태와 결과 동일.

```python
if _IFRAME_SELECTOR_RE.match(seg):
    cur = f"{cur}.frame_locator({json.dumps(seg)})"
    continue
```

### 3. 대안 — stable_selector 캡처 단계에서 frame context 박기
**기각**: executor 에서 frame context 까지 직렬화하면 캡처 로직이 커지고 stable_selector 의 정의("통과 시점 element 의 단일 식별자") 가 흐려진다. consumer (regression_generator) 가 호출 측 정보로 보존하는 편이 surgical.

## 구현 범위

| 위치 | 변경 |
|---|---|
| [shared/zero_touch_qa/regression_generator.py](../shared/zero_touch_qa/regression_generator.py) | `_frame_chain_prefix` 헬퍼 신설, stable_selector 채택 분기 보강, `_chain_to_playwright_code` bare iframe 분기 추가, `_IFRAME_SELECTOR_RE`/`_split_iframe_chain` import |
| [test/test_regression_emit_runs.py](../test/test_regression_emit_runs.py) | 회귀 가드 2종 — `bare_iframe_chain_uses_frame_locator`, `preserves_frame_chain_when_stable_selector_present` |

## 검증

- `test_regression_test_bare_iframe_chain_uses_frame_locator` — codegen ``locator("iframe[...] >> #x")`` 형태가 회귀 .py 에서 `.frame_locator(...)` 로 변환되는지 PASS.
- `test_regression_test_preserves_frame_chain_when_stable_selector_present` — 이중 iframe + stable_selector 조합에서 회귀 .py 가 `.frame_locator(...)` 를 최소 2회 emit 하고 `#keditor_body` leaf 가 함께 들어가는지 PASS.
- 기존 18개 슈트 중 `splits_exact_modifier_from_name` / `emits_visibility_heal_pre_actions` 2건 FAIL 은 **본 PR 이전부터 존재** (git stash 로 재현 확인). 본 PR 범위 외 별 PR 권장.

## 별 PR 권장 — cognitive complexity

`regression_generator.generate_regression_test` 함수가 본 PR 후 cognitive complexity 96 (기존 87 → +9, 허용치 15). 한 함수 안에서 14개 액션 emit + healing override + popup focus 회복 + visibility pre-action prepend 가 모두 처리되는 구조라 surgical 수정으로는 줄이기 어렵다. emitter 분리 / for 루프 본문 추출이 필요한 광범위 리팩토링. 본 회귀 픽스와 분리해 별 PR 로 다뤄야 한다 (user 정책: 함수 통째 리팩토링은 별 PR).
