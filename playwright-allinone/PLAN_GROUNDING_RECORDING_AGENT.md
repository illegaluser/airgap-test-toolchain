# DOM Grounding · Recording · Browser Agent 통합 — 4트랙 구현 로드맵

---

## Context

`playwright-allinone/` 솔루션은 **기획문서 → 테스트 계획·시나리오 초안** 생성에는 우수하다. 그러나 **기획문서 → 실행 가능한 UI 자동화 테스트** 까지는 도달하지 못한다. 원인은 셀렉터 갭이다.

설계 문서(`PLAN_TEST_PLANNING_RAG.md` §1.2 lines 47-50) 가 이 한계를 명시적으로 인정하고 있다.

본 로드맵은 이 갭을 **두 축으로 동시에 공략한다**.

| 트랙 | 공략 방향 | 진행 |
| --- | --- | --- |
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
| --- | --- |
| Dify 버전 | **1.13.3** (Plugin Daemon 0.5.3-local) |
| MCP 지원 | ❌ 없음 |
| 챗플로우 노드 타입 | `start / if-else / llm / answer / knowledge-retrieval` 5종 — 도구·에이전트 노드 부재 |
| 사용 모델 | `gemma4:26b` (도구 호출 신뢰성 미검증) |
| Node.js | 22 LTS 컨테이너 내 존재 (`Dockerfile:193-204`) |
| LLM 컨텍스트 | `provision.sh:49` default **12288** 보수 출발. Test Planning RAG 트랙은 별도로 20480 채택(`PLAN_TEST_PLANNING_RAG.md:281`). 본 로드맵은 12288 그대로 시작하고, **Phase 1 첫 호출의 prompt+completion 토큰을 inline 측정** 해 마진 부족 시 20480 상향 결정 (T1.4 §"기본 한도" 참조) |
| Executor 모델 | `zero_touch_qa/executor.py` per-test 호출 (데몬 아님) |
| 핵심 통합 지점 | `dify_client.py:206-273` `generate_scenario()` |
| 호스트 GPU 사양 | **미확정** — Phase 0 사전 작업 (모델 후보별 VRAM 매칭에 필요) |
| Healer 호출 baseline | **본 개정에서 절대 baseline 미사용** — Phase 1 DoD 는 동일 카탈로그 페어(flag off vs on) 의 `llm_calls.jsonl` `kind=healer` 카운트 비교로 측정 |

---

## 전체 목표

**기획문서·화면정의서만으로 운영 URL 에 대한 실행 가능 UI 자동화 테스트를 생성한다.**

단, 시각 회귀·부하 테스트·a11y 자동 검증은 본 로드맵 범위 밖이다.

### 단계별 가치 단계 (Value Ladder)

#### Doc-based 트랙

| 단계 | 도달 능력 | 셀렉터 정확도 (1차) | 사람 개입 |
| --- | --- | --- | --- |
| **현재** | fixture HTML 한정, 운영 URL 불가 | ~20-30% | 필수 (셀렉터 수정) |
| **Phase 1 후** | 정적·공개 페이지 운영 URL 가능 | ~75-90% | 부분적 |
| **Phase 2 후** | 인증 페이지 1종 end-to-end 가능 | 동일 | 거의 불필요 (탐색 범위 내) |
| **Phase 3 후** | 30종 페이지 운영 안정성 확보 | 동일 + 회복 | 예외 케이스만 |

#### Recording 트랙

| 단계 | 도달 능력 | 사용자 시간 비용 |
| --- | --- | --- |
| **현재** | `playwright codegen` CLI 수동 실행 + 별도 변환 호출 | 5-10분 (학습 곡선 + CLI) |
| **Phase R 후** | 통합 Web UI 에서 클릭 한 번으로 녹화 → DSL 자동 산출 | 1-2분 (행동 시간만) |
| **Phase R + 결합 시나리오** | 녹화 → IEEE 829 역추정, 또는 doc-DSL 과 의미 비교 | 추가 30초 |

---

## Phase 0 — 사전 준비 (W0, 양 트랙 공통 선행)

> **한 줄 요약**: 시작 자체에 필요한 3건만 확정하고 즉시 본 작업 진입한다. 측정성 baseline 은 본 작업 중 inline 으로 수집.

기존 5태스크(토큰 예산 실측 / healer baseline / 페이지 카탈로그 / GPU 사양 / 의존성 PR)는 첫 산출물까지 시간을 늘렸다. 본 개정에서는 **시작 자체에 필요한 3건** 만 W0 로 두고, 측정성 작업은 본 작업 중 inline 또는 운영 비교(flag off vs on)로 흡수한다.

### T0.1 — 평가 페이지 카탈로그 (0.5-1시간)

Phase 1 / Phase R / Phase 3 가 공유할 페이지 ID 표. **에어갭 호환** 페이지만 우선 확정.

| 카테고리 | 수 | 출처 / 예 | 인증 |
| --- | --- | --- | --- |
| 자체 fixture | 5 | `test/fixtures/*.html` (file://) | 없음 |
| 자체 호스팅 | 5 | Jenkins `:18080`, Dify console `:18081`, Allure 리포트, Ollama UI | 일부 인증 |
| 미러/공개 SaaS (Phase 3) | 후속 | Phase 3 진입 시 결정 | — |

본 로드맵의 모든 단계는 위 카탈로그 ID(`P0-FX-01` … `P0-HS-05`) 를 참조한다. **에어갭 환경 한정**: 공개 SaaS 는 Phase 3 진입 시점에 사내 미러(`mirror.local`) 로 결정.

- **산출물**: `docs/eval-page-catalog.md` (페이지 ID, URL, 골든 시나리오 시작점)

### T0.2 — 호스트 GPU 사양 + 의존성 선처리 PR (0.5일)

#### GPU 사양 (10분 작업)

운영 호스트(또는 운영 후보) 에서 `nvidia-smi` 1회 + `df -h` 1회. 결과를 한 줄로 기록.

- **산출물**: `docs/host-gpu-spec.md` (모델, VRAM, CUDA 버전, 디스크 여유)

#### 의존성 선처리 PR + `--convert-only` 플래그 (4-5시간)

Phase 1 / Phase R 양쪽이 `requirements.txt` 와 호스트 venv 부트스트랩을 동시 수정하면 머지 충돌이 확실하다. 또한 R-MVP 의 컨테이너 CLI 위임을 위해 `--convert-only` 플래그가 선결 조건. 단일 PR 로 묶는다.

| 영역 | 변경 | 정당화 |
| --- | --- | --- |
| 컨테이너 `requirements.txt` | `tiktoken` 추가, `playwright>=1.51` 핀 확정 | grounding 토큰 가드 + codegen 호환성 |
| 호스트 `mac-agent`/`wsl-agent` 부트스트랩 venv | `fastapi`, `uvicorn`, `requests` 설치 | recording_service FastAPI |
| Docker 빌드 시 사전 다운로드 캐시 | 위 신규 패키지 wheel | 에어갭 보존 (§"에어갭 보존" 정책) |
| `zero_touch_qa/__main__.py` | `--convert-only` 플래그 추가 (T0.3 §"신규 플래그" 참조) | R-MVP TR.3 컨테이너 CLI 위임의 선결 조건 |

- **산출물**: 단일 PR + Jenkins fresh rebuild PASS 1회 (회귀 영향 0 입증, 기존 `--mode convert` 단독 동작은 보존)

### T0.3 — Recording 서비스의 호스트/컨테이너 경계 결정 (2-3시간)

평가에서 지적된 설계 모호성. **결정** 을 사전에 못박아 TR.3 코딩 중 막히는 일을 피한다.

`recording_service` (호스트 FastAPI) 가 `zero_touch_qa.converter` 를 어떻게 호출하나?

- **선택 A — 호스트에서 패키지 import** (X): PYTHONPATH·코드 동기화·의존성 설치 부담. 컨테이너와 환경 분기 발생.
- **선택 B — 컨테이너 CLI 위임** (✅ 채택): 호스트는 codegen subprocess 실행 + 결과 파일을 공유 볼륨에 저장. 변환은 컨테이너 CLI 호출.

#### 컨테이너 CLI 신규 플래그 — `--convert-only` 도입 필요

기존 `--mode convert` 는 `_prepare_scenario()` 가 변환 결과를 return 한 뒤 `main()` 의 공통 흐름이 그대로 `executor.execute()` 까지 진행한다 (`__main__.py:117-119`). 즉 변환 후 헤디드 브라우저 실행이 자동으로 따라붙는다. R-MVP 의 "변환만" 요구와 충돌.

또한 기존 `convert` 분기는 `_validate_scenario()` 호출도 누락(`__main__.py:360-365`).

**필수 작업**: 본 plan 진입 전 `__main__.py` 에 다음 변경 선반영 (의존성 선처리 PR 과 함께 T0.2 단계에서 머지).

| 항목 | 변경 |
| --- | --- |
| CLI 인자 | `--convert-only` boolean 플래그 추가 (default `False`) |
| 동작 | `--mode convert --convert-only` 일 때: `convert_playwright_to_dsl()` → `_validate_scenario()` → `scenario.json` 저장 후 **즉시 종료** (executor 미실행) |
| 종료 코드 | 변환 + 검증 통과 = 0, 실패 = 1 (stderr 에 사유) |
| 출력 경로 | `<output_dir>/scenario.json` (기존 `convert_playwright_to_dsl` 의 두 번째 인자) |
| 기존 `--mode convert` (단독) | 동작 보존 — 기존 사용자 영향 0 |
| 오용 가드 | `--convert-only` 가 `--mode convert` 외 모드와 함께 들어오면 `_prepare_scenario()` 진입 전 즉시 exit 1. Dify 호출·재시도·error report 생성을 하지 않는다 |

#### 계약 (호스트 ↔ 컨테이너)

| 항목 | 값 |
| --- | --- |
| 호스트 → 컨테이너 호출 방식 | `docker exec <container_name> python -m zero_touch_qa --mode convert --convert-only --file <shared_path>` |
| 호스트 측 경로 | `~/.dscore.ttc.playwright-agent/recordings/<id>/` (env `RECORDING_HOST_ROOT`) |
| 컨테이너 측 경로 | `/recordings/<id>/` (env `RECORDING_CONTAINER_ROOT`). **`/data` 가 named volume 이라 그 위에 nested bind 는 docker 보장이 약함 → 별도 마운트 포인트 채택** (TR.8 결정) |
| build.sh 마운트 | `-v "$HOST_RECORDINGS_DIR":/recordings:rw` |
| 변환 결과 | 공유 경로의 `scenario.json` (컨테이너 가 쓰고 호스트 가 읽음) |
| 검증 | `_validate_scenario()` 가 `--convert-only` 분기 안에서 호출 — 실패 시 exit 1 + stderr |
| 에러 채널 | docker exec 의 exit code + stderr 캡처 |

- **산출물**: `__main__.py` 의 `--convert-only` 플래그 패치 (T0.2 의존성 PR 과 함께 머지) + 본 결정 표
- **2026-04-28 보강 검증**: `chat --convert-only` 오용 시 Dify retry 로 빠지던 결함을 조기 가드로 수정. `test_sprint2_runtime.py` 에 subprocess 회귀를 추가해 exit 1, 2초 미만 종료, `Retry` 로그 부재를 검증.

### Phase 0 결정 게이트 — Phase 1·R 진입 조건

- [ ] T0.1 카탈로그 5+5 페이지 ID 확정
- [ ] T0.2 의존성 선처리 PR 머지 + Jenkins 회귀 PASS
- [ ] T0.3 호스트/컨테이너 호출 계약 확정

추정 일정: **W0 합계 0.5-1일** (병행 진행 시 4시간).

> **본 작업 중 inline 으로 흡수되는 항목** (W0 제외):
>
> - **토큰 예산** — Phase 1 첫 호출에서 prompt/completion 토큰 측정. 12288 마진 부족 시 그때 20480 상향
> - **Healer baseline** — 절대값 baseline 미수집. Phase 1 DoD 는 "feature flag off vs on 1주 운영 비교 (2배 이상 감소)" 형태로 측정
> - **Phase 1.5 모델 후보 VRAM 매칭** — Phase 1.5 진입 시 T0.2 의 GPU 사양 표를 참조하면 충분
> - **`event_type` 필드 schema** — 본 plan 의 표기는 실제 코드 (`dify_client.py:389,417` 의 `kind: planner|healer`) 기준. 이전 표기 `event_type=heal` 은 본 개정에서 일괄 정정

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

**채택안 — `srs_text` prepend 패턴** (chatflow YAML 미수정).

기존 `__main__.py:367-372` 의 doc 모드가 이미 동일 패턴을 사용한다 — 파일 텍스트를 `srs_text` 앞에 합쳐 Planner 에 전달. grounding 도 같은 채널을 재사용하면 chatflow YAML 변경·기존 모드(chat/convert/execute) 회귀 부담이 사라진다.

```text
[변경 전]
dify_client.py:generate_scenario()
  ├─ inputs = {srs_text, target_url, api_docs, ...}
  └─ POST /v1/chat-messages (Dify Planner)
       └─ Planner LLM (셀렉터 추측)

[변경 후]
dify_client.py:generate_scenario()
  ├─ if dom_grounding_enabled and target_url:
  │     dom_inventory = grounding.fetch(target_url)
  │     srs_text = serialize_block(dom_inventory) + "\n\n" + srs_text
  ├─ inputs = {srs_text (prepended), target_url, api_docs, ...}
  └─ POST /v1/chat-messages
       └─ Planner LLM (셀렉터를 prepended 인벤토리 블록에서 선택)
```

직렬화 블록은 다음 마커로 감싼다 — Planner 가 자유 추측을 자제하도록 자연어 가이드.

```text
=== DOM INVENTORY (target_url) ===
- {role=button, name="로그인", selector_hint=getByRole('button', {name: '로그인'})}
- {role=textbox, name="이메일", selector_hint=getByRole('textbox', {name: '이메일'})}
- ...
=== END INVENTORY ===

(SRS 본문 이어서)
```

### 신규 모듈

| 파일 | 역할 | 추정 LOC |
| --- | --- | --- |
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

##### 첫날 5분 smoke check (선행)

코드 작성 전 컨테이너 + 호스트 양쪽에서 다음 REPL 검증.

에어갭 정책 일관성을 위해 **로컬 fixture** (T0.1 카탈로그의 `P0-FX-*`) 로만 검증한다.

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    # T0.1 카탈로그의 P0-FX-01 fixture 사용 (예: 로그인 폼)
    page.goto("file:///app/test/fixtures/login.html")
    snap = page.accessibility.snapshot()  # ← 핵심 확인
    print(type(snap), bool(snap))
    locs = page.locator("[role]").all()    # ← 핵심 확인
    print(len(locs))
```

`snap` 이 dict 로 반환 + `locs` 가 비어있지 않으면 통과. 실패 시 Playwright 버전 핀 재검토 (T0.2 의존성 PR 에 반영).

> 공개 URL(예: `https://playwright.dev`) 검증은 개발 호스트에서만 보조로 수행. 운영 호스트는 에어갭 환경이라 외부 URL 미접근.

##### 본 구현

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

##### 기본 한도 (보수적 출발)

- **기본 한도 1500 토큰** — 현재 `OLLAMA_CONTEXT_SIZE=12288` 기준 안전 마진. RAG 트랙 동시 사용 가정.
- env var `GROUNDING_TOKEN_BUDGET` 으로 운영자 override 가능
- Phase 1 첫 호출에서 prompt+completion 토큰을 측정 → 마진이 5000+ 남으면 한도 3000 으로 상향 (실측 후 `OLLAMA_CONTEXT_SIZE` 20480 상향과 동시 결정 가능)

##### 한도 초과 시 단계별 축소

1. 비인터랙티브 요소 제거 (heading·landmark)
2. 가시성 외 요소 제거
3. 우선순위 낮은 인터랙티브 (option·menuitem) 제거
4. 인터랙티브 role 별 상위 N개만 유지 (button/link/textbox 각 10개)
5. 여전히 초과 시 경고 로그 + truncate + `inventory.truncated=true` 메타데이터

- **산출물**: `budget.py` + 한도 테스트

#### T1.5 — `dify_client.py` 통합 (1일)

- `generate_scenario()` 시그니처에 `enable_grounding: bool = False` 추가
- Feature flag (`ENABLE_DOM_GROUNDING=1`) 로 기본값 토글
- **인벤토리 주입은 `srs_text` prepend 방식** (§"아키텍처" 의 마커 블록) — chatflow YAML 미수정. 기존 doc 모드의 파일 prepend 패턴(`__main__.py:367-372`) 과 동일 채널
- 추출 실패 시 조용히 스킵 (기존 경로 유지) + 로그
- 추출 시간·토큰 수 메트릭 기록 (`llm_calls.jsonl` — `kind=planner` 레코드에 `grounding_inventory_tokens` 필드 추가)
- **산출물**: `dify_client.py` 패치

#### T1.6 — Planner 가이드 문구 (0.5일)

prepend 블록 안에 자연어 가이드를 함께 넣는다 (chatflow YAML 시스템 프롬프트는 건드리지 않는다).

```text
=== DOM INVENTORY (target_url) ===
(요소 목록)
=== END INVENTORY ===

위 인벤토리는 target_url 의 실제 DOM 에서 추출된 요소 목록이다.
- 셀렉터는 가능하면 위 인벤토리의 selector_hint 를 그대로 사용한다.
- 인벤토리에 없는 요소가 필요하면 출력에 `(요소 미발견: <설명>)` 마커를 남긴다.
- 우선순위: getByRole(role, {name}) > getByText > getByTestId > CSS

(SRS 본문 이어서)
```

기존 chat / convert / execute 모드는 `enable_grounding=False` 가 default 라 prepend 발생하지 않음 → 회귀 영향 0.

- **산출물**: 가이드 블록 템플릿 + 단위 테스트 (block 형식 검증)

#### T1.7 — 효과 측정 하니스 (2일)

평가 페이지는 **Phase 0 T0.1 통일 카탈로그**(`docs/eval-page-catalog.md`) 의 자체 fixture 5 + 자체 호스팅 5 = **10종** 을 우선 대상.

각 페이지에 골든 시나리오(이상적 DSL) 정의 — 카탈로그의 ID 와 1:1 대응.

**자동 수집 메트릭**

- 첫 시도 셀렉터 정확도 — 분류 기준은 §"DoD" 참조 (정확 / 부분 / 실패)
- Healer 호출 빈도 — feature flag off vs on 페어 비교 (절대값 baseline 미사용)
- Planner 응답 시간 (페이지 로딩 시간 분리 측정)
- 토큰 사용량 (입력·출력·grounding 인벤토리 분리)

신·구 경로 비교 리포트 자동 생성 (`flag=off` 1회 + `flag=on` 1회 페어 실행).

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
2. `dify_client.py` 통합 패치 (srs_text prepend)
3. 평가 하니스 + flag off/on 페어 비교 리포트 (페이지 10종)
4. README 섹션
5. Feature flag 설계 문서

### 성공 기준 (Definition of Done)

#### 셀렉터 분류 정의 (메트릭 산정 기준)

페이지당 골든 시나리오의 각 step 의 첫 시도 셀렉터를 다음 3분류로 라벨링한다.

| 분류 | 정의 |
| --- | --- |
| **정확 (exact)** | LLM 출력 셀렉터가 의미적으로 골든과 동등 — `getByRole(role, {name})` 의 (role, name) 일치 또는 `getByText` 의 텍스트 일치. CSS 셀렉터의 경우 동일 요소를 결정적으로 가리킴 |
| **부분 (partial)** | 동일 요소를 가리키지만 우선순위 낮은 형식 사용 — 예: 골든이 `getByRole` 인데 출력이 `getByTestId` 또는 CSS. 또는 의미는 같으나 name 의 부분 일치 (substring) |
| **실패 (fail)** | 다른 요소를 가리키거나 존재하지 않는 셀렉터. Healer 가 호출되어야 하는 케이스 |

페이지의 셀렉터 정확도 = (정확 step 수) / (전체 step 수). "정확" 만 카운트, "부분" 은 별도 보조 지표.

#### DoD 항목

- ✅ 평가 페이지 10종 중 **8종 이상** 에서 첫 시도 셀렉터 정확도 **75%+** 달성 (분류는 위 정의)
- ✅ Healer 호출 빈도 — 동일 카탈로그 페어 실행 시 **flag=on 이 flag=off 대비 1/3 이하** (절대 baseline 미사용. `llm_calls.jsonl` 의 `kind=healer` 레코드 카운트로 비교)
- ✅ Planner 응답 시간 증가 **+10초 이내** (페이지 로딩 시간은 별도 분리 측정, 그라운딩 자체 부담은 +5초 이내)
- ✅ 토큰 예산 초과 페이지가 평가 셋의 10% 이하 (T1.4 의 1500/3000 단계 기준)
- ✅ Feature flag off 시 grounding 경로 자체는 분기되지 않으므로 chat/convert/execute 동작 변화 0. 단 Phase 0 의 의존성 PR (`__main__.py` `--convert-only` 추가 + requirements 변경) 의 회귀 영향은 **Phase 0 PR 단계의 Jenkins fresh rebuild PASS** 로 보장 (T0.2 산출물 참조)
- ✅ Feature flag on + 인벤토리 None 케이스(추출 실패)에서 prepend 미발생 + 정상 종료 (graceful degradation)
- ✅ 추출 실패 시 `inventory.error` 메타데이터가 `llm_calls.jsonl` 의 `kind=planner` 레코드에 기록되어 운영자 진단 가능

### 위험 & 완화

| 위험 | 영향 | 완화 |
| --- | --- | --- |
| SPA 페이지의 초기 DOM 비어있음 | 추출 실패 | `wait_until=networkidle` + 명시적 `wait_for_selector` 옵션 |
| 토큰 예산 만성 초과 | grounding 무력화 | 페이지별 sampling (folded summary), 컨텍스트 윈도우 확대 |
| 의미 HTML 부재한 페이지 | grounding 효과 없음 | `data-testid`·id·placeholder fallback 명시 |
| Playwright 호출이 에이전트 환경에서 실패 | 통합 차단 | 별도 프로세스 분리 옵션 (subprocess) 설계 |

### 추정 일정

**합계: 1.5주** (1명 엔지니어 풀타임). T1.6 의 chatflow YAML 회귀 검증 제거 + srs_text prepend 단순화로 종전 1.5-2주에서 0.5-1일 단축.

| 주차 | 태스크 |
| --- | --- |
| W1 D1 | T1.1 스키마 + T1.2 추출기 (a11y API 5분 smoke check 포함) |
| W1 D2-3 | T1.2 완료 + T1.3 pruner |
| W1 D4 | T1.4 budget + T1.5 통합 (srs_text prepend) + T1.6 가이드 블록 |
| W1 D5 ~ W2 D1 | T1.7 평가 하니스 |
| W2 D2-3 | T1.8 파일럿 + T1.9 문서 |

### 진행 로그

| 날짜 | 단계 | 비고 |
| --- | --- | --- |
| 2026-04-28 (오전) | T1.1~T1.6 | grounding 모듈 (`schema/extractor/pruner/budget/serializer`) + `dify_client._prepend_dom_inventory` + `__main__.py` env wiring + `test/test_grounding.py` 17 PASS |
| 2026-04-28 (오후) | T1.7 | 골든 6종 (`P0-FX-01..05` + `P0-HS-05`) + comparator (`grounding_eval/classifier.py`, `compare.py`) + 러너 (`scripts/run_grounding_eval.sh`) + 단위 테스트 28 PASS |
| 2026-04-28 (오후) | T1.8 | fixture 5종 결정론적 파일럿 (`test/test_grounding_pilot.py` 11건). fixture/AX 트리 검증으로 골든 정정 (`fill.html` label trailing-colon, `select.html` 동일, `full_dsl.html` search-input 라벨 부재 → CSS fallback). **결정론 버그 수정**: `fit_to_budget` 의 stage 1-4 `_within` 체크가 `truncated=False` 기준으로 토큰을 재 truncated 마커 줄(~30 토큰) 누락 → 한도 초과 채로 통과. 진입 시점에 `inv.truncated=True` 로 세팅해 일관성 확보 |
| 2026-04-28 (오후) | T1.9 | README §3.12 추가 (토글/한도/효과 측정/트러블슈팅/모듈 책임). 현재 구현 상태 표에 Phase 1 행 추가 |

### 결정 게이트 — Phase 1 → Phase 1.5

다음을 모두 충족해야 Phase 1.5 진입.

- [ ] DoD 7개 항목 모두 통과
- [ ] 평가 페이지 10종 페어(off/on) 비교 1주 운영 (예외 없음)
- [ ] Phase 2 의 비용·일정 추정에 필요한 데이터 확보 (특히 인증 영역에서 grounding 단독으로 안 되는 케이스 패턴 식별)

---

## Phase R — Recording-based Test Generation

> **한 줄 요약**: 호스트 GUI 환경에서 사용자 행동을 녹화하면 14-DSL 로 자동 변환되는 통합 UX 를 제공한다. Phase 1 과 병행 진행.

### 목표

호스트 에이전트(mac/wsl) 환경에서 사용자가 실제 브라우저 액션을 수행하면 14-DSL JSON 으로 자동 변환되는 **통합 레코딩 UX** 를 제공한다.

추가로 Recording ↔ Doc-based 두 트랙의 산출물을 결합하는 시너지 기능을 구현한다.

- **시나리오 A**: Recording → Doc 역추정
- **시나리오 B**: Doc-based ↔ Recording 비교

### 범위 — MVP / Plus 2 트랙 분리

평가 의견 채택. 첫 유효 산출물(녹화→변환→저장) 까지 시간을 단축하기 위해 MVP 와 Plus 를 분리한다. **MVP 만으로도 독립 가치 제공** — Plus 는 별도 결정 게이트 후 진행.

| 트랙 | 목적 | 포함 태스크 | 기간 |
| --- | --- | --- | --- |
| **R-MVP** | 사용자 행동 → 14-DSL 자동 산출 (회귀 시드용) | TR.1 / TR.2 / TR.3 / TR.4 / TR.8 / TR.9 / TR.10 | **1.5-2주** |
| **R-Plus** | 결합 시나리오 + replay (별도 게이트) | TR.5 (enricher) / TR.6 (comparator) / TR.7 (replay) | 2-2.5주 (선택) |

#### R-MVP IN

- Host agent 위에서 동작하는 Recording 서비스 (Python, FastAPI)
- `playwright codegen` subprocess 래핑 (검증된 MS 엔진 사용)
- 컨테이너 CLI 위임으로 codegen 출력 → 14-DSL JSON 변환 (Phase 0 T0.3 계약)
- 단순 Web UI — 시작/중지/결과 보기 + Assertion 추가 (verify/mock_*)
- Recording 결과 영속화 (host ↔ container 공유 디렉토리)
- mac-agent / wsl-agent 부트스트랩 통합

#### R-Plus IN (별도 게이트)

- **결합 시나리오 A**: Recording → IEEE 829-lite 테스트 계획서 LLM 역추정 (TR.5)
- **결합 시나리오 B**: Doc-based DSL ↔ Recording DSL 의미 비교 + HTML diff (TR.6)
- 녹화 직후 executor 재실행으로 DSL 검증 (TR.7 — round-trip)

#### OUT (양 트랙 공통)

- 컨테이너 단독 헤드리스 녹화 — 호스트 GUI 필수 결정으로 제외
- 커스텀 JS 오버레이·자체 셀렉터 생성기 — codegen 엔진에 위임
- 동시 다중 사용자 세션 — 단일 세션 우선
- 시각적 DSL 편집기 — 사용자가 DSL 직접 편집
- 모바일·터치 이벤트 녹화

### 아키텍처

#### 기본 Recording 흐름 (R-MVP)

```text
[호스트]                                          [컨테이너]
사용자 → Recording Web UI (http://localhost:18092)
          ↓ POST /recording/start {target_url}
       Recording Service (호스트 데몬, port 18092)
          ├─ 공유 디렉토리 준비:
          │     ~/.dscore.ttc.playwright-agent/recordings/<id>/
          │     (컨테이너 마운트: /recordings/<id>/)
          └─ subprocess.Popen([
                'playwright', 'codegen', target_url,
                '--target=python',
                '--output', '<공유경로>/original.py'
             ])
             ↓
          Playwright Inspector + 헤디드 Chromium (호스트 GUI)
             ↓
          사용자가 직접 페이지 조작 (클릭/입력/네비게이션)
             ↓
       Recording Web UI: "Stop" 클릭
          ↓ POST /recording/stop/<id>
       Recording Service
          ├─ subprocess SIGTERM (codegen 정상 종료)
          ├─ original.py 가 공유 디렉토리에 기록됨
          └─ docker exec <container> python -m zero_touch_qa \
                --mode convert --convert-only \
                --file /recordings/<id>/original.py
                                               ─────────────────→
                                          [컨테이너 측 처리]
                                          convert_playwright_to_dsl()
                                              ↓
                                          _validate_scenario()
                                              ↓
                                          /recordings/<id>/scenario.json 저장
                                              ↓
                                          exit 0 (변환만, executor 미실행)
                                          ←─────────────────
       Recording Service
          ├─ exit 0 확인
          ├─ <공유경로>/scenario.json 읽기
          └─ UI 에 결과 JSON 표시
             ↓
       사용자: Assertion 추가 (verify/mock_*) → 최종 scenario.json 저장
```

> **Replay (executor 재실행) / Generate Doc / Compare with Doc-DSL 은 R-Plus 트랙** — 별도 게이트 통과 후 활성화. 본 다이어그램에는 포함되지 않는다.

#### 결합 시나리오 A — Recording → Doc 역추정 (R-Plus)

```text
Recording 결과 → "Generate Doc" 버튼
  ↓ POST /recording/{id}/enrich
  → Ollama 직접 호출
    (system: "다음 DSL 시퀀스를 IEEE 829-lite 계획서로 역추정")
  → 입력: recorded DSL + 페이지 컨텍스트 (URL, title, optional DOM 인벤토리)
  → 출력: 테스트 계획서 markdown (목적/범위/단계/예상결과)
  → 영속화: {id}/doc_enriched.md
  → UI 표시 + 다운로드
```

#### 결합 시나리오 B — Doc ↔ Recording 비교 (R-Plus)

```text
Recording 결과 → "Compare with Doc-DSL" 버튼
  ↓ POST /recording/{id}/compare {doc_dsl: ...}
  → 의미 시퀀스 추출: [(action, role, name), ...]
  → LCS (longest common subsequence) 정렬
  → 분류: 일치 / 불일치 (값만 다름) / 누락 / 추가
  → HTML diff 리포트 생성
  → 영속화: {id}/doc_comparison.html
  → UI 표시 + 다운로드
```

### 신규 모듈

| 위치 | 파일 | 역할 | 추정 LOC |
| --- | --- | --- | --- |
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
| --- | --- | --- |
| POST | `/recording/start` | 녹화 시작 (input: target_url, planning_doc_ref?) |
| POST | `/recording/stop/{id}` | 녹화 종료 + 변환 |
| GET | `/recording/sessions` | 세션 목록 |
| GET | `/recording/sessions/{id}` | 세션 상세 (DSL, 메타데이터) |
| POST | `/recording/sessions/{id}/replay` | executor 재실행 — **R-Plus only** (TR.7) |
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

#### TR.3 — 컨테이너 CLI 위임 변환 (1일)

Phase 0 T0.3 의 호스트/컨테이너 경계 결정에 따라, 호스트는 `converter.py` 를 직접 import 하지 않는다. 변환은 **컨테이너 CLI 위임**.

```bash
docker exec <container_name> python -m zero_touch_qa \
  --mode convert \
  --convert-only \
  --file /recordings/<id>/original.py
```

- `--convert-only` 플래그는 Phase 0 T0.2 에서 컨테이너 CLI 에 선반영됨 (T0.3 §"신규 플래그" 참조)
- 호스트: `original.py` 를 공유 볼륨 (`/recordings/<id>/`) 에 저장 + `docker exec` 호출
- 컨테이너: 동일 경로의 파일을 읽어 `convert_playwright_to_dsl()` → `_validate_scenario()` → `scenario.json` 저장 후 **즉시 종료** (executor 미실행)
- 컨테이너 CLI: `--convert-only` 를 `convert` 외 모드와 함께 받으면 Dify 호출 전에 즉시 exit 1. Recording 서비스는 이를 호출 계약 위반으로 UI 에 표시한다
- 호스트: exit 0 확인 후 `scenario.json` 읽어 UI 에 표시

##### 검증 실패 시

- 원본 .py 보존 (`<id>/original.py`)
- docker exec stderr 캡처 후 UI 에 노출
- 재시도 옵션 제공

변환된 DSL 의 step 수가 0 이면 사용자 경고.

- **산출물**: 변환 통합 + 회귀 테스트

#### TR.4 — Web UI (간단) (3.5일)

단일 HTML 페이지 + vanilla JS (프레임워크 없음).

**UI 구성**

- **입력 폼**: target_url, optional planning_doc_ref (텍스트)
- **액션 버튼 (MVP)**: [Start Recording] [Stop]
- **액션 버튼 (Plus)**: [Replay] [Generate Doc] [Compare with Doc-DSL]
- **진행 상태**: 현재 세션 ID, 경과 시간, 캡처된 액션 수 (폴링)
- **결과 영역**: DSL JSON preview (syntax-highlight), 메타데이터, 결합 시나리오 결과(Plus)
- **세션 목록**: 사이드바 또는 하단 테이블
- **Assertion 추가 영역 (MVP)**: 녹화 종료 후 verify / mock_status / mock_data step 을 사용자가 수동 추가 (드롭다운 + selector hint + value). codegen 이 emit 하지 않는 14-DSL 액션을 보충

MVP 단계에서 Plus 버튼들은 비활성화 (회색) 로 표시 + 호버 시 "R-Plus 게이트 통과 후 활성화" 툴팁.

위치: 호스트의 `http://localhost:18092/`. **장식 최소화** — 기능 우선.

- **산출물**: 단순 UI + 사용성 테스트 (수동)

#### TR.5 — 결합 시나리오 A: Recording → Doc 역추정 (2.5일) — **R-Plus**

LLM 호출 (Ollama 직접 호출, Dify 챗플로우 우회).

##### 시스템 프롬프트 + few-shot (3개)

```
당신은 QA 엔지니어다. 다음 사용자 액션 시퀀스(14-DSL)는 실제 사용자가
웹 페이지에서 수행한 행동을 기록한 것이다. 이 시퀀스로부터 IEEE 829-lite
형식의 테스트 계획서(목적, 범위, 사전조건, 단계, 예상 결과, 검증 기준)
를 역추정하여 작성하라.
```

few-shot 예시 3종을 시스템 프롬프트에 임베드한다 (gemma4:26b 가 IEEE 829 형식 지식 부족).

| ID | 시드 시나리오 | 골든 IEEE 829-lite |
| --- | --- | --- |
| FS-A1 | 로그인 (navigate → fill username → fill password → click submit → verify dashboard) | 인증 흐름 계획서 — 사전조건 / 단계 / 예상 결과 명시 |
| FS-A2 | CRUD 생성 (navigate list → click create → fill 폼 5필드 → submit → verify 생성된 항목) | 데이터 입력 검증 계획서 — 경계값·필수값 항목 |
| FS-A3 | 다단계 검색 (search → filter dropdown → sort → pagination → verify 결과 건수) | 탐색 시나리오 계획서 — 조합 조건 명시 |

##### 평가 rubric (사람 평가 4/5 의 의미 정의)

| 점수 | 기준 |
| --- | --- |
| 5 | 모든 6 섹션(목적/범위/사전조건/단계/예상결과/검증기준) 완비 + 단계 누락 0 + 환각 0 |
| 4 | 6 섹션 완비 + 단계 누락 1 이내 + 환각 0 |
| 3 | 1개 섹션 누락 또는 단계 누락 2 + 환각 0 |
| 2 | 2개 섹션 누락 또는 환각 1건 |
| 1 | 3개 이상 섹션 누락 또는 환각 2건+ |

평가자 2명 독립 채점 → 평균. 합의 안 되면 (>1점 차이) 토론 후 재채점.

- 입력: recorded DSL + 페이지 컨텍스트 (URL, title, optional Phase 1 인벤토리)
- 출력: Markdown 테스트 계획서
- 영속화: `<id>/doc_enriched.md`
- Web UI 에 "Generate Doc" 버튼으로 노출
- 토큰 예산 가드: 입력 DSL + 인벤토리 + few-shot 합계 토큰 측정 — `OLLAMA_CONTEXT_SIZE` 의 50% 초과 시 인벤토리 생략

- **산출물**: `enricher.py` + few-shot 본문 + 평가 rubric + 5개 샘플(평가 페이지 카탈로그에서 선정)에 대한 2인 평가 결과

#### TR.6 — 결합 시나리오 B: Doc ↔ Recording 비교 (3.5일) — **R-Plus**

##### 입력 비대칭 명시 (필수 전제)

`playwright codegen` 은 사용자 행동만 emit 한다. 14-DSL 의 다음 액션은 codegen 이 자연 발생시키지 않는다.

| 액션 | codegen emit | TR.6 처리 |
| --- | --- | --- |
| navigate · click · fill · press · select · check · hover · upload · drag · scroll | ✅ | LCS 정렬 대상 |
| **verify** | ❌ assertion 미생성 | **정렬 대상 제외** — 단 TR.4 의 Assertion 추가 UI 로 사용자가 후속 추가했다면 정렬 대상 포함 |
| **mock_status / mock_data** | ❌ `page.route` 미생성 | **정렬 대상 제외** — 마찬가지로 사용자 추가 시만 포함 |
| **wait** | △ 명시적 wait 만 | 보조 지표 (정렬 결과에 영향 없음) |

따라서 비교는 **2단계** 로 수행한다. (1) 양쪽 공통 가능 액션만 추출해 LCS 정렬, (2) doc-DSL 에만 있는 verify/mock_* 는 "녹화 외 의도" 카테고리로 별도 표시 (누락이 아님).

**의미 시퀀스 정규화**

- 각 DSL step → `(action_type, semantic_target_role, semantic_target_name, value?)`
- 페이지 URL 변화 추적

**비교 알고리즘**

- 1단계: 정렬 대상 액션만으로 LCS (longest common subsequence) 산출
- 2단계: doc 의 verify/mock_* 를 "녹화 외 의도(intent-only)" 로 분리 표시
- 분류: 정확한 일치 / 의미는 같으나 값만 다름 / 누락 / 추가 / **녹화 외 의도** (5분류)
- role+name 매칭은 fuzzy (textdistance·difflib ratio, 임계값 0.7 — 골든 페어 10세트로 튜닝)

**HTML diff 리포트**

- 좌(doc-DSL) vs 우(recording-DSL) 사이드바이사이드
- 색상 코딩 (녹/적/회/노 + 5번째 분류용 `intent-only` 회색 + italic)
- 각 차이에 대한 자연어 설명
- 헤더에 비대칭 안내 문구 — "녹화는 사용자 행동만 캡처합니다. doc-DSL 의 verify/mock_* 는 비교 대상에서 제외되며 별도 표시됩니다."

**입력 옵션**

- doc-DSL JSON 직접 업로드
- 또는 chat 세션 ID 로 doc-DSL 가져오기

영속화: `<id>/doc_comparison.html`

- **산출물**: `comparator.py` + 비교 알고리즘 단위 테스트 + 임계값 튜닝 데이터 + 샘플 리포트 + 비대칭 안내 문구 표준화

#### TR.7 — Replay 검증 통합 (1일) — **R-Plus**

녹화 직후 또는 사용자 요청 시 DSL 을 executor 로 재실행.

**호스트 → 컨테이너 통신** (executor 가 컨테이너 내부)

- 옵션 A: 호스트가 컨테이너의 executor REST 엔드포인트 호출 (executor 에 추가 필요)
- 옵션 B: 호스트가 ssh/docker exec 로 executor CLI 호출
- **권장**: 옵션 A (Phase 2 의 agent service 와 일관성)

결과 (PASS/FAIL/HEALED) 를 UI 에 표시. 실패 단계 강조 + healing 옵션.

- **산출물**: replay 통합 + 케이스 테스트

#### TR.8 — Session 영속화 + 호스트↔컨테이너 공유 (3일)

**호스트 영속화 디렉토리**: `~/.dscore.ttc.playwright-agent/recordings/<id>/`

| 파일 | 내용 |
| --- | --- |
| `original.py` | codegen 원본 |
| `scenario.json` | 변환된 14-DSL |
| `metadata.json` | target_url, started_at, ended_at, action_count, planning_doc_ref? |
| `doc_enriched.md` | 시나리오 A 결과 (있는 경우) |
| `doc_comparison.html` | 시나리오 B 결과 (있는 경우) |
| `replay_result.json` | replay 결과 (있는 경우) |

**컨테이너 ↔ 호스트 공유**

- 호스트의 recordings 디렉토리를 컨테이너에 마운트 (volume)
- 컨테이너 내 executor 가 동일 경로로 접근
- **인프라 변경 범위**: `build.sh` (또는 `docker run` 옵션) + `supervisord.conf` (recording_service 아님 — executor 측 mount path env). Sprint 4 운영 결함 패턴(EADDRINUSE 3000 / dify-web PID1 재부모화)을 감안해 fresh rebuild 회귀 1회 포함

세션 목록·검색·삭제 API 제공.

- **산출물**: 영속화 + 마운트 설정 + fresh rebuild 회귀 PASS 증빙

#### TR.9 — 호스트 에이전트 통합 (mac/wsl) (3일)

`mac-agent-setup.sh`·`wsl-agent-setup.sh` 에 다음을 추가한다.

- Python venv 에 `fastapi`, `uvicorn`, `requests` 설치 (호스트측 deps — Phase 0 선처리 PR 과 일치)
- recording_service 코드 호스트로 배포
- 서비스 자동 기동 (mac: launchd, wsl: systemd 또는 nohup)
- 헬스체크
- recordings 디렉토리 자동 생성
- 컨테이너에 호스트 IP·포트 알림 (`RECORDING_SERVICE_URL=http://host.docker.internal:18092`)
- WSLg / `host.docker.internal` 양쪽 환경에서 동작 검증 — wsl 의 X-server fallback 경로 별도 시간 배정

- **산출물**: 에이전트 스크립트 패치 + mac/wsl 동작 검증 리포트

#### TR.10 — 문서화 + 운영 가이드 + Jenkins 통합 (1.5일)

##### README.md §"Recording 모드" 추가

- 사용 절차 (호스트 에이전트 환경 가정)
- Web UI 스크린샷 (실제 동작 후 캡처)
- 결합 시나리오 사용법

##### docs/recording-troubleshooting.md

- playwright 명령 못 찾음
- 헤디드 Chromium 안 뜸
- 변환 실패 시 디버깅

##### Jenkins 잡 페이지 → Recording UI 링크 (운영자 UX)

운영자가 Jenkins (`http://localhost:18080/job/ZeroTouch-QA/`) 에서 Recording UI 로 한 번에 진입할 수 있도록 잡 description 에 링크 추가. **TR.4 완료 후 진행** — 그 전엔 dead link.

- **신규 `jenkins-init/markup-formatter.groovy`**: Jenkins 의 Markup Formatter 를 OWASP "Safe HTML" 로 전환 (a 태그·이모지 허용, 스크립트 차단)
- **`provision.sh:1173` 의 잡 `<description>` 갱신**: plain text 한 줄 → HTML 블록

  ```html
  <p>📹 <a href="http://localhost:18092/" target="_blank">Recording UI</a> —
    사용자 행동을 녹화해 14-DSL 시나리오로 자동 변환합니다.</p>
  <p>🎬 <a href="http://localhost:18092/recording/sessions" target="_blank">최근 세션 목록</a></p>
  ```

- 포트 18092 는 §"TR.1 엔드포인트" 의 단일 호스트 데몬 고정 포트
- fresh rebuild 시 Markup Formatter 가 init 단계에서 자동 적용되도록 `init.groovy.d/` 배치
- 검증: Jenkins 잡 페이지에서 링크 클릭 → 새 탭 18092 진입

- **산출물**: README 패치 + 트러블슈팅 문서 + `jenkins-init/markup-formatter.groovy` + `provision.sh` description 패치

### 산출물 (Deliverables)

#### R-MVP

1. `recording_service/` 모듈 (호스트, ~500 LOC + 테스트 — comparator/enricher 제외)
2. mac-agent·wsl-agent 부트스트랩 패치
3. 호스트 ↔ 컨테이너 공유 디렉토리 설정
4. Web UI 단일 페이지 (시작/중지 + Assertion 추가)
5. README + 트러블슈팅 문서

#### R-Plus (별도 게이트)

1. `zero_touch_qa/recording/` 모듈 (컨테이너, enricher / comparator, ~280 LOC)
2. 결합 시나리오 A·B 구현
3. 5개 샘플 녹화 + 역추정 결과 (품질 입증)

### 성공 기준 (Definition of Done)

평가 페이지는 **Phase 0 T0.1 통일 카탈로그**를 사용한다. Phase R 단계에서는 Phase 1 과 동일한 10종 중 사용자 행동 시나리오가 자연스러운 5종 이상을 선정.

#### R-MVP DoD

- ✅ 호스트 에이전트 환경에서 Web UI 통해 녹화 → DSL 변환 **5단계 이내** 완료
- ✅ 카탈로그 5종 이상에서 녹화 → DSL 변환 + 저장 + 재로드 PASS (round-trip 은 R-Plus 에서)
- ✅ TR.3 의 컨테이너 CLI 위임이 동작 — `docker exec` exit code 0 + `scenario.json` 생성
- ✅ TR.4 의 Assertion 추가 UI 가 verify / mock_status / mock_data 3종 step 을 사용자 입력으로 받아 `_validate_scenario` 통과 산출
- ✅ 호스트 ↔ 컨테이너 공유 디렉토리 + fresh rebuild 회귀 PASS
- ✅ 녹화 중단·재시작 안전 (idempotent)
- ✅ Jenkins 잡 페이지(`/job/ZeroTouch-QA/`) description 의 Recording UI 링크가 실제 18092 데몬으로 이동 (TR.10)

#### R-Plus DoD (별도 게이트)

- ✅ 시나리오 A: 5개 녹화에 대해 TR.5 의 평가 rubric 으로 2인 평균 **4/5 이상** (rubric 엄격 적용)
- ✅ 시나리오 B: 의도적 차이가 있는 doc-DSL 과 recording-DSL 비교 시 **정렬 대상 액션의 모든 차이 정확히 식별** + verify/mock_* 는 "녹화 외 의도" 분류로 표시
- ✅ TR.7 replay: 녹화→변환→executor 재실행 통과율 **90%+** (정렬 대상 액션 한정)

### 위험 & 완화

| 위험 | 영향 | 완화 |
| --- | --- | --- |
| `playwright codegen` 셀렉터 품질이 페이지마다 편차 | 변환 후 DSL 품질 저하 | converter 후처리에서 의미 셀렉터 우선 변환 (Phase 1 과 동일 휴리스틱 재사용) |
| 호스트 환경별 GUI 차이 (mac/wsl) | 일관성 저하 | wsl 의 경우 WSLg 검증, X-server fallback 명시 |
| 호스트 ↔ 컨테이너 통신 (특히 wsl) | replay 실패 | `host.docker.internal` 동작 검증, IP fallback 옵션 |
| 녹화 도중 페이지 새로고침 | codegen 출력 깨짐 | converter 가 부분 출력도 처리 가능하도록 robust 화 |
| 시나리오 A 가 일관성 없는 계획서 생성 | 신뢰성 저하 | few-shot 예시 3개를 시스템 프롬프트에 포함 |
| 시나리오 B fuzzy 매칭 임계값 튜닝 | false positive·negative | 골든 페어 10세트로 임계값 결정, 사용자 조정 옵션 |

### 추정 일정

#### R-MVP — 1.5-2주 (1명 엔지니어 풀타임, Phase 1 과 다른 엔지니어가 병행)

| 주차 | 태스크 |
| --- | --- |
| W1 D1-2 | TR.1 서비스 골격, TR.2 codegen 래퍼 시작 |
| W1 D3 | TR.2 완료, TR.3 컨테이너 CLI 위임 변환 |
| W1 D4-5 | TR.4 Web UI (시작/중지 + Assertion 추가) |
| W2 D1-3 | TR.8 영속화 (3일 — fresh rebuild 회귀 포함) |
| W2 D4-5 | TR.9 에이전트 통합 (mac/wsl 양쪽) + TR.10 문서 |

#### R-Plus — 2-2.5주 (선택, 별도 게이트 통과 후)

| 주차 | 태스크 |
| --- | --- |
| W1 D1-3 | TR.5 시나리오 A (few-shot + rubric, 2.5일) |
| W1 D4 ~ W2 D2 | TR.6 시나리오 B (비대칭 처리, 3.5일) |
| W2 D3 | TR.7 replay |
| W2 D4-5 | 안정화 + R-Plus DoD 검증 |

### Phase R 결정 게이트

#### R-MVP 종료 게이트

- [ ] R-MVP DoD 7개 항목 모두 통과 (Jenkins 잡 description 링크 포함)
- [ ] 카탈로그 5종 일관 동작
- [ ] mac-agent·wsl-agent 양쪽 환경에서 동작 검증

#### R-Plus 진입 게이트 (선택)

- [ ] R-MVP 1주 안정 운영 (회귀 0)
- [ ] Phase 2 시너지 의 시나리오 B 활용도 확인 (Phase 2 진입 시점에 결정)
- [ ] 운영 우선순위에서 R-Plus 가 다음 카드인지 확인 — 아니면 backlog 로 이관

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
| --- | --- | --- |
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

| 모델 | 위치 | VRAM (Q4) | Phase 0 호스트 적합 | 비고 |
| --- | --- | --- | --- | --- |
| `gemma4:26b` | 베이스라인 | ~17GB | (T0.2 결과 기재) | 현재 |
| `qwen2.5:32b` | 후보 | ~22GB | (T0.2 결과 기재) | 도구 호출 강세로 알려짐 |
| `llama3.3:70b-instruct-q4_K_M` | 후보 | ~42GB | (T0.2 결과 기재) | 일반 능력 ↑ |
| `granite3-dense:8b` | 후보 | ~6GB | (T0.2 결과 기재) | IBM, 함수 호출 특화 (소형) |

T0.2 의 호스트 GPU 가용 VRAM 미만의 모델만 실측 후보. 각 모델로 50 시나리오 실행 → 메트릭 수집. GPU 메모리 사용량·응답 시간 기록.

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
| --- | --- |
| `gemma4:26b` | ~17GB |
| `qwen2.5:32b` | ~22GB |
| `llama3.3:70b` | ~42GB |
| `granite3-dense:8b` | ~6GB |

**검토 항목**

- 호스트 머신 사양 (Phase 0 T0.2 결과 활용 — 본 단계에서 재수집 불필요)
- 다운로드 시간·디스크 사용량
- 컨테이너 이미지 변경 영향
- 모델 교체 절차 (재배포·마이그레이션)

##### Dify 1.13 plugin-model 등록 절차 (모델 교체 시 필수)

Dify 1.13.3 의 Ollama provider 는 **플러그인 기반**이며, 커스텀 모델 등록은 단순 `ollama pull` 만으로 끝나지 않는다. 메모리 [Dify 1.13 plugin-model 등록 방식](../../.claude/projects/-Users-luuuuunatic-Developer-airgap-test-toolchain/memory/project_dify113_plugin_model.md) 참조.

| 단계 | 작업 | 위치 |
| --- | --- | --- |
| 1 | 호스트에서 신규 모델 `ollama pull <model>` | 호스트 ollama |
| 2 | Dify Ollama provider 플러그인이 모델을 인식하는지 확인 (provider list API) | `provision.sh` 단계 |
| 3 | 커스텀 모델은 `/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials` 엔드포인트로 등록 (`/models` 가 아님 — `/models` 는 load-balancing 전용) | `provision.sh:308 / 389` 패턴 참조 |
| 4 | workspace default-model 갱신 (Planner 가 신규 모델을 default 로 사용하게) | `/console/api/workspaces/current/default-model` |
| 5 | KB embedding 모델은 별도 — embedding 변경 시 Qdrant collection 차원 lock 충돌 가능 (RAG 트랙 §4.4.2 참조) | KB 재인덱싱 절차 별도 |
| 6 | chatflow YAML 의 model 필드 갱신 후 publish | `dify-chatflow.yaml` / `test-planning-chatflow.yaml` |
| 7 | fresh rebuild 회귀 (Sprint 5 호스트 하이브리드 빌드 방식) | Jenkins build #2/#5/#6/#7 동등 회귀 |

본 7단계 절차의 자동화는 Phase 1.5 산출물에 포함하지 않으나, 절차 문서화는 필수.

- **산출물**: 인프라 변경 제안서 + plugin-model 등록 7단계 자동화 후보 백로그

### 성공 기준

- ✅ 50 시나리오 평가에서 **신뢰성 90%+** 모델 1종 이상 식별
- ✅ 인프라 변경이 필요하면 변경 비용·일정 명확히 산정
- ✅ Phase 2 진입 가능성에 대한 명확한 Go·No-Go 결정

### 위험 & 완화

| 위험 | 완화 |
| --- | --- |
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
| --- | --- | --- |
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
| --- | --- | --- |
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
| --- | --- |
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
| --- | --- | --- |
| LLM 이 무한히 같은 도구 반복 | 자원 낭비·timeout | T2.5 의 무한 루프 감지 |
| 인증 자격증명 누출 | 보안 사고 | secrets manager·env var only·로그 마스킹 |
| Playwright 브라우저 상태 누적 | 메모리 누수 | 실행당 새 context, 종료 시 `context.close()` |
| Dify ↔ 에이전트 통신 단절 | 사용자 경험 저하 | 폴링·재연결·부분 결과 반환 |
| 도구 호출이 의도치 않은 페이지로 이탈 | 위험 액션 | 도메인 화이트리스트 + 액션 확인 옵션 |

### 추정 일정

**합계: 3-4주** (1명 엔지니어 풀타임)

| 주차 | 태스크 |
| --- | --- |
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

## Phase 3 — Stabilization & Operations

> **한 줄 요약**: 30종 페이지에서 안정 동작 + 운영 대시보드 + 회복 정책. 운영 가능 수준으로 격상.

### 목표

Phase 2 의 에이전트를 **30종 대표 페이지** 에서 안정적으로 동작하도록 강화한다. 운영 가시화는 별도 대시보드(`http://<host>:18091/dashboard`) + Jenkins artifact 로 충족. Dify 챗 UI SSE 통합은 backlog 이관.

운영 가능 수준으로 격상.

### 범위

**IN**

- 30종 페이지 회귀 코퍼스 (자체 + 공개 SaaS 혼합)
- 추가 도구 구현 (file upload·dropdown·date picker·scroll into view)
- Multi-tab 시나리오 지원
- iframe 시나리오 지원 (가능한 범위)
- 실패 회복 정책 (재시도·부분 결과 사용)
- 운영 대시보드 (간단한 HTML, Jenkins artifacts 연계) — 운영 진행 가시화의 단일 채널
- 비용·성능 가시화
- 운영자 매뉴얼

**BACKLOG** (Phase 3 본 트랙 제외) — Dify 챗 UI ↔ 에이전트 진행 스트리밍 (T3.5). §"T3.5" 결정 노트 참조.

**OUT**

- Shadow DOM — 도구 호출로 한계 극복 어려움, 별도 R&D 필요
- WebSocket 기반 동적 콘텐츠 검증 — 별도 도구 필요
- 시각 회귀·a11y·성능 — 영구 OUT
- 외부 시스템 연동(이메일·SMS) 검증

### 상세 태스크

#### T3.1 — 30종 코퍼스 선정 + 골든 시나리오 (3일)

**카테고리 분배**

| 카테고리 | 수 | 예시 |
| --- | --- | --- |
| 자체 환경 | 10종 | Jenkins, Dify 콘솔, Allure 리포트, fixture |
| 공개 SaaS | 10종 | 인증 불요 |
| 협력사 staging | 10종 | 인증 필요 |

각 페이지에 골든 시나리오 정의 (성공 판정 기준). 자동 평가 하니스 확장.

- **산출물**: `tests/agent_corpus/`

#### T3.2 — 추가 도구 구현 (4일)

| 도구 | 시그니처 | 비고 |
| --- | --- | --- |
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

#### T3.5 — Dify 스트리밍 통합 — **BACKLOG (Phase 3 본 트랙 제외)**

> **2026-04-28 결정**: Dify 1.13 의 챗플로우 노드 한계(HTTP 노드 미정·SSE 통합 비표준) 와 운영 가치 대비 구현 비용 불균형으로 본 Phase 3 에서 제외. 운영 진행 가시화는 T3.7 의 별도 운영 대시보드(`http://<host>:18091/dashboard`) 와 Jenkins artifact 로 흡수한다. 향후 Dify 가 SSE 통합을 정식 지원하거나 운영 요구가 명확해지면 별도 이니셔티브로 재개.

##### 구 계획 (참고, 비활성)

당초 안은 에이전트 서비스에 SSE 엔드포인트(`GET /agent/runs/{id}/stream`) 추가 + Dify HTTP 노드 폴링 또는 별도 프록시(SSE→Dify chat 메시지 변환). 진행 이벤트는 `turn_start` / `tool_call` / `tool_result` / `done` 4종. UX 는 챗봇이 "현재 로그인 페이지 탐색 중..." 식 자연어 진행 보고. 본 안은 위 결정에 따라 보류.

#### T3.6 — 실패 회복 정책 (3일)

**도구 실패 분류**

| 분류 | 대응 |
| --- | --- |
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
3. 회복 정책 코드
4. 운영 대시보드 (운영 진행 가시화 단일 채널)
5. 비용 모니터링
6. 회귀 통과 데이터
7. 운영자 매뉴얼

### 성공 기준 (Definition of Done)

- ✅ 30종 코퍼스 통과율 **90%+** (3회 평균)
- ✅ 평균 실행 시간 **3-5분**
- ✅ T3.7 운영 대시보드 + Jenkins artifact 로 진행·결과 가시화 (Dify 챗 UI 통합은 backlog)
- ✅ 1주 운영 중 치명적 실패(서비스 다운) 0건
- ✅ 비용 가시화로 시간당 비용 추정 가능
- ✅ 운영자가 매뉴얼만 보고 새 페이지 추가 가능

### 위험 & 완화

| 위험 | 완화 |
| --- | --- |
| 일부 페이지가 끝까지 90% 미달 | 해당 페이지 수동 셀렉터 보강 (하이브리드 모드) |
| 운영 비용 예상 초과 | 페이지별 max_turn 차등 적용, 캐싱 (같은 페이지는 한번만 grounding) |
| 모델 업그레이드 영향 | 회귀 코퍼스가 모델 교체 시 회귀 검증 인프라로 자동 활용 |

### 추정 일정

**합계: 3.5-5주** (T3.5 backlog 이관으로 종전 4-6주에서 -0.5~-1주 단축).

| 주차 | 태스크 |
| --- | --- |
| W1 | T3.1 코퍼스, T3.2 도구 시작 |
| W2 | T3.2 완료, T3.3 multi-tab, T3.4 iframe |
| W3 | T3.6 회복, T3.7 대시보드 |
| W4 | T3.8 비용, T3.9 회귀 안정화 시작 |
| W5 | T3.9 안정화 완료, T3.10 문서 |

---

## 횡단 관심사 (Cross-Cutting Concerns)

### 로깅 표준

모든 단계의 신규 코드에 적용한다.

- 구조화 로그 (`structlog` 또는 JSON 라인)
- 필드: `timestamp, phase, run_id, turn?, tool?, status, duration_ms, tokens_in?, tokens_out?, error?`
- 로그 위치: `/data/logs/grounding.log`, `/data/logs/agent.log`
- 자동 회전 (50MB × 5)

### 메트릭 통합

기존 `llm_calls.jsonl` 과 호환되는 형식 확장. 기존 schema 는 `kind: planner|healer` (`dify_client.py:389,417` 참조). 신규 phase 는 `kind` 값을 추가한다.

| Phase | 추가 `kind` 값 |
| --- | --- |
| Phase 1 | `grounding_fetch` (보조 — Planner 레코드에 `grounding_inventory_tokens` 필드 추가만으로도 가능) |
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
| --- | --- |
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

### Phase 0 검증

1. `docs/eval-page-catalog.md` 의 페이지 ID 5+5 확정
2. `docs/host-gpu-spec.md` 의 가용 VRAM 한 줄 기록
3. 의존성 선처리 PR 머지 + Jenkins fresh rebuild PASS 빌드 번호 기록
4. T0.3 호스트/컨테이너 호출 계약 확정 (본 문서 §"T0.3" 표 참조)

### Phase 1 검증

1. `ENABLE_DOM_GROUNDING=1` 환경에서 카탈로그 10종 자동 실행
2. `tests/grounding_eval/report.html` 확인 — flag off vs on 페어 비교 (정확/부분/실패 분류)
3. `kind=healer` 카운트가 flag=on 페어에서 1/3 이하인지 확인
4. 기존 회귀 테스트 30종 (`test/test_*.py`) 모두 통과 (chatflow YAML 미수정으로 회귀 영향 0 자명)
5. 추출 실패 케이스 (`inventory.error`) 가 `llm_calls.jsonl` 의 `kind=planner` 레코드에 기록되는지 확인

### Phase R 검증

#### R-MVP 검증

1. 호스트 에이전트 환경에서 `http://localhost:18092` 접속
2. 카탈로그 5종 녹화 → `docker exec` 변환 → `scenario.json` 생성 확인
3. TR.4 Assertion 추가 UI 로 verify / mock_status / mock_data 각 1건 추가 → `_validate_scenario` 통과 확인
4. mac-agent·wsl-agent 양쪽 환경 검증 + fresh rebuild 회귀 PASS

#### R-Plus 검증 (선택)

1. 시나리오 A: 녹화 5건에 대해 "Generate Doc" → 2인 평가 rubric 4/5 평균
2. 시나리오 B: 의도적으로 차이 있는 doc-DSL 업로드 → diff 리포트 5분류(정확/값차이/누락/추가/녹화 외 의도) 확인
3. TR.7 replay: 5종 녹화의 executor 재실행 통과율 90%+

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
3. Jenkins artifact 의 진행 로그로 운영 진행 가시화 확인 (T3.5 backlog 이관)
4. 1주 운영 후 대시보드 통계 검토

---

## 추정 총 일정 & 리소스

### 단일 트랙 (1명 순차)

| Phase | 기간 |
| --- | --- |
| **Phase 0** | **0.5-1일** |
| Phase 1 | 1.5주 (srs_text prepend 단순화) |
| **Phase R-MVP** | 1.5-2주 |
| Phase R-Plus (선택) | 2-2.5주 (별도 게이트) |
| Phase 1.5 | 1주 (모델 교체 시 +1주) |
| Phase 2 | 3-4주 |
| Phase 3 | 3.5-5주 (T3.5 backlog 제거로 단축) |
| **합계 (R-Plus 제외)** | **약 11-14주 (~2.75-3.5개월)** |
| **합계 (R-Plus 포함)** | **약 13-16주 (~3.25-4개월)** |

### 병행 트랙 (2명 — Phase 1+R-MVP 동시)

W0 는 양 트랙 공통 선행. 양 엔지니어가 W0 작업을 분담하면 4시간 안에 완료.

```text
주차:   0    1    2    3    4    5    6    7    8    9    10
엔지A:  [W0][Phase 1   ][Phase 1.5][Phase 2          ][Phase 3        ]
엔지B:  [W0][Phase R-MVP    ][.... Phase 2/3 지원 또는 R-Plus (선택) ....]
```

| Phase | 기간 (병행) |
| --- | --- |
| **Phase 0** | **W0 (4시간)** |
| Phase 1 | W1-W2 (1.5주) |
| Phase R-MVP | W1-W2.5 (1.5-2주) |
| Phase 1.5 | W2.5-W3.5 |
| Phase 2 | W3.5-W7 |
| Phase 3 | W7-W10 (T3.5 제거로 단축) |
| **합계** | **~10주 (~2.5개월)** |

R-Plus 는 본 트랙에 포함되지 않음. 별도 결정으로 W2.5+ 시점에 진입 여부 결정.

### 추가 자원

- GPU (모델 교체 시 재산정)
- 협력사 staging 환경 접근 (Phase 3 코퍼스용)
- mac·wsl 호스트 환경 양쪽 (Phase R 검증용)
- 리뷰어 시간 (Phase 별 결정 게이트)

---

## 핵심 파일 변경 요약

| 파일 | Phase 0 | Phase 1 | Phase R-MVP | Phase R-Plus | Phase 2 | Phase 3 |
| --- | --- | --- | --- | --- | --- | --- |
| `dify_client.py` | — | 수정 (srs_text prepend) | — | — | 수정 (agent 분기) | — |
| `dify-chatflow.yaml` | — | **미수정** | — | — | 수정 (run_mode=agent 추가) | — |
| `zero_touch_qa/grounding/` | — | **신규** | 재사용 (옵션) | 재사용 | 재사용 | — |
| `zero_touch_qa/recording/` | — | — | — | **신규** (enricher, comparator) | — | — |
| `zero_touch_qa/converter.py` | — | — | 재사용 (변경 없음) | — | — | — |
| `zero_touch_qa/__main__.py` | **수정 (`--convert-only` 플래그 추가, T0.2 PR)** | — | 호출만 | — | — | — |
| `zero_touch_qa/agent/` | — | — | — | — | **신규** | 확장 |
| `recording_service/` (host) | — | — | **신규** (codegen 래퍼 + UI + 영속화) | 확장 (replay/enrich/compare) | — | — |
| `supervisord.conf` | — | — | 수정 (recordings 공유 볼륨 mount) | — | 수정 (agent 서비스 등록) | — |
| `mac-agent-setup.sh` | 수정 (deps 선처리) | — | 수정 (recording 부트스트랩) | — | — | — |
| `wsl-agent-setup.sh` | 수정 (deps 선처리) | — | 수정 (recording 부트스트랩) | — | — | — |
| `build.sh` | — | — | 수정 (volume 마운트) | — | 수정 (FastAPI) | — |
| `requirements.txt` | **수정 (단일 PR — tiktoken / fastapi / uvicorn)** | — | — | — | — | — |
| `provision.sh` | — | — | 수정 (잡 description HTML 화 — TR.10) | — | (모델 교체 시) plugin-model 등록 7단계 | — |
| `jenkins-init/markup-formatter.groovy` | — | — | **신규** (Safe HTML formatter — TR.10) | — | — | — |
| `README.md` | — | 섹션 추가 | 섹션 추가 (Recording MVP) | 섹션 확장 | 섹션 추가 | 섹션 확장 |
| `docs/` | **신규** (eval-page-catalog / host-gpu-spec) | — | — | — | — | — |
| `tests/` | — | 신규 (grounding_eval) | 신규 (recording) | 신규 (recording/plus) | 신규 (agent) | 신규 (corpus) |

---

## Recording ↔ Doc-based 트랙 시너지 활용

Phase R 이 Phase 2·3 와 만들어내는 추가 가치.

### 시너지 ① — Phase 2 자가 평가 데이터

Phase R 의 시나리오 B(의미 비교) 인프라를 Phase 2 에이전트의 **자가 평가** 에 재사용한다.

> 사용자 녹화 = 정답 액션 시퀀스 → 에이전트 출력과 자동 비교 → 품질 메트릭

Phase 2 의 파일럿 검증(T2.9) 에서 활용 가능.

### 시너지 ② — Phase 3 회귀 코퍼스 시드

Phase R 로 30종 페이지 각각에 대해 사용자 녹화 1회 → 골든 시나리오 시드 생성.

**정량 가정 + 한계**:

- 비인증 페이지 20종은 녹화 → DSL 변환 + verify 수동 추가(TR.4 Assertion UI)로 **약 50% 단축 추정** (행동 5분 + verify 추가 5분 vs 손작성 20분)
- 인증 페이지 10종은 자격증명·세션 처리가 codegen 영역 밖 → 단축 효과 0 ~ 20% 수준
- **30종 전체 가중 평균 약 30~35% 단축** (당초 50% 표기는 비인증 전제)
- 단축률은 Phase 3 T3.1 진입 시점의 실제 페이지 분포로 재산정

### 시너지 ③ — Doc → 자동화 갭 가시화

시나리오 B 비교 결과를 운영 대시보드(Phase 3) 에 누적 → "기획서가 실제 UI 와 어디서 어긋나는가" 의 통계.

**격상 주장의 전제 조건**:

- TR.6 의 "녹화 외 의도(intent-only)" 분류가 false positive 를 충분히 억제 (골든 페어 10세트 튜닝 후 임계값 0.7 수준에서 정밀도 80%+)
- Phase R DoD ✅ "verify/mock_* 는 별도 분류로 표시" 통과 후
- 통계 누적 N ≥ 30 (페이지 수) × 5 회 이상 — 충분한 표본 확보 후 격상 주장

위 전제가 미충족이면 본 시너지는 **백로그**로 이동, 자동화 도구 본업으로만 가치 평가.

---

## 다음 액션

이 로드맵 승인 후 즉시 시작 가능한 항목. **Phase 0 (W0) 결정 게이트 통과 전 Phase 1·R-MVP 본 작업 진입 금지**.

### W0 (4시간 — 양 엔지니어 분담)

1. **T0.1 평가 페이지 카탈로그** — `test/fixtures/*.html` 5종 + 자체 호스팅 5종 ID 부여 (30분-1시간)
2. **T0.2 의존성 선처리 PR + GPU 사양** — `tiktoken` / `fastapi` / `uvicorn` 머지 + Jenkins fresh rebuild PASS + `nvidia-smi` 1줄 기록 (3-4시간)
3. **T0.3 호스트/컨테이너 호출 계약** — 본 PLAN §"T0.3" 표 그대로 채택 확인 + TR.3 코딩 진입 (1-2시간)

### W1 진입 (W0 게이트 통과 후)

1. **Phase 1 T1.1** (DOM 인벤토리 스키마 설계) — 엔지니어 A
2. **Phase 1 T1.2 첫 5분 a11y smoke check** — `page.accessibility.snapshot()` REPL 검증
3. **Phase R-MVP TR.1** (Recording 서비스 골격) — 엔지니어 B (병행)
4. **Phase 3 코퍼스 후보 페이지 리스트업** (시너지 ② 가정에 따라 비인증/인증 비율 조정 — 시간 여유 시)

### W2.5+ 결정 시점

- **R-Plus 진입 여부 결정** — Phase 2 진입 시점에 결합 시나리오 가치 재평가
- **Phase 1.5 진입** — Phase 1 종료 후 즉시
- **Phase 1 토큰 예산 상향 결정** — T1.4 의 첫 호출 실측 결과로 1500→3000 또는 OLLAMA_CONTEXT_SIZE 20480 조정 결정
