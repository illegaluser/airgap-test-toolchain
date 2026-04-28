# 평가 페이지 카탈로그 (Phase 0 T0.1 산출물)

> Phase 1 (DOM Grounding) / Phase R (Recording) / Phase 3 (운영 회귀) 가 공유하는 페이지 ID 표.
> 본 카탈로그는 **에어갭 호환** 페이지만 포함. 공개 SaaS 행은 Phase 3 진입 시 사내 미러로 결정.

## 카탈로그 ID 규칙

`P0-{카테고리}-{번호}` 형식.

- `FX` — 자체 fixture (`test/fixtures/*.html`, `file://` 경로)
- `HS` — 자체 호스팅 (운영 호스트 컨테이너)

## P0-FX-* (자체 fixture)

| ID | 파일 | 시나리오 시작점 | 의미 액션 커버 |
| --- | --- | --- | --- |
| P0-FX-01 | `test/fixtures/click.html` | "Submit" 버튼 클릭 → 결과 확인 | click + verify |
| P0-FX-02 | `test/fixtures/fill.html` | 이메일·비밀번호 입력 → 제출 | fill + click + verify |
| P0-FX-03 | `test/fixtures/select.html` | 드롭다운 선택 → 선택값 확인 | select + verify |
| P0-FX-04 | `test/fixtures/verify_conditions.html` | 다양한 verify 조건 (visible/text/url) | verify (다양) |
| P0-FX-05 | `test/fixtures/full_dsl.html` | 14대 DSL 통합 시나리오 | navigate + click + fill + select + check + hover + drag + upload + scroll + wait + press + verify + mock_status + mock_data |

**경로 규약**: 컨테이너 내부에서는 `file:///app/test/fixtures/{name}.html`. 호스트에서는 fixture 파일 경로 직접.

## P0-HS-* (자체 호스팅)

| ID | URL (운영 호스트 기준) | 시나리오 시작점 | 인증 |
| --- | --- | --- | --- |
| P0-HS-01 | `http://localhost:18080/` | Jenkins 로그인 → 대시보드 → "ZeroTouch-QA" 잡 클릭 → 빌드 히스토리 확인 | 관리자 로그인 |
| P0-HS-02 | `http://localhost:18080/job/ZeroTouch-QA/` | 잡 상세 → 마지막 빌드 → 콘솔 출력 페이지 도달 | 관리자 로그인 (P0-HS-01 세션 재사용) |
| P0-HS-03 | `http://localhost:18081/console/apps` | Dify 콘솔 로그인 → 앱 목록 → 첫 앱 클릭 → 채팅 화면 도달 | 관리자 로그인 |
| P0-HS-04 | `http://localhost:18081/console/api-keys` | Dify 콘솔 → API 키 관리 페이지 → 키 발급 폼 노출 확인 | 관리자 로그인 (P0-HS-03 세션 재사용) |
| P0-HS-05 | `http://localhost:18081/explore/apps` | Dify 챗봇 공개 채팅 페이지 → 메시지 입력 → 응답 영역 노출 확인 | 비인증 |

**인증 처리**: Phase 1 단계는 비인증 페이지(P0-FX-01..05, P0-HS-05) 우선. 인증 페이지는 Phase 2 진입 시 BrowserContext.storage_state() 로 처리.

## 골든 시나리오 (각 페이지)

Phase 1 T1.7 (효과 측정 하니스) 가 페이지별 골든 DSL 을 작성한다. 카탈로그 ID 와 골든 시나리오 파일은 1:1 대응.

```text
tests/grounding_eval/golden/
  P0-FX-01.scenario.json
  P0-FX-02.scenario.json
  ...
  P0-HS-05.scenario.json
```

골든 시나리오는 사람이 작성 (LLM 출력 비교의 ground truth).

## Phase 3 진입 시 추가 항목 (TBD)

- `P0-MR-*` — 사내 미러(`mirror.local`) 기반 30종 코퍼스 (Phase 3 T3.1 시점에 결정)
- `P0-SaaS-*` — 개발 호스트 한정 공개 SaaS (Phase 3 진입 직전 결정, 미러로 대체 권장)

## 변경 이력

| 날짜 | 변경 |
| --- | --- |
| 2026-04-28 | 초기 작성 (Phase 0 T0.1 산출물). FX 5 + HS 5 = 10종 |
