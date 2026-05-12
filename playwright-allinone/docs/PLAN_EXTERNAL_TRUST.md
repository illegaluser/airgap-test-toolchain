# PLAN — 외부 신뢰성 강화 패키지

> **Status**: v1 (2026-05-13)
> **Scope**: 외부 평가에서 식별된 *외부 신뢰성* 약점을 해소하는 패키지.
> 트랙 3 (SUT 호환성 사전 진단) + 트랙 2 (외부 SUT 벤치마크) 두 트랙 통합 의사결정 기록.
> **History**: 2026-05-13 v1 — 평가 후속 작업 사이클 시작.

---

## 0. Executive Summary

평가 결과 본 솔루션의 *외부 신뢰성* (= 임의 SUT 에서 잘 작동하는가) 영역에 빈
틈이 식별됨. 본 사이클은 그 중 두 트랙을 진행:

- **트랙 3** — SUT 호환성 사전 진단 (`feat/compat-diag`, 완료)
- **트랙 2** — 외부 SUT 벤치마크
  - Phase B1: DSL 측정 액션 5개 확장 (`feat/dsl-action-dialog-choose`, 완료)
  - Phase B2: 시나리오 80개 + flake rate dashboard (`feat/external-bench`, pending)

트랙 1 (Recording UI 자기 도그푸딩) 은 사용자 결정으로 영구 보류.

---

## 1. 배경

### 1.1 평가에서 식별된 약점 (2026-05-13)

| # | 약점 | 본 사이클 처리 |
| --- | --- | --- |
| 1 | B1 인증 시나리오 (OAuth 자동 감지) | OUT — 본인 PLAN v3 [PLAN_AUTH_PROFILE_NAVER_OAUTH.md](PLAN_AUTH_PROFILE_NAVER_OAUTH.md) 의 의도적 OUT 결정 (D1, D17) 존중 |
| 2 | B3 모델 신뢰성 게이트 | OUT — Phase 1.5 별 트랙 진행 중 |
| 3 | B5 외부 SUT 일반화 데이터 부재 | ✅ **트랙 2** 로 해소 (Phase B2) |
| 4 | E2E 자가 도그푸딩 부재 | OUT — 사용자 영구 보류 결정 (수동 매트릭스 22+20 으로 충분) |
| 5 | Closed Shadow DOM 도입 마찰 | ✅ **트랙 3** 으로 해소 (호환성 진단 도구) |

### 1.2 검토한 옵션과 선택 — 트랙 2 reframe

DSL 표현력 한계 ([PLAN_DSL_COVERAGE.md](PLAN_DSL_COVERAGE.md)) 가 *Sprint 6 이전 ~45~55%* 로
좁아, 단순 시나리오 80개 작성이 *14-DSL 자기 한계* 측정으로 의미 약화 위험. 3개
옵션 검토:

| 옵션 | 내용 | 채택 여부 | 사유 |
| --- | --- | --- | --- |
| A (한정) | DSL 커버 영역만 벤치마크. 측정 영역은 OUT 명시 | ❌ | 도구 가치 확장 기회 놓침 |
| **B (확장)** | **측정 액션 5~7개 확장 선행 → 벤치마크** | **✅** | **0% 커버 영역은 *암묵적 빠짐*이지 의도적 OUT 아님. 도구 제품성도 같이 상승** |
| C (메타) | PASS/FAIL 이 아닌 *표현력 매트릭스* 측정 | ❌ | 메타-분석은 솔루션 자신감 못 줌 |

---

## 2. 의사결정 로그

| # | 결정 | 일자 | 근거 |
| --- | --- | --- | --- |
| T1 | 트랙 3 (호환성 진단) 우선 — 가장 단독 + 외부 의존 적음 | 2026-05-13 | 3~5일 분량, 트랙 2 의 SUT 선별 기준 제공 |
| T2 | 트랙 2 = 옵션 B (측정 액션 확장 + 벤치마크) | 2026-05-13 | [§1.2](#12-검토한-옵션과-선택--트랙-2-reframe) 참조 |
| T3 | 측정 액션 5개 필수 (dialog_choose / storage_read / cookie_verify / performance / visual_diff) + 권장 2개 (a11y_check / viewport_resize) | 2026-05-13 | [PLAN_DSL_COVERAGE.md §4](PLAN_DSL_COVERAGE.md) 의 *암묵적 빠짐 10개* 중 ROI 큰 순 |
| T4 | 권장 2개 (a11y / viewport) 는 Sprint 7 로 이관 | 2026-05-13 | 본 사이클 폭 통제, 사용자 수요 낮음 |
| T5 | 각 측정 액션은 *별 브랜치 + 별 PR* 권장이었으나 *한 브랜치 + 별 커밋* 으로 통합 | 2026-05-13 | 사용자 검토 부담 1회로 압축 ("최종 커밋 & 푸시 이전 전체 검증") |
| T6 | 트랙 1 (도그푸딩) 영구 보류 | 2026-05-13 | 사용자 결정 — 수동 매트릭스 22+20 으로 충분 |
| T7 | dead test 4 파일 통째 삭제 + 현재 함수 신규 회귀 35개 작성 | 2026-05-13 | "양보다 의미" 사용자 명시. D17 .py 일원화 후 stale |
| T8 | e2e_chromium scope session→module fix 본 사이클에 포함 | 2026-05-13 | dead test 분류 작업 중 환경 오염 원인 식별 — 부산물 |

---

## 3. 트랙 3 — SUT 호환성 사전 진단

### 3.1 목표

도메인 URL 입력 → DOM 스캔 → 자동화 가능/제한적/불가 5종 카테고리 판정 리포트
1장 자동 생성. 외부 SUT 도입 *전* 사용자가 호환성 분 단위로 진단.

### 3.2 구현 (브랜치 `feat/compat-diag`, commit `7c3b2cc`)

| 파일 | 역할 |
| --- | --- |
| [shared/zero_touch_qa/compat_diag.py](../shared/zero_touch_qa/compat_diag.py) | addInitScript hook + DOM probe. closed Shadow DOM / WebSocket / Dialog / CAPTCHA / canvas 면적 비율 감지 |
| [replay-ui/monitor/compat_diag_cmd.py](../replay-ui/monitor/compat_diag_cmd.py) | CLI 진입점 `python -m monitor compat-diag <url>` |
| [replay-ui/replay_service/server.py](../replay-ui/replay_service/server.py) | `POST /api/compat-diag` 라우터 |
| [test/test_compat_diag.py](../test/test_compat_diag.py) | 10 케이스 (5종 픽스처 + 분류 단위) |

### 3.3 판정 카테고리

| Verdict | 의미 | CLI exit |
| --- | --- | --- |
| compatible | DSL 커버 영역 안 | 0 |
| limited | 일부 동작이 별 트랙 필요 (WebSocket/Dialog/canvas-heavy) | 0 |
| incompatible:closed-shadow | closed Shadow DOM (자동화 불가, [§D17 영구 OUT](PLAN_PRODUCTION_READINESS.md)) | 2 |
| incompatible:captcha | reCAPTCHA/hCaptcha/Turnstile 감지 | 2 |
| unknown | 페이지 로드 실패 또는 timeout | 2 |

### 3.4 검증 결과

- pytest 10/10 PASS
- file:// 픽스처 진단 동작 확인
- 회귀 212/212 PASS (인접 영역 영향 없음)

---

## 4. 트랙 2 Phase B1 — DSL 측정 액션 확장

### 4.1 목표

[PLAN_DSL_COVERAGE.md §4](PLAN_DSL_COVERAGE.md) 의 *암묵적 빠짐 10개* 중 ROI 큰 5개를 DSL 액션으로 추가.

### 4.2 구현 (브랜치 `feat/dsl-action-dialog-choose`, commits `1e9898e` + `634194e`)

| 액션 | target | value | 핵심 호출 | 실패 모드 |
| --- | --- | --- | --- | --- |
| dialog_choose | `any` / `alert` / `confirm` / `prompt` / `beforeunload` | `accept` / `dismiss` / prompt 응답 텍스트 | `page.on("dialog", ...)` one-shot | 등록만 PASS, 실제 dialog 는 후속 step |
| storage_read | `local:KEY` / `session:KEY` | 기대 값 (빈=존재만) | `page.evaluate("...getItem(k)")` | VerificationAssertionError |
| cookie_verify | `NAME` 또는 `NAME@DOMAIN` | 기대 값 (빈=존재만) | `context.cookies()` 비교 | 동일 |
| performance | "" | 임계 ms | `performance.timing.loadEventEnd - navigationStart` | 동일 |
| visual_diff | golden 이미지 경로 | 임계 % 또는 `"auto"` | PIL `ImageChops.difference` + bbox 추정 + tobytes() | 동일 |

### 4.3 핵심 설계

- `_dispatch_measurement` 공통 helper — VerificationAssertionError 를 FAIL StepResult
  로 변환. measurement 는 selector 변경으로 치유 불가능 → healing 체인 (fallback /
  local_healer / dify) 거치지 않고 즉시 FAIL 마감.
- LLM Planner 가 emit 하지 *않음* — 사용자 시나리오에만 등장 (auth_login /
  reset_state 와 동일 정책).

### 4.4 검증 결과

- 신규 테스트 36개 PASS (dialog_choose 8 + storage_read 8 + cookie_verify 8 + performance 5 + visual_diff 7)
- 회귀 825/825 PASS (테스트 인프라 정리 포함, 5분 01초)

---

## 5. 트랙 2 Phase B2 — 외부 벤치마크 (완료)

### 5.1 목표

공개 안정 사이트 9개 × 시나리오 50개 작성 → flake rate 측정 인프라 구축.

### 5.2 사이트 선정 (안정성/접근성 기준)

| 사이트 | 시나리오 유형 | 작성 | 비고 |
| --- | --- | --- | --- |
| playwright.dev | 검색 / 문서 네비게이션 | 5 | 공식 안정 |
| TodoMVC (demo.playwright.dev/todomvc) | CRUD | 5 | 자동화 학습 표준 |
| the-internet.herokuapp.com | 모달 / 폼 / iframe / dialog | 10 | 자동화 표적 — Sprint 6 dialog_choose 회귀 포함 |
| demoqa.com | UI 컴포넌트 광범위 | 8 | UI 다양성 |
| saucedemo.com | 로그인 → 상품 → 결제 | 8 | E2E 데모 표준 |
| practicesoftwaretesting.com | 검색 → 카트 | 5 | 안정적 데모 |
| news.ycombinator.com | 읽기 전용 (검색, 페이징) | 3 | 정적 + 변화 적음 |
| wikipedia.org | 읽기 전용 (검색) | 3 | 글로벌 안정 |
| Salesforce Trailhead | closed shadow 검증용 | 3 | 의도적 ❌ (자동화 불가 증거 데이터) |
| ~~Naver 메인~~ | ~~한국어 읽기 전용~~ | 0 | **사용자 결정 (2026-05-13): 제외** |

### 5.3 구현 (완료)

```text
브랜치: feat/external-bench (main 기반)

산출:
- test/bench/sites/<site>/<scenario>.json × 50
- test/bench/flake_runner.py   (N회 반복 실행 + JSONL append)
- test/bench/dashboard.py      (정적 HTML 시계열)
- test/bench/README.md         (사용 가이드)
```

### 5.4 정기 실행 — 외부 서비스 위탁

**사용자 결정 (2026-05-13)**: `.github/workflows/bench.yml` daily cron 은 제거.
정기 flake rate 시계열 누적은 *추후 별도 서비스 내부 구현* 으로 대체.

본 레포는 *실행 인프라 (flake_runner / dashboard) 와 시나리오 50개* 만 자산화.
운영 데이터(7일 시계열, UNSUPPORTED 마킹 자동화 등) 는 별 서비스 내부에서 수집.

### 5.5 검증 결과 (1회 baseline, 2026-05-13)

```text
TOTAL=50 PASS=30 FAIL=20 (첫 그린 60%)

상위 안정: todomvc 100% / herokuapp 80% / practicesoftwaretesting 80% / saucedemo 75%
하위:     demoqa 38% / wikipedia 33% / hackernews 33% / salesforce 33% / playwright_dev 20%
```

- todomvc 100%, saucedemo 75% — *우리 인프라의 안정성* 확인 (사이트 자체가 안정)
- demoqa/wikipedia/hackernews — *외부 사이트 변동성* + selector 변경 영향
- salesforce 33% — closed shadow 의도적 FAIL 2개 + landing 1 PASS (호환성 진단 도구 검증 데이터)

### 5.6 트레이드오프

- **데이터 본질**: 외부 사이트가 마음대로 바뀜 → flake 가 *우리 도구* 가 아닌
  *SUT 변동성* 의 측정. 갱신을 *하지 않는 것* 이 측정 의도.
- **네트워크 의존**: 폐쇄망 본체 솔루션과 격리. 운영 정기 실행은 외부 서비스 위탁.

---

## 6. 부산 작업 — 테스트 인프라 정리 (commit `fc288c8`)

본 사이클 작업 중 발견된 *기존 부채* 일괄 정리. 평가 결과 5번째 항목
("e2e 통과 못함") 의 진짜 원인.

### 6.1 환경 오염 fix

- 원인: [test/conftest.py](../test/conftest.py) 의 `e2e_chromium` 이 session-scope
  로 sync_playwright 활성화 → 세션 끝까지 asyncio loop 유지 → 이후 비-e2e 테스트의
  자체 `sync_playwright()` 가 "Sync API inside asyncio loop" 거부.
- fix: scope `session → module`. file transition 시 깨끗이 close.
- 효과: main 의 40 failed + 10 errors 중 22개 (test_url_discovery 12 +
  test_visibility_healer 10) 해소.

### 6.2 Dead code 4 파일 삭제

[PLAN_AUTH_PROFILE_NAVER_OAUTH.md D17](PLAN_AUTH_PROFILE_NAVER_OAUTH.md) (2026-05-11) 의
"번들 zip 폐기 → .py 일원화" 결정 후 *테스트 파일은 안 지워짐*. 4 파일 통째 삭제.

### 6.3 함수별 신규 회귀 35개

- test_auth_flow.py 16 — sanitize_script / grep_credential_residue / select_script_source
- test_orchestrator.py 9 — probe_verify_url / run_script / _LOGIN_PATH_PATTERN / _utc_iso
- test_recording_service.py 10 — get_session_original sanitize gate / list_sessions / get_session / _estimate_action_count

### 6.4 lint fix

- `shared/zero_touch_qa/__main__.py` — log.error → log.exception 7곳 (S8572)
- `docs/PLAN_DSL_ACTION_EXPANSION.md` — markdown lint 약 40개 일괄 정리
- `.githooks/pre-commit` — PYTHONPATH 부채 fix (`.` → `shared:recording-ui:replay-ui`)

### 6.5 결과

- main : 866 passed, 40 failed, 10 errors
- 본 사이클 후: 825 passed, 0 failed, 0 errors (5분 01초)

---

## 7. 미해결 / 후속 사이클

| # | 항목 | 상태 |
| --- | --- | --- |
| F1 | 트랙 2 Phase B2 (벤치마크 80개 + dashboard) | pending — 별 사이클 |
| F2 | Sprint 7 — a11y_check / viewport_resize / WebSocket / 클립보드 / right-click / IndexedDB read | pending |
| F3 | __main__.py cognitive complexity S3776 (3곳) | 별 PR — 함수 통째 리팩토링 |
| F4 | __main__.py same return value S3516 (1곳) | 별 PR — 동작 변경 위험 |
| F5 | 트랙 1 도그푸딩 | 영구 보류 (사용자 결정) |
| F6 | B1 인증 OAuth 자동 감지 | OUT — v3 의 의도적 OUT 결정 존중 |
| F7 | B3 모델 신뢰성 게이트 | 별 트랙 진행 중 (Phase 1.5) |

---

## 8. 산출물 요약

### 8.1 신규 파일

- `shared/zero_touch_qa/compat_diag.py`
- `replay-ui/monitor/compat_diag_cmd.py`
- `test/test_compat_diag.py`
- `test/fixtures/compat_clean.html` / `compat_websocket.html` / `compat_canvas.html`
- `test/fixtures/dialog_alert.html` / `dialog_confirm.html` / `dialog_prompt.html`
- `test/fixtures/storage_set.html` / `cookie_set.html`
- `test/test_action_dialog_choose.py` / `test_action_storage_read.py` / `test_action_cookie_verify.py` / `test_action_performance.py` / `test_action_visual_diff.py`
- `test/test_auth_flow.py` (재작성)
- `test/test_orchestrator.py` (재작성)
- `test/test_recording_service.py` (재작성)
- `docs/PLAN_DSL_COVERAGE.md` (본 사이클의 자매 PLAN)
- `docs/PLAN_EXTERNAL_TRUST.md` (본 문서)

### 8.2 수정 파일

- `shared/zero_touch_qa/executor.py` (측정 액션 5개 + dispatch helper)
- `shared/zero_touch_qa/__main__.py` (_VALID_ACTIONS / _TARGET_OPTIONAL_ACTIONS / logging.exception 7곳)
- `replay-ui/replay_service/server.py` (`POST /api/compat-diag`)
- `replay-ui/monitor/cli.py` (compat-diag 등록)
- `test/conftest.py` (e2e_chromium scope=module)
- `test/test_recording_ui_e2e.py` (e2e_browser scope=module)
- `docs/PLAN_DSL_ACTION_EXPANSION.md` (§12 Sprint 6 + markdown lint 정리)
- `.githooks/pre-commit` (PYTHONPATH fix)

### 8.3 삭제 파일

- `test/test_recording_tools.py` (D17 .py 일원화 후 stale)

### 8.4 브랜치

- `feat/compat-diag` — commit `7c3b2cc`
- `feat/dsl-action-dialog-choose` — commits `1e9898e` (dialog_choose), `634194e` (나머지 4개 측정 액션), `fc288c8` (인프라 정리), `<이 PR>` (문서화)
