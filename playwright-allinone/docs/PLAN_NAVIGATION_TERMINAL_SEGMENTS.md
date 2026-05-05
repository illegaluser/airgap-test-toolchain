# Navigation/Terminal step 분리 — segment 기반 시나리오 실행

## 배경

### 사례
2026-05-05, recording `4c1cf7f5a8e9` 의 execute 모드 실행 (15 step):

```
[Step 13] click: get_by_role("button", name="다음 슬라이드").first
  → element resolved to <div role="button" aria-label="다음 슬라이드"
                              aria-disabled="true"
                              class="swiper-button-next swiper-button-disabled">
  → "element is not enabled" → 10s timeout → FAIL
[로컬복구 성공] 유사도 100% 매칭 → 같은 disabled element 다시 잡음 → FAIL
[Dify 치유] 401 UNAUTHORIZED → FAIL
실행 종료. 후속 step 14 ("사용신청"), step 15 도달 못 함.
```

후속 단순 시나리오 (12 step, "다음 슬라이드" .first 9회 + 사용신청 + 취소) 도 동일 패턴으로 실패.

### 문제
- carousel "다음 슬라이드" 가 끝 도달 시 `swiper-button-disabled` (aria-disabled="true") 가 됨.
- executor 는 이 disabled 클릭 실패를 fatal 로 처리 → 시나리오 중단.
- 정작 의도된 클릭 ("사용신청") 은 DOM 에 visible/enabled 임에도 도달하지 못함.
- LocalHealer 는 동일 disabled element 를 100% 유사도로 재선택 → 같은 실패 반복.

### 사용자 통찰
> "이동 클릭을 몇 번을 하든 최종 위치에 존재하는 엘리먼트는 불변인데 이걸 못 찾는다는 게 말이 되나? 엘리먼트를 예측하는 건가?"

핵심 지적: Playwright locator 는 호출 시점 DOM 평가 — 예측 X. 시스템이 element 를 못 찾은 게 아니라 **녹화된 보조 step (carousel 이동) 의 실패에 발이 묶여 의도 step 까지 도달하지 못한 것**.

### 사용자 결정
> "정공으로 가야지."

→ 단순 soft-skip 단기안이 아닌, navigation/terminal 분리 모델로 진화.

## 의사결정

### 1. step["kind"] = "auxiliary" | "terminal" 도입

**채택**: scenario step dict 에 `kind` 필드 추가. converter 가 emit 시 분류, executor 는 이를 읽어 분기.

```json
{
  "action": "click",
  "target": "role=button, name=다음 슬라이드, nth=0",
  "kind": "auxiliary",
  "step": 5
}
```

**기각된 대안**:
- (a) executor 가 매 실행마다 분류: 동일 시나리오를 여러 번 실행해도 분류는 안정적이어야 함. converter 가 한 번 결정하는 게 의미상 깨끗하고 디버깅 용이.
- (b) Segment 자체를 nested 객체로 emit (`[{aux:[...], terminal:{...}}]`): scenario.json 포맷 큰 변경 → 기존 시나리오와 비호환. 평탄 step + kind flag 가 surgical.

**기본값**: `kind` 누락 시 `terminal`. 기존 시나리오 동작 불변.

### 2. 분류 규칙 (converter)

**채택**: aria-label / role-name 의 키워드 매칭. 보수적 — 매치 안 되면 terminal.

키워드 (확장 용이):
- 한국어: `다음 슬라이드`, `이전 슬라이드`
- 영어: `next slide`, `previous slide`, `next`, `prev`, `previous` (단독 name 한정)

**기각된 대안**: class 기반 (`swiper-button-*`) 분류. codegen 출력에는 class 정보가 없음. role+name 만 보존됨.

향후: 다른 carousel 라이브러리 / 다언어는 같은 분류 함수에 키워드 추가.

### 3. Executor segment 실행 알고리즘

**채택**: scenario for-loop 를 segment 단위로 wrap. segment = (auxiliary*, terminal?).

```
segments = build_segments(scenario)  # 연속 aux + 다음 terminal 1개

for seg in segments:
    aux_steps, terminal = seg

    # 1) terminal 우선 시도 (visible & enabled?)
    if terminal and is_target_actionable(terminal):
        run(terminal); continue

    # 2) aux 들 순차 실행. 단 aux target 이 disabled 면 soft-skip.
    for aux in aux_steps:
        if is_target_disabled(aux):
            log_soft_skip(aux); record PASS-skip; continue
        run(aux)  # aux 의 fatal 실패도 segment 진행 막지 않음
        if terminal and is_target_actionable(terminal):
            break

    # 3) terminal 본 실행 (없으면 segment 종료)
    if terminal:
        run(terminal)  # 통상 healer 체인 적용
```

**핵심 변화**:
- aux 실패는 segment 안에서만 영향. terminal 까지 못 가면 그때 fatal.
- terminal 이 처음부터 클릭 가능하면 aux 전부 skip — 이상적인 fast path.

**`is_target_actionable`**: locator.first 의 `is_visible(timeout=200ms)` && `is_enabled(timeout=200ms)`. 모호하면 false (보수적 → aux 실행).

**`is_target_disabled`**: locator 가 attached + (`aria-disabled="true"` || `disabled` prop || class contains `*-disabled`) → True. 못 찾으면 False (= 정상 실행).

### 4. LocalHealer disabled 후보 skip

**채택**: `try_heal` 의 candidate loop 에 disabled 검사 추가. 동일 disabled element 100% 매칭 재선택 차단.

```python
for el in candidates:
    if _is_disabled(el):
        continue
    text = self._extract_text(el)
    ...
```

이 변경은 navigation/terminal 모델과 독립적으로도 의미 있음 — 어떤 시나리오든 disabled element 재매칭은 무의미.

### 5. 호환성

- 기존 scenario.json 에 `kind` 없음 → 모두 terminal → segment 는 [(aux=[], terminal=step)] 1:1 매핑 → 기존 동작 동일.
- 새 scenario 만 segment 모델로 동작.

## 구현 범위

| # | 파일 | 변경 |
|---|---|---|
| 1 | `zero_touch_qa/converter_ast.py` | emit 시 `kind` 분류 함수 호출 |
| 2 | `zero_touch_qa/converter.py` | line-based parser 도 동일 분류 |
| 3 | `zero_touch_qa/executor.py` | scenario for-loop → segment loop. helper `is_target_actionable`, `is_target_disabled`, `_build_segments` |
| 4 | `zero_touch_qa/local_healer.py` | candidate disabled skip |
| 5 | `zero_touch_qa/helpers/scenarios.py` | 테스트 빌더에 `kind` 인자 (default `terminal`) |
| 6 | `tests/test_executor_full_dsl.py` | 새 회귀 케이스: carousel-disabled scenario |

코드 줄 수 예상: 추가 ~150 줄, 수정 ~30 줄.

## 검증

**기존 회귀**: `pytest playwright-allinone/tests/` 전 통과.

**신규 케이스**:
1. **aux disabled → terminal 도달**: mock page 에 disabled "다음 슬라이드" + visible "사용신청" → segment 가 aux 1개 skip 후 terminal PASS.
2. **terminal fast-path**: terminal 이 처음부터 클릭 가능 → aux 전부 skip, terminal PASS.
3. **aux 정상 실행**: aux 클릭 가능, terminal 클릭 → 둘 다 실행.
4. **kind 누락 시나리오**: 기존 시나리오 그대로 동작 (모두 terminal).

**실측**: recording `4c1cf7f5a8e9` + 후속 12-step 시나리오 재실행 → PASS 기대.

**롤백**: kind 분류 함수가 빈 결과 반환하도록 환경변수 toggle (`ZTQ_DISABLE_SEGMENTS=1`). 이전 동작과 동등.

## 미해결 / 향후 작업

- 분류 키워드 확장 (다른 carousel 라이브러리, 다언어).
- "사용자 의도" 가 terminal 1개로 응축 안 되는 케이스 (multi-action segment) — 현 모델 미지원, 발견 시 별도 PLAN.
- 녹화 단계에서 redundant aux click 자체를 trim (recorder/annotator 책임) — 본 PLAN 범위 외.
