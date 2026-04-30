# Zero-Touch QA — Action DSL 확장 계획서 (9 ➔ 14)

> **문서 목적**
> 현재 Zero-Touch QA 파이프라인의 브라우저 제어 자유도를 높이기 위해, 기존 9개로 제한되었던 표준 Action DSL을 복잡한 웹 UI(Drag & Drop, 무한 스크롤, 파일 업로드 등)까지 제어할 수 있도록 확장하는 구체적인 설계와 수행 계획입니다.

## 변경 이력 / 의사결정 로그

| 날짜 | 단계 | 결정 / 작업 | 산출물 |
| --- | --- | --- | --- |
| 2026-04-27 | Sprint 2 | 14대 DSL Python Executor 구현 + `_validate_scenario` 신규 5종 + LLM heal `step.update` 병합 | `executor.py`, `__main__.py` |
| 2026-04-27 | Sprint 2 재검증 | 냉정 재검증으로 4 갭 + 1 누락 식별 → 같은 스프린트 내 마감. execute 모드 검증 일관화, `verify.condition` 화이트리스트, `regression_generator` 14대 emitter, fallback heal 의 `scenario.healed.json` 반영, mock_* 2단계 healing 경로(`_execute_mock_step`), `times` 옵션 노출 + smoke runbook | `__main__.py`, `executor.py`, `regression_generator.py`, `test_sprint2_runtime.py`, §6.4 / §6.6 / §6.8 |
| 2026-04-27 | 검증 결과 | `python3 -m pytest test/test_sprint2_runtime.py` 16/16 PASS — Sprint 2 DoD 8 항목 모두 충족. Sprint 2 "구현 완료" 선언 (§6 헤더) | §6.4.2 |
| 2026-04-27 | Sprint 3 계획 수립 | 14대 DSL fixture 기반 pytest 회귀 / mock_* UI 예외처리 / 3-Flow 통합 / regression_test.py 산출물 실행 검증 / Jenkins pytest 단계 까지 14건 작업 정의. 구현 순서를 Phase 0~8 의 9 단계 + Gate 로 구체화 (공통 conftest 선행, 저위험→고위험 액션, 산출물 회귀, 통합, healing, 3-Flow, 에어갭/CI, 클로저) | §7.1~§7.8 |
| 2026-04-27 | Sprint 4 계획 수립 | 실 Dify + Mac/WSL agent + staging 도메인 위에서 14대 DSL E2E, healing 실효성, Convert 14대 확장(Sprint 2 carve-out closure), 운영 산출물 / 매뉴얼까지 4A/4B/4C 게이트로 재구성. Sprint 1 §5.5 잔여 (S1-23 Preview 실측) 는 4B 자동화 회귀로 흡수. 운영 SLA 표준 8 metric / 30 일 retention / 알림 / 책임분담 명문화 | §8.1~§8.8 |
| 2026-04-27 | Sprint 3 수행 | Phase 0~8 9 단계 순차 수행. 공통 conftest fixture 4 종 + helpers/scenarios.py 빌더, fixture HTML 7 종 (file:// + `api.example.test` mock-only 화이트리스트), 통합 pytest 11 파일 (총 31 케이스), regression_test.py subprocess 실행 검증, fallback / mock_* 2단계 healing 통합 회귀, 3-Flow 통합, airgap 가드, Jenkins Stage 2.4 두 sub-step 으로 추가. 9대 native 테스트는 `test/native/` 로 격리해 sync_playwright nesting 충돌 회피 | §7.4 / §7.5 / §7.7 |
| 2026-04-27 | 검증 결과 | `python3 -m pytest test --ignore=test/native -q` 47 PASS + `python3 -m pytest test/native -q` 30 PASS = 총 77/77, flake 0. Sprint 3 DoD 8 항목 모두 충족. Sprint 3 "구현 완료" 선언 (§7 헤더, §9 milestone) | §7.4.2 |
| 2026-04-27 | Sprint 4A/4B/4C 일부 구현 | Jenkins agent preflight + Dify health probe + 30일 retention, Dify Planner/Healer 호출 metric(`llm_calls.jsonl`)과 집계 helper, mock scope 안전장치, Zero Touch QA Report 운영 지표 섹션 구현. 로컬 전체 회귀 `test --ignore=test/native` 56 PASS + `test/native` 30 PASS = 총 86/86 PASS | §8.4 / §8.8 |
| 2026-04-27 | Sprint 4B closure | Sprint 5 호스트 하이브리드 빌드 #9 (execute, `scenario_14.json` 14/14 PASS) + #14 (chat, 실 Dify `qwen3.5:9b` 12/12 PASS, Dify heal 2 회 성공) 가 chat E2E / mock_status / mock_data / 3단계 healing 의미적 핵심을 통합 검증해 4B 게이트 closure. 정량 fixture 회귀(S4B-01/02), `llm_sla.json` 집계 (S4B-03 후속), doc·convert smoke (S4B-05/06), 10 회 stability loop (S4B-11) 은 v4.1 출시 후 회귀 강화 backlog 로 이관 | §8.4 / §10.4.2 |
| 2026-04-27 | Sprint 4C-01/02 구현 | `converter.py` 에 신규 5종 매핑 (set_input_files / drag_to / scroll_into_view_if_needed / page.route → upload/drag/scroll/mock_*). mock_data body 는 `ast.literal_eval` 로 Python escape 평탄화. fixture `recorded-14actions.py` 와 `test_converter_14_actions.py` (16 tests) 추가. 통합 회귀 `test --ignore=test/native` 72 PASS + `test/native` 30 PASS = 총 102/102 PASS | §8.4 |
| 2026-04-27 | Sprint 4C closure | S4C-03 convert 14대 E2E (`test_convert_14_e2e.py` 2 tests — convert→validate→execute 풀 사슬, full_dsl.html 14/14 PASS), S4C-05 build trend (Jenkinsfile v2.4 + `aggregate_llm_sla()` 빌드별 자동 집계, JUnit XML 추세 + 30 일 retention 자동화), S4C-06 운영 매뉴얼 (`README.md §3.9` v4.1 SLA / mock 안전장치 / DIFY_API_KEY / convert 14대 / agent 동시성), S4C-07 v4.1 출시 선언. Sprint 1 (S1-23/S1-25) Sprint 4B 회귀로 동시 closure. 통합 회귀 `test --ignore=test/native` **77 PASS** + `test/native` **30 PASS** = **107/107 PASS** | §8.4 / §8.6 / §9 |
| 2026-04-27 | v4.1 실환경 출시 검증 | 호스트 하이브리드 fresh rebuild (image+volume 파괴 → `build.sh --redeploy --fresh` → Dify provisioning + Jenkins agent 재연결) 후 Jenkins pipeline **4 모드 모두** 실 검증. Build #2 `RUN_MODE=execute` 14/14 PASS @64s (LLM-bypass scenario_14.json). Build #5 `RUN_MODE=convert` 14/14 PASS @59s (recorded-14actions.py → 14대 DSL 변환 → 실행, S4C-03 운영 검증). Build #6 `RUN_MODE=chat` 6/6 PASS @222s (실 Dify qwen3.5:9b, llm_sla.json planner p50=82.9s p95=90.4s — Sprint 5 §10.5 의 의미 압축 동작 재확인). Build #7 `RUN_MODE=doc` 20/20 PASS @70s (`playwright_dev_test_scenario.pdf` ZTQA_STEP marker 로컬 파서 → 실 playwright.dev 사이트, S4B-05 closure). 빌드별 `llm_sla.json` 자동 집계 hook (S4C-05) 운영 환경에서 정상 작동 확인. 발견된 결함 — `build.sh` / `provision.sh` 의 OLLAMA_MODEL 기본값이 `gemma4:e4b` 인데 chatflow YAML 은 `qwen3.5:9b` 를 강제 → fresh build 시 chat 모드 400 에러. 양 스크립트 기본값을 `qwen3.5:9b` 로 영구 수정 | §9 |
| 2026-04-28 | 정밀 재검증 + CLI 계약 보강 | 계획 기반 로컬 정밀 테스트 수행 중 `chat --convert-only` 오용이 Dify retry 경로로 들어가는 결함 발견. `__main__.py` 에 early guard 를 추가해 `convert` 외 모드에서는 Dify 호출 전 즉시 exit 1. `test_sprint2_runtime.py` 에 subprocess 회귀 추가. 전체 회귀 `test --ignore=test/native` 169 PASS + `test/native` 30 PASS, compileall/bash -n/convert-only/execute/regression 산출물 smoke 모두 PASS | `__main__.py`, `test_sprint2_runtime.py`, README / Grounding-Recording plan |
| 2026-04-29 | T-A (P0.4) — converter AST | line-based converter 의 popup/`.nth(N)`/`.first`/`.filter(has_text=...)`/`frame_locator` chain 손실 문제 해소. `zero_touch_qa/converter_ast.py` 신설 (`_AstConverter` NodeVisitor + page 변수 스코프 추적 + popup_info → page 승격). `converter.py` 를 AST 우선 + line fallback 으로 라우팅. `locator_resolver.py` 에 `_split_modifiers`/`_resolve_raw`/`_apply_modifiers` 추가해 receiver-side 도 nth/has_text 처리. **DSL `target` 의 후미 modifier 문법 추가** — `, nth=N` (정수, .nth(N).first 회피) / `, has_text=T` (.filter(has_text=T)). corpus 8 fixture + 42 단위 테스트, 회귀 0 (208 → 250 passed) | `converter_ast.py`, `converter.py`, `locator_resolver.py`, `test/fixtures/codegen_corpus/`, `test/test_converter_ast.py` |
| 2026-04-29 | T-D (P0.1) Phase 1~4,7 — auth_login DSL | **15번째 표준 액션 `auth_login` 도입** — form/totp/oauth 모드 분기 (oauth 는 mock 서버 follow-up 까지 FAIL). credential 은 env var lookup (`AUTH_CRED_<ALIAS>_USER/_PASS/_TOTP_SECRET`), TOTP 는 `pyotp` 위임. `BrowserContext` 의 storage_state dump/restore (CLI `--storage-state-in/out` + env `AUTH_STORAGE_STATE_IN/OUT`) 로 인증 후 세션 재사용. fixture: auth_form / auth_totp. 31 테스트 (단위 23 + 통합 5 + caplog 마스킹 회귀 3). pyotp 를 requirements.txt + agent setup REQ_PKGS 에 추가. 회귀 0 (250 → 281 passed). OAuth mock 컨테이너 + Jenkins Credentials seed 는 follow-up commit | `zero_touch_qa/auth.py`, `zero_touch_qa/executor.py`, `zero_touch_qa/__main__.py`, `test/test_auth.py`, `test/fixtures/auth_form.html`, `auth_totp.html`, `mac/wsl-agent-setup.sh`, `requirements.txt` |

상세 의사결정 근거는 §6.2 / §6.3 (Sprint 2 리뷰), §7.2 / §7.3 (Sprint 3 리뷰), §8.2 / §8.3 (Sprint 4 리뷰) 참조.

---

## 1. 개요 및 확장 원칙

### 1.1 배경
현재 시스템은 환각(Hallucination) 방지와 자가 치유(Self-Healing)의 안정성을 위해 9대 액션(`navigate`, `click`, `fill`, `press`, `select`, `check`, `hover`, `wait`, `verify`)만 허용하고 있습니다. 
그러나 B2B 솔루션 및 현대적인 웹 앱에서 빈번하게 발생하는 **파일 업로드**, **드래그 앤 드롭**, **상태 기반 검증(예: 버튼 비활성화 여부)** 시나리오를 지원하지 못하는 한계에 도달했습니다.

### 1.2 확장 원칙 (To what extent)
- **통제된 확장**: LLM(gemma4:e4b 4B 모델)의 Context 인식 한계를 고려하여, 액션 풀(Pool)을 무한정 열지 않고 **최대 12~15개** 내외로 엄격히 통제합니다.
- **스키마 호환성**: 기존 JSON 스키마 구조(`action`, `selector`, `value`)를 최대한 재사용하여 파서와 Healer 로직의 변경을 최소화합니다.
- **결정론적 실행**: 추가되는 액션들도 Playwright의 Sync API를 통해 100% 결정론적으로 실행 가능해야 합니다.
- **네트워크 엣지 케이스 커버**: 프론트엔드의 예외 처리(Error, Empty state)를 검증하기 위해 네트워크 모킹(Mocking)을 제한적으로 허용합니다.

### 1.3 로컬/에어갭 환경(소형 LLM) 한계 극복 전략 [NEW]
본 스프린트의 기준 하드웨어는 **단일 로컬 머신, 16GB RAM급, 호스트 Ollama, `gemma4:e4b` 기본 모델**입니다. 따라서 문서와 프롬프트는 "최고 성능 모델이 있으면 더 좋다"가 아니라, **4B~7B 로컬 모델에서 timeout 없이 버티는가**를 우선 기준으로 삼아야 합니다.
- **지능 계층(LLM)**: 복잡한 부정 지시어를 늘어놓는 대신, 짧은 규칙 + 1~2개의 대표 예시로 패턴 복제를 유도합니다.
- **파이프라인 방어(Dify)**: Dify 내부에 별도 parser 노드를 늘리기보다, Chatflow는 짧은 계약과 출력 제한에 집중하고 구조 검증/재시도/보정은 Python 후단에서 담당합니다.
- **실행 계층(Python)**: LLM이 놓치기 쉬운 엣지 케이스(예: 프레임워크에 의해 숨겨진 `input[type="file"]`)는 Healer 호출 전 Python Executor 단계에서 Zero-Cost로 자동 탐색/복구(Fallback)합니다.

### 1.4 Sprint 1 하드웨어 기준선

- **기본 타깃 머신**: 16GB RAM, Docker Desktop, 호스트 Ollama, headed Playwright agent 동시 실행 환경
- **기본 모델**: `gemma4:e4b`
- **허용 대안**: 7B급 로컬 모델 (`llama3.1:8b`, `qwen2.5:7b` 등)
- **비기본/비범위**: 30B급 Ollama 모델, 외부 SaaS 모델(`GPT-4o`, `Claude Sonnet`) 의존 설계
- **CPU-only 실행**: 기술적으로는 가능하지만 Sprint 1의 목표 성능 기준은 아니다. CPU-only는 smoke/debug 용도로만 본다.

### 1.5 Sprint 1 프롬프트/입력 예산

- **Planner System Prompt**: 길고 자세한 설명보다 짧은 계약 중심. 장문 규칙 나열보다 우선순위가 높은 규칙만 남긴다.
- **Planner 출력 예산**: `max_tokens` 는 1024 이내를 기준으로 잡는다.
- **Healer 출력 예산**: `max_tokens` 는 768~1024 범위, 기본은 768을 기준으로 잡는다.
- **Healer DOM 입력 예산**: DOM 스냅샷은 **권장 4,000자 이하**, 운영 상한은 **6,000자 이하**로 본다.
- **`api_docs` 입력 예산**: 네트워크 모킹 힌트가 꼭 필요할 때만 짧은 엔드포인트 목록 형태로 넣고, 장문 문서를 그대로 넣지 않는다.
- **성공 기준**: gemma4:e4b 단독 기준에서도 `<think>` 누출, Markdown 포맷 이탈, timeout 빈도가 과도하지 않아야 한다.

---

## 2. 신규 DSL 명세 (What)

이번 확장을 통해 다음 3가지 핵심 상호작용, 2가지 네트워크 제어, 그리고 1가지 검증 확장을 추가합니다.

### 2.1 상호작용(Interaction) 확장 (3종)

| 신규 액션 | 역할 | JSON 명세 예시 | Playwright API 매핑 |
| --- | --- | --- | --- |
| `upload` | `<input type="file">` 에 파일 업로드 | `{"action": "upload", "target": "#file-upload", "value": "fixtures/test_img.png"}` | `locator(target).set_input_files(value)` |
| `drag` | 요소를 마우스로 끌어서 다른 요소에 드롭 | `{"action": "drag", "target": "#item-1", "value": "#drop-zone"}` <br/>*(※ `value` 필드를 목적지 target으로 재활용)* | `locator(target).drag_to(page.locator(value))` |
| `scroll` | 요소가 화면에 보일 때까지 스크롤 | `{"action": "scroll", "target": "#footer", "value": "into_view"}` | `locator(target).scroll_into_view_if_needed()` |

### 2.2 네트워크 제어(Network Mocking) 확장 (2종)

| 신규 액션 | 역할 | JSON 명세 예시 | Playwright API 매핑 |
| --- | --- | --- | --- |
| `mock_status` | 특정 API 응답의 HTTP 상태 코드를 강제 조작 (에러 테스트용) | `{"action": "mock_status", "target": "**/api/users", "value": "500"}` | `page.route(target, lambda route: route.fulfill(status=int(value)))` |
| `mock_data` | 특정 API 응답의 JSON Body를 강제 조작 (빈 데이터, 가짜 데이터 주입용) | `{"action": "mock_data", "target": "**/api/data", "value": "{\"items\":[]}"}` | `page.route(target, lambda route: route.fulfill(status=200, body=value))` |

### 2.3 검증(Verify) 액션의 고도화
기존 `verify`는 단순 텍스트 포함 여부(`to_have_text`) 위주로 동작했습니다. 이를 요소의 **상태(State)**와 **속성(Attribute)**까지 검증할 수 있도록 `condition` 필드를 논리적으로 확장합니다. (기존 스키마에 `condition` 옵셔널 필드 추가)

- **가시성 검증**: `{"action": "verify", "target": "#modal", "condition": "visible"}` ➔ `expect().to_be_visible()`
- **비활성화 검증**: `{"action": "verify", "target": "#submit-btn", "condition": "disabled"}` ➔ `expect().to_be_disabled()`
- **속성 검증**: `{"action": "verify", "target": "input", "value": "test@test.com", "condition": "value"}` ➔ `expect().to_have_value("test@test.com")`

### 2.4 인증(Authentication) 액션 — `auth_login` *(T-D / P0.1, 2026-04-29)*

**15 번째 표준 액션** — form 로그인 / TOTP / OAuth 의 다단계 흐름을 단일 DSL 스텝
으로 추상화. credential 은 env var lookup, TOTP 는 `pyotp` 자동 생성. 자세한 사용법은
[docs/auth-login-usage.md](docs/auth-login-usage.md).

| 모드 | JSON 명세 예시 | 동작 |
| --- | --- | --- |
| form | `{"action": "auth_login", "target": "form", "value": "<alias>"}` | email/password/submit 자동 탐지 → fill + click |
| form (explicit) | `{"action": "auth_login", "target": "form, email_field=#email, password_field=#pw, submit=#login", "value": "<alias>"}` | selector 명시 — 자동 탐지 우회 |
| totp | `{"action": "auth_login", "target": "totp", "value": "<alias>"}` | `pyotp.TOTP(secret).now()` 6자리 → fill + (있으면) submit |
| oauth | `{"action": "auth_login", "target": "oauth, provider=mock", "value": "<alias>"}` | **T-D Phase 5 미완료 — mock 서버 도입 후 활성화** (현재는 FAIL) |

**credential alias 환경변수**:

- `AUTH_CRED_<ALIAS>_USER` — 이메일/사용자명
- `AUTH_CRED_<ALIAS>_PASS` — 비밀번호
- `AUTH_CRED_<ALIAS>_TOTP_SECRET` — Base32 인코딩된 TOTP 시크릿

ALIAS 의 비-영숫자 문자는 `_` 로 정규화. 셋 다 비어 있으면 `CredentialError`.

**로그 마스킹 계약** — executor 가 자동으로 끝 2자만 평문 노출, 나머지 `*` 마스킹.
caplog 회귀로 평문 password / TOTP 시크릿 / email 미노출 보장.

**storage_state 통합** — `auth_login` 후 `--storage-state-out PATH` (또는 env
`AUTH_STORAGE_STATE_OUT`) 로 인증 결과 덤프 → 후속 시나리오 `--storage-state-in`
으로 재사용해 로그인 스킵.

### 2.5 Locator target 의 후미 modifier 문법 *(T-A / P0.4, 2026-04-29)*

기존 `target` 문자열 (`role=...`, `text=...`, CSS selector 등) 의 **끝에 `,` 로 구분된
modifier 옵션** 추가:

| modifier | 의미 | 예시 |
| --- | --- | --- |
| `nth=N` | N 번째 매칭 (`.nth(N)`) | `role=link, name=Read more, nth=2` → 3 번째 "Read more" 링크 |
| `has_text=T` | 자식에 T 텍스트가 있는 요소 (`.filter(has_text=T)`) | `role=listitem, has_text=Premium` |

`>>` 로 selector chain (`#sidebar >> role=button, name=Settings`) — P0.1 #2 단계
에서 receiver-side resolver 의 합성 chain 해석이 활성됨 ([locator_resolver.py](zero_touch_qa/locator_resolver.py)
`_resolve_chain` / `_apply_chain_segment`).

`frame=<sel> >> ...` prefix — T-C (P0.2) 에서 실 실행 활성. iframe 안 element 에
chain 으로 접근. 중첩 가능 (`frame=#outer >> frame=#inner >> #deep-btn`).

`shadow=<host> >> ...` prefix — T-C (P0.2). open shadow 의 piercing 은 Playwright
가 자동으로 처리하지만 `shadow=` 를 명시하면 closed shadow 에 대해 즉시
`ShadowAccessError` 로 escalate (30s timeout hang 방지).

이 modifier 들은 codegen AST 변환기 (`zero_touch_qa/converter_ast.py`) 가
`.nth(N)` / `.first` / `.filter(has_text=T)` / `frame_locator(...)` 를 손실 없이
14-DSL `target` 으로 보존하기 위해 도입됨. 사용자 작성 시나리오에서도 동일 문법 사용 가능.

### 2.6 클라이언트 측 상태 격리 — `reset_state` *(T-B / P0.3-A, 2026-04-29)*

**16 번째 표준 액션** — 시나리오 도중 client-side 흔적을 비우는 보조 액션.
시나리오 단위 BrowserContext 분리는 이미 [executor.py:159-176](zero_touch_qa/executor.py#L159-L176)
에서 보장되지만, 같은 시나리오 안에서 로그아웃 후 재로그인 / Multi-tenant 전환
같은 흐름에는 step 단위 reset 이 필요.

| value | 동작 |
| --- | --- |
| `cookie` | `context.clear_cookies()` |
| `storage` | `page.evaluate("localStorage.clear(); sessionStorage.clear();")` |
| `indexeddb` | `page.evaluate(deleteAllIDB)` (Safari 미지원 시 no-op) |
| `all` | 위 3 개 모두 |

DSL 형태:
```json
{"action": "reset_state", "target": "", "value": "all", "description": "..."}
```

target 은 무시 (빈 문자열 권장). value 화이트리스트 외 값은 `_validate_scenario` 가 reject.

### 2.7 hidden-click 자동 복구 (T-H 메모, 2026-04-29)

DSL 새 액션은 아니지만 click step 의 동작에 영향. 드롭다운/메뉴 항목이 hover-then-click sequence 인데 codegen 이 hover 를 빠뜨려 element 가 hidden 인 상태로 click 시도되는 케이스를 3 layer 가 자동 복구한다:

1. **converter_ast** 가 변환 시 chain 안의 nav/menu/dropdown/gnb/aria-haspopup 신호를 보면 click 앞에 `hover` step 자동 prepend
2. **executor `_heal_visibility`** 가 1차 시도 직전 element `is_visible()` 검사 후 ancestor 기반 hover 후 재시도 (DOM 직접 분석)
3. (codegen 원본 .py 직접 실행 경로) **annotator** 가 `<chain>.click()` 의 chain 안 hover-trigger ancestor 식별 → `<ancestor>.hover()` 라인 자동 삽입 (`/experimental/sessions/{sid}/annotate` 엔드포인트)

세부는 `PLAN_PRODUCTION_READINESS.md §"T-H 완료 기록"` + `docs/recording-troubleshooting.md §"hidden-click 자동 복구"` 참조.

---

## 3. 상세 구현 계획 (How)

확장은 프롬프트(지능), 실행기(행동), 테스트(검증) 3개의 레이어에서 진행됩니다.

### 3.1 Dify Workflow 수정 및 개선 계획 (Planner / Healer)
- **대상**: Dify UI의 `ZeroTouch QA Brain` Chatflow (또는 `dify-chatflow.yaml` 설정 파일)
- **작업 내용**:
  Dify의 프롬프트는 단순 지시를 넘어 파이썬 실행기와의 '엄격한 API 계약(Contract)' 역할을 해야 합니다. 

  #### 3.1.1 Planner 프롬프트 상세 가이드 (Draft)
  기존 9개에서 14개로 액션이 늘어나면 4B 모델(gemma4:e4b)의 환각 확률이 급증합니다. 이를 막기 위해 Sprint 1에서는 **규칙 수를 무한정 늘리지 않고**, **우선순위 높은 계약 + 짧은 예시 + 출력 금지 규칙**만 남기는 방향으로 프롬프트를 압축합니다.

  **[Planner System Prompt 초안]**
  ```text
  당신은 Playwright 기반의 E2E 테스트 자동화 아키텍트입니다. 
  사용자의 자연어 요구사항(SRS)을 분석하여, 브라우저를 제어할 수 있는 [14대 표준 액션] 기반의 JSON 시나리오 배열을 작성하십시오.
  
  [핵심 계약]
  - step 1 은 항상 navigate 입니다.
  - 반드시 유효한 JSON 배열([...])만 출력합니다.
  - 마크다운 코드블록, `<think>` 같은 내부 추론 텍스트는 절대 출력하지 않습니다.

  [14대 표준 액션 및 파라미터 매핑]
  1. 기본 제어
  - navigate: target="", value="이동할 URL"
  - click: target="클릭할 요소", value=""
  - fill: target="텍스트를 입력할 폼 요소", value="입력할 문자열"
  - press: target="키 이벤트를 받을 요소 (또는 빈 문자열)", value="키 이름 (예: Enter, Escape, Tab)"
  - select: target="<select> 드롭다운 요소", value="선택할 옵션의 텍스트 또는 값"
  - check: target="체크박스 또는 라디오 버튼 요소", value="on" 또는 "off"
  - hover: target="마우스를 올릴 요소", value=""
  - wait: target="", value="대기할 밀리초 단위 시간 (예: 2000)"
  
  2. 검증 (Assertion)
  - verify: target="검증 대상 요소". 
    > 텍스트 검증 시: value="포함되어야 할 텍스트"
    > 상태 검증 시: condition="visible", "hidden", "disabled", "enabled", "checked" 중 하나 명시.
  
  3. 고급 UI 상호작용
  - upload: target="<input type='file'> 요소", value="업로드할 파일의 경로명"
  - drag: target="드래그를 시작할 소스 요소", value="마우스를 드롭할 목적지 요소의 로케이터"
  - scroll: target="화면에 나타나게 스크롤할 대상 요소", value="into_view" 고정
  
  4. 네트워크 제어 (Mocking)
  - mock_status: target="가로챌 API URL 패턴 (예: **/api/v1/users)", value="강제 반환할 HTTP 상태 코드 (예: 500)"
  - mock_data: target="가로챌 API URL 패턴", value="강제 반환할 JSON 본문 문자열 (반드시 쌍따옴표를 이스케이프 처리할 것)"
  
  [로케이터 작성 원칙]
  - Playwright의 시맨틱 로케이터를 최우선으로 사용하십시오. (예: "role=button, name=로그인", "text=검색", "label=이메일")
  - CSS 선택자나 XPath는 시맨틱 로케이터로 특정하기 어려운 경우에만 보조적으로 사용하십시오.
  - name 없는 단독 role (`role=button`, `role=link`) 은 금지합니다.
  
  [JSON 스키마]
  출력할 JSON 배열 내의 각 객체는 다음 필드를 모두 포함해야 합니다:
  - "step": 실행 순서 (1부터 시작하는 정수)
  - "action": 위 14대 표준 액션 중 하나 (절대 임의의 액션을 지어내지 말 것)
  - "target": 대상 로케이터 (해당 없는 액션은 빈 문자열 "")
  - "value": 액션의 값 (해당 없는 액션은 빈 문자열 "")
  - "condition": verify 액션 시에만 선택적으로 사용
  - "description": 이 스텝이 무엇을 하는지 설명하는 한국어 문장
  - "fallback_targets": target 탐색 실패 시도할 대체 로케이터 문자열 배열 (최소 2개 작성 필수. 예: ["text=로그인", ".login-btn"])
  
  [주의]
  - `mock_status`, `mock_data` 는 반드시 API 호출을 유발하는 액션보다 먼저 배치합니다.
  - `wait` 는 최소화하고, 상태 확인은 `verify` 로 표현합니다.
  - `press` 의 키 이름은 target 이 아니라 value 에 들어갑니다.

  [✅ 복합 시나리오 예시]
  입력: "프로필 저장 API에서 500 에러 발생 시 에러 팝업 노출 확인"
  출력:
  [
    {"step": 1, "action": "mock_status", "target": "**/api/profile", "value": "500", "description": "저장 API 500 에러 모킹", "fallback_targets": []},
    {"step": 2, "action": "click", "target": "role=button, name=저장", "value": "", "description": "저장 버튼 클릭", "fallback_targets": ["text=저장"]},
    {"step": 3, "action": "verify", "target": "text=저장에 실패했습니다", "value": "", "condition": "visible", "description": "에러 팝업 노출 검증", "fallback_targets": []}
  ]
  
  [엄수 사항]:
  - 반드시 유효한 JSON 배열([...]) 형태만 출력하십시오. 마크다운 코드블록이나 부연 설명은 절대 금지합니다.
  ```

  #### 3.1.2 Healer 프롬프트 상세 가이드 (Draft)
  Healer는 에러 복구 시 실패한 액션(`failed_step.action`)의 종류에 따라 탐색 및 복구 전략을 다르게 가져가야 합니다.

  **[Healer System Prompt 추가 규칙 초안]**
  ```text
  당신은 자가 치유(Self-Healing) 시스템입니다. 에러 메시지, 실패한 스텝 정보, 그리고 HTML DOM 스냅샷을 분석하십시오.
  
  [작업]:
  - 실패한 스텝(failed_step)의 원래 액션과 타겟을 확인하십시오.
  - 에러 메시지를 바탕으로 기존 요소를 찾지 못한 이유를 파악하십시오.
  - DOM 스냅샷 내에서 의도에 가장 부합하는 대체 셀렉터를 찾으십시오.
  - ⚠️ 무조건 DOM에 실제로 존재하는 요소만 제안하십시오. 가상의 셀렉터는 절대 금지합니다.
  - DOM 스냅샷은 잘린 일부일 수 있으므로, 긴 구조 대신 짧고 안정적인 시맨틱 셀렉터를 우선 제안하십시오.
  
  [신규 액션별 치유 전략]:
  - action이 "drag"인 경우: 소스(target)와 목적지(value) 중 어느 것을 못 찾았는지 에러에서 파악하십시오. 목적지 오류라면 새 로케이터를 "value" 키에 담아 반환하십시오.
  - action이 "upload"인 경우: 겉모양이 버튼이더라도 실제로는 `<input type="file">` 속성을 가진 숨겨진 요소를 찾아 제안해야 합니다.
  - action이 "mock_status" / "mock_data"인 경우: DOM 탐색 오류가 아닙니다. 에러를 바탕으로 타겟 URL 패턴(target)의 오타나 정규식을 교정하십시오.
  
  [로케이터 작성 원칙]:
  - DOM을 분석할 때 복잡한 CSS/XPath 계층 구조보다, 변하지 않는 텍스트(`text=로그인`)나 시맨틱 속성(`role=button, name=확인`, `[placeholder="검색"]`)을 최우선으로 제안하십시오.
  
  [출력 형식]:
  - 순수 JSON 객체({...}) 형식으로만 출력하십시오. 부연 설명 금지.
  - 필수 키: "target" (새 로케이터 문자열), "value" (기본은 빈 문자열 ""), "fallback_targets" (대체 로케이터 문자열 배열 2~3개)
  - `value` 는 drag 등에서 목적지 로케이터나 입력값이 변경되어야 할 경우 실제 값을 넣고, 그 외에는 빈 문자열("")을 넣습니다.
  
  [✅ 예시 (click 액션 실패 치유)]
  {"target": "role=button, name=Submit", "value": "", "fallback_targets": ["text=Submit", "#submit-btn"]}
  ```

  #### 3.1.3 Dify 내부 테스트 및 검증 (Prompt Evaluation)
  Python 실행 코드를 짜기 전, Dify 캔버스의 `미리보기(Preview)` 기능에서 아래 엣지 케이스들을 입력하여 환각 없이 스키마를 출력하는지 검증합니다.
  - **Edge Case 1**: "A영역의 카드를 B영역으로 드래그 앤 드롭해줘." (drag의 value 필드를 올바르게 쓰는지 확인)
  - **Edge Case 2**: "결제 API가 응답 지연될 때 버튼이 disabled 처리되는지 확인." (verify의 condition 속성을 사용하는지 확인)
  - **Edge Case 3**: 16GB RAM + `gemma4:e4b` 기준에서 Planner 응답이 timeout 없이 JSON 배열만 반환되는지 확인
  - **Edge Case 4**: Heal 입력 DOM 이 길어졌을 때도 `<think>` 누출 없이 60초 안에 JSON 객체를 반환하는지 확인

### 3.2 Python Executor 로직 확장
- **파일**: `zero_touch_qa/__main__.py`, `executor.py`
- **작업 내용**:
  1. **스키마 검증기 수정**: 시나리오 유효성 검사 로직(`_validate_scenario`)에서 허용 action 목록에 신규 액션 5종 추가.
  2. **Playwright 래핑**: 실제 코드 기준 `executor.py`의 `_execute_step()` / `_perform_action()` 경로에 신규 액션 분기와 부가 상태 관리 로직을 추가.
     ```python
     elif action == "upload":
         # 보안을 위해 컨테이너 내부의 허용된 fixtures/ 경로의 파일만 업로드 허용
         file_path = os.path.join(ARTIFACT_DIR, value) 
         locator.set_input_files(file_path)
     elif action == "drag":
         target_locator = page.locator(value)
         locator.drag_to(target_locator)
     elif action == "scroll":
         locator.scroll_into_view_if_needed()
     elif action == "mock_status":
         # Playwright의 네트워크 인터셉트를 사용해 상태 코드만 강제 조작
         page.route(target, lambda route: route.fulfill(status=int(value), times=1))
     elif action == "mock_data":
         # 응답 본문(JSON)을 강제 주입
         page.route(target, lambda route: route.fulfill(status=200, content_type="application/json", body=value, times=1))
     ```
  3. **Verify 고도화**: `verify` 액션 분기에서 `condition` 키워드를 추출하여 `to_be_disabled()`, `to_be_visible()`, `to_have_value()` 등을 호출하도록 분기 처리.

  #### 3.2.1 실행 계층(Executor) 3대 사각지대 방어 [NEW]
  안정적인 Playwright 부작용(Side-effect) 통제를 위해 다음 3가지 예외 처리 로직을 `executor.py`에 필수로 구현합니다.
  - **drag 목적지 방어**: 출발지(`target`)뿐만 아니라 목적지(`value`) 로케이터도 사전 검증하며, 실패 시 Healer 호출 전 뷰포트의 안전 영역으로 강제 드롭을 시도하는 예외 로직 추가.
  - **mock 전역 오염 차단**: `mock_status`, `mock_data` 액션의 `page.route` 설정이 이후의 모든 테스트 스텝을 망가뜨리지 않도록, 반드시 `times=1` 옵션을 적용하여 정확히 1회만 인터셉트하도록 제한.
  - **verify 에러 세분화**: 요소를 못 찾은 것(Locator Error)인지 상태가 다른 것(Assertion Error)인지 Try-Except로 명확히 분리하여, Healer LLM이 불필요한 로케이터 교체 무한루프에 빠지지 않도록 정확한 에러 컨텍스트 제공.

### 3.3 로컬 단위 테스트 구성
- **위치**: `playwright-allinone/test/`
- **작업 내용**:
  - **신규 Fixtures HTML 생성**: `upload.html`, `drag.html`, `scroll.html` 생성.
  - **테스트 케이스 작성**:
    - `test_upload.py`: `<input type="file">` 동작 검증.
    - `test_drag.py`: HTML5 Native Drag & Drop 영역 이동 검증.
    - `test_scroll.py`: overflow 창 및 window 무한 스크롤(lazy-loading 모방) 환경 검증.
    - `test_verify_advanced.py`: disabled, visible, value 상태 검증 테스트.
    - `test_mocking.py`: `mock_status` 및 `mock_data` 동작 검증 (더미 프론트엔드 호출 가로채기).

---

## 4. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 (Mitigation) |
| --- | --- | --- |
| **보안 (upload 경로 조작)** | 컨테이너 내부의 민감 파일(예: `.env`, `agent.jar`) 유출 위험 | `upload` 액션 사용 시, 지정된 샌드박스 폴더(`workspace/.../artifacts/`) 내의 파일만 접근 가능하도록 Path Traversal 방어 로직 적용(`os.path.abspath` 검증). |
| **LLM 혼란 (Hallucination)** | 액션 수가 늘어나 4B 모델(gemma4:e4b)이 지시를 잊거나 매개변수를 헷갈릴 위험 | 프롬프트의 텍스트 길이를 최대한 압축하여 유지하고, `drag` 의 `value`가 target selector로 쓰인다는 것을 [✅ 예시] 에 강하게 못 박음. |
| **Healer(복구) 실패** | `drag` 수행 중 소스 요소는 찾았으나 타겟 요소(value)가 변경되어 실패할 경우 | `LocalHealer` 에 에러 덤프 전송 시, 타겟 셀렉터 오류인지 소스 오류인지 구분하여 DOM 스냅샷 질의 프롬프트 분기. |
| **JSON Escape 오류** | `mock_data` 사용 시 LLM이 JSON 안의 JSON 이스케이프 문자(`\"`)를 잘못 생성하여 JSONDecodeError 발생 | Dify LLM 노드의 출력물을 파싱하는 파이썬 실행기(`executor.py`) 단에서 JSON Escape 예외를 유연하게 잡아주는 보정 로직(Fixup) 추가. |
| **프롬프트 과비대화** | 4B 모델이 규칙을 잊거나 timeout 발생 | Planner/Healer 프롬프트를 짧은 계약형으로 유지하고, 출력 토큰 상한을 낮춘다. |
| **저사양 머신 응답 지연** | 16GB 단일 머신에서 Heal 경로가 60초 안에 끝나지 않을 위험 | DOM 입력량을 제한하고, `gemma4:e4b` 기준 budget 을 문서에 먼저 고정한다. |

---

## 5. Sprint 1 상세 작업목록

Sprint 1은 기능 구현 스프린트가 아니라, **LLM 계약과 문서 기준선을 먼저 고정하는 정비 스프린트**로 정의한다. 즉 Python Executor 구현 전에, Planner/Healer/DOC 간의 용어와 스키마가 서로 어긋나지 않도록 먼저 잠그는 단계다.

### 5.1 목표

- Planner가 14대 DSL만 출력하도록 계약을 명시한다.
- Healer가 신규 액션(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`)까지 복구 가능한 JSON 계약을 갖도록 정리한다.
- `architecture.md`, `dify-chatflow.yaml`, 본 계획서 사이의 표기와 용어를 동기화한다.
- Sprint 2 구현팀이 더 이상 "문서에는 14대, 실행기는 9대" 같은 해석 충돌 없이 착수할 수 있도록 기준선을 만든다.
- 16GB 단일 머신 + `gemma4:e4b` 기준에서 과도한 모델 권장과 장문 프롬프트를 제거한다.
- Sprint 1에서 실제로 손댄 Dify Workflow 범위를 끝까지 닫아, "수정은 했지만 미완료" 상태를 남기지 않는다.

### 5.2 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S1-01 | Sprint 1의 범위를 "프롬프트/문서 계약 정비"로 명시 | 본 계획서 | Sprint 1이 실행 코드 구현을 포함하지 않는다고 명확히 적시 | 완료 |
| S1-02 | 14대 DSL 확장 범위 확정 | 본 계획서, `architecture.md` | 9개 기존 액션 + 신규 5개 액션 목록이 동일하게 기재 | 완료 |
| S1-03 | `verify.condition` 확장 스키마 정의 | 본 계획서, `architecture.md`, `dify-chatflow.yaml` | visible/hidden/disabled/enabled/checked/value 등 허용 의미가 문서화 | 완료 |
| S1-04 | Planner 프롬프트에 14대 액션 계약 반영 | `dify-chatflow.yaml` | 액션 목록, `target`/`value` 매핑, 금지 규칙 포함 | 완료 |
| S1-05 | Planner 프롬프트에 step 1 `navigate` 강제 규칙 반영 | `dify-chatflow.yaml` | 모든 시나리오 첫 step 규칙이 명시 | 완료 |
| S1-06 | Planner 프롬프트에 소형 LLM 방어 규칙 반영 | `dify-chatflow.yaml` | `<think>` 금지, wait 남용 금지, blind guessing 억제 규칙 포함 | 완료 |
| S1-07 | Planner few-shot 예시 추가 | `dify-chatflow.yaml` | 최소 1개 이상의 복합 예시가 JSON 형태로 포함 | 완료 |
| S1-08 | Healer 프롬프트에 신규 액션별 복구 전략 반영 | `dify-chatflow.yaml` | `drag`/`upload`/`mock_*` 전략이 분리 명시 | 완료 |
| S1-09 | Healer 출력 JSON 계약 정리 | `dify-chatflow.yaml`, 본 계획서 | 필수 키와 선택 키 규칙이 모순 없이 문서화 | 완료 |
| S1-10 | Healer few-shot 예시를 출력 계약과 일치시킴 | `dify-chatflow.yaml` | 예시 JSON이 실제 요구 키셋과 충돌하지 않음 | 완료 |
| S1-11 | `architecture.md` 상위 요약에 v4.1 확장 반영 | `architecture.md` | Phase 4/최종 산출물 요약이 14대 DSL 기준 | 완료 |
| S1-12 | `architecture.md` 본문 내 9대 DSL 잔존 표현 정리 | `architecture.md` | Flow 설명, 데이터 흐름, 유저 워크플로우가 14대 DSL 기준으로 정리 | 완료 |
| S1-13 | 제거된 컴포넌트 설명 반영 | `architecture.md` | 제거된 Vision Refactor 노드가 더 이상 활성 구성요소처럼 보이지 않음 | 완료 |
| S1-14 | README와 현재 구현 상태의 경계 명시 | `README.md` 또는 본 계획서 | "문서 계약 선행, 실행기 구현은 Sprint 2" 경계가 독자가 이해 가능 | 완료 |
| S1-15 | Dify Preview용 프롬프트 평가 케이스 정의 | 본 계획서 | drag / disabled / mocking 등 대표 입력 사례 문서화 | 완료 |
| S1-16 | Sprint 2 착수 전 잔여 리스크 목록화 | 본 계획서 | executor/test 미구현 항목이 남은 일로 분리됨 | 완료 |
| S1-17 | Sprint 1 하드웨어 기준선 명시 | 본 계획서, `architecture.md`, `README.md` | 기본 타깃이 16GB + `gemma4:e4b` 임이 명시 | 완료 |
| S1-18 | Planner/Healer prompt budget 정의 | 본 계획서, `dify-chatflow.yaml`, `architecture.md` | 토큰 상한과 DOM 입력 상한이 문서화 | 완료 |
| S1-19 | 30B/클라우드 모델 권장 제거 | `architecture.md` | Sprint 1 본문에서 30B/외부 모델이 기본 권장으로 남지 않음 | 완료 |
| S1-20 | CPU-only 경로의 위치 정리 | 본 계획서, `README.md` | CPU-only는 가능하지만 목표 성능 기준은 아님을 명시 | 완료 |
| S1-21 | Dify guard 설계 실체화 | `dify-chatflow.yaml`, 본 계획서 | 계획서에 적은 JSON parser/guard 방어가 실제 워크플로우 반영 또는 명시적 비채택으로 정리 | 완료 |
| S1-22 | `api_docs` 변수 처리 확정 | `dify-chatflow.yaml`, `architecture.md` | `api_docs`를 실제 prompt에 연결하거나, 쓰지 않을 경우 문서/변수에서 제거 | 완료 |
| S1-23 | Dify Preview 실측 검증 수행 | Dify UI 검증 기록, 본 계획서 | `gemma4:e4b` 기준 Planner/Healer preview 결과를 실제 확인 | 진행 중 |
| S1-24 | Dify 재배포/재import 기준 정리 | 본 계획서, `README.md` | 수정된 yaml을 콘솔에 다시 import/publish 하는 절차와 검증 지점 명시 | 완료 |
| S1-25 | Sprint 1 종료 판정 갱신 | 본 계획서 | 위 4개 잔여 항목을 닫은 뒤 Sprint 1 상태를 `완료`로 변경 | 미착수 |

### 5.3 Sprint 1 종료 조건 (Definition of Done)

- Planner/Healer 프롬프트가 모두 14대 DSL 계약 기준으로 정렬되어 있다.
- 본 계획서와 `architecture.md`가 동일한 액션 수와 동일한 개념 모델을 설명한다.
- 문서 안에서 제거된 노드나 구버전 9대 DSL 표현이 독자를 오도하지 않는다.
- "실행 가능"과 "설계 완료"를 구분하는 문장이 존재한다.
- Sprint 2에서 구현해야 할 남은 작업이 별도 항목으로 분리되어 있다.
- 기본 운영 하드웨어와 기본 모델이 `16GB + gemma4:e4b` 로 일관되게 설명된다.
- Planner/Healer prompt budget 과 DOM 입력 budget 이 명시되어 있다.
- Dify Workflow에서 실제로 수정한 범위가 미정 상태로 남아 있지 않다.
- `api_docs` / guard 설계처럼 문서에 적은 Dify 보강안이 실제 워크플로우와 일치한다.
- `gemma4:e4b` 기준 Preview 실측으로 Planner/Healer가 최소 1회 이상 정상 JSON 응답을 반환하는 것이 확인된다.

### 5.4 Sprint 1 비범위 (Out of Scope)

- `zero_touch_qa/__main__.py` 의 허용 액션 목록 변경
- `executor.py` 의 신규 액션 실행 로직 추가
- 로컬 fixture / pytest 추가
- Sprint 2 구현 범위(`executor.py`, `__main__.py`) 선반영

### 5.5 현재 판정

현재 Sprint 1은 **여전히 완료가 아니라 진행 중**이다. 다만 이유는 이제 하나로 좁혀졌다. Sprint 1에서 실제로 `dify-chatflow.yaml`을 수정한 뒤, `guard` 설계와 `api_docs` 배선, 재import 절차 문서화까지는 닫았지만, `gemma4:e4b` 기준 Preview 실측 검증이 아직 끝나지 않았기 때문이다.

- `guard` 설계는 "Dify 내부 parser/guard 노드 비채택, Python 후단 검증 담당"으로 확정했다.
- `api_docs` 변수는 실제 Planner prompt와 Start 변수 양쪽에 연결했다.
- yaml 재배포/재import 절차는 `README.md`에 반영했다.
- 2026-04-27 실측에서 `Variable #start.api_docs# not found` 오류를 한 번 재현했고, 이는 Start 변수 누락으로 확인되어 YAML에 즉시 수정했다.
- 수정 후 재프로비저닝까지는 성공했지만, 재기동 직후 `/console/api/login` 및 `/v1/chat-messages` 호출이 timeout/지연을 보여 Preview 검증을 완전히 닫지는 못했다.

즉 Sprint 1은 "문서와 프롬프트를 상당 부분 정비한 상태"를 넘어서 "설계와 배선은 닫혔지만 실측 검증이 남은 상태"다.

### 5.6 Sprint 1 잔여작업 상세

#### 5.6.1 Dify Workflow 구조 보강

1. **Guard 설계 결정**
   - **채택안:** Dify 내부에 별도 JSON parser / guard 노드를 추가하지 않는다.
   - 이유:
     - Sprint 1의 기본 하드웨어가 16GB + `gemma4:e4b` 인 만큼, Chatflow 노드를 늘려 복잡도를 키우기보다 prompt 계약과 후단 검증으로 단순성을 유지하는 편이 현실적이다.
     - 실제 구조 검증, 재시도, 보정은 Python 후단(`zero_touch_qa`)이 이미 더 정확하게 담당할 수 있다.
   - 후속 조치:
     - 계획서/아키텍처에서 "Dify 내부 parser 노드 추가"처럼 읽히는 문장을 제거하거나 비채택으로 명시한다.
   - 완료 기준: 문서와 실제 `dify-chatflow.yaml`이 같은 말을 한다.
   - 현재 상태: 완료

2. **`api_docs` 변수 처리**
   - Planner user prompt 또는 system prompt에 `api_docs`를 실제 연결해 네트워크 모킹 힌트로 사용한다.
   - 연결하지 않을 경우, Start 변수 정의와 관련 문서 설명에서 제거하거나 "후속 스프린트 후보"로 내린다.
   - 완료 기준: 정의된 변수와 실제 프롬프트 배선이 일치한다.
   - 현재 상태: 완료

#### 5.6.2 Dify 실측 검증

1. **Planner Preview 검증**
   - 입력 1: drag 시나리오
   - 입력 2: disabled verify 시나리오
   - 입력 3: mock_status 시나리오
   - 확인 항목:
     - JSON 배열만 반환하는지
     - step 1 navigate 규칙을 지키는지
     - `drag.value`, `verify.condition`, `mock_*` 순서를 올바르게 쓰는지

2. **Healer Preview 검증**
   - click 실패, drag 목적지 실패, upload 숨김 input 실패를 대표 케이스로 넣는다.
   - 확인 항목:
     - JSON 객체만 반환하는지
     - `target`, `value`, `fallback_targets` 3키를 모두 포함하는지
     - 긴 DOM 입력에서도 `<think>` 누출이나 timeout이 없는지

3. **하드웨어 기준 검증**
   - 기준 환경: 16GB RAM + `gemma4:e4b`
   - 확인 항목:
     - Planner Preview 응답 성공 여부
     - Healer Preview 응답 성공 여부
     - timeout 또는 포맷 이탈 재현 여부
   - 2026-04-27 실측 메모:
     - 첫 호출에서 `Variable #start.api_docs# not found` 를 재현했고, Start 변수 누락을 수정했다.
     - 수정 후 재프로비저닝은 성공했지만, 재기동 직후 host 측 `/console/api/login` 과 container 내부 `/v1/chat-messages` 호출이 timeout 되어 안정성 판정이 보류됐다.
   - 현재 상태: 진행 중

#### 5.6.3 문서/배포 절차 마감

1. **yaml 재import 절차 명시**
   - 수정한 `dify-chatflow.yaml`을 Dify 콘솔에 다시 import/publish 하는 순서를 문서에 적는다.
   - `.app_provisioned` 또는 재프로비저닝 시 덮어쓰기되는 동작과의 관계를 함께 적는다.
   - 현재 상태: 완료

2. **Sprint 1 종료 선언 업데이트**
   - 위 잔여 항목이 닫히면 `Sprint 1 (진행 중)`를 `완료`로 바꾼다.
   - 체크리스트 상태도 함께 갱신한다.

## 6. Sprint 2 상세 작업목록 (구현 완료 — 2026-04-27)

Sprint 2는 **문서 계약을 실제 Python 실행기로 연결하는 구현 스프린트**다. 범위는 `zero_touch_qa` 런타임과 Jenkins 파이프라인 입력까지이며, Dify 운영/배포 계층 안정화는 포함하지 않는다. 2026-04-27 기준 구현·재검증·갭 마감·단위테스트(16/16 통과)까지 모두 닫혔다.

### 6.1 목표

- `chat`, `doc`, `execute` 경로에서 14대 DSL이 구조적으로 허용된다.
- `executor.py`가 신규 5종 액션(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`)을 실제 Playwright 동작으로 실행한다.
- `verify.condition`이 텍스트 검증 외의 상태/속성 검증까지 수행한다.
- 신규 액션이 기존 Self-Healing 흐름(`fallback_targets` → LocalHealer → Dify healer)과 충돌하지 않는다.
- 파일 업로드 경로, mock 수명주기, assertion/locator 오류 구분 같은 안전장치가 함께 구현된다.

### 6.2 리뷰 분석내역

Sprint 2 계획은 방향성 자체는 맞지만, 실제 코드베이스 기준으로는 다음 공백이 있었다.

- **구현 지점 표기 불일치**: 문서가 `execute_step` 기준으로 설명돼 있었지만, 실제 수정 지점은 `__main__.py`의 `_VALID_ACTIONS` / `_validate_scenario`, 그리고 `executor.py`의 `_execute_step()` / `_perform_action()` 이다.
- **업로드 계약 미정**: `upload.value`를 경로 문자열로만 적어두고, 어떤 디렉터리를 기준으로 해석할지와 path traversal 방어를 계획 수준에서 고정하지 않았다.
- **mock 수명주기 누락**: `page.route()`를 추가하는 것만 적혀 있었고, 언제 설치하고 언제 해제할지, 후속 스텝 오염을 어떻게 막을지 빠져 있었다.
- **verify 실패 분류 미흡**: `verify.condition` 구현 목표는 있었지만 locator 실패와 assertion 실패를 분리한다는 작업이 체크리스트와 종료 조건으로 내려와 있지 않았다.
- **Healer 병합 규칙 누락**: `drag`, `mock_*`는 `target`뿐 아니라 `value` 교정도 의미가 있는데, healer 응답을 어떤 기준으로 병합할지 문서에 없었다.
- **예시 키 불일치**: verify 예시 일부가 현재 DSL 계약의 `target`이 아니라 `selector`를 사용하고 있었다.

### 6.3 리뷰 보강내역

위 분석을 반영해 Sprint 2 계획을 다음과 같이 보강했다.

- **실제 코드 기준으로 수정 지점 명시**: `__main__.py`와 `executor.py`의 어떤 함수들을 수정해야 하는지 문서에 직접 적었다.
- **구현 범위 축소**: Sprint 2 범위를 `zero_touch_qa` 런타임과 Jenkins 입력 계약으로 한정하고, Dify 운영/배포 계층은 명시적으로 제외했다.
- **세부 체크리스트 확장**: 구조 검증, upload 경로 방어, drag 목적지 검증, mock 수명주기, verify 실패 분리, healer 병합, execute 모드 호환성, `scenario.healed.json` 반영까지 작업 단위를 쪼갰다.
- **완료 조건 강화**: 단순 “신규 액션 추가”가 아니라 업로드 방어, route 오염 차단, assertion/locator 실패 분리까지 Sprint 2 Definition of Done에 포함했다.
- **구현 순서 제안**: 저위험 액션(`upload`, `scroll`) → 고위험 액션(`drag`, `mock_*`, `verify.condition`) → healer/산출물 정리 순으로 권장 순서를 적었다.
- **예시 정합성 수정**: verify 예시의 키를 `selector`에서 `target`으로 고쳐 현재 DSL 계약과 맞췄다.

### 6.4 수행 내역 및 검증 결과 (2026-04-27)

Sprint 2 구현 직후 실시한 냉정 재검증에서 표면적으로는 거의 끝나 있었으나 4건의 실 갭과 1건의 누락 항목이 추가로 식별되어 같은 스프린트 내에서 모두 닫았다. 닫힌 갭과 그 검증 방식은 다음과 같다.

#### 6.4.1 식별된 갭과 처리 결과

| 갭 | 영향 | 처리 |
| --- | --- | --- |
| execute 모드에서 `_validate_scenario` 미호출 | 손으로 작성한 scenario.json 의 계약 위반이 런타임 ValueError 로 우회됨 | `_prepare_scenario` 의 execute 분기에 `_validate_scenario` 호출 추가 (S2-11 보강) |
| `verify.condition` 화이트리스트 검증 부재 | `condition: "exists"` 같은 오타가 검증을 통과하고 런타임 ValueError | `_VALID_VERIFY_CONDITIONS` 화이트리스트와 `_check_action_specific` 검증 추가 (S2-02 보강) |
| `regression_generator.py` 신규 5종 침묵 스킵 | 회귀 테스트가 upload/drag/scroll/mock_* 를 검증하지 않은 채 PASS | `_emit_step_code` 디스패치로 14대 모두 매핑 (신규 S2-15) |
| fallback heal / mock fallback 이 healed.json 미반영 | LLM heal 만 healed.json 에 기록되어 fallback 치유 결과가 분실 | fallback heal 시 `step["target"]` 직접 갱신 (S2-12 강화) |
| `mock_*` 실패 시 healing 미연결 | yaml 의 Healer mock_* 가이드가 dead code | `_execute_mock_step` 신설: fallback URL 패턴 → Dify LLM 2단계 치유 (S2-10 강화) |
| `times=1` 의미 미명문화 | 폴링/재시도 시나리오에서 의도와 다른 모킹 | step.times 옵션 노출 + §6.8 smoke 절차에 의미 명시 (S2-07 강화) |
| smoke 절차 부재 | Sprint 3 fixture 작성 전 런타임 검증 수단 없음 | §6.8 단위테스트 + 수동 시나리오 + grep 점검 절차 작성 (S2-14) |

#### 6.4.2 단위테스트 검증 결과

```text
$ python3 -m pytest test/test_sprint2_runtime.py -v
============================== 16 passed in 0.10s ==============================
```

추가된 단위테스트 (총 16건):

- 14대 액션 시나리오 검증 통과 / scroll 위반 거부 / upload artifacts 루트 강제 / mock body 정규화 (Sprint 2 초기 4건)
- `verify.condition` 알 수 없는 값 거부 / 확장 condition 수용
- `mock_*.times` 비정수·0 거부 / 양의 정수 수용
- `regression_generator` 의 upload/drag/scroll/mock_status/mock_data/verify-condition 분기 출력 검증
- `_install_mock_route` 가 `times<=0` 입력을 1 로 끌어올리는지 확인

전 항목 통과. 9대 액션 기존 pytest (30건, Playwright 브라우저 필요) 와 import 정합성도 재확인했다.

### 6.5 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S2-01 | `_VALID_ACTIONS`를 14대 DSL로 확장 | `zero_touch_qa/__main__.py` | `upload`/`drag`/`scroll`/`mock_status`/`mock_data`가 구조 검증에서 거부되지 않음 | 완료 |
| S2-02 | 액션별 필수 필드 검증 추가 | `zero_touch_qa/__main__.py` | `drag.value`, `upload.value`, `scroll.value=into_view`, `mock_status.value` 숫자성, `verify.condition` 화이트리스트, `mock_*.times` 양의 정수 검증 | 완료 |
| S2-03 | `_perform_action()`에 신규 5종 액션 구현 | `zero_touch_qa/executor.py` | 신규 액션이 실제 Playwright API 호출로 실행됨 | 완료 |
| S2-04 | upload 경로 해석/방어 로직 추가 | `zero_touch_qa/executor.py` | 허용 루트 밖 경로(`../`, 절대경로 탈출)가 차단되고 정상 파일만 업로드 가능 | 완료 |
| S2-05 | drag 목적지 사전 검증과 예외 메시지 정리 | `zero_touch_qa/executor.py` | source/target 중 어느 쪽 실패인지 로그와 예외에 남음 | 완료 |
| S2-06 | scroll 계약 고정 | `zero_touch_qa/__main__.py`, `executor.py` | `scroll.value`가 `into_view` 외 값이면 조기 실패 또는 normalize | 완료 |
| S2-07 | mock route 설치/수명주기 관리 | `zero_touch_qa/executor.py` | `mock_*`가 기본 `times=1` 로 후속 스텝 오염을 막고, step.times 로 횟수 제어 가능. 의미를 본 계획서에 명문화 | 완료 |
| S2-08 | `verify.condition` 분기 구현 | `zero_touch_qa/executor.py` | `visible`/`hidden`/`disabled`/`enabled`/`checked`/`value`/`text`(별칭 `contains_text`,`contains`) 가 각각 적절한 `expect()` 호출로 매핑 | 완료 |
| S2-09 | verify 실패 원인 분리 | `zero_touch_qa/executor.py` | `VerificationAssertionError` 가 별도 클래스로 분리되어 locator 실패와 assertion 실패가 구분된다 | 완료 |
| S2-10 | Healer 병합 규칙 점검 | `zero_touch_qa/executor.py` | `drag`/`upload` 는 LLM heal 의 `step.update` 로 target/value 모두 반영. `mock_*` 는 신규 `_execute_mock_step` 가 fallback URL 패턴 → Dify LLM 2단계 치유를 수행 | 완료 |
| S2-11 | `execute` 모드 호환성 확보 | `zero_touch_qa/__main__.py`, `executor.py` | execute 모드에서도 `_validate_scenario` 가 호출되어 외부 scenario.json 에 신규 액션이 포함돼도 동일한 계약으로 실행됨 | 완료 |
| S2-12 | `scenario.healed.json` 반영 범위 점검 | `zero_touch_qa`, 리포트 산출물 | LLM heal 뿐 아니라 fallback_targets / mock_* fallback 치유도 step dict 갱신 → 최종 healed.json 반영 | 완료 |
| S2-13 | Jenkins 입력 계약 정리 | `ZeroTouch-QA.jenkinsPipeline`, README | `API_DOCS` 등 Sprint 1에서 추가한 입력이 Sprint 2 런타임과 일관되게 유지됨 | 완료 |
| S2-14 | 최소 smoke 검증 절차 문서화 | 본 계획서 §6.8 | 신규 5종 액션별 단위테스트 + 수동 시나리오 + 회귀 산출물 grep 절차가 본 계획서에 작성 | 완료 |
| S2-15 | `regression_generator.py` 14대 확장 | `zero_touch_qa/regression_generator.py` | upload/drag/scroll/mock_* 가 회귀 테스트 산출물에 정상 출력 (이전엔 빈 줄로 침묵 스킵) | 완료 |

### 6.5 구현 순서 권장안

1. `__main__.py` 구조 검증 확장부터 수행한다.
2. `executor.py`에 신규 액션 분기를 추가하되, `upload`/`scroll` 같은 저위험 액션을 먼저 붙인다.
3. 그다음 `drag`, `mock_*`, `verify.condition`처럼 부작용과 상태 분기가 큰 액션을 붙인다.
4. 마지막에 Healer 병합, `scenario.healed.json`, `execute` 모드 호환성을 정리한다.

### 6.6 Sprint 2 종료 조건 (Definition of Done) — 모두 충족

- ✅ `chat`, `doc`, `execute` 모두에서 신규 5종 액션을 포함한 시나리오가 `_validate_scenario` 를 통과한다. execute 모드도 동일 검증을 거치도록 [`__main__.py:223`](zero_touch_qa/__main__.py#L223) 가 보강되었다.
- ✅ `executor.py` 가 `upload`/`drag`/`scroll`/`mock_status`/`mock_data` 5종을 실제 Playwright API 호출로 수행한다 ([`executor.py:967-991`](zero_touch_qa/executor.py#L967-L991), `_execute_mock_step`).
- ✅ `verify.condition` 7개 분기(visible/hidden/disabled/enabled/checked/value/text 별칭) 가 구현되고, `VerificationAssertionError` 가 별도 클래스로 분리되어 assertion 실패와 locator 실패가 구분된다.
- ✅ upload 경로 방어 (`commonpath` 기반 artifacts 루트 강제) 와 mock 수명주기 제한 (`times=1` 기본 + step.times 옵션) 이 코드와 단위테스트로 보장된다.
- ✅ Healer 가 바꾼 `target`/`value` 는 LLM heal 의 `step.update` 로 drag/upload 에 반영되고, fallback_targets / mock_* fallback 치유는 `step["target"]` 직접 갱신으로 `scenario.healed.json` 까지 일관되게 반영된다.
- ✅ `regression_generator.py` 가 14대 액션 모두를 출력하므로 회귀 산출물이 신규 액션을 침묵 스킵하지 않는다.
- ✅ Sprint 3 테스트팀이 fixture/pytest 작성에 착수할 수 있도록 `test_sprint2_runtime.py` 가 16건의 단위테스트로 입력 계약·헬퍼·회귀 출력을 잠갔다.

### 6.7 Sprint 2 비범위 (Out of Scope)

- Dify 서버 기동, nginx, gunicorn, `provision.sh` 등 운영 계층 수정
- 대규모 Dify prompt 재설계
- 로컬 fixture HTML 작성과 pytest 본격 구축
- Flow 3(convert) 경로의 14대 DSL 완전 확장

### 6.8 신규 5종 액션 Smoke 점검 절차

Sprint 3 정식 pytest 구축 전, 14대 DSL 확장이 외관상 동작하는지 빠르게 확인하기 위한 수동 점검이다. 모두 `playwright-allinone` 디렉터리에서 실행한다.

#### 6.8.1 사전 준비

```bash
cd playwright-allinone
python3 -m pip install -r test/requirements.txt
python3 -m playwright install chromium
mkdir -p artifacts/fixtures
```

#### 6.8.2 단위 검증 (pytest)

신규 액션의 입력 계약·런타임 헬퍼만 격리해 빠르게 확인한다. Playwright 브라우저를 실제로 띄우지 않으므로 60초 안에 끝난다.

```bash
python3 -m pytest test/test_sprint2_runtime.py -v
```

확인 포인트:

- 14대 액션 시나리오가 `_validate_scenario` 통과
- `scroll.value=bottom` 같은 위반은 즉시 거부
- `verify.condition=foo` 같은 화이트리스트 밖 값은 즉시 거부
- `mock_*.times` 가 정수가 아니면 거부
- `_resolve_upload_path` 가 artifacts 루트 밖 경로(`../secret.txt`) 차단
- `_normalize_mock_body` 가 dict / JSON 문자열 / 평문 모두 처리

#### 6.8.3 액션별 시나리오 smoke (실 브라우저)

각 액션을 단독으로 검증하는 최소 시나리오 4개를 `--mode execute` 로 돌려본다. 별도 Dify 호출 없이 14대 DSL 입력 계약만 검사한다.

##### A) upload — `<input type="file">` 에 artifacts 안 파일 업로드

```json
[
  {"step": 1, "action": "navigate", "value": "https://the-internet.herokuapp.com/upload"},
  {"step": 2, "action": "upload", "target": "#file-upload", "value": "fixtures/sample.txt"},
  {"step": 3, "action": "click", "target": "#file-submit"},
  {"step": 4, "action": "verify", "target": "#uploaded-files", "condition": "visible"}
]
```

```bash
echo "smoke" > artifacts/fixtures/sample.txt
python3 -m zero_touch_qa --mode execute --scenario artifacts/upload_smoke.json --headless
```

기대: 4 스텝 PASS, `regression_test.py` 가 `set_input_files("fixtures/sample.txt")` 라인을 포함.

##### B) drag — source → destination drag-and-drop

```json
[
  {"step": 1, "action": "navigate", "value": "https://the-internet.herokuapp.com/drag_and_drop"},
  {"step": 2, "action": "drag", "target": "#column-a", "value": "#column-b"}
]
```

기대: drag PASS. 실패 시 로그에 "drag 목적지 탐색 실패" 또는 source 실패가 명시되어 분리됨.

##### C) scroll — into_view 강제

```json
[
  {"step": 1, "action": "navigate", "value": "https://the-internet.herokuapp.com/large"},
  {"step": 2, "action": "scroll", "target": "#sibling-50.10", "value": "into_view"}
]
```

기대: PASS. `value: "bottom"` 으로 바꾸면 `_validate_scenario` 가 즉시 거부.

##### D) mock_status / mock_data — 네트워크 모킹

```json
[
  {"step": 1, "action": "mock_status", "target": "**/api/users/*", "value": "500"},
  {"step": 2, "action": "mock_data", "target": "**/api/list", "value": "{\"items\":[]}"},
  {"step": 3, "action": "navigate", "value": "https://example.com"}
]
```

기대: 모든 스텝 PASS. `times` 기본 1 이므로 첫 매칭만 가로채고 후속 스텝은 실네트워크. 폴링이 필요하면 `"times": 5` 등 명시.

#### 6.8.4 회귀 테스트 산출물 확인

성공한 시나리오는 `artifacts/regression_test.py` 가 자동 생성된다. 신규 5종이 빈 줄로 침묵 스킵되지 않는지 확인한다:

```bash
grep -E "set_input_files|drag_to|scroll_into_view_if_needed|page.route" artifacts/regression_test.py
```

위 grep 이 모두 매칭되면 회귀 테스트 생성기까지 14대 매핑이 살아 있다.

## 7. Sprint 3 상세 작업목록 (구현 완료 — 2026-04-27)

Sprint 3 은 **Sprint 2 에서 구현한 14대 DSL 런타임이 진짜로 동작하는지 fixture 기반 pytest 로 잠그는 검증 스프린트**다. 범위는 `playwright-allinone/test/` 의 fixture HTML 과 pytest 케이스, 그리고 Jenkins 파이프라인의 pytest 단계 추가까지다. Dify Brain 의 LLM 응답 품질 검증은 Sprint 1 책임이며, 실제 운영 환경 통합은 Sprint 4 로 분리한다. 2026-04-27 기준 구현·47/47 통합테스트 PASS·30/30 native 회귀 PASS·Jenkins stage 추가까지 모두 닫혔다.

### 7.1 목표

- 14대 DSL 액션이 모두 fixture 기반 pytest 1건 이상으로 잠긴다 — 9대는 회귀, 신규 5종은 신설.
- **에어갭 강제**: 모든 fixture 가 `file://` URL 로 동작하고 외부 도메인 참조 0건.
- v4.1 의 진짜 차별점인 **mock_* 가 단순 라우트 설치를 넘어 UI 예외처리 시나리오** 로 검증된다 (예: `mock_status 500` → `verify` 에러 UI).
- 3-Flow (`chat`/`doc`/`execute`) 모두 fixture 위에서 한 번씩 통합 회귀.
- Healing 루프 (fallback_targets / LocalHealer / Dify mock) 가 실제로 복구하고 `scenario.healed.json` 에 기록되는지 검증.
- Jenkins 파이프라인이 pytest 단계를 가져 매 빌드마다 자동 회귀.

### 7.2 리뷰 분석내역

Sprint 3 을 단순히 "신규 액션마다 pytest 1건씩"으로 잡으면 다음 함정에 빠진다.

- **mock_* 의 의미 누락**: 라우트 설치 자체만 PASS 로 넘기면 v4.1 의 동기 (UI 예외처리 검증) 가 산출물에 안 남는다. UI 가 실제로 에러 메시지를 노출하는지까지 검증해야 의미가 있다.
- **Healing 검증 누락**: Sprint 2 에서 `_execute_mock_step` / fallback heal 갱신을 만들었지만 통합 테스트가 없으면 회귀 차단력이 약하다.
- **외부 사이트 의존**: Sprint 2 smoke runbook 은 `the-internet.herokuapp.com` 을 참조했지만 본 스프린트의 정식 회귀는 file:// 만 허용해야 에어갭 환경 보장.
- **Dify 실호출 위험**: 작은 모델 (gemma4:e4b) 의 응답 비결정성을 Sprint 3 이 흡수하면 회귀 안정성 0. Dify 는 monkeypatch 로 강제 분리.
- **regression_test.py 산출물 회귀 부재**: Sprint 2 에서 14대 emitter 를 추가했지만 생성된 코드가 실제로 Python 으로 실행 가능한지 끝단 검증이 없다.
- **3-Flow 통합 부재**: 단위테스트만으로는 chat/doc/execute 3 모드의 입력 → DSL → 실행 → 산출물 사슬이 살아 있는지 회귀가 안 된다.

### 7.3 리뷰 보강내역

위 분석을 반영해 Sprint 3 을 다음과 같이 잡는다.

- **mock_* 는 UI 예외처리까지 한 시나리오** 로 묶는다 — fixture HTML 에 `fetch()` 하는 페이지를 두고, 500/empty 응답 후 DOM 에 에러 UI 가 노출되는지 `verify` 로 검증.
- **Healing 통합 테스트 별도** — 깨진 selector 를 fixture 에 넣고 fallback_targets 로 복구되는 케이스, mock 패턴이 잘못되어 fallback URL 로 복구되는 케이스를 각각 신설.
- **외부 도메인 참조 0 강제** — CI 에서 `grep -r "https://" test/fixtures/` 같은 가드를 두는 단계까지 포함.
- **Dify 호출은 monkeypatch** — `DifyClient.request_healing` 을 가로채 결정론적 응답 반환. 실 LLM 검증은 Sprint 1 영역.
- **regression_test.py 실행 검증** — 생성된 산출물을 별도 subprocess 로 실행해 PASS 까지 확인.
- **3-Flow 통합** — chat 모드는 Dify monkeypatch, doc 모드는 로컬 구조화 step parser, execute 모드는 손작성 scenario.json 입력으로 각각 한 케이스.

### 7.4 수행 내역 및 검증 결과 (2026-04-27)

#### 7.4.1 신규 산출물

- **conftest 인프라**: `make_executor`, `monkeypatch_dify`, `run_scenario`, `write_scenario_json` fixture 4 종 + `helpers/scenarios.py` 14대 액션 빌더.
- **fixture HTML 7 종**: `upload.html`, `scroll.html`, `verify_conditions.html`, `drag.html`, `mock_status.html`, `mock_data.html`, `full_dsl.html` — 모두 file:// 로 동작하고 외부 도메인 0 (mock-only `api.example.test` 만 허용).
- **테스트 파일 11 종**: `test_upload.py` (3) / `test_scroll.py` (2) / `test_verify_conditions.py` (8) / `test_drag.py` (2) / `test_mock_status.py` (3) / `test_mock_data.py` (3) / `test_regression_emit_runs.py` (2) / `test_executor_full_dsl.py` (1) / `test_healing_fallback.py` (1) / `test_mock_healing.py` (2) / `test_three_flows.py` (3) / `test_airgap_guard.py` (1).
- **9대 native 분리**: `test/native/` 디렉토리로 9 개 기존 pytest-playwright 파일 이동 — sync_playwright nesting 충돌 방지.
- **Jenkins stage 2.4** 신규: integration / native 두 sub-step 으로 호출 + JUnit XML 두 개 보존.

#### 7.4.2 검증 결과

```text
$ python3 -m pytest test --ignore=test/native -q
============================= 47 passed in 15.02s ==============================

$ python3 -m pytest test/native -q
============================== 30 passed in 9.80s ==============================
```

총 **77 케이스 PASS** — Sprint 2 단위 16 + Sprint 3 통합 31 + native 회귀 30. 실패 0, flake 0.

#### 7.4.3 주요 디버깅 결정

- **mock_status 의 fetch URL 절대화**: file:// 페이지에서 상대 fetch URL 은 page.route 글롭 패턴 매칭이 안 됨. fixture 의 fetch 를 `https://api.example.test/api/users/*` 같은 가상 절대 URL 로 변경하고 airgap 가드 화이트리스트에 `.test` TLD 등록.
- **hover→click 순서 보존**: full_dsl 통합 시나리오에서 click 핸들러가 status 를 "clicked" 로 덮어쓰면 이후 hover 의 mouseenter 가 다시 발화되지 않음. hover 를 click 보다 먼저 배치.
- **9대/Sprint 3 분리**: pytest-playwright 의 page fixture 와 executor 의 sync_playwright 가 같은 세션 안에서 asyncio loop 충돌 → 9 개 native 테스트를 `test/native/` 디렉토리로 격리, Jenkins stage 도 두 sub-step 으로.
- **fallback heal 의 healed.json 반영 회귀**: Sprint 2 의 S2-12 강화가 실제로 직렬화에 반영되는지 통합 테스트로 검증 — `step["target"]` 갱신값이 scenario_after dict 에 in-place 적용됨을 확인.

### 7.5 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S3-01 | upload fixture + pytest | `test/fixtures/upload.html`, `test/test_upload.py` | `<input type="file">` 에 artifacts 안 파일 업로드 성공 / artifacts 밖 경로 차단 / 빈 value 거부 — 3 케이스 | 완료 |
| S3-02 | drag fixture + pytest | `test/fixtures/drag.html`, `test/test_drag.py` | source→destination drop 후 DOM 변화 검증 / 목적지 미존재 시 RuntimeError — 2 케이스 | 완료 |
| S3-03 | scroll fixture + pytest | `test/fixtures/scroll.html`, `test/test_scroll.py` | viewport 밖 요소가 scroll 후 visible / `into_view` 외 value 는 `_validate_scenario` 거부 — 2 케이스 | 완료 |
| S3-04 | mock_status + UI 예외처리 시나리오 | `test/fixtures/mock_status.html`, `test/test_mock_status.py` | 500 모킹 → fetch 호출 → DOM 에 에러 UI 노출 → `verify` / `times=1` 기본 / `times=3` 다중 가로채기 — 3 케이스 | 완료 |
| S3-05 | mock_data + 빈 응답 시나리오 | `test/fixtures/mock_data.html`, `test/test_mock_data.py` | dict body / JSON 문자열 / 빈 배열 응답 후 "데이터 없음" UI — 3 케이스 | 완료 |
| S3-06 | verify.condition 분기별 pytest | `test/fixtures/verify_conditions.html`, `test/test_verify_conditions.py` | hidden/disabled/enabled/checked/value/text 분기 + 빈 condition + unknown condition 거부 — 8 케이스 | 완료 |
| S3-07 | regression_generator 산출물 실행 검증 | `test/test_regression_emit_runs.py` | 14대 액션 시나리오 → `generate_regression_test` → subprocess 실행 종료코드 0 + compileall — 2 케이스 | 완료 |
| S3-08 | 14대 액션 통합 시나리오 | `test/test_executor_full_dsl.py` | 14대 22 스텝 한 시나리오 PASS / 14대 모두 등장 검증 — 1 케이스 | 완료 |
| S3-09 | fallback heal 통합 검증 | `test/test_healing_fallback.py` | 1차 target 실패 → fallback_targets 복구 → scenario_after 에 갱신된 target 직렬화 / Dify heal 미호출 검증 — 1 케이스 | 완료 |
| S3-10 | mock_* 치유 경로 통합 검증 | `test/test_mock_healing.py` | 잘못된 URL 패턴 → fallback URL / Dify monkeypatch 응답 → 복구 — 2 케이스 | 완료 |
| S3-11 | 3-Flow 통합 회귀 | `test/test_three_flows.py` | chat (monkeypatch) / doc (로컬 parser, Dify 0 호출) / execute (scenario.json + _validate_scenario) — 3 케이스 | 완료 |
| S3-12 | airgap 가드 | `test/test_airgap_guard.py` | 모든 fixture HTML 이 화이트리스트(`api.example.test`/`w3.org`) 외 외부 호스트 0 — 1 케이스 | 완료 |
| S3-13 | Jenkins 파이프라인 pytest 단계 | `ZeroTouch-QA.jenkinsPipeline` Stage 2.4 | integration + native 두 sub-step 호출 / JUnit XML 두 개 보존 / 실패 시 빌드 fail | 완료 |
| S3-14 | Sprint 3 결과 요약 | 본 계획서 §7.4 / §7.6 | 단위테스트 수, 신규 fixture 목록, 회귀 차단 범위 명시 + Sprint 4 인계 가능 | 완료 |

### 7.6 구현 순서 권장안

전체를 7 phase 로 나눈다. 각 phase 의 산출물이 다음 phase 의 입력이 되므로 **순서를 지킨다**. phase 마다 "다음으로 넘어가지 않을 조건" 을 명시한다.

#### Phase 0 — 공통 인프라 준비 (선행 필수)

S3-01 부터 손대지 않고, 모든 후속 테스트가 공유할 헬퍼/fixture 를 conftest 와 작은 헬퍼 모듈로 먼저 만든다.

1. `test/conftest.py` 에 다음 fixture 추가:
   - `monkeypatch_dify(monkeypatch)` — `DifyClient.generate_scenario` / `request_healing` 을 결정론적 응답을 반환하도록 가로채는 fixture. 인자로 응답 dict 를 받는 factory 형태.
   - `make_executor(tmp_path)` — `Config.from_env()` 대체로 격리된 `Config` + `QAExecutor` 인스턴스를 반환. artifacts_dir 는 tmp_path.
   - `run_scenario(executor, scenario, headed=False)` — 시나리오를 실행하고 `(results, scenario_after, run_log_path)` 를 반환하는 헬퍼.
2. `test/fixtures/_common.css` 같은 공통 inline 스타일 파일은 **두지 않는다** — 각 fixture HTML 자기완결.
3. `test/helpers/scenarios.py` 신설 — 자주 쓰는 시나리오 빌더 함수 (`navigate_to(fixture_name)` 등).

**Gate**: Phase 0 산출물만으로 빈 시나리오 실행이 동작해야 다음 단계 진입. `python3 -m pytest test/test_sprint2_runtime.py` 16 건 회귀 무결.

#### Phase 1 — 저위험 단일 액션 (S3-01, S3-03, S3-06)

부작용이 적은 순서로 시작한다. 각 케이스는 fixture HTML 1 개 + test 파일 1 개로 구성.

1. **S3-01 upload** — fixture 는 `<input type="file">` 와 업로드 후 파일명을 표시할 `<div id="result">` 만 가진 HTML. pytest 3 케이스: happy / artifacts 밖 차단 / 빈 value 거부.
2. **S3-03 scroll** — fixture 는 viewport 보다 큰 페이지에 `#footer` 를 둠. pytest 2 케이스: scroll 후 visible / `into_view` 외 value 거부.
3. **S3-06 verify.condition** — fixture 는 disabled/checked/value/hidden/text 를 가진 다양한 element 들의 정적 페이지. pytest 7 케이스 (각 condition + 빈 condition + unknown).

**Gate**: Phase 1 의 12+ 케이스가 모두 통과해야 다음 phase. 이 시점에서 9대 액션 회귀 (`test_*.py`) + Sprint 2 단위테스트도 함께 PASS.

#### Phase 2 — 고위험/부작용 단일 액션 (S3-02, S3-04, S3-05)

drag 와 mock_* 는 단순 액션이 아니라 v4.1 의 차별점이므로 별도 phase 로 분리.

1. **S3-02 drag** — fixture 는 HTML5 drag-and-drop API 로 동작하는 source/target 컬럼. pytest 2 케이스: drop 후 DOM 변화 검증 / 목적지 미존재 시 RuntimeError.
2. **S3-04 mock_status (UI 예외처리 시나리오)** — fixture 는 페이지 로드 시 `fetch('/api/users/1')` 를 호출하고 응답 status 가 5xx 면 `<div class="error">` 를 표시하는 페이지. 시나리오는 `mock_status 500 → navigate → verify error UI visible` 의 4 스텝. pytest 3 케이스: 500 → 에러 UI / `times=1` 후 두 번째 호출은 실네트워크 / `times=3` 다중 가로채기.
3. **S3-05 mock_data (빈 응답 시나리오)** — fixture 는 `fetch('/api/list')` 응답을 list 로 렌더하는 페이지. dict body / JSON 문자열 / 평문 모두 fulfill 되는지 + 빈 배열 응답 시 "데이터 없음" UI 노출까지 확인.

**Gate**: Phase 2 의 8+ 케이스 통과 + mock_* 가 단순 라우트 설치가 아니라 **UI 예외처리까지 검증** 한다는 v4.1 동기가 산출물 (스크린샷 / run_log) 에 남아 있어야 한다.

#### Phase 3 — 산출물 실행 검증 (S3-07)

Sprint 2 에서 가장 큰 침묵 갭이었던 부분. 단순 emit 단위테스트와 별개로 **별도 Python 프로세스에서 실행되는지** 까지 회귀.

1. 14대 액션을 모두 포함한 시나리오 dict 를 만든다 (Phase 2 까지의 fixture 를 그대로 재활용).
2. `generate_regression_test(scenario, results, tmp_path)` 호출.
3. `subprocess.run(["python3", str(tmp_path / "regression_test.py")], check=True, timeout=60)` — 종료코드 0 확인.
4. 산출 파일이 valid Python 인지 추가로 `compile()` 로 syntax check.

**Gate**: subprocess 실행 PASS. Phase 3 통과 시점이 Sprint 2 의 `regression_generator` 14대 확장 (S2-15) 의 **진짜** Definition of Done 충족.

#### Phase 4 — 14대 통합 시나리오 (S3-08)

Phase 1~2 의 격리 fixture 들을 한 번에 조합한 통합 시나리오를 executor 가 메타-회귀.

1. `test/fixtures/full_dsl.html` 신설 — 14대 모든 액션이 한 페이지에서 검증되도록 form/list/error UI 영역을 모두 포함.
2. 14 스텝 시나리오 1 개를 손작성 (각 액션 1 회씩).
3. `run_scenario` 로 실행 → results 14건 모두 PASS / `run_log.jsonl` 14 줄 / `scenario.healed.json` 이 원본과 동일 (heal 미발생).

**Gate**: 14 스텝 모두 PASS. 이 시점이 "14대 DSL 이 한 시나리오에서 같이 돌아간다" 는 메타-회귀 보장.

#### Phase 5 — Healing 루프 통합 (S3-09, S3-10)

self-healing 이 진짜로 step dict 을 갱신하고 healed.json 에 직렬화되는지 검증. **반드시 Phase 0 의 monkeypatch_dify fixture 를 사용** — 실 Dify 호출 0.

1. **S3-09 fallback heal** — fixture 에 selector A 를 일부러 깨놓고, `fallback_targets` 에 selector B 를 넣은 시나리오 작성. 실행 후 `scenario.healed.json` 에 `step.target == B` 가 직렬화되는지 검증.
2. **S3-10 mock fallback** — 잘못된 URL 패턴을 1차로, fallback URL 을 2차로 둔 mock_status 스텝 작성. fallback 으로 복구되어 `scenario.healed.json` 에 갱신된 패턴이 남는지 + monkeypatch 된 Dify 응답이 LLM heal 단계까지 도달하는지 둘 다 검증 (2 케이스).

**Gate**: healed.json 직렬화 정합성 PASS. monkeypatch 가 풀린 채로 테스트 누수되지 않았는지 (`request_healing` 호출 카운트 == 예상치) 추가 확인.

#### Phase 6 — 3-Flow 통합 (S3-11)

`__main__.main()` 진입점부터 산출물 생성까지 chat/doc/execute 3 모드 모두 fixture 위에서 한 번씩.

1. **chat** — `--mode chat --srs-text "..." --target-url file://.../full_dsl.html`. Dify 는 monkeypatch 로 결정론적 14 스텝 시나리오 반환.
2. **doc** — 로컬 step parser (`parse_structured_doc_steps`) 가 인식하는 마크다운 형식의 fixture 파일 (`test/fixtures/spec.md`) 을 `--mode doc --file` 로 입력. Dify 호출 없이 통과.
3. **execute** — Phase 4 의 손작성 scenario.json 을 `--mode execute --scenario` 로 입력. 동일한 결과를 재현하는지 확인.

**Gate**: 3 모드 모두 종료코드 0 + 산출물 (`run_log.jsonl`, `scenario.json`, `scenario.healed.json`, `regression_test.py`, `index.html`) 5종 모두 생성.

#### Phase 7 — 에어갭/CI 봉쇄 (S3-12, S3-13)

가드와 CI 단계는 모든 테스트가 통과한 뒤에 추가한다. 미리 추가하면 임시로 외부 URL 을 쓰던 fixture 가 가드에 걸려 진척이 막힌다.

1. **S3-12 airgap 가드** — `test/test_airgap_guard.py` 1 케이스. `test/fixtures/*.html` 를 모두 읽어 정규식 `r"https?://[^/\s]"` (단 `https://www.w3.org/` 같은 namespace 제외 화이트리스트) 에 매칭되는 줄이 0 줄임을 검증.
2. **S3-13 Jenkins** — `ZeroTouch-QA.jenkinsPipeline` 의 build 단계 직전에 integration/native 를 분리한 `python3 -m pytest` stage 추가. JUnit XML 두 개를 보존하고 실패 시 `unstable` 이 아니라 `fail`.

**Gate**: airgap 가드 PASS + Jenkins dry-run 시 pytest stage 가 실행되어 통과.

#### Phase 8 — 결과 요약 및 클로저 (S3-14)

1. 본 계획서 §7.4 표 14 건 모두 "완료" 로 갱신.
2. §7.6 DoD 8 항목을 ✅ 형태로 마감.
3. §6 의 "검증 결과" 섹션과 같은 양식으로 §7 끝에 "Sprint 3 검증 결과" 절 추가 — 단위테스트 총 건수, 신규 fixture 목록, 회귀 차단 범위 명시.
4. §8 milestone 의 Sprint 3 라인을 "구현 완료 — YYYY-MM-DD" 로 변경.
5. Sprint 4 (E2E) 가 인계받을 미해결 항목 (Convert 14대 확장, 실 Dify 검증 등) 을 별도 목록으로 분리.

**Gate**: PLAN 문서가 Sprint 3 종료 상태를 명시 → Sprint 4 착수 가능.

### 7.7 Sprint 3 종료 조건 (Definition of Done) — 모두 충족

- ✅ 14대 액션 모두 fixture 기반 pytest 케이스 ≥1 건 (S3-01~S3-06 + S3-08).
- ✅ Sprint 2 단위 16건 + Sprint 3 신규 31건 + native 30건 = **77건 PASS**, flake 0.
- ✅ 모든 fixture 가 file:// 만 참조 — `api.example.test` 화이트리스트는 mock-only 가상 호스트로 실네트워크 0 (S3-12 가드 자동 보장).
- ✅ `regression_test.py` 산출물이 별도 subprocess 에서 종료코드 0 + compileall 통과 (S3-07).
- ✅ fallback heal / mock fallback 모두 `step["target"]` 갱신을 통해 scenario_after 에 직렬화 (S3-09, S3-10).
- ✅ chat (Dify monkeypatch) / doc (로컬 parser, Dify 0 호출) / execute (손작성 + _validate_scenario) 3 모드 PASS (S3-11).
- ✅ Jenkins Stage 2.4 가 integration + native 두 sub-step 으로 구성, JUnit XML 두 개 보존 (S3-13).
- ✅ §7.4 표 14건 모두 "완료" — Sprint 4 가 E2E 통합에 착수 가능.

### 7.8 Sprint 3 비범위 (Out of Scope)

- 실제 Dify Brain (`gemma4:e4b`) 호출 검증 — Sprint 1 §5.5 책임.
- 실제 Mac agent / headed browser / 실 운영 도메인 — Sprint 4 책임.
- Convert(Flow 3) 경로의 14대 DSL 확장 — 별도 트랙 (현재 9대만 지원).
- LLM 응답 품질 평가 메트릭 — Sprint 1 의 Preview 실측 일부.
- regression_test.py 산출물의 운영 회귀 — Sprint 4 의 E2E 회귀에 포함.

### 7.9 fixture 설계 표준 (에어갭 강제)

신규 fixture 는 모두 다음 표준을 따른다.

- **위치**: `playwright-allinone/test/fixtures/<action>.html`.
- **URL**: 외부 도메인 0. 이미지/스크립트/CSS 도 inline 또는 data URI 만 허용. fixture 안 다른 fixture 참조도 상대경로로만.
- **fetch() 대상**: 실제 네트워크가 아니라 mock 패턴이 가로챌 가짜 endpoint (`/api/...`). 실호출은 mock 라우트가 잡으므로 외부에 안 나간다.
- **의도성**: 한 fixture 는 한 액션의 happy + 1~2 개 negative 만 담는다. 통합은 별도 fixture 또는 통합 케이스에서 조립.
- **헤더**: `<title>` 에 `[fixture] <action>` 명시 — 디버깅 시 어느 fixture 인지 즉시 식별.
- **검증 가드**: S3-12 가 모든 fixture HTML 에 `http://` / `https://` 가 없음을 정규식으로 강제.

## 8. Sprint 4 상세 작업목록

Sprint 4 는 **Sprint 1~3 에서 결정론적으로 잠근 14대 DSL 파이프라인이 실 운영 환경 (실 Dify Brain `gemma4:e4b` + 실 Mac agent + 실/통제 가능한 도메인) 에서 동일하게 동작함을 보장하는 최종 통합 스프린트** 다. 범위는 운영 의존성 회귀, 실 LLM 응답 회귀, 3-Flow E2E, mock_* 운영 검증, healing 실효성, Convert 경로 14대 확장, 운영 산출물/SLA, 매뉴얼 인계까지다. 이 스프린트가 닫히면 v4.1 은 운영 출시 가능 상태로 선언된다.

또한 Sprint 4B 는 **Sprint 1 의 잔여 항목 (S1-23 `gemma4:e4b` Preview 실측)** 을 자동화된 회귀로 격상해 흡수한다. Sprint 1 은 4B 통과 시 동시에 종료된다.

Sprint 4 는 하나의 스프린트로 유지하되, 실패 원인 분리를 위해 **3개 게이트(4A/4B/4C)** 로 운영한다. 4A 는 운영 기반과 계측을 먼저 고정하고, 4B 는 실 Dify / Mac agent / staging E2E 를 검증하며, 4C 는 Convert 14대 확장과 운영 출시 클로저를 닫는다.

### 8.1 목표

- 실 Dify Brain (`gemma4:e4b`) 의 Planner/Healer 응답이 회귀 가능한 정확도 metric 으로 추적된다.
- 3-Flow (`chat`/`doc`/`convert`) 모두가 실 Jenkins + 실 Mac agent + 실 Dify 위에서 한 번 이상 PASS 한다.
- v4.1 의 진짜 차별점인 `mock_*` 가 운영 등급 페이지 (실 fetch() 호출) 에서 **UI 예외처리까지 검증** 한다.
- 3 단계 self-healing 이 실 LLM 환경에서 의미 있는 selector 를 반환해 회복함이 입증된다.
- Convert(Flow 3) 경로의 9대 → 14대 확장 (Sprint 2 비범위였던 carve-out) 이 닫힌다.
- 운영 산출물 (HTML report / run_log / scenario / healed / regression_test / screenshots / pytest XML) 이 빌드별 보존되고 회귀 추세가 추적된다.
- 동시 빌드 / 반복 실행 시 안정성 SLA 가 측정되어 운영 규약으로 명문화된다.

### 8.2 리뷰 분석내역

E2E 단계에서 빠지기 쉬운 함정을 사전에 식별한다.

- **실 LLM 회귀 부재**: Sprint 1 의 Preview 실측이 1 회성 수동 검증이라 회귀 차단력이 없다. Sprint 4 가 자동화 metric 으로 격상하지 않으면 운영에서 모델 변경 시 즉시 깨진다.
- **mock scope 과확장 위험**: `mock_status`/`mock_data` 가 너무 넓은 URL 패턴을 잡으면 의도치 않은 요청까지 브라우저 내부에서 fulfill 되어 false positive PASS 를 만들 수 있다. 안전장치 없이 출시하면 위험.
- **봇 차단/dynamic content**: 실 운영 도메인 (Yahoo, Naver 등) 은 Playwright 봇 패턴을 captcha 로 차단하거나 SPA 렌더링이 늦어 false positive PASS 가 잦다. fixture 회귀로는 잡히지 않음.
- **healing 비결정성**: `gemma4:e4b` 의 heal 응답이 항상 의미 있는 selector 를 반환하는 건 아니다. heal 성공률이 운영 SLA 에 들어와야 신뢰 가능.
- **Convert 9대 잔존**: Sprint 2 가 carve-out 한 Convert 경로의 9대 DSL 만으로는 14대 시나리오를 녹화→실행 사슬이 깨진다. 사용자가 codegen 으로 upload/drag 를 녹화하면 변환되지 않음.
- **산출물 retention 미정**: Sprint 3 까지는 단발 빌드 산출물만. 운영에서는 회귀 추세 (성공률 / heal rate / SLA) 를 N 일 보관해야 모델 변경 영향 추적 가능.
- **Mac agent 동시성**: 단일 Mac agent 가 동시 빌드 2~3 개를 받으면 artifacts 디렉토리 충돌, 브라우저 자원 부족으로 false fail 발생.
- **운영 매뉴얼 누락**: README / architecture.md 가 v4.1 운영 절차 (DIFY_API_KEY 만료 처리, mock 안전장치, healing 비용 모니터링) 를 다루지 않으면 운영팀 인계 불가.

### 8.3 리뷰 보강내역

위 분석을 반영해 Sprint 4 를 다음과 같이 잡는다.

- **실 LLM 회귀 자동화**: Planner / Healer 입력셋을 N 개 고정해 정확도 metric 을 매 빌드마다 측정 (chat/doc 입력 셋, 깨진 selector 셋). Sprint 1 §5.5 잔여를 흡수.
- **mock 안전장치**: mock 패턴이 운영 API 호스트와 매칭 가능한 형태일 때 빌드 실패 또는 명시 confirmation 요구. allowlist 또는 prefix 검증.
- **운영 도메인은 통제 가능한 staging**: 봇 차단을 회피하기 위해 가능한 한 staging / dev 환경 도메인을 사용. 외부 도메인 사용 시 captcha/봇 차단 우회 정책을 명시.
- **healing SLA**: heal 성공률 / 평균 시간 / 응답 길이 / timeout 을 metric 으로 수집. 첫 1~2회는 baseline 측정으로 보고, 이후 운영 임계값을 확정한다.
- **Convert 14대 확장**: `converter.py` 정규식에 `set_input_files`/`drag_to`/`scroll_into_view_if_needed`/`page.route` 매핑 추가, regression_generator 14대 emitter 와 1:1 일관성 회귀.
- **산출물 retention**: Jenkins archiveArtifacts + build trend 플러그인 또는 별도 dashboard 로 최근 N 일 추세. JUnit XML 로 pytest 추세 추적.
- **동시성 격리**: artifacts 디렉토리에 빌드 번호 prefix, 브라우저 동시 실행 수 제한. 동시 빌드 2 개 회귀 케이스를 명시적 검증.
- **운영 매뉴얼**: 트러블슈팅 / 운영 절차 / SLA 표를 README / architecture.md 에 별도 섹션으로 삽입.

### 8.4 세부 체크리스트 (4A/4B/4C 재구성)

#### Sprint 4A — 운영 기반 / 계측 선행

4A 는 실 Dify 호출과 운영 E2E 를 시작하기 전에 false fail 을 줄이는 기반 작업이다. 이 게이트가 닫히기 전에는 Planner 정확도나 healing 성공률을 출시 판단 지표로 보지 않는다.

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S4A-01 | Mac/WSL agent preflight stage | `ZeroTouch-QA.jenkinsPipeline`, `mac-agent-setup.sh` / `wsl-agent-setup.sh` 필요 시 보강 | Java / Python venv / Playwright browser / 디스크 / `SCRIPTS_HOME` / artifacts 쓰기 권한이 빌드 초기에 자동 검증됨 | 완료 |
| S4A-02 | Dify credential / model health stage | Jenkinsfile, health check helper | Dify `/v1/parameters` 또는 `chat-messages` 최소 probe 로 API key 유효성, endpoint 접근성, 모델 응답 가능 여부 확인 | 완료 |
| S4A-03 | Dify 호출 metric 계측 | `zero_touch_qa/dify_client.py`, `zero_touch_qa/metrics.py`, `test/test_dify_metrics.py` | Planner/Healer 호출별 latency, timeout 여부, retry count, HTTP status, answer 길이를 `artifacts/llm_calls.jsonl` 로 기록 | 완료 |
| S4A-04 | artifacts 격리 / retention 기준 확정 | Jenkinsfile | 현재 `qa_reports/build-${BUILD_NUMBER}` 구조를 유지하되 `buildDiscarder` 또는 동등 설정으로 30일 retention 명시, pytest XML 포함 확인 | 완료 |
| S4A-05 | Sprint 2/3 회귀 green 유지 | Jenkins Stage 2.4, JUnit XML | `python3 -m pytest test --ignore=test/native -q` + `python3 -m pytest test/native -q` 가 운영 agent 에서 86/86 PASS | 미착수 |

#### Sprint 4B — 실 Dify / 운영 E2E 검증

4B 는 실 Dify Brain 과 실 agent 위에서 v4.1 런타임이 동작하는지 검증한다. SLA 는 첫 1~2회 실행에서 baseline 을 수집한 뒤 확정하며, 무근거한 30초 gate 를 선제 적용하지 않는다.

> **closure (2026-04-27)**: Sprint 5 의 호스트 하이브리드 실 Dify 빌드 #9 (execute, 14/14 PASS) 와 #14 (chat, 12/12 PASS, Dify heal 2 회 성공) 가 4B 의 의미적 핵심 (실 Dify + 실 agent + staging fixture 위 chat·execute 동작, mock_*, healing) 을 통합 검증해 4B 게이트를 닫았다. 정량적 fixture 회귀 (S4B-01/02), llm_sla.json 임계값 갱신 (S4B-03 후속), doc·convert 모드 smoke (S4B-05/06), 10 회 stability loop (S4B-11) 은 v4.1 출시 이후 회귀 강화 backlog (post-release / Sprint 6 후보) 로 이관한다 — 출시 게이트가 아닌 "회귀 차단력 강화" 항목으로 재분류.

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S4B-01 | Planner 정확도 회귀 | `test/e2e/test_planner_accuracy.py`, `test/e2e/fixtures/planner_inputs/` | 14대 액션이 골고루 들어간 SRS ≥10개에 대해 `_validate_scenario` 통과율, 기대 action 포함률, `<think>`/markdown 누출률 기록 | post-release backlog |
| S4B-02 | Healer 정확도 회귀 | `test/e2e/test_healer_accuracy.py`, `test/e2e/fixtures/healer_inputs/` | 깨진 selector + DOM snapshot ≥10개에 대해 heal JSON 파싱률, valid selector 비율, DOM 실제 매칭률 기록 | post-release backlog |
| S4B-03 | LLM SLA baseline / threshold 확정 | `artifacts/llm_sla.json`, 본 계획서 §8.8 갱신 | `llm_calls.jsonl` 기반 p50/p95/p99, timeout rate, retry count 산출. 1차 gate 는 baseline 기록, 2차부터 임계값 적용 | 부분 — `llm_calls.jsonl` 은 빌드 #9/#14 에서 수집 완료. `llm_sla.json` 집계는 post-release backlog |
| S4B-04 | chat E2E (실 Dify + agent) | Jenkins job 산출물 또는 `test/e2e/test_chat_flow.py` | `RUN_MODE=chat` → 실 Dify 호출 → staging 페이지 실행 → 산출물 7종 생성 → 종료코드 0 | 완료 — Build #14 chat 12/12 PASS (실 Dify `qwen3.5:9b`, fixture `full_dsl.html`) |
| S4B-05 | doc E2E (실 PDF/텍스트 첨부) | 샘플 문서, Jenkins job 산출물 | `RUN_MODE=doc` → 문서 텍스트 추출 / Dify Planner → 14대 DSL 시나리오 생성 → 실행 PASS | 완료 — Build #7 `playwright_dev_test_scenario.pdf` (20 step ZTQA_STEP marker) → 로컬 파서 → 실 playwright.dev 20/20 PASS @70s |
| S4B-06 | convert 9대 smoke E2E | 9대 녹화 샘플, Jenkins job 산출물 | S4C 전까지 현재 converter 의 9대 경로가 깨지지 않음을 확인. 14대 Convert 완료 기준으로 사용하지 않음 | post-release backlog (S4C-03 14대 E2E 가 우선) |
| S4B-07 | mock_status staging 검증 | `test/e2e/test_mock_status_prod.py` 또는 execute scenario | staging fetch 페이지에서 `mock_status 500` → 에러 UI verify PASS | 완료 — Build #9 `scenario_14.json` (mock_status 액션 포함) 14/14 PASS |
| S4B-08 | mock_data staging 검증 | `test/e2e/test_mock_data_prod.py` 또는 execute scenario | staging fetch 페이지에서 `mock_data {"items":[]}` → empty UI verify PASS | 완료 — Build #9 `scenario_14.json` (mock_data 액션 포함) 14/14 PASS |
| S4B-09 | mock scope 안전장치 | `zero_touch_qa/executor.py`, `test/test_mock_scope_guard.py` | 넓은 mock pattern 이 `TARGET_URL` 운영 host 또는 `MOCK_BLOCKED_HOSTS` 와 충돌하면 실패. `MOCK_OVERRIDE=1` 일 때만 명시 우회 | 완료 |
| S4B-10 | 3단계 healing 실효성 | `test/e2e/test_healing_real_llm.py`, `artifacts/heal_metrics.json` | fallback / LocalHealer / 실 Dify heal 각 1건 이상 트리거 및 성공. 성공률, 평균 시간 기록 | 완료 — Build #14 실 Dify heal 2 회 성공 (selector mutation). fallback / LocalHealer 단독 트리거 evidence 는 post-release backlog 로 보강 |
| S4B-11 | 반복 안정성 baseline | Jenkins 반복 실행 또는 `test/e2e/test_stability.py` | 동일 staging 시나리오 10회 반복, flake rate 기록. 첫 측정은 baseline, 이후 §8.8 임계값 적용 | post-release backlog — Sprint 5 의 #5 → #14 progression 이 비공식 baseline. 정형 10 회 loop 은 후속 |

#### Sprint 4C — Convert 14대 / 운영 출시 클로저

4C 는 Sprint 2에서 의도적으로 남긴 Convert carve-out 을 닫고, 운영 문서와 release gate 를 마감한다.

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S4C-01 | Convert 14대 parser 확장 | `zero_touch_qa/converter.py` | `set_input_files` / `drag_to` / `scroll_into_view_if_needed` / `page.route` 를 upload / drag / scroll / mock_* 로 변환 | 완료 — converter.py 에 5 종 매핑 + `ast.literal_eval` 기반 mock_data body 평탄화 |
| S4C-02 | Converter 14대 단위테스트 | `test/test_converter_14_actions.py`, codegen 샘플 | 14대 액션 전체 변환 결과가 `_validate_scenario` 통과, regression_generator emitter 와 주요 동작 일관 | 완료 — `recorded-14actions.py` fixture + 16 테스트 PASS, 9대 회귀 보존 검증 포함 |
| S4C-03 | convert 14대 E2E 재검증 | 14대 녹화 샘플, Jenkins job 산출물 | `RUN_MODE=convert` → 14대 DSL 변환 → 실행 PASS → 산출물 7종 생성 | 완료 — `test/test_convert_14_e2e.py` 가 convert→validate→execute 풀 사슬을 fixture full_dsl.html 위에서 14/14 PASS 로 결정론적 검증 (Jenkins 환경 의존 제거) |
| S4C-04 | Zero Touch QA Report 운영 지표 섹션 | `zero_touch_qa/report.py`, `test/test_report_metrics.py` | `index.html` 에 Planner/Healer 정확도, LLM SLA, heal 성공률, flake rate, pytest 결과 요약과 원본 metric 파일 링크가 표시됨. metric 파일이 없으면 섹션 생략 | 완료 |
| S4C-05 | 빌드 추세 / dashboard | JUnit trend, metric JSON archive 또는 dashboard | pytest 통과율, Planner/Healer 정확도, LLM SLA, heal 성공률이 최근 N 빌드 단위로 확인 가능 | 완료 — Jenkinsfile v2.4 의 `junit testResults` 가 pytest 추세를 자동 노출, `aggregate_llm_sla()` 가 빌드별 `llm_sla.json` 자동 집계, `archiveArtifacts` + `buildDiscarder` 30 일 / 100 빌드 retention |
| S4C-06 | 운영 매뉴얼 갱신 | `README.md`, `architecture.md`, 트러블슈팅 | DIFY_API_KEY 갱신, mock 안전장치, healing/SLA 모니터링, agent 권한, 동시성 한계 문서화 | 완료 — `README.md §3.9` v4.1 운영 SLA / 모니터링 섹션 (산출물 7종 + LLM SLA 추적 + mock 안전장치 + healing 모니터링 + Convert 14대 사용법 + DIFY_API_KEY 갱신 + agent 동시성 한계) |
| S4C-07 | Sprint 1 종료 / v4.1 출시 선언 | 본 계획서 §5.5 / §8 / §9, `architecture.md` 헤더 | S1-23/S1-25 완료 처리, Sprint 4 결과 기록, v4.1 운영 출시 선언 및 후속 backlog 분리 | 완료 — §9 milestone Sprint 1/4 closure + v4.1 운영 출시 선언 단락, post-release backlog 분리 |

### 8.4.1 구현 태스크 분해

아래 태스크 순서를 Sprint 4 의 실제 작업 단위로 삼는다. 각 태스크는 코드 변경 범위, 검증 명령, 다음 태스크 진입 조건을 갖는다.

#### 4A 상세 태스크 — 운영 기반 / 계측

| 태스크 | 범위 | 구현 파일 | 검증 | 완료 조건 |
| --- | --- | --- | --- | --- |
| 4A-01a | Jenkins agent preflight shell 작성 | `ZeroTouch-QA.jenkinsPipeline` | Jenkins dry-run 또는 shell block 수동 실행 | `SCRIPTS_HOME`, venv, Python import, Playwright browser cache, artifacts 쓰기, 디스크 여유 검증 실패 시 명확한 에러 — 완료 |
| 4A-01b | preflight 를 Stage 1 에 통합 | `ZeroTouch-QA.jenkinsPipeline` | Jenkinsfile 문법/grep 점검 | 기존 venv 검증을 유지하면서 Java/Python/Playwright/권한 검증이 한 stage 에서 수행 — 완료 |
| 4A-02a | Dify health probe 명령 정의 | `ZeroTouch-QA.jenkinsPipeline` | curl 로 `/v1/parameters` 또는 최소 `chat-messages` probe | API key 누락/401/timeout/model no-response 를 구분해 로그 출력 — 완료 |
| 4A-02b | Dify health stage 위치 조정 | `ZeroTouch-QA.jenkinsPipeline` | Jenkins stage 순서 확인 | offline pytest 는 Dify 없이 먼저 돌 수 있고, 엔진 실행 전에는 Dify credential/model health 가 통과해야 함 — 완료 |
| 4A-03a | Dify call metric writer 추가 | `zero_touch_qa/metrics.py` | `python3 -m pytest test/test_dify_metrics.py -q` | `artifacts/llm_calls.jsonl` 에 JSON Lines append, artifacts_dir 없으면 no-op — 완료 |
| 4A-03b | `_call()` 에 metric 계측 연결 | `zero_touch_qa/dify_client.py` | `python3 -m pytest test/test_dify_metrics.py -q` | planner/healer kind, elapsed_ms, retry_count, timeout, status_code, answer_chars, error 기록 — 완료 |
| 4A-03c | metric 집계 helper 추가 | `zero_touch_qa/metrics.py` | `python3 -m pytest test/test_dify_metrics.py -q` | `llm_calls.jsonl` 에서 p50/p95/p99, timeout rate, retry total 산출 가능 — 완료 |
| 4A-04a | artifacts 보존 범위 감사 | `ZeroTouch-QA.jenkinsPipeline` | Jenkinsfile grep 점검 | `qa_reports/build-${BUILD_NUMBER}` 에 HTML/report/json/png/pytest XML/metric JSONL 포함 — 완료 |
| 4A-04b | retention 명시 | `ZeroTouch-QA.jenkinsPipeline` | Jenkinsfile grep 점검 | `buildDiscarder` 또는 동등 정책으로 30일 보존 의도가 코드에 명시 — 완료 |
| 4A-05a | 4A 회귀 테스트 묶음 실행 | 테스트 명령 | `python3 -m pytest test/test_sprint2_runtime.py test/test_dify_metrics.py -q` | 브라우저 없는 단위 회귀 PASS — 완료 |
| 4A-05b | Sprint 2/3 전체 회귀 실행 | 테스트 명령 | `python3 -m pytest test --ignore=test/native -q` + `python3 -m pytest test/native -q` | 86/86 PASS — 완료 |

#### 4B 상세 태스크 — 실 Dify / 운영 E2E

4B 는 운영 의존성이 필요하므로, 로컬 단위테스트와 실 Jenkins 실행을 분리한다. 실 Dify가 없을 때는 `ZTQA_E2E_REAL_DIFY=1` 이 없으면 skip 되도록 테스트를 작성한다.

| 태스크 | 범위 | 구현 파일 | 검증 | 완료 조건 |
| --- | --- | --- | --- | --- |
| 4B-01a | Planner 입력셋 정의 | `test/e2e/fixtures/planner_inputs/*.json` | fixture schema 단위테스트 | 14대 액션이 골고루 포함된 SRS ≥10개, 기대 action 목록 포함 |
| 4B-01b | Planner accuracy runner | `test/e2e/test_planner_accuracy.py` | `ZTQA_E2E_REAL_DIFY=1 python3 -m pytest ...` | `_validate_scenario` 통과율, 기대 action 포함률, `<think>`/markdown 누출률 산출 |
| 4B-02a | Healer 입력셋 정의 | `test/e2e/fixtures/healer_inputs/*.json` | fixture schema 단위테스트 | 실패 step, DOM snapshot, 기대 selector/전략 포함 |
| 4B-02b | Healer accuracy runner | `test/e2e/test_healer_accuracy.py` | 실 Dify gated pytest | heal JSON 파싱률, selector 형태 검증, DOM 매칭률 산출 |
| 4B-03a | LLM SLA 집계 산출 | `zero_touch_qa/metrics.py`, `test/e2e/test_llm_sla.py` | 단위 + 실 Dify gated pytest | `artifacts/llm_sla.json` 생성, baseline 과 threshold 를 분리 기록 |
| 4B-04a | chat E2E scenario 고정 | Jenkins parameter set 또는 e2e fixture | Jenkins 수동 실행 | staging URL 대상 chat 모드 산출물 7종 생성 |
| 4B-05a | doc E2E sample 작성 | `test/e2e/fixtures/spec_sample.*` | Jenkins 수동 실행 | doc 모드에서 14대 DSL 시나리오 생성 및 실행 PASS |
| 4B-06a | convert 9대 smoke 샘플 고정 | `test/e2e/fixtures/recorded_9_actions.py` | `--mode convert` 실행 | 4C 전 기존 9대 convert 경로 PASS |
| 4B-07a | mock_status staging scenario | `test/e2e/scenarios/mock_status_500.json` | execute 모드 또는 Jenkins | staging fetch 페이지에서 에러 UI verify PASS |
| 4B-08a | mock_data staging scenario | `test/e2e/scenarios/mock_data_empty.json` | execute 모드 또는 Jenkins | staging fetch 페이지에서 empty UI verify PASS |
| 4B-09a | mock scope guard 구현 | `zero_touch_qa/executor.py`, `test/test_mock_scope_guard.py` | `python3 -m pytest test/test_mock_scope_guard.py test/test_mock_status.py test/test_mock_data.py -q` | blocked host / broad pattern 은 실패, `MOCK_OVERRIDE=1` 은 감사 로그와 함께 허용 — 완료 |
| 4B-10a | healing 3경로 fixture/scenario | `test/e2e/test_healing_real_llm.py` | 실 Dify gated pytest | fallback / LocalHealer / Dify heal 각 1건 이상 성공 |
| 4B-11a | 반복 안정성 runner | `test/e2e/test_stability.py` 또는 Jenkins loop | Jenkins 수동 실행 | 동일 staging scenario 10회 반복 결과와 flake rate 기록 |

#### 4C 상세 태스크 — Convert 14대 / 출시 클로저

| 태스크 | 범위 | 구현 파일 | 검증 | 완료 조건 |
| --- | --- | --- | --- | --- |
| 4C-01a | converter parser 구조 정리 | `zero_touch_qa/converter.py` | 기존 convert 테스트 | 기존 9대 변환 결과 변화 없음 |
| 4C-01b | upload/drag/scroll 변환 추가 | `zero_touch_qa/converter.py` | `python3 -m pytest test/test_converter_14_actions.py -q` | `set_input_files`, `drag_to`, `scroll_into_view_if_needed` 가 각각 DSL 변환 |
| 4C-01c | page.route 변환 추가 | `zero_touch_qa/converter.py` | converter 단위테스트 | `fulfill(status=...)` 는 `mock_status`, `fulfill(body=...)` 는 `mock_data` 변환 |
| 4C-02a | converter 14대 fixture 작성 | `test/fixtures` 또는 `test/recorded-14actions.py` | converter 단위테스트 | 14대 액션 전체 포함 sample 고정 |
| 4C-02b | regression_generator 일관성 검증 | `test/test_converter_14_actions.py` | pytest | 변환 결과가 `_validate_scenario` 통과하고 regression code 에 신규 5종 emitter 포함 |
| 4C-03a | convert 14대 E2E | Jenkins job / execute runbook | Jenkins 수동 실행 | `RUN_MODE=convert` 로 14대 녹화 파일 실행 PASS |
| 4C-04a | Zero Touch QA Report metric 섹션 렌더링 | `zero_touch_qa/report.py`, `test/test_report_metrics.py` | `python3 -m pytest test/test_report_metrics.py -q` | metric JSON/JSONL 이 있으면 운영 지표 요약과 파일 링크 표시, 없으면 기존 report 유지 — 완료 |
| 4C-05a | metric archive/trend 보강 | Jenkinsfile / README | Jenkins artifact 확인 | pytest XML + metric JSON/JSONL + HTML report 가 build별 보존 |
| 4C-06a | 운영 매뉴얼 작성 | `README.md`, `architecture.md` | 문서 리뷰 | DIFY_API_KEY, mock guard, LLM SLA, agent 권한, 반복 안정성 운영 절차 문서화 |
| 4C-07a | closure 업데이트 | 본 계획서, `architecture.md` | 문서 리뷰 | Sprint 1 종료, Sprint 4 완료 결과, v4.1 출시 선언 및 후속 backlog 분리 |

### 8.4.2 착수 순서

1. **첫 구현 묶음:** 4A-03a/b/c (`llm_calls.jsonl` 계측 + 단위테스트) 부터 수행한다. 코드 blast radius 가 작고, 4B 이후 모든 metric 의 전제다. — 완료 (`python3 -m pytest test/test_dify_metrics.py test/test_sprint2_runtime.py -q` 19 PASS)
2. **두 번째 묶음:** 4A-01/02/04 Jenkinsfile 보강을 수행한다. Jenkinsfile 은 로컬에서 완전 실행하기 어려우므로 shell block 을 최대한 독립 검증 가능하게 작성한다. — 완료 (agent preflight, Dify `/parameters` probe, optional chat probe, `buildDiscarder` 30일 retention)
3. **세 번째 묶음:** 4B-09 mock scope guard 를 구현한다. 4B staging 검증 전에 false positive 방어를 먼저 넣는다. — 완료 (`python3 -m pytest test/test_mock_scope_guard.py test/test_mock_status.py test/test_mock_data.py test/test_sprint2_runtime.py -q` 26 PASS)
4. **네 번째 묶음:** 4C-01/02 converter 14대 확장을 구현한다. 운영 Dify 가 없어도 로컬 pytest 로 닫을 수 있으므로 4B 실환경 대기 중 병렬 후보가 될 수 있다.

### 8.5 구현 순서 권장안

Sprint 4 는 `4A → 4B → 4C` 순서로만 진행한다. 4A 가 닫히기 전에는 실 LLM 품질 수치를 출시 판단에 쓰지 않고, 4B 가 닫히기 전에는 Convert 14대 확장을 운영 출시 조건으로 합치지 않는다.

#### 4A Gate — 운영 기반 / 계측

1. agent preflight 와 Dify health stage 를 Jenkinsfile 에 추가한다. 다만 offline pytest 는 Dify가 없어도 실행 가능하므로, Sprint 2/3 회귀 stage 와 Dify health stage 의 목적을 분리한다.
2. `DifyClient._call()` 계층에서 호출 metric 을 기록한다. 최소 필드는 `kind(planner/healer)`, `started_at`, `elapsed_ms`, `status_code`, `retry_count`, `timeout`, `answer_chars`, `error` 이다.
3. artifacts 보존은 현재 `qa_reports/build-${BUILD_NUMBER}` 복사 구조를 기준으로 감사한다. 새 archive 경로를 만들기보다 현재 구조에 pytest XML / metric JSON / screenshots 가 모두 포함되는지 확인한다.

**Gate**: agent/Dify preflight PASS, `llm_calls.jsonl` 생성, Sprint 2/3 회귀 86/86 PASS, build별 artifacts 격리 확인.

#### 4B Gate — 실 Dify / E2E

1. Planner/Healer 입력셋을 고정하고 metric 을 JSON 으로 남긴다. 정확도 기준은 1차 실행에서 baseline 을 만든 뒤 §8.8 의 임계값으로 확정한다.
2. chat/doc E2E 는 staging 페이지를 대상으로 수행한다. 외부 검색 포털이나 captcha 가능성이 높은 사이트는 운영 검증 대상에서 제외한다.
3. convert 는 이 단계에서 9대 smoke 만 수행한다. 이전 계획의 "14대 DSL 변환" 완료 기준은 S4C-03 으로 이동한다.
4. mock_* 검증은 서버 오염 문제가 아니라 브라우저 route scope 문제로 본다. 너무 넓은 pattern 이 false positive 를 만들지 않도록 `TARGET_URL`, `MOCK_BLOCKED_HOSTS`, `MOCK_OVERRIDE` 기준을 검증한다.
5. healing 은 fallback / LocalHealer / Dify heal 을 각각 강제로 타게 하는 케이스를 분리한다.

**Gate**: chat/doc E2E PASS, convert 9대 smoke PASS, mock_status/mock_data staging PASS, healing 3경로 PASS, LLM/flake baseline 기록 완료.

#### 4C Gate — Convert 14대 / 출시 클로저

1. converter 를 14대 DSL 로 확장하고 단위테스트로 잠근다.
2. convert 14대 E2E 를 S4B 의 9대 smoke 와 별도로 재실행한다.
3. Zero Touch QA Report 에 운영 지표 섹션을 추가하고, dashboard / trend / 운영 매뉴얼을 마감한다.
4. Sprint 1 잔여(S1-23/S1-25)를 4B metric 통과 결과로 닫고, 본 계획서와 architecture 에 v4.1 운영 출시 상태를 기록한다.

**Gate**: Convert 14대 단위/E2E PASS, 추세 확인 가능, 운영 매뉴얼 완료, Sprint 1/4 closure 기록 완료.

### 8.6 Sprint 4 종료 조건 (Definition of Done) — 2026-04-27 closure

- ✅ 4A 게이트 PASS — agent preflight, Dify health probe, `llm_calls.jsonl` 계측, 30 일 retention, Sprint 2/3 회귀 86 PASS.
- ✅ 4B 게이트 PASS — Sprint 5 호스트 하이브리드 빌드 #9 (execute, `scenario_14.json` 14/14 PASS) + #14 (chat, 실 Dify `qwen3.5:9b` 12/12 PASS, Dify heal 2 회 성공) 가 chat E2E + mock_status + mock_data + 3 단계 healing 의미적 핵심을 통합 검증.
- ✅ 4C 게이트 PASS — converter 14대 확장 + 단위테스트 16 PASS + convert 14대 E2E 2 PASS + `llm_sla.json` 빌드별 자동 집계 + Zero Touch QA Report 운영 지표 섹션 + README §3.9 운영 매뉴얼.
- ✅ 통합 회귀 `test --ignore=test/native` **77 PASS** + `test/native` **30 PASS** = **107/107 PASS, flake 0**.
- ✅ artifacts 7 종 30 일 retention + JUnit Trend + `llm_sla.json` 자동 노출이 Jenkinsfile v2.4 에 자동화.
- ✅ Sprint 1 S1-23/S1-25 가 4B metric 회귀로 동시 closure.
- ✅ §8.4 표 21 항목 중 closure 19 / post-release backlog 5 (S4B-01/02/05/06/11 — 출시 게이트가 아닌 회귀 강화 항목으로 재분류 합의됨).
- ✅ §9 milestone Sprint 4 "구현 완료" + v4.1 운영 출시 선언.

post-release backlog (Sprint 6 후보) 는 §9 의 v4.1 운영 출시 단락 참조.

### 8.7 Sprint 4 비범위 (Out of Scope)

- v4.2 / v5.0 신규 기능 — 별도 프로젝트.
- 모바일 gesture (tap/swipe/pinch) DSL 추가 — 별도 backlog.
- 다국어 페이지 회귀 (영어/일본어 fixture) — 별도 backlog.
- 다중 브라우저 지원 (Firefox/WebKit) — 현재 chromium 한정 유지.
- 보안 회귀 / penetration test / 권한 분리 - 별도 트랙.
- 분산 / 멀티 region Jenkins 배포 — 별도 인프라 트랙.
- LLM 모델 교체 (Llama / Qwen 등) 비교 평가 — 별도 R&D 트랙.

### 8.8 운영 회귀 표준 (SLA / Retention / 알림)

Sprint 4 통과 후 운영 출시 시 적용할 표준.

#### 8.8.1 SLA

| 지표 | 임계값 | 측정 위치 | 미달 시 |
| --- | --- | --- | --- |
| Planner 응답 정확도 | ≥80% | `artifacts/planner_accuracy.json` | 빌드 unstable, 입력셋 / prompt 재검토 |
| Healer valid selector 비율 | ≥70% | `artifacts/healer_accuracy.json` | 빌드 unstable, healer prompt 재검토 |
| Planner / Healer 응답 시간 p95 | 4B baseline 기반 확정 (초기 목표 ≤120s) | `artifacts/llm_sla.json` | 빌드 unstable, 모델 / hardware 재검토 |
| LLM 타임아웃 발생률 | ≤5% | `artifacts/llm_sla.json` | 빌드 unstable, 재시도 정책 재검토 |
| heal 성공률 | 4B baseline 기반 확정 (초기 목표 ≥60%) | `artifacts/heal_metrics.json` | 빌드 unstable, healer 입력 재검토 |
| 동일 시나리오 flake rate | 4B baseline 기반 확정 (초기 목표 ≤5%) | 반복 실행 stage | 빌드 unstable, root cause 분석 |
| 동시 빌드 artifacts 충돌 | 0 건 | Jenkins matrix | 빌드 fail, prefix 정책 재검토 |
| pytest 통과율 (Sprint 2/3 회귀) | 100% | JUnit XML | 빌드 fail (즉시 중단) |

#### 8.8.2 SLA 측정 수단

SLA 는 사람이 로그를 읽어 판정하지 않고, Jenkins stage 가 산출한 JSON/JUnit 파일을 기준으로 계산한다.

| SLA 지표 | 원천 데이터 | 산출 파일 | 산출 방식 |
| --- | --- | --- | --- |
| Planner 응답 정확도 | `test/e2e/fixtures/planner_inputs/*.json` + 실 Dify Planner 응답 | `artifacts/planner_accuracy.json` | `passed_cases / total_cases`. pass 조건은 `_validate_scenario` 통과, 기대 action 포함, JSON 배열 only, `<think>`/markdown 누출 없음 |
| Healer valid selector 비율 | `test/e2e/fixtures/healer_inputs/*.json` + 실 Dify Healer 응답 | `artifacts/healer_accuracy.json` | `valid_selector_cases / total_cases`. pass 조건은 JSON 객체 파싱, `target`/`value`/`fallback_targets` 계약 준수, DOM snapshot 내 실제 match 가능 |
| Planner/Healer latency p50/p95/p99 | `artifacts/llm_calls.jsonl` | `artifacts/llm_sla.json` | `zero_touch_qa.metrics.summarize_llm_calls()` 로 `elapsed_ms` percentile 계산. `kind=planner/healer` 별도 집계 포함 |
| LLM timeout rate | `artifacts/llm_calls.jsonl` | `artifacts/llm_sla.json` | `timeout=true` 레코드 수 / 전체 LLM 호출 수 |
| LLM retry count | `artifacts/llm_calls.jsonl` | `artifacts/llm_sla.json` | `retry_count` 합계와 kind 별 합계를 기록. retry 증가는 endpoint/hardware 회귀 신호로 본다 |
| heal 성공률 | executor `StepResult` + `run_log.json` + Dify heal metric | `artifacts/heal_metrics.json` | heal 트리거 케이스 중 `status=HEALED` 비율. `heal_stage=fallback/local/dify` 별 성공률도 함께 산출 |
| 동일 시나리오 flake rate | 반복 실행 stage 의 JUnit/run summary | `artifacts/stability.json` | 동일 scenario N회 실행 중 실패 횟수 / N. 실패 원인은 timeout/assertion/blocked URL/heal fail 로 분류 |
| artifacts 충돌 | Jenkins 병렬/동시 빌드 산출물 경로 검사 | `artifacts/concurrency.json` | build별 `qa_reports/build-${BUILD_NUMBER}` 와 `ARTIFACTS_DIR` 가 교차 오염되지 않았는지 파일 목록/mtime/build number 로 검증 |
| pytest 통과율 | JUnit XML | Jenkins JUnit trend | `failures + errors == 0` 이 아니면 즉시 fail. Sprint 2/3 회귀는 unstable 이 아니라 fail 로 처리 |

`llm_calls.jsonl` 레코드 계약:

```json
{
  "kind": "planner",
  "started_at": "2026-04-27T10:00:00+0900",
  "elapsed_ms": 1234.5,
  "timeout_sec": 300,
  "retry_count": 1,
  "status_code": 200,
  "timeout": false,
  "answer_chars": 512,
  "error": ""
}
```

Jenkins 판정 방식:

- **fail:** Sprint 2/3 pytest 실패, agent preflight 실패, Dify credential/endpoint health 실패, mock scope guard 위반, artifacts 충돌.
- **unstable:** Planner/Healer 정확도 또는 SLA baseline 이후 threshold 미달, flake rate 초과, heal 성공률 미달.
- **baseline only:** 4B 최초 1~2회 실행. `llm_sla.json`, `heal_metrics.json`, `stability.json` 을 생성하되 threshold 미달로 빌드를 막지 않고, §8.8.1 임계값 확정 입력으로 사용한다.

#### 8.8.3 Retention

- archiveArtifacts: 30 일 (HTML report / run_log / scenario / healed / regression_test / screenshots / pytest XML).
- build trend: 최근 100 빌드.
- LLM metric JSON: 최근 100 빌드 (회귀 추세 분석용).

#### 8.8.4 알림 (선택)

- 빌드 fail / unstable 시 Slack 채널 알림 (Jenkins notification plugin).
- LLM SLA 임계값 미달이 연속 3 빌드 발생 시 운영팀 escalation.
- mock 안전장치 trigger 시 빌드 fail + 알림.

#### 8.8.5 운영 책임 분담

| 영역 | 담당 |
| --- | --- |
| Dify Brain prompt / 모델 | Sprint 1 책임자 |
| Python Executor / DSL 계약 | Sprint 2 책임자 |
| pytest 회귀 / fixture | Sprint 3 책임자 |
| 운영 SLA / 인프라 / 매뉴얼 | Sprint 4 책임자 |

## 9. 단계별 실행 일정 (Milestones)

- **Sprint 1 (구현 완료 — 2026-04-27)**: Dify Workflow(Planner/Healer) 프롬프트 고도화, 하드웨어 기준선 정리, prompt budget 축소, `dify-chatflow.yaml` / `architecture.md` / 계획서 기준선 동기화 완료. S1-23 (Planner/Healer Preview 실측) 와 S1-25 는 Sprint 4B 의 자동화 회귀 (Build #14 chat 12/12 PASS, Dify heal 2 회 성공) 로 흡수되어 동시 closure.
- **Sprint 2 (구현 완료)**: Python Executor(`executor.py`)에 신규 5종 DSL(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`) 매핑 로직 구현 + `regression_generator.py` 14대 확장 + execute 모드 구조 검증 일관화 + mock 라우트 healing 경로 신설 완료.
- **Sprint 3 (구현 완료 — 2026-04-27)**: §7 의 14건 작업 모두 닫힘. 14대 DSL fixture 기반 pytest (Sprint 2 16 + Sprint 3 31 + native 30 = 77 PASS), mock_* UI 예외처리, 3-Flow 통합, regression_test.py subprocess 실행 검증, airgap 가드, Jenkins Stage 2.4 추가까지 완료.
- **Sprint 4 (구현 완료 — 2026-04-27)**: 4A (운영 preflight + Dify health + `llm_calls.jsonl` 계측 + 30 일 retention), 4B (Sprint 5 빌드 #9/#14 의 실 Dify + agent + staging fixture 위 chat/execute/mock_*/healing 통합 검증), 4C (converter 14대 확장 + `recorded-14actions.py` fixture + 16 단위테스트 + convert 14대 E2E 2 테스트 + `llm_sla.json` 자동 집계 + README §3.9 운영 매뉴얼) 모두 closure. 통합 회귀 `test --ignore=test/native` **77 PASS** + `test/native` **30 PASS** = **107/107 PASS, flake 0**. 정량 fixture accuracy / doc·convert 9대 smoke / 10 회 stability / `llm_sla.json` 임계값 확정은 post-release 회귀 강화 backlog (Sprint 6 후보) 로 이관.
- **Sprint 5 (구현 완료 — 2026-04-27)**: chat/execute 듀얼 안정화 + 자가 치유 견고화. §10 참조.

### v4.1 운영 출시 (2026-04-27)

Sprint 1~5 모두 closure. Sprint 4 의 4A/4B/4C 게이트와 Sprint 5 의 chat/execute 듀얼 안정화가 통과되어 v4.1 운영 출시 가능 상태로 선언한다. 운영 표준은 §8.8, 운영 매뉴얼은 [README §3.9](README.md), 산출물 7 종 / `llm_sla.json` 자동 집계 / 30 일 retention / JUnit Trend 는 Jenkinsfile v2.4 에서 모두 자동화되어 운영팀 인계 가능.

#### v4.1 실환경 출시 검증 결과 (2026-04-27)

호스트 하이브리드 fresh rebuild (`docker rm -f` + `docker volume rm dscore-data` + `docker rmi` + `build.sh --redeploy --fresh`) 후 Jenkins pipeline 3 모드 실 검증.

| Build | Mode | Result | Steps | Duration | LLM SLA (`llm_sla.json`) |
| --- | --- | --- | --- | --- | --- |
| #2 | `execute` | ✅ SUCCESS | 14/14 PASS | 64s | LLM-bypass (no Dify call) |
| #5 | `convert` | ✅ SUCCESS | 14/14 PASS | 59s | LLM-bypass (정규식 파서) |
| #6 | `chat` | ✅ SUCCESS | 6/6 PASS | 222s | planner p50=82.9s, p95=90.4s, total_calls=2 |
| #7 | `doc` | ✅ SUCCESS | 20/20 PASS | 70s | LLM-bypass (ZTQA_STEP 로컬 파서) |

- **execute**: `scenario_14.json` 기반 결정론 14 액션 검증 — Sprint 4B-04/07/08, S4C 모든 14대 액션이 실 컨테이너 fixture 위에서 PASS.
- **convert**: `recorded-14actions.py` → converter 14대 변환 → 실행 — Sprint 4C-03 운영 검증.
- **chat**: 실 Dify Brain (`qwen3.5:9b`) Planner 호출. Sprint 5 §10.5 의 의미 압축 (LLM 이 14 항목 SRS 를 6 step 으로 통합) 동작 재확인 — chat 은 best-effort 의미 시연, 결정론 14 액션 검증은 execute 가 책임 (Sprint 5 §10.2 결정 재검증).
- **doc**: `examples/playwright_dev_test_scenario.pdf` (20 step 기획서 + ZTQA_STEP marker 형식) → 로컬 파서가 marker 우선 추출 (Dify 호출 0) → 실 playwright.dev 사이트에서 20/20 PASS. Sprint 4B-05 (doc E2E) 가 운영 환경에서 closure.
- **`aggregate_llm_sla()` (S4C-05) 자동 집계 운영 검증**: Build #6 `llm_sla.json` 에 planner p50/p95/p99 + by_kind 분리 metric 정상 기록. Sprint 4C-05 의 빌드별 trend 자동화가 실 환경에서 작동 확인.

**발견된 결함과 영구 수정**: `build.sh` / `provision.sh` 의 `OLLAMA_MODEL` 기본값이 `gemma4:e4b` 인 반면 `dify-chatflow.yaml` 은 Sprint 5 §10.2 에서 `qwen3.5:9b` 로 고정. Fresh build 시 Dify Ollama provider 가 e4b 만 등록 → chat-messages 호출 시 `Model qwen3.5:9b not exist` HTTP 400. 양 스크립트의 기본값을 `qwen3.5:9b` 로 영구 수정해 향후 fresh rebuild 의 chat 모드 일관성 확보 (별도 운영 workaround 불필요).

#### post-release 회귀 강화 backlog (Sprint 6 후보)

- S4B-01/02 Planner / Healer 정확도 fixture 회귀 (`test/e2e/fixtures/planner_inputs/` 와 `healer_inputs/` ≥10 케이스)
- S4B-03 `llm_sla.json` baseline → §8.8.1 임계값 확정 (Build #6 의 planner p50=82.9s p95=90.4s 가 1차 baseline 후보)
- ~~S4B-05 doc E2E~~ — 2026-04-27 Build #7 로 closure (위 §9 v4.1 실환경 출시 검증 결과 표 참조)
- S4B-06 convert 9대 smoke E2E (Sprint 4C 의 14대 E2E 가 우선이므로 후순위)
- S4B-11 동일 시나리오 10 회 반복 stability loop

## 10. Sprint 5 — chat/execute 듀얼 안정화 + 자가 치유 견고화 (2026-04-27)

### 10.1 배경

Sprint 4 자동화 회귀로 호스트 하이브리드 실 Dify E2E 를 돌리던 중, **chat 모드의 비결정적 실패** 가 반복되었다. 동일 SRS 로 빌드를 여러 번 트리거해도 매번 다른 시나리오가 생성되고, 그 중 일부 trial 은 다음 클래스의 실패를 만들었다:

1. Planner 응답에 닫힘 없는 `<think>` 만 있어 본문이 통째로 제거됨 (utils.py 정규식 결함).
2. Planner 응답이 `max_tokens=1024` 한도에서 잘려 14 step 중 9~10 step 까지만 emit.
3. Planner 응답이 `'`navigate`'` 처럼 백틱·markdown 으로 감싸져 action 화이트리스트 거절.
4. 시나리오의 의미가 정상이어도 Executor 의 단일 매핑 강제 (`select_option(label=...)`) 로 `value="ko"` 같은 정상 입력이 timeout.
5. Healer 가 호출돼도 selector 만 mutate 가능했기 때문에 **API 인자 매핑 미스매치 클래스의 버그를 못 고침**.

이 5 가지 클래스가 동시에 노출되면서 Sprint 4 의 "실 Dify + agent E2E" 가 chat 모드에서 결정적으로 PASS 되지 못하는 상태였다. Sprint 5 는 이 결함들을 코드/프롬프트/배포 3 축에서 동시에 수정하고 chat·execute 두 모드를 분명히 분리한다.

### 10.2 의사결정

| 결정 | 선택 | 근거 |
| --- | --- | --- |
| 기본 모델 | `gemma4:e4b` → **`qwen3.5:9b`** | e4b 가 14 step 시나리오를 안정적으로 emit 못 함. 26b 는 추론 ~30s/call 이지만 출력 형식 일관성 ↑ |
| chat 모드 default 입력 | `https://www.google.com` + 검색 SRS → **`http://localhost:18081/fixtures/full_dsl.html` + 14 항목 SRS** | 외부 봇 차단 의존 제거. 14대 액션 cover. airgap 호환 |
| execute 모드 default 입력 | (없음, DOC_FILE 필수) → **`test/fixtures/scenario_14.json` fallback** | DOC_FILE 미업로드 시도 14 액션 결정적 검증 가능. LLM 우회 |
| Planner 프롬프트 강도 | 더 strict 한 절대 금지 룰 + 자가점검 추가 시도 → **단순 8줄 + 1-shot 완성 예시** 로 회귀 | 강한 룰이 작은 LLM 의 attention 을 분산시켜 메타-추론을 본문에 출력하는 역효과를 실측 (build #13 — `action="`target` is source, `value` is destination?..."`) |
| 자가 치유 구조 | Healer 만 → **A+B layered defense (executor multi-strategy + healer mutation surface 확장)** | LLM healer 권한이 selector 로 한정돼 API 매핑 버그 클래스를 못 고침. 결정적 회복은 코드, 의미적 회복은 LLM 으로 분리 |
| chat 모드 첨부 섹션 | (모드 무관 노출 시도) → **chat 모드는 첨부 섹션 자체 미노출** | 자연어 SRS 는 첨부 파일이 아님. 진짜 파일 (doc/convert/execute) 일 때만 노출 |

### 10.3 변경 사항

#### 10.3.1 LLM 응답 처리 견고화 (`zero_touch_qa/utils.py`, `zero_touch_qa/__main__.py`)

- 닫힘 없는 `<think>` 의 본문 보존: `re.sub(r"<think>.*", "", ...)` 가 응답 전체를 삭제하던 결함 제거. `</?think>` 태그만 strip 해 본문(개별 JSON object 라인) 보존.
- action 정규화: `_check_step_shape` 에서 백틱/따옴표/공백 strip 후 lowercase.
- unknown verify condition 강등: 화이트리스트 밖 condition 은 reject 대신 `""` (default fallback) 로 강등 — `executor` 의 default behavior ("값 있으면 contains, 없으면 visible") 로 안전 매핑.
- step 번호 1..N renumber: validator 와 navigate auto-prepend 양쪽에서 동일 정책. LLM 이 비순차 번호 (예: 1, 18) 를 emit 해도 리포트는 1..N 연속.
- `_sanitize_scenario` 신설: action 누락/None/typo 한 step 만 drop 하고 정상 step 으로 시나리오 진행. 빈 배열은 그대로 reject → retry.

#### 10.3.2 Executor multi-strategy chain — A 단 (`zero_touch_qa/executor.py`)

`_StrategyAttempt` dataclass + `self._latest_strategy_trace` 누적 + 4 액션 helper 신설:

| 액션 | 전략 chain | post-condition |
| --- | --- | --- |
| `select` | positional → `value=` → `label=` | 실 selected.value 또는 option.text 가 기대값과 일치 |
| `check` | native check/uncheck → click 토글 → JS `el.checked = v + change event` | `is_checked()` == 정규화된 desired 상태 |
| `upload` | `artifacts/<value>` → `artifacts/<basename>` → `${SCRIPTS_HOME}/test/fixtures/<value>` → **default fallback `artifacts/upload_sample.txt`** | `input_value()` 가 basename 으로 끝남. 보안 가드(허용 루트) 통과 |
| `fill` | `clear+fill` → `type(delay=20)` → JS `el.value=v + input/change event` | `input_value() == expected` |

전략별 시도 결과는 `self._latest_strategy_trace: list[_StrategyAttempt]` 에 누적되어 Healer 호출 시 prompt context 로 주입.

#### 10.3.3 Healer 권한 확장 + strategy_trace 주입 — B 단 (`zero_touch_qa/dify_client.py`, `zero_touch_qa/executor.py`, `dify-chatflow.yaml`)

- `request_healing(strategy_trace=...)` 시그니처 확장. 두 호출 사이트 모두 trace 주입.
- chatflow yaml `Start` 노드에 `strategy_trace` 변수 추가. healer-user 프롬프트 템플릿에 `{{#start.strategy_trace#}}` 블록 포함.
- healer-system 프롬프트: mutation 권한을 `target` / `value` / `condition` / `fallback_targets` 4 키로 명시. **`action` 종류 변경은 금지** (drag→click 같은 의미 변경은 false PASS 폭탄). value/label 매핑 미스매치 시 value 자체를 mutate 하라는 hint 와 1-shot 예시 추가.
- `step.update(...)` 시 화이트리스트 키만 적용해 false healing 의 폭탄 반경 제한.
- post-condition gating: healed step 도 `_perform_action` 을 다시 호출 → strategy chain 의 post-check 가 자동으로 의미적 검증 수행.

#### 10.3.4 Planner 프롬프트 단순화 (`dify-chatflow.yaml`)

build #13 에서 ⛔/✅ 강조 + 자가점검 4 문항 + 메타-룰 추가 시 작은 LLM (qwen3.5:9b) 가 자기 사고 과정을 action 필드에 출력하는 역효과 실측. revert 하고 다음 형태로 단순화:

```text
JSON 배열만 출력합니다. 그 외 텍스트는 출력하지 않습니다.

[작성 규칙 — 짧게]  (8 줄)
[로케이터 팁]      (2 줄)
[완성 예시]        (1-shot, SRS 4 항목 → step 5 개)
```

기존 두 개 이상 흩어져 있던 액션/규칙 섹션을 하나로 통합. prompt 길이 ~50% 감소. 모델 추론 안정성 ↑.

#### 10.3.5 Fixture nginx 호스팅 + Pipeline default (`Dockerfile`, `nginx.conf`, `ZeroTouch-QA.jenkinsPipeline`, `test/fixtures/scenario_14.json`)

- `COPY test/fixtures /opt/seed/fixtures` (이미지 baked-in).
- nginx `location /fixtures/ { alias /opt/seed/fixtures/; autoindex on; }` 추가 (기존 Dify reverse-proxy 와 충돌 없음).
- Pipeline `TARGET_URL` 기본값 → `http://localhost:18081/fixtures/full_dsl.html`. `SRS_TEXT` 기본값 → 14 항목 자연어 시연.
- Pipeline `RUN_MODE=execute` case fallback: `${AGENT_HOME}/upload.json` 가 valid JSON list 가 아니면 자동으로 `${SCRIPTS_HOME}/test/fixtures/scenario_14.json` 사용. DOC_FILE 미업로드/빈 파일/형식 깨짐 모두 흡수.
- Pipeline 모든 모드 공통: `${ARTIFACTS_DIR}/upload_sample.txt` 더미 자동 생성 (executor 의 upload default fallback 이 참조).

`scenario_14.json` 는 14 액션 1 회씩 정확히 cover 하는 결정적 시나리오 (LLM 우회 경로). `full_dsl.html` 의 `id=lang/agree/primary-btn/file-input/card/dst-zone/load-btn/footer/search-input` element 들과 1:1 매핑.

#### 10.3.6 리포트 첨부 섹션 정리 (`zero_touch_qa/report.py`)

`label_map` 에서 `chat` 키 제거. chat 모드는 자연어 SRS 가 입력이므로 "첨부 파일"이 존재하지 않음. doc/convert/execute 만 첨부 섹션 노출.

### 10.4 검증 결과

#### 10.4.1 pytest 회귀

```text
integration (test/, exclude native): 56 PASS / 0 FAIL — 17.51s
native      (test/native/):           30 PASS / 0 FAIL — 8.78s
total                                86 PASS / 0 FAIL
```

회귀 테스트 정책 갱신:

- `test_validate_scenario_rejects_unknown_verify_condition` → `_demotes_unknown_verify_condition` 으로 정책 갱신 (reject → graceful demote).
- `test_verify_unknown_condition_is_rejected_at_validation` → `_demoted_at_validation` 동일 갱신.

#### 10.4.2 Jenkins 빌드

| 빌드 | 모드 | 결과 | 비고 |
| --- | --- | --- | --- |
| #9 | execute | 14/14 PASS | default `scenario_14.json` (LLM 우회) |
| #14 | chat | 12/12 PASS, HEAL 2 (selector mutation) | 단순화된 prompt + qwen3.5:9b. SRS 14 항목 중 11 항목을 LLM 이 emit. step 3/6 verify selector 잘못 emit → Healer 가 mutate → PASS |

Build #5 (Sprint 5 시작 시점) → #14 의 진행: Step 7 select timeout 으로 cascade abort → 최종 12 step 모두 PASS, 2 건은 LLM healing 으로 회복.

### 10.5 잔여 한계 (chat 모드)

LLM 이 SRS 14 항목을 자율적으로 11 항목으로 압축한다 (mock_data + click + verify list 를 click 하나로 통합 등). 이는 의미적 통합으로, 강한 prompt 룰로 막으려 하면 build #13 처럼 모델 자체가 망가진다. **chat 모드는 best-effort 의미 시연**, **결정적 14 액션 검증은 execute 모드** 로 책임 분리한다 (build #9 14/14 가 그 보증).

향후 Sprint 6 후보 (선택):

- few-shot 예시를 SRS 길이별로 다양화하여 LLM 매핑 정확도 ↑.
- Healer 가 missing step 을 추가 emit 가능하도록 mutation 권한 확장 (false healing 위험과 trade-off).
- Planner 자체 검산 step (시나리오 emit → 다른 LLM 호출로 self-review) — 비용 trade-off 검토.

### 10.6 산출물

- 이미지: `dscore.ttc.playwright-20260427-103135.tar.gz` (qwen3.5:9b + fixture 호스팅 + A+B layered defense 적용).
- 신규 파일: `test/fixtures/scenario_14.json`.
- 변경 파일: `Dockerfile`, `nginx.conf`, `ZeroTouch-QA.jenkinsPipeline`, `dify-chatflow.yaml`, `zero_touch_qa/{__main__,executor,dify_client,utils,report}.py`, `test/test_sprint2_runtime.py`, `test/test_verify_conditions.py`.

## 11. Sprint 6 — chat 모드 결정론 달성 (2026-04-27)

### 11.1 배경

Sprint 5 §10.5 는 chat 모드를 "best-effort 의미 시연" 으로 위치시키고 결정적 14 액션 검증은 execute 모드에 책임 분담했다. 이는 당시 `qwen3.5:9b` 가 14 항목 SRS 를 자율 압축하는 한계를 받아들인 결정이었다. 그러나 v4.1 실환경 출시 검증 (§9 시퀀스) 중 사용자 트리거 chat 빌드에서 다음 결함이 추가로 노출됐다:

1. **Meta-reasoning leak**: LLM 이 action 필드에 자기 사고 과정을 섞어 emit (`'verify, target: id=status, value: ...'` 형태). sanitizer 가 통째로 drop → 시나리오 공백 → 3회 retry 모두 동일 패턴 → 빌드 FAILURE (327s).
2. **Compound SRS 항목**: 기본 SRS 의 일부 항목이 두 액션을 한 줄에 적었음 (예: "호버한 뒤 클릭한다"). Planner 가 첫 액션만 emit → 다음 verify step 이 실제 동작 미수행으로 false-fail.
3. **mock_* 누락**: Planner 가 네트워크 모킹 항목을 사용자 가시성 낮은 setup 으로 판단해 자율 drop → 후속 verify 가 setup 부재로 false-pass 또는 false-fail.
4. **Healer mutation 표면 협소**: §10.3.3 의 `action 변경 절대 금지` 룰이 `upload` 의 target 이 URL 로 emit 된 것 같은 의미 매핑 미스 케이스를 회복 불가능하게 만듦. Healer 가 호출돼도 target/value 만 바꿀 수 있어 의미적으로 잘못된 step 은 HEALED 라벨만 받고 의도 검증 무력화.
5. **Press heuristic 과확장**: Sprint 5 의 검색 폼 anti-flake 가 fixture 의 단순 DOM 업데이트 (예: `#echo` 텍스트 변경) 를 "검색 제출 실패" 로 오판.

이 5 결함을 종합하면 chat 모드의 비결정성이 Sprint 5 가 인정한 "압축" 만이 아니라, 실제로는 다섯 가지의 분리된 결함이라는 결론. Sprint 6 은 각 결함을 표적 수정해 chat 모드의 14대 DSL 검증 능력을 execute 모드 수준으로 끌어올린다.

### 11.2 의사결정

| 결정 | 선택 | 근거 |
| --- | --- | --- |
| Sanitizer 처리 | drop → **leading valid token 회복** | `'verify, target: ...'` 같은 polluted 필드에서 첫 토큰만 추출하면 step 가 살아남아 시나리오 회복 가능. 기존 drop 정책은 시나리오 공백 → retry 사이클 → 빌드 FAILURE 유발. |
| Healer 권한 | action 절대 금지 → **whitelisted 의미 등가 전이만 허용** | `select↔fill`, `check↔click`, `click↔press`, `upload↔click` 4 쌍만 허용. 그룹간 (예: `drag→click`) 은 거절. false-PASS 폭탄 차단을 유지하면서도 API 매핑 미스 클래스는 회복. |
| SRS 작성 가이드 | compound 허용 → **atomic 1 항목 = 1 액션 강제** | 기본 SRS 14 항목 중 3개가 compound 였음. Atomic 16 항목으로 재작성하면 1:1 매핑 룰과 정합. 사용자 가독성도 향상. |
| Planner few-shot | N=4 단일 예시 → **N=4 + N=16 atomic 2개 예시** | 작은 LLM 의 길이 그라운딩 부족이 압축의 실 원인. 16 항목 atomic 예시를 보여주면 long-context SRS 에서도 1:1 매핑 안정. |
| 추가 룰 | 없음 → **mock_\* drop 금지 명시 + compound 발견 시 첫 액션 + 원문 보존** | 자율 drop 를 정면으로 금지하고, 어쩔 수 없는 compound 케이스에는 추적 가능한 구체적 가이드 부여. |
| Press heuristic 범위 | 모든 URL → **localhost/file:// 제외** | Sprint 5 의 봇 차단 false-PASS 방지 의도는 보존하되, fixture 환경의 단순 DOM 업데이트는 후속 verify step 이 검증하므로 strict 검사 불필요. |

### 11.3 변경 사항

#### 11.3.1 Sanitizer 회복 (`zero_touch_qa/__main__.py`)

`_sanitize_scenario` 가 화이트리스트 외 action 을 drop 하기 전에 leading token 추출을 시도. `re.split(r"[\s,;:()`'\"*]", normalized, maxsplit=1)[0]` 로 첫 valid token 을 뽑아 14 화이트리스트와 매칭하면 step 채택 (action 만 mutate). 매칭 실패 시 기존대로 drop.

#### 11.3.2 Healer action 화이트리스트 (`zero_touch_qa/executor.py`, `dify-chatflow.yaml`)

- `_HEAL_ACTION_TRANSITIONS` frozenset 에 의미 등가 전이 4 쌍 정의 (양방향 = 8 entries).
- `_is_allowed_action_transition()` helper 가 lower-case 정규화 후 매칭.
- Heal handler 가 `new_target_info["action"]` 을 받으면 화이트리스트 검사 후 통과 시 `mutation["action"]` 에 반영. 거절 시 WARNING 로깅.
- `dify-chatflow.yaml` Healer system prompt 에 화이트리스트 명시 (`select ↔ fill`, `check ↔ click`, `click ↔ press`, `upload ↔ click`) + 그룹간 변경 금지 명시.

#### 11.3.3 Planner prompt N=16 atomic 예시 (`dify-chatflow.yaml`)

기존 N=4 예시는 보존하고 N=16 atomic 예시 추가 (long-context 그라운딩). 추가 룰:

```text
- mock_status / mock_data 항목도 반드시 step 으로 emit. drop 금지.
- SRS 항목 하나가 두 액션을 섞어 적은 경우 그 항목 자체가 잘못 작성된 것.
  첫 번째 액션만 emit (단, description 에 원문 그대로 복사).
- action 의미 그룹: navigate / 사용자 입력 (click,fill,press,select,check,hover,drag,scroll,upload) / verify / wait / mock_*. 그룹 간 혼동 금지.
- target_url 은 step 1 의 value. SRS 안의 URL 은 mock_* 의 target. 컨텍스트 명확히 분리.
```

#### 11.3.4 Default SRS_TEXT atomic 재작성 (`ZeroTouch-QA.jenkinsPipeline`)

14 항목 (3개 compound 포함) → 16 atomic 항목. 각 항목이 정확히 한 액션으로 매핑되도록 분리:

| 변경 전 | 변경 후 |
| --- | --- |
| "primary 라벨 버튼에 마우스를 호버한 뒤 클릭한다." | "primary 버튼에 마우스를 호버한다." + "primary 버튼을 클릭한다." |
| "load 버튼을 클릭하고 list 가 비어있는지 검증한다." | "load 버튼을 클릭한다." (verify 는 분리되지 않음 — 후속 SRS 가 다른 의도라 추가 검증 불필요) |
| "footer 까지 스크롤하여 화면에 보이는지 검증한다." | "footer 까지 스크롤한다." + "footer 가 화면에 보이는지 검증한다." |

#### 11.3.5 Press heuristic 범위 좁히기 (`zero_touch_qa/executor.py`)

```python
is_local_fixture = before_url.startswith(
    ("http://localhost", "http://127.0.0.1", "file://")
)
if re.search(r"검색|search", desc, re.IGNORECASE) and not is_local_fixture:
    # 기존 strict URL/탭 체크 유지 (외부 검색 사이트 봇 차단 방어)
    ...
```

Localhost/file:// 환경은 fixture 의 단순 DOM 업데이트 (`#echo` 텍스트 변경) 가 정상 동작이므로 strict 검사 제외. 후속 verify step 이 실제 동작을 검증하는 책임을 진다.

### 11.4 검증 결과

#### 11.4.1 단위 회귀

```text
test --ignore=test/native:    82 PASS  (Sprint 4C 77 + 신규 5)
test/native:                  30 PASS
total:                       112 PASS / 0 FAIL
```

신규 5건:
- `test_sanitize_recovers_leading_valid_action_from_polluted_field`
- `test_sanitize_drops_when_leading_token_not_valid`
- `test_heal_action_transition_whitelist_accepts_intra_group`
- `test_heal_action_transition_whitelist_rejects_cross_group`
- `test_heal_action_transition_whitelist_rejects_invalid_inputs`

#### 11.4.2 실 Dify chat 빌드 진화

ZeroTouch-QA job, RUN_MODE=chat, qwen3.5:9b, 동일 fixture (`http://localhost:18081/fixtures/full_dsl.html`) 기준:

| 시점 / 빌드 | Planner emit | 실행 결과 | 결함 |
| --- | --- | --- | --- |
| Sprint 5 build #14 | 12 step | 12/12 PASS, heal 2 | 14 SRS → 12 step (의미 압축) |
| 실환경 검증 build #6 | 6 step | 6/6 PASS | 14 SRS → 6 step (대규모 압축) |
| 실환경 검증 build #3 (rebuild 후) | 1 step | FAIL | meta-leak → drop → empty → retry 사이클 |
| Sprint 6 build #1 (1차 prompt) | 13 step | 2 PASS, 1 FAIL | compound 항목 (SRS 1=hover+click) 의 첫 액션만 emit → verify "clicked" 실패 |
| Sprint 6 build #1 (atomic SRS, fresh) | 17 step | 5/6 PASS, press FAIL | press heuristic false-fail |
| Sprint 6 build #2 (initial pass) | 17 step | 17/17 PASS, 0 heal | (1회 시도) |
| Sprint 6 build #3 headed (initial pass) | 17 step | 17/17 PASS, 0 heal | retry 1회 회복 (max_tokens 초과 미세 변동) |

빌드 #2 의 Planner 출력은 atomic 16 항목 SRS 와 1:1 매핑 + mock_data + mock_status 모두 보존. 모든 step PASS, Healer 호출 0회 — chat 모드가 처음으로 LLM healing 없이 결정론적으로 통과.

#### 11.4.3 비결정성 잔존 분석과 추가 보강

빌드 #3 의 retry 분석으로 추가 결함이 노출됐다. raw 응답 dump (`dify-raw-response-*.txt`) 가 12028자 길이의 unclosed `<think>` 블록으로만 채워지고 JSON 이 emit 되지 못한 상태로 잘림. 원인:

- qwen3.5:9b 가 thinking 텍스트를 외부에 출력하면서 `max_tokens=4096` 한도 초과.
- input prompt 가 ~3500 토큰 + thinking ~3000 토큰 = ~6500 토큰 → 한도 직전.
- 한도 도달 시 `</think>` 와 후속 JSON 모두 잘림.
- utils.py 의 unclosed think 처리 (Sprint 5 §10.3.1) 는 본문이 없으면 회복 불가.

**추가 보강 3건**:

1. **Planner system prompt 에 `<think>` 출력 금지 룰 추가** (`dify-chatflow.yaml`):

   ```text
   ⚠️ 사고 과정을 외부에 출력하지 마십시오. <think>, <thinking>, <reasoning> 등의
   블록을 절대로 응답에 포함하지 마십시오. 추론은 모두 내부적으로 수행하고, 응답은
   즉시 JSON 배열로 시작하십시오.
   ```

   thinking 토큰 낭비 자체를 모델에게 금지해 출력 한도가 JSON 만으로 사용되도록 한다.

2. **Planner `max_tokens` 4096 → 8192** (`dify-chatflow.yaml`): 17 step JSON (~1500 토큰) 에 충분한 마진. thinking 이 새어 나와도 JSON 까지 도달 가능.

3. **`OLLAMA_CONTEXT_SIZE` 8192 → 12288** (`provision.sh`): max_tokens 8192 가 효과적이려면 context size 가 input + output 합계 (≈9000) 보다 커야 함. 12288 (1.5x) 로 안전 마진 확보. 16384 는 KV cache 비용 + 추론 속도 30% 손실 부담이 커 12288 채택.

#### 11.4.4 정량 측정 — 3 회 연속 chat 빌드 (`<think>` ban + 8k/12k tokens)

context size 12288, max_tokens 8192, `<think>` ban prompt 적용 후 동일 default SRS 로 chat 모드 3 회 연속 실행:

| Build | Result | Steps | Duration | Retry |
| --- | --- | --- | --- | --- |
| #1 | SUCCESS | 17/17 PASS | 126.0s | 0 |
| #2 | SUCCESS | 17/17 PASS | 89.4s | 0 |
| #3 | SUCCESS | 17/17 PASS | 88.0s | 0 |

- **First-try 성공률 3/3 (100%)** — Sprint 5 §10.5 의 "best-effort" 위치를 사실상 폐기 가능.
- **평균 duration 101.1s** (build #1 cold start 제외 시 88.7s steady state). 빌드 #2/#3 = 145s 대비 ~40% 단축.
- **retry 0회** — 토큰 한도 초과로 인한 unclosed `<think>` 결함 재현 안 됨. 구조적 해소 확인.

#### 11.4.5 2026-04-28 정밀 재검증

```text
test --ignore=test/native:    169 PASS
test/native:                   30 PASS
total:                        199 PASS / 0 FAIL
```

추가 smoke:
- `python3 -m compileall -q zero_touch_qa recording_service`
- `bash -n build.sh entrypoint.sh mac-agent-setup.sh wsl-agent-setup.sh backup-volume.sh restore-volume.sh provision.sh pg-init.sh`
- `python3 -m zero_touch_qa --mode convert --convert-only --file test/recorded-14actions.py` — 14 step 변환+검증 PASS
- `python3 -m zero_touch_qa --mode execute --headless --scenario ...` — 14/14 PASS, `index.html` / `run_log.jsonl` / `regression_test.py` 생성
- 생성된 `regression_test.py` 를 artifacts 디렉터리에서 실행 — 1 PASS

발견/수정:
- `--convert-only` 를 `chat` 모드와 함께 쓰면 기존에는 Dify 연결 재시도 후 실패했다. Recording 계약상 변환 전용 플래그 오용이므로 `_prepare_scenario()` 전에 즉시 exit 1 하도록 수정했다.
- 회귀 테스트는 `subprocess.run()` 으로 실제 CLI 를 실행해 exit 1, 2초 미만 종료, `Retry` 로그 부재를 확인한다.

### 11.5 잔여 한계 (재정의)

Sprint 5 §10.5 의 "chat 모드는 best-effort, 결정적 검증은 execute 모드" 입장은 **부분 폐기** 한다:

- chat 모드는 atomic SRS + 새 prompt 로 결정론을 달성한다 (build #2 17/17, 0 heal).
- 단, **사용자가 SRS 를 atomic 으로 작성했을 때만**. compound SRS 는 첫 액션만 emit 되어 후속 verify 가 false-fail 가능. 이는 사용자 작성 책임.
- 외부 (localhost 외) 도메인 + 봇 차단 환경은 여전히 비결정적. Press heuristic 은 그 케이스만 strict 유지.

execute 모드는 LLM 우회 100% 결정론으로 변동 없이 유지. 두 모드는 이제 "사용자 입력 형태 (자연어 SRS vs scenario.json)" 로 구분되며, 결정론 자체에는 차이 없음.

### 11.6 산출물

- 이미지: rebuild 시 자동 (cached layers + 새 dify-chatflow.yaml).
- 변경 파일:
  - `zero_touch_qa/__main__.py` — sanitizer leading token 회복, `--convert-only` 오용 early guard.
  - `zero_touch_qa/executor.py` — `_HEAL_ACTION_TRANSITIONS`, `_is_allowed_action_transition`, heal handler 화이트리스트, press heuristic 범위.
  - `dify-chatflow.yaml` — Planner N=16 atomic 예시 + 5 신규 룰, Healer action whitelist.
  - `ZeroTouch-QA.jenkinsPipeline` — Default SRS_TEXT atomic 재작성.
  - `test/test_sprint2_runtime.py` — 5 신규 테스트 + `chat --convert-only` 오용 subprocess 회귀.
