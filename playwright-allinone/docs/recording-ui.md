# playwright-allinone_RECORDING_UI: Recording UI 사용 가이드

이 문서는 호스트에서 도는 **Recording UI** (포트 18092) 의 모든 기능을 설명한다.

처음 시스템을 띄우는 절차는 [quickstart.md](quickstart.md) 에 있다. 본 문서는 Recording UI 가 떠 있다고 전제한다.

## 목차

1. [Recording UI 가 하는 일](#1-recording-ui-가-하는-일)
2. [한눈에 보는 작업 흐름](#2-한눈에-보는-작업-흐름)
3. [화면 구성 — 6개 카드](#3-화면-구성--6개-카드)
4. [🔐 Login Profile — 로그인 세션 시드](#4--login-profile--로그인-세션-시드)
5. [🔍 Discover URLs — 사이트 URL 수집](#5--discover-urls--사이트-url-수집)
6. [🎬 Recording — 브라우저 조작 녹화](#6--recording--브라우저-조작-녹화)
7. [▶️ Play & more — 시나리오 재생](#7-️-play--more--시나리오-재생)
8. [📊 결과 확인 및 스텝 추가](#8--결과-확인-및-스텝-추가)
9. [⬇ 다운로드 — 다른 PC 의 Replay UI 로 옮기기](#9--다운로드--다른-pc-의-replay-ui-로-옮기기)
10. [최근 세션 — 세션 목록 / 일괄 삭제](#10-최근-세션--세션-목록--일괄-삭제)
11. [자동 정리 기능 (codegen 노이즈 제거)](#11-자동-정리-기능-codegen-노이즈-제거)
12. [파일 구조 — `recordings/<id>/`](#12-파일-구조--recordingsid)
13. [자주 발생하는 문제와 원인](#13-자주-발생하는-문제와-원인)
14. [API 요약](#14-api-요약)

---

## 1. Recording UI 가 하는 일

브라우저 조작을 한 번 녹화하면 Playwright DSL 시나리오 (`scenario.json`) 가 만들어진다. 이걸 다음 두 방식으로 다시 실행 (재생) 할 수 있다.

| 재생 방식 | 누가 누른 적 없는 셀렉터가 깨졌을 때 |
| --- | --- |
| **Play (codegen)** | `fallback_targets` + 로컬 DOM 유사도 매칭으로 자가 치유 |
| **Play with LLM** | 위에 더해 Dify LLM 이 selector 를 다시 짜 줌 |

녹화·재생 과정 전체가 호스트에서 일어난다. 컨테이너 안의 Jenkins/Dify 와는 HTTP 호출로만 연결된다.

## 2. 한눈에 보는 작업 흐름

```text
┌─ 로그인 세션 준비 (선택) ─────────────────────────────────┐
│   🔐 Login Profile → 시드 → 검증                         │
└──────────────────────────────────────────────────────────┘
                ↓
┌─ URL 후보 수집 (선택) ───────────────────────────────────┐
│   🔍 Discover URLs → tour 스크립트 생성                   │
└──────────────────────────────────────────────────────────┘
                ↓
┌─ 시나리오 만들기 ───────────────────────────────────────┐
│   🎬 Recording 시작 → 브라우저 조작 → Stop & Convert     │
│   또는 기존 .py 업로드                                    │
└──────────────────────────────────────────────────────────┘
                ↓
┌─ 재생·검증 ─────────────────────────────────────────────┐
│   ▶️ Play (codegen) 또는 Play with LLM                   │
│   → 📊 run_log / 스크린샷 / 리포트 확인                  │
└──────────────────────────────────────────────────────────┘
```

## 3. 화면 구성 — 6개 카드

브라우저로 `http://localhost:18092` 에 접속하면 다음 6개 카드가 세로로 배치된다. 모두 **details** (펼침/접힘) 형식이라 필요한 카드만 펼쳐두면 된다.

| 카드 | 용도 |
| --- | --- |
| 🔐 Login Profile Registration | 테스트 대상 서비스의 로그인 세션 시드/검증/삭제 |
| 🔍 Discover URLs | 사이트 URL 후보 수집 + tour 시나리오 자동 생성 |
| 🎬 Recording | 브라우저 조작 녹화 또는 기존 `.py` 업로드 |
| ▶️ Play & more | scenario.json 재생 (codegen / LLM) |
| 📊 결과 확인 및 스텝 추가 | 결과 / scenario / run_log / regression diff / assertion 추가 |
| 최근 세션 | 세션 목록 + 검색 + 일괄 삭제 |

상단의 `health-badge` 가 녹색이면 서비스 정상.

---

## 4. 🔐 Login Profile — 로그인 세션 시드

### 언제 쓰는가

테스트 대상이 **로그인 후에만 쓸 수 있는 화면** 이면 (포털 마이페이지, 댓글 작성 등), 매번 녹화/재생할 때마다 로그인을 다시 하지 않도록 **storageState** 를 한 번 시드해 둔다.

본 시스템의 auth-profile 은 "네이버 로그인 그 자체" 를 테스트하려는 게 아니라, **네이버로 로그인되는 외부 서비스를 테스트** 하기 위한 발판이다.

### 흐름 — 시드 → 검증 → 사용

1. **시드 (Seed)** — `[+ 새 프로파일]` 클릭 → 모달에서 다음을 입력.
   - **이름** — 프로파일 식별자 (예 `dpg`, `fuck`)
   - **seed_url** — 테스트 *서비스* 진입 URL (네이버 로그인 URL 이 아님)
   - **verify_service_url / verify_service_text** — 검증 시 도달했는지 확인할 URL/텍스트
   - 선택 — `naver_probe` (네이버 OAuth 케이스에서 자동 진입 감지)
2. 새 Chromium 창이 뜬다. **사용자가 직접 로그인** (네이버, 사내 SSO, 무엇이든).
3. 로그인 완료 후 페이지가 verify URL 까지 도달하면 자동으로 storageState 가 저장됨.
4. **검증 (Verify)** — 드롭다운에서 프로파일 선택 → `[검증]`. 같은 storageState 로 verify URL/text 도달 가능 여부를 확인.
5. **사용** — Recording 카드의 `로그인 프로파일` 드롭다운에서 선택하면 그 storageState 가 자동 적용된다.

### 만료 / 재시드

세션이 만료되면 verify 실패. 같은 이름으로 재시드 (덮어쓰기) 하면 된다. 모달이 마지막 시드의 값으로 prefill 된다.

### 저장 위치

```text
~/ttc-allinone-data/auth-profiles/
├── _index.json              ← 카탈로그
├── _index.lock
└── <프로파일이름>.storage.json   ← Playwright storageState
```

### 보안 주의

`storage.json` 은 **세션 토큰을 그대로 담는다**. git 에 커밋하지 말 것 (`.gitignore` 처리됨). 공유 머신에서는 사용 후 삭제 권장.

---

## 5. 🔍 Discover URLs — 사이트 URL 수집

### 언제 쓰는가

"이 사이트의 모든 페이지를 한 번 둘러보는 시나리오 (tour)" 를 자동으로 만들고 싶을 때.

### 입력

- **start_url** — 크롤링 시작점
- **max_urls / max_depth** — 깊이/개수 제한
- **use_sitemap** — sitemap.xml 우선 사용 (기본 ON)
- **로그인 프로파일** — 로그인 필요한 사이트면 선택

### 출력

- **CSV / JSON** — 수집된 URL 목록 다운로드
- **트리 (crawl / path)** — parent_url 기준 또는 URL path 기준 토폴로지
- **Tour 스크립트** — 수집된 URL 을 자동으로 순회하는 Playwright `.py` 생성 → Recording 으로 업로드해 즉시 재생 가능

Tour 스크립트는 각 URL 을 `try/except AssertionError` 로 감싸 한 URL 이 실패해도 다음으로 넘어간다. 시나리오 abort 회피.

### 트리 시각화

`[트리 보기]` 펼치면 부모-자식 관계를 들여쓰기 트리로 표시. HTML 다운로드도 가능.

---

## 6. 🎬 Recording — 브라우저 조작 녹화

### 시작

1. **target_url** 입력 (필수)
2. **로그인 프로파일** 선택 (필요시)
3. **Start Recording** — 새 Chromium 창이 뜬다.
4. 그 창에서 사용자가 자유롭게 조작.
5. **Stop & Convert** — 자동으로 다음을 수행.
   - codegen 출력 (`original.py`) → AST 변환 → `scenario.json`
   - 자동 정리 (dedupe / IME 노이즈 / popup 메타) — [§10](#10-자동-정리-기능-codegen-노이즈-제거)
   - 검증 통과 시 `state=done`, 실패 시 `state=error` + `error_final.png`

### 녹화 팁

| 상황 | 대처 |
| --- | --- |
| 호버 메뉴 (드롭다운/GNB) | codegen 은 hover 를 기록하지 않는다. 상위 메뉴는 마우스만 올리고 클릭 금지. leaf 항목만 클릭 — 재생 시 visibility healer 가 cascade hover 자동 수행 |
| 같은 카드 빠른 더블클릭 | 자동 dedupe 로 1회로 압축됨 |
| 한글 IME 키워드 입력 | CapsLock / 빈 fill / Unidentified 키 자동 제거 |
| 답변 생성 중 로딩 텍스트 클릭 | "마무리 내용을 정리하고 있습니다" 같은 transient 텍스트 클릭은 재생 시 실패할 수 있음. 답변 완료까지 대기 후 클릭 |

### 기존 `.py` 업로드

이미 있는 Playwright 스크립트가 있으면 **📁 Play Script from File** 로 업로드. 새 세션이 등록되고 변환·재생을 즉시 시작한다. tour 스크립트, codegen 직출력, 손작성 모두 가능.

---

## 7. ▶️ Play & more — 시나리오 재생

선택된 세션의 `scenario.json` 을 두 가지 방식으로 재생할 수 있다.

### Play (codegen)

```text
zero_touch_qa --mode execute --scenario scenario.json
```

- `fallback_targets` 순회 → 로컬 DOM 유사도 매칭 (LocalHealer) 까지 자가 치유.
- LLM 호출 없음 → 가장 빠름 + 비용 0.
- 보통 시나리오의 **80%~95%** 가 이 단계에서 통과.

### Play with LLM

위 두 단계가 다 실패한 step 에서 **Dify LLM 치유** 가 추가로 발동.

- LLM 이 DOM 스냅샷을 보고 새 selector 후보를 제안.
- 호출 timeout 60s, 시도 1회 (재시도 없음).
- 비용 발생 — 사이트 구조 변경 후 1회 회복 용도로 권장.

### 옵션

| 옵션 | 의미 |
| --- | --- |
| 로그인 프로파일 | (세션 기본값 또는 수동 지정) |
| 화면 표시 | headed (창 보임) / headless |
| 액션 사이 지연 (slow-mo) | 1000ms 권장. 봇 차단 회피 + 디버깅 |

### 진행 상황

실행 중에는 `play-codegen.log` / `play-llm.log` 가 실시간 tail 됨. 우측에서 라이브 로그 확인.

---

## 8. 📊 결과 확인 및 스텝 추가

### 결과 패널

| 필드 | 의미 |
| --- | --- |
| 세션 ID | 디렉토리명. URL 공유에도 사용 |
| state | `done` / `error` / `recording` |
| step 수 | scenario.json 의 step 개수 |
| 인증 프로파일 | 사용된 auth profile 이름 |
| scenario.json 경로 | `~/.dscore.ttc.playwright-agent/recordings/<id>/scenario.json` |

### Scenario JSON / Original Script

펼치면 본문 + 다운로드 링크. JSON 은 step 단위 검토용, `.py` 는 codegen 원본.

### 실행 결과 (run_log.jsonl)

각 step 의 PASS / HEALED / FAIL + 셀렉터, 치유 단계, 스크린샷 경로.

### LLM 실행 로그 (play-llm.log)

LLM 치유 호출별 prompt + response + latency. 비용 분석에 사용.

### 원본 ↔ Regression 변경 분석

healing 으로 selector 가 수정된 step 의 **변경 전/후 diff** 를 시각화. 실패 후 어떻게 복구되었는지 한눈에.

### ＋ Step 추가 (codegen 미생성 액션)

codegen 이 잡지 못하는 액션을 수동 삽입. 예 — verify (URL/텍스트 단언), wait_for, scroll, mock_response 등.

| action | 용도 |
| --- | --- |
| verify | 결과 페이지 검증 — URL 패턴, 텍스트 포함, 요소 개수 등 |
| wait_for | 비동기 로딩 대기 — selector 또는 timeout |
| scroll | 무한 스크롤 페이지 |
| mock_status / mock_data | 외부 API mocking (재생 결정론 확보) |

---

## 9. ⬇ 다운로드 — 다른 PC 의 Replay UI 로 옮기기

### 언제 쓰는가

녹화한 시나리오를 **다른 PC** 에서 반복 실행하고 싶을 때. 받는 쪽 PC 에는 [Replay UI](replay-ui-guide.md) 가 설치되어 있어야 한다.

> **D17 (2026-05-11) 일원화** — 이전 `📦 모니터링 번들 다운로드` 모달 + `<세션ID>.bundle.zip` 흐름은 폐기됐다. 한 시나리오 = 한 `.py`. 받는 쪽이 alias / verify URL 을 사용자 입력으로 명시한다 (또는 *비로그인* 으로 비워둠). 결정 배경은 [PLAN_AUTH_PROFILE_NAVER_OAUTH.md §2 D17](PLAN_AUTH_PROFILE_NAVER_OAUTH.md).

### 어디 있는 버튼인가

`📊 결과 확인 및 스텝 추가` 섹션 → `Original Script` 카드 또는 `셀프힐링 후 (regression_test.py)` 카드 → **`⬇ 다운로드`** 링크.

### 흐름

1. 카드의 `⬇ 다운로드` 클릭 → 즉시 `.py` 파일 다운로드.
2. 응답은 **`auth_flow.sanitize_script` 통과한 안전한 본문** — 평문 비밀번호 / 의심 라인이 placeholder 로 자동 치환된다 (`__REPLACED_BY_BUNDLE_SANITIZER__`).
3. 받은 `.py` 를 USB / 이메일 / 사내 공유 폴더 등으로 모니터링 PC 에 옮긴다.

원본 `original.py` 와 셀프힐링 후 `regression_test.py` 둘 다 동일 sanitize 정책. 보통 selector 가 강화된 `regression_test.py` 가 안정적.

### 받는 쪽 (모니터링 PC) 에서 무엇을 하나

받는 쪽 흐름은 [replay-ui-guide.md](replay-ui-guide.md) 에 단계별로 정리되어 있다. 요약:

1. Replay UI 에서 (필요 시) 로그인 프로파일 등록 — 비로그인 시나리오면 생략 가능
2. `📄 시나리오 스크립트` 카드에 받은 `.py` 업로드
3. 적용할 프로파일 select (또는 *비로그인 — storage_state 미주입*) + verify URL 입력 (선택, 비우면 카탈로그 fallback)
4. `▶ 실행`

### 명령줄 (CLI) 진입점

UI 없이 같은 `.py` 를 받으려면 endpoint 직접 호출:

```bash
curl -o original.py 'http://127.0.0.1:18092/recording/sessions/<세션ID>/original?download=1'
curl -o regression_test.py 'http://127.0.0.1:18092/recording/sessions/<세션ID>/regression?download=1'
```

응답은 위 UI 흐름과 동일한 sanitize 통과 본문.

> *deprecated*: 이전 `python -m recording_service.recording_tools pack-bundle` CLI 는 D17 부로 stub (`NotImplementedError`) 으로 전환됨. 동등 결과는 위 endpoint 직접 호출.

---

## 10. 최근 세션 — 세션 목록 / 일괄 삭제

| 컬럼 | 의미 |
| --- | --- |
| ID | 클릭하면 그 세션이 현재 세션으로 선택됨 |
| state pill | 색상으로 done / error / recording 식별 |
| target_url | 녹화 시작 URL |
| step / created | 빠른 비교용 |

- **state filter** — 특정 상태만 보기
- **검색** — target_url substring
- **선택 삭제** — 체크박스 + `[선택 삭제]` 로 일괄 삭제 (recordings 디렉토리도 함께 제거)

---

## 11. 자동 정리 기능 (codegen 노이즈 제거)

Stop & Convert 단계에서 다음을 자동 수행. 사용자 개입 불필요.

### 변환 단계 (컨테이너 안)

| 정리 | 무엇을 처리 |
| --- | --- |
| **연속 중복 click 압축** | 같은 페이지 같은 accessible name 의 click 두 step 이 연속 → 1개로 합침. wrapper button + inner link 같은 codegen 이중 emit 회피. `popup_to` 보유한 쪽 우선 보존 |
| **IME 노이즈 키 제거** | `press CapsLock` / `Unidentified` / `Process` / `Compose` / `Dead` 무조건 drop. Playwright 가 거부하거나 재생 시 무의미 |
| **빈 fill 압축** | 빈 fill (`value=""`) 직후 같은 target 에 non-empty fill 이 오면 빈 fill drop. 한글 IME composition reset 부산물 |
| **navigation 분류** | 각 step 의 `kind` 메타 부여 (terminal / auxiliary). executor 가 페이지 이동 step 의 실패를 graceful 처리하는 데 사용 |
| **page alias 추적** | 새 탭 / popup 의 변수명 (`page1`, `page2` 등) 을 step 메타로 보존 |

### 재생 단계 (호스트 executor)

| 정리 | 무엇을 처리 |
| --- | --- |
| **popup 캡처 pages-diff fallback** | `expect_popup` timeout 났어도 `context.pages` diff 로 새 page 발견 시 alias 등록. JS dispatch fallback race 회피 |
| **transient alert click skip** | 재생 중 잠깐 떴다 사라지는 alert/dialog 버튼 클릭은 자동 skip |
| **typing fallback** | 자동완성 사이트의 fill 이 dropdown 을 못 띄울 때 한 글자씩 typing 으로 자동 전환 |
| **keyup 강제 dispatch** | typing 후 한글 IME 환경에서 ajax 추천이 발사되도록 keyup 이벤트 명시 dispatch |

자세한 결정 배경은 [docs/PLAN_RECORDING_DEDUPE_AND_POPUP_RACE.md](docs/PLAN_RECORDING_DEDUPE_AND_POPUP_RACE.md) 참조.

---

## 12. 파일 구조 — `recordings/<id>/`

호스트:

```text
~/.dscore.ttc.playwright-agent/recordings/<id>/
├── metadata.json              ← 세션 상태, error, auth_profile
├── original.py                ← codegen 출력 (Stop & Convert 직전)
├── scenario.json              ← 14-DSL 변환 결과 (자동 정리 적용됨)
├── scenario.healed.json       ← Play 후 selector 가 치유된 버전
├── run_log.jsonl              ← Play 결과 step 단위
├── play-codegen.log           ← Play (codegen) 표준 출력/에러
├── play-llm.log               ← Play with LLM
├── llm_calls.jsonl            ← LLM 호출별 prompt/response
├── llm_sla.json               ← LLM 호출 통계 (latency 분포 등)
├── step_<N>_pass.png          ← step 별 스크린샷 (PASS)
├── step_<N>_healed.png        ← (HEALED — 치유 후 성공)
├── error_final.png            ← 실패 시 마지막 화면
├── final_state.png            ← 정상 종료 시 최종 화면
└── index.html                 ← HTML 리포트 (한 페이지로 전체 결과)
```

컨테이너 안에서는 같은 디렉토리가 `/recordings/<id>/` 로 bind mount.

---

## 13. 자주 발생하는 문제와 원인

### Stop & Convert 후 state=error

`metadata.json` 의 `error` 필드가 어느 단계인지 알려준다.

| 메시지 | 원인 |
| --- | --- |
| "녹화 액션 0건" | 시작 직후 stop. target_url 페이지가 다 로딩되기 전에 stop |
| "converter_proxy 실패" | docker 데몬 미실행, 컨테이너 미존재 |
| "변환 실패 (returncode=...)" | original.py 가 14대 표준 액션 외 호출 사용 |
| "구조 검증 실패: step[N] action=fill 인데 value 가 비어 있음" | 빈 fill 이 단독으로 남음 (보통은 자동 정리되나 후속 fill 없는 경우 그대로) |

### 재생 시 popup alias 미등록

```text
[Step N] popup_to=pageX 마킹됐으나 popup 발생 안 함 — alias 등록 skip.
```

원인 — 카드/버튼이 화면 밖이거나 overlay 에 가려 Playwright click 이 timeout. 재녹화 시 해당 요소가 viewport 안에 보이는 상태에서 클릭 권장.

### "Locator.press: Unknown key: Unidentified"

자동 정리가 적용되기 전 시나리오. 새 변환을 한 번 더 돌리거나 (재녹화), `.py` 업로드 시 자동 정리 적용됨.

### 한글 입력이 자동완성 dropdown 을 안 띄움

자동 정리에 typing fallback + keyup dispatch 가 들어 있다. 그래도 실패하면 사이트가 `compositionend` 만 듣는 케이스 — 별도 PLAN 필요.

### "이 시나리오 결과로 회귀 테스트 자동 생성" 이 안 됨

`metadata.json` 에 실패 step 이 있으면 회귀 생성 skip. 모든 step PASS/HEALED 면 자동 생성 + Jenkins workspace 에 commit.

---

## 14. API 요약

OpenAPI 자동 문서 — `http://localhost:18092/docs`.

### 세션 라이프사이클

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/recording/start` | 녹화 시작 (codegen 프로세스 spawn) |
| POST | `/recording/stop/{sid}` | 녹화 중지 + 변환 |
| POST | `/recording/import-script` | 기존 `.py` 업로드 → 새 세션 |
| GET | `/recording/sessions` | 세션 목록 |
| GET | `/recording/sessions/{sid}` | 단일 세션 메타 |
| DELETE | `/recording/sessions/{sid}` | 삭제 (디렉토리 포함) |

### 세션 산출물

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/recording/sessions/{sid}/scenario` | scenario.json (또는 다운로드) |
| GET | `/recording/sessions/{sid}/original` | original.py |
| GET | `/recording/sessions/{sid}/run-log` | run_log.jsonl |
| GET | `/recording/sessions/{sid}/scenario_healed` | scenario.healed.json |
| GET | `/recording/sessions/{sid}/play-log/tail?kind={codegen,llm}&from={byte}` | 라이브 로그 tail |
| GET | `/recording/sessions/{sid}/screenshot/{name}` | 스크린샷 단일 파일 |
| GET | `/recording/sessions/{sid}/report` | HTML 리포트 |
| POST | `/recording/sessions/{sid}/assertion` | ＋ Step 추가 |

### 재생 (R-Plus)

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/experimental/sessions/{sid}/play-codegen` | Play (codegen) 시작 |
| POST | `/experimental/sessions/{sid}/play-llm` | Play with LLM 시작 |
| POST | `/experimental/sessions/{sid}/enrich` | LLM 메타 보강 |
| POST | `/experimental/sessions/{sid}/compare` | 두 실행 결과 비교 |

### Auth Profile

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/auth/profiles` | 카탈로그 목록 |
| GET | `/auth/profiles/{name}` | 단일 프로파일 |
| POST | `/auth/profiles/seed` | 시드 시작 (background) |
| GET | `/auth/profiles/seed/{seed_sid}` | 시드 진행 폴링 |
| POST | `/auth/profiles/{name}/verify` | 명시적 verify |
| DELETE | `/auth/profiles/{name}` | 삭제 |

### Discover

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/discover` | 크롤 시작 (background) |
| GET | `/discover/{job_id}` | 진행 상태 |
| POST | `/discover/{job_id}/cancel` | 취소 |
| GET | `/discover/{job_id}/csv` | URL 목록 CSV |
| GET | `/discover/{job_id}/json` | URL 목록 JSON |
| GET | `/discover/{job_id}/tree?type={crawl,path}` | 트리 JSON |
| GET | `/discover/{job_id}/tree.html` | 트리 HTML |
| POST | `/discover/{job_id}/tour-script` | tour `.py` 생성 |

---

## 다음 단계

| 알고 싶은 것 | 문서 |
| --- | --- |
| 시스템을 처음 띄우기 | [quickstart.md](quickstart.md) |
| 운영 (재배포 / 백업 / 로그) | [operations.md](operations.md) |
| 포트 / 환경변수 / DSL 계약 | [reference.md](reference.md) |
| 트러블슈팅 모음 | [docs/recording-troubleshooting.md](docs/recording-troubleshooting.md) |
| 자동 정리 결정 배경 | [docs/PLAN_RECORDING_DEDUPE_AND_POPUP_RACE.md](docs/PLAN_RECORDING_DEDUPE_AND_POPUP_RACE.md) |
