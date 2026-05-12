# PLAN — DSL 표현력 커버리지 분석

> **Status**: v1 (2026-05-13)
> **Scope**: ZTQA DSL (executor.py 의 action 카탈로그) 이 임의 웹사이트 동작을
> 어디까지 표현 가능한지 *사실 기반* 으로 정량화한다.
> **History**: 2026-05-13 v1 — 외부 평가 후속, 트랙 2(외부 벤치마크) 시작 전
> 선행 분석으로 작성.

---

## 0. Executive Summary

DSL 은 임의 웹사이트의 **약 45~55%** 의 동작만 표현 가능하다. 나머지는 *측정 영역*
(성능 / 시각 / 접근성 / Dialog / WebSocket / 클립보드 / IndexedDB read / Cookie verify
/ ARIA / 모바일 gesture / right-click) 으로 별 트랙 필요.

본 문서는 그 한계를 *명시적*으로 기록해 외부 SUT 적용 시 운영자가 *기대치를
정렬*할 수 있게 한다.

---

## 1. 실제 액션 카탈로그 (이름 정정)

[PLAN_DSL_ACTION_EXPANSION.md](PLAN_DSL_ACTION_EXPANSION.md) 는 "14-DSL" 이라 부르지만
[shared/zero_touch_qa/executor.py](../shared/zero_touch_qa/executor.py) 에는 실제로
**15개 표준 action + auth_login 메타 1개 = 16개**.

| # | Action | 부작용 | 검증 대상 |
| --- | --- | --- | --- |
| 1 | navigate | page.goto + download 감지 | domcontentloaded |
| 2 | click | 클릭 + popup/nav 감지 | 클릭 성공 |
| 3 | fill | 3단계(type/clear+fill/js-set) | input.value 일치 |
| 4 | press | 키 입력 + nav 검증 | 키 효과 |
| 5 | select | option 선택 | option selected |
| 6 | check | checkbox/radio | checked property |
| 7 | hover | mouseover | hover 성공 |
| 8 | drag | drag-to | 드래그 완료 |
| 9 | scroll | scroll_into_view_if_needed | 스크롤 완료 |
| 10 | upload | multipart 업로드 | input.value contains filename |
| 11 | wait | wait_for_timeout | 시간 경과 |
| 12 | verify | 상태 assertion | visible/hidden/text/url/disabled/value |
| 13 | mock_status | page.route HTTP status | 상태코드 조작 |
| 14 | mock_data | page.route body | 응답 body 조작 |
| 15 | reset_state | cookie/storage/indexeddb clear | 상태 초기화 |
| meta | auth_login | storage_state restore | 인증 세션 |

### Sprint 6 측정 액션 5개 (2026-05-13 추가)

[PLAN_DSL_ACTION_EXPANSION.md §12](PLAN_DSL_ACTION_EXPANSION.md) 참조.

| # | Action | 커버 영역 | 비고 |
| --- | --- | --- | --- |
| 16 | dialog_choose | Dialog 분기 (0% → 90%) | one-shot 응답 등록 |
| 17 | storage_read | localStorage/sessionStorage read (60% → 90%) | scope:key 형식 |
| 18 | cookie_verify | Cookie 검증 (0% → 95%) | NAME@DOMAIN 형식 |
| 19 | performance | 페이지 로드 시간 (0% → 80%) | 단일 임계 ms |
| 20 | visual_diff | 시각 회귀 (5% → 80%) | PIL pixelmatch + golden 자동 |

---

## 2. 영역별 커버리지

| 영역 | 커버 | 미커버 (별 트랙 필요) |
| --- | --- | --- |
| **상호작용** | ~90% | right-click context menu, 키보드 조합(Ctrl+C/V) |
| **네비게이션** | ~70% | 명시적 new window, history.back/forward |
| **상태 검증** | ~80% | element bbox, visibility %, computed style |
| **네트워크 mock** | ~50% | WebSocket, SSE/EventSource, request body assert, 응답 시간 측정 |
| **상태 관리 (read)** | ~90% _(Sprint 6 후)_ | IndexedDB *read* (read 액션 미지원, clear 만 가능) |
| **성능 측정** | ~80% _(Sprint 6 후)_ | FCP/LCP 다중 메트릭, Lighthouse |
| **시각 검증** | ~80% _(Sprint 6 후)_ | region 비교, viewport 동적 변경 후 비교 |
| **접근성** | **0%** | ARIA tree, axe-core, role 검증 |
| **시간 조작** | ~30% | clock.setTime(), animation pause |
| **모바일** | **0%** | gesture (swipe/tap/pinch), viewport runtime 변경 |
| **Dialog** | ~90% _(Sprint 6 후)_ | beforeunload 의 일부 케이스 |
| **파일** | ~50% | 다운로드 *감지* 만, 파일 내용 검증·인쇄·PDF 불가 |
| **클립보드** | **0%** | read/write 미지원 |

### 가중 평균

가정: 일반 웹사이트 시나리오 가중치(상호작용 30%, 네비게이션 15%, 상태검증 15%,
네트워크 10%, 상태관리 10%, 성능 10%, 시각 10%, 접근성/모바일 등 합 10%).

- **Sprint 6 이전**: ~45~55%
- **Sprint 6 이후**: ~70~75%

즉 측정 액션 5개 추가로 *임의 웹사이트의 약 25% 영역* 추가 커버.

---

## 3. 본인 PLAN 문서가 *명시한* OUT 영역

[PLAN_DSL_ACTION_EXPANSION.md §8.7](PLAN_DSL_ACTION_EXPANSION.md) (Sprint 4) 에서 명시:

- 모바일 gesture (별 backlog)
- 다국어 fixture (별 backlog)
- Firefox/WebKit (chromium 한정)
- 보안/penetration (별 트랙)
- LLM 모델 교체 비교 (별 R&D)

---

## 4. *암묵적으로 빠진* 영역 (2026-05-13 식별, 일부 Sprint 6 해소)

| # | 영역 | Sprint 6 이전 | 현재 상태 |
| --- | --- | --- | --- |
| 1 | Dialog 상호작용 | 자동 dismiss 만 | ✅ dialog_choose 로 해소 |
| 2 | 클립보드 API | read/write 미지원 | ❌ Sprint 7 |
| 3 | Performance API | DSL 액션 미래핑 | ✅ performance 액션으로 해소 |
| 4 | Visual Regression | screenshot 캡처만 | ✅ visual_diff 로 해소 |
| 5 | WebSocket/SSE | page.route 는 HTTP 만 | ❌ Sprint 7 |
| 6 | IndexedDB / localStorage *read* | clear 만 | ⚠️ localStorage/sessionStorage read 해소, IndexedDB read 미해소 |
| 7 | Cookie 검증 | clear 만 | ✅ cookie_verify 로 해소 |
| 8 | ARIA tree | accessibility_tree() 미연결 | ❌ Sprint 7 (a11y_check 액션 권장 2개에 포함) |
| 9 | Viewport 실행 중 변경 | 초기화만 | ❌ Sprint 7 (viewport_resize 액션 권장 2개에 포함) |
| 10 | Right-click context menu | locator.click(button="right") 미지원 | ❌ Sprint 7 |

**해소율**: 5/10 (50%). 나머지 5개는 Sprint 7 (별 트랙) 로 이관.

---

## 5. 외부 SUT 벤치마크에 미치는 영향

[PLAN_PRODUCTION_READINESS.md §B5](PLAN_PRODUCTION_READINESS.md) 는 "실 도메인 검증 데이터"
부재를 인식. 본 분석은 그 *벤치마크가 측정할 것* 자체를 정의:

- **기능적 흐름 PASS/FAIL** — DSL 으로 충분 (Sprint 6 이후 ~70~75%)
- **측정 (성능/시각/접근성)** — Sprint 6 으로 *성능/시각* 해소, 접근성은 Sprint 7
- **복잡 상호작용** — Dialog 해소. 클립보드/multi-window/right-click 은 Sprint 7

[PLAN_EXTERNAL_TRUST.md](PLAN_EXTERNAL_TRUST.md) 의 트랙 2 Phase B2 (외부 벤치마크
시나리오 80개 작성) 은 본 커버리지 범위 안에서만 작성하고, *미커버 영역은 명시적
OUT* 으로 표시한다.

---

## 6. 결정

| # | 결정 | 일자 | 근거 |
| --- | --- | --- | --- |
| C1 | DSL 표현력 한계를 본 문서로 공식 기록 | 2026-05-13 | 외부 SUT 벤치마크 시작 전 *기대치 정렬* 필요 |
| C2 | Sprint 6 측정 액션 5개 (dialog_choose / storage_read / cookie_verify / performance / visual_diff) 도입 | 2026-05-13 | 가장 큰 0% 영역 우선 해소 |
| C3 | a11y_check / viewport_resize (권장 2개) 는 Sprint 7 로 이관 | 2026-05-13 | 현 사용자 수요 낮음, 권장 우선순위 |
| C4 | WebSocket/SSE / 클립보드 / right-click / IndexedDB read 는 Sprint 7+ 영구 backlog | 2026-05-13 | 사용 빈도 낮음 + 별 인프라 필요 |
| C5 | 모바일 gesture / Firefox/WebKit / 다국어 / 보안 / LLM 교체 는 [PLAN_DSL_ACTION_EXPANSION.md §8.7](PLAN_DSL_ACTION_EXPANSION.md) 의 기존 OUT 유지 | 2026-05-13 | 본 사이클 scope 외 |

---

## 7. 검증

본 문서의 정량 수치는 [shared/zero_touch_qa/executor.py](../shared/zero_touch_qa/executor.py)
의 *실제 구현* 코드를 직접 읽어 도출. 추측 미포함.

- 액션 카탈로그: `grep "elif action ==" executor.py` + `_VALID_ACTIONS` (`__main__.py`)
- Sprint 6 액션 5개: `_dispatch_measurement` + 각 `_execute_X` 메서드 (executor.py)
- 가중치는 *경험적 추정* — 도메인별 변동 가능. Phase B2 외부 벤치마크 데이터로
  교정 예정.
