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
| 챗봇이 "테스트 계획서" 요청에 IEEE 829 7 섹션 markdown 응답 | 수동 호출 + 결과 검증 |
| 챗봇이 "시나리오" 요청에 자연어 + 14-DSL JSON 양쪽 응답 | 수동 호출 + JSON 을 `_validate_scenario` 통과 확인 |
| 14-DSL JSON 출력을 ZeroTouch-QA execute 모드로 실행 시 PASS | 수동 end-to-end 검증 |
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
  ├── query = user_query + (target_module 있으면 concat)
  ├── top_k = 5, score_threshold = 0.5
  ▼
[Knowledge Retrieval #2: kb_test_theory]
  ├── query = user_query
  ├── top_k = 3, score_threshold = 0.6
  ▼
[Variable Aggregator]
  └── 두 retrieval 결과를 단일 context 로 결합 (project_chunks + theory_chunks)
  ▼
[LLM: gemma4:26b]
  ├── system prompt = 의도 분류 + 출력 형식 분기 + 1-shot 예시 (계획서/시나리오 둘 다)
  ├── user prompt = 사용자 query + retrieved context
  ▼
[Answer]
  └── LLM 응답 그대로
```

#### 2.2.4 LLM (Planner role)

- **모델**: `gemma4:26b` (Ollama, 호스트, 기존과 동일).
- **completion_params**: `temperature=0.1`, `max_tokens=8192` (기존 ZeroTouch QA Brain 과
  동일 — Sprint 6 에서 검증된 값).
- **context_size**: Ollama provider credential 의 12288 (Sprint 6 에서 결정).
- **`<think>` 출력 금지**: ZeroTouch QA Brain 과 동일하게 system prompt 에 명시
  (max_tokens 낭비 방지).

#### 2.2.5 Output schema

다음 세 출력을 system prompt 1-shot 예시 + 명시적 schema 표로 정의:

**테스트 계획서 (Markdown)** — IEEE 829-lite 7 섹션:

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
```

**자연어 시나리오** (Given/When/Then):

```markdown
### 시나리오: <제목>

**Given** <전제 / 사전 조건>
**When** <사용자/시스템 동작>
**Then** <기대 결과>
```

**14-DSL JSON** (ZeroTouch-QA 호환) — `_validate_scenario` 와 동일 schema:

```json
[
  {"step": 1, "action": "navigate", "target": "", "value": "<URL>",
   "description": "<자연어 시나리오의 어느 부분에 해당>", "fallback_targets": []},
  {"step": 2, "action": "click", "target": "<role+name 또는 #id>", "value": "",
   "description": "...", "fallback_targets": ["..."]},
  ...
]
```

action 화이트리스트 14개 (`navigate`, `click`, `fill`, `press`, `select`, `check`,
`hover`, `wait`, `verify`, `upload`, `drag`, `scroll`, `mock_status`, `mock_data`).
field 계약은 ZeroTouch QA Brain Planner 와 1:1 일치 — 사용자가 출력을 그대로 복사해
ZeroTouch-QA execute 모드 입력으로 사용 가능.

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

### 3.5 출력 — 14대 DSL JSON

§2.2.5 의 schema. ZeroTouch QA Brain Planner 와 동일 계약:

- 첫 step 은 `navigate` (자동 prepend 책임은 ZeroTouch-QA 의 executor 에 있으므로
  Test Planning Brain 도 가능하면 emit).
- atomic 1:1 매핑 (Sprint 6 §11.3.4): 한 자연어 시나리오 step = 한 JSON step.
- target 은 시맨틱 셀렉터 우선 (`role+name`, `text=`, `label=`, `placeholder=`).
- description 은 자연어 시나리오의 해당 줄을 그대로 복사 (추적성).

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

### 4.3 청킹 파라미터

| 항목 | 값 | 근거 |
| --- | --- | --- |
| chunk_size | 500 | 한국어 ~250-350 토큰. retrieval 정밀도와 청크 수 균형 |
| overlap | 50 | 10% — 청크 경계의 의미 단절 방지 |
| indexing_mode | high-quality | embedding 정확도 우선. economy 는 향후 비용 압박 시 |
| separator | `\n\n` (단락) | 단락 단위 보존 |

이 값은 **초기 default**. T-12 검증 후 retrieval 품질이 낮으면 chunk_size 300 또는 700
로 조정 검토.

### 4.4 모델 SLA

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

### 5.1 Phase 0 — 인프라 (provision.sh patch)

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-01 | Embedding provider 등록 — `bona/bge-m3-korean:latest` 를 Dify Ollama provider 의 `text-embedding` 모델로 추가 | `provision.sh` | §2-3f 신규 단계 (LLM 등록 직후) | provision 후 DB `provider_model_credentials` 에 model_type=text-embedding 레코드 |
| T-02 | 빈 KB 자동 생성 — `kb_project_info`, `kb_test_theory` 두 개. POST `/console/api/datasets` | `provision.sh` | §2-5 신규 단계 (Chatflow import 이전) | provision 후 `/console/api/datasets` 목록에 두 KB 노출 |
| T-02a | KB 생성 시 indexing_technique=high-quality, embedding_model=bona/bge-m3-korean 명시 | `provision.sh` | T-02 안의 POST body | DB `datasets` 테이블 검증 |
| T-02b | KB 가 이미 존재하면 skip (idempotent) | `provision.sh` | T-02 안의 분기 | 두 번째 provision 시 중복 생성 안 됨 |

### 5.2 Phase 1 — 운영 절차 문서 (코드 없음)

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-03 | KB GUI 업로드 절차 문서화 | `README.md` (신규 §3.10) | 단계별 스크린샷 캡션 + 권장 폴더 구조 | 검토 |
| T-04 | 변경 파일 재인덱싱 절차 문서화 | `README.md` (§3.10 안 sub-section) | "다시 인덱싱" GUI 메뉴 사용법 | 검토 |

### 5.3 Phase 2 — Chatflow 설계

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-05 | Chatflow YAML 신규 작성 | `test-planning-chatflow.yaml` | Start → Retrieval×2 → Aggregator → LLM → Answer 노드 | YAML 문법 + 노드 그래프 시각 검토 |
| T-05a | Start 노드 inputs: user_query (text), output_mode (select), target_module (text optional) | T-05 안 | Start node JSON | Dify import 후 manual 호출 가능 |
| T-05b | Retrieval #1 (kb_project_info), top_k=5, score_threshold=0.5 | T-05 안 | Retrieval node JSON | 검색 결과가 5개 이하 정상 |
| T-05c | Retrieval #2 (kb_test_theory), top_k=3, score_threshold=0.6 | T-05 안 | Retrieval node JSON | 동일 |
| T-05d | Variable Aggregator — 두 retrieval 결과를 단일 context 로 결합 | T-05 안 | Aggregator node JSON | 변수 결합 성공 |
| T-05e | LLM 노드 (gemma4:26b, max_tokens=8192, temp=0.1) | T-05 안 | LLM node JSON | provision.sh 의 model 등록과 일치 |
| T-05f | Answer 노드 — LLM 출력 그대로 emit | T-05 안 | Answer node JSON | 검토 |
| T-06 | provision.sh 에 신규 chatflow auto-import + publish + API key 발급 (`dify-test-planning-api-token`) | `provision.sh` | §2-6 신규 단계 (기존 §2-4 와 비슷한 패턴) | provision 후 API key 가 Jenkins credentials 에 등록 |
| T-06a | API key 를 Jenkins credentials 에 등록 (Sprint 4A 의 dify-qa-api-token 등록과 동일 패턴) | `provision.sh` | T-06 안의 POST `/credentials/...` | Jenkins console 에 credential 노출 |

### 5.4 Phase 3 — System prompt 작성

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-07 | System prompt 의도 분류 룰 — 4 패턴 (§3.2) | `test-planning-chatflow.yaml` 안 LLM node text | 분류 표 + 키워드 룰 | 4 패턴 각각 manual 테스트 |
| T-08 | 테스트 계획서 1-shot 예시 (IEEE 829-lite 7 섹션) | T-07 안 | 1-shot 템플릿 | 출력이 7 섹션 모두 포함 |
| T-09 | 자연어 시나리오 1-shot 예시 (Given/When/Then) | T-07 안 | 1-shot 템플릿 | atomic + 검증 가능 |
| T-10 | 14-DSL JSON schema 설명 + 1-shot 예시 (ZeroTouch-QA 와 동일) | T-07 안 | schema 표 + 1-shot 템플릿 | 출력 JSON 이 `_validate_scenario` 통과 |
| T-10a | `<think>` 출력 금지 룰 (Sprint 6 §11.3.3 학습) | T-07 안 | 1줄 룰 | 출력에 think 블록 없음 |

### 5.5 Phase 4 — 검증

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-11 | 샘플 문서 세트 작성 — `examples/test-planning-samples/` | `examples/test-planning-samples/` | spec.pdf (가짜 모듈 spec 1쪽), api.csv (5 endpoint), test_theory.pdf (boundary value 1쪽) | 4 형식 모두 인덱싱 PASS |
| T-12 | end-to-end 검증 1: 계획서 요청 | (수동 호출) | 결과 markdown 캡처 | 7 섹션 모두 포함 + spec 인용 + 이론 적용 |
| T-13 | end-to-end 검증 2: 시나리오 요청 (둘 다 출력) | (수동 호출) | 결과 자연어 + JSON 캡처 | 자연어 atomic + JSON `_validate_scenario` 통과 |
| T-14 | end-to-end 검증 3: 14-DSL JSON 을 ZeroTouch-QA execute 모드로 실행 | Jenkins 수동 빌드 | run_log 17/17 PASS (또는 sample scope) | 결정론 PASS |
| T-15 | retrieval recall 정성 평가 | 수동 | 첫 5 chunks 가 query 와 의미적으로 정합한가 | 검토 |

### 5.6 Phase 5 — 문서

| ID | 작업 | 파일 | 산출물 | 검증 |
| --- | --- | --- | --- | --- |
| T-16 | 본 PLAN 의 변경 추적 (별도 트랙 closure 단락) | `PLAN_TEST_PLANNING_RAG.md` (본 파일) | §6 closure 추가 | 검토 |
| T-17 | README §3.10 신설 — Test Planning RAG 운영 가이드 | `README.md` | KB 업로드 / 챗봇 사용 / 결과 → ZeroTouch-QA 연계 절차 | 검토 |
| T-18 | architecture.md 헤더에 별도 트랙 줄 추가 (선택) | `architecture.md` | "Test Planning RAG" 별도 트랙 명시 | 검토 |

### 5.7 의존성 그래프

```text
T-01 (embedding provider)
  └─→ T-02 (KB 자동 생성)
        └─→ T-05 (chatflow YAML)
              ├─→ T-05a~f (노드 구성)
              └─→ T-07~T-10a (system prompt)
                    └─→ T-06 (auto-import)
                          └─→ rebuild required
                                └─→ T-11 (샘플 문서)
                                      └─→ T-12, T-13, T-14, T-15 (검증, 병렬 가능)
                                            └─→ T-16, T-17, T-18 (문서)
T-03, T-04 는 코드 없음 → 어느 시점에든 작성 가능 (실 환경 검증 후 캡처 추가하면 정확)
```

총 **18 태스크 + 12 sub-task = 30 작업 단위**. 코드 변경 파일: `provision.sh`,
`test-planning-chatflow.yaml` (신규), `README.md`, `architecture.md`,
`examples/test-planning-samples/` (신규).

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

---

## 8. 의존성과 위험

### 8.1 모델 / 인프라

| 의존성 | 현재 상태 | 위험 |
| --- | --- | --- |
| `gemma4:26b` (Ollama) | 호스트에 설치됨 | 모델 변경 시 Sprint 6 보강 효과 무효화 가능 |
| `bona/bge-m3-korean:latest` (Ollama) | 호스트에 설치됨 (1.2GB) | 모델 누락 시 Phase 0 부터 실패 |
| Dify 1.13.3 | 운영 중 | KB API schema 가 다음 메이저 (1.14+) 에서 바뀔 수 있음 |
| Qdrant (Dify 내장) | 운영 중 | 디스크 부족 시 인덱싱 실패 |

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
| Jenkins 통합 | 이번 트랙은 제외 | 2026-04-27 | 사람 검토 후 수동 실행이 더 안전 |
| Embedding 모델 | `bona/bge-m3-korean:latest` | 2026-04-27 | 한국어 우선, 호스트에 이미 설치 |
| 트랙 명명 | "Test Planning RAG" 별도 트랙 | 2026-04-27 | 본 PLAN 과 분리해 운영 책임 명확화 |
| KB 분리 여부 | 두 개 (`kb_project_info`, `kb_test_theory`) | 2026-04-27 | 의미 그룹 다름 + 검색 가중치 분리 |
| 출력 형식 | 셋 다 (계획서 / 자연어 시나리오 / 14-DSL JSON) | 2026-04-27 | 사용자 의도 따라 분기 |
| 산출물 템플릿 | IEEE 829-lite | 2026-04-27 | 사내 템플릿 도입 전 일반 표준 |
| LLM 모델 | `gemma4:26b` (기존 공유) | 2026-04-27 | provider 통합 운영 |
| Dify 인스턴스 | 같은 인스턴스 (port 18081) 에 두 번째 앱 | 2026-04-27 | 인프라 단순 |

---

## 10. 변경 이력

| 일자 | 항목 | 비고 |
| --- | --- | --- |
| 2026-04-27 | 초안 작성 | 본 PLAN 신설. T-01 (embedding provider 등록) 만 사전 patch 됨, 나머지는 미착수. |
