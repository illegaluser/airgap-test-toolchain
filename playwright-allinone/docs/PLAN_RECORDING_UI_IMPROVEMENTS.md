# PLAN — Recording Service UI 개선

작성: 2026-04-29 · 브랜치: `feat/grounding-recording-agent`

운영 중 발견된 UX 갭을 한 번에 해소. 사용자 4건 + 컨설턴트 4건 = 8 항목.
구현 우선순위는 "운영 디버깅 즉시 가치 > 신뢰성 > 발견성" 순.

## 목적과 범위

**목적**: 녹화부터 회귀 테스트 추출까지의 흐름에서 운영자가 "지금 무슨 일이
일어나고 있는지" / "이 결과를 어떻게 활용할지" 를 클릭 0~1 번에 알 수 있도록.

**범위**:
- `recording_service/web/` (HTML/CSS/JS) — 본 프로젝트 메인 변경 영역
- `recording_service/server.py` — 신규 endpoint 4건 (run-log / play-tail / diff / regression)
- `recording_service/rplus/router.py` — 비교 endpoint 추가
- `jenkins-init/` 또는 Jenkins job description — 외부 진입점 1군데
- 백엔드 Python 모듈 변경 없음 (executor / converter 등 unchanged)

**범위 외**:
- 다크 모드, 모바일 레이아웃
- 세션 메타데이터 마이그레이션 (기존 세션 호환 유지)
- 인증/권한 (recording-service 는 localhost-only daemon)

## 작업 항목 요약

| # | 항목 | 영역 | 작업량 | 가치 |
|---|---|---|---|---|
| 1 | Jenkins → Recording UI 새 탭 진입 | jenkins-init | 5분 | ★ |
| 2 | scenario.json / original.py 클립보드 복사 | UI | 30분 | ★★ |
| 5 | **P1 — Step 결과 시각화 (run_log + 스크린샷)** | UI + endpoint | 2~3h | **★★★** |
| 8 | P4 — Step 단위 JSON 복사 (5와 결합) | UI | 30분 | ★★ |
| 6 | P2 — Play 진행 로그 스트리밍 (tail polling) | UI + endpoint | 1.5h | ★★ |
| 3 | R-Plus 호버펼침 + 클릭 고정 그룹 | UI | 1h | ★ |
| 4 | LLM healed ↔ codegen 원본 비교 + regression .py 다운로드 | UI + endpoint | 2~3h | ★★ |
| 7 | P3 — 세션 목록 검색/필터 | UI | 30분 | ★ |

총 작업량 추정: **8.5~10.5h**, 회귀 테스트 추가 **+10~15건**.

## 의존성 그래프 (구현 순서)

```
1 ─────── (독립)
2 ─────── (독립)
5 ─┬───── 8 (5의 step 행에 복사 버튼 추가)
   └───── 4 (5의 결과 패널 위에 비교 메뉴 노출)
6 ─────── (독립, 5와 같은 결과 패널에 노출되지만 코드 격리 가능)
3 ─────── (독립)
7 ─────── (독립, 세션 목록 영역만 변경)
```

**구현 순서**: 1 → 2 → 5 → 8 → 6 → 3 → 4 → 7

각 단계 완료 후 데몬 재시작 + 핵심 회귀만 즉시 검증, 끝에서 전체 회귀.
브라우저는 Cmd+Shift+R 로 캐시 무효화.

---

## 항목별 상세

### 항목 1 — Jenkins 진입 새 탭

**현재**: Jenkins job (`ZeroTouch-QA`) 의 description 에 Recording UI 링크가
같은 탭으로 진입. Jenkins 빌드 화면이 사라져 운영자가 다시 돌아오기 번거로움.

**변경**:
- 링크 `<a href="..." target="_blank" rel="noopener">` 로 수정.
- `rel="noopener"` 는 Recording UI 페이지가 `window.opener` 를 통해 Jenkins
  을 조작하지 못하게 하는 보안 모범사례 (Jenkins 가 신뢰 가능해도 습관).

**영역**:
- 후보 1: `playwright-allinone/jenkins-init/*.groovy` 또는 `.xml` 의 description 필드
- 후보 2: `ZeroTouch-QA.jenkinsPipeline` 안의 description 출력

본 작업 시작 시 `grep -rn "18092\|Recording" jenkins-init/ ZeroTouch-QA.jenkinsPipeline` 으로 위치 확정.

**회귀**: 없음 — 정적 변경. 수동 확인 (Jenkins UI 진입 후 클릭).

---

### 항목 2 — 클립보드 복사

**현재**: `Scenario JSON` 카드와 `Original Script (.py)` 카드 헤더에 `⬇ 다운로드` 만 있음.

**변경**:
- 두 카드 헤더에 📋 복사 버튼 추가 (다운로드 옆).
- `navigator.clipboard.writeText(text)` 사용. Chrome/Edge/Safari 최신 모두 지원.
- 복사 직후 inline 토스트 — 버튼 옆에 0.8s 동안 `✓ 복사됨` 표시 후 fade.

**파일**:
- `recording_service/web/index.html` — 두 카드의 헤더 마크업
- `recording_service/web/app.js` — `_copyToClipboard(elemId)` 헬퍼 + 핸들러
- `recording_service/web/style.css` — `.toast-inline` 스타일

**회귀**: clipboard API 는 jsdom/headless 검증 어려움 — 수동 확인. 단,
DOM 셀렉터가 안 깨졌는지 확인하는 단순 컴포넌트 스모크 정도는 가능.
본 항목은 수동 검증으로 합리화.

---

### 항목 5 (P1) — Step 결과 시각화 ★★★

**현재 갭**: Play with LLM 후 `run_log.jsonl` 에 step 별 status / heal_stage
가 다 있는데 UI 에 안 노출. 운영자는 텍스트 stdout tail 만 봐야 함.
스크린샷 (`step_<n>_<status>.png`) 도 디스크에 있지만 UI 진입 경로 없음.

**변경**:

**(5-A) 신규 endpoint** — `GET /recording/sessions/{sid}/run-log`
- `<session>/run_log.jsonl` 을 파싱해 list[dict] 반환.
- 파일 없으면 404.
- 응답 구조:
  ```json
  [
    {"step": 1, "action": "navigate", "target": "...", "value": "...",
     "description": "...", "status": "PASS", "heal_stage": "none",
     "ts": 1777435771.23, "screenshot": "step_1_pass.png"},
    ...
  ]
  ```
  `screenshot` 필드는 `step_<n>_<status>.png` 가 디스크에 있을 때만 채움.

**(5-B) 신규 endpoint** — `GET /recording/sessions/{sid}/screenshot/{name}`
- 보안: `name` 은 `step_<digit>_<word>.png` 정규식 화이트리스트, path traversal 방지.
- 스크린샷 PNG 직접 서빙 (`FileResponse`).

**(5-C) 결과 패널 신규 섹션** — `<section class="card" id="run-log-card">`
- 위치: 기존 `#scenario-card` 다음.
- `state=done` 이고 `run_log.jsonl` 존재 시에만 노출.
- 표 형식:
  ```
  | step | action | target | status | heal_stage | screenshot |
  |  1   | navigate | ... | PASS  | none      | [📷]       |
  |  2   | click   | role=link, name=연혁 | HEALED | local | [📷] |
  ...
  ```
- `status` 컬럼: `PASS` 녹색 / `FAIL` 빨강 / `HEALED` 노랑. CSS pill.
- `heal_stage` 컬럼: `none` 회색 / `local` 노랑 / `dify` 주황 / `visibility` 파랑.
- 스크린샷 셀: 클릭 시 모달 확대 (간단 `<dialog>` + `<img>`).
- FAIL step 은 자동 스크롤 + 강조.

**파일**:
- `recording_service/server.py` — 두 신규 endpoint
- `recording_service/web/index.html` — 신규 카드 + 모달
- `recording_service/web/app.js` — 표 렌더링 + 스크린샷 모달
- `recording_service/web/style.css` — pill 색상 + 모달

**회귀**:
- `test_get_run_log_returns_parsed_steps` — fixture run_log.jsonl 만들고 endpoint 호출
- `test_get_run_log_404_when_no_log_file`
- `test_screenshot_endpoint_serves_png`
- `test_screenshot_endpoint_rejects_path_traversal` — `..` 시도, `name` 정규식
- `test_screenshot_endpoint_rejects_arbitrary_filename` — 정규식 외 이름

---

### 항목 8 (P4) — Step 단위 JSON 복사

**현재**: scenario.json 전체를 통째로만 복사 가능.

**변경**:
- 항목 5 의 표 각 행 끝 컬럼에 📋 버튼.
- 클릭 시 해당 step 의 JSON 객체 (run_log 형식 그대로) 를 클립보드에 복사.
- 항목 2 의 `_copyToClipboard()` 헬퍼 재사용.

**파일**:
- `recording_service/web/app.js` — 표 행 렌더링에 버튼 + 핸들러

**회귀**: 항목 2 와 동일 — 수동 확인.

---

### 항목 6 (P2) — Play 진행 로그 스트리밍

**현재 갭**: `▶ Play with LLM` 클릭 후 30~60s 동안 정적 스피너만. 사용자가
멈춘 줄 알고 다시 누르거나 새로고침하는 케이스 발생.

**설계 결정**: subprocess 실행을 비동기로 바꾸지 않음. 대신 `play-llm.log`
가 이미 디스크에 실시간으로 쓰여지므로 이를 tail polling.

**변경**:

**(6-A) 신규 endpoint** — `GET /experimental/sessions/{sid}/play-log/tail?from=<offset>`
- `<session>/play-llm.log` 또는 `play-codegen.log` 의 `from` 바이트 이후 내용 반환.
- 응답: `{"content": "...", "offset": <new_offset>, "exists": true}`.
- 파일 없으면 `{"content": "", "offset": 0, "exists": false}` (200) — 폴링 중에는
  파일이 늦게 생길 수 있으므로 404 가 아닌 200 + exists=false 로 신호.
- 어떤 로그를 tail 할지: `?kind=llm|codegen` 쿼리 (default `llm`).

**(6-B) 프론트엔드 스트리밍**
- `▶ Play with LLM` / `▶ Codegen Output Replay` 핸들러 변경:
  ```js
  // 1. POST /play-llm 비동기로 시작
  const playPromise = api(`/experimental/sessions/${sid}/play-llm`, {method: "POST"});
  // 2. 동시에 1s 마다 tail polling 시작
  let offset = 0;
  const tailTimer = setInterval(async () => {
    const t = await api(`/experimental/sessions/${sid}/play-log/tail?from=${offset}&kind=llm`);
    if (t.content) appendToProgressBox(t.content);
    offset = t.offset;
  }, 1000);
  // 3. play 완료 시 polling 중단 + 최종 결과 표시
  const result = await playPromise;
  clearInterval(tailTimer);
  ```
- 진행 박스: `<pre class="play-progress">` — 결과 패널 안. 자동 스크롤 (각 append 후
  `scrollTop = scrollHeight`).
- 완료 후: 최종 결과 (rc / elapsed / step PASS/FAIL 카운트) 와 함께 진행 박스
  를 collapsible 섹션으로 보존.

**파일**:
- `recording_service/server.py` — 신규 endpoint (rplus router 가 아닌 메인 — 모든 play 모드에서 공용)
- `recording_service/web/app.js` — `_runPlay()` 함수에 tail 로직 추가
- `recording_service/web/index.html` — 진행 박스 마크업
- `recording_service/web/style.css` — `.play-progress` 스타일 (검정 배경 + 모노 폰트)

**회귀**:
- `test_play_log_tail_returns_new_bytes_only` — fixture 로 .log 만들고 from offset 이동 검증
- `test_play_log_tail_when_file_absent` — 200 + exists=false
- `test_play_log_tail_kind_query` — llm vs codegen 분기

---

### 항목 3 — R-Plus 호버펼침 + 클릭 고정 그룹

**현재**: 4 버튼 flat —
```
[▶ Codegen Output Replay] [▶ Play with LLM] [📝 Generate Doc] [⚖ Compare with Doc-DSL]
```

**변경**: 2 그룹 dropdown —
```
[▶ Play ▾]   [📝 Generate Doc ▾]
   ├─ ▶ Codegen 녹화코드 실행          ├─ 📝 시나리오 문서 생성 (Generate Doc)
   └─ ▶ LLM 적용 코드 실행             └─ ⚖ 시나리오 문서 ↔ JSON 비교 (Compare)
```

**상호작용 — (b) hover-open + click-stick**:
- `:hover` 로 펼침 (CSS only) — 마우스만 올려도 즉시.
- 클릭 시 펼친 상태 고정 (`.expanded` 클래스 토글). 외부 클릭 / ESC 로 닫힘.
- 모바일 / 키보드: 그룹 버튼 자체에 `aria-haspopup="true"` + `aria-expanded` 동기.

**구조**:
```html
<div class="dropdown-group" data-group="play">
  <button class="dropdown-toggle">▶ Play <span class="caret">▾</span></button>
  <div class="dropdown-menu">
    <button id="btn-play-codegen">▶ Codegen 녹화코드 실행</button>
    <button id="btn-play-llm">▶ LLM 적용 코드 실행</button>
  </div>
</div>
<div class="dropdown-group" data-group="doc">
  <button class="dropdown-toggle">📝 Generate Doc <span class="caret">▾</span></button>
  <div class="dropdown-menu">
    <button id="btn-enrich">📝 시나리오 문서 생성</button>
    <button id="btn-compare-open">⚖ 시나리오 문서 ↔ JSON 비교</button>
  </div>
</div>
```

CSS:
```css
.dropdown-group { position: relative; display: inline-block; }
.dropdown-menu { display: none; position: absolute; ... }
.dropdown-group:hover .dropdown-menu,
.dropdown-group.expanded .dropdown-menu { display: block; }
```

JS:
- 클릭 시 `expanded` 토글 + 다른 그룹 닫기.
- `document.addEventListener("click", outside-close)`.
- ESC 핸들러.

**파일**:
- `recording_service/web/index.html` — R-Plus 섹션 마크업 교체
- `recording_service/web/app.js` — dropdown 핸들러 + 기존 `btn-play-*` ID 유지 (회귀 0)
- `recording_service/web/style.css` — `.dropdown-*` 스타일

**회귀**: 시각 동작 — 수동 확인. 단, 기존 4 버튼의 핸들러는 ID 가 그대로
유지되므로 기존 테스트에는 영향 없음.

---

### 항목 4 — LLM healed ↔ codegen 원본 비교 + regression .py 다운로드 ★★

**의도** (사용자 명세):
> LLM 힐링을 거쳐 의도에 맞게 동작하는 스크립트를 원본과 비교한 후, 문제없다
> 판단될 경우 바로 다운로드해서 리그레션 테스트에 활용.

**비교 대상**:
- LEFT: `original.py` (codegen 원본)
- RIGHT: `regression_test.py` (executor 가 `scenario.healed.json` 으로부터 자동 생성한 .py — `zero_touch_qa.regression_generator`)

`regression_test.py` 는 이미 매 Play with LLM 실행 후 자동 생성됨 (이전 로그
"`Regression] 독립 테스트 생성 완료`" 확인). 본 항목은 이 산출물을 노출 + 비교
+ 다운로드.

**변경**:

**(4-A) 신규 endpoint** — `GET /recording/sessions/{sid}/regression`
- `<session>/regression_test.py` 본문 반환. `?download=1` 시 `Content-Disposition: attachment`.
- 기존 `/original` endpoint 와 같은 패턴.

**(4-B) 신규 endpoint** — `GET /experimental/sessions/{sid}/diff-codegen-vs-llm`
- 두 파일을 읽어 unified diff 계산:
  ```python
  import difflib
  diff = "".join(difflib.unified_diff(
      orig.splitlines(keepends=True),
      regr.splitlines(keepends=True),
      fromfile="original.py (codegen)",
      tofile="regression_test.py (LLM healed)",
      n=3,
  ))
  ```
- 응답:
  ```json
  {
    "left_path": "original.py",
    "right_path": "regression_test.py",
    "left_content": "...",
    "right_content": "...",
    "unified_diff": "...",
    "left_exists": true,
    "right_exists": true
  }
  ```
- 한쪽 파일 없으면 해당 쪽 `_exists=false` + content `""` (200). 양쪽 다 없으면 404.
- `/experimental/` prefix 인 이유: Play with LLM 후에만 의미 있는 비교 (R-Plus 트랙).

**(4-C) 결과 패널 신규 섹션** — `<section class="card" id="diff-card">`
- 위치: 항목 5 의 `#run-log-card` 다음.
- 헤더: `Original vs Regression (LLM healed)` + `⬇ regression_test.py 다운로드` 링크.
- 본문: unified diff `<pre>` 렌더 + 라인별 색상:
  - `+` 시작 → 녹색 배경 (LLM healed 에서 추가)
  - `-` 시작 → 빨강 배경 (원본에 있던 것 변경/제거)
  - `@@` 시작 → 파랑 배경 (hunk header)
- 토글: `[unified] [side-by-side]` 라디오 — 사이드바이사이드 모드는 difflib.HtmlDiff 결과 inject.
- 문제 없다고 판단되면 다운로드 버튼 클릭 → regression .py 저장 → 사용자가 회귀 슈트로 옮김.

**파일**:
- `recording_service/server.py` — `/regression` endpoint
- `recording_service/rplus/router.py` — `/diff-codegen-vs-llm` endpoint
- `recording_service/web/index.html` — diff 카드
- `recording_service/web/app.js` — diff fetch + 렌더 + 토글
- `recording_service/web/style.css` — diff 라인 색상

**회귀**:
- `test_get_regression_returns_python_text` — fixture regression_test.py 만들고 GET
- `test_get_regression_with_download_query_sets_attachment_header`
- `test_get_regression_404_when_missing`
- `test_diff_endpoint_returns_unified_diff_when_both_files_exist`
- `test_diff_endpoint_returns_404_when_neither_exists`
- `test_diff_endpoint_partial_when_only_one_exists` — 한쪽만 있을 때 동작

---

### 항목 7 (P3) — 세션 목록 검색/필터

**현재**: `GET /recording/sessions` 가 전체 반환, 프론트는 그냥 표시.

**변경**:
- 백엔드 변경 없음 — 클라이언트 측 필터.
- 세션 목록 카드 헤더에 input + select 추가:
  ```html
  <input id="session-filter" placeholder="target_url / id 필터">
  <select id="session-state-filter">
    <option value="">모든 state</option>
    <option value="recording">recording</option>
    <option value="converting">converting</option>
    <option value="done">done</option>
    <option value="error">error</option>
  </select>
  ```
- JS: `input` / `change` 이벤트로 표 행 hide/show. localStorage 에 마지막 필터 보존.

**파일**:
- `recording_service/web/index.html` — 필터 input
- `recording_service/web/app.js` — 필터 로직 + 영속화
- `recording_service/web/style.css` — 필터 영역 스타일

**회귀**: 클라이언트 측만 변경 — 수동 확인.

---

## 회귀 테스트 전략

| 영역 | 추가 테스트 | 누적 |
|---|---|---|
| 항목 5 (run-log + screenshot) | 5건 | 5 |
| 항목 6 (play-log tail) | 3건 | 8 |
| 항목 4 (regression + diff) | 6건 | 14 |
| **총합** | | **+14건** |

기존 `test_recording_service.py` 90 → 104 passed 목표.

UI 변경 (항목 1, 2, 3, 7, 8) 은 jsdom/Playwright e2e 없이는 자동 검증 어려움
— 수동 smoke 후 commit. 단, 변경한 마크업 ID 가 기존 JS 핸들러와 일치하는지
는 코드 리뷰로 확인.

## 데몬 재시작 시점

각 항목 그룹 완료 후 1회 재시작 + 브라우저 hard refresh:

```
1, 2 완료 후     → 재시작 1회 (정적 변경만 — 사실상 reload 만으로 OK)
5, 8 완료 후     → 재시작 (endpoint 추가)
6 완료 후        → 재시작
3, 7 완료 후     → reload 만 (정적 변경)
4 완료 후        → 재시작 (endpoint 추가)
최종            → 재시작 + 전체 회귀
```

브라우저: `Cmd+Shift+R` (hard refresh) 가 모든 변경 후 필수.

## 위험과 회피

| 위험 | 회피 |
|---|---|
| 기존 `btn-play-*` ID 변경 시 회귀 깨짐 | ID 유지 — dropdown 안에 그대로 둠 |
| `/screenshot/{name}` path traversal | 정규식 `^step_\d+_\w+\.png$` 화이트리스트 |
| `play-log/tail` 폴링이 데몬 부하 | 1s 인터벌 + 완료 시 즉시 중단 |
| 큰 run_log.jsonl (1000+ step) 표 렌더 느림 | 가상 스크롤 미적용 — 현재 시나리오 평균 5~30 step |
| difflib HtmlDiff 의 출력 HTML XSS | `escape=True` (default) + iframe 격리 불필요 (자체 HTML 만 렌더) |

---

## 완료 기록 (2026-04-29)

**모든 8 항목 + follow-up 3건 완료**. 회귀 +17 (90 → 107 passed in test_recording_service).

### 1단계 — 8 항목 1차 구현

| 항목 | 결과 | 비고 |
|---|---|---|
| 1 — Jenkins 새 탭 | ⚠ 우회 | Safe HTML 정책이 `target` 속성 strip → 동일 탭 진입 (사이드바 링크 그대로). 사용자 안내 문구만 갱신 |
| 2 — 클립보드 복사 | ✅ | `_copyToClipboard()` + `.copy-btn` 토스트. `navigator.clipboard.writeText()` |
| 3 — R-Plus dropdown (b) | ✅ | hover-open + click-stick. CSS-only hover + JS toggle. ESC/외부클릭 닫힘 |
| 4 — codegen ↔ LLM diff | ✅ → **LLM 분석으로 격상** (follow-up #3) |
| 5 (P1) — Step 결과 시각화 | ✅ | `/run-log` + `/screenshot/{name}` (path traversal 방어). status/heal_stage pill + 모달 |
| 6 (P2) — Play 진행 스트리밍 | ✅ | `/play-log/tail?from=&kind=` polling (1s). play-llm.log / play-codegen.log 분기 |
| 7 (P3) — 세션 검색/필터 | ✅ | 클라이언트 측 필터. localStorage 영속. id/target_url + state |
| 8 (P4) — Step JSON 복사 | ✅ | run-log 표 행마다 📋 |

### 2단계 — Follow-up 3건 (1차 운영 피드백)

#### F1 — Jenkins 새 탭 진입 → 뒤로가기 버튼으로 대체
운영 검증: Jenkins antisamy-markup-formatter (Safe HTML) 가 `<a target="_blank">` 의
`target` / `rel` 속성을 보안상 strip — provision.sh 의 explicit `target="_blank"`
가 무력. sidebar-link plugin 의 `LinkAction` 클래스도 `target` 필드 미지원.

→ **Recording UI 자체에 좌측 상단 `← 뒤로` 버튼** 추가. `document.referrer` →
`history.back()` → Jenkins 메인 fallback. 항상 노출 (조건부 hidden 시 사용자가
혼란).

| 변경 | 위치 |
|---|---|
| `← 뒤로` 버튼 마크업 | `web/index.html` 헤더 좌측 |
| 핸들러 (referrer / history.back / fallback) | `web/app.js` `_initBackButton()` |
| 페이지 명칭 통일 (`Recording Service` → **`Recording UI`**) | `web/index.html` `<title>` / `<h1>` / footer |
| description 안내 문구 갱신 | `provision.sh` (`target="_blank"` 제거 + 뒤로버튼 안내) |

#### F2 — Regression Test Script (.py) 별도 카드 분리
1차 구현은 비교 (diff) 카드 안에서 다운로드 링크만 노출. 사용자 피드백 — Scenario
JSON / Original Script 와 동일한 패턴으로 **별도 섹션** 으로 보여줘야 일관성.

| 변경 | 위치 |
|---|---|
| `#regression-card` 신규 (📋 복사 + ⬇ 다운로드 + 코드 미리보기) | `web/index.html` |
| `_renderRegression(sid)` — 진입 시 자동 fetch | `web/app.js` |

#### F3 — 1차원 unified diff → LLM 의미 분석
1차 구현은 `difflib.unified_diff` 텍스트만 색상 코딩. 사용자 피드백 — selector
swap 의도, 위험 평가, 회귀 채택 권고 같은 **의미 정보** 가 더 유용.

LLM (Ollama) 호출로 4 섹션 markdown 생성:
1. 핵심 변경 요약
2. 변경 라인 분석 (selector swap / hover 추가 / 등)
3. 위험 평가 (결정성 / 의도 일치 / 잠재 리스크)
4. 회귀 채택 권고 (✅ / ⚠ / ❌)

| 변경 | 위치 |
|---|---|
| `analyze_codegen_vs_regression()` + `DiffAnalysisResult` | `recording_service/enricher.py` |
| `POST /experimental/sessions/{sid}/diff-analysis` | `recording_service/rplus/router.py` |
| `_run_diff_analysis_impl` monkeypatch hook | 위 모듈 (테스트 격리용) |
| 비교 카드에 `🔎 LLM 분석` 버튼 + markdown 렌더러 | `web/index.html` + `web/app.js` |
| 원시 diff 는 `<details>` collapsible 로 secondary | `web/index.html` |

POST 인 이유: Ollama 호출이 30~60s 부수효과 → GET 캐싱 의미론과 충돌.

### 신규 backend endpoint — 6건

```
GET  /recording/sessions/{sid}/run-log              (P1)
GET  /recording/sessions/{sid}/screenshot/{name}    (P1, path-traversal 방어)
GET  /recording/sessions/{sid}/play-log/tail        (P2, ?from=&kind=)
GET  /recording/sessions/{sid}/regression           (항목 4, ?download=1)
GET  /experimental/sessions/{sid}/diff-codegen-vs-llm  (항목 4 1차 — 원시 diff)
POST /experimental/sessions/{sid}/diff-analysis     (항목 4 follow-up — LLM 분석)
```

### 회귀 결과

| 영역 | 추가 | 누적 |
|---|---|---|
| run-log + screenshot | 5 | 95 |
| play-log/tail | 3 | 98 |
| regression .py | 3 | 101 |
| diff endpoint | 3 | 104 |
| diff-analysis (LLM hook) | 3 | **107** |

기존 90 건 모두 유지, 0 회귀.

### 의도된 비범위 — 후속 검토 후보

- 다크 모드 / 모바일 레이아웃
- `<a target="_blank">` 우회를 위한 antisamy 커스텀 정책 (위험: 전역 description 영향)
- LLM 분석 캐싱 (POST 결과를 `<session>/diff-analysis.md` 에 저장 → 재호출 시 fast path)
- 분석 결과의 `회귀 채택 권고` 가 ❌ 일 때 다운로드 버튼 disabled 처리
- run-log 의 step 단위 step JSON 복사 외 "이 step 만 분리 실행" 기능

## 변경 이력

| 일자 | 작성자 | 내용 |
| --- | --- | --- |
| 2026-04-29 | Claude (feat/grounding-recording-agent) | 초안 — 8 항목 + 의존성 + 회귀 전략 |
| 2026-04-29 | Claude (feat/grounding-recording-agent) | **8 항목 + follow-up 3건 완료**. 회귀 90 → 107. follow-up: F1 뒤로가기 버튼 (Safe HTML 우회), F2 regression .py 별도 카드, F3 LLM 의미 분석 |
