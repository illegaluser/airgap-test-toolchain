# PLAN: e2e 테스트 전면 재작성

확정일: 2026-05-16. 사용자 결정에 따른 전면 재작성.

## 1. 배경

지난 6개월간 fix/회귀 커밋 약 130건 중 P0 16건 + P1 28건은 거의 전부 기존 e2e 슈트가 사전에 잡지 못했다. 사용자 진단: **"들인 시간에 비해 효과가 없다"**. 본 PLAN 은 (a) 왜 효과가 없었는지를 진단하고 (b) 그 진단에 따라 새 슈트를 설계한다.

## 2. 진단 — 기존 e2e 슈트가 회귀를 못 잡은 이유

기존 슈트 10개를 회귀 분포와 대조하면 다음과 같다.

| 기존 슈트 | 검증 대상 | 회귀 분포와의 거리 |
|---|---|---|
| `test_auth_profile_api_e2e.py` | Auth Profile HTTP API CRUD | 회귀 0건. API 표면이 안정적이라 의미 없음. |
| `test_auth_profile_ui_e2e.py` | Auth Profile UI 렌더 | 회귀 0건. 카드 렌더 정도. |
| `test_discover_api_e2e.py` | Discover URLs API | 회귀 2건만 닿음 (헤드리스 무시, 다운로드 URL). |
| `test_recording_ui_layout_e2e.py` | Recording UI Round 3+4 레이아웃 | 표시 회귀만. P3. |
| `test_recording_ui_e2e.py` | Recording UI 일반 흐름 | 표시 + 일부 흐름. P1 와 거의 안 닿음. |
| `test_flow_usr_001_popup_e2e.py` | popup 1 시나리오 | popup 회귀 다발 (0d515de, a2645f1, 01819c6) 전부 우회. |
| `test_tour_full_pipeline_e2e.py` | tour 전체 파이프라인 | LLM 의존, 비결정론. 실제 회귀 잡힌 사례 0. |
| `test_delayed_appear_button_e2e.py` | 지연 등장 버튼 1건 | SPA polling 회귀 (c9924c3) 우회. |
| `test_convert_14_e2e.py` | 14 액션 변환 | converter unit 으로 잡혔어야 할 영역. |
| `test_user_journey_gold_e2e.py` | golden journey | 외부 SUT 의존, 비결정론. |

공통 문제 4가지:

1. **표면(UI 렌더, API CRUD) 만 검증** — 실제 회귀가 사는 녹화 → 변환 → 재생 → 회귀 .py emit → 재실행 체인의 결정론적 영역은 거의 비어 있음.
2. **외부 SUT 의존** — 사이트 변화 / 네트워크에 따라 flaky. 실패해도 "사이트 문제" 로 dismiss 됨.
3. **LLM 의존 비결정론** — tour/healing 슈트가 매번 다른 출력. 회귀 탐지 신호가 노이즈에 묻힘.
4. **abstract coverage 지향** — "이 화면을 열어 본다" 식. 실제 발생한 회귀 1건과 매핑되지 않음.

추가로 `*_headed.py` 4개 (`test_recording_ui_real_recording_headed.py`, `test_recording_ui_walkthrough_headed.py`, `test_replay_ui_real_run_headed.py`, `test_replay_ui_walkthrough_headed.py`) 는 자동 발사 슬롯에 들어갈 수 없으므로 (headed = display 필수) 폐기 대상에 포함.

## 3. 의사결정 (2026-05-16 서베이 결과)

| 항목 | 결정 | 사유 |
|---|---|---|
| 새 슈트 디렉토리 | `playwright-allinone/e2e-test/` (별도) | 신/구 슈트가 한 디렉토리에 섞이지 않음. 폐기 시점에 일괄 삭제 쉬움. |
| fixture HTML 위치 | `playwright-allinone/e2e-test/fixtures/` 단일 | 재사용 가능. 각 fixture 가 어떤 회귀를 표적하는지 한눈에 보임. |
| 폐기 시점 | 새 슈트 1건 들어오면 즉시 일괄 | 기존 슈트의 가치를 낮게 평가했으므로 망설일 이유 없음. 한동안 신/구 공존은 pre-commit 부담만 증가시킴. |
| pre-push branch 분기 | 분기 없음 — 모든 push 에 전체 슈트 | 단순성 우선. 단, 전체 슈트 < 5분 한도를 못 맞추면 fixture 단순화로 흡수 (분기로 도망가지 않음). |

대안과 트레이드오프는 본 PLAN 작성 직전 대화 (2026-05-16) 에 기록됨.

## 4. 4슬롯 구조

CI 0대 전제. github actions 비용 0 정책. 따라서 슬롯은 **로컬 4개** 뿐이다.

| 슬롯 | 발사 | 시간 상한 | 가드 |
|---|---|---|---|
| **pre-commit** | 매 commit (현행 `.githooks/pre-commit`) | < 30초 | P3 lint + 변경 영역 fixture 단위 (emit/generator) |
| **pre-push** (신설) | 매 push | < 5분 | P1 전체 + P2 fixture + daemon flow |
| **build-time selftest** | `./build.sh` 끝 | 환경별 | P0 중 빌드 PC 환경 가드 가능한 것 |
| **receiving-PC selftest** | `Launch-ReplayUI.{bat,command}` 최초 1회 | < 1분 | P0 중 받는 PC 환경 가드 |

pre-push hook 은 `.githooks/pre-push` 로 신설.

## 5. 새 슈트 그룹 A~E

### A. emit/generator unit (pre-commit)

`e2e-test/unit/` — Playwright 미사용. AST/문자열 변환 결정론.

**2026-05-16 재조사 후 확정 6건** (= 기존 `test_regression_emit_runs.py` 18건 + `test_role_conditional_submenu.py` 와 중복되지 않는 진짜 emit gap):

- `test_emit_no_visible_wait_before_click.py` (c57f537) — click 앞 `wait_for(state='visible')` 제거 가드
- `test_emit_lookahead_between_steps.py` (50868fa) — 다음 step 등장 lookahead emit + `REGRESSION_STEP_WAIT_TIMEOUT_MS`
- `test_emit_dynamic_settle_after_action.py` (5c1134f) — 각 action 직후 dynamic settle emit
- `test_emit_native_dialog_handler.py` (8077ee6) — setup 블록에 `page.on("dialog", ...)` auto-dismiss
- `test_emit_close_window_step.py` (0e59953) — converter_ast 가 '창 닫기' 시나리오 단계 보존 + emit
- `test_emit_action_timeout_env_var.py` (3c9156e) — emit 에 `REGRESSION_ACTION_TIMEOUT_MS` 환경변수 사용

**검토 후 추가 가능한 후보 4건** (Phase 3 마무리 시 재확인):

- e8ebd83 드롭다운 fallback 순서 (pos→val→label)
- a85f016 step 누락 + 스크롤 위치
- b4fc0a1 합성 selector 매치 0/2+ 가드
- 0d515de popup_to alias 보존

**이미 가드됨 — 신규 작성 불필요** (3건):

- 17b4a82 한글 ensure_ascii — `test_regression_emit_runs.py:238, 316`
- 4b8f622 role-conditional fallback — `test_role_conditional_submenu.py`
- 35bd5e0 iframe chain — `test_regression_emit_runs.py:523, 551`

**영역 불일치 — A 그룹 아님** (1건):

- a115498 hover 6 속성 — annotator/local_healer runtime 영역. fixture+browser integration 슬롯 후보.

목표: pre-commit 시간 예산 안에서 6~10건 결정론적 단위 (확정 6 + 후보 4). 외부 의존 0. 중복 회피된 3건 + 영역 불일치 1건은 별도 슬롯/기존 슈트가 가드.

### B. executor fixture (pre-push)

`e2e-test/integration/` — Playwright + `fixtures/` self-served HTML.

- `test_exec_popup_race.py` + `fixtures/popup_race.html` — popup 활성 페이지 전환 (0d515de)
- `test_exec_popup_settle.py` + `fixtures/popup_settle.html` — popup 열자마자 닫기 (a2645f1)
- `test_exec_popup_focus.py` + `fixtures/popup_focus.html` — popup 직후 포커스 복구 (01819c6)
- `test_exec_dropdown_select.py` + `fixtures/dropdown_select.html` — 드롭다운 항상 실패 (e8ebd83)
- `test_exec_select_before_mount.py` + `fixtures/select_lazy.html` — select 30s timeout (78615c3)
- `test_exec_spa_polling.py` + `fixtures/spa_lazy_mount.html` — SPA 비동기 마운트 (c9924c3)
- `test_exec_hover_height_zero.py` + `fixtures/hover_height_zero.html` — height:0 anchor (27c0ecc)
- `test_exec_iframe_contenteditable.py` + `fixtures/iframe_editor.html` — iframe contenteditable (d803b07, cb1bfc5)
- `test_exec_ime_dedupe.py` + `fixtures/ime_input.html` — 중복 click + IME (67cb8c8)
- `test_exec_actionability_fallback.py` + `fixtures/blocked_click.html` — actionability JS 폴백 (0c520c0)
- `test_exec_viewport_scroll.py` + `fixtures/offscreen_target.html` — viewport 외 자동 스크롤 (c57f537)
- `test_exec_autocomplete_typing.py` + `fixtures/autocomplete.html` — 자동완성 typing + keyup (ec5232d, 0c1215c, 3128af8)
- `test_exec_modal_dismiss.py` + `fixtures/modal.html` — 모달 안 닫힘 (e5a1e54)
- `test_exec_native_dialog.py` + `fixtures/native_dialog.html` — dialog 자동 처리 (8077ee6, ae5731b)
- `test_exec_role_conditional.py` + `fixtures/gnb_menu.html` — GNB role-conditional (4b8f622)

목표: 15개 fixture. 각 fixture 1개 회귀 1:1 매핑. headless 결정론.

### C. daemon flow (pre-push)

`e2e-test/flow/` — Recording UI + Replay UI daemon 띄움. fixture 페이지 1개를 녹화 → 변환 → 재생 → 회귀 .py emit → 재실행 round-trip.

- `test_flow_round_trip_basic.py` — 단순 1버튼 클릭 fixture round-trip
- `test_flow_round_trip_popup.py` — popup fixture round-trip (회귀 .py 가 popup 탭 보존하는지)
- `test_flow_round_trip_iframe.py` — iframe contenteditable round-trip
- `test_flow_round_trip_reimport.py` — 회귀 .py 재 import → 한글 깨짐 / 키 입력 사라짐 (73be3d3, b1dc29e)
- `test_flow_recording_ui_smoke.py` — Recording UI 표시 회귀 1건 (P3 흡수)
- `test_flow_replay_ui_smoke.py` — Replay UI 표시 회귀 1건

목표: 6건. daemon 5개 띄우는 비용 1회 분할 상환. 외부 SUT 의존 0.

### D. build-time selftest

`e2e-test/selftest_build/` — `./build.sh` 가 빌드 직후 자동 호출.

- WSL2 빌드 PC : `selftest_wsl2.sh` — venv 부팅, agent 연결, 컨테이너 변환 (d4d957b, 779e0d4, 308129e)
- container 변환 : `selftest_convert.py` — 컨테이너 안에서 시나리오 변환 import (d4d957b)
- Dify chatflow : `selftest_dify_heal.py` — chatflow LLM 치유 응답 형식 (1275bb4, b1dc29e). Dify 가 떠 있을 때만.
- code-AI-quality : `selftest_caq.sh` — 4 파이프라인 1회 (0babbcc, 514dacb)
- 휴대용 zip 빌드 : `selftest_portable_build.sh` — zip 의존성 충돌 (b7eed53)
- Replay UI 회귀 재생 : `selftest_replay_regression.py` — 회귀 재생 다발 (0da0036)

빌드 PC 환경에 해당하는 것만 실행. 결과는 `build.sh` stdout + `selftest.log`.

### E. receiving-PC selftest

`e2e-test/selftest_receive/` — `Launch-ReplayUI.{bat,command}` 가 최초 1회 자동 호출. `.selftest_done` 마커 후 skip.

- venv + Chromium 점검 : 휴대용 zip 의 파이썬/브라우저 (435ccf6, 4ccc736)
- Recording service 기동 : Windows 콘솔 상속 + 시그널 (4969092)
- 로그인 프로파일 카드 렌더 (4ccc736)
- Replay UI 띄우기 + 회귀 .py 1건 재생 1회
- 받는 PC 차단 5건 진단 (48d1ccd)

결과는 GUI 토스트 + `~/.dscore.ttc.playwright-agent/selftest.log`.

### 자동화 불가 — 체크리스트만

- popup Stop&Convert hang (316a132) — 사용자 녹화 패턴 의존
- R-Plus replay 수동 (b9b26dc)
- agent 자동연결 네트워크 토폴로지 의존 (308129e 잔여)

`docs/RELEASE_CHECKLIST.md` 에 명시 (별도 작성).

## 6. P0/P1/P2 → 새 슈트 매핑 표

| 심각도 | 건수 | 가드 슬롯 |
|---|---|---|
| P0 Blocker | 16 | D 8건 + E 5건 + 체크리스트 3건 |
| P1 Critical | 28 | A 14건 + B 14건 (일부 중복) |
| P2 Major | 22 | B 일부 + C 일부 + 외부 SUT 벤치 (release 직전 수동) |
| P3 Minor | 12 | A 표시 단위 + C smoke + lint |

(개별 회귀 ↔ 슈트 1:1 매핑은 각 슈트 파일 docstring 에 commit hash 명시.)

## 7. 폐기 대상

새 슈트 첫 1건 (`e2e-test/unit/test_emit_identifier_split.py` — f1761f2 가드, 현재 unit gap) 이 들어오는 commit 에서 일괄 삭제:

```
playwright-allinone/test/test_auth_profile_api_e2e.py
playwright-allinone/test/test_auth_profile_ui_e2e.py
playwright-allinone/test/test_discover_api_e2e.py
playwright-allinone/test/test_recording_ui_layout_e2e.py
playwright-allinone/test/test_recording_ui_e2e.py
playwright-allinone/test/test_flow_usr_001_popup_e2e.py
playwright-allinone/test/test_tour_full_pipeline_e2e.py
playwright-allinone/test/test_delayed_appear_button_e2e.py
playwright-allinone/test/test_convert_14_e2e.py
playwright-allinone/test/test_user_journey_gold_e2e.py
playwright-allinone/test/test_recording_ui_real_recording_headed.py
playwright-allinone/test/test_recording_ui_walkthrough_headed.py
playwright-allinone/test/test_replay_ui_real_run_headed.py
playwright-allinone/test/test_replay_ui_walkthrough_headed.py
```

동시에 `.githooks/pre-commit` 의 18094-18098 호출 블록 + 포트 충돌 감지 루프에서 18094-18098 범위 제거 (18092 / 18093 / 18099 영구 데몬 포트만 유지).

`playwright-allinone/test/E2E_COVERAGE.md` 는 폐기 시점에 같이 삭제 (새 PLAN 이 그 역할 대체).

## 8. 구현 순서

1. **PLAN 승인** (현재 단계). 사용자 확정 후 진행.
2. **Phase 1 — 슬롯 인프라**
   - `playwright-allinone/e2e-test/` 디렉토리 골격 (`unit/`, `integration/`, `flow/`, `fixtures/`, `selftest_build/`, `selftest_receive/`)
   - `e2e-test/conftest.py` — fixture 페이지 self-serve 헬퍼 (localhost 임의 포트)
   - `pytest.ini` 갱신 — `testpaths = test e2e-test`
   - `.githooks/pre-push` 신설 + install-git-hooks.sh 에 등록
3. **Phase 2 — 첫 슈트 + 일괄 폐기**
   - `e2e-test/unit/test_emit_identifier_split.py` 1건 작성 (f1761f2 가드 — 현재 unit 가 없는 진짜 gap)
   - 같은 commit 에서 기존 14개 e2e 파일 + pre-commit hook 블록 일괄 삭제
   - pre-commit/pre-push 통과 확인
4. **Phase 3 — A 그룹 완성 (emit/generator unit 14건)**
   - pre-commit 시간 예산 30초 안에 들어가는지 측정. 초과 시 가장 무거운 것 → pre-push 강등.
5. **Phase 4 — B 그룹 (executor fixture 15건)**
   - fixture HTML 1개씩 추가하며 슈트 1개씩 작성
   - pre-push 시간 예산 5분 안에 들어가는지 측정. 초과 시 fixture 단순화.
6. **Phase 5 — C 그룹 (daemon flow 6건)**
   - Recording UI + Replay UI 동시 띄우는 fixture-only round-trip
7. **Phase 6 — D 그룹 (build-time selftest)**
   - 빌드 PC 환경별로 셔플
   - `./build.sh` 끝에 hook
8. **Phase 7 — E 그룹 (receiving-PC selftest)**
   - `Launch-ReplayUI.{bat,command}` 에 hook
   - `.selftest_done` 마커 + 첫 실행 토스트
9. **Phase 8 — 체크리스트 + README 갱신**
   - `docs/RELEASE_CHECKLIST.md` 신설
   - `playwright-allinone/README.md` 의 테스트 섹션 재작성
   - CLAUDE.md 의 pre-commit e2e 표 갱신

## 9. 검증 기준

각 Phase 의 통과 조건:

- Phase 2 : pre-commit 통과 + 새 슈트 1건 PASS + 기존 슈트 14개 파일 부재
- Phase 3 : pre-commit < 30초 + A 그룹 14건 PASS + commit 시도 시 자동 발사 확인
- Phase 4 : pre-push < 5분 + B 그룹 15건 PASS + push 시도 시 자동 발사 확인
- Phase 5 : pre-push 시간 5분 한도 안에 C 그룹 포함 + flow 6건 PASS
- Phase 6 : `./build.sh` 완료 시 selftest 결과 stdout 노출 + `selftest.log` 생성
- Phase 7 : 받는 PC 더블클릭 시 selftest 토스트 + `.selftest_done` 마커 생성, 2회차 실행에선 skip
- Phase 8 : README + CLAUDE.md 의 표가 실제 슈트와 일치

각 슈트 docstring 첫 줄에 표적 commit hash 명시 (예: `"""Regression guard for 35bd5e0 — iframe chain emit."""`). 향후 회귀 발생 시 binary search 보조.

## 10. 위험 + 트레이드오프

| 위험 | 대응 |
|---|---|
| pre-push 5분 한도 초과 → `--no-verify` 우회 일상화 | fixture 단순화 우선. 부족하면 일부 슈트를 build-time 으로 강등. 분기 도입은 마지막 수단. |
| OS 매트릭스 0 — Mac push 가 Windows P0 못 잡음 | 받는 PC selftest 의 P0 커버리지를 최대한 키움 (E 그룹). Windows 휴대용 빌드는 사용자가 가끔 Windows 머신에서 빌드 트리거 권장. |
| fixture 가 실제 사이트 동작과 다를 수 있음 | 각 fixture 는 회귀 발생 당시의 실제 사이트 동작을 캡처 (HAR / screenshot) 해서 같이 보존. fixture 첫 작성 시 실제 사이트로 한 번 sanity check. |
| 새 슈트 작성 자체가 6개월 걸려서 의미 없어짐 | Phase 2-3 (A 그룹) 이 1주 안에 완료되도록 범위 통제. A 그룹 늦어지면 B/C 줄이는 결정 필요. |
| Dify chatflow / code-AI-quality / WSL2 selftest 가 환경 다양성 때문에 flaky | selftest 실패 시 빌드 자체는 통과 + 경고만. 실패 패턴 모이면 별도 슈트로 추출. |
| LLM 의존 슈트의 비결정론 회귀 | LLM 슈트는 D 그룹 (build-time) 만. pre-commit/pre-push 슈트에서 LLM 호출 0. |

## 11. 합의 필요 (PLAN 승인 전)

- [ ] 새 슈트 디렉토리 `playwright-allinone/e2e-test/` 와 4슬롯 구조
- [ ] 14개 기존 슈트 + E2E_COVERAGE.md 일괄 폐기 시점 (새 슈트 1건 들어올 때)
- [ ] pre-push hook 신설 + branch 분기 없음
- [ ] Phase 1-2 부터 착수 (`e2e-test/` 골격 + 첫 슈트 + 일괄 폐기 commit)
- [ ] LLM/외부 SUT 의존 슈트 0 정책 (pre-commit/pre-push 슬롯)

승인 시 Phase 1 (`e2e-test/` 골격 + `.githooks/pre-push` 신설) 부터 착수.
