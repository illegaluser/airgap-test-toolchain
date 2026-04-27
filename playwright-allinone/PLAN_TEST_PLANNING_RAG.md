# Test Planning RAG — 별도 트랙 계획서

> 별도 트랙 (Sprint 6 의 ZeroTouch-QA Brain 본 트랙과 분리). 본 PLAN 은 자체 완결적이며,
> 기존 PLAN_DSL_ACTION_EXPANSION.md 를 참조만 하고 수정하지 않는다.
>
> 작성일: 2026-04-27
> 트랙명: **Test Planning RAG**
> 책임 범위: Dify Knowledge Base + 신규 Chatflow (Test Planning Brain) 구축, 산출물 (테스트
> 계획서 / 시나리오) 자동 생성. ZeroTouch-QA 와는 출력 (14대 DSL JSON) 호환만 보장.

---

## 1. 목적 (Purpose)

### 1.1 배경

현재 `playwright-allinone` 패키지는 **이미 작성된 시나리오** (자연어 SRS 또는 14대 DSL JSON)
를 입력으로 받아 자동 실행 + 검증한다. 시나리오 자체를 **누가, 무엇을 근거로 작성할 것인가**
는 사람의 영역이었다. 한 번의 회귀 빌드를 위해 사용자가 매번 해야 했던 일:

1. 제품 spec / API 문서 / 기획서를 읽는다.
2. 테스트 설계 기법 (boundary value, equivalence partition 등) 을 머리에 떠올린다.
3. 위 둘을 결합해 테스트 계획서 (IEEE 829 또는 사내 템플릿) 와 시나리오를 작성한다.
4. 그것을 SRS 자연어 또는 JSON 으로 옮긴다 → ZeroTouch-QA 가 실행.

이 흐름의 1~3 단계가 사람의 시간을 가장 많이 잡아먹고, 작성자별로 결과 품질이 들쭉날쭉
한다. **프로젝트 정보** (in-house 산출물) 와 **테스트 이론** (외부 표준) 이 둘 다 문서로
이미 존재함에도 작성 시점에는 사람이 그것들을 mental search 해야 한다.

### 1.2 비즈니스 가치

- **시간 단축**: 테스트 계획서 / 시나리오 초안 작성 시간 (평균 4–8시간 / 모듈) 을 LLM 으로
  몇 분 단위로 압축. 사람은 검토 / 수정만 수행.
- **품질 균질화**: 신입 / 시니어가 동일한 테스트 이론 KB 를 참조하므로 작성자별 변동성
  감소. 누락된 테스트 설계 기법 (예: 경계값 분석) 을 LLM 이 KB 기반으로 빠뜨리지 않음.
- **추적성 (traceability)**: 모든 시나리오는 어떤 spec 청크 + 어떤 테스트 이론 청크에서
  파생됐는지 retrieval log 로 역추적 가능. 향후 spec 변경 시 영향받는 시나리오 식별이
  쉬워진다.
- **ZeroTouch-QA 와의 결합**: 시나리오 출력이 14대 DSL JSON 형식이면 그대로 자동 실행 가능.
  사람의 SRS 작성 → DSL 변환 단계가 아예 사라진다.

### 1.3 범위

#### in-scope

- Dify 의 **두 개 Knowledge Base** 신설 + 자동 생성 (provision.sh).
  - `kb_project_info`: 프로젝트 산출물 (spec / 기획서 / API 문서 / 사용자 시나리오)
  - `kb_test_theory`: 테스트 이론 / 프로세스 (테스트 설계 기법, V-model, 회귀 정책 등)
- 입력 문서 형식: `csv`, `pdf`, `pptx`, `docx`. 두 KB 모두 동일 형식 지원.
- **Embedding 모델**: `bona/bge-m3-korean:latest` (Ollama 호스트). 한국어 우선, 영어
  혼합 입력도 처리.
- **신규 Dify 챗봇**: `Test Planning Brain` (별도 chatflow YAML).
  - 같은 Dify 인스턴스 (port 18081) 에 추가. 기존 `ZeroTouch QA Brain` 은 그대로 보존.
  - LLM: `gemma4:26b` (기존과 동일 모델, 동일 provider).
- **출력 형식 3 종 모두 지원**: 사용자 의도 분류 → 적절한 조합 emit.
  - 테스트 계획서 (IEEE 829-lite Markdown)
  - 자연어 시나리오 (Given/When/Then)
  - 14대 DSL JSON (ZeroTouch-QA 의 `_validate_scenario` 호환)
- **운영 절차 문서화**: KB ingestion / 재인덱싱 / 챗봇 사용법 (README 보강).

#### out-of-scope (이번 트랙에서는 제외, 후속 검토)

- KB ingestion 자동화 (Jenkins Job, ingestion CLI). **이번에는 Dify console GUI 직접
  업로드 만**. 향후 자동화는 별도 트랙.
- 산출물의 사내 템플릿 매핑. 이번에는 일반 IEEE 829-lite 사용. 사내 템플릿 도입은 후속.
- 챗봇 결과를 ZeroTouch-QA execute 모드로 자동 연결하는 Jenkins 파이프라인. 이번에는
  사람이 결과 검토 후 수동 실행.
- 회귀 자동화 (예: spec 변경 → 영향받은 시나리오 자동 재생성). 후속 트랙.
- 다국어 지원 (영어/일본어 KB). 한국어 우선.

### 1.4 성공 기준 (Definition of Done)

| 기준 | 측정 방법 |
| --- | --- |
| 두 KB 가 fresh provision 시 자동 생성 | `provision.sh` 실행 후 Dify console 에서 두 KB 노출 |
| `bona/bge-m3-korean:latest` 가 embedding provider 로 등록 | DB `provider_model_credentials` 에 model_type=text-embedding 레코드 존재 |
| 4개 형식 (csv/pdf/pptx/docx) 모두 GUI 업로드 → 인덱싱 완료 | 각 형식별 1개 샘플 문서 업로드 후 chunk 수 > 0 |
| `Test Planning Brain` 챗봇이 fresh provision 시 자동 생성 | provision.sh 후 Dify console 에 두 번째 앱 노출 |
| **복수 chatflow seed 인프라** — `OFFLINE_DIFY_CHATFLOW_YAML` 단수 처리 → 복수 처리로 리팩토링 (Dockerfile / entrypoint.sh / provision.sh) | provision.sh 가 두 chatflow YAML 모두 import + publish + API key 발급 |
| **Dataset id 주입** — chatflow YAML 의 Knowledge Retrieval 노드가 fresh KB 의 실 ID 를 참조 | provision.sh 가 KB 생성 후 ID 캡처 → chatflow YAML 의 placeholder (`__KB_PROJECT_INFO_ID__`, `__KB_TEST_THEORY_ID__`) 를 substitute 후 import |
| **출력에 traceability 블록 포함** — 계획서 / 시나리오 모두 출처 인용 가능 | 각 산출물에 `source_documents` (문서명) + `source_chunks` (chunk id 또는 인용 텍스트) + `retrieval_score` 명시 |
| 챗봇이 "테스트 계획서" 요청에 IEEE 829 7 섹션 markdown 응답 + traceability 블록 | 수동 호출 + 결과 검증 |
| 챗봇이 "시나리오" 요청에 자연어 + 14-DSL JSON + traceability 양쪽 응답 | 수동 호출 + JSON 을 `_validate_scenario` 통과 확인 |
| **`scenario_dsl` 모드는 순수 JSON only** (markdown 혼합 금지). `both` 모드는 fenced block 경계 엄격 (`<!-- BEGIN SCENARIO_DSL -->` / `<!-- END SCENARIO_DSL -->`) | 자동화된 파서가 JSON 블록 추출 시 100% 성공 |
| 14-DSL JSON 출력을 ZeroTouch-QA execute 모드로 실행 시 PASS | 수동 end-to-end 검증 |
| **모델 / 오프라인 반입 가이드 정합성** — README / Dockerfile / 오프라인 반입 list 가 모두 `gemma4:26b` + `bona/bge-m3-korean:latest` 로 일관 | grep `gemma4:e4b` / 오프라인 반입 list 검토 |
| 운영 매뉴얼이 README 에 추가 + 본 PLAN 이 별도 트랙으로 분리 보존 | 문서 검토 |

---

## 2. 설계 구조 (Design Structure)

### 2.1 아키텍처 개요

```text
┌──────────────────────────────────────────────────────────────────┐
│                  Single Dify instance (port 18081)               │
│                                                                  │
│  ┌──────────────────────────┐   ┌──────────────────────────────┐│
│  │ App: ZeroTouch QA Brain  │   │ App: Test Planning Brain     ││
│  │ (기존 — 영향 없음)       │   │ (신규 트랙)                  ││
│  │                          │   │                              ││
│  │ - chat / heal flows      │   │ - test plan / scenario flows ││
│  │ - 14-DSL Planner+Healer  │   │ - retrieval-augmented        ││
│  │ - LLM: gemma4:26b        │   │ - LLM: gemma4:26b            ││
│  └──────────────────────────┘   └─────────────┬────────────────┘│
│                                                │                  │
│                  ┌─────────────────────────────┴──────┐           │
│                  │ Knowledge Retrieval (multi-dataset)│           │
│                  └─────┬────────────────────────┬─────┘           │
│                        │                        │                 │
│             ┌──────────┴──────────┐  ┌──────────┴───────────┐     │
│             │ KB: kb_project_info │  │ KB: kb_test_theory   │     │
│             │ csv/pdf/pptx/docx   │  │ pdf/docx (이론)       │     │
│             │ project spec, API   │  │ test design 기법,     │     │
│             │ doc, 기획서          │  │ V-model, 회귀 정책   │     │
│             └─────────────────────┘  └──────────────────────┘     │
│                        │                        │                 │
│                        └────────┬───────────────┘                 │
│                                 ▼                                 │
│              Embedding: bona/bge-m3-korean:latest                 │
│                       (Ollama, host)                              │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                  사용자 입력 (자연어 요청)
                                  │
                                  ▼
                Test Planning Brain 의 chatflow 가
                  retrieved context + LLM 으로
                                  │
                ┌─────────────────┼──────────────────┐
                ▼                 ▼                  ▼
         테스트 계획서       자연어 시나리오     14-DSL JSON
         (Markdown)        (Given/When/Then)  (ZeroTouch-QA 호환)
                                                     │
                                                     ▼
                                       (선택, 사람이 검토 후 수동)
                                       ZeroTouch-QA execute 로 실행
```

### 2.2 컴포넌트별 책임

#### 2.2.1 Knowledge Base (KB) — 두 데이터셋

**`kb_project_info`**
- 입력: 프로젝트 산출물 (spec PDF, 기획서 PPTX, API 문서 CSV, 사용자 시나리오 DOCX 등).
- 청킹: 500자 chunk_size + 50자 overlap (보수적 시작), high-quality indexing 모드.
- 검색 가중치: chatflow 의 retrieval 노드 #1 에서 top_k=5, score_threshold=0.5 기본.
- 갱신 정책: Dify console GUI 에서 사용자가 직접 추가 / 변경 시 "다시 인덱싱" 메뉴로
  변경 파일만 재인덱싱.

**`kb_test_theory`**
- 입력: 외부 표준 / 사내 정책 (테스트 설계 기법 PDF, ISO 29119 발췌, 사내 회귀 정책 DOCX 등).
- 청킹: 동일 (500/50, high-quality).
- 검색 가중치: chatflow retrieval 노드 #2 에서 top_k=3, score_threshold=0.6
  (정확도 우선 — 잘못된 이론 인용은 비용이 큼).
- 갱신 정책: 회사/팀 정책 변경 시에만. 보통 분기 1 회 미만.

**왜 KB 를 두 개로 분리하는가?**
- 두 KB 는 의미적 그룹이 다르다 (project-specific vs. general theory). 하나로 합치면
  retrieval 시 한쪽이 다른쪽을 압도해 다양성 손실.
- 검색 가중치를 분리해서 둘 다 항상 일정량 retrieve 하도록 강제 가능.
- 향후 `kb_test_theory` 만 별도 갱신 / 별도 권한 부여 등 운영 분리 가능.

#### 2.2.2 Embedding 모델

- **`bona/bge-m3-korean:latest`** (Ollama 호스트, ~1.2GB).
- 한국어 우선 학습 + 영어 호환. spec 문서가 한·영 혼재일 때 안전.
- Dify 의 Ollama provider 에 `model_type=text-embedding` 으로 등록.
- KB 의 청크가 인덱싱될 때 이 모델이 호출되어 벡터 생성.
- 최대 chunk 처리량: 32 (Dify 의 `max_chunks` 기본값). 한 번에 32개 청크 임베딩.

#### 2.2.3 Chatflow (Test Planning Brain)

신규 Dify 앱 — `test-planning-chatflow.yaml` 로 정의. 노드 구성:

```text
[Start]
  ├── inputs:
  │     - user_query (자연어)
  │     - output_mode (선택; "plan" / "scenario_natural" / "scenario_dsl" / "both")
  │     - target_module (선택; "결제 모듈", "로그인", 등 — retrieval 정확도 향상용)
  ▼
[Knowledge Retrieval #1: kb_project_info]
  ├── dataset_ids: []   ← provision.sh 가 sed 로 ['<UUID>'] 주입 (sister 패턴)
  ├── query_variable_selector: [start, user_query]
  ├── retrieval_mode: multiple
  ├── multiple_retrieval_config:
  │     ├── top_k: 3                ← 보수적 시작 (context 부담 ↓)
  │     ├── score_threshold_enabled: true
  │     └── score_threshold: 0.5
  ▼
[Knowledge Retrieval #2: kb_test_theory]
  ├── dataset_ids: []   ← 동일 sed substitute
  ├── query_variable_selector: [start, user_query]
  ├── retrieval_mode: multiple
  ├── multiple_retrieval_config:
  │     ├── top_k: 2                ← 보수적 시작
  │     ├── score_threshold_enabled: true
  │     └── score_threshold: 0.6
  ▼
[Variable Aggregator]
  └── 두 retrieval 결과를 단일 context 로 결합 (project_chunks + theory_chunks +
       각 chunk 의 source_document_name, retrieval_score 도 별도 변수로 보존 →
       LLM 이 traceability 블록 작성 시 사용)
  ▼
[LLM: gemma4:26b]
  ├── system prompt = 의도 분류 + 출력 형식 분기 + 1-shot 예시 (계획서/시나리오 둘 다)
  │                  + traceability 블록 강제 + scenario_dsl 모드 순수 JSON 룰
  ├── user prompt = 사용자 query + retrieved context (chunk 별 source 메타 포함)
  ▼
[Answer]
  └── LLM 응답 그대로
```

##### Dataset id 주입 메커니즘 (가장 큰 구현 리스크)

Dify 1.13.3 의 chatflow YAML 은 Knowledge Retrieval 노드에서 dataset 을 **이름이 아닌
ID (UUID)** 로 참조한다. fresh provision 시 KB 가 새로 생성되면 ID 가 매번 달라지므로,
chatflow YAML 을 import 하기 전에 ID 를 substitute 해야 한다.

**플로우** (provision.sh 책임):

1. `POST /console/api/datasets` 로 두 KB 생성 → 응답에서 각 `id` 캡처.
2. seed YAML 의 placeholder (`__KB_PROJECT_INFO_ID__`, `__KB_TEST_THEORY_ID__`) 를
   캡처한 ID 로 `sed` substitute → 임시 파일 (`/tmp/test-planning-chatflow.runtime.yaml`).
3. 임시 파일을 `POST /console/api/apps/imports` 로 import.
4. publish + API key 발급은 기존 ZeroTouch-QA Brain 와 동일.

**검증 완료** (sister fixture `code-AI-quality-allinone/scripts/dify-assets/sonar-analyzer-workflow.yaml:251-267`):

- 1.13.3 의 Knowledge Retrieval 노드는 `dataset_ids: list[str]` 정식 multi-dataset 지원.
- `top_k`, `score_threshold_enabled`, `score_threshold` 는 `multiple_retrieval_config`
  하위 (평면이 아닌 nested 구조).
- `query_variable_selector: [Start, var_name]` 으로 query 입력 변수를 path 로 지정.
- ID substitute 는 placeholder 문자열 대신 **빈 리스트 `dataset_ids: []` 를 sed target**
  으로 잡음 — 다른 필드 충돌 위험 zero (`provision.sh:580`).
- import 시 dataset_ids 가 비어 있으면 노드 검증은 통과하나 retrieval 결과가 빈 list 로
  emit. 따라서 import → sed 주입 순서가 뒤바뀌면 안 됨 (provision.sh 의 sequencing 중요).

##### 추가 검토 — KB body 의 `retrieval_model` (sister 채택)

sister 의 KB body 에는 retrieval 정책도 포함됨:

```json
"retrieval_model": {
  "search_method": "hybrid_search",
  "reranking_enable": false,
  "top_k": 6,
  "score_threshold_enabled": false,
  "score_threshold": 0.0
}
```

- `search_method: "hybrid_search"` 는 BM25 (키워드) + dense vector (임베딩) 결합 검색.
  한국어 spec / API 문서처럼 고유명사 (모듈명, endpoint 등) 가 많은 경우 정밀도 ↑.
- chatflow 노드의 `multiple_retrieval_config.top_k=3` 과 KB 자체의 `top_k=6` 중 어느
  쪽이 우선인지: chatflow 노드의 값이 retrieval 호출 시 **override** 함 (KB default
  값은 fallback). 따라서 PLAN 의 chatflow 노드 top_k=3/2 가 실제 동작 결정.
- 본 트랙 채택: **KB body 에 `search_method: "hybrid_search"`, `reranking_enable: false`,
  `top_k: 6`, `score_threshold_enabled: false`** (sister 와 동일). chatflow 노드가 더
  엄격한 top_k=3/2 + score_threshold 활성화로 override.

#### 2.2.4 LLM (Planner role)

- **모델**: `gemma4:26b` (Ollama, 호스트, 기존과 동일).
- **completion_params**: `temperature=0.1`, `max_tokens=8192` (기존 ZeroTouch QA Brain 과
  동일 — Sprint 6 에서 검증된 값).
- **context_size**: Ollama provider credential 의 12288 (Sprint 6 에서 결정).
- **`<think>` 출력 금지**: ZeroTouch QA Brain 과 동일하게 system prompt 에 명시
  (max_tokens 낭비 방지).

#### 2.2.5 Output schema

다음 세 출력을 system prompt 1-shot 예시 + 명시적 schema 표로 정의. 각 출력은 retrieval
근거를 명시하는 **traceability 블록** 을 항상 포함한다.

**테스트 계획서 (Markdown)** — IEEE 829-lite 7 섹션 + traceability:

```markdown
# 테스트 계획서: <모듈명>

## 1. 목적
<spec 의 무엇을 검증하는가>

## 2. 범위
- 포함: ...
- 제외: ...

## 3. 일정 / 자원
<예상 소요 + 필요 자원>

## 4. 책임
<역할별 담당>

## 5. 위험과 대응
<식별된 위험 + 완화 전략>

## 6. 종료 기준
<어떤 조건이 만족되면 테스트 종료 선언>

## 7. 산출물
<테스트 시나리오, 회귀 결과, 결함 리포트 등>

## 8. 근거 문서 (Traceability)
| 섹션 | 출처 문서 | 청크 ID | retrieval score | 인용 |
| --- | --- | --- | --- | --- |
| 1. 목적 | `module_payment_v1.2.pdf` | chunk-7f2a | 0.84 | "결제 모듈은 ..." (인용 첫 30자) |
| 2. 범위 | `api_endpoints.csv` (row 12) | chunk-9c3d | 0.78 | "POST /payments ..." |
| 5. 위험과 대응 | `boundary_value_analysis.pdf` | chunk-1b8e | 0.71 | "경계값 분석 ..." |
```

**자연어 시나리오** (Given/When/Then) — traceability 포함:

```markdown
### 시나리오: <제목>

**Given** <전제 / 사전 조건>
**When** <사용자/시스템 동작>
**Then** <기대 결과>

**근거**: `<문서명>` chunk-<id> (score=<0.xx>) — "인용 텍스트 첫 30자..."
```

**14-DSL JSON** (ZeroTouch-QA 호환) — `_validate_scenario` 와 동일 schema, traceability
는 별도 sibling 블록으로:

```json
{
  "scenario": [
    {"step": 1, "action": "navigate", "target": "", "value": "<URL>",
     "description": "<자연어 시나리오의 어느 부분에 해당>", "fallback_targets": []},
    {"step": 2, "action": "click", "target": "<role+name 또는 #id>", "value": "",
     "description": "...", "fallback_targets": ["..."]}
  ],
  "traceability": [
    {"step": 1, "source_document": "module_payment_v1.2.pdf", "chunk_id": "chunk-7f2a",
     "retrieval_score": 0.84, "citation": "결제 모듈의 첫 페이지는 ..."},
    {"step": 2, "source_document": "api_endpoints.csv", "chunk_id": "chunk-9c3d",
     "retrieval_score": 0.78, "citation": "POST /payments accepts ..."}
  ]
}
```

action 화이트리스트 14개 (`navigate`, `click`, `fill`, `press`, `select`, `check`,
`hover`, `wait`, `verify`, `upload`, `drag`, `scroll`, `mock_status`, `mock_data`).
field 계약은 ZeroTouch QA Brain Planner 와 1:1 일치. **단, ZeroTouch-QA execute 모드는
스칼라 list 형태의 scenario 를 받으므로**, 사용자는 위 wrapping object 의
`scenario` 필드만 추출해서 입력해야 한다 (또는 `scenario_dsl` 모드는 wrapping 없이
순수 list 만 emit — 아래 §3.6 출력 모드 정책 참조).

##### 출력 모드별 schema 계약 (자동화 친화)

| 모드 | 출력 형식 | scenario 추출 |
| --- | --- | --- |
| `plan` | Markdown 계획서 (8 섹션 — 7 + traceability) | N/A |
| `scenario_natural` | Markdown G/W/T + 근거 줄 | N/A |
| `scenario_dsl` | **순수 JSON list (`[{step:1,...}, ...]`)** + 별도 줄에 `# traceability:` 주석으로 source citation. 다른 텍스트 일절 금지 | 그대로 ZeroTouch-QA execute 입력 가능 |
| `both` | Markdown 계획서 / 자연어 시나리오 / **fenced code block** (`<!-- BEGIN SCENARIO_DSL -->\n```json\n...\n```\n<!-- END SCENARIO_DSL -->`) 로 14-DSL 분리 | 자동화 파서가 BEGIN/END marker 사이의 ```json 블록만 추출 → 그대로 입력 |

`scenario_dsl` 모드의 **순수 JSON only 강제** 는 사용자가 결과를 자동화 파이프라인에
바로 연결할 때 가장 안전하다. system prompt 가 이 모드일 때 markdown / 설명 / `<think>`
모두 금지를 명시한다.

### 2.3 데이터 흐름

#### 인덱싱 (one-time per file change)

```text
사용자
   ↓ (Dify console GUI 업로드)
[Dify Web UI]
   ↓
[Dify API: POST /datasets/{id}/document/create-by-file]
   ↓
[Dify ETL]
   - PDF: PyMuPDF (Dify 기본)
   - DOCX/PPTX: Dify 기본 ETL
   - CSV: Dify CSV 모드 (row-as-chunk)
   ↓
[Chunking] (chunk_size=500, overlap=50)
   ↓
[Embedding] bona/bge-m3-korean → 벡터 (1024차원)
   ↓
[Qdrant 저장]
```

#### 추론 (per user query)

```text
사용자
   ↓ (자연어 요청 — chat-messages API 또는 Dify Web UI)
[Test Planning Brain chatflow]
   ↓
[Knowledge Retrieval #1: kb_project_info]
   - 임베딩 (bge-m3-korean)
   - Qdrant top_k=5 검색
   - 청크 텍스트 + score 반환
   ↓
[Knowledge Retrieval #2: kb_test_theory]
   - 동일 (top_k=3)
   ↓
[Aggregator] context = project_chunks ++ theory_chunks
   ↓
[LLM: gemma4:26b]
   - system prompt = 의도 분류 + 1-shot
   - user prompt = query + context + (output_mode hint)
   ↓
[Answer] 텍스트 응답 (Markdown / 자연어 / JSON 또는 혼합)
```

### 2.4 기존 시스템과의 연계 (ZeroTouch-QA Brain)

| 항목 | 기존 (ZeroTouch QA Brain) | 신규 (Test Planning Brain) | 관계 |
| --- | --- | --- | --- |
| Dify 인스턴스 | port 18081 | 동일 인스턴스 | 같은 Dify 안에서 두 앱 |
| LLM 모델 | gemma4:26b | gemma4:26b | provider 공유 |
| Embedding | (사용 안 함) | bona/bge-m3-korean | 신규 추가 |
| KB | (사용 안 함) | 두 개 (project_info, test_theory) | 신규 추가 |
| Output | 14-DSL JSON (Planner) / mutation JSON (Healer) | Markdown + 자연어 + 14-DSL JSON | **14-DSL JSON 형식 동일** — 출력을 ZeroTouch-QA execute 입력으로 사용 가능 |
| API key | `dify-qa-api-token` | `dify-test-planning-api-token` (신규) | Jenkins credential 별도 |
| 운영 트리거 | Jenkins ZeroTouch-QA job | Dify console GUI (수동) | 자동화는 후속 |

**의도된 결합점**:
1. Test Planning Brain 이 14-DSL JSON 시나리오 emit.
2. 사람이 Dify Web UI 에서 결과 검토.
3. 사람이 JSON 을 복사해 ZeroTouch-QA execute 모드의 DOC_FILE 로 업로드.
4. ZeroTouch-QA 가 결정론적으로 실행 (Sprint 6 의 17/17 PASS 결과).

이 결합은 **수동**이다. 자동화는 후속 트랙 (예: "Test Planning → ZeroTouch-QA AutoLink")
의 검토 대상.

---

## 3. 입출력 명세

### 3.1 입력 — KB 문서 형식별 처리

| 형식 | Dify ETL | 청킹 정책 | 비고 |
| --- | --- | --- | --- |
| PDF | PyMuPDF (Dify 기본) | 500/50 | 표 / 이미지 텍스트 추출은 OCR 미사용 — 표는 마크다운 변환 권장 |
| DOCX | python-docx | 500/50 | 헤딩 구조 보존됨 |
| PPTX | python-pptx | 500/50 | 슬라이드 단위 chunking 권장 (수동 조정 가능) |
| CSV | Dify CSV 모드 | row-as-chunk | 표 헤더 + 1 행 = 1 청크. 행이 많으면 인덱싱 시간 ↑ |

#### 권장 사용 가이드

- **spec / 기획서**: PDF 또는 DOCX. PPTX 는 슬라이드 단위 청크가 의미를 자르므로 가능하면
  PDF 변환 후 업로드.
- **API 문서**: CSV 가 가장 자연. (endpoint, method, params, response, description) 컬럼.
- **테스트 이론**: PDF 가 표준 (책, 논문 발췌). DOCX 도 OK.

### 3.2 입력 — 사용자 자연어 요청 패턴

system prompt 가 다음 4 패턴을 의도 분류:

| 패턴 | 예시 | 출력 |
| --- | --- | --- |
| 계획서 only | "결제 모듈 테스트 계획서 만들어줘" | Markdown 계획서 |
| 자연어 시나리오 only | "로그인 기능 테스트 시나리오를 사람이 읽기 좋게 작성해줘" | Given/When/Then |
| 14-DSL only | "결제 시나리오를 자동 실행 가능한 JSON 으로 줘" | 14-DSL JSON |
| 둘 다 | "X 모듈의 계획서와 시나리오 둘 다" | Markdown + 자연어 + JSON |

분류 키워드 (system prompt 안):
- "계획서" / "test plan" → 계획서 그룹
- "시나리오" / "scenario" / "test case" → 시나리오 그룹
- "JSON" / "자동 실행" / "DSL" → 14-DSL emit
- "사람이 읽기" / "Given/When/Then" / "BDD" → 자연어 emit

### 3.3 출력 — 테스트 계획서 (IEEE 829-lite Markdown)

§2.2.5 의 7 섹션 템플릿. 각 섹션은:

- **목적**: spec 청크에서 retrieve 한 모듈 정의를 인용 (`> ... ` 인용 블록).
- **범위**: 포함/제외 항목을 spec 의 기능 목록 + 테스트 이론 KB 의 boundary
  (예: "성능 테스트는 별도 트랙") 에서 합성.
- **일정 / 자원**: spec 의 타임라인이 있으면 인용, 없으면 LLM 의 일반적 추정 (예: "QA
  엔지니어 1명 × 2주").
- **책임**: 테스트 이론 KB 의 RACI 매트릭스 패턴 사용.
- **위험과 대응**: spec 의 의존성 + 테스트 이론 KB 의 일반 위험 (예: "테스트 데이터 부족").
- **종료 기준**: 테스트 이론 KB 의 일반 기준 (모든 critical 시나리오 PASS, 회귀 통과율
  ≥ 95% 등).
- **산출물**: 테스트 시나리오 (자연어 + JSON), 회귀 결과, 결함 리포트.

### 3.4 출력 — 자연어 시나리오 (Given/When/Then)

§2.2.5 의 BDD 템플릿. 한 시나리오 당 1 개의 G/W/T 블록. 복합 시나리오는 여러 블록.

원칙:
- **Given**: 페이지 / 사용자 / 데이터 상태. 예: "사용자가 로그인된 상태이고 장바구니에 X 상품이 있다."
- **When**: 단일 명사 + 동사. compound 금지 (Sprint 6 §11.3.4 의 atomic 룰 적용).
- **Then**: 검증 가능한 단일 결과. 모호한 표현 ("정상 동작") 금지.
- **근거 줄**: 각 시나리오 끝에 `**근거**:` 줄로 출처 문서 / chunk_id / score / 인용 첫 30자
  명시. 비어 있으면 안 됨 (LLM 이 retrieval 결과를 무시한 hallucination 방지).

### 3.5 출력 — 14대 DSL JSON

§2.2.5 의 schema. ZeroTouch QA Brain Planner 와 동일 계약 + **traceability 블록**:

- 첫 step 은 `navigate` (자동 prepend 책임은 ZeroTouch-QA 의 executor 에 있으므로
  Test Planning Brain 도 가능하면 emit).
- atomic 1:1 매핑 (Sprint 6 §11.3.4): 한 자연어 시나리오 step = 한 JSON step.
- target 은 시맨틱 셀렉터 우선 (`role+name`, `text=`, `label=`, `placeholder=`).
- description 은 자연어 시나리오의 해당 줄을 그대로 복사 (추적성 1차).
- **traceability 블록**: §2.2.5 의 sibling list. step 별 (또는 step 그룹 별) source 정보
  포함. ZeroTouch-QA `_validate_scenario` 는 이 블록을 무시 (scalar list 만 검증), 따라서
  실행에는 영향 없음.

### 3.6 출력 모드별 자동화 계약 (구현 핵심)

§2.2.5 의 출력 모드별 schema 계약은 자동화 친화성의 핵심이다. 다시 정리:

#### `plan`

- 출력: Markdown 8 섹션 (IEEE 829-lite 7 + 근거 표).
- 자동화: 사람이 읽고 검토. 자동 파싱 대상 아님.

#### `scenario_natural`

- 출력: Markdown G/W/T 시나리오 (각 시나리오 끝에 `**근거**:` 줄).
- 자동화: 사람이 읽고 14-DSL 변환은 별도 (또는 같은 query 를 `scenario_dsl` 모드로 재호출).

#### `scenario_dsl` (자동화 핵심)

- 출력 계약: **첫 문자가 `[`, 마지막 문자가 `]` 인 순수 JSON list** + 그 다음 줄부터
  `# traceability:` 로 시작하는 주석 형태로 source citation. JSON 영역 안에는 `<think>`,
  markdown, 설명, fenced block 모두 **금지**.
- 예시:

  ```text
  [{"step": 1, "action": "navigate", "target": "", "value": "https://...", "description": "...", "fallback_targets": []},
   {"step": 2, "action": "click", "target": "#submit", "value": "", "description": "...", "fallback_targets": []}]
  # traceability:
  # step 1 — module_payment_v1.2.pdf chunk-7f2a (score=0.84)
  # step 2 — api_endpoints.csv row 12 chunk-9c3d (score=0.78)
  ```

- 파서 구현: `out.split("\n# traceability:", 1)[0]` 로 JSON 추출 → `json.loads()` →
  `_validate_scenario()`. 실패 시 모드 위반으로 reject (LLM retry).

#### `both`

- 출력 계약: Markdown 계획서 + 자연어 시나리오 + 14-DSL JSON 을 **strict fenced block**
  로 분리.
- 14-DSL 영역의 경계:
  - 시작: `<!-- BEGIN SCENARIO_DSL -->` 다음 줄
  - JSON: ```` ```json ```` ~ ```` ``` ```` 사이
  - 끝: `<!-- END SCENARIO_DSL -->` 이전 줄
- 파서 구현: regex `<!-- BEGIN SCENARIO_DSL -->.*?```json\n(.*?)\n```.*?<!-- END SCENARIO_DSL -->` (DOTALL).
  group(1) = JSON 텍스트. 마찬가지로 `json.loads` + `_validate_scenario`.

이 4 모드의 contract 위반 (예: scenario_dsl 모드인데 markdown 섞임) 은 system prompt 의
**모드별 금지 룰** 과 1-shot 예시로 모델에게 학습시킨다 (Sprint 6 §11.3 의 atomic 매핑
룰 학습 효과 입증됨).

---

## 4. 운영 정책

### 4.1 KB ingestion (Dify console GUI 직접 업로드)

#### 절차

1. 브라우저 접속: `http://localhost:18081` → admin 로그인 (admin@example.com / Admin1234!).
2. 좌측 메뉴 → **Knowledge** → 대상 KB 선택 (`kb_project_info` 또는 `kb_test_theory`).
3. 우측 상단 **Add documents** 버튼 → 파일 드래그 또는 선택.
4. 청킹 모드 확인 (자동 또는 사용자 정의 — 기본값 500/50 사용).
5. 인덱싱 시작 → 진행 상황 watcher → 완료 시 chunk 수 / score 분포 표시.

#### 권장 폴더 구조 (호스트 측, 향후 자동화 대비)

```text
data/kb-source/
├── project-info/
│   ├── spec/
│   │   ├── module_payment_v1.2.pdf
│   │   └── ...
│   ├── api/
│   │   ├── api_endpoints.csv
│   │   └── ...
│   └── 기획서/
│       ├── feature_login_v3.docx
│       └── ...
└── test-theory/
    ├── 설계기법/
    │   ├── boundary_value_analysis.pdf
    │   └── ...
    └── 프로세스/
        ├── v_model_v2.docx
        └── ...
```

이 구조는 **권장만** — 본 트랙에서는 GUI 업로드라 강제는 아니다. 향후 ingestion CLI
도입 시 이 구조를 자동 스캔.

### 4.2 재인덱싱 (변경 파일만)

- Dify console → 대상 KB → 문서 목록 → 변경된 문서 우측 메뉴 → **다시 인덱싱**.
- 전체 KB 재인덱싱은 사용 금지 (이전 청크 ID 가 변경되어 retrieval log 와 어긋남).
- 만약 청킹 정책 자체를 바꾸면 (예: 500 → 1000) 전체 재인덱싱 필요 — 이때만 KB 삭제 후
  재생성 권장.

### 4.3 청킹 / Retrieval 파라미터 + Context Size

| 항목 | 값 | 근거 |
| --- | --- | --- |
| chunk_size | 500 | 한국어 ~250-350 토큰. retrieval 정밀도와 청크 수 균형 |
| overlap | 50 | 10% — 청크 경계의 의미 단절 방지 |
| indexing_mode | high-quality | embedding 정확도 우선. economy 는 향후 비용 압박 시 |
| separator | `\n\n` (단락) | 단락 단위 보존 |
| **top_k (kb_project_info)** | **3** (보수적 시작) | context 부담 ↓ + 정밀 우선. 품질 부족 시 5 로 상향 검토 |
| **top_k (kb_test_theory)** | **2** (보수적 시작) | 동일. 이론 KB 는 잘못된 인용 비용 큼 → 정밀 우선 |
| score_threshold | 0.5 (project) / 0.6 (theory) | 약한 매칭 거절 |
| **OLLAMA_CONTEXT_SIZE** | **20480** (권장 상향, Sprint 6 의 12288 → +66%) | **Test Planning RAG 트랙 신규 결정** — RAG 구조가 retrieved context 를 추가로 차지하므로 ZeroTouch-QA Brain 보다 더 큰 책상이 필요. 자세한 근거 ↓ |

#### Context Size 확장 근거 (책상 비유)

LLM 한 번 호출 시 모델이 동시에 다룰 수 있는 토큰 수 = `context_size`. 이는 **책상 크기**
와 같다. 책상 위에 펼쳐야 하는 것:

```text
┌─ LLM 의 책상 (context_size) ─────────────────┐
│  [입력]                                       │
│  ┌─ system prompt (작업 지시서)              │
│  ├─ retrieved context (KB 검색 결과)         │  ← RAG 만의 추가 부담
│  └─ user query (사용자 질문)                 │
│  [출력]                                       │
│  └─ LLM 응답 (max_tokens 만큼 답안 작성)      │
│                                               │
│  ※ 입력 + 출력 합 > 책상 크기 → 답안 잘림    │
└───────────────────────────────────────────────┘
```

**Sprint 6 (ZeroTouch-QA Brain) 에서 12288 이 충분했던 이유**:

| 구성 | 토큰 |
| --- | --- |
| system prompt (atomic 1-shot) | ~3500 |
| user query (자연어 SRS 14항목) | ~700 |
| 출력 (`max_tokens=8192`) | ≤ 8192 |
| **합계** | ~12400 (한계 직전) |

build #1 의 1회 retry 가 정확히 이 빠듯함의 결과 (Sprint 6 §11.4.2).

**Test Planning Brain 에서 12288 이 부족한 이유**:

| 구성 | 토큰 | 비고 |
| --- | --- | --- |
| system prompt (4 모드 1-shot 4개 + traceability + strict contract) | **~5000** | Sprint 6 보다 길어짐 |
| **retrieved context** (5 chunks × ~250) | **~1250** | RAG 만의 부담 |
| user query (target_module + 상세 요청) | ~500 | 가변 |
| 출력 (`max_tokens=8192`) | ≤ 8192 | 계획서 + 시나리오 둘 다면 큼 |
| **합계** | **~14942** | **12288 한계 ~2700 초과** |

→ 첫 호출부터 truncation 발생 거의 확실 (T-15a 실측 전).

**왜 20480 인가** (16384 / 24576 비교):

| 옵션 | 값 | 안전 마진 (14942 기준) | gemma4:26b 추론 속도 영향 |
| --- | --- | --- | --- |
| 보수 | 16384 | ~1400 토큰 (10%) — 턱걸이 | -30% |
| **권장** | **20480** | **~5500 토큰 (27%)** | -50% |
| 안전 | 24576 | ~9600 토큰 (39%) | -65% |

`gemma4:26b` 의 native context 한계는 262144 (256k 토큰, Sprint 6 검증) 이라 모델 자체
한계는 없다. 단 KV cache 메모리 + 추론 속도 trade-off. **20480 이 균형점** — 16384 는
성장 여지 부족, 24576 은 속도 손실 큼.

이 값은 **초기 default**. T-12 검증 후 retrieval 품질이 낮으면 chunk_size 300 또는 700
로 조정. 추가 RAG 도큐먼트가 늘어 retrieved context 가 더 커지면 24576 으로 한 번 더
상향 검토 가능 (운영 결정 사항).

### 4.4 KB 사용 가이드라인 (저작권 / 민감 정보 / Embedding 모델 lock)

#### 4.4.1 저작권 / 민감 정보

`kb_test_theory` 에는 외부 표준 (ISO 29119, IEEE 829 등) 이나 책 발췌가 들어갈 수 있다.
**사내 사용 가능 범위가 명확하지 않은 자료는 업로드 금지**. 다음을 README 운영 가이드에
명시:

- **업로드 가능**: 사내 작성 테스트 정책 / 가이드 / 회귀 절차, 공개 표준의 요약 / 사내
  요약본 (자체 제작), 공개 라이선스 (CC, MIT 등) 자료.
- **업로드 금지**: 유료 표준 PDF 원본 (ISO/IEC 등 — 사용자별 라이선스), 저작권 있는 책
  발췌 (출판사 별도 허가 없음), 외부 컨퍼런스 슬라이드 원본 (재배포 금지 표시).
- **권장 대안**: 외부 자료를 **사내 인력이 자기 표현으로 요약 / 의역** 한 docx 를
  업로드. 인용 시 출처는 명시 (저작권은 표현 vs 아이디어 분리).

`kb_project_info` 도 마찬가지로 민감 정보 (개인정보, 비밀번호, 내부 IP, API key 원문 등)
가 포함된 spec / API doc 은 사전 마스킹 후 업로드. KB 는 LLM context 로 들어가므로 한 번
인덱싱되면 retrieval log 에 영구 기록될 수 있다.

#### 4.4.2 Embedding 모델 교체 금지 (Qdrant collection lock)

KB 의 벡터 DB (Qdrant) 는 **첫 문서 인덱싱 시점에 차원수가 고정** (collection lock) 된다.
한 번 lock 되면 다른 차원수의 임베딩 모델로 교체 불가.

**도서관 비유**: KB 는 도서관의 책 색인 시스템과 같다. 한 번 시스템 (예: Dewey Decimal
1024차원 vs Library of Congress 1536차원) 을 정하면 모든 책에 그 시스템 라벨을 붙인다.
시스템 바꾸려면 모든 책 재라벨링 (전체 재인덱싱) 필요.

**bge-m3-korean 의 차원**: 1024 (base bge-m3 와 동일 — 한국어 fine-tuning 으로 차원 변경
가능성 낮음). 첫 인덱싱 시점에 자동 lock.

**운영 룰**:

- KB 한 번 생성 후 **embedding 모델 교체 금지**.
- 모델 변경이 꼭 필요하면 다음 절차 (위험 + 시간 비용 큼):
  1. 기존 KB 두 개 모두 삭제 (`DELETE /console/api/datasets/{id}`).
  2. provision.sh 의 `EMBEDDING_MODEL` env 갱신 후 재기동 → 새 차원으로 재등록.
  3. 빈 KB 두 개 재생성.
  4. 모든 문서 재업로드 (Dify console GUI 수작업 — N개 문서 × 인덱싱 시간).
  5. Test Planning Brain chatflow 의 dataset_id placeholder 가 새 ID 로 substitute 되도록
     provision.sh 재실행 (또는 chatflow 재import).

- README 운영 매뉴얼 (T-17) 에 위 절차 명시. 사용자가 우발적으로 모델 교체하지 않도록
  경고.

### 4.5 모델 SLA

| 항목 | 임계값 | 측정 |
| --- | --- | --- |
| Embedding 응답 시간 (32 청크) | p95 ≤ 30s | Dify dataset 인덱싱 로그 |
| LLM 응답 시간 (계획서 1건) | p95 ≤ 120s | chatflow run 로그 |
| Retrieval recall (사람 판정) | 정성적 — 첫 5 chunks 가 적절한가 | T-12 검증 |
| 출력 JSON `_validate_scenario` 통과율 | ≥ 90% | T-13 검증 |

ZeroTouch-QA 의 LLM SLA 와 같은 metric 표는 본 트랙에서는 명시적으로 만들지 않는다
(GUI 운영이라 자동 metric 수집 없음). 향후 Jenkins 자동화 트랙에서 추가.

---

## 5. 태스크 분해

### 5.0 Phase -1 — Sister 프로젝트 fixture 인용 (spike 폐기)

**원래 계획**: spike 3건 (KB API body / Retrieval 노드 schema / dataset id substitute) 을
PoC 로 수행 후 PLAN 갱신.

**변경 — 2026-04-27 review**: 같은 repo 의 sister 프로젝트
[`code-AI-quality-allinone/scripts/`](../code-AI-quality-allinone/scripts/) 가 동일
패턴을 production 검증 중. spike 3건의 결론을 검증된 fixture / 코드 인용으로 대체.

| ID | 원래 spike | 대체 인용 | 결과 (실측 기반) |
| --- | --- | --- | --- |
| ~~T-spike-1~~ | KB 생성 API body | `code-AI-quality-allinone/scripts/dify-assets/code-context-dataset.json` + `provision.sh:402-525` | sister 의 KB body 는 다음 필드를 명시: `name` / `indexing_technique="high_quality"` / `embedding_model="bge-m3"` / `embedding_model_provider="langgenius/ollama/ollama"` / `permission="only_me"` / `retrieval_model{search_method, top_k, score_threshold_enabled, ...}`. 안전망으로 **(a) embedding provider 등록 + (b) workspace default-model 설정 + (c) KB body 에도 embedding_model 명시** 세 단계 모두 수행. 본 트랙도 동일 패턴 채택. |
| ~~T-spike-2~~ | Retrieval 노드 schema | `code-AI-quality-allinone/scripts/dify-assets/sonar-analyzer-workflow.yaml:251-267` (실 production yaml) | 1.13.3 의 Knowledge Retrieval 노드는 `dataset_ids: list[str]` 정식 multi-dataset 지원. `top_k`, `score_threshold_enabled`, `score_threshold` 는 `multiple_retrieval_config` 하위. 추가로 `query_variable_selector: list[str]` 로 어느 변수를 query 로 쓸지 path 지정 (`['start', 'kb_query']` 형태). PLAN §2.2.3 갱신. |
| ~~T-spike-3~~ | dataset id substitute PoC | `code-AI-quality-allinone/scripts/provision.sh:580` (sed 패턴) | sister 는 placeholder 문자열 대신 **빈 리스트 `dataset_ids: []` 를 sed target** 으로 잡음 (`sed -i.bak "s\|dataset_ids: \[\]\|dataset_ids: ['$dataset_id']\|g"`). 다른 필드 충돌 위험 zero. PLAN T-05 의 placeholder 도 같은 패턴 채택. |

**PLAN 적용**: 위 결과를 §2.2.3 (Knowledge Retrieval 노드 평면 표기 → `multiple_retrieval_config`
하위 표기) 와 §5.3 T-05 (placeholder 형식을 `dataset_ids: []` 빈 리스트 sed target 으로
변경) 에 반영. T-02 도 sister 의 default-model 선설정 패턴 채택.

**spike 폐기로 절약된 시간** 은 §5.5 의 사전 측정 두 건 (T-13a/T-15a) 를 우선 수행하는
데 투입한다 — 실제로 더 큰 위험 (LLM 출력 결정성 + context budget overflow) 을 코드
작성 전에 측정해 PLAN 가정을 검증한다.

#### 5.0.1 사전 측정 우선순위 (spike 대체)

| ID | 작업 | 측정 방법 | 합격선 |
| --- | --- | --- | --- |
| T-pre-13a | gemma4:26b 의 `scenario_dsl` 모드 결정성 사전 측정 | 신규 chatflow + 임시 KB 1개에 sample 문서 1건 업로드 → 동일 query 10회 호출 → 응답 첫 글자 `[` / 끝 `]` 비율 + `_validate_scenario` 통과율 측정 | ≥ 80% (90% 미달 시 LLM 노드 `structured_output` 옵션 + JSON schema 강제 필요 검토) |
| T-pre-15a | context budget 실측 | 가장 긴 SRS query 1회 호출 → Dify run log 의 prompt_tokens + completion_tokens 합산 | ≤ 18000 (마진 2480) — 합격 시 20480 충분, 초과 시 24576 으로 한 번 더 상향 |

### 5.1 Phase 0 — 인프라 (Dockerfile / entrypoint.sh / provision.sh)

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-01 | Embedding provider 등록 — `bona/bge-m3-korean:latest` 를 Dify Ollama provider 의 `text-embedding` 모델로 추가 | `provision.sh` | §2-3f 신규 단계 (LLM 등록 직후) — **이미 사전 patch 완료** | provision 후 DB `provider_model_credentials` 에 model_type=text-embedding 레코드 |
| T-01a | **복수 chatflow seed 인프라** — Dockerfile / entrypoint.sh / provision.sh 의 `OFFLINE_DIFY_CHATFLOW_YAML` 단수 처리를 복수 처리 (`OFFLINE_DIFY_CHATFLOW_YAMLS` 또는 디렉토리 스캔) 로 리팩토링 | `Dockerfile`, `entrypoint.sh`, `provision.sh` | (1) `Dockerfile` 이 `dify-chatflow.yaml` + `test-planning-chatflow.yaml` 둘 다 `/opt/` 로 COPY (2) `entrypoint.sh` 가 두 env 모두 export (3) `provision.sh` 의 §2-4 (Chatflow import) 가 loop 으로 두 YAML 모두 처리 | fresh provision 후 두 chatflow 모두 import + publish + API key 발급 |
| T-01b | Offline 반입 가이드 갱신 — `bona/bge-m3-korean:latest` 모델을 호스트 Ollama 에 사전 pull 해야 함을 README 의 사전 준비 섹션에 추가 | `README.md` | "Mac/Windows 사전 준비" 의 "Ollama 모델 pull" 줄에 embedding 모델 추가 | 검토 |
| T-02 | 빈 KB 자동 생성 — `kb_project_info`, `kb_test_theory` 두 개. POST `/console/api/datasets` (T-spike-1 결과 body 사용) | `provision.sh` | §2-5 신규 단계 (Chatflow import 이전). 응답에서 `id` (UUID) 캡처 → env 변수로 저장 | provision 후 `/console/api/datasets` 목록에 두 KB 노출 + ID env 가 다음 단계로 전달 |
| T-02a | KB 생성 시 indexing_technique=high-quality, embedding_model=bona/bge-m3-korean 명시 | `provision.sh` | T-02 안의 POST body | DB `datasets` 테이블 검증 |
| T-02b | KB 가 이미 존재하면 skip (idempotent) — 단, 기존 KB ID 를 캡처해서 동일하게 env 로 전달 | `provision.sh` | T-02 안의 분기 | 두 번째 provision 시 중복 생성 안 되고 ID 는 정확히 전달 |

### 5.2 Phase 1 — 운영 절차 문서 (코드 없음)

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-03 | KB GUI 업로드 절차 문서화 | `README.md` (신규 §3.10) | 단계별 스크린샷 캡션 + 권장 폴더 구조 | 검토 |
| T-04 | 변경 파일 재인덱싱 절차 문서화 | `README.md` (§3.10 안 sub-section) | "다시 인덱싱" GUI 메뉴 사용법 | 검토 |

### 5.3 Phase 2 — Chatflow 설계

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-05 | Chatflow YAML 신규 작성 — placeholder 형태로 dataset_id 박음 (`__KB_PROJECT_INFO_ID__`, `__KB_TEST_THEORY_ID__`). T-spike-2 결과 schema 와 일치 | `test-planning-chatflow.yaml` | Start → Retrieval×2 → Aggregator → LLM → Answer 노드 | YAML 문법 + 노드 그래프 시각 검토 |
| T-05a | Start 노드 inputs: user_query (text), output_mode (select: plan/scenario_natural/scenario_dsl/both), target_module (text optional) | T-05 안 | Start node JSON | Dify import 후 manual 호출 가능 |
| T-05b | Retrieval #1 (kb_project_info), top_k=**3** (보수적 시작), score_threshold=0.5, dataset_id=`__KB_PROJECT_INFO_ID__` | T-05 안 | Retrieval node JSON | 검색 결과가 3개 이하 정상 |
| T-05c | Retrieval #2 (kb_test_theory), top_k=**2** (보수적 시작), score_threshold=0.6, dataset_id=`__KB_TEST_THEORY_ID__` | T-05 안 | Retrieval node JSON | 동일 |
| T-05d | Variable Aggregator — 두 retrieval 결과 + 각 chunk 의 source_document_name + retrieval_score 를 별도 변수로 보존 (LLM 의 traceability 블록 작성용) | T-05 안 | Aggregator node JSON | 변수 결합 + 메타 보존 |
| T-05e | LLM 노드 (gemma4:26b, max_tokens=8192, temp=0.1) | T-05 안 | LLM node JSON | provision.sh 의 model 등록과 일치 |
| T-05f | Answer 노드 — LLM 출력 그대로 emit | T-05 안 | Answer node JSON | 검토 |
| T-06 | provision.sh 의 §2-4 chatflow import 단계가 두 YAML 모두 처리하는 loop 으로 리팩토링됨 (T-01a 와 통합). Test Planning Brain 추가 시: KB ID env 를 substitute 후 import → publish → API key 발급 | `provision.sh` | loop 안의 분기 | provision 후 두 앱 모두 노출 + 두 API key 모두 발급 |
| T-06a | API key (`dify-test-planning-api-token`) 를 Jenkins credentials 에 **선등록** — 본 트랙에서는 사용처 없음. 향후 자동화 트랙 (Test Planning → ZeroTouch-QA AutoLink) 대비 | `provision.sh` | T-06 안의 POST `/credentials/...` | Jenkins console 에 credential 노출 (사용처 없음 명시) |

### 5.4 Phase 3 — System prompt 작성

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-07 | System prompt 의도 분류 룰 — 4 패턴 (§3.2) | `test-planning-chatflow.yaml` 안 LLM node text | 분류 표 + 키워드 룰 | 4 패턴 각각 manual 테스트 |
| T-08 | 테스트 계획서 1-shot 예시 (IEEE 829-lite 7 섹션 + traceability 표 8번째 섹션) | T-07 안 | 1-shot 템플릿 | 출력이 7+1 섹션 모두 포함 + 근거 인용 |
| T-09 | 자연어 시나리오 1-shot 예시 (Given/When/Then + 근거 줄) | T-07 안 | 1-shot 템플릿 | atomic + 검증 가능 + 근거 줄 비어있지 않음 |
| T-10 | 14-DSL JSON schema 설명 + 1-shot 예시 (ZeroTouch-QA 호환 + traceability sibling 블록) | T-07 안 | schema 표 + 1-shot 템플릿 | 출력 JSON 의 `scenario` 필드가 `_validate_scenario` 통과 |
| T-10a | `<think>` 출력 금지 룰 (Sprint 6 §11.3.3 학습) | T-07 안 | 1줄 룰 | 출력에 think 블록 없음 |
| T-10b | **출력 모드별 strict contract** — `scenario_dsl` 모드: 순수 JSON only (markdown / 설명 / fence 모두 금지). `both` 모드: `<!-- BEGIN SCENARIO_DSL -->` ~ `<!-- END SCENARIO_DSL -->` 경계 강제. §3.6 의 4 모드 계약을 1-shot 예시로 학습 | T-07 안 | 모드별 1-shot 4 개 | 자동 파서가 각 모드 출력에서 JSON 100% 추출 |

### 5.5 Phase 4 — 검증

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-11 | 샘플 문서 세트 작성 — `examples/test-planning-samples/` (4개 형식 모두 포함) | `examples/test-planning-samples/` | (a) `spec.pdf` 가짜 모듈 spec 1쪽 (b) `api.csv` 5 endpoint (c) `feature_login.docx` 사용자 시나리오 1쪽 (d) `architecture.pptx` 슬라이드 3장 (e) `test_theory.pdf` boundary value analysis 1쪽 | 5개 형식 모두 GUI 업로드 시 인덱싱 PASS + chunk 수 > 0 |
| T-12 | end-to-end 검증 1: 계획서 요청 | (수동 호출) | 결과 markdown 캡처 | 8 섹션 모두 포함 (7 IEEE + traceability) + spec 인용 + 이론 적용 |
| T-13 | end-to-end 검증 2: 시나리오 요청 (`scenario_dsl` 모드) | (수동 호출) | 순수 JSON 출력 캡처 | first-char `[` last-char `]` + `_validate_scenario` 통과 + traceability 주석 존재 |
| T-13a | end-to-end 검증 3: 시나리오 요청 (`both` 모드) | (수동 호출) | markdown + fenced JSON 출력 | regex 파서가 BEGIN/END marker 사이 JSON 100% 추출 |
| T-14 | end-to-end 검증 4: scenario_dsl JSON 을 ZeroTouch-QA execute 모드로 실행 | Jenkins 수동 빌드 | run_log step 모두 PASS (sample scope) | 결정론 PASS |
| T-15 | retrieval recall 정성 평가 | 수동 | 첫 3+2 chunks 가 query 와 의미적으로 정합한가 | 검토 |
| T-15a | context budget 실측 — 실 호출 1회의 prompt + retrieved + output 토큰 합 측정. 12288 한계 안에 들어오는가 | (수동 호출) | Dify run log + tiktoken 측정 결과 | 합계 < 11500 (마진 800). 초과 시 OLLAMA_CONTEXT_SIZE 16384 상향 검토 |

### 5.6 Phase 5 — 문서

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-16 | 본 PLAN 의 변경 추적 (별도 트랙 closure 단락) | `PLAN_TEST_PLANNING_RAG.md` (본 파일) | §6 closure 추가 | 검토 |
| T-17 | README §3.10 신설 — Test Planning RAG 운영 가이드 | `README.md` | KB 업로드 / 챗봇 사용 / 결과 → ZeroTouch-QA 연계 절차 | 검토 |
| T-18 | architecture.md 헤더에 별도 트랙 줄 추가 (선택) | `architecture.md` | "Test Planning RAG" 별도 트랙 명시 | 검토 |

### 5.7 의존성 그래프

```text
T-spike-1, T-spike-2, T-spike-3 (병렬, 모두 코드 없는 PoC)
        ▼
T-01 (embedding provider — 사전 patch 완료)
  ├─→ T-01a (복수 chatflow seed 인프라 리팩토링)  ◀ NEW
  └─→ T-01b (offline 반입 가이드 갱신)            ◀ NEW
        ▼
T-02 (KB 자동 생성, ID 캡처)
  └─→ T-02a, T-02b
        ▼
T-05 (chatflow YAML — placeholder 박은 채로)
  ├─→ T-05a~f (노드 구성)
  └─→ T-07~T-10b (system prompt — 모드별 strict contract 포함)
        ▼
T-06 (auto-import — KB ID substitute + publish + API key)
  └─→ T-06a (Jenkins credential 선등록)
        ▼
rebuild required
        ▼
T-11 (5 형식 샘플 문서)  ◀ pdf/csv/docx/pptx 모두 포함
        ▼
T-12, T-13, T-13a, T-14, T-15, T-15a (검증, 병렬 가능)
        ▼
T-16, T-17, T-18 (문서)

T-03, T-04 는 코드 없음 → 검증 후 화면 캡처와 함께 작성하면 정확
```

총 **3 spike + 18 태스크 + 17 sub-task = 38 작업 단위** (보강 후). 코드 변경 파일:
`Dockerfile`, `entrypoint.sh`, `provision.sh`, `test-planning-chatflow.yaml` (신규),
`README.md`, `architecture.md`, `examples/test-planning-samples/` (신규),
`examples/spike/` (신규, T-spike fixture 보존용).

---

## 6. 검증 시나리오

### 6.1 단위 검증 (provision.sh / chatflow YAML)

- T-01 직후: `psql -c "SELECT model_name, model_type FROM provider_model_credentials WHERE model_type='text-embedding'"` → 1 행.
- T-02 직후: `curl /console/api/datasets` → 두 KB 노출.
- T-05 직후: `python3 -c "import yaml; yaml.safe_load(open('test-planning-chatflow.yaml'))"` → 파싱 성공.
- T-06 직후: `/console/api/apps?...` → `Test Planning Brain` 앱 노출.

### 6.2 통합 검증 (KB → 챗봇 → 14-DSL → ZeroTouch-QA execute)

End-to-end 절차:

1. fresh provision (`build.sh --redeploy --fresh`).
2. Dify GUI 에서 4개 형식 (csv/pdf/pptx/docx) 샘플 업로드 → 인덱싱 완료.
3. Dify Web UI 에서 `Test Planning Brain` 챗봇에 "결제 모듈 테스트 계획서 + 시나리오" 입력.
4. 응답 검증:
   - Markdown 계획서 7 섹션
   - 자연어 시나리오 (atomic Given/When/Then)
   - 14-DSL JSON (`_validate_scenario` 통과)
5. 14-DSL JSON 을 파일로 저장 → Jenkins ZeroTouch-QA Job 의 RUN_MODE=execute, DOC_FILE 로 업로드.
6. ZeroTouch-QA 결과 검증: 모든 step PASS (또는 사람 검토 후 일부 step 만 의도적 fail).

### 6.3 운영 SLA (정성)

- 한 챗봇 호출 (계획서 + 시나리오 + JSON) end-to-end 시간 ≤ 5 분.
- 사용자 입력에서 "결제" 가 나오면 retrieval 결과의 첫 5 chunks 중 ≥ 3 개가 결제 관련.
- 일주일 동안 5 회 이상 사용 후 사람 판정으로 "초안 작성 시간 50% 이상 단축" 확인.

---

## 7. 한계와 후속

### 7.1 알려진 한계

1. **Retrieval 한계**: spec 문서가 표 위주이거나 다이어그램에 정보가 있으면 PDF ETL 이
   놓친다. → 사용자가 표를 마크다운으로 변환 후 업로드 권장.
2. **계획서 일정 / 자원 추정**: spec 에 명시 없으면 LLM 의 일반 추정 — 사람 검토 필수.
3. **JSON 출력 결정성**: ZeroTouch-QA Brain 의 Sprint 6 보강 (atomic SRS, `<think>` ban
   등) 을 동일하게 적용했지만, 본 챗봇은 retrieved context 가 매번 다르므로 결정성이
   상대적으로 낮을 가능성. 측정 후 결정.
4. **GUI 업로드의 한계**: 수동 업로드라 변경 추적이 사람 책임. 대규모 spec 변경 시
   자동화 필요 — 후속 트랙.

### 7.2 후속 트랙 후보

- **Test Planning → ZeroTouch-QA AutoLink**: 챗봇 결과 14-DSL 을 자동으로 Jenkins
  execute 빌드로 트리거. webhook 또는 Jenkins job parameter 자동화.
- **KB Ingestion CLI**: §4.1 권장 폴더 구조를 자동 스캔해 변경 파일만 Dify API 업로드.
- **사내 템플릿 매핑**: 회사/팀의 테스트 계획서 표준 양식이 정해지면 그것에 맞춰 1-shot 갱신.
- **Multi-language KB**: 영어/일본어 spec 도 동시 RAG. 임베딩 모델 multilingual 변경 검토.
- **Spec change detection**: spec 청크 hash 비교로 변경된 모듈 식별 → 영향받은 시나리오
  자동 재생성.
- **Embedding 모델 변경 procedure**: Qdrant collection lock 으로 인해 모델 교체가 어려운
  현재 상태에서, 운영 중인 KB 의 embedding 모델을 안전하게 교체하는 절차 수립. 후보:
  (a) 새 KB 를 별도 이름 + 새 모델로 생성 → 모든 문서 재업로드 → chatflow yaml 의
  `dataset_ids` 만 새 ID 로 재import → 검증 후 구 KB 삭제 (zero-downtime). (b) Qdrant
  collection migration tool (Dify 공식 미지원) 자체 제작. 첫 단계로 (a) 만 검토.
- **Hybrid 검색 튜닝**: KB body 의 `search_method: hybrid_search` 의 BM25 가중치 / dense
  weighting 비율 튜닝. 한국어 고유명사 (모듈명, API endpoint) 매칭 정확도 측정 후 결정.

---

## 8. 의존성과 위험

### 8.1 모델 / 인프라

| 의존성 | 현재 상태 | 위험 | 완화 |
| --- | --- | --- | --- |
| `gemma4:26b` (Ollama) | 호스트에 설치됨 | 모델 변경 시 Sprint 6 보강 효과 무효화 가능 | README 사전 준비에 모델 명시 (T-01b) |
| `bona/bge-m3-korean:latest` (Ollama) | 호스트에 설치됨 (1.2GB) | 모델 누락 시 Phase 0 부터 실패 | 오프라인 반입 list / README 사전 준비 줄에 추가 (T-01b) |
| Dify 1.13.3 KB API schema | 운영 중 | 다음 메이저 (1.14+) 에서 바뀔 수 있음 | T-spike-1 으로 1.13.3 schema 실측 fixture 보존 (`examples/spike/`) |
| Dify 1.13.3 Chatflow Knowledge Retrieval 노드 schema | 미검증 | dataset_id 필드명 / 형식이 PLAN 가정과 다를 수 있음 | T-spike-2 로 export 결과 실측 후 PLAN §2.2.3 갱신 |
| Qdrant (Dify 내장) | 운영 중 | 디스크 부족 시 인덱싱 실패 | KB 업로드 가이드에 디스크 모니터링 명시 |
| 호스트 Ollama Embedding 처리량 | 미측정 | 32 청크 임베딩 시간 미상 — 대용량 KB 업로드 시 hang 가능 | T-12 검증 시 측정 + §4.5 SLA 행 추가 |

### 8.2 KV cache / context 부담

- Sprint 6 의 `OLLAMA_CONTEXT_SIZE=12288` 이 Test Planning Brain 에도 적용됨 (같은
  provider). retrieved context (5 + 3 chunks × 500 chars × 2 bytes/char ≈ 8000 chars
  ≈ 2000 tokens) + system prompt (~2000) + user query (~200) + max_tokens=8192 = 합계
  ~12400 — context 한계 12288 에 거의 닿음.
- **위험**: 사용자 query 가 길거나 retrieved chunk 수가 많으면 context overflow.
- **완화**:
  - top_k 를 5+3 → 3+2 로 줄이는 것이 안전 마진을 늘림.
  - 또는 OLLAMA_CONTEXT_SIZE 를 16384 로 상향 (Sprint 6 에서 거절했지만 본 트랙은 부담
    유형이 다름 — KB context 가 길어 context_size 상향이 합리적일 수 있음).
  - 첫 검증 (T-12) 에서 context overflow 발생 시 즉시 결정.

### 8.3 retrieval quality 의 사용자 입력 의존성

- 사용자가 "테스트 계획서 좀 만들어줘" 처럼 모호하게 query 하면 retrieval 이 random.
- **완화**: Start 노드의 `target_module` 입력을 권장 (선택이지만 정확도 ↑).
- Few-shot 안에 좋은 query 패턴 예시 포함 ("결제 모듈의 V1.2 spec 기준 통합 테스트 계획서").

### 8.4 운영 위험

| 위험 | 영향 | 완화 |
| --- | --- | --- |
| 사용자가 KB 에 민감 정보 (개인정보, 비밀번호) 업로드 | 노출 | KB 업로드 가이드에 "민감 정보 사전 마스킹" 명시 |
| 계획서 출력 품질이 낮아 사람이 신뢰 안 함 | 사용 안 함 → ROI 0 | 첫 5 회 사용 후 사람 판정 — 부족하면 1-shot / 청킹 / top_k 튜닝 |
| 14-DSL 출력이 ZeroTouch-QA 에서 fail | 결합 의의 상실 | Sprint 6 의 결정성 보강이 본 챗봇에도 동일 적용 — 첫 검증 (T-14) 에서 확인 |

---

## 9. 결정 사항 기록

| 결정 | 선택 | 일자 | 근거 |
| --- | --- | --- | --- |
| Ingestion 트리거 | Dify console GUI 직접 업로드 | 2026-04-27 | 운영 단순. 자동화는 후속 트랙 |
| 재인덱싱 정책 | 변경 파일만 | 2026-04-27 | retrieval log 의 청크 ID 안정성 |
| Jenkins 통합 | 이번 트랙은 제외 (단 API key 는 선등록) | 2026-04-27 | 사람 검토 후 수동 실행이 더 안전. 후속 자동화 대비 credential 만 미리 박음 |
| Embedding 모델 | `bona/bge-m3-korean:latest` | 2026-04-27 | 한국어 우선, 호스트에 이미 설치 |
| 트랙 명명 | "Test Planning RAG" 별도 트랙 | 2026-04-27 | 본 PLAN 과 분리해 운영 책임 명확화 |
| KB 분리 여부 | 두 개 (`kb_project_info`, `kb_test_theory`) | 2026-04-27 | 의미 그룹 다름 + 검색 가중치 분리 |
| 출력 형식 | 셋 다 (계획서 / 자연어 시나리오 / 14-DSL JSON) | 2026-04-27 | 사용자 의도 따라 분기 |
| 산출물 템플릿 | IEEE 829-lite | 2026-04-27 | 사내 템플릿 도입 전 일반 표준 |
| LLM 모델 | `gemma4:26b` (기존 공유) | 2026-04-27 | provider 통합 운영 |
| Dify 인스턴스 | 같은 인스턴스 (port 18081) 에 두 번째 앱 | 2026-04-27 | 인프라 단순 |
| **Dataset id 주입 방식** | provision.sh 가 KB 생성 후 ID 캡처 → chatflow YAML placeholder substitute → import | 2026-04-27 (review) | Dify 1.13.3 의 Knowledge Retrieval 노드는 dataset 을 ID 로 참조. 이름 참조 미지원 가정. T-spike-2 로 검증. |
| **복수 chatflow seed 인프라** | `OFFLINE_DIFY_CHATFLOW_YAML` 단수 → 복수 (`OFFLINE_DIFY_CHATFLOW_YAMLS` 또는 디렉토리 스캔) 리팩토링 | 2026-04-27 (review) | 기존 ZeroTouch QA Brain + 신규 Test Planning Brain 두 chatflow 동시 seed 필요 |
| **Top_k 보수적 시작** | project=3, theory=2 (5+3 → 3+2) | 2026-04-27 (review) | context budget 12288 한계에 안전 마진 확보. 품질 부족 시 5+3 으로 상향 검토 |
| **Traceability 강제** | 모든 출력에 source_documents / chunk_id / retrieval_score 인용 강제 | 2026-04-27 (review) | §1.2 의 "역추적" 가치를 출력 schema 에 박지 않으면 hallucination 검증 불가 |
| **출력 모드별 strict contract** | `scenario_dsl` = 순수 JSON only / `both` = `<!-- BEGIN/END SCENARIO_DSL -->` fence 강제 | 2026-04-27 (review) | 자동화 파서가 안정적으로 JSON 추출 — Markdown 혼합으로 깨지는 비용 방지 |
| **Phase -1 Spike 선행** | 코드 작성 전 Dify API/Chatflow schema 실측 spike 3건 (T-spike-1/2/3) | 2026-04-27 (review) | 가장 큰 미지수 (KB 생성 body, dataset id 주입 방식) 를 PoC 로 사전 해소 |
| **Spike 폐기 + Sister 인용** | T-spike-1/2/3 → sister 프로젝트 (`code-AI-quality-allinone/scripts/`) 의 production 검증 코드 인용으로 대체. 절약 시간을 T-pre-13a/T-pre-15a 사전 측정에 재투입 | 2026-04-27 (verification) | 같은 repo 의 sister 가 동일 패턴 운영 중. 직접 source code 검증 (`provision.sh:402-525`, `code-context-dataset.json`, `sonar-analyzer-workflow.yaml:251-267`) |
| **OLLAMA_CONTEXT_SIZE 20480 채택** | Sprint 6 의 12288 → **20480** (66% 상향) | 2026-04-27 (verification) | Test Planning RAG 는 retrieved context (~1250 tokens) 가 추가로 차지. 14942 토큰 추정치 + 안전 마진 27% 확보. 16384 는 마진 부족, 24576 은 추론 속도 -65% 부담. 20480 이 균형점 |
| **KB body schema (sister 안전망)** | embedding provider 등록 + workspace default-model + KB body 의 `embedding_model` 셋 모두 명시 (belt-and-suspenders) | 2026-04-27 (verification) | Sister `code-context-dataset.json` 직접 확인 — agent 의 "body 에 embedding_model 없음" 주장 오류. 세 단계 중 하나만 빠져도 high_quality 모드 실패할 위험 |
| **`search_method: hybrid_search` 채택** | KB `retrieval_model.search_method` 를 sister 와 동일하게 hybrid (BM25 + dense vector) | 2026-04-27 (verification) | 한국어 고유명사 (모듈명, API endpoint) 매칭에 dense embedding 만으로는 부족. BM25 결합으로 정밀도 ↑ |
| **Embedding 모델 lock 운영 룰** | KB 한 번 생성 후 embedding 모델 교체 금지. 교체 필요 시 KB 삭제 + 재생성 + 모든 문서 재업로드. README §4.4.2 명시 + 비유 (도서관 색인 시스템) | 2026-04-27 (verification) | Qdrant collection 이 첫 인덱싱 시점에 차원 lock. 사용자 우발 교체 방지 위해 명시적 운영 룰 + 후속 트랙 (§7.2 의 zero-downtime 교체 procedure) |

---

## 10. 변경 이력

| 일자 | 항목 | 비고 |
| --- | --- | --- |
| 2026-04-27 | 초안 작성 | 본 PLAN 신설. T-01 (embedding provider 등록) 만 사전 patch 됨, 나머지는 미착수. |
| 2026-04-27 | review 반영 — 5 우선 보강 + 5 세부 보강 | (1) 복수 chatflow seed 인프라 리팩토링 (T-01a) — Dockerfile/entrypoint/provision.sh `OFFLINE_DIFY_CHATFLOW_YAMLS` 또는 dir scan 으로 전환. (2) Dataset id 주입 메커니즘 — chatflow YAML 에 placeholder 박고 provision.sh 가 KB ID 캡처 후 substitute. T-spike-1/2/3 신설. (3) traceability 스키마 — 모든 출력에 source_documents/chunk_id/retrieval_score 강제 (§2.2.5, §3.3~3.5). (4) `scenario_dsl` 모드 순수 JSON only + `both` 모드 strict fence (§3.6). (5) 모델 / 오프라인 반입 정합성 — gemma4:26b + bge-m3-korean 모두 README/사전 준비 줄에 (T-01b). 세부: T-11 샘플 5 형식 (pdf/csv/docx/pptx + theory pdf), top_k 보수적 (3+2), 저작권 가이드 (§4.4), T-06a 선등록 명확화. 총 작업 단위 30 → 38 로 확장. |
| 2026-04-27 | 실현 가능성 검증 + sister 프로젝트 직접 인용 | 38 작업 단위에 대한 web search + 컨테이너 API probe + sister source code 직접 검증. (a) **OLLAMA_CONTEXT_SIZE 20480 채택** — Test Planning RAG 의 retrieved context 추가 부담으로 12288 부족. 책상 비유로 §4.3 설명. (b) **KB embedding lock 운영 룰** — Qdrant collection 첫 인덱싱 시 차원 lock. §4.4.2 신설 + 도서관 비유 + 교체 절차 명시. (c) **Phase -1 spike 폐기** — sister 프로젝트 (`code-AI-quality-allinone/scripts/`) 가 동일 패턴 production 검증 중. T-spike-1/2/3 → sister 인용표 + T-pre-13a/T-pre-15a 사전 측정 우선화. (d) **Sister 직접 검증으로 정정** — agent 요약의 "embedding_model 이 KB body 에 들어가지 않음" 주장은 오류. 실제 sister 는 (1) provider 등록 + (2) workspace default-model + (3) KB body 의 embedding_model 명시 셋 다 함 (belt-and-suspenders). (e) **`search_method: hybrid_search` 채택** — sister 와 동일하게 BM25 + dense 결합 검색으로 한국어 정밀도 ↑. (f) **Retrieval 노드 schema 정정** — `top_k`/`score_threshold` 가 `multiple_retrieval_config` 하위 (평면 아님) + `query_variable_selector: list[str]` path. §2.2.3 갱신. (g) **§7.2 후속 트랙 추가** — embedding 모델 변경 procedure (zero-downtime 후보) + hybrid 검색 가중치 튜닝. |
