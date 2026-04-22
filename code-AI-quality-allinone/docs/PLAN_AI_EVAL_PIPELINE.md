# AI 평가 파이프라인: README §5.1 기준 체계적 개선 계획 (v3)

## Context

사용자가 확정한 전제들:

1. **정본**: `readme.md` §5.1 (11지표/5단계). `외부 AI 에이전트 평가 시스템 구축 프로젝트 계획서.md` 폐기.
2. **Langfuse**: 현 단계 미사용. 단 코드 내 조건부 import 는 유지(향후 재활성화 대비).
3. **수정 범위**: 사전 영향도 검증 결과 `eval_runner/` 과 `jenkinsfiles/04 AI평가.jenkinsPipeline` 수정은 Jobs 01/02/03 에 영향 0 — 수정 허용.
4. **리포트**: 평가 결과의 **가독성·이해도를 핵심 산출물**로 취급. Jenkins `publishHTML` 로 직접 열람 가능해야 함.

본 계획서는 위 전제 위에서 작업을 **Phase 0 → 5 의 6단계 로드맵**으로 조직화한다. 각 Phase 는 진입 조건·산출물·검증 게이트·종료 조건을 명시하며, 후속 Phase 는 이전 Phase 의 종료 조건 충족 없이 시작하지 않는다.

---

## 1. 정본 스펙 및 제약 요약

### 1.1 README §5.1 정본 (11지표 × 5단계)

- **1단계 Fail-Fast**: ① Policy Violation (Promptfoo), ② Format Compliance (jsonschema, API 전용)
- **2단계 과제검사**: ③ Task Completion (DeepEval GEval)
- **3단계 심층평가**: ④ Answer Relevancy, ⑤ Toxicity, ⑥ Faithfulness*, ⑦ Contextual Recall*, ⑧ Contextual Precision* (* = API 전용, `retrieval_context` 있을 때만)
- **4단계 다중턴**: ⑨ Multi-turn Consistency (GEval)
- **5단계 운영**: ⑩ Latency (정보성), ⑪ Token Usage (API 전용)
- **대상 분기**: `TARGET_TYPE ∈ {http, ui_chat}` + `retrieval_context` 유무
- **Judge**: 기본 `qwen3-coder:30b`, temperature=0, env 오버라이드 허용
- **Wrapper 모드 (현 phase)**: **`local_ollama_wrapper` 단일 모드로 한정**. README 정본은 4 모드를 정의하나, 사용자 결정으로 현 phase 에서는 로컬 LLM(Ollama) 만 사용. `openai_wrapper`/`gemini_wrapper`/`direct` 는 향후 phase 로 이연.

### 1.2 Langfuse 스코프

현 Phase 전체에서 **미사용**. 영향:
- 관찰 대시보드(추세·drill-down) 불가, Jenkins artifacts 기반 HTML 리포트로 대체.
- 11지표 중 Latency/Token Usage 는 Langfuse 에 "소유"된 게 아니라 adapter 레벨에서 수집됨 → summary.json 에 일관 기록만 보장하면 지표 완전성 확보.
- 코드의 `try: from langfuse` / 조건부 instantiation 은 **제거 금지**, 향후 크리덴셜만 주입하면 재활성화되도록 유지.

### 1.3 계획서 이원 관리 원칙

본 계획서는 **두 위치에 이원 관리**된다.

| 위치 | 역할 | 갱신 주기 |
|---|---|---|
| `~/.claude/plans/effervescent-wondering-petal.md` | 작업용 working copy. 대화 중 즉시 갱신, 사고 흐름·중간 결정 누적 | 매 plan 모드 작업마다 |
| `code-AI-quality-allinone/docs/PLAN_AI_EVAL_PIPELINE.md` | 프로젝트 공식 사본. 팀 공유·PR 리뷰·이력 추적 | 주요 결정·Phase 종료·마일스톤 시점에 working copy 의 안정 상태를 복사 후 커밋 |

운영 규칙:
- working copy 가 항상 더 최신. 레포 사본은 안정 스냅샷.
- Phase 종료 시 또는 사용자가 명시적으로 요청할 때 동기화.
- 동기화 커밋 메시지 컨벤션: `docs(ai-eval): plan v<seq> sync (<phase X done|decision Y|...>)`.

### 1.4 수정 가능 범위 매트릭스

| 경로 | 수정 | 블래스트 반경 | 반영 시점 |
|---|---|---|---|
| `eval_runner/**` | ✅ | Job 04 only | 이미지 재빌드 |
| `jenkinsfiles/04 AI평가.jenkinsPipeline` | ✅ | Job 04 only | 이미지 재빌드 + Jenkins job 재로드 |
| `외부 AI 에이전트 평가 시스템 구축 프로젝트 계획서.md` 삭제 | ✅ | 문서만 | 즉시 |
| `pipeline-scripts/**`, `jenkinsfiles/01~03*` | ❌ | Jobs 01/03 | — |
| `Dockerfile` | ⚠️ | 전체 이미지 | 합의 후 (기본 불필요) |
| `docker-compose.*.yaml`, `scripts/provision.sh`, `scripts/entrypoint.sh` | ❌ | 전체 런타임 | — |

증거: [Dockerfile:254](code-AI-quality-allinone/Dockerfile#L254) `COPY eval_runner /opt/eval_runner` (디렉터리 단위 → 신규 파일 자동 포함, Dockerfile 수정 불필요), [entrypoint.sh:98](code-AI-quality-allinone/scripts/entrypoint.sh#L98) symlink, grep 결과 Jobs 01/02/03 이 `eval_runner/` 참조 0건.

---

## 2. Gap 인벤토리 (재분류)

카테고리: **G** 스펙 위반 · **R** 리포트 가독성 · **Q** 운영 품질.

### 🔴 G — 스펙 위반
| ID | 항목 | 증거 | 영향 | 현 phase 처리 |
|---|---|---|---|---|
| G1 | `openai_wrapper_api.py` 미구현 | [04:275](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline#L275) | `TARGET_MODE=openai_wrapper` 하드 크래시 | **이연** — 모드 자체를 Jenkinsfile choice 에서 제거하므로 위험 제거 |
| G2 | `gemini_wrapper_api.py` 미구현 | [04:331](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline#L331) | `TARGET_MODE=gemini_wrapper` 하드 크래시 | **이연** — 동일 |
| G3 | Langfuse-off 시 Latency/Token Usage 지표 미기록 | [test_runner.py:1022,:1567](code-AI-quality-allinone/eval_runner/tests/test_runner.py#L1022) | 11지표 중 ⑩⑪ 커버리지 실패 | Phase 1 의 **단독 핵심 스펙 위반** |
| G4 | Jenkinsfile 의 사용 불가 모드 노출 | [04:60-69, :246-353](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline#L60) | 운영자가 미구현 모드를 선택해 빌드를 깨뜨릴 수 있음 | Phase 1 에서 모드 단순화 (`local_ollama_wrapper` 단일) |

### 🟣 R — 리포트 가독성·이해도
| ID | 항목 | 현 상태 | 목표 |
|---|---|---|---|
| R1 | 임원 요약 헤더 없음 | PASS/FAIL 수치가 테이블 내부에 파묻힘 | 상단에 **단일 스크린 대시보드**: 전체 pass rate, 11지표별 막대, P50/P95/P99 latency, total tokens, judge/dataset 메타 |
| **R1.1** | **임원 문단 부재** | 숫자만 나열 | **로컬 LLM(Ollama Judge 재사용) 이 summary.json 을 입력받아 2~3 문장 "상태·주원인·권장 조치" 생성**. 필수(기본 on). 캐시 필수. 프롬프트 가드레일: "JSON 사실만, 추측 금지" |
| R2 | 11지표별 집계 뷰 없음 | 지표는 case 행에만 표시 | 지표 카드 11개: pass rate·threshold·분포 히스토그램·경계 case 수 |
| **R2.1** | **지표별 내러티브 부재** | 숫자만 표시 | **지표당 1줄 LLM 해석** ("Answer Relevancy pass 9/10, off-topic case 1건이 편차 원인"). 옵션(`SUMMARY_LLM_INDICATOR_NARRATIVE`, 기본 off — 비용 때문) |
| R3 | Case drill-down 부실 | HTML ~400 LOC 에 분산 | conversation accordion → turn row → input/output/Judge reason 토글 |
| **R3.1** | **실패 사유 해설 하드코딩** | `_easy_explanation` if-else 키워드 매칭 | **LLM 이 실패 metric + failure_message 로 1문장 "쉬운 해설" 생성**. 하드코딩 fallback 유지 (LLM 실패/비활성 시). 기본 on. |
| **R3.2** | **조치 권장 없음** | 실패 원인만 표시 | **실패 case 마다 "권장 조치" 1~2줄 LLM 생성** ("system prompt 에 응답 길이 제한 추가 권장"). 옵션(`SUMMARY_LLM_REMEDIATION_HINTS`, 기본 off) |
| R4 | 시스템 에러 vs 품질 실패 미구분 | 문자열 파싱에 의존 | 색상·섹션 분리, 5xx/ConnError 는 "가용성 이슈" 로 별도 집계 |
| R5 | Build-over-build delta 없음 | 단일 빌드만 표시 | 이전 빌드 `summary.json` 을 Jenkins artifacts 에서 로드해 지표 delta 표 (스트레치) |
| R6 | Jenkins 탭 접근성 | `publishHTML` 사용 중이나 `allowMissing: true` | `AI Eval Summary` 탭 기본 노출, 비어있으면 명시적 placeholder |

### 🟡 Q — 운영 품질
| ID | 항목 | 설명 |
|---|---|---|
| Q1 | `UniversalEvalOutput.error_type` enum 없음 | system vs quality 를 문자열 파싱으로 구분 |
| Q2 | Judge 잠금 메타 불완전 | summary 에 `judge_model` 만 기록, digest/temperature/base_url 누락 |
| Q3 | Dataset 버전·해시 미기록 | path 만 기록, drift 추적 불가 |
| Q4 | `test_runner.py` 1667 LOC god-module | 데이터 로더·정책·DSL·HTML 렌더·번역 혼재 |
| Q5 | Promptfoo subprocess 결합도 | 단순 regex 까지 subprocess 로 실행 |
| Q6 | Jenkinsfile 래퍼 기동 3× 중복 | 192–353 `nohup → /health` 3회 복사 |
| Q7 | Judge 변동성 완화 로직 없음 | temperature=0 이어도 실행 간 편차 가능 |

---

## 3. Phase 로드맵

각 Phase 는 **진입 조건 → 산출물 → 검증 게이트 → 종료 조건** 의 4요소로 정의. 후속 Phase 는 이전 종료 조건 없이 시작 금지.

### Phase 0 — Foundation (안전망 + 정본화)

**목적**: 후속 모든 변경의 회귀 검출 기반을 먼저 만든다. 스펙 문서를 정리한다.

**Steps**:
- **0.0** 본 계획서의 레포 등록 (가장 먼저)
  - `~/.claude/plans/effervescent-wondering-petal.md` 를 `code-AI-quality-allinone/docs/PLAN_AI_EVAL_PIPELINE.md` 로 복사
  - `git add code-AI-quality-allinone/docs/PLAN_AI_EVAL_PIPELINE.md` → 커밋 `docs(ai-eval): Phase 0~5 개선 계획서 추가` → `git push origin feat/ai-eval-pipeline`
  - 이후 plan 모드 작업은 `~/.claude/plans/` 의 사본에서 계속, 주요 결정 시점마다 레포 사본을 동기화 (수동)
- **0.1** 정본 문서 정리
  - `외부 AI 에이전트 평가 시스템 구축 프로젝트 계획서.md` 삭제
  - `CONVERSATION_LOG.md` 또는 `eval_runner/README.md` 에 "README §5.1 이 정본" 명시
- **0.2** 골든 하네스 구축 (모든 후속 변경의 안전망) — **property-based 접근**
  - 전체 파이프라인 byte-match (DeepEval/Ollama mock 필요) 는 Phase 0 범위 초과로 판단 → 대안으로 **결정론적 유틸리티 함수들의 property test** 로 회귀 검출
  - `eval_runner/tests/fixtures/tiny_dataset.csv` — **11 cases / 10 conversations**, 11지표 × 5단계 각각을 최소 1회 trigger
  - `eval_runner/tests/test_golden.py` — 검증 대상: `load_dataset()`, `_parse_success_criteria_mode()`, `_schema_validate()`, `_evaluate_simple_contains_criteria()`, `_is_blank_value()`, `_turn_sort_key()`
  - `eval_runner/tests/fixtures/expected_golden.json` — 각 함수 기대값 (dataset 그룹 구조, DSL/GEval 분기 매핑, 한/영 '포함' 템플릿 매칭 케이스, schema 통과/거부 샘플)
  - **deepeval 미설치 환경에서도 동작**: test_golden.py 가 sys.modules 에 stub 주입, DeepEval/Ollama 의존성 없이 util 함수만 import
  - **데이터셋 카테고리 매트릭스**:

| # | case_id | conv_id | turn | 대상 지표(단계) | retrieval_context | 기대 동작 |
|---|---|---|---|---|---|---|
| 1 | `policy-fail-pii` | — | 1 | ① Policy Violation (1단계) | — | AI 응답에 SSN 포함 → Fail-Fast FAIL, 이후 단계 중단 |
| 2 | `policy-pass-clean` | — | 1 | ① Policy Violation PASS | — | 정상 응답, 3단계로 진행 |
| 3 | `format-fail-missing` | — | 1 | ② Format Compliance (1단계) | — | schema 위반 (answer 누락) → Fail-Fast FAIL |
| 4 | `task-pass-simple` | — | 1 | ③ Task Completion (2단계) | — | success_criteria = "contains: 서울" 충족 → PASS |
| 5 | `task-fail-criteria` | — | 1 | ③ Task Completion FAIL | — | criteria 미충족 → FAIL |
| 6 | `deep-relevancy-offtopic` | — | 1 | ④ Answer Relevancy + ⑤ Toxicity (3단계, non-RAG) | — | 딴소리 응답 → Relevancy 낮음 (stub 결정론) |
| 7 | `rag-faithful` | rag-1 | 1 | ⑥ Faithfulness + ⑦ Recall + ⑧ Precision (3단계, RAG) | 제공 | context 근거 응답 → 전체 지표 PASS |
| 8 | `rag-hallucinate` | rag-2 | 1 | ⑥ Faithfulness FAIL | 제공 | context 없는 내용 생성 → Faithfulness 낮음 |
| 9 | `multi-consistent-t1` | mt-1 | 1 | 기억 수립 | — | "제 이름은 김철수" 입력·수신 |
| 10 | `multi-consistent-t2` | mt-1 | 2 | ⑨ Multi-turn Consistency (4단계) PASS | — | "이름이 뭔가요?" → "김철수" 응답, 일관성 PASS |
| 11 | `ops-latency-probe` | — | 1 | ⑩ Latency + ⑪ Token Usage (5단계) | — | 정상 응답 + latency/usage 기록 확인 |

  - **5단계 커버리지 확인**: 1~3(Fail-Fast) / 4~5(Task) / 6~8(심층, non-RAG·RAG) / 9~10(다중턴) / 11(운영) — 5단계 전부 hit.
  - **11지표 커버리지 확인**: ① 1,2 / ② 3 / ③ 4,5 / ④ 2,6 / ⑤ 2,6 / ⑥⑦⑧ 7,8 / ⑨ 10 / ⑩⑪ 모든 PASS case — 11지표 전부 hit.
  - **Target type**: 모두 `http` (ui_chat 은 browser stub 복잡성으로 골든 하네스 범위 외, Phase 1+ 에서 실 Jenkins smoke 로 검증).
  - **Stub 전략**:
    - `OllamaModel.generate()` → 지표명 기반 고정 응답 (예: Relevancy=0.9, Toxicity=0.05, Faithfulness=0.95, Recall=0.85, Precision=0.8, TaskCompletion GEval=1.0). 단 `*-fail-*` / `*-hallucinate*` / `*-offtopic*` case 는 낮은 점수로 분기.
    - `requests.post` (http_adapter) → case_id 기반 고정 JSON 응답. 실 네트워크 호출 0.
    - `subprocess.run` (Promptfoo) → `policy-fail-pii` 만 returncode=1, 나머지 returncode=0.
  - **정규화 규칙**: 타임스탬프(ISO8601) → `<TIMESTAMP>`, build_number/ID → `<BUILD>`, UUID → `<UUID>`, latency_ms 실수치 → `<LATENCY>` 치환 후 diff.
  - **실행 명령**: `LANGFUSE_PUBLIC_KEY="" OLLAMA_BASE_URL=http://127.0.0.1:0 pytest eval_runner/tests/test_golden.py -v`
- **0.3** 리포트 acceptance spec 문서화
  - `eval_runner/docs/REPORT_SPEC.md` 신규 — R1~R6 의 레이아웃 목업·필수 필드 명시
  - Phase 2 의 설계 기준이 됨

**검증 게이트**:
- `pytest eval_runner/tests/test_golden.py` 가 `main` 에서 **통과**
- Jobs 01/02/03 을 이미지 재빌드 후 각 1회 실행 → 동작 변화 없음
- 리뷰어가 `REPORT_SPEC.md` 를 승인

**종료 조건**: 골든 하네스가 CI 수준은 아니어도 수동 재현 가능, 정본 문서 확정.

---

### Phase 1 — Spec Compliance (로컬 LLM 단독, G3 + G4)

**목적**: 11지표 커버리지를 Langfuse off 상태에서도 완전하게 만들고, Jenkinsfile 의 모드 선택을 **현재 실동작 가능한 `local_ollama_wrapper` 단일** 로 정리한다. G1/G2(외부 API 래퍼) 는 본 phase 에서 다루지 않음.

**진입 조건**: Phase 0 완료.

**Steps**:
- **1.1** G3 — Latency/Token Usage 를 summary.json 에 필수 기록
  - `test_runner.py` 가 per-turn 결과를 summary 에 합산할 때 `latency_ms`, `usage` 포함
  - summary 스키마: `conversations[*].turns[*].{latency_ms, usage}` + `aggregate: {latency_p50, latency_p95, latency_p99, total_tokens}`
  - Langfuse 경로 제거 금지 (유지)
- **1.2** G4 — Jenkinsfile 모드 단순화 (Q6 동시 해소)
  - [04:60-69](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline#L60) `TARGET_MODE` Choice 옵션을 `local_ollama_wrapper` 단일로 축소
  - [04:163-455](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline#L163) Stage 1-2 의 case 분기 중 `openai_wrapper` / `gemini_wrapper` / `direct` 블록 제거 → 결과적으로 Q6 (3× 중복) 도 자동 해소
  - 외부 API 관련 파라미터 (`OPENAI_*`, `GEMINI_*`, `TARGET_AUTH_HEADER`) 도 정리. 단 `TARGET_URL` 은 호환성 위해 hidden default 로 유지 가능
  - 미래 재확장 대비: 제거 직전 코드를 `archive/` 디렉터리로 이동하지는 않음 (git history 가 보존)
- **1.3** (이연) G1/G2 OpenAI/Gemini 래퍼 실장 — 별도 phase 또는 별도 브랜치로

**검증 게이트**:
- 골든 하네스 통과
- `LANGFUSE_PUBLIC_KEY=""` 상태로 실행 → summary.json 에 11지표 전부 (latency/usage 모든 턴) 기록
- Jenkins 에서 `TARGET_MODE=local_ollama_wrapper` 1회 성공 실행
- Jenkins UI 에서 모드 드롭다운에 **단일 옵션만** 노출 확인

**종료 조건**: 11지표 커버리지가 Langfuse 유무와 무관하게 완전. `04 AI평가` Job 이 미구현 모드 선택으로 깨질 가능성 0.

---

### Phase 2 — Report Overhaul (R 핵심)

**목적**: 평가 결과의 **가독성·이해도** 를 파이프라인의 핵심 산출물로 승격. Jenkins `publishHTML` 탭에서 바로 이해되는 리포트.

**진입 조건**: Phase 1 완료 (summary.json 에 11지표 데이터 완전).

**Steps** (리포트 구조·LLM 내러티브 인프라 먼저, 이후 R1~R6 순차 구현):

- **2.0** 리포트 모듈 분리 **+ LLM 내러티브 인프라** (진입용 리팩터 + R1.1~R3.2 공통 기반, Q4 부분 착수)
  - **(a) 모듈 분리 (완료된 선행 작업 일부 포함)**: `eval_runner/reporting/{__init__,html,translate}.py` 패키지 신설. 기존 `test_runner.py` 의 HTML 렌더·번역 로직(~700 LOC) 이전. `render_summary_html(state)` 가 SUMMARY_STATE 를 인자로 받도록 시그니처 변경. 골든 하네스에 `test_render_summary_html_smoke` 추가.
  - **(b) LLM 생성기 일반화**: `reporting.translate._translate_with_ollama` 를 `reporting/llm.py` 의 `generate_with_ollama(prompt, *, cache_key, temperature=0, num_predict=...)` 로 승격. 번역·요약·해석·조치 권장 전부 이 함수 하나로 라우팅.
  - **(c) 결정론적 캐시**: LLM 호출 결과를 `(role, cache_key)` 로 캐시. `role` ∈ `{translate, exec_summary, indicator_narrative, easy_explanation, remediation}`. `cache_key` 는 입력 fields 의 canonical JSON (sha256). temperature=0 이어도 캐시가 결정성 확보.
  - **(d) Opt-in/out 환경변수 선언 (기본값 정책 포함)**:
    - `SUMMARY_LLM_EXEC_SUMMARY`: 기본 **on** (R1.1 핵심 기능, 빌드당 1 call)
    - `SUMMARY_LLM_EASY_EXPLANATION`: 기본 **on** (R3.1, 하드코딩 fallback 보존)
    - `SUMMARY_LLM_INDICATOR_NARRATIVE`: 기본 **off** (R2.1, 최대 11 calls/빌드 — 비용 고려)
    - `SUMMARY_LLM_REMEDIATION_HINTS`: 기본 **off** (R3.2, 실패 case 당 1 call)
    - `SUMMARY_LLM_TIMEOUT_SEC`: 기본 20 (개별 호출 실패 시 fallback)
  - **(e) 프롬프트 가드레일**: 각 role 별 system prompt 고정화, 공통 문구 — "주어진 JSON 필드의 사실만 사용. 추측·새 해석·외부 지식 금지. score/case_id/숫자/URL 원문 유지. 출력 길이 N 문장 제한."
  - **(f) Fallback 체계**: LLM timeout/error/네트워크 실패 시 기존 하드코딩 텍스트(`_easy_explanation` if-else, `_fallback_localize_common_phrases`) 로 graceful degrade. 리포트에 "🤖 LLM 생성" 또는 "📋 기본 메시지" 배지로 구분.
  - **(g) REPORT_SPEC 업데이트**: `eval_runner/docs/REPORT_SPEC.md` 에 §3.1.1 (R1.1), §3.2.1 (R2.1), §3.3.1 (R3.1), §3.3.2 (R3.2) 신규 섹션 추가. AC9~AC12 acceptance criteria 추가 (예: AC9 "실패 빌드 리포트에 LLM 요약이 30단어 이내, JSON 사실 포함 여부 체크").
  - **(h) 골든 하네스 확장**: `generate_with_ollama` 를 monkeypatch stub 으로 주입해 결정론 테스트 가능하도록. LLM off 상태에서도 리포트 정상 생성 확인.

- **2.1** R1 + **R1.1** — 임원 요약 헤더 + LLM 요약 문단
  - R1: 단일 스크린 대시보드 (전체 pass rate, 11지표 막대, latency P50/P95/P99, total tokens, judge/dataset 메타, build link)
  - **R1.1**: 헤더 최상단에 "🤖 이번 빌드 한 줄 요약" 섹션 추가 — LLM 이 summary.json 의 totals/indicators/실패 case 핵심을 읽어 2~3 문장 ("이번 빌드는 대화 10건 중 1건 실패. 주원인은 Policy Violation (PII 노출) 1건. 권장 조치: 프롬프트에 PII 금지 강화."). 빌드당 1 call. 결과를 summary.json 의 `aggregate.exec_summary: {text, generated_by, cached}` 에도 기록해 영구 보존.

- **2.2** R2 + **R2.1** — 11지표 대시보드 + 지표별 LLM 해석
  - R2: 지표 카드 11개 (pass rate %, threshold, 점수 분포 히스토그램, 경계 case 수)
  - **R2.1** (opt-in): `SUMMARY_LLM_INDICATOR_NARRATIVE=on` 일 때 각 카드 하단에 1줄 LLM 해석. 기본 off — UI 렌더에는 placeholder("지표 해석은 `SUMMARY_LLM_INDICATOR_NARRATIVE=on` 활성화 시 표시").

- **2.3** R3 + **R3.1** + **R3.2** — Case drill-down + LLM 해설/조치
  - R3: conversation accordion → turn row → input/output/Judge reason 토글
  - **R3.1** (기본 on, LLM 실패 시 fallback): 기존 `_easy_explanation` 하드코딩을 LLM 생성으로 교체. case 당 1 call (caching 포함). "이 빌드의 이 턴에서 왜 실패했는지" 1 문장.
  - **R3.2** (opt-in): `SUMMARY_LLM_REMEDIATION_HINTS=on` 일 때 각 실패 case 에 권장 조치 1~2 줄 ("system prompt 에 '반드시 XXX' 지시어 추가 권장").

- **2.4** R4 — 시스템 에러 vs 품질 실패 섹션 분리
  - Q1 (`error_type` enum) 선행 필요 → Phase 3 와 연계. Phase 2 에서는 임시로 문자열 패턴 매칭 + Phase 3 에서 구조화 재활용

- **2.5** R5 — Build-over-build delta (스트레치)
  - 이전 빌드 `summary.json` 을 `${BUILD_URL}/../lastSuccessfulBuild/artifact/...` 에서 fetch
  - Jenkins 에서 네트워크 접근 가능하면 활성, 아니면 placeholder
  - R1.1 의 exec_summary 가 이전 빌드와 현재를 비교하는 문장 포함 옵션 (LLM 호출 추가 불필요 — 동일 1 call 안에서)

- **2.6** R6 — Jenkins `publishHTML` 통합 검증
  - [04:483+](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline) post always 블록의 `publishHTML` 타겟 확인·개선
  - `AI Eval Summary` 탭이 기본 노출, 빈 상태 명시적 placeholder

**검증 게이트**:
- 골든 하네스 10/10 이상 유지 + R1.1/R3.1 LLM stub 결정론 테스트 통과
- Jenkins 에서 3가지 실제 실행 (성공 / 일부 실패 / 완전 실패) → 리포트가 각 상황을 명확히 표현하는지 수동 검토
- 리포트 열람 시간 측정: 리뷰어가 "이 빌드 결과 요약" 을 **30초 이내** 에 이해 가능한지 A/B
- **LLM 신뢰성 검증**: R1.1 의 생성된 요약이 JSON 에 없는 수치를 언급하는지 샘플 5건 수동 점검. 환각 있으면 프롬프트 재튜닝.
- **비용 검증**: 기본 설정(R1.1 + R3.1 on) 에서 빌드당 추가 LLM 호출 수 = `1 + 실패 case 수` 로 측정. 20 case 중 5 실패 → 6 calls × ~5s ≈ 30s 추가. 수용 가능.

**종료 조건**: 운영자가 Jenkins 탭만으로 빌드 결과를 판단·다음 조치 결정 가능. 숫자가 아니라 **자연어 해설**이 1차 판단 도구로 기능.

---

### Phase 3 — Metadata & Traceability (Q1~Q3)

**목적**: 재현성·감사 가능성 확보. 매 리포트가 "어느 Judge·어느 Dataset 기준 인지" 자기 기록.

**진입 조건**: Phase 2 완료 (리포트가 메타를 표시할 자리 확보).

**Steps**:
- **3.1** Q2 — Judge 잠금 메타
  - summary.json: `judge: {model, base_url, temperature, digest?}`, digest 는 Ollama `/api/show` best-effort
  - 리포트 헤더(R1) 에 상시 노출
- **3.2** Q3 — Dataset 메타
  - `load_dataset()` 이 SHA256 + mtime + row count 계산 → `dataset: {path, sha256, rows, mtime}`
- **3.3** Q1 — 에러 분류 구조화
  - `UniversalEvalOutput.error_type: Literal["system","quality"] | None` 추가
  - `http_adapter.py` 가 5xx/ConnError → `system`, 그 외 metric 실패 → `quality` 라우팅
  - R4 가 임시 문자열 매칭 → 구조화 필드 기반으로 교체

**검증 게이트**: 골든 하네스 통과, 리포트 헤더에 3종 메타(judge/dataset/error breakdown) 표시.

**종료 조건**: 임의 빌드의 summary.json 을 읽으면 Judge·Dataset·에러 분포를 외부 문맥 없이 이해 가능.

---

### Phase 4 — Structure Cleanup (Q4, Q5)

**목적**: 유지보수 비용을 줄인다. 이 Phase 는 기능 변화 0 (행위 보존 리팩터). Q6 는 Phase 1 의 모드 단순화로 자동 해소되었으므로 본 Phase 에서 제외.

**진입 조건**: Phase 3 완료 (골든 하네스가 성숙).

**Steps**:
- **4.1** Q5 — Promptfoo in-process 전환
  - `security_assert.py` 가 이미 Python 이므로 regex 체크는 in-process
  - `_promptfoo_policy_check` → `_run_policy_check(text) -> list[Violation]` 치환
  - LLM 기반 assertion 필요 시만 subprocess 유지
- **4.2** Q4 — `test_runner.py` 분할 (Phase 2 의 reporting/ 분리 이후 남은 부분)
  - `eval_runner/dataset.py` — 로딩/정규화
  - `eval_runner/policy.py` — Phase 1 정책 + schema 검증
  - `eval_runner/scoring.py` — GEval + DeepEval 메트릭 실행
  - `eval_runner/runner.py` — `run_conversation()` 오케스트레이션
  - `tests/test_runner.py` 는 ~30 LOC pytest shim

**검증 게이트**: 골든 하네스 byte-match (기능 변화 0), `pytest --collect-only` 가 pytest 디스커버리 유지.

**종료 조건**: `test_runner.py` ≤ 100 LOC, 각 모듈 단일 책임.

---

### Phase 5 — Evaluation Robustness (Q7)

**목적**: Judge LLM 변동성에 대한 방어선.

**진입 조건**: Phase 4 완료 (scoring.py 분리 덕분에 개입 지점 명확).

**Steps**:
- **5.1** Q7-a 보정 세트(calibration subset)
  - Golden dataset 에 `calib: true` 컬럼 허용, 매 실행 포함
  - summary 에 보정 case 점수 편차(std) 기록, 리포트 헤더에 노출
- **5.2** Q7-b 경계 case N-repeat (opt-in)
  - `--repeat-borderline N` CLI, 기본 비활성
  - 점수가 임계치 ±0.05 이내 case 만 N 회 재실행, median 채택
  - Judge 호출량 증가 영향 측정 → 리포트 `judge_calls_total` 기록

**검증 게이트**: 보정 세트가 빈 경우에도 리포트가 정상, N-repeat off 가 기본.

**종료 조건**: Judge 변동성이 관측 가능(편차 수치 노출) + opt-in 완화 로직 작동.

---

## 4. Scope

### In
- `eval_runner/` 전체 (수정 + 신규 파일 + `reporting/`, `docs/`, `tests/fixtures/` 하위 추가)
- `jenkinsfiles/04 AI평가.jenkinsPipeline`
- `configs/{schema.json, security.yaml, security_assert.py}`
- `외부 AI 에이전트 평가 시스템 구축 프로젝트 계획서.md` **제거**

### Out
- Jobs 01/02/03, `pipeline-scripts/`
- `Dockerfile` (디렉터리 COPY 라 신규 파일 자동 포함, 수정 불필요)
- `scripts/entrypoint.sh`, `scripts/provision.sh`, `docker-compose.*.yaml`
- `c:/developer/ttcScripts/e2e-pipeline` (별도 레포)
- 신규 지표 추가, Type A/B/C 분기 (README 미명시)
- Ragas 도입
- **OpenAI/Gemini 래퍼 (G1/G2)** 및 `direct` 모드 — 현 phase 에서 모드 제거. 향후 별도 phase 에서 재도입 (필요 시)

---

## 5. Critical Files

- [code-AI-quality-allinone/eval_runner/tests/test_runner.py](code-AI-quality-allinone/eval_runner/tests/test_runner.py) — 1667 LOC, Phase 2/4 에서 분할
- [code-AI-quality-allinone/eval_runner/adapters/base.py](code-AI-quality-allinone/eval_runner/adapters/base.py) — Phase 3 `error_type` 필드 추가
- [code-AI-quality-allinone/eval_runner/adapters/http_adapter.py](code-AI-quality-allinone/eval_runner/adapters/http_adapter.py) — Phase 3 에러 라우팅
- [code-AI-quality-allinone/eval_runner/ollama_wrapper_api.py](code-AI-quality-allinone/eval_runner/ollama_wrapper_api.py) — Phase 1 래퍼 템플릿
- [code-AI-quality-allinone/eval_runner/configs/schema.json](code-AI-quality-allinone/eval_runner/configs/schema.json)
- [code-AI-quality-allinone/eval_runner/configs/security_assert.py](code-AI-quality-allinone/eval_runner/configs/security_assert.py) — Phase 4 in-process 전환
- [code-AI-quality-allinone/jenkinsfiles/04 AI평가.jenkinsPipeline](code-AI-quality-allinone/jenkinsfiles/04%20AI%ED%8F%89%EA%B0%80.jenkinsPipeline) — Phase 1 래퍼 연결, Phase 2 publishHTML, Phase 4 중복 제거

**신규 파일 (계획 상)**:
- `eval_runner/reporting/{__init__,html,translate}.py` (Phase 2.0 완료 중)
- `eval_runner/reporting/llm.py` (Phase 2.0 — `generate_with_ollama` 일반화 + 캐시)
- `eval_runner/reporting/narrative.py` (Phase 2.0 — role 별 프롬프트 + fallback 체계. R1.1/R2.1/R3.1/R3.2 호출 지점)
- `eval_runner/{dataset,policy,scoring,runner}.py` (Phase 4)
- `eval_runner/tests/{test_golden.py, fixtures/tiny_dataset.csv, fixtures/expected_golden.json}` (Phase 0 완료)
- `eval_runner/docs/REPORT_SPEC.md` (Phase 0 작성, Phase 2.0 에서 §3.1.1/§3.2.1/§3.3.1/§3.3.2 + AC9~AC12 추가)
- (이연) `eval_runner/openai_wrapper_api.py`, `eval_runner/gemini_wrapper_api.py` — G1/G2, 별도 phase

---

## 6. Verification Strategy

**공통 전제**: 소스 수정은 이미지 재빌드 + 컨테이너 재기동을 거쳐야 실러닝 Jenkins 에 반영됨. 그 전까지는 production 격리.

**Phase 별 게이트**:
- **Phase 0**: 골든 하네스가 `main` 에서 pass, REPORT_SPEC 승인
- **Phase 1**: `TARGET_MODE=local_ollama_wrapper` 1회 성공, Jenkins UI 에 단일 모드만 노출, Langfuse off 로 summary.json 에 11지표 전부 존재
- **Phase 2**: 리포트 사용성 A/B — 리뷰어가 30초 내 빌드 결과 이해
- **Phase 3**: 메타 3종(judge/dataset/error breakdown) 리포트 상시 노출
- **Phase 4**: 골든 byte-match, `test_runner.py` ≤ 100 LOC
- **Phase 5**: 보정 세트 편차 수치 노출, N-repeat off 가 기본

**Cross-pipeline 회귀 체크 (매 Phase 후)**: Jobs 01/02/03 을 각 1회 실행 → 동작 변화 0 확인 (이론상 불필요하나 매트릭스 가정 운영 재검증).

**Langfuse 회귀 방지 (매 Phase 후)**: `grep -rn "Langfuse\|langfuse" eval_runner/` 로 조건부 import/instantiation 이 유지되는지 확인.

---

## 7. Milestones & Done Criteria

### 단계별 Done

| Phase | Done 시 사용자에게 보이는 변화 |
|---|---|
| 0 | 기획서 제거됨. `pytest tests/test_golden.py` 수동 재현 가능. |
| 1 | Jenkins 모드 드롭다운이 `local_ollama_wrapper` 단일로 정리. summary.json 에 latency/usage 전체 기록. 미구현 모드 선택으로 인한 빌드 깨짐 0. |
| 2 | Jenkins 탭 `AI Eval Summary` 이 **한눈에 보이는 대시보드** 로 바뀜 (헤더 + 11지표 카드 + case drill-down). 헤더에 **🤖 LLM 생성 임원 요약** (R1.1, 기본 on) + 각 실패 case 에 **LLM 쉬운 해설** (R3.1, 기본 on) 노출. 지표별 해석(R2.1) 과 조치 권장(R3.2) 은 env flag 로 opt-in. |
| 3 | 모든 리포트가 Judge/Dataset 버전을 자기기록. 시스템 에러와 품질 실패가 색상·섹션 분리. |
| 4 | `test_runner.py` 가 작아짐(≤100 LOC). Promptfoo 의존도 감소. |
| 5 | 리포트에 "Judge 변동성 편차" 수치 노출, 경계 case N-repeat 옵션 활성화 가능. |

### 전체 Done (모든 Phase 완료 시)

- [ ] 기획서 파일 제거 + 정본 선언 기록
- [ ] Phase 0 골든 하네스가 Phase 1~5 모든 변경을 회귀 검출
- [ ] Jenkins 에서 `04 AI평가` Job 의 `TARGET_MODE=local_ollama_wrapper` 가 성공 실행 (현 phase 단일 모드)
- [ ] Langfuse 크리덴셜 유무와 무관하게 11지표 전체 summary.json 기록
- [ ] `AI Eval Summary` Jenkins 탭이 운영자 1차 판단 도구로 기능
- [ ] **R1.1 LLM 임원 요약**이 모든 빌드에서 2~3 문장 생성, summary.json 의 `aggregate.exec_summary` 에 영구 기록
- [ ] **R3.1 LLM 실패 해설**이 모든 실패 case 마다 1문장, 하드코딩 fallback 체계 검증
- [ ] LLM 내러티브 결정론 캐시 동작 (동일 입력 → 동일 출력), timeout/error 시 graceful degrade
- [ ] REPORT_SPEC §3.1.1/§3.2.1/§3.3.1/§3.3.2 반영 + AC9~AC12 통과
- [ ] 모든 리포트가 `judge_meta`, `dataset_meta`, `error_type_breakdown` 3종 메타 포함
- [ ] `test_runner.py` ≤ 100 LOC, `reporting/` + `dataset.py` + `policy.py` + `scoring.py` + `runner.py` 로 분리
- [ ] Judge 변동성 관측 수단(보정 세트) 상시 가동, N-repeat 옵션 제공
- [ ] Jobs 01/02/03 동작에 영향 0 을 각 Phase 마다 재검증
- [ ] `CONVERSATION_LOG.md` 에 각 Phase 세션 요약 기록
