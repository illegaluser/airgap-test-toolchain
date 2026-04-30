# Playwright-AllInOne 통합 E2E 커버리지

이 문서는 `playwright-allinone` 프로젝트의 **통합 End-to-End 테스트 슈트** 가
어떤 기능을 어디까지 검증하는지 한 화면에 정리한다. pre-commit hook 은
관련 영역 변경 시 본 슈트 전체를 한 번의 `pytest -m e2e` 호출로 자동 실행한다.

## 한눈에 — 슈트 / 포트 / 데몬

| # | 영역 | 파일 | 포트 | 데몬 환경 |
|---|------|------|------|-----------|
| 1 | Recording UI | `test/test_recording_ui_e2e.py` | 18093 | uvicorn(server.py) + 사전 시드 세션 2개 |
| 2 | Auth Profile API | `test/test_auth_profile_api_e2e.py` | 18094 | uvicorn + `playwright` CLI stub + fake login service |
| 3 | Auth Profile UI | `test/test_auth_profile_ui_e2e.py` | 18095 | uvicorn + 사전 카탈로그 시드 |
| 4 | Discover URLs API | `test/test_discover_api_e2e.py` | 18096 | uvicorn + 로컬 fixture site |

총 **60 개** 테스트 함수 (Recording UI 27 → 29: Discover UI 회귀 2건 추가 포함). 각 슈트는 격리된 임시 디렉토리(`RECORDING_HOST_ROOT`,
`AUTH_PROFILES_DIR`, `DISCOVERY_HOST_ROOT`)와 별도 포트로 동작하므로 병렬
충돌이 없으며, pre-commit 은 4개 포트가 모두 비어있을 때만 진입한다.

## 실행

```bash
# 통합 e2e 일괄 실행 (pre-commit 과 동일)
pytest test -m e2e -q

# 슈트 1개만 실행 (개발 반복)
pytest test/test_discover_api_e2e.py -v

# 마커 + 키워드 조합
pytest test -m e2e -k "tour_script"
```

## 슈트 1 — Recording UI (`test_recording_ui_e2e.py`)

**Given**: 데몬 startup 시 디스크에 사전 시드된 세션 2개(`e2eDONE0001` 기본 +
`e2eFULL0002` 풀 메타) 가 자동 흡수됨. **When/Then**: 브라우저 UI 의 회귀.

마크업 / 헤더 / 풋터:
- `test_page_title_is_recording_ui` — 페이지 제목
- `test_footer_text_uses_recording_ui` — 풋터 문구
- `test_back_button_is_visible` — 뒤로 버튼 노출
- `test_hover_hint_banner_is_present_on_start_card` — 호버 메뉴 안내 배너
- `test_all_critical_ids_present_in_dom` — 핵심 DOM ID 회귀

세션 목록 + 필터:
- `test_seeded_sessions_appear_in_list` — 흡수된 세션 노출
- `test_session_filter_input_narrows_rows` — 검색 필터
- `test_session_filter_state_select_filters_done_only` — state 드롭다운 필터
- `test_session_filter_persists_across_reload` — 필터 localStorage 보존

R-Plus 드롭다운:
- `test_rplus_dropdown_expands_on_click`
- `test_rplus_dropdown_closes_on_escape`
- `test_rplus_dropdown_closes_on_outside_click`

Step 추가 폼:
- `test_step_add_form_has_scroll_and_hover_options`
- `test_selecting_scroll_auto_fills_value`

실행 결과 카드:
- `test_run_log_card_renders_with_status_pills` — PASS/HEALED/FAIL pill
- `test_screenshot_modal_opens_on_camera_click` — 스크린샷 모달
- `test_per_step_json_copy_button_exists`
- `test_copy_scenario_json_to_clipboard`
- `test_copy_original_py_to_clipboard`
- `test_copy_regression_py_to_clipboard`
- `test_regression_card_visible_and_shows_code`
- `test_regression_card_has_download_link`
- `test_diff_analysis_button_renders_4_section_markdown`
- `test_raw_diff_collapsible_present`
- `test_play_log_tail_endpoint_returns_seeded_log`

Import Script:
- `test_import_script_button_is_visible`
- `test_import_script_uploads_and_opens_result_panel`

## 슈트 2 — Auth Profile HTTP API (`test_auth_profile_api_e2e.py`)

**Given**: PATH stub `playwright` CLI (시드/녹화 시 외부 의존 차단) + 로컬
fake login service (`/login` → `/mypage` 쿠키 발급, verify 대상). **When/Then**:
auth-profile 엔드포인트 5종 + recording_start 통합 + 만료/오류 분기.

`TestAuthProfileApiLifecycle`:
- `test_initial_list_empty` — `/auth/profiles` 빈 목록
- `test_full_lifecycle` — seed → poll → verify → list → delete 풀 사이클
- `test_seed_without_verify_text_uses_url_access_check` — verify_text 생략 시 URL 접근 확인 fallback

`TestErrorPaths`:
- `test_recording_start_unknown_profile_404` — 없는 프로파일 거절
- `test_recording_start_failed_auth_leaves_no_orphan_session` — 검증 실패 시 orphan 세션 미생성 (회귀 가드)
- `test_seed_invalid_name_completes_with_error` — 이름 검증 위반 시 error state 종결
- `test_verify_unknown_profile_404`
- `test_delete_unknown_profile_404`

`TestExpiryDetection`:
- `test_corrupted_storage_yields_409_on_recording_start` — storageState 손상 → 409 + reason=profile_expired

`TestReplayExpiry`:
- `test_play_codegen_with_expired_profile_returns_409` — Play 시점 만료도 동일 응답

## 슈트 3 — Auth Profile UI (`test_auth_profile_ui_e2e.py`)

**Given**: 디스크 카탈로그에 프로파일 2개를 사전 시드 (정상/만료/sessionStorage 경고).
**When/Then**: Recording UI 의 auth fieldset / 시드 다이얼로그 / 만료 모달
브라우저 회귀.

`TestAuthMarkup`:
- `test_critical_ids_present` — auth-block ID 들 회귀
- `test_result_card_has_auth_profile_field` — 결과 카드 auth 라벨
- `test_session_table_has_auth_column` — 세션 표 auth 컬럼

`TestDropdown`:
- `test_seeded_profiles_listed` — `/auth/profiles` 결과 → option 렌더
- `test_session_storage_warning_label` — sessionStorage 경고 ⚠ 표시
- `test_select_updates_status`
- `test_verify_button_enabled_after_select`

`TestSeedDialog`:
- `test_opens_and_closes` — 다이얼로그 열기/취소
- `test_form_has_required_fields`
- `test_progress_dialog_explains_close_and_confirm`

`TestExpiryModal`:
- `test_start_with_expired_profile_shows_modal` — Start 클릭 시 409 → 만료 모달
- `test_reseed_button_opens_seed_dialog_with_prefill` — 재시드 시 입력값 prefill

## 슈트 4 — Discover URLs API (`test_discover_api_e2e.py`)

**Given**: 로컬 5페이지 fixture site (외부 링크/mailto/.pdf 노이즈 포함) +
DISCOVERY_HOST_ROOT 격리. **When/Then**: 6개 엔드포인트의 정상/예외 분기.

`TestDiscoverHappyPath`:
- `test_start_poll_json_csv` — 시작 → 폴링 → JSON/CSV(utf-8-sig BOM) 다운로드
- `test_csv_before_done_409` — 미존재 job → 404 (409 정책 회귀 placeholder)

`TestDiscoverConcurrencyAndCancel`:
- `test_concurrency_limit_429` — 동시 실행 한도 초과 시 429
- `test_cancel_partial_results` — 취소 → state=cancelled + 부분 결과 보존, 종료된 job 재취소 → 409, 미존재 → 404

`TestDiscoverTourScript`:
- `test_generate_tour_script_with_subset` — 선택 URL 만 박힘, AST 통과
- `test_unknown_url_422` — 미발견 URL → 422
- `test_normalized_url_match` — host 대문자/utm 변형도 매칭 성공

`TestDiscoverAuthErrors`:
- `test_unknown_profile_404`

`TestDiscoveryRootIsolation`:
- `test_results_under_discovery_host_root` — 결과 파일이 DISCOVERY_HOST_ROOT 에만, RECORDING_HOST_ROOT 아래에는 없음

## 의도적으로 단위(non-e2e) 가 책임지는 영역

다음은 e2e 가 아니라 단위/통합 슈트가 더 빠르고 정확하게 보장한다.
- URL 정규화 회귀 6종, BFS 격리/취소/세션만료 휴리스틱 → `test/test_url_discovery.py`
- Playwright sync API 호출 규약, fingerprint kwargs 직렬화 → 단위 + smoke
- TR.5 ~ TR.10 의 14-DSL 변환 의미 → `test/test_convert_*.py`, `test_post_process.py`

## 알려진 공백 (후속 보강 후보)

- 실제 SSO(예: portal.koreaconnect.kr) 플로우는 수동 검증만. CI 자동화는 자격증명 보관 정책이 잡힌 뒤.
- Discover의 *실패한 페이지가 결과 표/CSV에 status=null 로 기록* 되는 시각적 회귀는 단위에서만 확인.
- Discover UI 자체(`#discover-section`)의 Playwright 클릭 회귀는 미작성 — 후속에 합류 가능.
- 동시 e2e 슈트 실행 시 18093~18096 이외 OS-level race(예: agent venv 잠금)는 가드 없음.
- 코드 커버리지 수치는 측정하지 않는다. 필요 시 `pytest --cov=recording_service --cov=zero_touch_qa --cov-report=term-missing` 로 측정 (pre-commit 기본 비활성, 속도 가드).

## 회귀 시 디버깅 팁

1. 어느 슈트가 깨졌는지 확인: `pytest test -m e2e -q | tail`
2. 실패한 슈트만 단독 재실행: `pytest test/test_<which>_e2e.py -v --tb=long`
3. 데몬 살아있는지 확인: `lsof -i :18093-18096`
4. 데몬 로그: 각 슈트가 `subprocess.PIPE` 로 stderr 잡고 있다 — 실패 시 stderr 일부를 출력.
5. 브라우저 e2e 가 깨지면 headed 로 재현: `PWDEBUG=1 pytest test/test_recording_ui_e2e.py::test_X`
