# DOM Grounding · Recording · Browser Agent 통합 — 4트랙 구현 로드맵

---

## Context

`playwright-allinone/` 솔루션은 **기획문서 → 테스트 계획·시나리오 초안** 생성에는 우수하다. 그러나 **기획문서 → 실행 가능한 UI 자동화 테스트** 까지는 도달하지 못한다. 원인은 셀렉터 갭이다.

설계 문서(`PLAN_TEST_PLANNING_RAG.md` §1.2 lines 47-50) 가 이 한계를 명시적으로 인정하고 있다.

본 로드맵은 이 갭을 **두 축으로 동시에 공략한다**.

| 트랙 | 공략 방향 | 진행 |
|---|---|---|
| **Doc-based** | 기획문서로부터 자동 시나리오 생성 능력을 강화 | Phase 1 → 1.5 → 2 → 3 (순차) |
| **Recording** | 사용자 행동을 14-DSL 로 자동 변환 | Phase R (Phase 1 과 병행) |

두 트랙은 상호 보완한다.

- **Doc-based** 는 회귀·추적성을 담당한다.
- **Recording** 은 탐색적·복잡 시나리오를 빠르게 캡처한다.

두 산출물을 묶는 **결합 시나리오** 로 추가 가치가 발생한다.

- **Recording → Doc 역추정** — 자동 추적성 확보
- **Doc ↔ Recording 비교** — 기획-구현 갭 발견

각 단계는 독립적으로 가치를 제공한다. 단계 간 결정 게이트를 통과해야 다음 단계로 진입한다.

### 사전 조사 결과 요약

| 항목 | 현재 상태 |
|---|---|
| Dify 버전 | **1.13.3** (Plugin Daemon 0.5.3-local) |
| MCP 지원 | ❌ 없음 |
| 챗플로우 노드 타입 | `start / if-else / llm / answer / knowledge-retrieval` 5종 — 도구·에이전트 노드 부재 |
| 사용 모델 | `gemma4:26b` (도구 호출 신뢰성 미검증) |
| Node.js | 22 LTS 컨테이너 내 존재 (`Dockerfile:193-204`) |
| LLM 컨텍스트 | `OLLAMA_CONTEXT_SIZE=12288`, Planner output 8192 예약 → 입력 헤드룸 ~4K 토큰 |
| Executor 모델 | `zero_touch_qa/executor.py` per-test 호출 (데몬 아님) |
| 핵심 통합 지점 | `dify_client.py:206-273` `generate_scenario()` |

---

## 전체 목표

**기획문서·화면정의서만으로 운영 URL 에 대한 실행 가능 UI 자동화 테스트를 생성한다.**

단, 시각 회귀·부하 테스트·a11y 자동 검증은 본 로드맵 범위 밖이다.

### 단계별 가치 단계 (Value Ladder)

#### Doc-based 트랙

| 단계 | 도달 능력 | 셀렉터 정확도 (1차) | 사람 개입 |
|---|---|---|---|
| **현재** | fixture HTML 한정, 운영 URL 불가 | ~20-30% | 필수 (셀렉터 수정) |
| **Phase 1 후** | 정적·공개 페이지 운영 URL 가능 | ~75-90% | 부분적 |
| **Phase 2 후** | 인증 페이지 1종 end-to-end 가능 | 동일 | 거의 불필요 (탐색 범위 내) |
| **Phase 3 후** | 30종 페이지 운영 안정성 확보 | 동일 + 회복 | 예외 케이스만 |

#### Recording 트랙

| 단계 | 도달 능력 | 사용자 시간 비용 |
|---|---|---|
| **현재** | `playwright codegen` CLI 수동 실행 + 별도 변환 호출 | 5-10분 (학습 곡선 + CLI) |
| **Phase R 후** | 통합 Web UI 에서 클릭 한 번으로 녹화 → DSL 자동 산출 | 1-2분 (행동 시간만) |
| **Phase R + 결합 시나리오** | 녹화 → IEEE 829 역추정, 또는 doc-DSL 과 의미 비교 | 추가 30초 |

---

## Phase 1 — DOM Grounding

> **한 줄 요약**: Planner LLM 호출 직전에 실제 DOM 인벤토리를 주입해 셀렉터 추측을 관측 기반 선택으로 전환한다.

### 목표

Planner LLM 호출 *직전* 에 `target_url` 을 실제 Playwright 로 로딩한다. accessibility tree 와 인터랙티브 요소 인벤토리를 추출해 Planner 컨텍스트에 주입한다.

LLM 의 작업이 **"셀렉터 추측"** 에서 **"존재하는 요소 중 선택"** 으로 바뀐다.

### 범위

**IN**

- 정적·공개(비인증) 페이지의 DOM 인벤토리 추출
- Accessibility tree 기반 의미 셀렉터(role/name/text) 우선 후보 생성
- `dify_client.py` 에 사전처리 단계 통합
- Planner 시스템 프롬프트 보강 — "주어진 인벤토리에서만 선택"
- DOM 가지치기(pruning) 휴리스틱
- 토큰 예산 가드
- Feature flag 로 신·구 경로 동시 운영
- 효과 측정 메트릭

**OUT**

- 인증 후 페이지 → Phase 2
- 다단계 wizard·modal 내부 탐색 → Phase 2
- 다중턴 LLM 도구 호출 → Phase 2
- iframe·shadow DOM → Phase 3
- Dify UI 변경 → Phase 3

### 아키텍처

```
[변경 전]
dify_client.py:generate_scenario()
  ├─ inputs = {srs_text, target_url, api_docs, ...}
  └─ POST /v1/chat-messages (Dify Planner)
       └─ Planner LLM (셀렉터 추측)

[변경 후]
dify_client.py:generate_scenario()
  ├─ if dom_grounding_enabled and target_url:
  │     dom_inventory = grounding.fetch(target_url)
  │     inputs['dom_inventory'] = serialize(dom_inventory)
  ├─ inputs = {srs_text, target_url, api_docs, dom_inventory?, ...}
  └─ POST /v1/chat-messages
       └─ Planner LLM (인벤토리에서 선택)
```

### 신규 모듈

| 파일 | 역할 | 추정 LOC |
|---|---|---|
| `zero_touch_qa/grounding/__init__.py` | 진입점 | 30 |
| `zero_touch_qa/grounding/extractor.py` | Playwright 로드 + a11y 추출 | 120 |
| `zero_touch_qa/grounding/pruner.py` | 가지치기 룰 | 80 |
| `zero_touch_qa/grounding/serializer.py` | LLM 친화적 직렬화 | 60 |
| `zero_touch_qa/grounding/budget.py` | 토큰 예산 가드 | 40 |
| `tests/test_grounding_*.py` | 단위 테스트 | 200 |

### 상세 태스크

#### T1.1 — DOM 인벤토리 스키마 설계 (1일)

각 요소를 다음 구조로 표현한다.

```
{role, name, text, selector_hint, visible, enabled, position}
```

- 직렬화 형식은 **Markdown 권장** (LLM 컨텍스트 효율 +20% vs JSON)
- 샘플 페이지 5개로 골든 스펙 확정
- **산출물**: `docs/grounding-schema.md`

#### T1.2 — Playwright 추출기 구현 (2일)

- `extractor.fetch(url, options)` → `Inventory` 객체
- 내부적으로 `page.accessibility.snapshot()` + `page.locator('[role]').all()` 병합
- 각 요소의 `getByRole(...)` 호환 식별자 생성
- 타임아웃·`wait_until` 정책 (기본 `domcontentloaded`, 옵션 `networkidle`)
- User-Agent·viewport·locale 설정 가능
- **산출물**: `extractor.py` + 단위 테스트

#### T1.3 — 가지치기 휴리스틱 (2일)

**제외 규칙**

- `aria-hidden="true"`, `display:none`, `visibility:hidden`, `opacity:0`
- 뷰포트 밖 요소 (옵션)
- 텍스트 길이 > 100자 → 클리핑
- 중복 요소 (같은 role+name) → 첫 N개만

**포함 우선순위**

1. 인터랙티브 role: `button`, `link`, `textbox`, `combobox`, `checkbox`, `radio`, `tab`, `menuitem`, `option`, `searchbox`
2. heading·landmark (구조 컨텍스트)

페이지 사이즈별 적응형 정책 적용 — 소형은 모두, 대형은 인터랙티브만.

- **산출물**: `pruner.py` + pruning 룰 테스트

#### T1.4 — 토큰 예산 가드 (1일)

직렬화 후 토큰을 추정한다 (`tiktoken` 또는 char/4 근사).

한도 (기본 3000 토큰) 초과 시 단계별로 줄인다.

1. 비인터랙티브 요소 제거 (heading·landmark)
2. 가시성 외 요소 제거
3. 우선순위 낮은 인터랙티브 (option·menuitem) 제거
4. 여전히 초과 시 경고 로그 + truncate

- **산출물**: `budget.py` + 한도 테스트

#### T1.5 — `dify_client.py` 통합 (1일)

- `generate_scenario()` 시그니처에 `enable_grounding: bool = False` 추가
- Feature flag (`ENABLE_DOM_GROUNDING=1`) 로 기본값 토글
- 추출 실패 시 조용히 스킵 (기존 경로 유지) + 로그
- 추출 시간·토큰 수 메트릭 기록 (`llm_calls.jsonl` 확장)
- **산출물**: `dify_client.py` 패치

#### T1.6 — Planner 프롬프트 보강 (1일)

`dify-chatflow.yaml` Planner 시스템 프롬프트에 다음을 추가한다.

- "다음 DOM 인벤토리가 제공된 경우, 셀렉터는 인벤토리의 요소만 사용한다"
- "인벤토리에 없는 요소가 필요하면 출력에 `(요소 미발견: <설명>)` 표시"
- 셀렉터 형식 우선순위: `getByRole(role, {name})` > `getByText` > `getByTestId` > CSS

기존 자유 추측 모드와의 호환성 유지 — 인벤토리가 비면 fallback 동작.

- **산출물**: `dify-chatflow.yaml` 패치 + 프롬프트 변경 비교 문서

#### T1.7 — 효과 측정 하니스 (2일)

- 평가용 페이지 10종 선정 (공개 SaaS 5 + 자체 fixture 5)
- 각 페이지에 골든 시나리오(이상적 DSL) 정의

**자동 수집 메트릭**

- 첫 시도 셀렉터 정확도 (정확/부분/실패)
- Healer 호출 빈도
- Planner 응답 시간
- 토큰 사용량

신·구 경로 비교 리포트 자동 생성.

- **산출물**: `tests/grounding_eval/` + 비교 리포트

#### T1.8 — 파일럿 + 튜닝 (2-3일)

- 운영 후보 페이지 3-5종에서 실제 동작 검증
- 발견된 가지치기 허점·직렬화 비효율 수정
- 프롬프트 미세 조정
- **산출물**: 튜닝 PR + 파일럿 결과 문서

#### T1.9 — 문서화 & 롤아웃 (1일)

- `README.md` §"DOM Grounding" 섹션 추가
- Feature flag 사용법·토글 절차
- 운영자 트러블슈팅 (추출 실패 시 진단)
- **산출물**: README 패치

### 산출물 (Deliverables)

1. `zero_touch_qa/grounding/` 모듈 (~330 LOC)
2. `dify_client.py` 통합 패치
3. `dify-chatflow.yaml` Planner 프롬프트 패치
4. 평가 하니스 + 리포트 (페이지 10종)
5. 신·구 경로 비교 데이터
6. README 섹션
7. Feature flag 설계 문서

### 성공 기준 (Definition of Done)

- ✅ 평가 페이지 10종 중 **8종 이상** 에서 첫 시도 셀렉터 정확도 **75%+** 달성
- ✅ Healer 호출 빈도 **현재 대비 1/3 이하**
- ✅ Planner 응답 시간 증가 **+10초 이내** (페이지 로딩 포함)
- ✅ 토큰 예산 초과 페이지가 평가 셋의 10% 이하
- ✅ Feature flag off 시 기존 동작 100% 보존
- ✅ 추출 실패 시에도 chatflow 정상 종료 (graceful degradation)

### 위험 & 완화

| 위험 | 영향 | 완화 |
|---|---|---|
| SPA 페이지의 초기 DOM 비어있음 | 추출 실패 | `wait_until=networkidle` + 명시적 `wait_for_selector` 옵션 |
| 토큰 예산 만성 초과 | grounding 무력화 | 페이지별 sampling (folded summary), 컨텍스트 윈도우 확대 |
| 의미 HTML 부재한 페이지 | grounding 효과 없음 | `data-testid`·id·placeholder fallback 명시 |
| Playwright 호출이 에이전트 환경에서 실패 | 통합 차단 | 별도 프로세스 분리 옵션 (subprocess) 설계 |

### 추정 일정

**합계: 1.5-2주** (1명 엔지니어 풀타임)

| 주차 | 태스크 |
|---|---|
| W1 D1-2 | T1.1 스키마, T1.2 추출기 시작 |
| W1 D3-4 | T1.2 완료, T1.3 pruner |
| W1 D5 | T1.4 budget |
| W2 D1 | T1.5 통합, T1.6 프롬프트 |
| W2 D2-3 | T1.7 평가 하니스 |
| W2 D4-5 | T1.8 파일럿 + T1.9 문서 |

### 결정 게이트 — Phase 1 → Phase 1.5

다음을 모두 충족해야 Phase 1.5 진입.

- [ ] DoD 6개 항목 모두 통과
- [ ] 운영 환경 1주 안정 운영 (예외 없음)
- [ ] Phase 2 의 비용·일정 추정에 필요한 데이터 확보 (특히 인증 영역에서 grounding 단독으로 안 되는 케이스 패턴 식별)

---

## Phase R — Recording-based Test Generation

> **한 줄 요약**: 호스트 GUI 환경에서 사용자 행동을 녹화하면 14-DSL 로 자동 변환되는 통합 UX 를 제공한다. Phase 1 과 병행 진행.

### 목표

호스트 에이전트(mac/wsl) 환경에서 사용자가 실제 브라우저 액션을 수행하면 14-DSL JSON 으로 자동 변환되는 **통합 레코딩 UX** 를 제공한다.

추가로 Recording ↔ Doc-based 두 트랙의 산출물을 결합하는 시너지 기능을 구현한다.

- **시나리오 A**: Recording → Doc 역추정
- **시나리오 B**: Doc-based ↔ Recording 비교

### 범위

**IN**

- Host agent 위에서 동작하는 Recording 서비스 (Python, FastAPI)
- `playwright codegen` subprocess 래핑 (검증된 MS 엔진 사용)
- 기존 `converter.py` 재사용으로 codegen 출력 → 14-DSL JSON 변환
- 단순 Web UI (vanilla JS, SPA 비사용) — 시작/중지/결과 보기/replay
- Recording 결과 영속화 (host ↔ container 공유 디렉토리)
- **결합 시나리오 A**: Recording → IEEE 829-lite 테스트 계획서 LLM 역추정
- **결합 시나리오 B**: Doc-based DSL ↔ Recording DSL 의미 비교 + HTML diff
- 녹화 직후 executor 재실행으로 DSL 검증 (round-trip)
- mac-agent / wsl-agent 부트스트랩 통합

**OUT**

- 컨테이너 단독 헤드리스 녹화 — 호스트 GUI 필수 결정으로 제외
- 커스텀 JS 오버레이·자체 셀렉터 생성기 — codegen 엔진에 위임
- 동시 다중 사용자 세션 — 단일 세션 우선
- 시각적 DSL 편집기 — 사용자가 DSL 직접 편집
- 모바일·터치 이벤트 녹화

### 아키텍처

#### 기본 Recording 흐름

```
사용자 → Recording Web UI (호스트 브라우저, http://localhost:18092)
          ↓ POST /recording/start {target_url, planning_doc_ref?}
       Recording Service (호스트 데몬, port 18092)
          ├─ subprocess.Popen([
          │     'playwright', 'codegen', target_url,
          │     '--target=python',
          │     '--output', '/tmp/rec_<id>.py'
          │   ])
          └─ Playwright Inspector + 헤디드 Chromium 창 띄움
             ↓
          사용자가 직접 페이지 조작 (클릭/입력/네비게이션)
             ↓
       Recording Web UI: "Stop" 클릭
          ↓ POST /recording/stop/<id>
       Recording Service
          ├─ subprocess SIGTERM (codegen 정상 종료)
          ├─ /tmp/rec_<id>.py 읽기
          ├─ converter.convert_playwright_to_dsl() 호출
          ├─ _validate_scenario 통과 검증
          ├─ 영속화: ~/.dscore.ttc.playwright-agent/recordings/<id>/
          └─ 결과 JSON 반환 (UI 에 표시)
             ↓
       사용자: "Replay" → Executor 재실행 → 통과/실패 표시
```

#### 결합 시나리오 A — Recording → Doc 역추정

```
Recording 결과 → "Generate Doc" 버튼
  ↓ POST /recording/<id>/enrich
  → Ollama 직접 호출
    (system: "다음 DSL 시퀀스를 IEEE 829-lite 계획서로 역추정")
  → 입력: recorded DSL + 페이지 컨텍스트 (URL, title, optional DOM 인벤토리)
  → 출력: 테스트 계획서 markdown (목적/범위/단계/예상결과)
  → 영속화: <id>/doc_enriched.md
  → UI 표시 + 다운로드
```

#### 결합 시나리오 B — Doc ↔ Recording 비교

```
Recording 결과 → "Compare with Doc-DSL" 버튼
  ↓ POST /recording/<id>/compare {doc_dsl: ...}
  → 의미 시퀀스 추출: [(action, role, name), ...]
  → LCS (longest common subsequence) 정렬
  → 분류: 일치 / 불일치 (값만 다름) / 누락 / 추가
  → HTML diff 리포트 생성
  → 영속화: <id>/doc_comparison.html
  → UI 표시 + 다운로드
```

### 신규 모듈

| 위치 | 파일 | 역할 | 추정 LOC |
|---|---|---|---|
| 호스트 에이전트 | `recording_service/server.py` | FastAPI 엔드포인트 | 150 |
| 호스트 에이전트 | `recording_service/codegen_runner.py` | subprocess 래퍼 + 수명주기 | 120 |
| 호스트 에이전트 | `recording_service/session.py` | 세션 ID·상태 관리 | 80 |
| 호스트 에이전트 | `recording_service/storage.py` | 영속화 (recordings/) | 60 |
| 호스트 에이전트 | `recording_service/web/index.html` | 단일 페이지 UI | 200 |
| 호스트 에이전트 | `recording_service/web/app.js` | UI 로직 (vanilla) | 200 |
| 컨테이너 (재사용) | `zero_touch_qa/converter.py` | 기존 변환기 (변경 최소) | — |
| 컨테이너 | `zero_touch_qa/recording/enricher.py` | 시나리오 A 구현 | 100 |
| 컨테이너 | `zero_touch_qa/recording/comparator.py` | 시나리오 B 구현 | 180 |
| 호스트 에이전트 | `mac-agent-setup.sh` | recording 서비스 부트스트랩 | +50 |
| 호스트 에이전트 | `wsl-agent-setup.sh` | 동일 | +50 |
| 테스트 | `tests/recording/*` | 단위·통합 | 300 |

### 상세 태스크

#### TR.1 — Recording 서비스 골격 (2일)

호스트에서 동작하는 Python 데몬 (FastAPI + uvicorn).

**엔드포인트**

| Method | Path | 역할 |
|---|---|---|
| POST | `/recording/start` | 녹화 시작 (input: target_url, planning_doc_ref?) |
| POST | `/recording/stop/{id}` | 녹화 종료 + 변환 |
| GET | `/recording/sessions` | 세션 목록 |
| GET | `/recording/sessions/{id}` | 세션 상세 (DSL, 메타데이터) |
| POST | `/recording/sessions/{id}/replay` | executor 재실행 |
| DELETE | `/recording/sessions/{id}` | 삭제 |
| GET | `/healthz` | 헬스체크 |

- CORS 설정 — Web UI 가 호스트 브라우저에서 호출
- 호스트의 systemd·launchd 등록 (선택)
- **산출물**: 서비스 기동 + 헬스체크 통과

#### TR.2 — codegen subprocess 래퍼 (2일)

- `playwright codegen <url> --target=python --output <path>` subprocess 실행
- PID·프로세스 생명주기 추적
- 정상 종료 (SIGTERM) / 비정상 종료 감지
- 프로세스 stdout/stderr 캡처 (디버깅용)
- 시간 한도 (기본 30분, env var 로 변경)

**에러 케이스 처리**

- playwright 미설치 → 명확한 에러 메시지
- target_url 도달 불가 → 명확한 에러 메시지
- 출력 파일 비어있음 → "녹화 액션 0건" 알림

- **산출물**: `codegen_runner.py` + 단위 테스트

#### TR.3 — converter.py 통합 + 검증 (1일)

- 기존 `zero_touch_qa/converter.py` 호출 → DSL JSON 생성
- `_validate_scenario` 통과 검증

**검증 실패 시**

- 원본 .py 보존 (`<id>/original.py`)
- 에러 메시지를 UI 에 노출
- 재시도 옵션 제공

변환된 DSL 의 step 수가 0 이면 사용자 경고.

- **산출물**: 변환 통합 + 회귀 테스트

#### TR.4 — Web UI (간단) (3일)

단일 HTML 페이지 + vanilla JS (프레임워크 없음).

**UI 구성**

- **입력 폼**: target_url, optional planning_doc_ref (텍스트)
- **액션 버튼**: [Start Recording] [Stop] [Replay] [Generate Doc] [Compare with Doc-DSL]
- **진행 상태**: 현재 세션 ID, 경과 시간, 캡처된 액션 수 (폴링)
- **결과 영역**: DSL JSON preview (syntax-highlight), 메타데이터, 결합 시나리오 결과
- **세션 목록**: 사이드바 또는 하단 테이블

위치: 호스트의 `http://localhost:18092/`. **장식 최소화** — 기능 우선.

- **산출물**: 단순 UI + 사용성 테스트 (수동)

#### TR.5 — 결합 시나리오 A: Recording → Doc 역추정 (2일)

LLM 호출 (Ollama 직접 호출, Dify 챗플로우 우회).

**시스템 프롬프트**

```
당신은 QA 엔지니어다. 다음 사용자 액션 시퀀스(14-DSL)는 실제 사용자가
웹 페이지에서 수행한 행동을 기록한 것이다. 이 시퀀스로부터 IEEE 829-lite
형식의 테스트 계획서(목적, 범위, 사전조건, 단계, 예상 결과, 검증 기준)
를 역추정하여 작성하라.
```

- 입력: recorded DSL + 페이지 컨텍스트 (URL, title, optional Phase 1 인벤토리)
- 출력: Markdown 테스트 계획서
- 영속화: `<id>/doc_enriched.md`
- Web UI 에 "Generate Doc" 버튼으로 노출

- **산출물**: `enricher.py` + 5개 샘플 녹화로 역추정 품질 평가

#### TR.6 — 결합 시나리오 B: Doc ↔ Recording 비교 (3일)

**의미 시퀀스 정규화**

- 각 DSL step → `(action_type, semantic_target_role, semantic_target_name, value?)`
- 페이지 URL 변화 추적

**비교 알고리즘**

- LCS (longest common subsequence) 로 정렬
- 분류: 정확한 일치 / 의미는 같으나 값만 다름 / 누락 / 추가
- role+name 매칭은 fuzzy (textdistance·difflib ratio)

**HTML diff 리포트**

- 좌(doc-DSL) vs 우(recording-DSL) 사이드바이사이드
- 색상 코딩 (녹/적/회/노)
- 각 차이에 대한 자연어 설명

**입력 옵션**

- doc-DSL JSON 직접 업로드
- 또는 chat 세션 ID 로 doc-DSL 가져오기

영속화: `<id>/doc_comparison.html`

- **산출물**: `comparator.py` + 비교 알고리즘 단위 테스트 + 샘플 리포트

#### TR.7 — Replay 검증 통합 (1일)

녹화 직후 또는 사용자 요청 시 DSL 을 executor 로 재실행.

**호스트 → 컨테이너 통신** (executor 가 컨테이너 내부)

- 옵션 A: 호스트가 컨테이너의 executor REST 엔드포인트 호출 (executor 에 추가 필요)
- 옵션 B: 호스트가 ssh/docker exec 로 executor CLI 호출
- **권장**: 옵션 A (Phase 2 의 agent service 와 일관성)

결과 (PASS/FAIL/HEALED) 를 UI 에 표시. 실패 단계 강조 + healing 옵션.

- **산출물**: replay 통합 + 케이스 테스트

#### TR.8 — Session 영속화 + 호스트↔컨테이너 공유 (2일)

**호스트 영속화 디렉토리**: `~/.dscore.ttc.playwright-agent/recordings/<id>/`

| 파일 | 내용 |
|---|---|
| `original.py` | codegen 원본 |
| `scenario.json` | 변환된 14-DSL |
| `metadata.json` | target_url, started_at, ended_at, action_count, planning_doc_ref? |
| `doc_enriched.md` | 시나리오 A 결과 (있는 경우) |
| `doc_comparison.html` | 시나리오 B 결과 (있는 경우) |
| `replay_result.json` | replay 결과 (있는 경우) |

**컨테이너 ↔ 호스트 공유**

- 호스트의 recordings 디렉토리를 컨테이너에 마운트 (volume)
- 컨테이너 내 executor 가 동일 경로로 접근

세션 목록·검색·삭제 API 제공.

- **산출물**: 영속화 + 마운트 설정

#### TR.9 — 호스트 에이전트 통합 (mac/wsl) (2일)

`mac-agent-setup.sh`·`wsl-agent-setup.sh` 에 다음을 추가한다.

- Python venv 에 `fastapi`, `uvicorn`, `requests` 설치
- recording_service 코드 호스트로 배포
- 서비스 자동 기동 (mac: launchd, wsl: systemd 또는 nohup)
- 헬스체크
- recordings 디렉토리 자동 생성
- 컨테이너에 호스트 IP·포트 알림 (`RECORDING_SERVICE_URL=http://host.docker.internal:18092`)

- **산출물**: 에이전트 스크립트 패치 + 동작 검증

#### TR.10 — 문서화 + 운영 가이드 (1일)

**`README.md` §"Recording 모드" 추가**

- 사용 절차 (호스트 에이전트 환경 가정)
- Web UI 스크린샷 (실제 동작 후 캡처)
- 결합 시나리오 사용법

**`docs/recording-troubleshooting.md`**

- playwright 명령 못 찾음
- 헤디드 Chromium 안 뜸
- 변환 실패 시 디버깅

- **산출물**: README 패치 + 트러블슈팅 문서

### 산출물 (Deliverables)

1. `recording_service/` 모듈 (호스트, ~810 LOC + 테스트)
2. `zero_touch_qa/recording/` 모듈 (컨테이너, ~280 LOC)
3. mac-agent·wsl-agent 부트스트랩 패치
4. 호스트 ↔ 컨테이너 공유 디렉토리 설정
5. Web UI (단일 페이지)
6. 결합 시나리오 A·B 구현
7. README + 트러블슈팅 문서
8. 5개 샘플 녹화 + 역추정 결과 (품질 입증)

### 성공 기준 (Definition of Done)

- ✅ 호스트 에이전트 환경에서 Web UI 통해 녹화 → DSL 변환 **5단계 이내** 완료
- ✅ 5종 샘플 페이지에서 녹화 → DSL → replay 통과율 **90%+**
- ✅ 시나리오 A: 5개 녹화에 대해 LLM 이 일관된 IEEE 829 계획서 생성 (사람 평가 4/5 이상)
- ✅ 시나리오 B: 의도적 차이가 있는 doc-DSL 과 recording-DSL 비교 시 **모든 차이 정확히 식별**
- ✅ 호스트 ↔ 컨테이너 공유 디렉토리로 결과 자동 동기화
- ✅ 녹화 중단·재시작 안전 (idempotent)

### 위험 & 완화

| 위험 | 영향 | 완화 |
|---|---|---|
| `playwright codegen` 셀렉터 품질이 페이지마다 편차 | 변환 후 DSL 품질 저하 | converter 후처리에서 의미 셀렉터 우선 변환 (Phase 1 과 동일 휴리스틱 재사용) |
| 호스트 환경별 GUI 차이 (mac/wsl) | 일관성 저하 | wsl 의 경우 WSLg 검증, X-server fallback 명시 |
| 호스트 ↔ 컨테이너 통신 (특히 wsl) | replay 실패 | `host.docker.internal` 동작 검증, IP fallback 옵션 |
| 녹화 도중 페이지 새로고침 | codegen 출력 깨짐 | converter 가 부분 출력도 처리 가능하도록 robust 화 |
| 시나리오 A 가 일관성 없는 계획서 생성 | 신뢰성 저하 | few-shot 예시 3개를 시스템 프롬프트에 포함 |
| 시나리오 B fuzzy 매칭 임계값 튜닝 | false positive·negative | 골든 페어 10세트로 임계값 결정, 사용자 조정 옵션 |

### 추정 일정

**합계: 2.5-3주** (1명 엔지니어 풀타임, Phase 1 과 다른 엔지니어가 병행)

| 주차 | 태스크 |
|---|---|
| W1 D1-2 | TR.1 서비스 골격, TR.2 codegen 래퍼 시작 |
| W1 D3-4 | TR.2 완료, TR.3 converter 통합 |
| W1 D5 | TR.4 Web UI 시작 |
| W2 D1-3 | TR.4 Web UI 완료, TR.5 시나리오 A |
| W2 D4-5 | TR.6 시나리오 B 시작 |
| W3 D1 | TR.6 완료 |
| W3 D2 | TR.7 replay |
| W3 D3-4 | TR.8 영속화, TR.9 에이전트 통합 |
| W3 D5 | TR.10 문서 + 안정화 |

### Phase R 결정 게이트

**단독 종료 게이트** (다른 Phase 와 독립 평가)

- [ ] DoD 6개 항목 모두 통과
- [ ] 5종 샘플 페이지 일관 동작
- [ ] mac-agent·wsl-agent 양쪽 환경에서 동작 검증

**Phase 2 시너지 게이트** (Phase 2 진입 시 추가 평가)

- [ ] Phase R 의 시나리오 B 가 Phase 2 에이전트 출력 평가에 활용 가능한지 확인 (Phase 2 의 자가 평가 인프라로 재사용)

---

## Phase 1.5 — 모델 도구 호출 신뢰성 검증

> **한 줄 요약**: `gemma4:26b` 가 Phase 2 의 도구 호출 에이전트를 구동할 수 있는지 검증한다. 불가하면 대체 모델을 식별한다.

### 목표

`gemma4:26b` 가 Phase 2 의 다중턴 도구 호출 에이전트 루프를 신뢰성 있게 구동할 수 있는지 검증한다. 불가하면 대체 모델을 식별하고 인프라 영향을 산정한다.

### 범위

**IN**

- 표준 도구 스키마 5종 정의 (Phase 2 후보)
- Ollama 기반 도구 호출 PoC 하니스
- 후보 모델 4종 벤치마크 (`gemma4:26b` 포함)
- 의사결정 매트릭스 + 인프라 영향 분석
- 의사결정 문서

**OUT**

- 실제 Phase 2 에이전트 구현 — 별도 단계
- 모델 fine-tuning
- 다른 추론 백엔드(vLLM 등) 평가

### 상세 태스크

#### T1.5.1 — 표준 도구 스키마 정의 (1일)

Phase 2 에서 사용할 도구 5종을 명세한다.

| 도구 | 시그니처 | 용도 |
|---|---|---|
| `navigate` | `(url)` | 페이지 이동 |
| `get_dom_subtree` | `(selector?)` | 현재 또는 특정 영역 인벤토리 |
| `interact` | `(action, role, name)` | click/fill/select 등 의미 액션 |
| `verify` | `(condition, target)` | 검증 |
| `done` | `(scenario_dsl)` | 완료 + DSL 반환 |

각 도구의 OpenAI function schema (JSON Schema) 작성.

- **산출물**: `docs/agent-tools-spec.md`

#### T1.5.2 — PoC 하니스 (2일)

Python 스크립트로 Ollama `/v1/chat/completions` 직접 호출. 50개 시나리오 프롬프트 (단일턴 30 + 다중턴 20) 준비.

**자동 평가 메트릭**

- 도구 호출 유효성 (parseable JSON, 스키마 준수)
- 파라미터 정확도 (인간 평가 + LLM-as-judge)
- 다중턴 컨텍스트 일관성 (앞 턴 결과를 다음 턴에 반영하는지)
- 종료 신호 정확성 (`done` 호출 적절성)
- 환각률 (존재하지 않는 도구·파라미터 호출)

- **산출물**: `tests/tool_calling_eval/`

#### T1.5.3 — 후보 모델 벤치마크 (2일)

| 모델 | 위치 | 비고 |
|---|---|---|
| `gemma4:26b` | 베이스라인 | 현재 |
| `qwen2.5:32b` | 후보 | 도구 호출 강세로 알려짐 |
| `llama3.3:70b-instruct-q4_K_M` | 후보 | 일반 능력 ↑ |
| `granite3-dense:8b` | 후보 | IBM, 함수 호출 특화 (소형) |

각 모델로 50 시나리오 실행 → 메트릭 수집. GPU 메모리 사용량·응답 시간 기록.

- **산출물**: 벤치마크 리포트 + raw 데이터

#### T1.5.4 — 의사결정 매트릭스 (1일)

평가 축: 도구 호출 신뢰성 / 응답 속도 / GPU 비용 / 운영 복잡도.

**의사결정 트리**

```
if gemma4:26b 신뢰성 ≥ 90%:
    → 유지 (Phase 2 진행)
elif 후보 중 ≥ 90% 도달:
    → 모델 교체 (인프라 재산정 필요)
else:
    → ReAct/RAISE 스타일 프롬프트로 우회 (도구 호출 미사용)
    → Phase 2 아키텍처 재설계
```

- **산출물**: `decisions/2026-XX-model-tool-calling.md`

#### T1.5.5 — 인프라 영향 산정 (모델 교체 시, 1-2일)

**GPU VRAM 요구량 (Q4 양자화 기준)**

| 모델 | VRAM |
|---|---|
| `gemma4:26b` | ~17GB |
| `qwen2.5:32b` | ~22GB |
| `llama3.3:70b` | ~42GB |
| `granite3-dense:8b` | ~6GB |

**검토 항목**

- 호스트 머신 사양 검토 (현재 GPU?)
- 다운로드 시간·디스크 사용량
- 컨테이너 이미지 변경 영향
- 모델 교체 절차 (재배포·마이그레이션)

- **산출물**: 인프라 변경 제안서

### 성공 기준

- ✅ 50 시나리오 평가에서 **신뢰성 90%+** 모델 1종 이상 식별
- ✅ 인프라 변경이 필요하면 변경 비용·일정 명확히 산정
- ✅ Phase 2 진입 가능성에 대한 명확한 Go·No-Go 결정

### 위험 & 완화

| 위험 | 완화 |
|---|---|
| 모든 후보가 90% 미달 | ReAct 스타일 (도구 호출 없는 prompted 액션) 폴백 설계 — Phase 2 일정 +2주 |
| 가장 좋은 모델이 GPU 한계 초과 | Q4→Q3 양자화 또는 일부 도구만 LLM, 나머지 결정론적 코드 |
| 호스트 GPU 정보 부재 | Phase 1.5 시작 전 호스트 사양 확정 필수 |

### 추정 일정

**합계: 1주** (모델 교체 결정 시 추가 1주)

### 결정 게이트 — Phase 1.5 → Phase 2

- [ ] 1개 이상 모델이 신뢰성 90%+ 통과
- [ ] 인프라 변경(필요 시) 승인 완료
- [ ] Phase 2 아키텍처가 선택된 모델에 맞게 조정 가능 확인

---

## Phase 2 — External Agent Skeleton

> **한 줄 요약**: Dify 챗플로우 외부에 별도 Python 에이전트 서비스를 구축, 인증 페이지 1종에 대해 end-to-end 자동 시나리오 생성을 입증한다.

### 목표

**Dify 챗플로우 외부에 별도 Python 에이전트 서비스** 를 구축한다. LLM ↔ Playwright 다중턴 도구 호출 루프로 인증 페이지 1종에 대해 **end-to-end 자동 시나리오 생성** 을 입증한다.

Dify 1.13 의 챗플로우 한계를 우회한다.

### 범위

**IN**

- 외부 Python 에이전트 서비스 (장기 실행 데몬)
- Phase 1.5 에서 검증된 도구 5-7종 구현
- 에이전트 루프 (LLM 호출 ↔ 도구 실행 반복)
- 안전 가드 (최대 턴·타임아웃·비용 한도)
- 인증 흐름 처리 (쿠키·로컬스토리지 영속화)
- 브라우저 상태 → DSL 변환
- 파일럿 인증 페이지 1종 (자체 환경 권장 — 로컬 Jenkins 대시보드)
- 기존 Executor 와의 통합 (생성된 DSL → 즉시 실행)
- 기본 메트릭 수집 (턴 수·시간·성공률)

**OUT**

- Dify UI 통합·스트리밍 → Phase 3
- 다중 페이지 회귀 → Phase 3
- Multi-tab·iframe → Phase 3
- 운영 모니터링 대시보드 → Phase 3
- 시각 회귀·성능·a11y 테스트 — 영구 OUT

### 아키텍처

```
[현재]
사용자 → Dify Chatflow (단일 LLM 호출) → DSL JSON

[Phase 2 후]
사용자 → Dify Chatflow (요청 접수, KB 검색)
          ↓ HTTP POST
       Agent Service (FastAPI, port 18090)
          ├─ LLM 호출 (Ollama)
          ├─ 도구 실행 (Playwright)
          └─ 루프 (max N 턴)
          ↓ 완성된 DSL JSON
       Dify Chatflow (결과 표시)
          ↓
       Executor (기존, 변경 없음)
```

### 신규 모듈

| 파일 | 역할 | 추정 LOC |
|---|---|---|
| `zero_touch_qa/agent/__init__.py` | 진입점 | 30 |
| `zero_touch_qa/agent/server.py` | FastAPI 엔드포인트 | 120 |
| `zero_touch_qa/agent/loop.py` | 에이전트 메인 루프 | 200 |
| `zero_touch_qa/agent/tools/navigate.py` | 도구 구현 | 60 |
| `zero_touch_qa/agent/tools/dom.py` | DOM 인벤토리 (Phase 1 재사용) | 40 |
| `zero_touch_qa/agent/tools/interact.py` | 인터랙션 도구 | 100 |
| `zero_touch_qa/agent/tools/verify.py` | 검증 도구 | 80 |
| `zero_touch_qa/agent/tools/done.py` | 종료 + DSL 추출 | 60 |
| `zero_touch_qa/agent/state.py` | 브라우저·세션 상태 관리 | 100 |
| `zero_touch_qa/agent/safety.py` | 가드 (limits·timeouts) | 80 |
| `zero_touch_qa/agent/llm_client.py` | Ollama 도구 호출 래퍼 | 100 |
| `zero_touch_qa/agent/dsl_emitter.py` | 상태 → DSL 변환 | 120 |
| `tests/agent/*.py` | 단위·통합 테스트 | 400 |

### 상세 태스크

#### T2.1 — 에이전트 서비스 골격 (3일)

FastAPI 서버 구조 (`server.py`).

**엔드포인트**

| Method | Path | 역할 |
|---|---|---|
| POST | `/agent/explore` | 탐색 요청 (input: target_url, requirements, auth?) |
| GET | `/agent/runs/{id}` | 실행 상태 조회 |
| GET | `/agent/runs/{id}/result` | 완성된 DSL |
| DELETE | `/agent/runs/{id}` | 중단 |

- supervisord 설정 추가
- 로깅·헬스체크
- **산출물**: 서버 골격 + 헬스체크 통과

#### T2.2 — LLM 클라이언트 (도구 호출 래퍼, 2일)

- Ollama `/v1/chat/completions` 도구 호출 모드
- 메시지 히스토리 관리 (system + user + assistant + tool 메시지)
- 도구 호출 파싱·검증 (스키마 위반 시 재시도)
- 토큰 사용량 추적
- 폴백: 도구 호출 실패 시 ReAct 프롬프팅 자동 전환 (Phase 1.5 결과에 따라)
- **산출물**: `llm_client.py` + 단위 테스트

#### T2.3 — 도구 구현 (4일)

##### T2.3.1 navigate (0.5일)

- Playwright `page.goto(url)` + `wait_until` 옵션
- 새 탭 감지 (Phase 3 까지는 무시)

##### T2.3.2 get_dom_subtree (1일)

- Phase 1 의 `extractor` 재사용
- `selector` 파라미터로 부분 트리 가능
- 결과를 LLM 친화적 형식으로 직렬화

##### T2.3.3 interact (1.5일)

- `action`: click·fill·select·check·hover·press
- `role` + `name` 으로 의미 셀렉터 사용
- 실행 후 DOM 변화 감지 (URL 변경, 새 요소 등장)
- 실행 결과를 도구 응답으로 반환 (성공·실패 + 변화 요약)

##### T2.3.4 verify (0.5일)

- `condition`: visible·text·url_match·...
- 실패 시 명확한 메시지

##### T2.3.5 done (0.5일)

- LLM 이 탐색을 마쳤다고 판단 시 호출
- 누적된 액션 시퀀스를 DSL JSON 으로 직렬화
- 에이전트 루프 종료 신호

**산출물**: 5개 도구 모듈 + 통합 테스트

#### T2.4 — 에이전트 메인 루프 (3일)

`loop.py` 핵심 로직.

```python
while turn < max_turns and not done:
    response = llm.chat(messages, tools=tool_specs)
    if response.tool_call:
        result = execute_tool(response.tool_call)
        messages.append(tool_result)
    else:
        messages.append(response.content)
        # LLM 이 도구 호출 없이 응답 → 종료 또는 nudge
    turn += 1
```

**시스템 프롬프트 구성**

- 목표 (요구사항 충족 시나리오 생성)
- 가능한 도구 + 사용 가이드
- 종료 조건 (`done` 호출)
- 환각·이탈 방지 가드

멀티 시도 지원 (도구 호출 실패 시 재시도).

- **산출물**: `loop.py` + e2e 테스트 1종

#### T2.5 — 안전 가드 (2일)

**한도 (env var 로 변경 가능)**

| 가드 | 기본값 |
|---|---|
| `max_turns` | 20 |
| `max_total_seconds` | 300 |
| `max_navigate_calls` | 10 |
| `max_interact_calls` | 30 |
| `max_total_tokens` | 50000 |

- 한도 초과 시 graceful 종료 + 부분 결과 반환
- 위험 도메인 가드 (이미 executor 에 있는 룰 재사용)
- 무한 루프 감지 (같은 도구+파라미터 N회 반복)

- **산출물**: `safety.py` + 가드 테스트

#### T2.6 — 인증 흐름 처리 (3일)

- 옵션 A: 사전 인증 (사용자가 쿠키·세션 제공)
- 옵션 B: 에이전트가 자동 로그인 (LLM 이 로그인 폼 발견 → 제공된 자격증명으로 fill → submit)
- Playwright `BrowserContext.storage_state()` 사용
- 자격증명 안전 관리 (env var, 절대 로그에 노출 금지)
- 세션 만료 감지 + 재로그인 정책

- **산출물**: 인증 처리 + 1개 자체 환경에서 동작 입증

#### T2.7 — 상태 → DSL 변환기 (2일)

- 에이전트가 수행한 액션 시퀀스를 14-DSL JSON 으로 변환
- 각 액션마다 traceability 코멘트 (어느 LLM 턴에서 결정됐는지)
- 기존 `_validate_scenario` 통과 보장
- 변환 후 즉시 `executor` 로 재실행하여 검증 (round-trip 테스트)

- **산출물**: `dsl_emitter.py` + round-trip 테스트

#### T2.8 — Dify 챗플로우 → 에이전트 호출 (2일)

- `dify-chatflow.yaml` 또는 `test-planning-chatflow.yaml` 에 새 분기 추가
- **옵션 A (간단·권장)**: `run_mode=agent` 추가, `dify_client` 가 분기 — Dify 호출 *대신* 에이전트 서비스 호출
- 옵션 B (복잡): Dify 의 HTTP 노드 활용 (Dify 1.13 지원 여부 확인 필요)

권장 이유: Dify 챗플로우 변경 최소화.

- **산출물**: 통합 PR

#### T2.9 — 파일럿 인증 페이지 1종 검증 (3일)

- **후보 페이지**: 자체 호스팅 Jenkins 대시보드 (`http://localhost:18080`)
- **시나리오**: 관리자 로그인 → 작업 목록 → 'ZeroTouch-QA' 잡 클릭 → 빌드 히스토리 확인
- 5회 반복 실행, 성공률 측정
- 실패 케이스 분석 / 가드·프롬프트 튜닝

- **산출물**: 파일럿 결과 리포트

#### T2.10 — 기본 메트릭 + 로깅 (1일)

**실행당 기록 항목**

- 총 턴 수
- 총 시간
- 토큰 사용량 (input·output)
- 도구 호출 분포 (어느 도구를 몇 번 사용했는지)
- 성공·실패·timeout

`agent_runs.jsonl` 파일에 적재 (Jenkins artifacts 와 별도).

- **산출물**: 메트릭 수집 + 샘플 리포트

### 산출물 (Deliverables)

1. `zero_touch_qa/agent/` 모듈 (~1500 LOC + 테스트 400)
2. FastAPI 서비스 + supervisord 등록
3. 5개 도구 구현
4. 인증 처리 코드
5. DSL 변환기
6. Dify 통합 패치
7. 파일럿 결과 리포트
8. 메트릭 수집 인프라
9. 운영 가이드 문서

### 성공 기준 (Definition of Done)

- ✅ 자체 인증 페이지 1종에서 **5회 연속 시도 시 4회 이상 성공**
- ✅ 평균 에이전트 턴 수 **15 이내**
- ✅ 평균 실행 시간 **5분 이내**
- ✅ 생성된 DSL 이 `executor` 로 재실행 시 **100% 통과**
- ✅ 안전 가드 (timeout·max turns) 가 의도대로 동작
- ✅ Dify 챗플로우의 기존 모드는 영향 없음

### 위험 & 완화

| 위험 | 영향 | 완화 |
|---|---|---|
| LLM 이 무한히 같은 도구 반복 | 자원 낭비·timeout | T2.5 의 무한 루프 감지 |
| 인증 자격증명 누출 | 보안 사고 | secrets manager·env var only·로그 마스킹 |
| Playwright 브라우저 상태 누적 | 메모리 누수 | 실행당 새 context, 종료 시 `context.close()` |
| Dify ↔ 에이전트 통신 단절 | 사용자 경험 저하 | 폴링·재연결·부분 결과 반환 |
| 도구 호출이 의도치 않은 페이지로 이탈 | 위험 액션 | 도메인 화이트리스트 + 액션 확인 옵션 |

### 추정 일정

**합계: 3-4주** (1명 엔지니어 풀타임)

| 주차 | 태스크 |
|---|---|
| W1 | T2.1 서버, T2.2 LLM 클라이언트 |
| W2 | T2.3 도구 5종 |
| W3 | T2.4 루프, T2.5 가드, T2.6 인증 시작 |
| W4 | T2.6 인증 완료, T2.7 변환기, T2.8 Dify 통합, T2.9 파일럿, T2.10 메트릭 |

### 결정 게이트 — Phase 2 → Phase 3

- [ ] DoD 6개 모두 통과
- [ ] 1주 안정 운영 (자체 페이지에서)
- [ ] Phase 3 의 30종 페이지 코퍼스 후보 리스트 확정
- [ ] 운영 비용 (LLM 토큰·GPU 시간) 시간당 추정치 확보

---

## Phase 3 — Stabilization & Dify UI Integration

> **한 줄 요약**: 30종 페이지에서 안정 동작, Dify 챗 UI 진행 스트리밍, 운영 대시보드까지. 운영 가능 수준으로 격상.

### 목표

Phase 2 의 에이전트를 **30종 대표 페이지** 에서 안정적으로 동작하도록 강화한다. Dify 챗 UI 와의 진행 스트리밍 통합으로 사용자 경험을 완성한다.

운영 가능 수준으로 격상.

### 범위

**IN**

- 30종 페이지 회귀 코퍼스 (자체 + 공개 SaaS 혼합)
- 추가 도구 구현 (file upload·dropdown·date picker·scroll into view)
- Multi-tab 시나리오 지원
- iframe 시나리오 지원 (가능한 범위)
- Dify ↔ 에이전트 진행 스트리밍 (사용자가 챗 UI 에서 진행 상황 확인)
- 실패 회복 정책 (재시도·부분 결과 사용)
- 운영 대시보드 (간단한 HTML, Jenkins artifacts 연계)
- 비용·성능 가시화
- 운영자 매뉴얼

**OUT**

- Shadow DOM — 도구 호출로 한계 극복 어려움, 별도 R&D 필요
- WebSocket 기반 동적 콘텐츠 검증 — 별도 도구 필요
- 시각 회귀·a11y·성능 — 영구 OUT
- 외부 시스템 연동(이메일·SMS) 검증

### 상세 태스크

#### T3.1 — 30종 코퍼스 선정 + 골든 시나리오 (3일)

**카테고리 분배**

| 카테고리 | 수 | 예시 |
|---|---|---|
| 자체 환경 | 10종 | Jenkins, Dify 콘솔, Allure 리포트, fixture |
| 공개 SaaS | 10종 | 인증 불요 |
| 협력사 staging | 10종 | 인증 필요 |

각 페이지에 골든 시나리오 정의 (성공 판정 기준). 자동 평가 하니스 확장.

- **산출물**: `tests/agent_corpus/`

#### T3.2 — 추가 도구 구현 (4일)

| 도구 | 시그니처 | 비고 |
|---|---|---|
| `upload` | `(file_path)` | 파일 업로드 (input + 커스텀 picker) |
| `select_option` | `(role, name, option)` | 드롭다운 |
| `pick_date` | `(date)` | 캘린더 위젯 (휴리스틱: 클릭 후 일자 매칭) |
| `scroll_to` | `(role, name)` | 가시 영역으로 스크롤 |

각 도구의 시스템 프롬프트 가이드라인 작성.

- **산출물**: 4개 도구 + 단위 테스트

#### T3.3 — Multi-tab 지원 (3일)

- `BrowserContext.on('page', ...)` 리스너
- 새 탭 발생 시 LLM 에 알림 (도구 응답에 "new tab opened" 포함)
- LLM 이 새 탭으로 컨텍스트 전환 결정 (`switch_tab` 도구 추가)

- **산출물**: `tools/switch_tab.py` + 시나리오 테스트

#### T3.4 — Iframe 지원 (4일)

- `frame_locator` 기반 도구 변형
- LLM 에 iframe 존재 시 알림
- 인벤토리 추출 시 iframe 내부도 포함 옵션
- 한계 명시 — cross-origin iframe 미지원

- **산출물**: iframe 처리 + 1개 시나리오 테스트

#### T3.5 — Dify 스트리밍 통합 (4일)

에이전트 서비스에 SSE 엔드포인트 추가 (`GET /agent/runs/{id}/stream`).

**진행 이벤트**

| 이벤트 | 의미 |
|---|---|
| `turn_start` | 턴 시작 |
| `tool_call` | 도구 호출 |
| `tool_result` | 도구 결과 요약 |
| `done` | 완료 |

**Dify 노출 옵션**

- 옵션 A: Dify HTTP 노드 + 폴링
- 옵션 B: 별도 프록시가 SSE → Dify chat 메시지 변환

사용자 경험: 챗봇이 "현재 로그인 페이지 탐색 중...", "관리자 메뉴 발견..." 등 자연어 진행 보고.

- **산출물**: 스트리밍 인프라 + UX 데모

#### T3.6 — 실패 회복 정책 (3일)

**도구 실패 분류**

| 분류 | 대응 |
|---|---|
| 일시적 (네트워크) | 재시도 (지수 backoff) |
| 영구적 (요소 부재) | LLM 에 알림 → 대안 탐색 |
| 치명적 (브라우저 크래시) | 새 context 로 재시작 |

**부분 결과 정책**: max_turns 도달 시에도 누적 액션을 DSL 로 emit (사용자가 review).

- **산출물**: 회복 로직 + 케이스 테스트

#### T3.7 — 운영 대시보드 (2일)

단일 HTML 페이지 (`http://<host>:18091/dashboard`).

**표시 항목**

- 최근 24h 에이전트 실행 (성공·실패 비율)
- 평균 턴 수·시간·토큰
- 코퍼스 회귀 통과율
- 최근 실패 5건 (드릴다운 가능)

단순 정적 SSG (Jinja2 템플릿) — 별도 SPA 안 만듦.

- **산출물**: dashboard 페이지

#### T3.8 — 비용·성능 가시화 (2일)

- 토큰 사용량 → 운영 비용 환산 (자체 호스팅이라 GPU 시간 환산)
- 시나리오당 평균 비용·시간 추적
- Threshold 알림 (시간당 N회 초과, 단일 실행 비용 초과)

- **산출물**: 비용 모니터링

#### T3.9 — 30종 회귀 검증 + 안정화 (5일)

- 코퍼스 전체 자동 실행 (3회 반복)
- 실패 패턴 분석 → 도구·프롬프트·가드 보강
- 안정화 반복 — 목표 코퍼스 통과율 90%+

- **산출물**: 회귀 통과 데이터 + 안정화 PR 시리즈

#### T3.10 — 운영자 매뉴얼 + 운영 전환 (2일)

**`docs/agent-operator-guide.md`**

- 에이전트 서비스 시작·중지
- 새 페이지 등록
- 트러블슈팅 (로그 위치, 흔한 실패 패턴)
- 비용 통제
- 모델 교체 절차

`README.md` §"AI Agent 모드" 추가.

- **산출물**: 매뉴얼 + README 패치

### 산출물 (Deliverables)

1. 30종 코퍼스 + 골든 시나리오
2. 추가 도구 4종 + 탭·iframe 도구
3. SSE 스트리밍 인프라
4. 회복 정책 코드
5. 운영 대시보드
6. 비용 모니터링
7. 회귀 통과 데이터
8. 운영자 매뉴얼

### 성공 기준 (Definition of Done)

- ✅ 30종 코퍼스 통과율 **90%+** (3회 평균)
- ✅ 평균 실행 시간 **3-5분**
- ✅ Dify 챗 UI 에서 진행 상황 가시화 동작
- ✅ 1주 운영 중 치명적 실패(서비스 다운) 0건
- ✅ 비용 가시화로 시간당 비용 추정 가능
- ✅ 운영자가 매뉴얼만 보고 새 페이지 추가 가능

### 위험 & 완화

| 위험 | 완화 |
|---|---|
| 일부 페이지가 끝까지 90% 미달 | 해당 페이지 수동 셀렉터 보강 (하이브리드 모드) |
| Dify 1.13 의 SSE 통합 한계 | 폴링 방식 폴백 또는 별도 작은 UI 페이지 |
| 운영 비용 예상 초과 | 페이지별 max_turn 차등 적용, 캐싱 (같은 페이지는 한번만 grounding) |
| 모델 업그레이드 영향 | 회귀 코퍼스가 모델 교체 시 회귀 검증 인프라로 자동 활용 |

### 추정 일정

**합계: 4-6주** (1명 엔지니어 풀타임, 일부 태스크 병렬화 가능 → 2명 시 3-4주)

| 주차 | 태스크 |
|---|---|
| W1 | T3.1 코퍼스, T3.2 도구 시작 |
| W2 | T3.2 완료, T3.3 multi-tab, T3.4 iframe |
| W3 | T3.5 스트리밍, T3.6 회복 |
| W4 | T3.7 대시보드, T3.8 비용 |
| W5-6 | T3.9 회귀 안정화, T3.10 문서 |

---

## 횡단 관심사 (Cross-Cutting Concerns)

### 로깅 표준

모든 단계의 신규 코드에 적용한다.

- 구조화 로그 (`structlog` 또는 JSON 라인)
- 필드: `timestamp, phase, run_id, turn?, tool?, status, duration_ms, tokens_in?, tokens_out?, error?`
- 로그 위치: `/data/logs/grounding.log`, `/data/logs/agent.log`
- 자동 회전 (50MB × 5)

### 메트릭 통합

기존 `llm_calls.jsonl` 과 호환되는 형식 확장.

| Phase | 추가 event_type |
|---|---|
| Phase 1 | `grounding_fetch` |
| Phase 2 | `agent_turn`, `tool_call` |
| Phase 3 | `corpus_run` |

### Feature Flag 정책

- 모든 신규 기능은 환경변수로 토글 가능
- 기본값은 OFF (역호환성)
- Phase 1 안정화 후 ON 전환
- Phase 2·3 도 동일 패턴

### 비용 통제

- LLM 호출당 max_tokens 강제
- 에이전트 실행당 비용 한도 (env var `AGENT_MAX_COST_USD`)
- 초과 시 graceful 종료 + 알림

### 에어갭 보존

- 모든 신규 의존성은 `requirements.txt` 에 핀 + Docker 빌드 시 사전 다운로드
- 외부 API 호출 0 (LLM 은 로컬 Ollama 만)
- 도메인 화이트리스트 (target_url 의 호스트만 허용)

---

## 단계 간 결정 게이트 (Decision Gates)

각 게이트에서 다음 중 하나 결정.

| 결정 | 의미 |
|---|---|
| **Go** | 다음 단계 진입 |
| **Iterate** | 현재 단계 보강 후 재평가 |
| **Pivot** | 아키텍처 재설계 (예: 외부 에이전트 → MCP 표준 채택) |
| **Stop** | 비용·효과 부적합, 이후 단계 보류 |

### Gate 1 — Phase 1 → Phase 1.5

- 셀렉터 정확도 75%+ 미달 → **Iterate** (가지치기 + 프롬프트 튜닝)
- 운영 안정성 미충족 → **Iterate**
- 통과 → **Go**

### Gate 1.5 — Phase 1.5 → Phase 2

- 모든 모델 90% 미달 + ReAct 폴백도 부적합 → **Stop** (DOM grounding 만으로 운영)
- 모델 교체 + 인프라 비용 비현실적 → **Pivot** (외부 에이전트 대신 사람-인-더-루프 강화)
- 통과 → **Go**

### Gate 2 — Phase 2 → Phase 3

- 파일럿 성공률 80% 미달 → **Iterate** (도구·프롬프트 보강)
- 운영 비용 시간당 한도 초과 → **Iterate** (캐싱·단축 전략)
- 통과 → **Go**

---

## 명시적 OUT 항목 (영구 범위 외)

이 로드맵은 다음을 다루지 않는다.

- 시각 회귀 테스트 (Percy·Applitools 영역)
- 부하·성능 테스트 (k6·JMeter)
- 접근성 (a11y) 자동 검증 (axe-core 영역)
- 외부 시스템 (이메일·SMS·결제 PG) 통합 검증
- A/B 테스트 분기 자동 인식
- 모바일 앱 자동화 (Appium 영역)
- 테스트 데이터 시드·cleanup 자동화 (DB 작업)
- LLM 자체 fine-tuning

이 항목들이 필요하면 별도 이니셔티브로 다룬다.

---

## 검증 방법

각 단계 종료 시 수행한다.

### Phase 1 검증

1. `ENABLE_DOM_GROUNDING=1` 환경에서 평가 페이지 10종 자동 실행
2. `tests/grounding_eval/report.html` 확인 — 신·구 경로 비교
3. 기존 회귀 테스트 30종 (`test/test_*.py`) 모두 통과
4. Healer 호출 빈도 메트릭 확인

### Phase R 검증

1. 호스트 에이전트 환경에서 `http://localhost:18092` 접속
2. 5종 샘플 페이지 녹화 → DSL 변환 → replay → 통과율 확인
3. 시나리오 A: 녹화 1건에 대해 "Generate Doc" → markdown 검토
4. 시나리오 B: 의도적으로 차이 있는 doc-DSL 업로드 → diff 리포트 확인
5. mac-agent·wsl-agent 양쪽 환경 검증

### Phase 1.5 검증

1. `tests/tool_calling_eval/run.sh` 실행 → 모델별 신뢰성 점수
2. 의사결정 문서 리뷰 + 합의

### Phase 2 검증

1. 에이전트 서비스 시작 (`docker exec ... supervisorctl start agent`)
2. 자체 인증 페이지에 5회 연속 탐색 요청
3. 생성된 DSL 을 executor 로 재실행 → 통과 확인
4. `agent_runs.jsonl` 메트릭 확인

### Phase 3 검증

1. 30종 코퍼스 자동 실행 (`./scripts/run_corpus.sh`)
2. 운영 대시보드 (`http://<host>:18091/dashboard`) 확인
3. Dify 챗 UI 에서 1종 페이지 탐색 → 진행 스트리밍 가시화 확인
4. 1주 운영 후 대시보드 통계 검토

---

## 추정 총 일정 & 리소스

### 단일 트랙 (1명 순차)

| Phase | 기간 |
|---|---|
| Phase 1 | 1.5-2주 |
| Phase R | 2.5-3주 |
| Phase 1.5 | 1주 (모델 교체 시 +1주) |
| Phase 2 | 3-4주 |
| Phase 3 | 4-6주 |
| **합계** | **12-16주 (~3-4개월)** |

### 병행 트랙 (2명 — Phase 1+R 동시)

```
주차:   1   2   3   4   5   6   7   8   9   10  11
엔지A:  [Phase 1     ][Phase 1.5][Phase 2          ][Phase 3              ]
엔지B:  [Phase R         ][............ Phase 2/3 지원, 시나리오 B 활용 ....]
```

| Phase | 기간 (병행) |
|---|---|
| Phase 1 | W1-W2 |
| Phase R | W1-W3 |
| Phase 1.5 | W3 |
| Phase 2 | W4-W7 |
| Phase 3 | W7-W11 |
| **합계** | **~11주 (~2.5개월)** |

### 추가 자원

- GPU (모델 교체 시 재산정)
- 협력사 staging 환경 접근 (Phase 3 코퍼스용)
- mac·wsl 호스트 환경 양쪽 (Phase R 검증용)
- 리뷰어 시간 (Phase 별 결정 게이트)

---

## 핵심 파일 변경 요약

| 파일 | Phase 1 | Phase R | Phase 2 | Phase 3 |
|---|---|---|---|---|
| `dify_client.py` | 수정 (grounding 통합) | — | 수정 (agent 분기) | — |
| `dify-chatflow.yaml` | 수정 (Planner 프롬프트) | — | 수정 (run_mode=agent 추가) | — |
| `zero_touch_qa/grounding/` | **신규** | 재사용 (옵션) | 재사용 | — |
| `zero_touch_qa/recording/` | — | **신규** (enricher, comparator) | — | — |
| `zero_touch_qa/converter.py` | — | 재사용 (변경 최소) | — | — |
| `zero_touch_qa/agent/` | — | — | **신규** | 확장 |
| `recording_service/` (host) | — | **신규** (전체) | — | — |
| `supervisord.conf` | — | — | 수정 (agent 서비스 등록) | — |
| `mac-agent-setup.sh` | — | 수정 (recording 부트스트랩) | — | — |
| `wsl-agent-setup.sh` | — | 수정 (recording 부트스트랩) | — | — |
| `Dockerfile` | 수정 (의존성?) | 수정 (volume 마운트) | 수정 (FastAPI) | — |
| `requirements.txt` | 수정 (tiktoken 등) | 수정 (fastapi, uvicorn — 호스트) | 수정 (fastapi, uvicorn) | — |
| `README.md` | 섹션 추가 | 섹션 추가 (Recording 모드) | 섹션 추가 | 섹션 확장 |
| `tests/` | 신규 (grounding_eval) | 신규 (recording) | 신규 (agent) | 신규 (corpus) |

---

## Recording ↔ Doc-based 트랙 시너지 활용

Phase R 이 Phase 2·3 와 만들어내는 추가 가치.

### 시너지 ① — Phase 2 자가 평가 데이터

Phase R 의 시나리오 B(의미 비교) 인프라를 Phase 2 에이전트의 **자가 평가** 에 재사용한다.

> 사용자 녹화 = 정답 액션 시퀀스 → 에이전트 출력과 자동 비교 → 품질 메트릭

Phase 2 의 파일럿 검증(T2.9) 에서 활용 가능.

### 시너지 ② — Phase 3 회귀 코퍼스 시드

Phase R 로 30종 페이지 각각에 대해 사용자 녹화 1회 → 골든 시나리오 자동 생성.

Phase 3 의 T3.1(코퍼스 + 골든 시나리오) 작업량을 **~50% 단축**.

### 시너지 ③ — Doc → 자동화 갭 가시화

시나리오 B 비교 결과를 운영 대시보드(Phase 3) 에 누적 → "기획서가 실제 UI 와 어디서 어긋나는가" 의 통계.

솔루션이 단순 자동화 도구를 넘어 **기획-구현 정합성 모니터링 도구** 로 격상.

---

## 다음 액션

이 로드맵 승인 후 즉시 시작 가능한 항목.

1. **Phase 1 T1.1** (DOM 인벤토리 스키마 설계) — 엔지니어 A 즉시 시작
2. **Phase R TR.1** (Recording 서비스 골격) — 엔지니어 B 즉시 시작 (병행)
3. **Phase 1.5 사전 작업** — 호스트 GPU 사양 확인 (병행 가능)
4. **Phase 3 코퍼스 후보 페이지 리스트업** (병행 가능, Phase R 시너지 ② 와 연계)
5. mac-agent·wsl-agent 환경 준비 확인 (Phase R 의 검증 환경)
