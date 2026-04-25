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
| **회귀 회복 시도** (사이클 3+E') | 빈 섹션 헤더 제거 + 답변 검토범위 자동 후처리 + system prompt 압축 | **부분 회복** — 인용률 80% 회복 + 검토범위 자동표기 0/10 → 10/10. 그러나 정적 메타 인용은 20% 그대로 (3+D 의 30% 회복 못 함) |
| **프롬프트-creator 정합성 정렬** (사이클 3+F) | system 프롬프트 정리 (코드네임 제거 / classification·confidence 신호 명시 / suggested_fix·diff 케이스 분리) + `fp_reason` dead field 를 Sonar 코멘트로 부활 + 본문 중복 하드 차단 | 측정 전 (다음 빌드 대기) — 8개 LLM 필드 정합성 7/8 → 8/8, 본문 중복 deterministic 차단 |
| **PM 친화 리포트 v3 + 인프라 정비** (사이클 4) | 02 사전학습 리포트 8 신규 섹션 (TL;DR / 프로젝트 / 구조 / 연관관계 / 테스트 / 학습 진단 / 결론·액션 / 디버깅 접힘) + 무한 indexing wait + sonar.java.binaries fix + --report-only 모드 + nodegoat→realworld 단일화 | end-to-end 검증 완료 (build #6 SUCCESS) — PM 5분 의사결정 가능 / KB 358 docs / 03+04 정상 |
| **Rule 한글 + impact 충실화** (사이클 5) | Rule 설명 Ollama gemma4 한글 번역 (rule_key 캐시) + impact 분량 3~6줄 → **최소 10 문장 4-구조** (본질·맥락·영향·방향) | build #6 검증 — Rule 한글 518자 / impact 14문장 1197자 / RAG 인용 6+ |
| **가독성 강화** (사이클 5+α) | 모든 문장 끝 두 공백 + 줄바꿈 (soft break) / 4-구조 사이 빈 줄 / 추상어 → 구체어 / 전문용어 풀이 / 비유 OK | build #8 검증 — 33줄/11 soft-break/16 빈 줄, 주니어 친화 표현 정착 |
| **04 빌드 결과 리포트 v2** (사이클 6) | "RAG Diagnostic Report" → **"정적분석 결과리포트"** PM 친화 6 섹션 (TL;DR funnel / 즉시 조치 위험 카드 / 모듈별 표 / 분포 / 신뢰 신호 / 개발자 진단 접힘) + iid 직접 링크 | build #3 검증 — 6 섹션 + iid 매핑 + 위험 카드 자동 추출 |

### 현재 위치

- ✅ 데이터 정확도 fix (tree-sitter 누수 봉쇄) 는 정량 효과 입증됨 — 사이클 3+D 가 정적 메타 인용에서 가장 높은 안정 지점 (30%).
- ⚠️ 데이터 풍부도 확장 (Phase E) 는 회귀를 일으킴. "더 많은 컨텍스트 → 더 좋은 답변" 가정이 깨짐.
- 🟡 Phase E' (회복 처방) 적용 후 인용률·검토범위 표기는 회복했으나 정적 메타 인용은 미회복.
- 🔍 **새 핵심 발견**: KB 학습 (Stage 2) 와 답변 활용 (Stage 4) 사이의 비대칭 — 함수 호출 매핑 95% 인데 RAG retrieval 로 caller 청크 회수율은 20%. 학습 → 검색 단계에서 누수.
- 🔍 본질적 한계 — 현 LLM (gemma4:e4b 4B) 의 attention bandwidth 가 입력 토큰 증가 시 무너지고, 정적 메타 (endpoint/decorator/매개변수명) 를 본문에 인용하지 못함.
- 🧹 **사이클 3+F (design alignment)**: 프롬프트와 `gitlab_issue_creator` 사이의 dead field (`fp_reason`) 를 Sonar 코멘트로 활용 + 본문 중복 하드 차단 + system 프롬프트 -8% 토큰 정리. 측정은 다음 빌드 대기 — citation/ts_any_hit 회귀 부재 확인 필요.
- 📊 **사이클 4 (PM 친화 리포트 v3 + 인프라 정비)**: 02 사전학습 리포트 청중 재정의 (개발자 → PM), 8 섹션 + sticky nav + verdict color-coding. 무한 indexing wait, sonar.java.binaries 빈 디렉토리 trick, nodegoat → realworld 단일화. end-to-end 흐름 (offline 빌드~04 실행) 자동화 검증 완료.
- 🇰🇷 **사이클 5 (Rule 한글 번역 + impact 충실화)**: SonarQube 룰 설명을 Ollama gemma4 로 한국어 번역 (rule_key 캐시). impact_analysis 분량 3~6줄 → **최소 10 문장 4-구조** (본질·맥락·영향·방향), RAG 컨텍스트 근거 의무 강화.
- ✏️ **사이클 5+α (가독성 강화)**: 한 단락 안에 여러 문장 뭉치는 문제 해소 — 모든 문장 끝 soft line break, 추상어 → 구체어, 전문용어 풀이 동반. PM/주니어 개발자가 GitLab Issue 본문을 자력으로 읽고 이해 가능한 상태.
- 📊 **사이클 6 (04 빌드 결과 리포트 v2 — "정적분석 결과리포트")**: 04 의 publishHTML 탭 리포트가 PM 비친화였음. 6 섹션 PM 대시보드로 재설계 — TL;DR funnel (Sonar→AI→GitLab) / 즉시 조치 위험 카드 (룰 기반 자동 추출) / 모듈별 GitLab Issue 표 (iid 직접 링크) / 결함 분포 / AI 신뢰 신호 / 개발자 진단 접힘. PM 한 화면에서 트리아지·할당·신뢰도 의사결정 가능.

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

#### 측정 (Cycle 3+D / 3+E / 3+E' 비교)

| 메트릭 | 3+D | 3+E | **3+E'** | E' 의 변화 |
|--------|-----|-----|----------|-----------|
| empty impact | 0/10 | 0/10 | **0/10** | = |
| retry_exhausted | 0/10 | 0/10 | **0/10** | = |
| avg citation rate | 80% | 70% | **80%** | ✅ 회복 |
| avg citation_depth | 4.2 | (미상) | **4.3** | ✅ 사상 최고 |
| ts_any_hit_pct | 30% | 20% | **20%** | ❌ 미회복 |
| avg ts_hits | 0.30 | 0.20 | **0.20** | ❌ 미회복 |
| **E4 검토범위 표기** | — | 0/10 | **10/10** | ✅ deterministic 후처리 100% 작동 |
| partial_citation | 0/10 | 0/10 | 1/10 | (정상 — P7 강등 작동) |

#### Phase E' 의 작동 / 미작동 분리

**✅ 성공한 처방 (3가지 중 2가지)**

- **(a) 빈 섹션 헤더 제거** — citation 70% → 80% 회복은 noise 감소가 기여한 것으로 추정.
- **(b) E4 자동 후처리** — **10/10 채택률, 100% 작동**. 모든 답변에 `🔍 검토: callers N · tests M · others K · depth-2 D · git history H · 유사 위치 L` 자동 부착됨. 가설 검증: LLM 의 자발적 의무 (0/10 무시) → analyzer 가 deterministic 으로 보장 (10/10).
- **(c) system prompt 압축** — citation_depth 4.3 (사상 최고) 은 압축이 LLM attention 회복에 도움 됐음을 시사.

**❌ 회복 실패한 부분**

- ts_any_hit 20% → 30% 회복 못 함.
- **HTTP route 인용 0/10 / 데코레이터 인용 0/10 / 매개변수명 인용 0/10** — 이슈 함수 자체의 정적 메타가 답변에 1건도 인용되지 않음.
- RAG 청크 메타 인용은 2/10 — Phase E 의 4개 신규 입력 중 RAG 청크의 endpoint 메타만 일부 흘러감.

#### 가장 큰 발견 — Stage 2 vs Stage 4 의 비대칭

| Stage 2 (KB 가 학습한 것) | Stage 4 (답변에 쓴 것) |
|--------------------------|------------------------|
| 함수 호출 매핑률 **95%** | callers bucket fill **20%** |
| HTTP API 진입점 **17개 매핑** | HTTP route 인용 **0건** |

→ KB 학습은 풍부한데 **RAG 검색 단계에서 caller/test 청크를 못 꺼냄**. 사이클 3+E' 자동 진단이 이를 명확히 지지:

- B (callers=0): **8/10 (80%)** — 호출 관계 청크가 retrieve 안 됨
- C (tests=0): **10/10 (100%)** — 테스트 청크 전부 retrieve 실패

이는 **04 의 진짜 누수가 RAG retrieval 단계에 있다** 는 새 가설을 제공. KB 메타는 충실하니 Phase E 의 추가 입력이 아닌 **검색 자체의 임계치/쿼리 전략** 을 손봐야 한다는 방향.

#### 진행 중 발생한 이슈 (1차 빌드)

1. **Dify SQLAlchemy hit_count bug 재발** — 02 사전학습 단계에서 60청크 업로드 중 `RemoteDisconnected` → `Read timed out (300s)` 패턴. `dify-api supervisorctl restart` 로 회복.
2. **Jenkins executor 부족** — provision.sh 가 Job 재생성 시 1차 build (parameter 등록용 빈 build) 가 default 파라미터로 cascade → 02/03/04 큐 점유. 동시에 2차 buildWithParameters 가 큐 추가 → executor 2개 한도로 03 가 "Still waiting to schedule task" 14분 대기.
3. **사용자 발견** — "정적분석 전혀 돌고있지 않다" — 03 가 시작도 못 한 상태로 장시간 BUILDING 표시.
4. 1차 빌드 강제 abort 후 클린 상태에서 재 trigger → 2차 빌드 정상 완료 (04 #1 SUCCESS, 7.3분).

#### 현 상태

- KB chunks=60, endpoints=17, parser_failed_files 사이드카 정상
- Phase E' 코드 (a+b+c) 가 측정으로 검증됨
- citation/citation_depth 회복했으나 ts_any_hit 미회복
- 다음 처방 카드는 §4.5 의 (i)/(ii)/(iii) 옵션

---

### 3.8 사이클 3+F (프롬프트-creator 정합성 정렬) — 2026-04-25 심야

> 측정-주도 사이클이 아닌 **design alignment cleanup**. 시스템 프롬프트와 다운스트림 (`gitlab_issue_creator.py`) 사이의 결합도/dead field 정합성을 손봄. 다음 빌드 측정으로 회귀 부재 확인 필요.

#### 진단 — 8개 LLM 출력 필드 vs creator 소비 매트릭스

프롬프트 객관 평가 후 발견:

| LLM 필드 | creator 소비 | 부합 |
|---------|--------------|------|
| `title` / `labels` / `impact_analysis_markdown` | 본문 / `_merge_labels` / "⚠️" 섹션 | ✓ |
| `classification` | **Sonar `do_transition` 호출** + label | ✓ (강한 액션) |
| `confidence` | label + `_action_verdict` | ✓ |
| `suggested_fix_markdown` | "🛠️" 섹션 (코드펜스 자동 wrap) | ✓ |
| `suggested_diff` | **별도** "기계 적용 가능한 diff:" 블록 | △ 중복 위험 |
| `fp_reason` | **소비 0건 (dead field)** | ✗ |

추가로 system 프롬프트 자체의 노이즈:

- 내부 코드네임 (`Fix D`, `Phase B F2c`, `Phase E'`) 이 LLM 입력에 그대로 노출 — 모델 입장에선 의미 없는 토큰
- classification 분류 정의가 한 줄씩만 — 4B 모델이 신호 부족으로 medium 에 몰릴 가능성
- `suggested_fix_markdown` vs `suggested_diff` 는 둘 다 "수정안" 인데 분리 기준이 LLM 자가판단
- 길이 가드레일 약함 (`impact ≤ 6줄` 권장만, 코드펜스 길이 무한)

#### 처방 3개

| Fix | 변경 위치 | 의도 |
|-----|----------|------|
| **(a) 프롬프트 정리** | `sonar-analyzer-workflow.yaml` LLM 노드 system text | 코드네임 제거 / classification 신호 명시 (test 경로·생성 코드·deprecated 등) / `suggested_fix`·`suggested_diff` 케이스 A/B/C 상호배타 / 길이 가드레일 (`impact ≤ 6줄`, `fence ≤ 30줄`) / confidence 기준 구체화 |
| **(b) `fp_reason` dead field 부활** | `gitlab_issue_creator.py` `_sonar_add_comment()` 신규 + false_positive 분기 | LLM 의 오탐 판정 근거를 Sonar UI 코멘트로 부착 (전이 직전 호출, 실패해도 전이는 진행 — dual-path 원칙 유지) |
| **(c) 본문 중복 하드 차단** | `gitlab_issue_creator.py` `render_issue_body()` fix_blocks 직전 정규화 | `suggested_diff` 가 채워지면 `suggested_fix_markdown` 의 코드펜스 블록을 regex strip → LLM 위반 여부와 무관하게 본문 중복 0% (deterministic enforcement) |

#### 코드 변경

- `scripts/dify-assets/sonar-analyzer-workflow.yaml`:
  - LLM 노드 system 프롬프트 정리. 행위 지시 보존, 운영 메모 (Fix D / Phase B F2c / Phase E') 제거. 시스템 길이 약 -8%
- `pipeline-scripts/gitlab_issue_creator.py`:
  - `_sonar_add_comment()` 신규 — `POST /api/issues/add_comment` (Basic auth, token:empty)
  - false_positive 분기에서 `fp_reason` 비지 않으면 `[Auto-FP by LLM · confidence=X]\n<reason>` 코멘트 등록 후 `_sonar_mark_fp()` 호출. 코멘트 실패는 silent warn
  - `render_issue_body()` fix_blocks 직전: `diff_present and suggested_fix` 일 때 `re.sub(r"\`\`\`[\s\S]*?\`\`\`", "", suggested_fix).strip()` — 코드펜스만 제거, 자연어 보존
  - `null` / `none` 문자열 sentinel 처리를 변수 `diff_present` 로 추출해 일관화

#### 검증 (단위 테스트 — 빌드 측정 전)

- YAML 파싱 OK / 15개 placeholder (`{{#start.*#}}`, `{{#context_filter.filtered_context#}}`) 보존
- 프롬프트 내부 코드네임 3종 모두 제거 확인 (`Fix D` / `Phase B F2c` / `Phase E'` → 0건)
- `_sonar_add_comment()` silent skip on empty inputs 확인 (token / text 빈값)
- 정규화 단위 테스트 4 케이스 모두 통과:
  - Case A 위반 (fix 에 코드펜스 + diff 채움) → 코드펜스 strip / 자연어 보존
  - Case B (diff 빈값) → fix 무변경
  - Case C (둘 다 빈값) → 정상
  - `diff="null"` sentinel → false-trigger 없음
- `python3 -m py_compile gitlab_issue_creator.py` OK

#### 가설 (다음 빌드 측정 대상)

| H | 가설 | 측정 방법 |
|---|------|----------|
| H8 | classification 신호 명시 → false_positive 정밀도 향상 (medium 쏠림 감소) | `confidence` 분포 + Sonar `do_transition` 성공률 |
| H9 | `fp_reason` 코멘트 부착 → 분석가 검토 시간 단축 (질적) | Sonar UI 샘플 1건 확인, 분석가 피드백 |
| H10 | 본문 중복 차단 → "🛠️" 섹션과 diff 블록의 코드 중복 0% | GitLab Issue 5건 샘플로 grep 비교 |
| H11 | 시스템 프롬프트 -8% 토큰 → LLM attention 회복으로 citation 안정성 유지 | citation rate / depth — 3+E' 의 80% / 4.30 유지 가능성 |

#### 운영 적용 체크리스트

1. `gitlab_issue_creator.py` 변경 — `${SCRIPTS_DIR}` 가 컨테이너 내부 경로이므로 provision 재실행 또는 마운트 갱신 필요
2. workflow YAML — `provision.sh` 재실행 또는 Dify console 에서 워크플로우 재 import
3. 다음 빌드 후 Sonar UI 에서 false_positive 마킹된 이슈 1건 샘플로 LLM 코멘트 가시 확인
4. GitLab Issue 본문에서 "🛠️" + "**기계 적용 가능한 diff:**" 의 코드 중복 사라짐 확인

#### 현 상태

- 프롬프트와 creator 의 8개 필드 정합성 7/8 → **8/8** (`fp_reason` dead → live)
- 본문 중복 가능성 (LLM 위반 시) → **deterministic 차단**
- 측정은 다음 빌드 대기. citation / ts_any_hit 회귀 부재가 핵심 검증 포인트.

---

### 3.9 사이클 4 (PM 친화 리포트 v3 + 인프라 정비) — 2026-04-25 ~ 26 새벽

> 사용자 요구의 큰 전환 — "리포트가 무엇을 의미하는지 모르겠다 / 분석대상 프로젝트 구조·구현목표·코드간 연관관계·기타 코드정보가 보고 싶다". 02 사전학습 리포트의 청중을 개발자 → **PM** 으로 명시 재정의.

#### 사용자 입력 트리거 (한 줄 요약)

- "사전학습 리포트가 의미를 알기 어렵다 — 분석대상 프로젝트 구조 / 구현목표 / 코드간 연관관계 / 기타 코드정보가 보고 싶다"
- "PM 대상 리포트로서 효과를 발휘할 수 있는 정보가 담기길 바란다"
- "지식화가 끝날때까지 임의로 타이머를 빌드가 멈춰지는 일이 없도록 젠킨스 파이프라인을 수정해"

#### 처방 4가지 (병행)

| Fix | 변경 위치 | 의도 |
|-----|----------|------|
| **(a) PM 친화 리포트 v3** | `repo_context_builder.write_html_report` 전면 재설계 + A1~A6 신규 추출 함수 + B1 wrapper | 청킹 디버깅 통계 → "AI 가 우리 프로젝트를 어떻게 이해했는가" 종합 narrative |
| **(b) 무한 indexing wait** | `dify_kb_wait_indexed.py` `--timeout 0` 지원 + 02 jenkinsfile 적용 | realworld 387 청크 indexing 이 30분 hard cap 초과 시 자동 abort 되어 KB partial 상태 잔존하는 사고 차단 |
| **(c) sonar.java.binaries fix** | `sample-projects/realworld/sonar-project.properties` + GitLab repo 의 `build/empty-classes/.gitkeep` | SonarQube 26.x Java 룰이 binary 디렉토리 필수 요구 — source-only 분석을 위해 빈 디렉토리 trick 사용 (build step 추가 회피) |
| **(d) `--report-only` 모드** | `repo_context_builder.py main()` + `load_chunks_from_jsonl_dir()` 신규 | KB 재학습 (40분) 없이 기존 JSONL 에서 리포트만 다시 그리기 — v3 디자인 검증 시간 절약 |

#### 신규 함수 (모두 LLM/네트워크 호출 0, build 시간 영향 ≈ 0)

| 함수 | 데이터 출처 | 목적 |
|------|-----------|------|
| `extract_project_overview()` | README + LICENSE + build.gradle/pom.xml/package.json | TL;DR 카드의 "프로젝트가 뭐 하는지" 한 줄 요약 |
| `extract_dependencies()` | build.gradle / pom.xml / package.json | 5 카테고리 (framework / db / auth / test / util) 자동 분류 |
| `extract_domain_entities()` | 청크 메타 (kind ∈ {class, type, ...} + 이름 패턴 + path 힌트) | 도메인 객체 top 10 자동 식별 |
| `extract_class_inheritance()` | tree-sitter Java AST (extends / implements) | 클래스 상속/구현 표 |
| `compute_test_coverage_map()` | `test_for` 역인덱스 + is_public 휴리스틱 | "테스트 없는 public 함수 top 10" — PM 액션 직결 위험 신호 |
| `compute_overall_verdict()` | A1~A5 + 기존 stats | rule-based 0~100 scoring → 🟢/🟡/🔴 verdict + reason_lines |
| `suggest_pm_actions()` | verdict + coverage + endpoints 조합 | rule-based 액션 추천 3개 |
| `load_chunks_from_jsonl_dir()` | 기존 JSONL 디렉토리 | --report-only 모드용 재구성 |
| `render_kb_intelligence_section()` (B1) | `_kb_intelligence.json` 사이드카 | 04 의 4-stage 학습진단 narrative 를 02 리포트에서 재사용 |

#### 리포트 v3 — 8 섹션 (sticky nav + verdict color-coding)

```text
§1 🎯 한눈에     — TL;DR 카드 (파일·함수·API·테스트커버·AI신뢰도) + 종합 verdict
§2 📖 프로젝트   — README description + 의존성 5카테고리
§3 🏗 코드 구조  — 디렉토리 트리 + 패키지 분포 + 도메인 엔티티
§4 🔗 연관관계   — HTTP 진입점 + caller hub + callees hub + 클래스 상속
§5 🧪 테스트     — coverage % + 테스트 없는 public 함수 top 10
§6 📚 학습 진단  — 4-stage (scope/depth/quality/impact) — B1 wrapper
§7 🤖 결론·액션  — 종합 verdict + 점수 산출 근거 + PM 추천 액션 3개
§9 🔧 디버깅     — 기존 v1 통계 (접힘 — 개발자용)
```

#### 다른 정비 사항

- **샘플 프로젝트 단일화**: nodegoat + ttc-sample-app 제거 → `realworld` (Spring Boot RealWorld) vendoring. airgap 보장 (인터넷 fallback 비활성).
- **End-to-end 검증 흐름**: 컨테이너·이미지·데이터 모두 삭제 → offline 빌드 → run-mac.sh → provision (~7분) → 01 chain trigger 까지 완전 자동화 검증.
- **Dockerfile 재빌드 없는 코드 변경 적용 절차 확립**: `docker cp script.py ttc-allinone:/var/jenkins_home/scripts/` hot-reload 패턴.
- **Jenkins Job config patch 절차 확립**: jenkinsfile 변경이 살아있는 Job config 에 자동 반영 안 됨을 확인 → Job config XML 의 inline pipeline script 를 직접 patch + `/manage/reload` 호출.

#### 검증 (build #2 ~ #6)

| build | 목적 | 결과 |
|-------|------|------|
| 02 #2 | KB 학습 (387 청크 indexing) | ✅ SUCCESS 40분 (--timeout 0 의 가치 입증 — 1800s hard cap 이었으면 fail) |
| 03 #2 | 첫 sonar 분석 (sonar.java.binaries=src/main/java) | ❌ FAIL 5초 — Sonar 가 source dir 을 binary 로 받지 X |
| 03 #3 | 빈 디렉토리 trick 적용 후 | ✅ SUCCESS 16.6초 — analysis report uploaded |
| 04 #2 | 첫 end-to-end (10 GitLab Issues 생성) | ✅ created=10 / failed=0 |
| (사이클 5 로 이어짐) | | |

#### 현 상태

- 02 publishHTML 에 v3 리포트 자동 노출 (다음 build 부터)
- KB 358 docs 정상 잔존 (재학습 불필요)
- 03/04 모두 정상 SUCCESS — Java 분석 + LLM 답변 + GitLab Issue 등록 흐름 완전
- 다음 사이클 처방: rule 본문 한글화 + impact 분량 강화 (사이클 5)

---

### 3.10 사이클 5 (Rule 한글 번역 + impact 본문 분량 강화) — 2026-04-26 새벽

> 사용자 검토 후 두 가지 명확한 요청: "Rule 설명을 한글로 번역", "전체적인 이슈 설명을 보다 충실하게". 영문 원문은 SonarQube 링크에서 확인 가능하므로 GitLab Issue 본문엔 한글만.

#### 사용자 입력 트리거

- "룰에 대한 설명을 한글로 번역하는게 좋겠다"
- "전체적인 이슈에 대한 설명을 현재 작성분량보다 충실하게 작성되길 바란다"
- "영문은 숨겨도 된다. 이미 소나큐브 링크가 있으니까"
- "분량은 필수내용을 모두 포함해야 한다는 전제하에 RAG에 기반한 10개 이상의 문장"

#### 처방 2가지

| Fix | 변경 위치 | 의도 |
|-----|----------|------|
| **(a) Rule 한글 번역** | `gitlab_issue_creator.py` `_translate_rule_to_korean()` 신규 + Ollama gemma4 + rule_key dict 캐시 | 영문 rule 설명을 한국어로 번역해 GitLab 본문에 노출. 같은 룰 N회 → 1회만 호출 (캐시) |
| **(b) impact 본문 분량 강화** | `sonar-analyzer-workflow.yaml` system prompt | 3~6줄 → **최소 10 문장 4-구조 의무** (① 문제 본질 ② 발생 맥락 ③ 영향 범위 ④ 권장 방향), 모든 내용 RAG 컨텍스트 근거, 인용/메타/원인분석 의무 모두 강화 |

#### 운영 적용 절차 (Job config + Dify workflow 모두 갱신)

1. `gitlab_issue_creator.py` hot-reload (`docker cp`)
2. 04 jenkinsfile 의 `--ollama-base-url` / `--ollama-model` 인자 추가 → Job config XML 직접 patch (살아있는 Job 에 반영) + `/manage/reload`
3. Dify workflow YAML 재 import (기존 App DELETE → fresh import → publish + Content-Type/body 필수 → API key 재발급)
4. Jenkins credential `dify-workflow-key` 갱신 (새 API key)

#### 운영 발견 — 2가지 함정

- **Dify workflow publish 호출은 `Content-Type: application/json` + 빈 body `{}` 필수**. 빠뜨리면 silent fail → 04 build 가 "Workflow not published" 400 에러로 모든 이슈 retry 3회 후 실패.
- **Jenkins Job config 의 inline pipeline script 는 jenkinsfile 변경과 독립**. provision 시점에 jenkinsfile 내용을 박아두므로 코드 수정 후 Job config XML 도 patch 해야 함. (다음 provision 부터는 자동 반영)

#### 검증 (build #4 ~ #6)

- build #4: dedup (이전 #2 의 결과와 sonar_key 동일) → created=0, skipped=10. **`llm_analysis.jsonl` artifact 에서 새 prompt 효과 직접 확인** — title 한글 ("유틸리티 클래스에 기본 생성자 노출 문제"), impact 14문장 / 1197자 / 4-구조 명시 / RAG 인용 6+ 개.
- build #5: GitLab issue 10건 삭제 후 재 trigger → 새 issue 본문에서 impact 4-구조 + 14문장 모두 확인. 단 Rule 영문 그대로 (Job config 미patch — 다음 step 에서 fix).
- build #6: Job config patch + Jenkins reload 후 → **Rule 한글 번역 본문 등장**: "[근본 원인] Double Brace Initialization (DBI)은 소유 객체에 대한 참조를 가진 익명 클래스를 생성하기 때문에..." (518자, 코드블록 보존, "비준수 코드 예제"/"준수 솔루션" SonarQube 표준 용어 유지).

#### 현 상태

- Rule 설명 한글 번역 ✓ (rule_key 캐시로 효율적)
- impact 본문 4-구조 + 10+ 문장 + RAG 인용 6+ 모두 ✓
- 다음 사이클 처방: 가독성 — 한 단락에 여러 문장이 줄바꿈 없이 뭉치는 문제

---

### 3.11 사이클 5+α (가독성 강화 — soft line break + 쉬운 표현) — 2026-04-26 새벽

> 사용자 검토 후 두 가지 추가 요청. 짧은 사이클 — system prompt 만 수정.

#### 사용자 입력 트리거

- "문장 하나를 작성하면 줄바꿈처리를 하자. 화면상에서 분석내용을 읽기가 힘들다."
- "내용또한 보다 쉽게 작업자가 이해할 수 있도록 보다 쉽고 구체적으로 지시하도록 프롬프트를 작성할 필요가 있겠어."

#### 처방

`sonar-analyzer-workflow.yaml` system prompt 의 `impact_analysis_markdown` 가이드에 두 블록 추가:

**(1) 줄바꿈 규칙**
- 모든 문장 끝 두 공백 + 줄바꿈 (`...다.  \n`) — GitHub/GitLab markdown soft line break
- 4-구조 bold heading 사이엔 빈 줄 1개 (paragraph break)
- 한 문장 = 한 주장 = 한 줄. 합쳐쓰지 말 것
- 구체 예시 포함 (✓ good / ✗ bad 대조)

**(2) 쉽게 쓰기 가이드 (작업자 친화)**
- 추상어 ("잠재적", "구조적") 보다 구체어 우선. 예: "잠재적 메모리 누수" (bad) → "100번 호출되면 약 1MB 누적" (good)
- 전문 용어 사용 시 한 줄 한국어 풀이 동반. 예: "idempotent (= 두 번 호출하면 두 번 실행되어 중복)"
- 코드 동작 예시 인라인. "이렇게 호출되면 이렇게 됨" 패턴
- 어려운 개념 비유 OK
- 추상명사 나열 금지. 주어-동사 분명한 짧은 평서문

#### 검증 (build #8 — MAX_ISSUES=1 빠른 1건 검증)

- impact_analysis_markdown 33줄 / 11 soft-break / 16 빈 줄 — 각 문장 독립 줄 분리 ✓
- 4-구조 사이 빈 줄로 paragraph break (`**문제의 본질**` ↔ 빈 줄 ↔ 본문 ↔ 빈 줄 ↔ `**발생 맥락**` 순서 정확) ✓
- 구체적 표현: "정적(static) 헬퍼", "`new Util()` 같이 인스턴스 생성", "메모리에 불필요하게 남아" ✓
- RAG 인용 6+ 개: `Util`, `isEmpty(String value)`, `Article.java`, `User.java`, `AuthorizationService.java`, `SecurityUtil.java` ✓

#### 현 상태

- GitLab Issue 화면에서 본문 가독성 정상화 — 한 줄에 여러 문장 뭉치는 문제 해소
- 표현 수준 PM/주니어 개발자 친화로 전환
- 사이클 4~5+α 의 누적 효과: PM 이 02 리포트 (5분 종합 의사결정) + 04 GitLab Issue (각 이슈 5분 이해) 모두 자력으로 활용 가능한 상태 도달

---

### 3.12 사이클 6 (04 빌드 결과 리포트 v2 — "정적분석 결과리포트") — 2026-04-26 새벽

> 사용자 요구의 두 번째 큰 전환 — 04 의 publishHTML 리포트 ("RAG Diagnostic Report") 가 PM 비친화 (개발자/AI엔지니어 용어 위주). PM 이 04 build 결과 한 화면에서 (a) 트리아지 (b) 할당 (c) 신뢰도 의사결정 가능한 대시보드로 재설계.

#### 사용자 입력 트리거

- "04 빌드화면에서의 결과 리포트 — 냉정하게 현재의 4번째 빌드 파이프라인에 대한 결과리포트가 요구사항을 명확히 반영한다고 판단하나?"
- "PM관점에서 이것 외에 추가되어야할 내용이 뭐가 있을지 보다 진지하게 탐구해보자"
- "현학적으로 작성했다. 이해가 힘들다." → 평이한 말로 재설계
- "지금 보이고자 하는 지표는 모두 기존 데이터에서 가져올 수 있는건가?"
- "구현하자"

#### 진단 — 기존 RAG Diagnostic Report 의 6 가지 구조적 문제

1. 이름 자체가 PM 비친화 ("RAG Diagnostic" — 클릭조차 안 함)
2. 빌드 결과 funnel (Sonar→AI→GitLab 카운트) 부재
3. GitLab Issue 직접 링크 부재
4. 02 와 4-stage 진단 100% 중복
5. 첫 카드 우선순위 잘못됨 ("실무 권장 액션" 이 결과 funnel 보다 위)
6. 개발자 진단 메트릭 (citation rate / bucket fill) 이 PM 흐름 위에 군림

#### 처방 — 6 섹션 PM 대시보드

```text
[이름] "RAG Diagnostic Report" → "정적분석 결과리포트"

§1 🎯 한눈에 (TL;DR)        — 빌드 메타 + funnel + GitLab 큰 버튼 + verdict
§2 🚨 즉시 조치 권장          — 위험도 룰 자동 추출 (severity ≥ MAJOR + 호출자 ≥ 5
                                + classification=true_positive + confidence ≥ medium)
§3 📋 모듈별 GitLab Issue 표  — path 그룹화 + iid 직접 링크 (`/-/issues/{iid}`)
§4 📊 결함 종류 분포           — severity / type / 처리결과 막대그래프
§5 🔬 AI 답변 신뢰 신호        — confidence 분포 + partial / fp 카운트
§6 (접힘) 🔧 개발자 진단      — 기존 RAG diagnostic 모든 콘텐츠 재사용
```

#### 데이터 출처 (모두 기존 artifact + 한 곳 patch)

| 지표 | 출처 |
|------|------|
| 빌드 메타 (build / commit / branch) | Jenkins env + `git log -1` |
| Sonar 발견 카운트 | `sonar_issues.json["issues"]` |
| AI 분석 카운트 | `llm_analysis.jsonl` line 수 |
| GitLab 등록 카운트 / iid | `gitlab_issues_created.json` (iid 추가 필요 — patch 적용) |
| 위험도 (callers ≥ 5) | llm_analysis row 의 `direct_callers` field (이미 존재) |
| classification + confidence | `outputs` |
| severity / type / tags | `issue_search_item.type` + `tags` |
| partial_citation / fp 카운트 | `outputs.labels` + `gitlab_issues_created.fp_transitioned` |

#### 코드 변경

- `pipeline-scripts/diagnostic_report_builder.py`:
  - 신규 helper 8개 (load / fetch / compute)
  - 신규 render 6개 (§1~§6)
  - 신규 `_CSS_PM` (sticky nav + funnel + cards + verdict color-coding)
  - `render_pm_report()` — 새 6 섹션 통합 렌더
  - `main()` 재작성 — 신규 CLI 인자 8개 (`--gitlab-output` / `--sonar-issues` /
    `--build-number` / `--commit-sha` / `--repo-root` / `--branch` /
    `--gitlab-public-url` / `--gitlab-project` / `--legacy`)
  - 기존 `render()` 유지 (`--legacy` 플래그로 호출 가능)

- `pipeline-scripts/gitlab_issue_creator.py`:
  - created entry 에 `iid` 추가 (3줄 patch) — POST 응답 body 의 json 에서 추출

- `jenkinsfiles/04`:
  - publishHTML 호출에 신규 인자 8개 전달
  - reportName 변경: "RAG Diagnostic Report" → "정적분석 결과리포트"
  - `sh '''` → `sh """` + shell 변수 escape (`\${BUILD_NUMBER}`, `\${REPO_NAME}`, `\${SCRIPTS_DIR}`)

#### 운영 발견 — 함정 2개

- **`sh '''` (single quote) + `${env.X}` 함정**: Job config XML 의 sh 블록이
  `'''` (single quote) 면 groovy interpolation 안 일어남. dash sh 가 `${env.COMMIT_SHA}`
  같은 변수명을 invalid (`.` 포함) 로 봐 "Bad substitution" 에러 발생 → build #1
  실패. fix: `sh """` 로 변경 + groovy interpolate 대상 제외하고 shell 변수만 `\${...}` escape.

- **정규식 patch 의 greedy 매칭 위험**: Job config XML 의 `sh '''...'''` 블록을
  정규식으로 patch 시 greedy 매칭으로 04 Job config 손상 → Job 자체가 사라짐.
  복구: jenkinsfile 에서 직접 새 Job 등록 (provision.sh 의 `jenkins_create_pipeline_job`
  로직 단독 실행). 교훈: config XML patch 전 백업 또는 dry-run 필수.

#### 검증 (build #2 → #3, MAX_ISSUES=1, 2~7분)

- 6 섹션 anchor 모두 생성 (sec-1 ~ sec-6)
- §1 funnel: Sonar 43 → AI 1 → GitLab 1 정확 표시
- §2 위험 카드 1건 자동 추출 (Util.java MAJOR + 호출자 ≥5 + AI 신뢰 high)
- §3 iid 직접 링크 작동 (`/-/issues/34`)
- §5 신뢰 신호 5 카운트 카드 (high=1, 나머지=0)
- Rule 한글 번역 fallback 작동 (S1118 timeout → 원문 유지)

#### 현 상태

- 04 의 publishHTML 탭 리포트가 PM 자력 활용 가능한 대시보드로 전환
- "PM 5분 의사결정 (트리아지·할당·신뢰도)" 모두 한 화면에서 가능
- 사이클 1~6 누적 누적 효과:
  - 02 사전학습 리포트 (사이클 4) — "AI 가 우리 프로젝트를 어떻게 이해했는가"
  - GitLab Issue 본문 (사이클 5+α) — 한글 + 4-구조 + 가독성
  - 04 publishHTML 리포트 (사이클 6) — "이번 빌드가 무엇을 만들었나" 한 화면
  → 3 청중 (PM/개발자/임원) 모두 자력 활용 가능

---

## 4. 미해결 이슈 / 향후 검토

### 4.1 RAG retrieval 단계의 누수 (사이클 3+E' 측정으로 새로 명확해진 핵심 갭)

**증상**:
- 자동 진단 표 (사이클 3+E' 빌드의 §3.7) — B 카테고리 (callers=0) **80%**, C (tests=0) **100%**
- callers bucket fill **20%**, tests bucket fill **0%**
- 그러나 KB 자체는 callees 매핑률 95% / endpoints 17 / callers links 39 로 **풍부함**

**의미**: KB 학습 단계 (Stage 2) 에서 잘 갖춰진 caller/test 정보가 **이슈 분석 시점 RAG 검색에서 회수되지 않음**. 학습 ↔ 검색 사이의 비대칭이 04 의 잔존 누수 중 가장 큰 것.

**원인 후보 (가설)**:
1. **score_threshold (0.25) 가 caller 청크에 너무 높음** — caller 코드 (예: route handler) 와 이슈 함수 (예: DAO method) 의 임베딩 거리가 멀다
2. **kb_query 가 caller 의미 표현 약함** — 현재 `callers: <enclosing>` 라인을 추가하지만 이게 dense retrieval 에 약함 (이전 P1 사이클에서도 관찰)
3. **self-exclusion 이 너무 공격적** — 같은 파일의 sibling 함수까지 가끔 제외되어 kept_total 작아짐
4. **test 청크와 본문 코드의 임베딩 거리가 큼** — Cypress test 스타일 vs DAO 메서드 스타일 (이전 사이클 P4 도 같은 진단)

**처방 후보**:
- (i-1) score_threshold 0.25 → 0.15 완화
- (i-2) kb_query 의 `callers: X` 라인을 자연어 ("이 함수 X 를 호출하는 controller / handler / 라우터") 로 강화
- (i-3) test 청크 footer 에 동의어 강제 (이전 D3 와 같은 방향, 확장)
- (i-4) self-exclusion 을 path 단위 → (path, line range) 정밀화 (이미 P5 적용 — 추가로 line range 너비 정책 검토)

→ **다음 사이클의 1순위 처방 대상**.

### 4.2 LLM 의 정적 메타 인용 한계 (4B 모델의 본질적 제약)

**증상** (사이클 3+E' 측정):
- HTTP route 인용 이슈: **0/10**
- decorator 인용 이슈: 0/10 (JS 라 정상)
- 매개변수명 인용 이슈: **0/10**
- RAG 청크 메타 인용 이슈: 2/10

**의미**: enclosing 함수의 정적 메타 (endpoint=`POST /benefits` 등) 가 LLM 프롬프트에 정확히 들어가도 답변 본문에 **단 한 번도 인용되지 않음**. RAG 청크의 meta 인용만 미미하게 (2/10) 흘러감.

**가설**:
- gemma4:e4b (4B thinking 모델) 가 system prompt 의 정적 메타 활용 가이드를 약하게 인식
- thinking 모델 특성상 reasoning 토큰을 많이 쓰고 답변 본문이 짧아져 메타 인용이 자연스레 누락
- 4B 모델의 attention bandwidth 가 프롬프트 길이 증가 시 무너짐 (Phase E 회귀에서 이미 관찰)

**처방 후보**:
- (ii-1) **모델 업그레이드** — Apple Silicon 32GB 환경이면 qwen2.5-coder:14b 또는 deepseek-coder-v2:16b-lite (MoE, active 2.4B 라 빠름) 시도
- (ii-2) NVIDIA GPU VRAM 8GB 환경이면 qwen2.5-coder:7b / phi-4-mini:3.8b / qwen3 7B
- (ii-3) **system prompt 의 메타 인용 강제** — "endpoint 가 있으면 본문에 1번 의무 언급" 룰 추가. 단 사이클 3+E 에서 LLM 의무 룰 채택률 0/10 이었으므로 deterministic 후처리 가 더 안정 가능성
- (ii-4) **자동 후처리 확장** — analyzer 가 답변에 endpoint 가 있는지 검사 후, 없으면 "이 함수는 `POST /login` 라우트입니다" 같은 자동 prefix 부착. 단 LLM 답변과 자연스럽게 합쳐지지 않을 위험

### 4.3 인프라 한계

| 이슈 | 원인 | 처방 후보 |
|------|------|----------|
| Jenkins executor 2개 한도 | 기본 설정 | jenkins-init Groovy 에서 numExecutors 4~6 으로 증가 |
| provision.sh Job 재생성 시 1차 빈 build cascade | parameter 등록 회로 | "is not parameterized" 첫 호출도 정상 실행되도록 Jenkinsfile 의 parameters 블록 정리 |
| dify-api workers=1 | 단일 worker | gunicorn workers 2~3 + worker-connections 적절 조정 |
| Dify SQLAlchemy hit_count bug 산발 재발 | 1.13.3 의 알려진 bug | 이미 빌드 시 patch 적용 (build-mac.sh patches/) 됐으나 산발 재발 — dify-api restart 로 회복. 운영상 자동 회복 cron 또는 health-check 후 자동 재시작 검토 |

### 4.4 04 의 의도 대비 잔존 갭

| Gap | 의도 (§2) | 사이클 3+E' 후 상태 |
|-----|-----------|-------------------|
| G1 "전체 저장소" | 함수 dependency graph + 모든 호출 관계 | callers retrieval 누수 (§4.1) — KB 는 풍부한데 검색 단계에서 못 꺼냄 |
| G2 "프로젝트 정보" | README + CONTRIBUTING + package.json | E2-lite 로 LLM 프롬프트에 첨부됨 — 단 LLM 답변 본문 인용 미관찰 |
| G3 "실질적 원인분석" | "왜 이렇게 작성됐는가" + invariant | git history (E3) 첨부됐으나 답변 인용 거의 없음 — 모델 한계 (§4.2) |
| G4 "수정 조치 방향" | convention-aware + impact 분석 | similar_locations (E5) 첨부됐으나 패턴 언급 답변 거의 없음 — 모델 한계 |

### 4.5 다음 사이클 처방 카드 (우선순위 정리)

| 옵션 | 설명 | 예상 효과 | 비용 |
|------|------|----------|------|
| **(i) RAG retrieval 개선** | §4.1 — score_threshold 완화, kb_query 자연어 강화, test 청크 동의어 footer | callers fill 20%→50%+, ts_any_hit 일부 회복 | 작음 (workflow YAML + analyzer) |
| **(ii) 모델 업그레이드** | §4.2 — qwen2.5-coder:14b / deepseek-coder-v2:16b-lite | ts_any_hit 30%+ 가능, 답변 품질 큰 도약 | 중간 (호스트 모델 추가 + provider 재등록 + 추론 시간 ↑) |
| **(iii) Phase E 부분 rollback** | E1/E3/E5 (활용도 낮음) 제거. E4 자동표기 + E2-lite + Phase D 까지 유지 | 단순화, citation/depth 안정 | 작음 (코드 revert) |
| **(iv) 인프라 안정화** | §4.3 — Jenkins executor / dify-api workers / 1차 빈 build 회로 | 운영 안정성 | 작음 |

**개인 의견** — 우선순위:
1. **(i) RAG retrieval 개선 먼저** — 비용 작고 측정 결과 (B 80%, C 100%) 가 명확한 누수를 가리킴.
2. (i) 후에도 ts_any_hit 못 올라가면 **(ii) 모델 업그레이드** 검토.
3. (iii) 은 (i)+(ii) 둘 다 효과 없을 때 마지막 카드.
4. (iv) 는 다른 작업과 병행 가능.

---

## 5. 측정 메트릭 누적 추적 (참고용)

| 사이클 | citation rate | citation_depth | ts_any_hit_pct | KB chunks | endpoints | E4 표기율 | partial_citation 강등 |
|--------|---------------|-----------------|-----------------|-----------|-----------|-----------|----------------------|
| 베이스라인 | 2.1% | — | — | — | — | — | — |
| Fix R + 4종 | 60% | — | — | — | — | — | — |
| P1+P3+P4 | 60% | — | — | — | — | — | — |
| P5+P7+T1+T2 | 65% | 3.10 | — | — | — | — | 2/10 |
| 사이클 1 | 65% | 3.70 | — | — | — | — | 1/10 |
| 사이클 2 | 75% | 4.20 | — | — | — | — | 0/10 |
| 사이클 3 | 70% | — | 0% | 27 | 0 | — | 0/10 |
| **사이클 3+D** | **80%** | 4.20 | **30%** | **60** | **17** | — | **0/10** |
| 사이클 3+E | 70% | (미상) | 20% | 60 | 17 | **0/10** | 0/10 |
| **사이클 3+E'** | **80%** | **4.30** | 20% | 60 | 17 | **10/10** | 1/10 |

🎯 누적 (사이클 3+E' 기준):
- citation rate **2.1% → 80%** (38배)
- citation_depth **3.10 → 4.30** (사상 최고)
- KB 청크 **0 → 60**
- HTTP API 진입점 **0 → 17**
- partial_citation 자동 강등 작동 (1건 강등으로 정직성 보존)
- 답변에 정적 메타 등장 비율 **0% → 20%** (3+D 의 30% 미회복 — §4.1 누수 미해소)
- per-issue enclosing_function 정확도 **부분 → 100%** (fn:err 버그 0)
- **E4 검토범위 자동 표기 0/10 → 10/10** (deterministic 후처리의 가치 입증)

🚧 잔존 갭 (사이클 3+E' 후):
- ts_any_hit_pct **20%** — 3+D 의 30% 못 회복 → §4.1 (RAG retrieval 누수) + §4.2 (LLM 모델 한계) 가 다음 사이클 대상
- callers bucket fill **20%** / tests bucket fill **0%** — KB 풍부함이 검색에 안 흐름
- HTTP route / decorator / 매개변수명 인용 **각 0/10** — 4B 모델의 정적 메타 인용 능력 한계

---

## 6. 04 파이프라인의 본질 — 회고

### 6.1 사이클 3+E' 측정 후 검증된 / 부분 검증된 / 깨진 가설

| # | 가설 | 결과 | 측정 근거 |
|---|------|------|----------|
| H1 | **데이터 정확도 fix > 데이터 풍부도 확장** | ✅ 검증됨 | Phase D (정확도) → ts_any_hit 0%→30%. Phase E (풍부도) → 30%→20% regress |
| H2 | **LLM 의 자발적 의무 룰은 신뢰 불가, deterministic 후처리가 안정** | ✅ 검증됨 | E4 LLM 의무 채택률 0/10 → analyzer 자동 부착으로 10/10 |
| H3 | **빈 섹션 헤더 noise 제거가 LLM attention 회복** | ✅ 부분 검증 | citation 70%→80% 회복 + citation_depth 4.3 (사상 최고) |
| H4 | **system prompt 압축이 LLM attention 회복** | ✅ 부분 검증 | (H3 와 같은 결과 — 분리 측정 어려움) |
| H5 | **더 많은 입력 → 더 좋은 답변** | ❌ 깨짐 | Phase E 의 4 신규 입력 → ts_any_hit regress |
| H6 | **(a+b+c) 처방으로 Phase D 수준 회복** | 🟡 부분 | citation 회복 ✓ / ts_any_hit 미회복 ✗ |
| H7 | **KB 학습 풍부도 → 답변 활용도** | ❌ 깨짐 | 새 발견. callees 매핑 95% 인데 callers fill 20%. 학습 ↔ 검색 사이 누수 |

### 6.2 사이클 3+E' 가 새로 던진 질문

**Q1**: "callees 매핑 95% / endpoints 17 같은 풍부한 KB 가 있는데 왜 RAG retrieval 이 caller 청크를 못 꺼내나?"
- 가설: score_threshold 0.25 가 caller-callee 임베딩 거리에 너무 빡빡 / kb_query 의 caller 신호가 dense 매칭 약함 / self-exclusion 정책 영향
- 검증 방법: §4.5 의 (i) RAG retrieval 개선 후 callers fill 변화 측정

**Q2**: "LLM 프롬프트에 endpoint 가 분명히 들어갔는데 왜 답변 본문에 0번 등장하나?"
- 가설: 4B thinking 모델 (gemma4:e4b) 이 reasoning 토큰을 많이 써 답변 본문이 짧아짐 / system prompt 의 메타 인용 가이드 약하게 인식
- 검증 방법: §4.5 의 (ii) 모델 업그레이드 후 ts_any_hit 변화 측정. 또는 답변 본문에 자동 prefix 부착 (deterministic) 실험

### 6.3 04 의 정체성 재정의 (3 사이클 누적)

- 04 는 "AI 가 더 정확한 판단" 을 하는 곳이 아니라 "**AI 가 받은 정확한 데이터로 일관된 답변을 만드는**" 곳.
- 정직성 메트릭 (E4 검토범위 자동 표기, partial_citation 강등) 이 04 의 핵심 가치 — **사이클 3+E' 에서 E4 가 100% 작동하며 이 가치 확립**.
- 입력 데이터의 **정확도** 가 풍부도보다 우선 (H1).
- 그러나 정확도 / 풍부도 외에 **데이터 흐름 (KB 학습 → RAG 검색 → LLM 활용) 의 각 단계가 독립적 누수 지점** 임을 새로 인식 (H7).

### 6.4 다음 사이클 의 핵심 가설 (검증 대상)

1. **"RAG retrieval 임계치 + 쿼리 전략" 만 손봐도 ts_any_hit 30%+ 회복 가능** (§4.5 (i))
2. **(i) 후에도 부족하면 모델 업그레이드 (gemma4:e4b → qwen2.5-coder:14b 또는 deepseek-coder-v2:16b-lite) 가 결정적** (§4.5 (ii))
3. **Phase E 의 5 추가 입력 중 일부는 영구히 LLM 활용 안 됨** — 그 경우 (iii) 부분 rollback 으로 단순화 (§4.5 (iii))

이 3 가설을 다음 사이클에서 순서대로 검증하면 04 의 의도 (§1.1) 에 가까워질 가능성. 각 가설의 비용/효과는 §4.5 표 참조.

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

측정 결과 (검증 완료): citation 70%→80% 회복, citation_depth 4.3 (사상 최고), E4 자동 표기 0/10→10/10. 단 ts_any_hit 20% 미회복.

### 사이클 3+F (프롬프트-creator 정합성)

`scripts/dify-assets/sonar-analyzer-workflow.yaml` — LLM 노드 system 프롬프트 정리 (-8%)

- 내부 코드네임 제거 (Fix D / Phase B F2c / Phase E')
- classification 분류 신호 명시 (test 경로·생성 코드·deprecated 등)
- `suggested_fix_markdown`·`suggested_diff` 케이스 A/B/C 상호배타
- 길이 가드레일 (`impact ≤ 6줄`, `fence ≤ 30줄`)
- confidence 기준 구체화

`pipeline-scripts/gitlab_issue_creator.py`

- (b) `_sonar_add_comment()` 신규 + false_positive 분기에서 `fp_reason` 을 Sonar UI 코멘트로 부착 (전이 직전 호출, 실패해도 전이는 진행)
- (c) `render_issue_body()` fix_blocks 직전 정규화 — `suggested_diff` 가 채워지면 `suggested_fix_markdown` 의 코드펜스 regex strip (본문 중복 deterministic 차단)

검증 (단위 테스트): YAML 파싱 OK / 15개 placeholder 보존 / 코드네임 3종 제거 / 정규화 4 케이스 (A 위반·B·C·null sentinel) 모두 통과 / `py_compile` OK. **빌드 측정 대기** — H8~H11 가설 (§3.8) 확인 필요.

### 사이클 4 (PM 친화 리포트 v3 + 인프라 정비)

`pipeline-scripts/repo_context_builder.py`

- `extract_project_overview()` / `extract_dependencies()` / `extract_domain_entities()` /
  `extract_class_inheritance()` / `compute_test_coverage_map()` /
  `compute_overall_verdict()` / `suggest_pm_actions()` / `load_chunks_from_jsonl_dir()` 신규 (8개 함수)
- `write_html_report()` 전면 재설계 — 8 섹션 + sticky nav + verdict color-coding
- `main()` 에 `--report-only` 플래그 추가 (KB 재학습 없이 리포트만 갱신)

`pipeline-scripts/diagnostic_report_builder.py`

- `render_kb_intelligence_section()` thin wrapper 신규 — 04 의 4-stage 진단 narrative 를 02 사전학습 리포트에서 재사용 가능

`pipeline-scripts/dify_kb_wait_indexed.py`

- `--timeout 0` 무한 대기 지원 (큰 KB 의 indexing 시간 초과 보호)

`jenkinsfiles/02 코드 사전학습.jenkinsPipeline`

- KB-Wait 호출에 `--timeout 0` 적용

`sample-projects/realworld/sonar-project.properties`

- `sonar.java.binaries=build/empty-classes` (빈 디렉토리 trick — SonarQube 26.x source-only Java 분석)

`sample-projects/realworld/build/empty-classes/.gitkeep`

- 빈 디렉토리 vendoring (git 추적 강제)

검증 (build #2~6): 02 #2 SUCCESS 40분 (--timeout 0 입증) / 03 #3 SUCCESS 16.6초 (sonar fix) / 04 #2 created=10 정상 등록.

### 사이클 5 (Rule 한글 번역 + impact 본문 분량 강화)

`pipeline-scripts/gitlab_issue_creator.py`

- `_translate_rule_to_korean()` 신규 — Ollama gemma4 호출 + `_rule_translation_cache` dict (rule_key 캐시)
- 호출부 — `rule_full = "**Rule 전체 설명 (한글):**\n\n{translated}"` (영문 원문 노출 X)
- CLI 인자 신규 — `--ollama-base-url`, `--ollama-model`

`scripts/dify-assets/sonar-analyzer-workflow.yaml`

- system prompt 의 `impact_analysis_markdown` 가이드 전면 재작성:
  분량 3~6줄 → **최소 10 문장**, 4-구조 의무 (① 본질 ② 맥락 ③ 영향 ④ 방향),
  RAG 컨텍스트 근거 의무, 인용 2+ 개, 정적 메타 2번 이상, 원인분석 2곳 이상
- schema 라인 동기화 (`impact_analysis_markdown` 필드 description)

`jenkinsfiles/04 정적분석 결과분석 및 이슈등록.jenkinsPipeline`

- `gitlab_issue_creator.py` 호출에 `--ollama-base-url http://host.docker.internal:11434 --ollama-model gemma4:e4b` 인자 전달

검증 (build #4~6): impact 14문장 / 1197자 / RAG 인용 6+ / 4-구조 모두 명시. Rule 한글 518자 (코드블록 보존, "비준수 코드 예제"/"준수 솔루션" 표준 용어 유지).

### 사이클 5+α (가독성 강화 — soft line break + 쉬운 표현)

`scripts/dify-assets/sonar-analyzer-workflow.yaml`

- system prompt 의 `impact_analysis_markdown` 가이드에 두 블록 추가:
  - **줄바꿈 규칙**: 모든 문장 끝 두 공백 + 줄바꿈 (soft break) / 4-구조 사이 빈 줄
    (paragraph break) / 한 문장 = 한 주장 = 한 줄 / ✓ good ✗ bad 대조 예시
  - **쉽게 쓰기 가이드**: 추상어 → 구체어 / 전문 용어 한 줄 풀이 동반 /
    코드 동작 인라인 / 비유 OK / 추상명사 나열 금지

검증 (build #8 — MAX_ISSUES=1): 33줄 / 11 soft-break / 16 빈 줄. 4-구조 사이 paragraph break 정확. "정적(static) 헬퍼", "`new Util()` 같이 인스턴스 생성" 등 구체적 표현. RAG 인용 6+ 개.

### 사이클 6 (04 빌드 결과 리포트 v2 — "정적분석 결과리포트")

`pipeline-scripts/diagnostic_report_builder.py`

- 신규 helper 8개:
  - `_load_sonar_count` / `_load_gitlab_created` / `_fetch_build_meta`
  - `_compute_severity_distribution` / `_extract_high_risk` (위험도 룰 적용)
  - `_group_issues_by_module` / `_compute_trust_signals` / `_compute_overall_verdict_v2`
- 신규 render 6개: `_render_section_1_tldr` ~ `_render_section_6_dev_diag_collapsed`
- 신규 `_CSS_PM` (sticky nav + funnel + cards + verdict color-coding + 막대그래프)
- `render_pm_report()` — 새 6 섹션 통합 렌더
- `main()` 재작성 — 신규 CLI 인자 8개 (`--gitlab-output`, `--sonar-issues`,
  `--build-number`, `--commit-sha`, `--repo-root`, `--branch`,
  `--gitlab-public-url`, `--gitlab-project`, `--legacy`)
- 기존 `render()` 유지 (`--legacy` 플래그로 호출 가능)

`pipeline-scripts/gitlab_issue_creator.py`

- `created` entry 에 `iid` 추가 (3줄 patch) — POST 응답 body 에서 추출.
  §3 모듈별 표의 GitLab 직접 링크 (`/-/issues/{iid}`) 생성용.

`jenkinsfiles/04 정적분석 결과분석 및 이슈등록.jenkinsPipeline`

- post-action publishHTML 호출에 신규 인자 8개 전달
- `reportName: 'RAG Diagnostic Report'` → `reportName: '정적분석 결과리포트'`
- `sh '''` → `sh """` + shell 변수 escape (`\${BUILD_NUMBER}`, `\${REPO_NAME}`, `\${SCRIPTS_DIR}`)

검증 (build #2 → #3, MAX_ISSUES=1, 2~7분):

- 6 섹션 anchor 모두 생성 (sec-1 ~ sec-6) ✓
- §1 funnel: Sonar 43 → AI 1 → GitLab 1 ✓
- §2 위험 카드 1건 자동 추출 (Util.java MAJOR + 호출자 ≥5 + AI 신뢰 high) ✓
- §3 iid 직접 링크 작동 (`/-/issues/34`) ✓
- §5 신뢰 신호 5 카운트 카드 ✓
- Rule 한글 번역 fallback 작동 (S1118 timeout → 원문 유지) ✓

---

## 8. 운영 중 발생한 인프라 이슈 정리 (사이클 3 → 3+E' 누적)

진행 중 발생한 운영 이슈와 그 처방을 한 곳에 정리. 이 중 일부는 §4.3 의 향후 검토 대상.

### 8.1 Dify SQLAlchemy hit_count bug 산발 재발

- **증상**: 02 사전학습의 60청크 업로드 중 갑자기 `RemoteDisconnected` → 다음 retry 에서 `Read timed out (300s)`. dify-api 가 응답은 살아있으나 단일 worker 가 wedged.
- **원인**: Dify 1.13.3 의 알려진 SQLAlchemy session pool 버그. 빌드 시 `scripts/patches/dify_hit_count_bypass.py` 로 patch 적용 (build-mac.sh / Dockerfile) 됐으나 산발적으로 재발.
- **즉시 처방**: `docker exec ttc-allinone supervisorctl restart dify-api` — 30초 내 새 worker. 다음 retry 가 즉시 succeed.
- **항구 처방 (검토)**: gunicorn workers=1 → 2~3 으로 증가. health-check + auto-restart cron.

### 8.2 Jenkins executor 부족 (2개 한도)

- **증상**: 사이클 3+E' 1차 빌드에서 03 정적분석 #1 이 14분간 "Still waiting to schedule task" — 시작도 못 함. 사용자 지적: "정적분석 전혀 돌고있지 않다".
- **원인 트리거**: provision.sh 가 Workflow 재import 시 Jenkins Job 도 재생성 → 그 직후 trigger 가 "is not parameterized" 400 → 1차로 빈 build (parameter 등록용) 호출 → 그 빈 build 가 default 파라미터로 02→03→04 cascade → executor 점유.
- **부가 트리거**: 동시에 사용자가 2차 buildWithParameters 호출 → 01 chain #2 + 02 #2 큐 추가. executor 2개 한도라 03 #1 이 자리 못 받음.
- **즉시 처방**: `curl -X POST .../job/{N}/stop` 으로 모든 진행 빌드 + 큐 항목 abort → executor busy=0 / queue=0 클린 → 새 buildWithParameters trigger.
- **항구 처방 (검토)**:
  - **(I)** Jenkins numExecutors 2 → 4~6 (`jenkins-init/basic-security.groovy` 또는 별도 Groovy init script)
  - **(II)** Jenkinsfile 의 `parameters {}` 블록을 declarative 로 정리해 첫 호출 (`build` action) 도 정상 작동하도록 — "1차 빈 build 회로" 제거

### 8.3 provision.sh Workflow 재import 시 부작용

- **증상**: workflow YAML 수정 후 provision.sh 재실행 → Workflow App 재생성 (UUID 변경) + Jenkins Job 재생성 → §8.2 의 cascade 유발.
- **현재 동작**: `dify_import_workflow()` 가 동일명 App 자동 삭제 후 fresh import — 멱등 보장 위해. 부작용으로 Job 도 영향.
- **항구 처방 (검토)**: Job 재생성 회피. workflow App 만 재import 하고 credential 만 갱신. Job 자체는 보존.

### 8.4 워크플로우 변경 반영 시 운영 비용

- 매 사이클마다 코드 변경 → docker cp 로 컨테이너 동기 → workflow YAML 변경 시 provision.sh 재실행 → Jenkins 1차 빈 build → executor 정리 → buildWithParameters → 14~25분 대기.
- 누적: 사이클 3+D (1회), 3+E (1회), 3+E' (2회 — 1차 abort 후 2차) = **총 4회 빌드** 의 인프라 부담.
- **항구 처방 (검토)**: 코드 변경만 있으면 **이미지 재빌드 없이 docker cp + Jenkins kill-and-rerun** 만으로 대부분 검증 가능. workflow YAML 변경 시만 provision.sh 일부 호출.

### 8.5 측정 시 알려진 함정

- **regex 추출 오류 (저자 자가 발견)**: 사이클 3+D 측정 직후 "신호등 1축 라벨/색상 모순" 이라고 잘못 보고했으나 실제는 정상. 내가 만든 regex 가 HTML 의 lazy match 때문에 잘못된 텍스트를 추출. 이후 cycle 3+E' 측정에서는 mini-card 단위 분리 추출로 정정.
- **교훈**: 자동 추출 결과를 **항상 1~2 case 수동 cross-check** 후 "회귀 발견" 같은 큰 결론 도출.

---

_이 문서는 2026-04-25 기준 04 파이프라인 의 베이스라인 → 사이클 3+E' 까지의 진화 이력 + 운영 인프라 이슈 누적 정리 회고. 후속 사이클이 진행되면 §3 에 추가 항목 / §5 메트릭 표 / §8 운영 이슈 갱신 필요._
