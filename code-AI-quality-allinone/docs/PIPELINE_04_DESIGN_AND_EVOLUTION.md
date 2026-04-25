# 파이프라인 4 (정적분석 결과분석 및 이슈등록) — 설계 의도와 진화 이력

> 작성: 2026-04-25
> 대상 파이프라인: `04 정적분석 결과분석 및 이슈등록` (Jenkins Job)
> 운영 컨테이너: `ttc-allinone` (Mac M-series 기준)

---

## 0. 이 문서가 다루는 것

이 문서는 두 가지 질문에 답한다:

1. **04 파이프라인은 무엇을 해야 하는가** (의도)
2. **그 의도에 도달하기 위해 지금까지 무엇을 시도했고, 각각 어떤 효과가 나왔는가** (회고)

단순 변경 로그가 아니다. 매 시도마다 **왜 그게 필요했고, 그래서 어떤 측정 결과가 나왔으며, 다음 시도는 무엇을 알게 됐는가** 의 인과를 보존한다. 후속 작업자가 같은 시행착오를 반복하지 않도록.

## 0.1 한 장 요약 — 지금까지의 흐름과 현 위치

### 04 파이프라인 의 본질

> SonarQube 정적분석으로 나온 수십~수백 건의 이슈를, **AI 가 우리 프로젝트의 실제 코드와 문서를 보고** 진짜 문제인지 판정하고 수정 방향을 제시해서, 개발자에게는 "조치 가능한 GitLab Issue" 만 도달하게 만드는 자동화.

### 시도 단계 (시간순, 평이한 표현)

각 단계를 한국어로 풀어서:

| 단계 | 무엇을 했나 | 핵심 결과 |
|------|-------------|----------|
| 베이스라인 | RAG (검색 기반 생성) 만 켜둠 | AI 가 자기 답변에 코드 인용 거의 안 함 (인용률 2.1%) |
| 초기 RAG 품질 개선 (P1·P1.5·P2) | 노이즈 제거, 검색 정확도 향상, 다언어 지원 | 인용률 2.1% → 65% |
| 도메인 메타 풍부화 (사이클 1·2) | 데코레이터 / HTTP 라우트 / 도메인 모델 / 문서화 정보를 KB 에 추가 | 인용률 75%, AI 답변이 더 구체적이 됨 |
| **AI 가 추출한 정보 활용도 측정** (사이클 3) | "AI 가 풍부한 KB 를 정말 답변에 쓰고 있나?" 를 측정하는 메트릭 신설 + 누수 7개 발견 | 새 메트릭 도입, 누수 발견 |
| **누수 봉쇄** (사이클 3+D) | 7개 누수 중 가장 영향 큰 3개 (tree-sitter 의 함수 인식 버그 등) 수정 | KB 청크 27 → 60, HTTP 라우트 검출 0 → 17, "AI 가 정적 메타를 답변에 인용한 비율" 0% → 30% |
| **실질적 원인분석 강화 시도** (사이클 3+E) | "전체 저장소 + 프로젝트 정보 기반 분석" 을 지향해 5종 입력 (의존성 그래프 / git 이력 / 유사 패턴 / 프로젝트 개요 / 답변 검토범위 의무) 추가 | **회귀 발생** — 4B 모델이 입력 분산을 처리 못 함. 인용률 80%→70%, 메타 인용 30%→20% |
| **회귀 회복 시도** (사이클 3+E') | 빈 섹션 헤더 제거 + 답변 검토범위 자동 후처리 + system prompt 압축 | 적용했으나 인프라 이슈 (Jenkins 실행자 부족) 로 측정 미완료 |

### 현재 위치

- ✅ 데이터 정확도 fix (tree-sitter 누수 봉쇄) 는 정량 효과 입증됨 — 사이클 3+D 가 마지막으로 검증된 안정 지점.
- ⚠️ 데이터 풍부도 확장 (Phase E) 는 회귀를 일으킴. "더 많은 컨텍스트 → 더 좋은 답변" 가정이 깨짐.
- 🔄 회복 처방 (사이클 3+E') 은 코드 적용 완료, 측정 대기.
- 🔍 본질적 한계 — 현 LLM (gemma4:e4b 4B) 의 attention bandwidth 가 입력 토큰 증가 시 무너짐. 모델 업그레이드 검토 카드.

### 이 문서의 내부 용어 표 (모르고 본문 읽으면 헷갈리므로 미리)

| 용어 | 뜻 |
|------|------|
| **사이클** | "측정 → 처방 → 재측정" 한 번의 주기. 베이스라인부터 시작해 cycle 3+E' 까지 진행 |
| **Phase A / B / C / D / E** | 사이클 3 안의 작업 그룹. A=KB 추출 보강 / B=프롬프트 보강 / C=가시화 / D=누수 봉쇄 추가 fix / E=실질 분석 강화 |
| **Phase E'** | Phase E 의 회귀 회복 처방 (a+b+c 조합) |
| **F1~F7** | 사이클 3 진단 시 식별된 7개 tree-sitter 누수 (kb_query 미사용, 다언어 미지원 등) |
| **citation rate** | AI 답변이 RAG 가 제공한 코드 청크를 백틱(\`\`)으로 실제 인용한 비율 |
| **ts_any_hit_pct** | 답변에 정적 메타(decorator/endpoint/매개변수명/RAG 청크 메타) 가 1번 이상 등장한 이슈 비율 |
| **partial_citation 강등** | AI 가 받은 청크 절반 미만 인용했는데 자신감 high 이면 자동 medium 으로 강등 + 라벨 부착 |
| **enclosing_function** | 이슈가 발생한 코드 라인을 포함하는 함수의 이름. tree-sitter 가 추출 |

### 이 문서를 어떻게 읽으면 되나

- 처음 읽는다 / 04 의 큰 그림 보고 싶다 → §0 (이 섹션) + §1 + §2 만 읽어도 충분
- 특정 사이클 결과 / fix 의도 알고 싶다 → §3 의 해당 사이클 + §5 메트릭 표
- 후속 작업 의사결정 → §4 (미해결 이슈) + §6 (회고) 가 가장 가치
- 코드 변경 매트릭스 → §7

---

## 1. 04 파이프라인의 존재 목적

### 1.1 사용자가 표명한 의도

> "**SonarQube 정적분석 결과 도출된 이슈에 대해 전체 저장소 코드 및 프로젝트 정보에 기반해 실질적인 원인분석 및 수정조치 방향을 제시**"

이 한 문장이 04 의 north star.

### 1.2 의도의 구성 요소

| 요소 | 정의 | 의미 |
|------|------|------|
| **입력** | SonarQube 정적분석 이슈 | rule 위반 사실만 있고 의미 해석은 없음 |
| **컨텍스트** | 전체 저장소 코드 + 프로젝트 정보 | "이 함수가 어디서 호출되는가", "프로젝트의 다른 코드는 같은 패턴을 어떻게 처리하는가", "도메인 어휘는 무엇인가" |
| **출력 1** | 실질적 원인분석 | 단순 rule 풀이가 아니라 "이 코드가 왜 이렇게 작성됐나", "이 패턴이 프로젝트의 컨벤션인가 일회성 실수인가", "이 코드가 보호하려던 invariant 는 무엇인가" 까지 |
| **출력 2** | 수정조치 방향 | 일반 best-practice 가 아니라 "이 프로젝트의 다른 N 곳은 X 패턴을 쓴다 → 같은 패턴으로 통일", "이 함수의 caller 23 곳 중 N 곳이 시그너처 변경 시 깨진다" 처럼 convention/impact-aware |

### 1.3 진짜 가치 (사용자가 표명한 것 + 우리가 추가로 발견한 것)

- **개발자 시간 절약**: Sonar 이슈 100건을 PM/개발자가 손으로 분류하는 대신 AI 가 1차 분류 + RAG 기반 영향 분석.
- **인지 부담 흡수**: PM 친화 본문 (사이클 3+D 의 PM 친화 GitLab Issue 본문) 이 "지금 고쳐야 하나" 를 신호등 1줄로 압축.
- **정직성 — AI 가 모르는 것을 모른다고 표시**: 답변에 검토 범위 (`🔍 검토: callers N · tests M · ...`) 자동 부착으로 silent confidence 차단.
- **사전학습 → 답변 활용 인과 측정**: tree_sitter_hits 메트릭으로 "AI 가 정말 우리 프로젝트 코드를 봤나" 를 정량 검증.

### 1.4 하지 말아야 할 것 (anti-goals)

- ❌ Sonar rule 설명을 그냥 자연어로 풀어쓰는 것 (이건 MDN 문서 수준)
- ❌ AI 가 자신 없이 자신감 있게 판정 (silent failure 위험)
- ❌ 비-개발자에게 코드 블록 + Rule key 보여주기 (인지 부담)
- ❌ "AI 가 분석한다는 사실 자체에 환상" — 같은 rule 풀이를 비싸게 다시 만드는 것

---

## 2. 의도 vs 현 상태 — 4 가지 갭 (사이클 3+D 시점 진단)

| Gap | 의도 | 사이클 3+D 시점 실제 | 의미 |
|-----|------|----------------------|------|
| **G1. "전체 저장소 코드 기반"** | 함수 dependency graph + 모든 호출 관계 | RAG retrieval top_k=15, 카테고리별 top-3 = 답변당 6~9 청크 | "전체" 가 아니라 일부 |
| **G2. "프로젝트 정보"** | 도메인 어휘 + 의존성 + 아키텍처 결정 + 변경 이력 | KB 청크 메타 + git blame 1줄 | 코드 메타만 있고 README/CONTRIBUTING/package.json/git log 깊이 부재 |
| **G3. "실질적 원인분석"** | "왜 이렇게 작성됐는가" + "보호하려던 invariant" + "프로젝트 컨벤션" | rule 풀이 + 1~2 caller 언급 | 현상 설명 수준에 머묾 |
| **G4. "수정 조치 방향"** | convention-aware + impact 분석 ("caller N 곳 깨짐") | "Number.parseInt 권장" 식 generic best-practice | MDN 문서 수준 |

이 4개 갭을 메우려는 작업이 **Phase E** (사이클 3+E) 의 출발점.

---

## 3. 사이클 별 진화 이력

각 사이클의 **목적 / 핵심 fix / 측정 결과 / 다음 사이클로의 교훈** 을 정리.

### 3.1 사이클 0 (베이스라인) — 2026-04-25 새벽 이전

- 측정: citation 2.1%, callers fill 0%, tests fill 0%, KB 메타 `?::?` 비율 79%
- 문제: KB 가 비어있는 것이나 다름없고 RAG 답변이 일반 원칙에 의존

### 3.2 P1 / P1.5 / P2 사이클 — 2026-04-25 새벽

> 상세는 README §0 표 참조. 핵심:

- **P1**: tree-sitter pass 2 (callers / test_paths 역인덱스), context_filter Code 노드 (self-exclusion + 카테고리 섹션화)
- **P1.5**: vendor/minified 제외, trivial/dup 청크 필터, weighted_score 재정렬, score_threshold=0.35, RAG Diagnostic Report 신설
- **P2**: 다언어 지원 확장 (Go/Rust/C#/Kotlin/C/C++), docstring 추출, retry variation, HyDE 간소화

측정: citation 2.1% → 60% → 65%, callers 25%, tests 50% (P5+P7+T1+T2 후)

### 3.3 사이클 1+2 (tree-sitter 강화) — 2026-04-25

- import 기반 caller 그래프 (false-positive 차단)
- `@app.route` decorator → endpoint 추출
- decorator 텍스트 footer 노출
- TS interface/type/enum 청크화
- JSDoc `@param/@returns/@throws` 구조 분리
- KB 인텔리전스 8 카드 (PM 친화 RAG Diagnostic Report v2)

측정: citation 65→75%, depth 3.10→4.20, partial_citation 자동 강등 2→0

### 3.4 사이클 3 (Phase A+B+C — tree-sitter 누수 봉쇄) — 2026-04-25 후속

> 첫 번째 큰 cycle. 사이클 1+2 까지 KB 추출은 풍부했으나 **쿼리/프롬프트/진단 가시화 단계** 에서 신호가 새고 있음을 확인.

#### 7개 누수 식별

| # | 누수 | 위치 |
|---|------|------|
| F1 | kb_query 가 endpoint/decorators/params 미사용 | dify_sonar_issue_analyzer.build_kb_query() |
| F2a | 이슈 함수의 정적 메타가 LLM 프롬프트에 미주입 | sonar_issue_exporter / analyzer |
| F2b | analyzer 가 LLM 프롬프트용 enclosing_meta 텍스트 미생성 | analyzer |
| F2c | workflow YAML 의 user 프롬프트에 정적 메타 섹션 부재 | sonar-analyzer-workflow.yaml |
| F3 | context_filter used_items 에 메타 보유 플래그 부재 | workflow YAML |
| F4 | citation 측정이 정적 메타 활용 미감지 | analyzer._compute_citation |
| F5 | go/rust/c#/kotlin/c/cpp callees 분기 부재 | repo_context_builder.collect_callees() |
| F6 | imperative route (`app.get('/x', handler)`) 미검출 | repo_context_builder |
| F7 | Google/NumPy docstring 미지원 | repo_context_builder.parse_docstring_structure() |

#### Phase A — 추출 보강

- **F5** `collect_callees()` 에 go/rust/c_sharp/kotlin/c/cpp 분기 추가 (생성자·매크로·method_call 포함). 11개 언어 전부 callers 역인덱스 가능.
- **F6** `extract_imperative_routes()` 신설 — Express/Koa `app.get('/x', handler)` + Flask `add_url_rule` AST 패스로 검출 → endpoint 매핑.
- **F7** `parse_docstring_structure()` 에 Google `Args:` / `Returns:` / `Raises:` block + NumPy `----` underline 파서 추가. raw 추출 경로 `_extract_leading_doc_text` 분리.
- `_kb_intelligence.json` 사이드카 자동 작성 — scope/depth/quality 3계층, 진단 리포트의 데이터 소스.

#### Phase B — 보존+쿼리+프롬프트

- `sonar_issue_exporter._enclosing_meta()` 신설 — symbol/lines 외 decorators/endpoint/doc_struct/callees 까지 청크에서 통째 추출. enriched 객체에 9개 필드 추가.
- `analyzer.build_kb_query()` attempt=0/2 에 `endpoint:` `decorators:` `params:` 라인 추가.
- `format_enclosing_meta()` 신설 — LLM 프롬프트용 멀티라인 텍스트 생성.
- Dify start 변수 `enclosing_meta` 추가 + workflow user 프롬프트에 "## 이슈 함수 정적 메타 (tree-sitter)" 섹션 + system 프롬프트에 "정적 메타 활용" 규칙.

#### Phase C — 가시화 (두 리포트 모두 PM 친화)

- context_filter Code 노드의 `used_items` 에 `has_decorators`/`has_endpoint`/`has_doc_struct` boolean + `decorators_raw`/`endpoint_raw`/`doc_params_raw` 추가.
- `_compute_tree_sitter_hits()` 신설 — endpoint/decorator/param/RAG-meta 4종 신호의 답변 등장 횟수 측정. `tree_sitter_hits` 메트릭으로 보관.
- **diagnostic_report_builder 4-stage 학습진단 재설계** — 기존 8 카드 나열을 폐기. Scope/Depth/Quality/Impact 사다리:
  - Stage 1 학습 범위 (파일·언어 분포 막대·노이즈 제외)
  - Stage 2 학습 깊이 (호출 매핑률 / endpoint / 테스트 연결률 / 도메인 모델 / 문서화)
  - Stage 3 학습 품질 (파서 성공률 / 언어 비대칭 자동 경고)
  - Stage 4 분석 영향 (사전학습 → 답변 활용 인과: ts_hits)
- per-issue used_items 표에 🛡️🌐📝 메타 아이콘 컬럼.
- **gitlab_issue_creator PM 친화 본문 재설계** — 🚦 Action Verdict 신호등 + 📌 무엇이 / 🎯 어디서 + 정적 컨텍스트 / ⚠️ 영향 / 🛠️ 수정 / 🔍 AI 판단 근거 (NEW) / 📂 같은 패턴 / 📖 기술 상세 ▶ 접기.

측정 (사이클 3 — D 적용 전 마지막 빌드):
- KB chunks 27 (nodegoat — 작은 수치)
- HTTP API endpoints 0 (F6 가 작동했어야 했지만 nodegoat 의 routes/index.js 가 chunk 추출 실패해 0건)
- ts_any_hit_pct 0%
- per-issue 헤더에 `fn: err` 같은 enclosing_function 오인 발견

### 3.5 사이클 3+D (3가지 fix) — 2026-04-25 늦은 저녁

> 사이클 3 의 미흡 결과를 정직하게 진단해 3가지 fix 추가.

#### 진단 — 3가지 명백한 누수

| # | 문제 | 원인 |
|---|------|------|
| 1 | `routes/index.js` 가 KB 에 없음 | 파일 전체가 `const index = (app, db) => {}` 익명 화살표 함수. `get_symbol_name` 의 fallback 이 직계 identifier 자식 찾기 → arrow_function 직계는 statement_block 등 컴포지트 → None → chunk skip → `parser_failed += 1` |
| 2 | F6 imperative route 0건 | `extract_imperative_routes` 자체는 19건 모두 정상 검출! 하지만 매칭이 같은 파일 내 청크로 한정 (sym_to_idx file-local). `routes/index.js` 의 `displayLoginPage` 가 가리키는 핸들러 (`SessionHandler.displayLoginPage`) 는 다른 파일에 있어 cross-file 매칭 안 됨 |
| 3 | `fn: err` 버그 | `err => { ... }` 단일 파라미터 arrow_function 의 직계 자식이 identifier `err` → fallback 이 그걸 symbol 로 잡음 |

→ 모두 **arrow_function symbol naming + cross-file 매칭 결손** 으로 수렴.

#### Fix

| Fix | 변경 |
|-----|------|
| **A** get_symbol_name 강화 | name field 없으면 arrow_function/function_expression 의 부모 컨텍스트 (variable_declarator / pair / assignment / public_field_definition) 에서 이름 부여. 그 외 익명 → None 반환 (chunk skip 유도) |
| **B** cross-file imperative routes | `extract_chunks_from_file` 가 imperative_pairs 를 chunk meta 로 보존 (또는 routing-only sentinel chunk). `scan_repo` 의 pass 1.5 단계에서 모든 청크 symbol 인덱스 대비 매칭 |
| **C** parser_failed_files 가시화 | `_kb_intelligence.json` 의 `scope.parser_failed_files: list[str]` (cap 50). diagnostic_report_builder Stage 1 에 펼침 박스 |
| (보너스) | diagnostic narrative 를 Stage 1/3/4 색상 조합으로 동적 분기 — "원료 부재" vs "활용 누수" 케이스 구분 |

#### 측정 (Cycle 3+D)

| 메트릭 | Cycle 3 | **Cycle 3+D** | 변화 |
|--------|---------|----------------|------|
| KB chunks | 27 | **60** | +122% |
| HTTP API endpoints | 0 | **17** | +∞ (cross-file 매칭 작동) |
| 답변에 정적 메타 등장 비율 (ts_any_hit_pct) | 0% | **30%** | +30%p |
| RAG citation rate (평균) | 70% | **80%** | +10%p |
| `fn:err` 버그 | 발생 | **0건** | 100% 수정 |
| `fn:` 공란 | 다수 | **0건** | 모든 enclosing_function 정확히 매핑 |
| parser_failed 25개 정체 | 미상 | **모두 list 노출** | 정당한 케이스 (Cypress test, config) 확인됨 |

#### 교훈

- tree-sitter 정확도는 **코드를 직접 진단** 하면 즉시 해결 가능. nodegoat 같은 실 레포에서 직접 호출해 결함 위치를 찾는 게 가장 빠름.
- `routes/index.js` 같은 익명 화살표 패턴은 흔하다. 부모 컨텍스트 lookup 이 필수.
- file-local 매칭의 한계 — 일반적 Express/Koa 구조 (라우트 등록 ≠ 핸들러 정의) 에서 항상 0건이 됨.

### 3.6 사이클 3+E (5개 fix — 실질적 원인분석 보강) — 2026-04-25 후속

> 사용자가 명시한 04 의 의도 (§1.1) 대비 4개 갭 (§2) 을 메우는 시도.

#### 5개 Fix 설계

| Fix | 의도 | 데이터 소스 | LLM 활용 경로 |
|-----|------|-------------|---------------|
| **E1** depth-2 graph traversal | 영향 범위 추적 | exporter 의 `_depth2_callers()` — direct caller + callers-of-callers | `dependency_tree` paragraph |
| **E2-lite** 프로젝트 메타 직접 첨부 | 도메인 어휘 + 의존성 인식 | exporter 의 `_build_project_overview()` — README + CONTRIBUTING + package.json deps | `project_overview` paragraph (모든 이슈 동일) |
| **E3** git 깊이 확장 | "왜 이렇게 작성됐나" 단서 | exporter 의 `_git_context()` 에 `git log -L` (함수 변경 이력 5개) + `_similar_rule_history()` (같은 rule 의 과거 fix commit) | `git_history` paragraph |
| **E4** 답변 scope 정직 표기 | silent confidence 차단 | system prompt 의무 룰 | LLM 이 답변 끝에 `🔍 검토: callers N · tests M · ...` 추가 (의무) |
| **E5** 유사 패턴 위치 수집 | 프로젝트 전반 패턴 인식 | exporter 가 같은 rule_key 의 다른 위치 사전 집계 | `similar_locations` paragraph |

#### 코드 변경 (4 파일)

| 파일 | 추가 / 변경 |
|------|-------------|
| `sonar_issue_exporter.py` | `_depth2_callers()`, `_build_project_overview()`, `_similar_rule_history()` 신설. `_git_context()` 에 `git log -L` + similar_fixes. main loop 에 4종 enrichment + 출력 JSON 의 `metadata` 섹션 |
| `dify_sonar_issue_analyzer.py` | `format_dependency_tree()`, `format_git_history()`, `format_similar_locations()` 신설. metadata 읽고 4종 inputs 주입 |
| `sonar-analyzer-workflow.yaml` | start node 에 4 신규 paragraph 변수. LLM user 프롬프트에 4 섹션. system 프롬프트에 E4 검토범위 의무 + 5종 정보 활용 가이드 1단락 |

#### 측정 (Cycle 3+E)

| 메트릭 | Cycle 3+D | **Cycle 3+E** | 변화 |
|--------|-----------|----------------|------|
| empty impact | 0/10 | 0/10 | = |
| retry_exhausted | 0/10 | 0/10 | = |
| avg citation rate | 80% | **70%** | ↓ −10%p |
| avg tree_sitter_hits | 0.30 | **0.20** | ↓ −33% |
| ts_any_hit_pct | 30% | **20%** | ↓ −10%p |
| **E4 검토범위 표기** | (없음) | **0/10** | LLM 완전 무시 |
| 04 build 시간 | 7~8분 | 7.3분 | = |

#### 가설 vs 결과

| 가설 | 결과 |
|------|------|
| "4개 신규 입력 → LLM 답변에 도메인 어휘 / 영향 분석 / 패턴 인식 / 작성 의도 등장" | **REGRESS** — citation 도 ts_hits 도 모두 하락 |
| "E4 의무 룰 → LLM 이 답변 끝에 검토범위 표기" | **0/10 무시** — gemma4:e4b 가 system prompt 마지막 룰 채택 안 함 |

#### 진단 — 왜 regress 했나

1. **입력 분산**: 4 섹션 추가로 LLM attention 분산. 이전엔 enclosing_meta 1 섹션만 있어 집중도 높았음.
2. **빈 섹션도 헤더 출력**: workflow user 프롬프트가 `## Dependency Graph\n{{#start.dependency_tree#}}` 형식 → 변수 비어있어도 헤더만 남음 = noise.
3. **system prompt 길이**: Phase E 가이드 1단락 + E4 의무 추가로 thinking 모델 부담 증가.
4. **E4 의무 룰의 표현력**: "impact_analysis_markdown 의 마지막 줄에 다음 형식으로..." — 마지막 룰 추가는 thinking 모델 (gemma4:e4b) 에 약하게 인식됨.

#### 교훈

- **"더 많은 컨텍스트 → 더 좋은 답변" 가정이 깨짐**. 4B 모델 (gemma4:e4b) 의 attention bandwidth 한계.
- **데이터 추출 (exporter 측) 는 잘 됐는데 LLM 활용 측에서 누수** — Phase B/C 에서도 같은 패턴이 있었지만 거기선 1 섹션만 추가라 영향 작았음.
- LLM 의 자발적 의무 (system prompt 룰) 는 **deterministic 후처리** 가 더 안정. 측정 메트릭 (E4 표기율) 이 0/10 으로 명시적으로 보여줌.

### 3.7 사이클 3+E' (a+b+c 절충안) — 2026-04-25 늦은 밤

> 사이클 3+E 의 regress 를 회복하기 위한 처방. 단, 사이클 3+E 의 데이터 추출은 보존.

#### 처방 3개

| Fix | 변경 | 의도 |
|-----|------|------|
| **(a)** 헤더를 format_*() 안으로 이동 | format_dependency_tree() 등 4개 함수가 헤더 (`## ...`) 까지 포함해 반환. 빈 값이면 통째 빈 문자열. workflow user 프롬프트에서 정적 헤더 제거 | 빈 섹션 noise 제거 |
| **(b)** E4 의 LLM 의무 → analyzer 자동 후처리 | `_build_out_row()` 에서 LLM 답변 받은 후 `🔍 검토: callers N · tests M · others K · depth-2 D · git history H · 유사 위치 L` 자동 부착. LLM 답변에 이미 `🔍 검토` 있으면 skip | 정직성 메트릭 100% 보장 |
| **(c)** system prompt 압축 | Phase E 가이드 1단락 → 2~3줄로. E4 의무 단락 제거 | LLM attention 회복 |

#### 코드 변경

- `dify_sonar_issue_analyzer.py`:
  - format_*() 4개 모두 헤더 포함 / 빈 값이면 빈 문자열
  - `format_project_overview()` 신설 (헤더 일관성)
  - `_build_out_row()` 에 검토범위 자동 부착 로직 (LLM 답변 후처리)
- `sonar-analyzer-workflow.yaml`:
  - user 프롬프트에서 4개 정적 헤더 제거 (변수가 헤더 포함하므로)
  - system 프롬프트 의 Phase E 가이드 압축 (E4 의무 단락 삭제)

#### 측정

**미완료** — 빌드 도중 인프라 이슈 발생.

#### 진행 중 발생한 이슈

1. **Dify SQLAlchemy hit_count bug 재발** — 02 사전학습 단계에서 60청크 업로드 중 `RemoteDisconnected` → `Read timed out (300s)` 패턴. `dify-api supervisorctl restart` 로 회복.
2. **Jenkins executor 부족** — provision.sh 가 Job 재생성 시 1차 build (parameter 등록용 빈 build) 가 default 파라미터로 cascade → 02/03/04 큐 점유. 동시에 2차 buildWithParameters 가 큐 추가 → executor 2개 한도로 03 가 "Still waiting to schedule task" 14분 대기.
3. **사용자 발견** — "정적분석 전혀 돌고있지 않다" — 03 가 시작도 못 한 상태로 장시간 BUILDING 표시.
4. 모든 빌드 강제 abort — 클린 상태 (executor busy 0, queue 0).

#### 현 상태

- KB 는 사이클 3+D 결과 살아있음 (chunks=60, endpoints=17, parser_failed_files 사이드카 정상)
- Phase E' 코드 (a+b+c) + 새 Workflow (240b4a3d...) 적용됨
- 04 단독 실행 또는 01 chain 재실행으로 측정 가능

---

## 4. 미해결 이슈 / 향후 검토

### 4.1 Phase E' 의 효과 측정 미완료

- 빌드 abort 로 cycle 3+E' 의 ts_any_hit / citation / E4 표기율 미정.
- 다음 액션: 04 단독 실행 (02 결과 재사용) 또는 01 chain 재실행 후 측정.

### 4.2 인프라 한계

| 이슈 | 원인 | 처방 후보 |
|------|------|----------|
| Jenkins executor 2개 한도 | 기본 설정 | jenkins-init Groovy 에서 numExecutors 4~6 으로 증가 |
| provision.sh Job 재생성 시 1차 빈 build cascade | parameter 등록 회로 | "is not parameterized" 첫 호출도 정상 실행되도록 Jenkinsfile 의 parameters 블록 정리 |
| dify-api workers=1 | 단일 worker | gunicorn workers 2~3 + worker-connections 적절 조정 |

### 4.3 LLM 모델 한계

- gemma4:e4b (4B 클래스 thinking 모델) 가 입력 토큰 증가 시 attention 분산 → 답변 품질 regress.
- system prompt 의 추가 룰을 LLM 이 약하게 인식 (E4 채택률 0/10).
- **모델 업그레이드 검토** — 사용자가 NVIDIA GPU VRAM 8GB 한도 언급. 후보:
  - qwen2.5-coder:7b (4.7GB)
  - phi-4-mini:3.8b (3.5GB)
  - qwen3 7B (4.5GB)
  - deepseek-coder-v2:16b-lite (MoE, active 2.4B, 빠름)

### 4.4 04 의 의도 대비 잔존 갭

사이클 3+D 후에도 G3/G4 (실질적 원인분석 / 수정조치 방향) 는 부분 해소만 됨. Phase E 가 의도였지만 LLM 활용 측 regress 로 효과 미확인. Phase E' 의 (a+b+c) 가 이 갭을 메울지 측정 필요.

### 4.5 Phase E rollback 옵션

- (a)+(b)+(c) 처방 후에도 regress 면 Phase E 자체를 git revert 하고 사이클 3+D 수준에서 정착하는 것도 합리적 옵션.
- 그 경우 04 의 의도 대비 갭은 G1/G2 부분 + G3/G4 미해소 상태로 유지됨. "E2-lite (project_overview 첨부) 만 살리고 E1/E3/E5 는 제거" 같은 부분 rollback 도 검토 가능.

---

## 5. 측정 메트릭 누적 추적 (참고용)

| 사이클 | citation rate | ts_any_hit_pct | KB chunks | endpoints | partial_citation 강등 |
|--------|---------------|-----------------|-----------|-----------|----------------------|
| 베이스라인 | 2.1% | — | — | — | — |
| Fix R + 4종 | 60% | — | — | — | — |
| P1+P3+P4 | 60% | — | — | — | — |
| P5+P7+T1+T2 | 65% | — | — | — | 2/10 |
| 사이클 1 | 65% | — | — | — | 1/10 |
| 사이클 2 | 75% | — | — | — | 0/10 |
| 사이클 3 | 70% | 0% | 27 | 0 | 0/10 |
| **사이클 3+D** | **80%** | **30%** | **60** | **17** | **0/10** |
| 사이클 3+E | 70% | 20% | 60 | 17 | 0/10 |
| 사이클 3+E' | (미측정) | (미측정) | 60 | 17 | (미측정) |

🎯 누적 (사이클 3+D 기준):
- citation rate **2.1% → 80%** (38배)
- KB 청크 **0 → 60**
- HTTP API 진입점 **0 → 17**
- partial_citation 자동 강등 **2 → 0**
- 답변에 정적 메타 등장 비율 (사이클 3 신규 메트릭) **0% → 30%**
- per-issue enclosing_function 정확도 **부분 → 100%**

---

## 6. 04 파이프라인의 본질 — 회고

**Phase D 가 작동하고 Phase E 가 regress 한 이유**:
- Phase D 는 **데이터 정확도 fix** — tree-sitter 가 더 정확하게 추출하니 모든 단계 (검색/프롬프트/측정) 가 동시에 개선됨.
- Phase E 는 **데이터 풍부도 확장** — 더 많은 정보를 LLM 에 제공했지만, LLM 은 그 정보를 활용하지 않았고 오히려 distraction 으로 작용.

**04 의 정체성 재정의** (이번 사이클들로 명확해진 것):
- 04 는 "AI 가 더 정확한 판단" 을 하는 곳이 아니라 "**AI 가 받은 정확한 데이터로 일관된 답변을 만드는**" 곳.
- 정직성 메트릭 (E4 검토범위, partial_citation 강등) 이 04 의 핵심 가치.
- 입력 데이터의 **정확도** 가 풍부도보다 우선 — Phase D 작동, Phase E regress 가 이를 입증.

**다음 사이클의 가설**:
- "더 많은 입력" 보다 "**더 정확한 입력 + LLM 부담 감소**" 가 ROI 높음.
- (a)+(b)+(c) 가 그 방향이고, 측정으로 검증 필요.
- 그 후에도 regress 면 모델 업그레이드 (qwen2.5-coder:14b 등) 를 다음 카드로.

---

## 7. 부록 — 코드 변경 파일 목록

### 사이클 3 (Phase A+B+C)

| 파일 | 변경 |
|------|------|
| `pipeline-scripts/repo_context_builder.py` | F5/F6/F7 + KB 사이드카 |
| `pipeline-scripts/sonar_issue_exporter.py` | F2a — _enclosing_meta() |
| `pipeline-scripts/dify_sonar_issue_analyzer.py` | F1+F2b — kb_query 보강 + format_enclosing_meta() + tree_sitter_hits |
| `scripts/dify-assets/sonar-analyzer-workflow.yaml` | F2c+F3 — enclosing_meta var + system prompt + context_filter 메타 플래그 |
| `pipeline-scripts/diagnostic_report_builder.py` | 4-stage 학습진단 재설계 |
| `pipeline-scripts/gitlab_issue_creator.py` | PM 친화 본문 재설계 |

### 사이클 3+D (3가지 fix)

| 파일 | 변경 |
|------|------|
| `pipeline-scripts/repo_context_builder.py` | Fix A — get_symbol_name 강화 / Fix B — cross-file imperative routes / Fix C — parser_failed_files |
| `pipeline-scripts/diagnostic_report_builder.py` | parser_failed 펼침 박스 + narrative 동적 분기 |

### 사이클 3+E (5개 fix)

| 파일 | 변경 |
|------|------|
| `pipeline-scripts/sonar_issue_exporter.py` | E1 _depth2_callers / E3 _git_context 확장 + _similar_rule_history / E5 rule_to_locations / E2-lite _build_project_overview / metadata 출력 |
| `pipeline-scripts/dify_sonar_issue_analyzer.py` | format_dependency_tree / format_git_history / format_similar_locations / metadata 읽고 inputs 주입 |
| `scripts/dify-assets/sonar-analyzer-workflow.yaml` | 4 신규 start var / user 프롬프트 4 섹션 / system 프롬프트 가이드 + E4 의무 |

### 사이클 3+E' (a+b+c)

| 파일 | 변경 |
|------|------|
| `pipeline-scripts/dify_sonar_issue_analyzer.py` | (a) format_*() 헤더 포함 / format_project_overview / (b) _build_out_row 자동 후처리 |
| `scripts/dify-assets/sonar-analyzer-workflow.yaml` | (a) user 프롬프트 헤더 제거 / (c) system 프롬프트 압축 + E4 의무 제거 |

---

_이 문서는 2026-04-25 기준 04 파이프라인 의 사이클 3 → 3+E' 까지의 진화 이력을 재구성한 회고이며, 후속 사이클이 진행되면 §3 에 추가 항목 / §5 메트릭 표 갱신이 필요._
