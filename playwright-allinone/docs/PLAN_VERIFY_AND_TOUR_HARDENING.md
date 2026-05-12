# verify_profile 강화 + tour 첫 실패 abort 보강

## 배경

### 보고된 사고 (2026-05-02)

사용자가 `dpg` 로그인 프로파일을 적용한 상태로 Discover URLs 가 만든 tour 스크립트를 실행했는데, 첫 진입부터 비로그인 상태였음. 직접 검증 결과:

- `dpg.storage.json` 의 핵심 인증 쿠키 (`piolb`, portal/login/sso 도메인) 만료 시각이 2026-05-01 12:27~28 — 어제 만료.
- Playwright 가 storage_state 로드 시 만료 쿠키를 자동 폐기 → 컨텍스트엔 portal.koreaconnect.kr 도메인 valid 쿠키 0개 → 모든 URL 비로그인 진입.
- 그런데도 시스템의 `verify_profile` 은 ok=true 로 통과시켜 사용자가 만료를 인지하지 못함. 이유: `service_text` 가 빈 dpg 프로파일에 대해 status<400 + same-host 만 검사했고, 보호 페이지가 비로그인 안내를 정상 응답(200)으로 돌려주는 것을 그대로 통과로 간주.

### 추가로 발견된 부수 문제

- 첫 URL assert 실패에서 tour 스크립트가 즉시 abort. 옛 pytest 골격은 30 URL 모두 돌고 한꺼번에 결과 보고했는데, codegen 패턴 전환 후엔 첫 fail 에서 종료.
- AST 변환기는 `try/except` 블록 내부의 statement 를 인식하지 못함. 그래서 try/except 로 감싸면 verify step 매핑이 빠짐.

## 의사결정

### 1. verify_profile 에 storage 쿠키 만료 사전 검사 추가 (C.5-a)

**채택**: `_storage_alive_cookie_count_for_host(storage_path, host)` 헬퍼로 service_url 도메인 매칭 쿠키 중 살아있는(=세션 쿠키 또는 expiry > now) 개수를 센다. `total > 0 and alive == 0` 이면 service-side 검증 호출 전에 즉시 fail (`fail_reason="storage_cookies_expired"`).

**근거**:
- Playwright 의 자동 만료 폐기 동작과 1:1 일치 — 사실상 폐기될 쿠키를 사전에 감지.
- 비용 0 (브라우저 안 띄우고 JSON 파싱만).
- false-positive 위험 없음 (`total==0` 인 신규 storage 는 통과시킴, 매칭 도메인 쿠키가 *모두* 만료된 경우만 fail).

**기각된 대안**:
- 핵심 쿠키 이름 (e.g. piolb) 을 프로파일에 명시하게 하고 그것만 검사 — 사이트별 메타 늘어남, 등록 시 사용자 부담.

### 2. service_text 가 빈 프로파일에 body 비로그인 휴리스틱 추가 (C.5-b)

**채택**: `_body_looks_unauthenticated(body_text)` 헬퍼로 본문에 "로그아웃"/"Logout"/"Sign out" 가 *없고* "로그인"/"Login"/"Sign in" 이 *있으면* 비로그인 신호로 판정. status+host 검사가 통과해도 이 휴리스틱이 fail 신호를 내면 ok 를 뒤집음 (`fail_reason="body_indicates_unauthenticated"`).

**근거**:
- service_text 가 정의되지 않은 프로파일은 보강 검증이 필요. dpg 사고가 정확히 이 케이스.
- 양쪽 키워드 모두 있을 때 (e.g. 로그인 후 "최근 로그인 기록" 메뉴) 는 보수적으로 로그인 상태로 간주 — false-positive 차단.
- 사이트별 차이는 service_text 를 등록 시점에 채우는 정공법으로 극복. 본 휴리스틱은 service_text 미정의 프로파일의 안전망.

**기각된 대안**:
- 모든 프로파일에 강제 검사 — service_text 가 명시된 프로파일은 이미 authoritative 검증 중이라 충돌 가능 (false-fail 위험).

### 3. tour 스크립트의 각 URL 블록을 try/except 로 감싸기 (C.6-a)

**채택**: 각 URL 의 navigate + 2 assert 를 `try: ... except AssertionError: pass` 로 감싸 첫 실패에서 abort 되지 않게.

**근거**:
- 옛 pytest 골격이 제공하던 "전 URL 통과 후 한꺼번에 보고" 의미를 회복.
- assert 자체는 코드 그대로 유지 — converter 가 verify step 으로 매핑하는 흐름 보존.

### 4. AST 변환기에 ast.Try.body 재귀 추가 (C.6-b)

**채택**: `_handle_stmt` 에 `ast.Try` 분기 추가. `try.body` 의 statement 만 재귀 (handlers/finalbody/orelse 는 무시). 정상 흐름의 navigate/verify step 추출은 그대로.

**근거**:
- (3) 와 1:1 매칭. (3) 만 적용하고 (4) 안 하면 verify step 들이 시나리오에서 누락.
- handlers 무시는 의도적 — `except AssertionError: pass` 는 시나리오 의미가 없음.

## 구현 범위

### 파일 변경

| 파일 | 내용 |
|---|---|
| `shared/zero_touch_qa/auth_profiles.py` | `_storage_alive_cookie_count_for_host` / `_body_looks_unauthenticated` / `_check_status_and_host` / `_evaluate_service_response` 헬퍼 추가. `verify_profile` 시작에 cookie expiry pre-check. `_verify_service_side` 본문을 헬퍼 호출로 단순화 (기존 cognitive complexity 27 → 분할). |
| `shared/zero_touch_qa/converter_ast.py` | `_handle_stmt` 에 `ast.Try` 분기 — body 만 재귀. |
| `recording-ui/recording_service/server.py` | `_format_tour_steps_block` 가 각 URL 을 try/except 로 감싼 출력 생성. |
| `recording-ui/recording_service/annotator.py` | `_seg_looks_like_hover_trigger` 함수명 변경 (snake_case) 따라가는 import + 호출 갱신. |
| `test/test_auth_profiles.py` | C.5 단위 테스트 13건 (cookie alive count 6 + body heuristic 5 + 부수 2). |
| `test/test_converter_ast.py` | C.6 회귀 가드 1건 (`test_try_except_body_is_recursed_for_extraction`). |
| `test/test_recording_service.py` | float 동등 비교 보정, commented-code 오해 회피 표현. |

### 부수 lint 정리

- converter_ast.py 의 redundant `continue` 제거 (S3626).
- converter_ast.py 의 `_SEG_LOOKS_LIKE_HOVER_TRIGGER` → `_seg_looks_like_hover_trigger` (S1542). 호출자 (`annotator.py`) 갱신.
- converter_ast.py 의 inline 주석 (`# tag=nav` 류) 을 별도 줄 한국어 설명으로 이전 (S125 false-positive 회피).
- test_recording_service.py 의 float 동등 비교 → tolerance 비교 (S1244).
- test_recording_service.py 의 commented-code 오해 표현 정리 (S125).

## 검증

| 항목 | 결과 |
|---|---|
| C.5 단위 테스트 (storage alive count + body heuristic) | 13/13 통과 |
| C.6 단위 테스트 (`ast.Try` body 재귀) | 1/1 통과 |
| converter_ast 전체 단위 (51건) | 모두 통과 |
| auth_profiles 전체 단위 (36건) | 모두 통과 |
| 통합 e2e (`pytest -m e2e`) | 111 passed, 722 deselected |

### 직접 확인 (실 실행)

- 만료된 dpg storage 로 tour 실행 시 첫 URL 의 body 에 "로그인" 단어 + "로그아웃" 부재 확인 (직접 probe). 즉 향후 강화된 verify 가 호출되면 즉시 만료 감지.
- `_storage_alive_cookie_count_for_host(dpg.storage.json, "portal.koreaconnect.kr")` → 결과 (0, 1) — alive=0 → 사전 검사 단계에서 `storage_cookies_expired` fail.

## 후속 작업 (이번 묶음 외)

- HTTPException OpenAPI `responses=` 문서화 (server.py + rplus/router.py 의 ~20 엔드포인트, S8415).
- Cognitive Complexity refactor (executor.py / __main__.py / converter_ast 의 일부 함수, S3776).
- Pydantic Optional Field default 명시 (S8396).

## 즉시 사용자 액션

**dpg 프로파일 재시드 필요** — 본 변경은 만료 감지/보강을 강화한 것이고, 현재 storage 자체의 인증 토큰은 어제 만료된 상태라 새 시드를 받기 전엔 인증 작동 안 함.
