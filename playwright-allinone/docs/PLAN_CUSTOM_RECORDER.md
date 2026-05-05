# PLAN — Custom Recorder (R-Phase) — codegen 의존 제거

브랜치: 본 PLAN 은 설계 단계. 단기 hotfix
([`PLAN_TITLE_DIFY_SCROLL_FIXES.md`](PLAN_TITLE_DIFY_SCROLL_FIXES.md)) 와 분리
되어 별도 라운드로 진행.

## 동기 — 왜 codegen 을 떠나는가

현재 녹화 → 시나리오 파이프라인은 3 단계 lossy 변환:

```
[user 행동]
    ↓ (1) playwright codegen
[original.py]   — wheel/scroll, get_by_title 등 silent drop
    ↓ (2) AST 변환 (zero_touch_qa.converter_ast)
[scenario.json] — codegen 미지원 메서드 추가 silent drop
    ↓ (3) executor — 실행 시점에 healing
[실행 결과]
```

(1) 의 lossy 동작은 외부 의존이라 통제 불가. Playwright 1.57.0 기준:

- `playwright codegen --help` 에 `--save-trace` 부재.
- Playwright Python `BrowserContext` 에 recorder 제어 hook 미노출
  (`_har_recorders` 만).
- codegen 은 외부 init_script 주입 미지원.

→ codegen 의 lossy 동작에 hook 을 거는 모든 경로가 막혀 있음. 미래에도
새로운 codegen 미지원 메서드/이벤트가 등장할 때마다 본 프로젝트에 silent drop
이 누적됨. 본 사례 (`get_by_title` 누락 + wheel 누락) 는 그 패턴의 한 인스턴스.

근본 해결 = **(1) 단계 자체를 우리가 통제하는 recorder 로 교체**.

## 설계 — 자체 Recorder 의 핵심

### 데이터 흐름

```
[user 행동]
    ↓ chromium (Playwright Python 으로 직접 launch)
    ↓ context.add_init_script — DOM 이벤트 listener 주입
    ↓ context.expose_function — JS → backend 실시간 전송
[event timeline (RAW)]
    ↓ selector candidate ranker (backend)
    ↓ action timeline → step list 압축 (예: 연속 input 이벤트 → 단일 fill)
[scenario.json — 직접 생성, AST 단계 없음]
```

### in-page JS 책임

- 이벤트 리스너:
  - `click` — `event.composedPath()` 로 closest interactive ancestor 식별
  - `input` / `change` — debounce 후 최종 값
  - `wheel` — 스크롤 방향/거리 시작/끝 (이벤트 다발 → 압축)
  - `scroll` — 컨테이너의 viewport 진입 트리거
  - `keydown` — 단축키 / Enter / Tab 등 의미 키
  - `mouseover` / `mouseenter` (옵션) — hover-only 메뉴 시그널
  - `submit` — form submission (별도 click 으로 보강될 수 있음)
- iframe / shadow DOM — 각 frame 별 init_script 자동 부착.
- 셀렉터 후보 산출 (각 element 마다 N개):
  - `role` + accessible name (Playwright 호환 알고리즘)
  - `text` (visible text trim)
  - `label` (associated label of input)
  - `placeholder`
  - `testid` (data-testid)
  - `title`
  - `alt` (img)
  - 짧은 CSS path (1차 휴리스틱)
  - XPath (last resort)

### backend 책임

- `expose_function` 핸들러 — 이벤트 timeline 누적 (sidecar JSONL 도 같이).
- 셀렉터 ranking — 후보 중 가장 *고유성 + 안정성* 높은 것을 선택.
  점수 함수는 codegen 의 휴리스틱을 부분 차용 (role+name > testid > label >
  text > placeholder > title > alt > css > xpath).
- 액션 통합:
  - 연속 wheel → 단일 scroll step (`into_view` 또는 거리 기반).
  - 연속 input → 단일 fill (final value).
  - click + 직후 navigation → click step 만 (navigation 은 부산물).
  - hover → click 이 같은 element 면 click 만 (hover 자동 시도).
- 시나리오 빌더 — `step / action / target / value / description /
  fallback_targets` 형식으로 직접 emit.

### Recorder UI

codegen Inspector 동등 기능:

- 별도 control window (Playwright 의 popup window) 또는 사이드 패널.
- 실시간 시나리오 미리보기.
- "Stop" / "Pause" / "Discard" 버튼.
- 셀렉터 hover hint (codegen 의 element pick 모드 동등).

1차 R-1 에선 미니멀 — control 은 사용자가 브라우저 닫는 것으로 종료. 본격 UI
는 R-3 이후.

## 단계 (R-1 ~ R-4)

### R-1 — Recorder MVP (codegen 과 병행)

**목표**: 기본 액션 (click / fill / press / goto) 의 캡처 fidelity 가 codegen
과 동등 + 자체 시나리오 직접 emit.

**범위**:

- `recording_service/recorder/` 신규 모듈
  - `runner.py` — chromium launch + init_script 부착 + 종료 시 시나리오 저장
  - `inpage.js` — DOM listener + selector 후보 산출 (in-page 코드)
  - `selector_ranker.py` — 후보 ranking
  - `timeline_builder.py` — event timeline → scenario.json
- 기존 codegen 파이프라인은 그대로. 새 recorder 는 **feature flag**
  (`RECORDER_BACKEND=custom`) 로 선택.
- UI 에 recorder backend 선택 토글 추가.

**검증**:

- 기존 사이트 5종 (예: 로그인 페이지, dashboard, list+detail) 에서 codegen
  vs custom 의 시나리오 비교. step 동등성 보고.
- 본 사례 (`get_by_title` 클릭 + 메뉴 전개 후 link 클릭) — custom recorder 가
  처음부터 정확한 step 으로 캡처.

### R-2 — wheel / scroll / hover / drag 지원

**목표**: 단기 hotfix 의 (C0b) manual scroll 추가 의존 제거.

**범위**:

- in-page JS 에 wheel / scroll / hover / dragstart-dragend 리스너 추가.
- timeline_builder 에 압축 규칙 (연속 wheel → 단일 scroll step) 추가.
- 회귀 — 기존 R-1 시나리오 동등성 유지.

**검증**:

- 무한 스크롤 페이지에서 사용자 wheel → scenario 에 scroll step 자동 포함.
- hover-only 메뉴 (GNB) → hover step 자동 포함.

### R-3 — iframe / popup parity + Recorder UI

**목표**: codegen 의 iframe / popup 추적 동등 + 자체 control UI.

**범위**:

- `BrowserContext.on('page')` / `Page.on('frameattached')` 로 새 frame 마다
  init_script 자동 부착.
- popup chain 의 `page1`, `page2` 변수 동등 추적.
- control window — 별도 popup 으로 시나리오 미리보기 + Stop/Pause/Discard.

**검증**:

- 본 사례 같은 popup 다단계 시나리오 — codegen 동등 캡처.
- shadow DOM 호스트 element 클릭 — `shadow=` 또는 적절한 셀렉터 emit.

### R-4 — Cutover + codegen fallback

**목표**: 자체 recorder 가 default. codegen 은 명시적 fallback 으로만.

**범위**:

- 단기 hotfix 의 (A) `get_by_title` 변환 분기 deprecation 검토 — custom
  recorder 가 처음부터 정확한 selector 를 emit 한다면 AST 변환 자체 단순화.
- 단기 hotfix 의 (C0b) manual scroll API — UI 에서 hide (백엔드는 호환 위해
  유지).
- 운영 매뉴얼 — codegen → custom recorder 마이그레이션 가이드.

**검증**:

- 기존 모든 회귀 시나리오 통과.
- 성능: codegen 대비 녹화 latency / 시나리오 정확도 비교.

## 위험 / 미정

- **셀렉터 품질**: codegen 의 selector 휴리스틱은 수년 다듬어진 자산.
  초기 R-1 단계에선 codegen 보다 부정확할 수 있음 → feature flag 로 운영자가
  선택할 수 있게.
- **edge case 누적**: contenteditable / canvas / SVG / Web Components / 
  custom focus traps. 발견될 때마다 in-page JS 에 핸들러 추가.
- **Playwright 업그레이드**: codegen 의존 제거가 끝나면 Playwright 의 recorder
  관련 변경에 무관해지지만, in-page event API 변화 (예: composedPath 동작)는
  여전히 영향. 정기 회귀 필요.
- **공수**: R-1 ~ R-4 합산 1~2 주 (단일 개발자 기준 추정). 단기 hotfix 가
  ship 된 상태라 시급성은 완화됨.

## 비범위

- 모바일 (touch / pointerdown 등) — 본 프로젝트 1차 범위 밖.
- AI 기반 selector 추론 — 결정성 우선, ML 도입은 별도 PLAN.
- 원격 녹화 (서버 측 chromium) — 1차 범위는 호스트 chromium 직접 실행.

## 단기 hotfix 와의 연결

| hotfix 항목 | R-Phase 후 운명 |
| --- | --- |
| (A) `get_by_title` AST 분기 | R-4 에서 deprecation 검토 — custom recorder 가 직접 정확한 selector emit |
| (B) Dify 진단 메시지 | 무관 — 그대로 유지 |
| (C0b) manual scroll API + UI | R-2 완료 후 UI 에서 hide. 백엔드 API 는 호환 유지 |

R-Phase 진행 도중에도 단기 hotfix 는 운영 환경에서 계속 가치를 가짐.
