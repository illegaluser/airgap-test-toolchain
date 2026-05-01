# PLAN — Recording Service UI 개선

브랜치: `feat/grounding-recording-agent` 외 후속 — 본 문서는 Recording UI 의
**점진적 개선 라운드를 일지(journal) 형태로 누적**합니다. 각 라운드는 완결된
변경 묶음이며 (배경 → 핵심 변경 → 회귀) 순서로 정리됩니다. 새 라운드는
문서 하단에 추가하고, 상단의 *현재 UI 구조 스냅샷* 만 그에 맞춰 갱신합니다.

## 목적과 범위

**목적**: 녹화 → 재생 → 회귀 추출까지의 흐름에서 운영자가 "지금 무슨 일이
일어나고 있는지" / "이 결과를 어떻게 활용할지" 를 클릭 0~1 번에 알 수 있도록.

**1차 변경 영역**:

- `recording_service/web/` — HTML / CSS / JS (메인)
- `recording_service/server.py` — endpoint 신설
- `recording_service/rplus/router.py` — 비교 endpoint
- `jenkins-init/` 또는 Jenkins job description — 외부 진입점

**범위 외**: 다크 모드 · 모바일 레이아웃 · 세션 메타데이터 마이그레이션 ·
인증/권한 (recording-service 는 localhost-only daemon).

## 현재 UI 구조 스냅샷 (Round 4 기준)

페이지는 위에서 아래로 다음 순서:

1. **▶ New Recording** — 토글 *(default 닫힘)* — target_url, planning_doc_ref,
   로그인 프로파일 *선택* dropdown, Start / Play Script from File.
2. **🔍 Discover URLs** — 토글 *(default 닫힘)* — seed_url, auth profile, max
   pages/depth, 고급 옵션 5종(sitemap / request capture / SPA selectors /
   ignore_query / include_subdomains). 결과 표 + CSV / Tour Script 생성.
3. **🔐 Login Profile Registration** — 토글 *(default 닫힘)* — 인증 프로파일의
   *시드 / 검증 / 삭제* 만 담당. 시드 다이얼로그 4종 포함.
4. **활성 세션** — 녹화 진행 중에만 노출.
5. **결과 패널 군** — Scenario JSON / Original Script / **Run-log +
   Screenshots (모드 탭: LLM / 원본)** / R-Plus 그룹(Play / Generate Doc) /
   Compare / Regression Test Script.
6. **최근 세션** — 필터(text / state) + 일괄 선택 (체크박스 + 전체선택 /
   선택해제 / 선택삭제) + 행 단위 열기/삭제.

신규 backend endpoint (라운드 누적):

```text
GET  /recording/sessions/{sid}/run-log?mode=auto|llm|codegen   (R1+R4)
GET  /recording/sessions/{sid}/screenshot/{name}?mode=llm|codegen
                                                              (R1+R4, path-traversal 방어)
GET  /recording/sessions/{sid}/play-log/tail        (R1 — P2, ?from=&kind=)
GET  /recording/sessions/{sid}/regression           (R1 — 항목 4, ?download=1)
GET  /experimental/sessions/{sid}/diff-codegen-vs-llm  (R1 — 항목 4)
POST /experimental/sessions/{sid}/diff-analysis     (R2 — F3, LLM 의미 분석)
POST /discover                                      (Discover plan — 별도 문서)
GET  /discover/{job_id}                             (Discover plan)
POST /discover/{job_id}/cancel                      (Discover plan)
GET  /discover/{job_id}/csv                         (Discover plan)
GET  /discover/{job_id}/json                        (Discover plan)
POST /discover/{job_id}/tour-script                 (Discover plan)
```

> **R4 변경 (Breaking)** — `/run-log` 응답이 *list* 에서 `{mode, records}`
> 객체로 wrap. UI 의 모드 탭에서 어떤 데이터인지 구분하기 위함. 외부 클라이언트
> 에서 사용 중이면 `data.records` 로 unwrap 필요.

> Discover URLs 의 자체 설계는 [PLAN_URL_DISCOVERY.md](PLAN_URL_DISCOVERY.md) /
> [PLAN_URL_DISCOVERY_COVERAGE.md](PLAN_URL_DISCOVERY_COVERAGE.md). 본 문서는
> *Recording UI* 안에서의 통합 위치만 다룹니다.

---

## Round 1 — 1차 UX 갭 해소 (2026-04-29)

운영 중 발견된 UX 갭 8건을 한 묶음으로 처리. 우선순위는
**"운영 디버깅 즉시 가치 > 신뢰성 > 발견성"** 순. 총 작업량 ~9~10시간,
회귀 +14~15건 목표.

### 항목 요약

| # | 항목 | 영역 | 가치 |
|---|---|---|---|
| 1 | Jenkins → Recording UI 새 탭 진입 | jenkins-init | ★ |
| 2 | scenario.json / original.py 클립보드 복사 | UI | ★★ |
| 3 | R-Plus 호버펼침 + 클릭 고정 그룹 | UI | ★ |
| 4 | LLM healed ↔ codegen 원본 비교 + regression .py 다운로드 | UI + endpoint | ★★ |
| 5 (P1) | **Step 결과 시각화 (run_log + 스크린샷)** | UI + endpoint | **★★★** |
| 6 (P2) | Play 진행 로그 스트리밍 (tail polling) | UI + endpoint | ★★ |
| 7 (P3) | 세션 목록 검색/필터 | UI | ★ |
| 8 (P4) | Step 단위 JSON 복사 (5와 결합) | UI | ★★ |

### 의존성과 진행 순서

```text
1 ─────── (독립)
2 ─────── (독립)
5 ─┬───── 8 (5의 step 행에 복사 버튼 추가)
   └───── 4 (5의 결과 패널 위에 비교 메뉴 노출)
6 ─────── (독립)
3 ─────── (독립)
7 ─────── (독립)
```

진행 순서: **1 → 2 → 5 → 8 → 6 → 3 → 4 → 7**. 각 단계 완료 후 데몬 재시작 +
핵심 회귀만 즉시 검증, 끝에서 전체 회귀. 브라우저는 `Cmd+Shift+R` 로
캐시 무효화.

### 항목별 상세

#### 항목 1 — Jenkins 진입 새 탭

기존 Jenkins job (`ZeroTouch-QA`) description 의 Recording UI 링크가 같은 탭으로
진입해 운영자가 Jenkins 빌드 화면으로 돌아오기 번거로웠음. `<a target="_blank"
rel="noopener">` 으로 수정. 위치는 `jenkins-init/*.groovy` 또는 `.xml`
description, 또는 `ZeroTouch-QA.jenkinsPipeline`. 회귀: 정적 변경 — 수동 확인.

> ⚠ 후속 — Jenkins 의 antisamy-markup-formatter (Safe HTML) 가 `target` /
> `rel` 속성을 strip 해 무력화됨. Round 2 의 F1 에서 *Recording UI 자체에
> 뒤로가기 버튼* 으로 우회.

#### 항목 2 — scenario.json / original.py 클립보드 복사

두 카드 헤더에 `📋 복사` 버튼 추가 (다운로드 옆). `navigator.clipboard.writeText`
사용 — Chrome / Edge / Safari 최신 모두 지원. 복사 직후 inline 토스트 (버튼 옆
0.8s `✓ 복사됨`). 파일: `web/index.html` (마크업), `web/app.js`
(`_copyToClipboard()` 헬퍼 + 핸들러), `web/style.css` (`.toast-inline`).
회귀: clipboard API 는 jsdom 검증 어려워 수동.

#### 항목 5 (P1) — Step 결과 시각화 ★★★

**갭**: Play with LLM 후 `run_log.jsonl` 에 step 별 status / heal_stage 가 다
있는데 UI 에 미노출. 운영자는 텍스트 stdout tail 만 봐야 했고, 스크린샷
(`step_<n>_<status>.png`) 도 디스크에 있지만 UI 진입 경로 없음.

**(5-A) 신규 endpoint** `GET /recording/sessions/{sid}/run-log` —
`run_log.jsonl` 을 파싱해 `list[dict]` 반환. `screenshot` 필드는 PNG 가
디스크에 있을 때만 채움. 파일 없으면 404.

**(5-B) 신규 endpoint** `GET /recording/sessions/{sid}/screenshot/{name}` —
보안: `name` 은 `^step_\d+_\w+\.png$` 정규식 화이트리스트로 path traversal 차단.
PNG 직접 서빙 (`FileResponse`).

**(5-C) 결과 패널 신규 카드** `#run-log-card` — 기존 `#scenario-card` 다음
위치. `state=done` 이고 `run_log.jsonl` 존재 시에만 노출. 표 컬럼: step / action
/ target / status / heal_stage / screenshot. status 는 PASS 녹색 / FAIL 빨강 /
HEALED 노랑 pill. heal_stage 는 none/local/dify/visibility 4색. 스크린샷 셀은
클릭 시 `<dialog>` 모달 확대. FAIL step 자동 스크롤 + 강조.

**회귀** (5건): run-log 정상 파싱 / 파일 없을 때 404 / screenshot 정상 서빙 /
path traversal 거부 / 화이트리스트 밖 이름 거부.

#### 항목 8 (P4) — Step 단위 JSON 복사

항목 5 의 표 각 행 끝에 📋 버튼. 해당 step 의 JSON 객체(run_log 형식 그대로)를
클립보드에. 항목 2 의 `_copyToClipboard()` 재사용. 수동 확인.

#### 항목 6 (P2) — Play 진행 로그 스트리밍

**갭**: `▶ Play with LLM` 클릭 후 30~60s 동안 정적 스피너만 — 사용자가 멈춘 줄
알고 다시 누르거나 새로고침.

**설계 결정**: subprocess 실행을 비동기로 바꾸지 않음. `play-llm.log` /
`play-codegen.log` 가 이미 디스크에 실시간으로 쓰이므로 이를 tail polling.

**신규 endpoint** `GET /experimental/sessions/{sid}/play-log/tail?from=&kind=` —
지정 offset 이후 바이트만 반환. 파일이 늦게 생기는 경우를 위해 미존재 시
404 가 아니라 `200 + exists=false`. `kind=llm|codegen` 분기.

**프론트**: `▶ Play` 핸들러를 비동기 시작 + 1s 폴링으로 변경. 진행 박스
(`<pre class="play-progress">` 검정 배경 + 모노 폰트) 자동 스크롤. 완료 후
박스를 collapsible 로 보존.

**회귀** (3건): 새 바이트만 / 파일 없을 때 200 + exists=false / kind 분기.

#### 항목 3 — R-Plus 호버펼침 + 클릭 고정 그룹

flat 4 버튼 → 2 그룹 dropdown:

```
[▶ Play ▾]                   [📝 Generate Doc ▾]
   ├─ ▶ Codegen 녹화코드 실행      ├─ 📝 시나리오 문서 생성
   └─ ▶ LLM 적용 코드 실행         └─ ⚖ 시나리오 문서 ↔ JSON 비교
```

상호작용: `:hover` 로 즉시 펼침 (CSS only) + 클릭 시 `.expanded` 고정. 외부
클릭 / ESC 로 닫힘. 모바일/키보드 보조: `aria-haspopup` + `aria-expanded`.
**기존 `btn-play-*` ID 는 유지** — 기존 핸들러/테스트 회귀 0.

#### 항목 4 — codegen ↔ LLM healed 비교 + regression .py 다운로드 ★★

LLM 힐링으로 의도에 맞게 동작하는 스크립트를 codegen 원본과 비교한 후, 문제
없으면 다운로드해 회귀 슈트로 옮기는 흐름.

**비교 대상**: LEFT `original.py` (codegen) ↔ RIGHT `regression_test.py`
(`scenario.healed.json` 으로부터 자동 생성). 후자는 매 Play with LLM 후 이미
생성됨 — 본 항목은 *노출 + 비교 + 다운로드*.

**신규 endpoint**:
- `GET /recording/sessions/{sid}/regression` — `regression_test.py` 본문.
  `?download=1` 시 attachment.
- `GET /experimental/sessions/{sid}/diff-codegen-vs-llm` — `difflib.unified_diff`
  결과 + 양쪽 raw content. 한쪽 부재 시 200 + `_exists=false`. 양쪽 부재 시 404.

**UI**: 신규 카드 `#diff-card` (위치: `#run-log-card` 다음). unified diff `<pre>`
라인별 색상(`+` 녹색 / `-` 빨강 / `@@` 파랑). `[unified] [side-by-side]` 라디오
(side-by-side 는 `difflib.HtmlDiff` inject — `escape=True` default 로 XSS 차단).
판단 후 `⬇ regression_test.py 다운로드` 클릭.

**회귀** (6건): regression GET / download 헤더 / 404 / diff 양쪽 / diff 404 /
diff 한쪽만.

#### 항목 7 (P3) — 세션 목록 검색/필터

백엔드 변경 없음 — 클라이언트 측 필터. 카드 헤더에 text input + state select.
`input` / `change` 이벤트로 행 hide/show. `localStorage` 에 마지막 필터 보존.
수동 확인.

### Round 1 회귀 누적

| 영역 | 추가 | 누적 |
|---|---|---|
| 항목 5 (run-log + screenshot) | 5 | 95 |
| 항목 6 (play-log tail) | 3 | 98 |
| 항목 4 (regression .py + diff) | 6 | 104 |

기존 90 건 모두 유지, 0 회귀.

---

## Round 2 — 운영 피드백 follow-up (2026-04-29)

Round 1 출시 직후 받은 운영 피드백 3건. 모두 *기존 변경의 약점 보강*.

### F1 — Jenkins 새 탭 진입 → Recording UI 의 뒤로가기 버튼

운영 검증에서 Jenkins antisamy-markup-formatter (Safe HTML) 가 `<a
target="_blank">` 의 `target` / `rel` 속성을 보안상 strip 함을 확인. provision.sh
의 explicit `target="_blank"` 가 무력화. sidebar-link plugin 의 `LinkAction`
클래스도 `target` 미지원.

→ **Recording UI 자체에 좌측 상단 `← 뒤로` 버튼**. `document.referrer` →
`history.back()` → Jenkins 메인 fallback. *항상 노출* (조건부 hidden 시
사용자 혼란).

| 변경 | 위치 |
|---|---|
| `← 뒤로` 버튼 마크업 | `web/index.html` 헤더 좌측 |
| 핸들러 | `web/app.js` `_initBackButton()` |
| 페이지 명칭 통일 (`Recording Service` → **`Recording UI`**) | `web/index.html` `<title>` / `<h1>` / footer |
| description 안내 갱신 (`target="_blank"` 제거) | `provision.sh` |

### F2 — Regression Test Script (.py) 별도 카드 분리

R1 의 항목 4 는 비교 카드 안에서 다운로드 링크만 노출. 사용자 피드백 — Scenario
JSON / Original Script 와 동일하게 **별도 섹션** 으로 보여줘야 일관성 ↑.

| 변경 | 위치 |
|---|---|
| `#regression-card` (📋 복사 + ⬇ 다운로드 + 코드 미리보기) | `web/index.html` |
| `_renderRegression(sid)` 진입 시 자동 fetch | `web/app.js` |

### F3 — 1차원 unified diff → LLM 의미 분석

R1 의 unified diff 는 라인별 색상만. 사용자 피드백 — selector swap 의도, 위험
평가, 회귀 채택 권고 같은 **의미 정보** 가 더 유용.

LLM (Ollama) 호출로 4 섹션 markdown 생성:

1. 핵심 변경 요약
2. 변경 라인 분석 (selector swap / hover 추가 등)
3. 위험 평가 (결정성 / 의도 일치 / 잠재 리스크)
4. 회귀 채택 권고 (✅ / ⚠ / ❌)

POST 인 이유: Ollama 호출이 30~60s 부수효과 → GET 캐싱 의미론과 충돌.

| 변경 | 위치 |
|---|---|
| `analyze_codegen_vs_regression()` + `DiffAnalysisResult` | `recording_service/enricher.py` |
| `POST /experimental/sessions/{sid}/diff-analysis` | `recording_service/rplus/router.py` |
| 비교 카드에 `🔎 LLM 분석` + markdown 렌더러 | `web/index.html` + `web/app.js` |
| 원시 diff 는 `<details>` collapsible 로 secondary | `web/index.html` |

### Round 2 회귀 누적

| 영역 | 추가 | 누적 |
|---|---|---|
| diff endpoint | 3 | 104 (R1 마감) |
| diff-analysis (LLM hook) | 3 | **107** |

기존 90 건 모두 유지.

---

## Round 3 — 레이아웃 재구성 (2026-04-30)

### 배경

Round 1~2 로 결과 패널과 백엔드 endpoint 가 풍부해졌지만, **상단 폼 영역이
세 가지 이질적 작업을 하나로 묶고 있어** 사용자가 매번 무관한 영역까지
스크롤해야 했다. 또한 Recent Sessions 의 일괄 정리 수단이 없어 세션이 누적되면
1건씩 삭제 버튼을 반복해 눌러야 했다.

이번 라운드는 *기능 추가가 아니라 정리* — 동일한 기능을 더 적은 클릭으로
도달 가능하게 한다.

### 변경 묶음

#### R3-1 — 상단 메뉴 3 토글 통일

기존: `<section class="card">` 가 항상 펼쳐진 채로 첫 화면을 차지. Discover URLs
는 이미 `<details>` 였지만 `open` 기본값이라 사실상 항상 열림.

변경: 메인 폼 카드 3종을 모두 *default-collapsed* `<details>` 로 통일.

| 토글 ID | 제목 | 기존 |
|---|---|---|
| `#new-recording-section` | **▶ New Recording** | "새 녹화 시작" 평면 섹션 |
| `#discover-section` | **🔍 Discover URLs** | `<details open>` |
| `#login-profile-section` | **🔐 Login Profile Registration** | New Recording 안에 묶여 있던 인증 fieldset |

각 `<summary>` 좌측에 ▸ 마커 + `[open]` 시 90° 회전 transition (CSS only).

> "새 녹화시작" → **"New Recording"** 영문 명칭 통일. h1/h2 사이의 시각적 위계와
> 다른 섹션 명칭과의 톤 일관성을 위해.

#### R3-2 — Login Profile Registration 분리

기존 New Recording 폼 안의 `<fieldset class="auth-block">` 가 *3가지 책임* 을
혼재했음:

1. 녹화에 사용할 프로파일 *선택* (dropdown)
2. 프로파일 *관리* (verify / 삭제)
3. 새 프로파일 *시드* (`+ 새 세션 시드` + 다이얼로그 4종)

분리 결과:

- **New Recording** 에 단순 `<select id="recording-auth-profile" name="auth_profile">`
  만 남김 (책임 #1). 사용자는 *이미 등록된* 프로파일을 고르기만 함.
- 새 섹션 **Login Profile Registration** 이 책임 #2~#3 을 담당. 기존 fieldset
  과 4 다이얼로그 (`#auth-seed-dialog` / `#auth-seed-progress` /
  `#auth-expired-dialog` / `#auth-machine-mismatch-dialog`) 를 모두 이리로 이동.
  관리용 selector 는 기존 ID `#auth-profile-select` 유지 — 모든 verify/delete/seed
  핸들러 무수정.

JS 동기화 (app.js):

- `loadAuthProfiles()` 가 두 selector (`#auth-profile-select` 관리용 +
  `#recording-auth-profile` 녹화용) 를 같은 옵션 목록으로 채움. 각자 사용자
  선택값 보존.
- 시드 완료 (`#btn-auth-seed-done`) 시 두 selector 모두 새 프로파일로 미리
  선택 — 사용자가 등록 직후 Recording 으로 이동했을 때 한 번 더 고르지 않게.
- Discover 의 auth selector 동기 기준을 `#auth-profile-select` →
  `#recording-auth-profile` 로 변경 (관리 selector 가 아니라 녹화 selector 를
  따라가야 자연스러움).

#### R3-3 — 최근 세션 일괄 작업

행 첫 컬럼에 체크박스 추가 (colspan 7→8). 카드 상단에 일괄 작업 바:

```
[전체 선택] [선택 해제] [선택 삭제]   N개 선택
```

- `#session-th-check` (헤더) — 현재 *보이는* 행 전체 선택/해제. 부분 선택 시
  `indeterminate` 상태로 표시.
- `#btn-session-delete-selected` — 선택 0 시 disabled. 클릭 시 confirm 후
  순차 `await deleteSession(sid)` (병렬 호출은 데몬에 부하). 실패 항목은
  누적 후 한 번에 alert.
- `_selectedSessionIds()` / `_updateSessionBulkUi()` 헬퍼.

#### R3-4 — `.auth-btn` 텍스트 가시성 버그 수정

전역 `button { color: #fff; }` (style.css:94) 에 대해 `.auth-btn` 이
`background: #fff` 만 잡고 `color` 를 안 잡아 cascade 결과 *흰 글자 + 흰 배경*
— "+ 새 세션 시드" 버튼 텍스트가 invisible 이었다.

원인 추적이 늦어진 이유: verify·삭제 버튼은 default disabled 라 `:disabled`
의 `color: #888` 이 보였고, `.auth-btn-danger` 는 빨간색을 명시 — 두 케이스
모두 우연히 보임. enabled 한 plain `.auth-btn` (= seed 버튼) 만 invisible.

수정: `.auth-btn { color: var(--text); }` 한 줄 추가.

### 변경 파일

| 파일 | 변경 |
|---|---|
| `web/index.html` | 3 토글 (`<details>`) 구조, New Recording fieldset 단순화, Login Profile Registration 신규 섹션 + 다이얼로그 이동, 세션 테이블 colspan 8 + 일괄 작업 바 |
| `web/app.js` | `loadAuthProfiles` 양 selector 동기, seed-done 양 selector 미리 선택, discover sync 셀렉터 변경, `_selectedSessionIds` / `_updateSessionBulkUi` / 일괄 삭제 핸들러 |
| `web/style.css` | 3 토글 공통 `summary` + `▸` 마커 회전, `.session-bulk-actions` 레이아웃, `.auth-btn { color }` 가시성 수정 |

### 회귀

UI 정적 변경 위주 — 자동화 신규 회귀 없음. **기존 백엔드 회귀는 무수정 통과**:

- `test_url_discovery.py` 24/24
- `test_discover_api_e2e.py` 13/13

수동 검증 체크리스트:

1. 첫 진입 시 3 토글 모두 닫힘. `▸` → 클릭 → `▾` 회전.
2. New Recording 의 dropdown 만으로 비로그인 녹화 시작 / 프로파일 선택 녹화
   양쪽 동작.
3. Login Profile Registration 에서 `+ 새 세션 시드` 텍스트가 *보임*.
   (R3-4 회귀 가드)
4. 시드 완료 후 New Recording dropdown 에서도 새 프로파일이 미리 선택됨.
5. Discover URLs 폼의 auth dropdown 이 New Recording 선택값을 따라 표시됨
   (사용자가 손대기 전).
6. 최근 세션 — 체크박스 0 시 `선택 삭제` disabled. 헤더 체크박스로 전체 선택,
   부분 선택 시 indeterminate. 일괄 삭제 후 결과 패널 / assertion 영역 hidden.

> 브라우저 hard refresh (`Cmd+Shift+R`) 필수 — 정적 자산 캐시.

---

## Round 4 — codegen 원본 재생의 액션별 결과 시각화 (2026-04-30)

### 배경

R-PLUS 의 *▶ Play ▾* 드롭다운에는 두 재생 모드가 공존한다.

| 모드 | 메커니즘 | step 메타 + 스크린샷 | Run-log 카드 |
|---|---|---|---|
| ▶ LLM 적용 코드 실행 | `zero_touch_qa` DSL executor | `run_log.jsonl` + `step_<n>_<status>.png` 자동 생성 | 표가 정상 노출 |
| ▶ 테스트코드 원본 실행 | `original.py` 그대로 subprocess | **없음** (raw Playwright) | 카드 hidden |

운영 사용자 관찰: "테스트코드 원본 실행" 을 누르면 외부 Chromium 창에서 액션이
화면으로는 보이지만, *결과 패널 안에는 아무 흔적도 남지 않는다*. 사용자는 step
별 PASS/FAIL, 액션 타임라인, 액션 직후 스크린샷이 LLM 모드와 동일하게 노출되길
원했다.

### 설계 결정

- **데이터 소스**: Playwright `context.tracing.start(snapshots, screenshots)`.
  Playwright 권장 방식이며, raw 스크립트를 *수정하지 않고* 부수적 산출물
  (trace.zip) 만으로 액션 타임라인을 얻을 수 있음.
- **LLM 모드는 그대로**: 기존 `run_log.jsonl` (heal_stage 보존). 변경 없음.
- **데이터 격리**: codegen artifacts 는 별도 위치 — `codegen_run_log.jsonl` +
  `codegen_screenshots/`. 두 모드의 산출물이 충돌하지 않음.
- **UI 통합**: `#run-log-card` 에 모드 탭 (`[LLM] [원본]`) 추가. 데이터가
  있는 탭만 활성. 마지막 실행한 모드를 자동 선택.

### 변경 묶음

#### R4-1 — codegen subprocess 에 tracing 자동 주입

신규: [recording_service/codegen_trace_wrapper.py](../recording_service/codegen_trace_wrapper.py).

`Browser.new_context` / `BrowserContext.close` 를 monkey-patch 한 진입점.
context 가 만들어지면 즉시 `tracing.start(screenshots=True, snapshots=True,
sources=False)` 호출, close 직전에 `tracing.stop(path=<session>/trace.zip)`.
사용자 스크립트는 `runpy.run_path(..., run_name="__main__")` 으로 *수정 없이*
실행. atexit 핸들러로 예외 종료 시에도 best-effort 저장.

[recording_service/replay_proxy.py:199-260](../recording_service/replay_proxy.py#L199-L260) 의 `run_codegen_replay()` 변경:

- subprocess 명령을 `[py, str(script)]` → `[py, "-m",
  "recording_service.codegen_trace_wrapper"]` 로 교체.
- 실행 대상 스크립트는 `CODEGEN_SCRIPT` env 로 전달 (annotated 분기 유지).
- `PYTHONPATH` 에 프로젝트 루트 추가 — 래퍼 모듈 import 보장.
- subprocess 종료 직후 `trace_parser.parse_trace()` 호출. 파싱 실패는 silent
  (codegen subprocess 의 returncode/stdout 은 그대로 사용자에게 반환).

#### R4-2 — trace.zip → run-log + 스크린샷 변환

신규: [recording_service/trace_parser.py](../recording_service/trace_parser.py).

- `zipfile.ZipFile` + 표준 라이브러리만 사용 (Pillow 는 PNG 변환 시 *선택*).
- `trace.trace` (JSONL) 의 `before` / `after` 페어 또는 단일 `action` 이벤트
  양쪽 형식 지원 — Playwright 버전 변경 내성.
- noise 메소드 (`browser.newContext`, `browserContext.close`, `newPage` 등)
  자동 제외 → 사용자 의미 있는 액션만 표에 남김.
- `screencast-frame` 이벤트의 `(timestamp, sha1)` 매핑에서 각 액션 endTime
  직후의 frame 한 장을 골라 `step_<n>_<status>.png` (Pillow 있을 때) 또는
  `.jpeg` 로 저장.
- 출력 record 스키마는 LLM 모드와 동일: `{step, action, target, status, ts,
  screenshot, [error]}`. heal_stage 는 codegen 에서 항상 미보유 → 표에서
  default `none`.

#### R4-3 — server.py: `mode` 쿼리 도입

[recording_service/server.py](../recording_service/server.py):

- `GET /recording/sessions/{sid}/run-log?mode=auto|llm|codegen` —
  - `auto` (기본): llm 우선, 없으면 codegen, 둘 다 없으면 404
  - 응답: **`{mode, records}`** 객체로 wrap (이전 list 에서 변경 — Breaking)
- `GET /recording/sessions/{sid}/screenshot/{name}?mode=llm|codegen` —
  - `mode=codegen` 시 `<session>/codegen_screenshots/<name>` 검색
  - 화이트리스트 정규식에 `.jpeg`/`.jpg` 추가 (Pillow 미설치 환경 대응)

#### R4-4 — UI: 모드 탭

[recording_service/web/index.html](../recording_service/web/index.html):

- `#run-log-card` 헤더에 `<div class="run-log-mode-tabs">` —
  `[LLM] [원본]` 토글 버튼.

[recording_service/web/app.js](../recording_service/web/app.js):

- `_runLogState` 모듈 상태 — 현재 sid / 선택 모드 / 두 모드 가용성 추적.
- `_renderRunLog(sid, {mode})` 가 두 모드 모두 `Promise.all` 로 fetch →
  존재 여부에 따라 탭 활성/비활성 자동 결정. 카드 노출 조건도 통합.
- `▶ 테스트코드 원본 실행` 완료 → `mode=codegen` 자동 선택 + re-render.
- 스크린샷 모달은 `data-shot-mode` 를 읽어 `?mode=codegen` 쿼리 전달.

[recording_service/web/style.css](../recording_service/web/style.css):

- `.run-log-mode-tabs` — pill 그룹 스타일. 활성 탭은 accent 색상 배경.

### 사용자 흐름

1. New Recording 으로 녹화 → 종료 → 결과 패널 진입.
2. `▶ Play ▾` 드롭다운에서 **▶ 테스트코드 원본 실행** 클릭.
3. 외부 Chromium 창에서 codegen 그대로 재생되며 화면 노출. 종료 후 자동
   닫힘.
4. *Run Log + Screenshots* 카드의 **[원본]** 탭이 자동 활성. 표에 액션이
   step / action / target / status 로 나열. 각 행의 📷 클릭 시 스크린샷
   모달 확대.
5. 같은 세션에서 **▶ LLM 적용 코드 실행** 도 돌리면 [LLM] 탭이 활성화 —
   탭 클릭으로 두 모드를 비교 가능.

### 회귀

| 영역 | 추가 | 누적 |
|---|---|---|
| trace_parser 단위 (action 추출 / FAIL / noise 제외 / 빈 trace / action 형식 / Pillow fallback) | 7 | 114 |
| `/run-log` 모드 분기 (codegen / auto-prefers-llm / fallback-codegen / scheme wrap) | 4 | 118 |
| `/screenshot` codegen 모드 + jpeg 허용 | 1 | 119 |
| codegen_trace_wrapper 통합 (실제 Chromium → trace.zip 검증) — e2e marker | 1 | 120 |

기존 회귀 모두 유지. 단, `/run-log` 응답 스키마 변경에 맞춰
`test_get_run_log_returns_parsed_steps_with_screenshot_field` 가 `data["records"]`
로 unwrap 하도록 보강. `test_play_codegen_invokes_python_on_original` 은 래퍼
호출로 cmd 가 바뀜에 맞춰 업데이트.

### 신규/수정 파일

**신규**:

- `playwright-allinone/recording_service/codegen_trace_wrapper.py`
- `playwright-allinone/recording_service/trace_parser.py`
- `playwright-allinone/test/test_codegen_trace_wrapper.py`
- `playwright-allinone/test/test_trace_parser.py`

**수정**:

- `playwright-allinone/recording_service/replay_proxy.py` — wrapper 호출 +
  trace 파싱 후처리
- `playwright-allinone/recording_service/server.py` — `/run-log`,
  `/screenshot/{name}` 에 `mode` 쿼리, 응답 wrap, jpeg 화이트리스트
- `playwright-allinone/recording_service/web/index.html` — 모드 탭 마크업
- `playwright-allinone/recording_service/web/app.js` — `_runLogState`,
  `_renderRunLog` mode-aware 재구현, 탭 핸들러, 스크린샷 모달 mode 전달
- `playwright-allinone/recording_service/web/style.css` — `.run-log-mode-tabs`
- `playwright-allinone/test/test_recording_service.py` — 응답 스키마 + codegen
  모드 케이스, wrapper 호출 검증

### 위험과 회피 (R4 추가분)

| 위험 | 회피 |
|---|---|
| trace.zip 디스크 누적 (수십 MB) | 세션당 1개만 보존, 재실행 시 덮어쓰기. 누적 정리는 후속 |
| Playwright 버전별 trace 포맷 차이 | parser 가 `before/after` 와 `action` 양쪽 형식 모두 처리. 핵심 필드만 사용 |
| `/run-log` 응답 wrap 변경 (Breaking) | UI 함께 변경. 외부 클라이언트 영향은 0 (해당 endpoint 는 Recording UI 전용) |
| Pillow 미설치 환경 | JPEG 그대로 저장, 화이트리스트가 jpeg 허용. PNG 변환은 best-effort |
| async API codegen | 1차 범위 밖. 현재 codegen 은 sync 만 출력 |
| 사용자 스크립트 중간 예외로 close() 미호출 | atexit 핸들러로 trace 강제 stop — best-effort 저장 |

> 브라우저 hard refresh (`Cmd+Shift+R`) 필수 — 정적 자산 캐시.

---

## 위험과 회피 (라운드 누적)

| 위험 | 회피 | 도입 라운드 |
|---|---|---|
| 기존 `btn-play-*` ID 변경 시 회귀 깨짐 | ID 유지 — dropdown 안에 그대로 둠 | R1 |
| `/screenshot/{name}` path traversal | 정규식 `^step_\d+_\w+\.png$` 화이트리스트 | R1 |
| `play-log/tail` 폴링이 데몬 부하 | 1s 인터벌 + 완료 시 즉시 중단 | R1 |
| 큰 run_log.jsonl (1000+ step) 표 렌더 | 가상 스크롤 미적용 — 평균 5~30 step 가정 | R1 |
| `difflib.HtmlDiff` 출력 XSS | `escape=True` default + 자체 HTML 만 | R1 |
| Jenkins Safe HTML 의 `target` strip | Recording UI 자체 뒤로가기 버튼 | R2 (F1) |
| LLM 분석 30~60s 지연 | POST + 진행 표시. 캐싱은 후속 후보 | R2 (F3) |
| 일괄 삭제 병렬 호출이 데몬 부하 | 순차 `await` 처리 + 실패 누적 후 한 번에 alert | R3 |
| `.auth-btn` 같은 cascade 색상 함정 재발 | `color` 항상 명시 권장 — code review 가드 | R3 |

## 의도된 비범위 — 후속 검토 후보

- 다크 모드 / 모바일 레이아웃
- `<a target="_blank">` 우회를 위한 antisamy 커스텀 정책 (위험: 전역
  description 영향)
- LLM 분석 캐싱 (POST 결과를 `<session>/diff-analysis.md` 에 저장 → 재호출 시
  fast path)
- 분석의 `회귀 채택 권고` 가 ❌ 일 때 다운로드 버튼 disabled 처리
- run-log 의 step 단위 "이 step 만 분리 실행" 기능
- 토글 상태(open/closed) 의 localStorage 영속화 — 사용자별 워크플로 학습
- 세션 일괄 삭제의 진행 표시 (현재는 순차이지만 다수 선택 시 답답할 수 있음)

## 변경 이력

| 일자 | 작성자 | 라운드 | 내용 |
| --- | --- | --- | --- |
| 2026-04-29 | Claude | R1 초안 | 8 항목 + 의존성 + 회귀 전략 |
| 2026-04-29 | Claude | R1 + R2 완료 | 8 항목 + follow-up 3건. 회귀 90 → 107. F1 뒤로가기 버튼 (Safe HTML 우회), F2 regression .py 별도 카드, F3 LLM 의미 분석 |
| 2026-04-30 | Claude | R3 완료 | 상단 3 토글 통일 ("새 녹화시작" → "New Recording"), Login Profile Registration 분리, 최근 세션 일괄 선택/삭제, `.auth-btn` 가시성 수정. UI 정적 변경 — 백엔드 회귀 0. 문서를 라운드 일지 형태로 재구성 |
| 2026-04-30 | Claude | R4 완료 | codegen 원본 재생에 Playwright tracing 자동 주입 (`codegen_trace_wrapper.py`) → `trace.zip` 파싱 (`trace_parser.py`) → `codegen_run_log.jsonl` + `codegen_screenshots/` 산출. Run-log 카드에 `[LLM] [원본]` 모드 탭. `/run-log` 응답 스키마 wrap (Breaking) + `/screenshot` 에 mode 쿼리. 단위/통합 +13 (107 → 120). 문서 스냅샷 갱신 |
