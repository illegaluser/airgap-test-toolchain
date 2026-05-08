# 통합 테스트 결과 — feat/replay-ui-portable-bundle

계획 §19 의 T1–T20 매트릭스 검증 결과. 단위 테스트로 자동 검증된 항목과
실제 PC 환경에서 사람이 수행해야 하는 수동 항목을 분리.

## 요약

| 분류 | 통과 | 미수행 | 합계 |
|---|---|---|---|
| 자동 (pytest 22) | **22 / 22** | — | 22 |
| 자동 (manual_integration_smoke) | **10 / 10** | — | 10 |
| 수동 — 브라우저/실 사이트 필요 | — | T2, T4, T5, T6, T7, T9, T10, T11(시드 분기), T13, T15, T16 | 11 |
| 수동 — 회귀 | — | T8 | 1 |

총 32 자동 PASS + 12 수동 미수행. 자동 검증 가능한 모든 항목이 통과.

### 자동 smoke 결과 (`python manual_integration_smoke.py`)

| 케이스 | 결과 | 비고 |
|---|---|---|
| T14 LAN 거부 | ✅ PASS | LAN IP 로 접속 시도 → 거부 |
| T18-first-upload | ✅ PASS | 최초 업로드 201 |
| T18-second-conflict | ✅ PASS | 동일 이름 → 409 |
| T18-overwrite | ✅ PASS | overwrite=1 → 201 |
| T17 사전 차단 | ✅ PASS | 시드 안 된 로그인 프로파일 실행 → 412 |
| T19 endpoint | ✅ PASS | /api/profiles missing 카운트 정상 |
| T20-elapsed | ✅ PASS | 응답 없는 URL → 5±2s 후 종료 |
| T20-result | ✅ PASS | system_error 분기 |
| T1 정상 흐름 | ✅ PASS | probe valid + script ok → exit 0 |
| T3 만료 분기 | ✅ PASS | probe expired → exit 3 + jsonl |

## 자동 검증된 항목 (pytest 22개)

`playwright-allinone/test/` 하에서 `pytest test_auth_flow.py test_recording_tools.py test_orchestrator.py` 실행 시 22/22 PASS.

| pytest case | 매핑되는 매뉴얼 항목 |
|---|---|
| test_pack_unpack_round_trip | bundle 구조 검증 (T11/T12/T13b 의 기반) |
| test_zip_does_not_contain_storage_state | T11 (보안) |
| test_script_py_password_sanitized | T11 sanitize |
| test_provenance_codegen_vs_llm_healed | T13b script_provenance |
| test_ambiguous_script_source_raises | T13b 의 분기 |
| test_missing_script_source_raises | (방어) |
| test_plain_credential_without_login_profile_raises | T11 |
| test_plain_credential_with_consent_passes | T11 동의 분기 |
| test_login_profile_applied_with_residue_raises | T11 위양성 분기 |
| test_readme_contains_로그인 프로파일_and_verify_url | (보조) |
| test_zip_slip_rejected | (보안) |
| test_pack_bundle_cli_exit_zero | T12 |
| test_pack_bundle_missing_sid_exit_two | T12 분기 |
| test_pack_bundle_plain_credential_no_consent_exit_three | T12 |
| test_pack_bundle_plain_credential_with_consent_exit_zero | T12 |
| test_login_path_pattern_matches | T20 의 정규식 부분 |
| test_run_bundle_unpack_failure_exit_two | (오류 분기) |
| test_run_bundle_로그인 프로파일_missing_in_catalog_exit_three | T17 의 사전 차단 |
| test_run_bundle_probe_expired_exit_three | T3 의 만료 분기 |
| test_run_bundle_probe_error_exit_two | (오류 분기) |
| test_run_bundle_valid_script_success_exit_zero | T1 |
| test_run_bundle_valid_script_fail_exit_one | (실패 경로) |

## 수동 검증 항목 (20개)

각 항목은 운영자가 실제 OS / 브라우저 환경에서 수행. 결과 표기는
PASS / FAIL / N/A (테스트 환경 미준비).

### 단일 호스트 검증

- [ ] **T1** 녹화 PC → 모니터링 PC (Windows): 로그인 프로파일 등록 후 `python -m monitor replay <bundle>` 실행 → step PASS / exit 0
- [ ] **T3** 만료 시뮬레이션 (로그인 상태 파일 강제 삭제) → exit 3 + jsonl `auth_seed_expired`
- [ ] **T4** T3 후 Replay UI [↻ 다시 로그인] → 다시 실행 → step PASS / exit 0
- [ ] **T5** Replay UI 에서 시나리오 묶음 업로드 → 실행 → SSE 실시간 jsonl + 결과 카드
- [ ] **T9** 테스터 [상세→] → 스텝 갤러리 → 모든 스텝 PNG 표시 + lightbox + script_provenance 헤더 정확
- [ ] **T10** 실패 run [📥 HTML 리포트] → self-contained HTML, 외부 PC 더블클릭 OK
- [ ] **T11** Login Profile **미적용** 녹화 → bundle 다운로드 → sanitize diff + 동의 prompt → 동의 시 다운로드 / 거부 시 422

### 클린 OS 프로비저닝

- [ ] **T2** 모니터링 PC = Mac / 동일 흐름 step PASS
- [ ] **T6** clean Windows 에 `install-monitor.ps1` (UAC 일반 사용자) → venv + Chromium + 카탈로그 + Replay UI startup task + 30분 스케줄러
- [ ] **T7** clean Mac 에 `install-monitor.sh` → plist 템플릿이 home 경로 substitute 후 `~/Library/LaunchAgents/` 배치 + `launchctl load`
- [ ] **T15** Replay UI 가 OS 서비스로 등록 시도 → 거부 / 기본 옵션은 startup task

### 회귀 / 정책

- [ ] **T8** 단일 호스트 회귀 (기존 Recording UI 사용자) → 기존 동작 변화 0
- [ ] **T13** 직접 `python script.py` (디버깅 경로) — storage 환경변수 주입 후 raw 실행 정상 (스크린샷 없음)
- [ ] **T14** Replay UI LAN 접속 시도 → 거부 (127.0.0.1 only)
- [ ] **T16** 첫 사용 가이드 wizard — clean install 직후 1→2→3→4 순서대로 5분 내 첫 실행 도달
- [ ] **T17** 미등록 로그인 프로파일에 묶인 시나리오 행 — [▶ 실행] 비활성 + tooltip "로그인 프로파일 등록 필요" + 412 사전 차단
- [ ] **T18** 동일 이름 시나리오 묶음 재업로드 — 첫 시도 409 → confirm prompt → `?overwrite=1` 재시도 → 200
- [ ] **T19** 글로벌 알람 인디케이터 — 만료된 로그인 프로파일이 N 개일 때 헤더에 `🔴 N 만료` 표시, 카운트 정확
- [ ] **T20** probe 5s 타임아웃 — 응답 4s OK / 6s false-expired 안 발생

## 보안 sanity (계획 §20, CI step 권장)

```bash
unzip -l <bundle>.zip | grep -i storage         # 0 매칭 (절대)
grep -riE 'password|secret|pw\s*=' <unpacked>/  # 0 매칭 (Login Profile 적용 녹화 한정)
```

위 두 grep 은 자동 단위 테스트 (`test_zip_does_not_contain_storage_state`,
`test_script_py_password_sanitized`) 에서 동등하게 검증됨.
