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

**범위 (보정 — 현실 가능 범위로 한정)**

대상에 포함:

- credential 주입 액션 신설 (DSL: `auth_login`, value=계정 alias)
- credential 저장소 (Jenkins Credentials + 컨테이너 안전 노출)
- **Form 로그인** (id/pw)
- **OAuth (Google/GitHub form 부분까지)** — redirect/callback 자체 추적
- **TOTP** — 시크릿 보관 + `pyotp` 6자리 자동 생성
- 인증 후 세션 쿠키를 `storage_state` 로 dump → 후속 시나리오 재사용

대상에서 제외 (도메인별 별도 PoC 또는 영구 OUT):

- ❌ **SMS OTP** — 전용 SMS gateway / mock 서비스 인프라 필요. 별도 트랙
- ⚠️ **WebAuthn / Passkey** — Playwright `virtualAuthenticator` API 가능하나
  prod IdP 가 virtual authenticator 거부할 수 있음. 도메인별 사전 협의 필요
- ❌ **reCAPTCHA / hCaptcha** — 봇 차단 의도라 우회 비추천. 테스트 환경에서
  disable 또는 test key 발급 (도메인 협조)
- ⚠️ **SAML / OIDC 사내 IdP** — 가능하나 IdP 화면이 회사별 → 도메인별 selector
  작성 필요. 첫 PoC 범위에서 제외
- ❌ **Magic Link (이메일)** — 메일박스 polling 인프라 필요. 별도 트랙

**완료 조건 (보정)**

- Form 로그인 + OAuth (Google) + TOTP, 3 가지 시나리오 자동 통과
- credential 이 로그/스크린샷에 노출 안 됨 (마스킹 검증)
- SMS / WebAuthn / SAML / Magic Link 는 본 P0.1 외 별도 PoC 트랙으로 명시

**비용**: 중-대 (2~3 주, 보정 범위 기준)

**의존성**: P0.4 (converter AST 화) 권장 — auth flow 는 popup/redirect 가 잦아
정확한 변환 필요

---

#### P0.2 — iframe / Shadow DOM 지원 *(B2 해소)*

**범위 (보정 — 현실 가능 범위로 한정)**

대상에 포함:

- 14-DSL 의 `target` 문법에 frame/shadow path 옵션 추가 (예: `frame=#iframe1>>role=button, name=확인`)
- `locator_resolver` 에 `frame_locator` 자동 traversal
- **단일 iframe** (Stripe / Toss 결제 위젯 등)
- **Open mode Shadow DOM** — Playwright 가 자동 piercing 하므로 기본 selector
  로 통과
- recording 측 codegen 도 frame 진입 라인 (`page.frame_locator(...).get_by_role(...)`) 보존

대상에서 제외 (영구 OUT 또는 별도 트랙):

- ❌ **Closed mode Shadow DOM** (Salesforce LWC 일부) — **브라우저 정책상 영구
  접근 불가**. 본 P0.2 의 OUT 으로 명시. 대상 시스템이 closed shadow 면 다른
  자동화 전략 (백엔드 API 호출 등) 으로 우회
- ⚠️ **깊게 중첩된 nested frame chain** — 가능하나 codegen 변환 복잡도 + healer
  신뢰도 저하. 별도 운영 데이터 기반 의사결정 (P2.3 벤치 통과 후 재평가)

**완료 조건 (보정)**

- 단일 iframe 결제 위젯 1종 + open shadow DOM 디자인 시스템 1종에서 fill/click/verify
  동작 확인
- frame_locator 진입 시 healer 도 같은 frame 안에서 fallback 수행
- closed shadow 만나면 명확한 에러 메시지 (`closed shadow root — automation 불가`)
  + 시나리오 즉시 FAIL 로 마감

**비용**: 중 (2 주, 보정 범위 기준)

**의존성**: 없음 (executor + converter 양쪽 동시 작업)

---

#### P0.3 — 세션 / 데이터 격리 *(B4 해소)*

**범위 (보정 — 클라이언트/백엔드 분리)**

P0.3-A — 클라이언트 측 격리 (확실히 가능, 도메인 무관):

- 시나리오 단위 `BrowserContext` 분리 (현재는 단일 page 재사용 가능성 점검)
- localStorage / IndexedDB / cookie 명시적 reset 액션 (DSL: `reset_state`)
- `BrowserContext.clear_cookies()` / `storage_state` dump+restore 활용

P0.3-B — 백엔드 fixture seed/reset hook (도메인 의존):

- 시나리오 메타데이터에 `setup_url` / `teardown_url` 필드 추가, executor 가
  step 0 / step ∞ 에서 호출
- 사내 앱: 가능 — admin API 노출 시키거나 DB seed 스크립트 작성
- ⚠️ 외부 SaaS: **거의 불가능** (Salesforce 등은 회귀용 reset API 미제공) →
  "스테이징 계정 분리 + 사람 주기 reset" 운영 절차로 보완 (P2.3 벤치 시 합의)

**완료 조건 (보정)**

- P0.3-A: 동일 시나리오 100회 연속 실행 (fixture HTML + 사내 앱) 통과율 95% 이상
- P0.3-A: 시나리오 A 가 시나리오 B 결과를 오염시키지 않음을 회귀 케이스로 증명
- P0.3-B: 사내 앱 1종에서 setup/teardown hook 동작 + 외부 SaaS 는 운영 절차
  문서화 (자동화 OUT 명시)

**비용**: 중 (P0.3-A 1주 + P0.3-B 사내 앱 1종 0.5주, 외부 SaaS 운영 절차 별도)

**의존성**: 없음 (P0.3-A 는 즉시 착수 가능 — 아래 §"당장 착수 가능한 상세 태스크" 참조)

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

## 당장 착수 가능한 상세 태스크

본 절의 4 개 태스크는 **외부 인프라/도메인 의존성 없음** + **현 코드베이스 안에서
완결** 으로, 이번 브랜치에서 즉시 착수 가능하다. 각 태스크는 **단계 / 변경 파일 /
단위 테스트 / 수락 기준 / 예상 시간** 을 명시한다. 우선 추천 순서: T-A → T-C → T-D → T-B.

---

### T-A — converter AST 화 (P0.4 본체)

**목표**: line-based regex 를 `ast.parse` 기반 정확 파싱으로 교체. popup/`.nth`/
`.first`/`.filter`/frame_locator chain 등 codegen 의 모든 변형을 손실 없이 14-DSL
로 전환.

**예상 시간**: 5 영업일 (1 주)

**의존성**: 없음

**단계**

1. **Day 1 — 측정 baseline**
   - 현 line-based converter 의 손실 패턴 8 개 (popup/.nth/.first/.filter/nested
     locator/frame_locator/expect_navigation/page2 chain) 을 fixture 로 codegen
     출력 샘플 8 개 수집
   - 각 샘플에 대해 현 converter 결과 + 기대 결과를 표로 정리 → `test/fixtures/codegen_corpus/`
2. **Day 2~3 — AST visitor 구현**
   - 신규 파일 `zero_touch_qa/converter_ast.py`:
     - `ast.NodeVisitor` 서브클래스 — top-level `def run(playwright)` body 를 순회
     - page 변수 스코프 추적 (dict[str, PageContext]) — `page = context.new_page()`
       / `page1 = page1_info.value` / `with page.expect_popup() as page1_info:`
       전부 처리
     - call chain 평탄화: `page.locator("a").nth(1).click()` → `(target, action)` 튜플
     - frame_locator chain 누적 (`page.frame_locator("#f").get_by_role("button")`)
   - `_extract_target_from_node` — `Call` 노드를 받아 14-DSL `target` 문자열 생성
3. **Day 3~4 — converter 라우팅**
   - 기존 `convert_playwright_to_dsl` 의 라인 루프를 AST 우선 + 실패 시 line fallback
     으로 교체:

     ```python
     try:
         scenario = _convert_via_ast(file_path, output_dir)
     except Exception as e:
         log.warning("[Convert] AST 변환 실패 — line fallback. 사유: %s", e)
         scenario = _convert_via_lines(file_path, output_dir)  # 현 함수
     ```

   - line fallback 은 그대로 유지 — 비표준 codegen 출력에 대한 안전망
4. **Day 4 — `.nth(N)` / `.filter(...)` 보존 → DSL 확장**
   - 14-DSL `target` 에 `, nth=1` / `, has_text=...` 옵션 추가
   - executor 의 `locator_resolver` 에서 nth/filter 옵션 처리
5. **Day 5 — 테스트 + 문서**
   - `test/test_converter_ast.py` 30 케이스 — 8 fixture × 평균 4 단계
   - 기존 [zero_touch_qa/converter.py:51-57](zero_touch_qa/converter.py#L51-L57) 의 정규화 hotfix 는 AST
     visitor 도입 시 **자동 무력화** (제거하지 않고 line fallback 에 그대로 둠)
   - `docs/recording-troubleshooting.md` §4-2 의 ".nth backlog" 항목 closed 처리

**변경 파일**

- 신규: `zero_touch_qa/converter_ast.py` (~400 라인 예상)
- 신규: `test/test_converter_ast.py`
- 신규: `test/fixtures/codegen_corpus/` (8 샘플 + 각 expected.json)
- 수정: `zero_touch_qa/converter.py` — entry point 가 AST 우선 + line fallback
- 수정: `zero_touch_qa/locator_resolver.py` — nth/filter 옵션 추가
- 수정: `docs/recording-troubleshooting.md` §4-2

**단위 테스트 (필수 통과)**

- 기존 18 fixture 의 codegen 출력 손실 0
- `.nth(1)` / `.first` / `.filter(has_text="...")` 보존 확인 (3 케이스)
- popup chain (`with page.expect_popup() as p1_info` → `page1.click(...)`) 정확히
  click 액션으로 변환 + page 컨텍스트 정보 메타에 보존 (1 케이스)
- frame_locator chain (`page.frame_locator("#f").get_by_role("button").click()`)
  → DSL `target=frame=#f>>role=button` (2 케이스)
- 비표준 패턴 (lambda, 변수 별칭) → line fallback 으로 자연스러운 degrade (3 케이스)

**수락 기준**

- naver popup 시나리오 (이번 세션 6 스텝) 정확 변환 + nth 정보 메타에 보존
- 8 fixture 에서 변환 손실 0
- pytest 전체 스위트 208 → 238 passed (30 신규 케이스 추가)

---

### T-B — 클라이언트 측 세션 격리 (P0.3-A)

**목표**: 시나리오 단위 BrowserContext 분리 + `reset_state` DSL 액션 추가.
백엔드 hook 없는 자체 완결.

**예상 시간**: 5 영업일

**의존성**: 없음

**단계**

1. **Day 1 — BrowserContext per scenario 검증**
   - 현 [zero_touch_qa/executor.py:120-130](zero_touch_qa/executor.py#L120-L130) 의
     context 생성 흐름 추적 — 시나리오 1개당 1 context 인지, 여러 시나리오 batch
     실행 시 context 재사용 가능성 있는지 확인
   - regression: 동일 시나리오 100회 연속 → 통과율 측정 (baseline)
2. **Day 2 — `reset_state` DSL 액션 신설**
   - `zero_touch_qa/converter.py` 의 14-DSL 액션 매핑에 `reset_state` 추가:
     - `value=cookie` — `context.clear_cookies()`
     - `value=storage` — `page.evaluate("() => localStorage.clear(); sessionStorage.clear();")`
     - `value=indexeddb` — `page.evaluate(deleteAllIDB)`
     - `value=all` — 위 3 개 + permissions reset
   - `zero_touch_qa/executor.py` 에 `_handle_reset_state` 추가
3. **Day 3 — `storage_state` dump/restore**
   - 시나리오 메타데이터에 `storage_state_in` / `storage_state_out` (선택) 필드
   - executor 가 시작 시 restore, 끝나면 dump
4. **Day 4 — 멱등성 회귀 케이스**
   - `test/test_isolation.py` 신설:
     - fixture HTML 에서 시나리오 A (localStorage 에 값 쓰기) 실행 후 시나리오 B
       (해당 값이 없음을 verify) 가 100회 연속 통과해야 함
     - cookie / IndexedDB 도 동일
5. **Day 5 — 측정**
   - 동일 시나리오 100회 연속 → 통과율 측정 (post-fix). 95% 이상이면 수락

**변경 파일**

- 수정: `zero_touch_qa/executor.py` — `_handle_reset_state` + storage_state 처리
- 수정: `zero_touch_qa/converter.py` — `reset_state` DSL 매핑 + 18 fixture 의
  `metadata` 스펙 갱신
- 수정: `zero_touch_qa/__main__.py` — `storage_state_in/out` CLI 옵션 (선택)
- 신규: `test/test_isolation.py`
- 신규: `test/fixtures/isolation_a.html`, `isolation_b.html`

**단위 테스트**

- `reset_state value=cookie` 후 cookie 비어 있음 (1)
- `reset_state value=storage` 후 localStorage 비어 있음 (1)
- 동일 시나리오 100회 연속 통과율 ≥95%
- A → B 격리 회귀 (10 케이스)

**수락 기준**

- 100회 연속 통과율 ≥95% 측정 데이터 첨부
- A→B 오염 0 건

---

### T-C — orphan codegen 자동 정리 (P1.5)

**목표**: codegen 이 외부 요인(브라우저 직접 닫기/크래시)으로 죽었을 때 즉시
세션을 `state=error` 로 마킹. 서버 재시작에 의존하지 않음.

**예상 시간**: 2 영업일

**의존성**: 없음

**단계**

1. **Day 1 오전 — heartbeat 스레드**
   - `recording_service/server.py` 에 `_handle_watchdog` 모듈 신설:
     - `threading.Thread(daemon=True)` 가 5 초 간격으로 `_handles` 순회
     - 각 handle 의 `proc.poll()` 호출, `not None` (즉 종료됨) 이면 즉시 cleanup:
       - `_pop_handle(sid)` 로 dict 에서 제거
       - 출력 파일 크기 검사 → 0 이면 `state=error, error="codegen 외부 종료 + 액션 0"`,
         >0 이면 `state=error, error="codegen 외부 종료 (자동 변환 미수행)"`
       - storage.save_metadata(sid, ...) 로 디스크 동기화
2. **Day 1 오후 — startup hook 통합**
   - 기존 `_absorb_disk_sessions` 직후 watchdog 시작
   - `@app.on_event("shutdown")` 에서 watchdog stop
3. **Day 2 — 단위 테스트**
   - `test/test_recording_service.py` 에 케이스 추가:
     - codegen subprocess fake (sleep 1초 후 자살) → 5초 polling 후 state=error 확인
     - 정상 codegen + Stop & Convert 호출 → watchdog 와 race 안 발생
4. **Day 2 후 — 문서 갱신**
   - `docs/recording-troubleshooting.md` §4-1 임시 우회 절 → "watchdog 가 자동 마킹"
     으로 갱신

**변경 파일**

- 신규: `recording_service/watchdog.py` (~80 라인)
- 수정: `recording_service/server.py` — startup/shutdown hook 통합
- 수정: `test/test_recording_service.py` — 2 케이스 추가
- 수정: `docs/recording-troubleshooting.md` §4-1

**단위 테스트**

- 외부 SIGKILL 후 10초 이내 state=error 마킹 (1)
- 정상 stop 흐름과 watchdog race 없음 (1)

**수락 기준**

- 외부 codegen kill 시 10초 안에 세션 `state=error`
- 기존 stop endpoint 동작 변동 없음 (회귀 0)

---

### T-D — recording 세션 retention GC (P1.3)

**목표**: `~/.dscore.ttc.playwright-agent/recordings/` 의 오래된 세션 자동 정리.
디스크 무한 증가 방지.

**예상 시간**: 2 영업일

**의존성**: 없음

**단계**

1. **Day 1 — GC 로직**
   - `recording_service/storage.py` 에 `gc_old_sessions(retention_days: int)` 추가:
     - 각 세션 디렉토리의 metadata.json 의 `created_at` 기준 retention 초과 제거
     - state=recording (활성) 은 제외 — watchdog 가 cleanup 후 GC 가 처리
     - 디스크 한도 (`RECORDING_DISK_LIMIT_MB` 기본 5000) 초과 시 가장 오래된 세션
       부터 추가 삭제
   - `RECORDING_RETENTION_DAYS` 기본 30, env 로 조정
2. **Day 1 후 — startup hook + 일별 스케줄**
   - startup 에서 1회 GC, 이후 24시간마다 backgound thread 로 반복
3. **Day 2 — 테스트**
   - 임시 디렉토리에 가짜 세션 5 개 (각각 1/15/31/45/60 일 전 created_at) 생성
   - retention=30 으로 GC → 31/45/60 일 전 세션만 삭제 확인
   - 디스크 한도 초과 시뮬레이션 → 가장 오래된 세션부터 삭제 확인

**변경 파일**

- 수정: `recording_service/storage.py` — `gc_old_sessions` 추가
- 수정: `recording_service/server.py` — startup + 24h 주기
- 수정: `test/test_recording_service.py` — 3 케이스 추가
- 수정: `docs/recording-troubleshooting.md` §9 (로그 위치) 다음에 GC 정책 추가

**단위 테스트**

- retention=30 일 경계 케이스 (3)
- 디스크 한도 초과 시 LRU 삭제 (1)
- 활성 세션 (state=recording) 보존 (1)

**수락 기준**

- 30일 초과 세션 자동 삭제 + GC 로그 출력
- 디스크 사용량 한도 가시화

---

### 즉시 착수 권고 순서

T-A 부터 시작 권고. 이유:

1. **블로커 해소 효과 가장 큼** — popup 누락 fix 의 정식 후속, 다른 P0 (P0.1
   인증 흐름의 popup/redirect 정확 변환) 의 전제
2. **외부 의존성 0** — 코드베이스 + pytest 만으로 완결
3. **회귀 안전망 확보됨** — 208 passing 스위트 + 8 corpus fixture 로 손실 측정 가능

T-A 끝나면 T-C / T-D (각각 2일, 백그라운드 가능) → T-B (1주, P0.3-A 본체).

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
2. **§"당장 착수 가능한 상세 태스크"** 의 T-A (converter AST 화) 부터 착수 권고
   — 비용 적고 P0.1 의 전제, 외부 의존성 0
3. T-A 완료 후 T-C (orphan watchdog, 2일) + T-D (retention GC, 2일) 을 백그라운드
   small task 로 동시 진행
4. 그 다음 T-B (P0.3-A 클라이언트 격리, 1주)

---

## 변경 이력

| 일자 | 작성자 | 내용 |
| --- | --- | --- |
| 2026-04-29 | Claude (feat/grounding-recording-agent) | 초안 작성 — 직접 검증 결과 + 6 블로커 / 5 취약점 / P0~P3 로드맵 |
| 2026-04-29 | Claude (feat/grounding-recording-agent) | P0.1/P0.2/P0.3 범위·완료조건을 현실 가능 범위로 보정 (form+OAuth+TOTP / 단일 iframe + open shadow / 클라이언트 vs 백엔드 분리) + §"당장 착수 가능한 상세 태스크" 신설 (T-A/B/C/D 4 개 태스크의 단계·변경파일·테스트·수락기준·예상시간) |
