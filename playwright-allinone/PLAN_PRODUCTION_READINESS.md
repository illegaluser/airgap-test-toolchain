# Production Readiness — 실 운영 자동화 진입 로드맵

작성일: 2026-04-29
작성 근거: feat/grounding-recording-agent 브랜치의 직접 검증 (208 pytest passed,
recording 라운드트립 6/6 스텝, Jenkins 파이프라인 stage 1~2.4 그린)

---

## Context

`playwright-allinone/` 의 **현재 상태는 fixture 기반 PoC/데모 수준에서는 충분히
동작**한다. 14-DSL 액션 전부 executor 구현, 녹화→변환→재실행 라운드트립, 자가
치유 3단계, Jenkins 5-stage 파이프라인, 에어갭 호환 단일 tar.gz 배포까지 골격은
모두 살아 있다.

그러나 **실제 SaaS/엔터프라이즈 도메인의 운영 자동화 진입에는 6개의 구조적
블로커**가 남아 있다. 핵심 엔진은 단단하지만 실 도메인이 으레 요구하는 것
(로그인/iframe/세션 격리/관찰성)이 의도적으로 OUT 또는 backlog 로 미뤄져 있어
그 결손이 도구의 적용 범위를 좁힌다.

본 로드맵은 그 블로커들을 P0/P1/P2 우선순위로 정렬하고, 각 항목의 비용·완료
조건·차단 의존성을 명시한다. 기존 [PLAN_GROUNDING_RECORDING_AGENT.md](PLAN_GROUNDING_RECORDING_AGENT.md)
와는 보완 관계 — 그 로드맵이 *능력 확장* (DSL/RAG/Agent) 을 다룬다면, 본 문서는
*운영 진입* (인증/iframe/격리/관찰성) 을 다룬다.

---

## 현재 상태 스냅샷

### ✅ 견고한 부분

| 영역 | 상태 | 근거 |
| --- | --- | --- |
| 14-DSL 액션 커버리지 | navigate / click / fill / press / select / check / hover / verify / wait / upload / drag / scroll / mock_status / mock_data 전부 executor 구현 | [zero_touch_qa/executor.py](zero_touch_qa/executor.py) — 1496 라인, 14 핸들러 모두 존재 |
| 녹화→변환→재실행 사이클 | playwright codegen → 14-DSL JSON → executor 재실행 라운드트립 | 2026-04-29 검증: naver popup 6/6 스텝 (commit 316a132) |
| 자가 치유 3단계 | fallback target → LocalHealer → DifyClient (Ollama/gemma4:26b) | [zero_touch_qa/local_healer.py](zero_touch_qa/local_healer.py) + dify_client |
| CI 통합 | Jenkins 5-stage + 30일 artifact + JUnit + 208 passing 회귀 | [ZeroTouch-QA.jenkinsPipeline](ZeroTouch-QA.jenkinsPipeline), `pytest test --ignore=test/native` → 208 passed |
| 에어갭 호환 | 호스트 Ollama + 컨테이너 Jenkins/Dify/RAG 하이브리드, 단일 tar.gz 배포 | [build.sh](build.sh), [README.md](README.md) §"이미지 로드" |

### 🚨 6대 블로커 (실 운영 진입 차단)

| # | 항목 | 현 상태 | 영향 |
| --- | --- | --- | --- |
| B1 | 인증/로그인 | Phase 2 OUT 명시 ([PLAN_GROUNDING_RECORDING_AGENT.md](PLAN_GROUNDING_RECORDING_AGENT.md) §"명시적 OUT 항목") | 대부분 SaaS 의 첫 페이지가 로그인 폼 → 진입 자체 불가 |
| B2 | iframe / Shadow DOM | Phase 3 backlog | 결제 위젯, 임베디드 폼, 디자인 시스템 (Lit/Stencil) 거의 다 해당 |
| B3 | Phase 1.5 모델 신뢰성 게이트 | gemma4:26b tool-calling 90% 신뢰도 검증 진행 중 | 통과 못하면 Phase 2 진입 불가 → 복잡 시나리오 LLM 자율 생성 불가 |
| B4 | 세션/데이터 격리 | 메커니즘 부재 (확인 못 함) | 시나리오 간 쿠키/storage/DB 오염 → 멱등성 무너지면 회귀 자동화 불가 |
| B5 | 실 도메인 검증 데이터 | fixtures 18개 HTML, 실 SaaS 안정성 측정 데이터 없음 | selector 변동 / 모달 race / SPA 라우팅 등에 대한 안정성 미지수 |
| B6 | 운영 관찰성 / RCA | 스크린샷 + 로그만, healer 통계 미수집, 시나리오 Git 미통합, 대시보드 미구현 | 실패 원인 추적 불가 → 회귀 도입해도 디버깅 비용 폭증 |

### ⚠️ 부분 구현 / 취약점 (블로커는 아니나 운영 시 문제 야기)

| # | 항목 | 현 상태 | 후속 조치 |
| --- | --- | --- | --- |
| W1 | converter 의 단순성 (line-based regex) | popup 누락 / `.nth(N)` 손실 — popup 만 commit 316a132 에서 fix | AST 화 (P0.4) |
| W2 | recording_service 세션 GC 부재 | `~/.dscore.ttc.playwright-agent/recordings/` 무한 증가 | retention 정책 (P1.3) |
| W3 | Stop & Convert orphan handle | codegen 외부 종료 시 `state=recording` 박제 — 서버 재시작 시점에만 마킹 | heartbeat 스윕 (P1.5) |
| W4 | LLM 출력 강건성 | `_validate_scenario` 수준, hallucinated target/value 보정 미확인 | dry-run 검증 (P1.4) |
| W5 | 이번 fix 의 영구 반영 | converter.py / converter_proxy.py 호스트 + 핫카피만, 컨테이너 baked-in 안 됨 | 다음 `./build.sh --redeploy --fresh` 시 반영 (P0.5) |

---

## 우선순위 로드맵

### P0 — 운영 진입 필수 (예상 6~8주)

#### P0.1 — 로그인/인증 시나리오 처리 *(B1 해소)*

**범위**

- credential 주입 액션 신설 (DSL: `auth_login`, value=계정 alias)
- credential 저장소 (Jenkins Credentials + 컨테이너 안전 노출)
- OAuth 흐름 핸들러 (redirect → callback)
- 인증 후 세션 쿠키를 `storage_state` 로 dump → 후속 시나리오 재사용

**완료 조건**

- 사내 SSO 1개 + OAuth 외부 1개 (Google) 흐름 시나리오 자동 통과
- credential 이 로그/스크린샷에 노출 안 됨 (마스킹 검증)

**비용**: 중-대 (2~3 주)

**의존성**: P0.4 (converter AST 화) 권장 — auth flow 는 popup/redirect 가 잦아
정확한 변환 필요

---

#### P0.2 — iframe / Shadow DOM 지원 *(B2 해소)*

**범위**

- 14-DSL 의 `target` 문법에 frame/shadow path 옵션 추가 (예: `frame=#iframe1>>role=button, name=확인`)
- `locator_resolver` 에 `frame_locator` 자동 traversal
- shadow root 통과 selector — Playwright 의 `:light()` / piercing selector 활용
- recording 측 codegen 도 frame 진입 라인 (`page.frame_locator(...).get_by_role(...)`) 보존

**완료 조건**

- 결제 위젯 (Stripe/Toss) 1종 + 디자인 시스템 (예: Material Web Components) 1종에서
  fill/click/verify 동작 확인
- frame_locator 진입 시 healer 도 같은 frame 안에서 fallback 수행

**비용**: 중 (2 주)

**의존성**: 없음 (executor + converter 양쪽 동시 작업)

---

#### P0.3 — 세션 / 데이터 격리 *(B4 해소)*

**범위**

- 시나리오 단위 `BrowserContext` 분리 (현재는 단일 page 재사용 가능성 점검)
- 백엔드 fixture seed/reset hook — 시나리오 메타데이터에 `setup_url` / `teardown_url`
  필드 추가, executor 가 step 0 / step ∞ 에서 호출
- localStorage / IndexedDB / cookie 명시적 reset 액션 (DSL: `reset_state`)

**완료 조건**

- 동일 시나리오 100회 연속 실행 시 통과율 95% 이상 (현재는 측정 데이터 없음)
- 시나리오 A 가 시나리오 B 결과를 오염시키지 않음을 회귀 케이스로 증명

**비용**: 중 (1.5 주)

**의존성**: 없음

---

#### P0.4 — converter AST 화 *(W1 해소, P0.1 의 전제)*

**범위**

- 현재 line-based regex 를 `ast.parse` 기반 정확 파싱으로 교체
- `.nth(N)` / `.first` / `.filter(has_text=...)` / `.locator(...).locator(...)` 보존
- popup/page 변수 추적 (이번 commit 316a132 의 정규화는 임시 처리)
- frame_locator chain 보존

**완료 조건**

- 기존 codegen 18 fixture + naver/google/SaaS 3종에서 손실 없는 변환
- 단위 테스트 30 케이스 (각 codegen 패턴별)

**비용**: 소-중 (1 주)

**의존성**: 없음 — 이번 commit 316a132 의 정규화 hotfix 와 호환 (정규화 라인을
AST 변환 후로 이동만 하면 됨)

---

#### P0.5 — 이미지 빌드 / 배포 자동화 *(W5 해소)*

**범위**

- 이번 세션 fix (converter / converter_proxy / codegen_runner / sidebar-link)
  baked-in 검증
- CI 파이프라인에 sanity 빌드 추가 — feat/* 브랜치 push 시 build.sh 자동 실행
- 빌드 산출물 무결성 (sha256) + 빌드 매트릭스 (mac arm64 / wsl amd64) 자동화

**완료 조건**

- main 브랜치 push 시 양 아키 tar.gz 자동 산출
- 산출물의 sha256 + 빌드 시각이 release 노트에 자동 기재

**비용**: 소 (3~5 일)

**의존성**: 없음

---

### P1 — 안정성 / 관찰성 (예상 4~6주)

#### P1.1 — 시나리오 Git 통합 *(B6 부분 해소)*

- Dify chatflow 변환 결과 / recording_service 변환 결과를 별도 repo 브랜치에
  자동 commit
- diff/ 가시화 (이전 시나리오 대비 selector 변화 감지)
- **완료**: 변환 1회당 commit 1개 + 30일 retention

**비용**: 소 (3~5 일)

#### P1.2 — healer 신뢰도 메트릭 *(B6 부분 해소)*

- `heal_stage` (fallback / local / dify) 별 성공/실패율 시계열 수집
- 운영 대시보드 (P3 의 일부) 의 핵심 위젯
- **완료**: 4주 이상 데이터 축적 후 stage 별 신뢰 구간 산출

**비용**: 중 (1~1.5 주)

#### P1.3 — 세션 retention 정책 *(W2 해소)*

- recording_service 의 7/30일 GC + 디스크 한도 가드
- `RECORDING_RETENTION_DAYS` env 로 조정
- **완료**: 디스크 사용량 상한 가시화 + GC 로그

**비용**: 소 (2~3 일)

#### P1.4 — LLM 출력 검증 강화 *(W4 해소)*

- LLM 이 만든 selector 의 사전 dry-run (target_url 에 실제 접속해 locator
  resolve 시도) → invalid step 거르고 reroll
- hallucinated value (예: 존재하지 않는 옵션 텍스트) 자동 거부
- **완료**: invalid step 비율 5% 이하

**비용**: 중 (1.5 주)

#### P1.5 — orphan codegen 자동 정리 *(W3 해소)*

- recording_service 가 5초 간격으로 alive handle 의 process 상태 polling
- dead handle 발견 시 즉시 `state=error, error="codegen 외부 종료"` 마킹
- **완료**: 외부 codegen kill 후 10초 안에 상태 반영

**비용**: 소 (2~3 일)

---

### P2 — LLM 자율도 향상 (Phase 2 진입, 예상 8~12주)

본 항목들은 [PLAN_GROUNDING_RECORDING_AGENT.md](PLAN_GROUNDING_RECORDING_AGENT.md)
의 Phase 1.5 / Phase 2 와 직접 대응한다. 본 문서에서는 운영 관점의 게이트 조건만
정리한다.

#### P2.1 — 모델 신뢰성 게이트 통과 *(B3 해소)*

- gemma4:26b 또는 대체 모델 (qwen2.5:32b / llama-3.3-70b 후보) 의 다중턴
  도구 호출 90% 신뢰도 검증
- **완료**: PLAN_GROUNDING_RECORDING_AGENT.md §"R-Plus 진입 게이트" 의 4 항목 통과

**비용**: 중 (2 주, 벤치마크 기간)

#### P2.2 — External Agent skeleton

- SRS → 다중턴 자율 탐색으로 시나리오 설계
- target_url 에 실제 접속하며 DOM 인벤토리 + RAG 자료를 결합한 의사결정 루프
- **완료**: 사내 시스템 1종에서 SRS only → 통과 시나리오 자동 생성 PoC

**비용**: 대 (4~6 주)

#### P2.3 — 실 도메인 안정성 벤치 *(B5 해소)*

- 5~10 개 SaaS / 사내 앱에서 회귀 1주 무중단 검증
- 매일 야간 회귀 + 다음 날 통과율 리포트
- **완료**: 통과율 90% 이상 4주 연속 유지

**비용**: 중 (2~3 주, 운영 기간)

---

### P3 — 영구 OUT 재확인

본 항목들은 PLAN_GROUNDING_RECORDING_AGENT.md §"명시적 OUT 항목" 에서 이미
영구 OUT 으로 결정됨. 본 로드맵에서도 동일하게 OUT 유지를 권고한다.

| 항목 | OUT 사유 |
| --- | --- |
| 시각 회귀 / 성능 / a11y | 별도 도구 위임이 정답 (Percy / Lighthouse / axe) |
| iframe 복합 시나리오 (Phase 3 이상) | 복잡도 대비 ROI 낮음. P0.2 의 단일-iframe 까지 |
| Dify SSE 스트리밍 UI | 2026-04-28 결정, 별도 운영 대시보드로 대체 |

---

## 결정 게이트

| 게이트 | 판단 시점 | 통과 기준 |
| --- | --- | --- |
| G0 → 운영 PoC 진입 | P0 5개 항목 완료 | 사내 시스템 1종에서 로그인 + iframe + 격리된 시나리오 5개 통과 |
| G1 → 운영 베타 | P0 + P1 완료 | 4주 회귀 무중단, healer 통계 + Git diff 가시화 |
| G2 → 운영 GA | P0 + P1 + P2.3 완료 | 5+ 도메인 4주 90%+ 통과, RCA 인프라 정상 |

---

## 리스크 / 미지수

1. **Playwright 의 frame piercing 한계** — Shadow DOM 의 closed mode 는 개발자
   도구로도 접근 불가. 일부 디자인 시스템 (Salesforce LWC 등) 은 closed shadow.
   범위 명시 필요.

2. **gemma4:26b 의 한국어 / 다중턴 신뢰도** — 검증 진행 중. 통과 못 시 Phase 2
   가 24주~ 단위로 지연.

3. **인증 방식의 다양성** — SAML / OIDC / WebAuthn / passkey / MFA 까지 가면
   P0.1 만으로 부족. 첫 PoC 는 OAuth + form 로그인 한정 권고.

4. **세션 격리의 백엔드 의존성** — 백엔드 fixture seed/reset 은 대상 시스템의
   admin API 또는 DB 접근권을 요구. 사내 앱은 가능, 외부 SaaS 는 한계.

---

## 다음 액션

1. 본 로드맵에 대한 사용자 승인/우선순위 재조정
2. P0.4 (converter AST 화) 부터 착수 권고 — 비용 적고 P0.1 의 전제
3. P0.5 (이미지 빌드 자동화) 와 P1.5 (orphan 정리) 는 백그라운드 small task 로
   동시 진행 가능

---

## 변경 이력

| 일자 | 작성자 | 내용 |
| --- | --- | --- |
| 2026-04-29 | Claude (feat/grounding-recording-agent) | 초안 작성 — 직접 검증 결과 + 6 블로커 / 5 취약점 / P0~P3 로드맵 |
