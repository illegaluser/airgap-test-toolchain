# 녹화 누락 동작의 자동 복원 구조

Playwright `codegen` 으로 녹화한 시나리오가 hover 같은 비명시 동작을 놓치는 문제를, 실행 시점에 자동으로 메우는 구조를 정리한 문서.

---

## Part 1. 개념 — 무엇을, 왜, 어떻게 메우는가

### 1.1 문제: 녹화기는 "마우스 올림" 을 기억하지 못한다

테스트 시나리오는 Playwright **녹화기(codegen)** 로 사람의 조작을 따라 그려서 만든다. 그런데 녹화기는 **클릭·입력·키 누름처럼 "딱 떨어지는 동작"** 만 기록하고, 다음 행동은 받아 적지 못한다.

- 메뉴 위에 마우스를 **올려놓는** 동작 (hover)
- 그 hover 로 **펼쳐진 서브메뉴** 를 클릭하는 흐름
- "회사소개 → 회사연혁 → 2013년" 처럼 **여러 단계로 펼쳐지는** 메뉴

그래서 녹화된 스크립트에는 보통 **마지막 클릭 한 번** 만 남는다. 이 스크립트를 그대로 재생하면, 클릭 대상이 아직 **화면에 보이지 않는 상태** 여서 "요소를 찾을 수 없다" 는 오류로 실패한다.

### 1.2 해법: 실행 중에 두 명의 "보조원" 이 빈틈을 채운다

테스트를 실제로 돌릴 때, 클릭 시도 **직전에** 자동 보조 단계가 끼어들어 사람 흉내를 낸다. 보조원은 두 명이다.

**보조원 A — 규칙 기반 보조원**
사람이 메뉴를 찾을 때의 행동을 순서대로 흉내낸다.

1. 화면 밖이면 **스크롤로 끌어온다.**
2. **부모 메뉴들을 바깥쪽부터 안쪽으로 차례차례 마우스로 가리킨다.** 다단 메뉴는 바깥부터 펼쳐야 다음 단계가 나타나기 때문.
3. 그래도 안 되면 **헤더·내비게이션 영역을 가볍게 hover** 해서 사이트 전체에 "사용자가 들어왔다" 는 신호를 준다.
4. 메뉴가 애니메이션으로 천천히 펼쳐지는 경우를 위해 **최대 2초 기다린다.**
5. 같은 이름의 항목이 여러 개라면 **눈에 보이는 다른 항목으로 대상을 바꾼다.**

이 보조원은 "어떤 요소가 메뉴인지" 를 화면의 여러 단서(접근성 속성, 역할 표시, 태그, CSS 의 `:hover` 규칙 등)로 자동 판별한다. 평소 잘 보이는 요소에는 비용이 거의 0 이고, 문제가 있는 순간에만 작동한다.

**보조원 B — AI(LLM) 보조원**
보조원 A 가 다섯 단계를 다 써도 못 살리면 AI 가 등판한다. AI 에게는 "무엇을 찾으려다 실패했는지", "지금 화면 구조가 어떤지", "직전에 어떤 시도들을 어떻게 실패했는지" 를 함께 전달한다. AI 는 이 맥락을 보고 **새 대상이나 조건** 을 제안한다. 다만 AI 가 동작 자체(예: 클릭을 입력으로)를 멋대로 바꾸면 가짜 성공이 날 수 있으므로, **미리 허용된 변경만** 받아들인다.

### 1.3 사례

녹화 결과에 `[2013년 메뉴 클릭]` 한 줄만 남았다고 하자.

```
[클릭] "2013년" 메뉴
  ↓ 화면에 안 보임
  ↓ 보조원 A 출동
     - 화면 안으로 끌어오기 → 여전히 안 보임
     - 부모 메뉴를 바깥부터 차례로 hover
        · "회사소개" hover  → "회사연혁" 펼쳐짐
        · "회사연혁" hover  → "2013년" 펼쳐짐 ✓
  ↓ "2013년" 클릭 진행
[성공]
```

A 가 실패하면 B(AI) 가 새 단서를 제안하고, 그 결과로 한 번 더 시도한다.

### 1.4 한 줄 요약

> **녹화기가 hover 를 못 적는 약점을, 실행 중에 시스템이 사람 흉내로 자동 보완한다.** 1차는 규칙 기반, 2차는 AI. 평상시 비용은 0 이고 문제가 생긴 순간에만 작동한다.

---

## Part 2. 구현 — 어디에, 어떻게 들어가 있는가

위 두 보조원은 [zero_touch_qa/executor.py](../zero_touch_qa/executor.py) 의 단계별 치유 루프 안에 구현되어 있다.

### 2.1 전체 치유 루프

```
Step 실행
  ├─ (0) 1차 시도 직전: Visibility Healer (T-H)   ← 보조원 A
  ├─ (1) fallback_targets 순회                    [heal_stage=fallback]
  ├─ (2) action_alternatives (Planner 명시)       [heal_stage=alternative]
  ├─ (3) LocalHealer DOM 유사도 매칭              [heal_stage=local]
  ├─ (4) Dify LLM 치유                            [heal_stage=dify]   ← 보조원 B
  └─ (5) press(Enter) → 검색버튼 휴리스틱
```

Hover 누락 케이스는 **(0) 이 1차 방어, (4) 가 2차 방어** 를 맡는다.

### 2.2 보조원 A — Visibility Healer (T-H)

**위치**
- 본체: [executor.py:2045-2163](../zero_touch_qa/executor.py#L2045-L2163)
- 후보 추출 JS: [executor.py:96-179](../zero_touch_qa/executor.py#L96-L179)

**트리거 조건**
타깃 locator 의 `is_visible() == False` 일 때만 진입. 정상 visible 요소에는 비용 0.

**복원 5단계** (visible 즉시 단축, 전체 한도 ~6초)

| 단계 | 전략 | 대상 패턴 |
|---|---|---|
| (1) | `scroll_into_view_if_needed` | Intersection Observer 기반 lazy 메뉴 |
| **(2)** | **Cascade ancestor hover** — JS 로 조상 체인을 훑어 hover 후보를 추출, **outermost → innermost** 순으로 누적 hover | **다단 hover 메뉴 (codegen 누락 핵심 케이스)** |
| (3) | `<header>/<nav>/<main>/<body>` 순서로 hover | 사이트 전역 mousemove 로 GNB 가 lazy expand 되는 케이스 |
| (4) | `bounding_box` 가 0 → >0 될 때까지 200ms × 10 폴링 | CSS/JS 애니메이션 transition |
| (5) | `filter(visible=True).first` | 다중 매치 중 첫 매치가 hidden 인 경우 |

**Hoverable 후보 판별 규칙** (`_VISIBILITY_HEALER_JS`, 우선순위 순)

1. `aria-haspopup` 속성 존재
2. `aria-expanded="false"`
3. `role` ∈ {menu, menubar, listbox, tooltip, combobox}
4. 태그 ∈ {nav, details, summary}
5. `data-state="closed"`
6. `:hover` CSS 룰의 트리거 — 모든 stylesheet 의 `selectorText` 를 파싱해 `A:hover B` 패턴에서 `A` 를 추출, 현재 노드가 매칭되면 hoverable 로 인정

각 후보는 stable 한 `cssPath`(id 우선, 없으면 `nth-of-type` 체인)로 반환된다. leaf 에서 `<body>` 까지 최대 12 depth, 후보는 최대 5 단계까지 cascade.

**Cascade 동작 원리**
Playwright `hover()` 는 마우스를 요소 중앙으로 이동시킨다. 다음 hover 대상이 직전 hover 의 descendant 라면 ancestor 의 `:hover` 상태는 브라우저가 자동 유지하므로, **outermost 부터 차례로 hover 를 누적** 하는 것만으로 다단 메뉴가 풀린다 ([executor.py:2087-2120](../zero_touch_qa/executor.py#L2087-L2120)).

### 2.3 보조원 B — Dify LLM 치유

**위치**
- 호출부: [executor.py:630-690](../zero_touch_qa/executor.py#L630-L690)
- 클라이언트: [dify_client.py:305-349](../zero_touch_qa/dify_client.py#L305-L349)

**입력**
- 실패 메시지 (`요소 탐색/실행 실패: <원본 target>`)
- DOM 스냅샷 (`config.dom_snapshot_limit` 길이로 truncate)
- 실패한 step 원본
- **`strategy_trace`** — 직전 step 의 strategy chain 시도/실패 이력 ("selector 만 바꿔도 같은 timeout 이었다" 같은 정보를 LLM 에 컨텍스트로 주입)

**출력 적용 정책** ([executor.py:647-669](../zero_touch_qa/executor.py#L647-L669))

| 키 | 변경 허용 |
|---|---|
| `target` / `value` / `condition` / `fallback_targets` | 자유롭게 mutate |
| `action` | `_HEAL_ACTION_TRANSITIONS` 화이트리스트 전이만 (false-PASS 방지) |
| 그 외 | 무시 |

이 정책은 `dify-chatflow.yaml` 의 Healer 프롬프트와 1:1 동기화되어 있다.

### 2.4 런타임 흐름 — Hover 누락 케이스

녹화 raw step 이 `click <leaf-menu>` 하나뿐인 경우:

```
[Step N] click → leaf-menu
  └ is_visible? false
    └ Visibility Healer 진입
       (1) scroll_into_view → still hidden
       (2) JS evaluate → 후보 [#gnb, #gnb > li.depth1, #gnb > li.depth2]
           ├ hover #gnb            (outer)        → still hidden
           ├ hover #gnb > li.depth1               → depth2 visible
           └ hover #gnb > li.depth2               → leaf visible ✓
    └ leaf-menu visible 상태로 click 진행
[Step N] PASS (Visibility Healer 는 1차 시도 직전 보정 — heal_stage 기록 없음)
```

Visibility Healer 까지 실패하면 fallback → alternative → LocalHealer → Dify 순으로 진행되고, Dify 가 새 `target` 또는 `fallback_targets` 를 제안하면 그것으로 재시도한다.

### 2.5 정리

- **codegen 이 놓친 hover 와 그 부수 액션은 두 단계로 자동 복원된다.**
  - 결정론적 단계: Visibility Healer 가 DOM·CSS 시그널로 ancestor hover 체인을 추론·실행
  - 학습 기반 단계: 실패 시 Dify LLM 이 새 target/condition 제안
- 단순 selector 보정이 아니라 **액션 시퀀스 보강** 구조 — codegen 산출물에 없던 hover 가 런타임에 동적으로 끼워 넣어진다.
- 정상 케이스 비용은 visible 검사 1회로 사실상 0.
