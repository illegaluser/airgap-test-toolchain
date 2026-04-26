# 협업 세션 의사결정 통합 정리 — 2026-04-26

> **이 문서가 무엇인가**
> 2026-04-26 협업 세션 (`feat/code-rag-hybrid-retrieve` 브랜치) 동안 내려진 모든 의사결정·정정·신규
> 산출물을 *한 곳에 통합 정리* 한 문서. PLAN §11 / EXECUTION §10 의 변경 이력과 *상호보완* — 본 문서는
> 세션 전체의 *서사·주제 그룹화·영향도* 를 우선하고, PLAN/EXECUTION 의 변경 이력은 일자별 *결정의
> 근거·세부* 를 우선한다.
>
> **사용 시점**: 세션이 다 끝난 뒤 *내가 무엇을 결정했었는지 한 페이지로 보고 싶을 때*. 또는 *새로
> 합류한 동료에게 현재 상태 한 번에 전달* 할 때.

---

## 0. 세션 한 페이지 요약 (TL;DR)

| 영역 | 핵심 결정 |
|---|---|
| **운영 환경** | WSL2 / Intel + RTX 4070 (1차) + M4 Pro (2차 이식). 양 머신 동일 모델·동일 이미지·동일 supervisord. 차이는 arch + `OLLAMA_NUM_PARALLEL` 1 개만 |
| **절대 전제** | 폐쇄망 (airgap) SI 환경. 외부 API 호출 0. 모든 모델·binary·plugin 은 외부망 1 회 다운로드 → 이미지 통합 → 폐쇄망 `docker load` |
| **Phase 0 인프라** | Hybrid RAG 트리플 스토어 — Meilisearch (sparse) + FalkorDB (graph) + Qdrant (dense, 현행) + retrieve-svc (FastAPI 어댑터) + bge-reranker-v2-m3 (in-process rerank). 인프라 구동 ✅, Dify Workflow 와의 datasource 교체는 Phase 4 미완 |
| **모델 라인업** | LLM `gemma4:e4b` (양 머신 동일, 큰 모델 영구 비채택) / 임베딩 `qwen3-embedding:0.6b` (retrieve-svc) + `bge-m3` (Dify 내장) / 리랭커 `bge-reranker-v2-m3` |
| **02 ↔ 04 동시 구동** | 영구 차단. `lockable-resources` 플러그인 + `lock(resource: 'ttc-llm-bus')` 양 jenkinsfile 적용 |
| **Joern CPG** | 정식 채택 — Phase 8 신설 (3~4주, Phase 7 와 병렬 가능). 02b 야간 batch + taint·data flow 질의. **현시점 미구현** |
| **PM 가치** | 요구사항 구현율 (Implementation Rate) 자동 측정 — IMPLEMENTATION_RATE_PM_GUIDE.md 신설 |
| **사용자 친화** | README 전면 재작성 (3,745 → 1,375 줄, 6 섹션 구조). 자동 설치 스크립트 2 개 (`setup-host.sh`, `download-rag-bundle.sh`) |

---

## 1. 핵심 방향 결정

### 1.1 1차 검증 머신 — M4 Pro → WSL2/RTX 4070 으로 전환

**컨텍스트**: 사용자 지시 — *"wsl에서 해당 사항 먼저 구현하기로 한다"*.

**이전**: M4 Pro (arm64, 48GB unified memory) 1차 검증 → RTX 4070 이식.
**현재**: **WSL2 / Intel 185H + RTX 4070 8GB VRAM (amd64) 1차 검증** → M4 Pro 이식.

**영향**:
- 모든 GO/NO-GO 게이트는 1차 머신 (WSL2/RTX 4070) 기준으로 판정
- `build-wsl2.sh` / `run-wsl2.sh` 가 정본 (`build-mac.sh` / `run-mac.sh` 는 2차 이식 시점)
- offline-assets 디렉터리 트리에 amd64 우선 표기

### 1.2 양 머신 동일 원칙 강화

**컨텍스트**: 사용자 지시 — *"양 머신 동일구성 및 동일 설치방법이 유지되어야 한다"*.

**원칙**:
- 모델 / 컨테이너 / supervisord / entrypoint / 포트 / 볼륨 / 스크립트 흐름 *전부 동일*
- 차이는 **단 두 가지**:
  1. arch (`linux/amd64` vs `linux/arm64`)
  2. `OLLAMA_NUM_PARALLEL` 환경변수 (RTX `1` / M4 Pro `3` — throughput 만)

**영구 비채택 (양 머신 동일 원칙 보존)**:
- `gemma4:26b` 등 큰 LLM (RTX 4070 8GB VRAM 안 들어감)
- "M4 Pro 야간 batch 만 26b 사용" 옵션 D 폐기
- Phase 7 정확도 부족 시 — 모델 변경 대신 *e4b ×3 self-consistency 다수결* 로 우회

### 1.3 폐쇄망 (airgap) 절대 전제

**컨텍스트**: 사용자 지시 — *"모든 소프트웨어는 폐쇄망 기준으로 구동되어야 한다. 현재 구현중인
프레임워크 기본 전제가 폐쇄망 기반 SI 환경에서의 테스트자동화 및 코드기반 측정 솔루션 구개발이다.
이걸 절대 간과해선 안된다."*

**전제 4 항목**:
1. 외부 API 호출 0 (Codestral / Voyage / Claude / Gemini / OpenAI / Jina API 등 *어떤 컬럼에도* 채택 X)
2. 모든 자산 외부망 1 회 다운로드 → 이미지 통합 → 폐쇄망 `docker load`
3. 런타임 인터넷 endpoint 0 — `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`
4. 고객사 NDA 격리 — L2b cross-project 영구 제외, dataset purge 의무

**산출**: PLAN §1 의 절대 전제 박스 + §4 도구 매트릭스 "❌ 사용 불가 (airgap)" 컬럼 신설.

---

## 2. Phase 0 — Hybrid RAG 인프라

### 2.1 결정 — 02 의 단일 dense RAG 를 트리플 스토어로 확장

| 인덱스 | 기존 | Phase 0 |
|---|---|---|
| Dense (의미) | Qdrant (Dify 내장) | 동일 — 변경 없음 |
| Sparse (BM25) | (없음) | **Meilisearch v1.42** 신규 (:7700 내부) |
| Graph (호출관계) | (없음) | **FalkorDB** Redis module 신규 (:6380 내부) |
| 어댑터 | Dify 내장 retrieve | **retrieve-svc** FastAPI (:9100 내부) |
| Rerank | (없음) | **bge-reranker-v2-m3** sentence-transformers in-process |

### 2.2 모델 라인업 (양 머신 동일)

| 컴포넌트 | 모델 | 출처 |
|---|---|---|
| LLM (분석 + enricher 공유) | `gemma4:e4b` | Ollama 라이브러리 정식 |
| 임베딩 (retrieve-svc) | `qwen3-embedding:0.6b` | Ollama 라이브러리 정식 |
| 임베딩 (Dify 내장 — 현행) | `bge-m3` | Ollama (provision.sh 가 등록) |
| 리랭커 | `bge-reranker-v2-m3` (568M, Apache 2.0) | HuggingFace BAAI |

> **임베딩 이중 트랙 (정직)** — 현행 04 Workflow 가 Dify 내장 dense retrieve 를 사용하므로
> provision.sh 는 `bge-m3` 등록. retrieve-svc 의 hybrid retrieve 는 `qwen3-embedding:0.6b` 사용.
> 04 Workflow 의 datasource 를 retrieve-svc 로 교체하는 작업 (Phase 4) 는 *미완* — 그래도 04 는
> 현재 정상 동작.

### 2.3 정정 — gemma4:e4b 표기

**이전 잘못된 표기**: `gemma4:e4b-it-q4_K_M` (Ollama 공식 태그 아님)
**현재**: plain `gemma4:e4b`

**용량 정정**:
- on-disk **9.6 GB** = 멀티모달 manifest 합 (text decoder + vision + audio 인코더)
- 텍스트 추론 시 **VRAM 실사용 ~3~5 GB** (text decoder Q4 weights ~2.5~3 GB + KV cache @ `num_ctx=4096` ~0.5~1.5 GB)
- → RTX 4070 8GB VRAM 에 *여유 fit* (이전 "KV cache CPU offload 발생" 표현은 부정확했음)

### 2.4 retrieve-svc 포트 — 9000 → 9100

**컨텍스트**: SonarQube 가 컨테이너 내부 :9000 점유.
**결정**: retrieve-svc :9100 / spec-svc (Phase 7) :9101 — 9100 대역 묶음.

### 2.5 구현 산출물 (Phase 0 P0.4 + P0.5)

| 산출 | 위치 | 비고 |
|---|---|---|
| retrieve-svc 스켈레톤 | [code-AI-quality-allinone/retrieve-svc/](../retrieve-svc/) | FastAPI + RRF + 4 backends + 단위 테스트 15/15 PASS |
| Dockerfile 보강 | [code-AI-quality-allinone/Dockerfile](../Dockerfile) | FalkorDB multi-stage (Stage 2.5) / Meilisearch binary COPY / bge-reranker weight COPY / retrieve-svc venv 설치 |
| supervisord.conf | [scripts/supervisord.conf](../scripts/supervisord.conf) | 신규 3 program (meilisearch / falkordb / retrieve-svc). Total 11 → **14 program** |
| entrypoint.sh | [scripts/entrypoint.sh](../scripts/entrypoint.sh) | MEILI_MASTER_KEY generate-once / Meili+Falkor 헬스 대기 / retrieve-svc 명시 start |

---

## 3. 운영 정책 결정

### 3.1 02 ↔ 04 동시 구동 영구 차단

**컨텍스트**: 사용자 지시 — *"04, 02 파이프라인은 절대 겹치게 구동되어선 안된다. 차제에 막아야한다."*

**원인**: host Ollama 가 *단일 LLM bus* — 02 (enricher LLM) 와 04 (분석 LLM) 가 시간 겹치면 모델
swap / KV cache 경합 / OOM 위험.

**메커니즘**:
1. `lockable-resources:latest` 플러그인 추가 ([.plugins.txt](../.plugins.txt))
2. 02 / 04 jenkinsfile `options` 블록:
   ```groovy
   options {
       disableConcurrentBuilds()
       lock(resource: 'ttc-llm-bus')
   }
   ```
3. 향후 06 (Phase 7 구현률 측정) / 02b (Phase 8 Joern batch) 도 동일 lock

**lock 미적용**:
- 03 (Sonar) — LLM 미호출
- 01 (chain) — 02→03→04 순차 호출만, 각 child 가 자체 lock

**산출 영향 파일**:
- [`.plugins.txt`](../.plugins.txt) — `lockable-resources:latest` 추가
- [`jenkinsfiles/02 코드 사전학습.jenkinsPipeline`](../jenkinsfiles/02%20코드%20사전학습.jenkinsPipeline) — `options { ... lock(...) }` 추가
- [`jenkinsfiles/04 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline`](../jenkinsfiles/04%20코드%20정적분석%20결과분석%20및%20이슈등록.jenkinsPipeline) — 동일 옵션 추가
- PLAN §6.11 / EXECUTION §8.1 T0 (최우선 리스크) 문서화

### 3.2 Joern CPG 정식 채택 — Phase 8 신설

**컨텍스트**: 사용자 지시 — *"joern은 실제로 현재 프로젝트에 필요한 도구라 생각된다."*

**배경**: tree-sitter 기반 호출그래프 (Phase 0~5) 는 *AST 만* 정규화 → taint·reaching-def·data flow
질의 불가. Joern 의 Code Property Graph (AST + CFG + DDG + PDG + Call Graph 통합) 가 보안 룰
(SQL injection / XSS / command injection) 분석에 결정적.

**Phase 8 요지**:

| 항목 | 내용 |
|---|---|
| 기간 | 3~4 주 (Phase 7 와 병렬 가능 — 동일 ~15 주 시점 완료) |
| 신규 Job | `02b 코드 CPG batch.jenkinsPipeline` (야간 02:30 cron, lock 공유) |
| 신규 산출물 | `offline-assets/joern/joern-cli.zip` / Dockerfile 수정 / `cpg_to_falkor.py` / retrieve-svc taint backend |
| FalkorDB 스키마 확장 | `Variable` / `Literal` / `Block` 노드 + `CFG` / `DDG` / `REACHING_DEF` / `TAINTS` 엣지 |
| retrieve-svc 라벨 | `source_layer = "graph_taint"` (보안 룰 분석에 인용) |
| 시간 격리 | 02b 도 동일 lock (`ttc-llm-bus`) — 02·04·06 와 cross-pipeline 직렬화 |
| KPI | taint 인용률 0% → 30%+ (보안 룰 한정), 보안 false-positive 강등률 +10pp |
| 자원 부담 | JVM RAM 6~12 GB · 100k LOC 기준 CPG 빌드 ~30 분 · FalkorDB 디스크 증분 ~2 GB |

**현재 상태**: **계획만, 미구현** — `02b` jenkinsfile / `cpg_to_falkor.py` 모두 없음.

### 3.3 도구 매트릭스 airgap 정합화

**컨텍스트**: 사용자 지적 — *"도구 매트릭스 확인하다 문제를 발견했다."*

**진단**: §4 도구 매트릭스에 API 의존 후보 (Codestral 1순위 / Jina Reranker 대안 / Claude·Gemini self-verification 대안 / DeepEval cloud / LangExtract API) 가 잔존.

**조치**:
1. **"❌ 사용 불가 (airgap)"** 컬럼 신설. API 의존 도구 일괄 격리:
   - Codestral · Voyage code-3 · Gemini Embedding · OpenAI text-embedding-3
   - Claude API · Gemini API · OpenAI API · Anthropic Bedrock
   - Jina Reranker API · Cohere Rerank API
   - Pinecone · Weaviate Cloud · Vertex Vector Search · Aura · Memgraph Cloud · Neptune
   - Algolia · ElasticCloud · Pinecone Sparse
   - LangExtract API · unstructured.io SaaS · LlamaParse API
   - DeepEval cloud · OpenAI Evals · Promptfoo cloud
2. **1순위 = 폐쇄망 채택** 으로 명명 정정
3. **검증 절차 4 항목** 신설 — 신규 도구 도입 시 *외부망 1 회 다운로드 자급 / 런타임 인터넷 endpoint 0 / 상용 SI 라이선스 / 사전 배치 가능* 모두 ✅ 이어야 채택

---

## 4. PM 가치 정의

### 4.1 핵심 가치 — 요구사항 구현율 (Implementation Rate)

**컨텍스트**: 사용자 질의 — *"현재 목표를 구현함에 따라 요구사항 대비 프로젝트 구현율을 가시적으로
측정·평가할 수 있다는거지? 중요한게 그거야. PM 측면에서 어떤 원리를 통해 이를 측정할 수 있는지에
대한 논리 및 근거가 필요하다구."*

**4 단계 측정 원리**:

```
PRD (자유 텍스트)
   ↓ ① 분해 (LLM + INVEST 검증)
N 개 atomic requirements
   ↓ ② Hybrid Retrieve (dense + sparse + graph) — 각 요구사항당 후보 코드 5~10
후보 코드
   ↓ ③ LLM-as-Judge (3-state + cited_symbols + confidence)
판정 1
   ↓ ④ Self-verification (e4b ×2 다른 prompt) — 일치 시 자동 마감 / 불일치 시 needs-review
최종 verdict
```

**4 가지 신뢰 근거**:

1. **학술 검증** — Trace Link Recovery 30+ 년 연구. TraceLLM (2026) Macro-F1 58.75% / ProReFiCIA recall 93.3% / 자동차 도메인 RAG 기반 TLR validation 99% / recovery 85.5%
2. **Abstention** — 4 종 보정 메트릭으로 자동 confidence. 0.85 미만은 사람 위임 (자기 한계 인지)
3. **Ground Truth** — petclinic-rest 12 atomic req 사람 정답지 vs 자동 판정 → precision/recall/F1 측정. 1차 50% / 튜닝 후 70%+
4. **폐쇄망 자급** — 외부 API 호출 0

### 4.2 PM 가이드 신설

**산출**: [docs/IMPLEMENTATION_RATE_PM_GUIDE.md](IMPLEMENTATION_RATE_PM_GUIDE.md)

**구조**: 8 섹션 + FAQ + 용어 정리
1. 비유 — *시험 답안 자동 채점기*
2. PM 화면 예시 (구현율 73% / 영역별 분포 / 변화 추이 / 사람 검수 큐)
3. 4 단계 원리
4. 4 가지 신뢰 근거
5. 정직한 한계
6. PM 실전 사용 (스프린트 종료 / 릴리스 게이트 / 이해관계자 보고 / 회귀 감지)
7. FAQ 7 개
8. 한 페이지 요약

**대상**: PM · 이해관계자 · 임원 · 비개발자 (개발 용어 최소화).

---

## 5. 사용자 친화 / 자동화

### 5.1 자동 설치 스크립트 신설

**컨텍스트**: 사용자 제안 — *"ui형태로 설치파일이 제공되는 솔루션(docker desktop, ollama)를 제외하고,
호스트 wsl2 혹은 macos 터미널에서 일괄적으로 모든 구성요소를 다운로드받아 설치할 수 있는 스크립트를
구성하는게 어떨까?"*

**산출 스크립트 2 개**:

#### scripts/setup-host.sh (~250 줄)

호스트 OS 패키지 자동 설치 (Docker Desktop / Ollama 본체 제외).

| 동작 | 처리 |
|---|---|
| OS 자동 감지 | macOS / WSL2 / Linux (uname + /proc/version) |
| Docker 검증 | 없으면 OS 별 설치 안내 + 명확한 에러 종료 |
| Ollama 검증 / 자동 설치 | Linux/WSL2 한정 `--install-ollama` 시 `curl ... \| sh` |
| macOS | Homebrew 자동 + `brew install python@3.12 git curl` |
| WSL2/Linux | `sudo apt install python3 python3-pip python3-venv git curl bash unzip build-essential` |
| huggingface-cli | `--user` 모드 + `~/.bashrc` 또는 `~/.zshrc` PATH 자동 등록 |
| 검증 | 8 도구 모두 `--version` 응답 |

**플래그**:
- `--check` : 검증만
- `--install-ollama` : Linux/WSL2 한정 Ollama 자동 설치
- `--with-assets` / `--all` : `download-rag-bundle.sh` 자동 호출

#### scripts/download-rag-bundle.sh (~190 줄)

외부 자산 일괄 다운로드 (멱등).

| # | 자산 | 위치 |
|---|---|---|
| 1 | Jenkins / Dify 플러그인 (`download-plugins.sh` 호출) | `jenkins-plugins/` + `dify-plugins/` |
| 2 | Meilisearch v1.42 binary (호스트 native arch 한 개) | `offline-assets/meilisearch/` |
| 3 | bge-reranker-v2-m3 weight (huggingface-cli) | `offline-assets/rerank-models/` |
| 4 | FalkorDB Docker 이미지 (multi-stage 차용) | Docker daemon |
| 5 | Ollama 모델 (gemma4:e4b + qwen3-embedding:0.6b + bge-m3) | `~/.ollama/models/` |

### 5.2 README 전면 재작성 (3 단계)

#### 1차 — 6 섹션 통합 구조 (3,745 → 741 줄)

**컨텍스트**: 사용자 지시 — *"중복내용 제외하고 내용 전면 재구성해. ... 1. 문서 존재 목적 2. 필수
사전 준비물 3. 플랫폼별 빌드 4. 플랫폼별 서비스 구동 및 프로비저닝 5. 각 파이프라인 이용방법
6. 기타"*

#### 2차 — 비개발자 친화 보강 (741 → 1,375 줄)

**컨텍스트**: 사용자 평가 요청 후 *"전부 보강해. 제로베이스의 초심자가 이해하고 따라할 수 있어야
한다."*

| 보강 항목 | 신규 |
|---|---|
| §2.1 OS 환경 부트스트랩 | macOS Xcode CLT + Homebrew / WSL2 + Ubuntu / Linux Docker 그룹 / Docker Desktop 첫 실행 / Ollama 설치 / 셸 여는 법 (5 OS 별 분기) |
| 시간·용량 경고 (⏱ / 💾) | 모든 장시간 명령에 인라인 |
| §3.5 USB 매체 가이드 | exFAT 권장 / OS 별 마운트 경로 / SHA256 검증 |
| §5.2 Jenkins UI ASCII | Job 목록 / Build with Parameters 위치 / Console Output |
| §5.9 결과 해석 | GitLab Issue PM 친화 / Pre-training Report / RAG Diagnostic Report 4-stage / SonarQube UI / AI 평가 summary.html / chain_summary.json |
| §6.1 트러블슈팅 | 7 → 14 케이스 (Ollama 정체 / HF rate limit / WSL2 nvidia-smi / FAT32 USB / sudo 비번 등) |
| 액션 패턴 | 🎯 무엇을 / 📦 결과 / ✅ 정상 신호 / ❌ 자주 막히는 곳 |

#### 3차 — 자동 스크립트 우선 흐름 (구조 재배열)

**컨텍스트**: 사용자 지적 — *"설치스크립트를 새로 생성했으니 README.MD에 해단 내용을 업데이트 하고
가이드상 기술순서도 달라져야 하지 않나?"*

| 변경 | 내용 |
|---|---|
| §2.1.2 (macOS) | "Xcode CLT + Homebrew" → "Xcode CLT 만" (Homebrew 는 setup-host.sh 처리) |
| §2.1.6 신규 | Ollama 설치 (UI / brew / curl) — 이전엔 §2.3 안에 묻혀 있었음 |
| §2.3 | ⭐ "한 줄 명령 `setup-host.sh --all`" 이 맨 위. 수동 7 도구 표는 부록 |
| §3.2 | "§2.3 에서 이미 처리됨" 안내. 별도 호출 시 `download-rag-bundle.sh`. 수동은 부록 |
| §3.1 흐름 그림 | 스크립트 표기 명시 |

---

## 6. 정정 사항 (이전 문서가 틀렸음)

| 정정 항목 | 이전 | 현재 |
|---|---|---|
| Ollama 모델 태그 | `gemma4:e4b-it-q4_K_M` | `gemma4:e4b` (suffix 없음 — Ollama 공식 태그) |
| gemma4:e4b 메모리 | "VRAM ~5 GB" | "on-disk 9.6 GB / VRAM 실사용 ~3~5 GB" (멀티모달 manifest 합 ≠ VRAM) |
| retrieve-svc 포트 | :9000 | **:9100** (SonarQube :9000 충돌 회피) |
| spec-svc 포트 (Phase 7) | :9001 | **:9101** (9100 대역 묶음) |
| 1차 검증 머신 | M4 Pro | **WSL2 / RTX 4070** |
| Codestral Embed 1순위 | "코드 임베딩 1순위" | ❌ "사용 불가 (airgap)" — API 의존 |
| 임베딩 1순위 | `bge-m3` (범용) | `qwen3-embedding:0.6b` (Ollama 정식, MTEB Code 우위) |
| §4 매트릭스 대안 | "Claude / Gemini self-verification" | ❌ "사용 불가" — 패턴만 차용, 자체 host Ollama 구현 |
| 02 ↔ 04 동시 구동 | Jenkins build queue 로 자연 직렬화 가정 | **명시 lock(ttc-llm-bus) 필수** — Manual trigger 불통 |
| Joern | "점진 도입" | "**Phase 8 정식 채택**" (3~4 주, Phase 7 병렬) |

---

## 6.1 후속 결정 — Windows + WSL2 의 Ollama 설치 정책

**컨텍스트**: 사용자가 `setup-host.sh` 를 WSL2 셸에서 실행했더니 호스트(Windows) 에 GUI 로 이미
설치된 Ollama 를 인식 못 하고 재설치 시도 → 본 세션 후반 fix.

**진단**: 기존 `verify_ollama` 가 `command -v ollama` 만 체크 → Windows GUI 설치는 ollama CLI 를
*Windows host PATH 에만* 등록하므로 WSL2 Ubuntu 셸의 PATH 에는 없음 → 오판.

**선택지 정리**:

| 선택 | 절차 | 모델 저장 위치 | 권장도 |
|---|---|---|---|
| **A** | Windows GUI + Windows PowerShell 에서 `ollama pull` | `C:\Users\<Win>\.ollama\models\` (WSL2 에서 `/mnt/c/Users/<Win>/.ollama/models/`) | **⭐ 권장** |
| B | WSL2 안에 별도 CLI + daemon 설치 (`curl install.sh`) | WSL2 home `~/.ollama/models/` (Windows 와 별개) | 모델 두 번 받음 |
| C | WSL2 CLI + `OLLAMA_HOST=http://localhost:11434` 로 Windows daemon 공유 | Windows 측 (path 표시 헷갈림) | 비추 |

**결정**: **선택 A 가 정본**. 이유:
- Windows 사용자의 자연스러운 흐름 (시작 시 자동 기동)
- 단일 모델 저장소 (디스크 22 GB+ 절약)
- Windows GUI 의 모델 관리 UI 그대로 활용

**구현 (커밋 `f68749a`)**:

`verify_ollama` 4 케이스 매트릭스 도입:
- A: CLI + daemon → 정상
- **B: CLI 없음 + daemon 응답 → 호스트 GUI 설치 인식, *통과*** (Windows + WSL2 케이스)
- C: CLI 있음 + daemon 미응답 → 백그라운드 기동
- D: 둘 다 없음 → 진짜 미설치 → 설치 안내

`download-rag-bundle.sh` 도 동일 매트릭스:
- B 케이스에서 Ollama 모델 다운로드 단계 자동 skip
- 사용자에게 Windows PowerShell `ollama pull` 안내 + `/mnt/c/Users/<Win>/.ollama/models/` 경로 명시

**문서 갱신**:
- README §2.1.6 — Windows + WSL2 의 ⭐ 선택 A 권장 명시 + 선택 B 단점 명시
- README §3.2 — `download-rag-bundle.sh` 가 자동 skip 하는 케이스 안내 박스
- README §3.4 — Ollama 모델 export 시 OS 별 분기 (macOS/Linux/WSL2-native vs Windows-GUI+WSL2)
  + Windows 사용자명 자동 감지 명령 (`cmd.exe /c 'echo %USERNAME%'`)

---

## 7. 미해결 / 진행 중

| 영역 | 상태 | 대응 |
|---|---|---|
| **Phase 4** — 04 Workflow 의 datasource 를 Dify 내장 → retrieve-svc External KB API 로 교체 | ⏳ 미완 | 현재 04 는 여전히 Dify 내장 dense retrieve (Qdrant + bge-m3). retrieve-svc 는 *기동만 됨*. 인프라 영향 0 |
| **Phase 5** 측정·튜닝 (A/B 회귀 비교) | ⏳ 미시작 | Phase 4 선결 |
| **Phase 6** L2c canonical 큐레이션 (선택) | ⏳ 미시작 | Phase 5 후 진입 |
| **Phase 7** Spec↔Code 추적성 (Implementation Rate batch Job) | ⏳ 미시작 | spec-svc 신규 작성 필요 |
| **Phase 8** Joern CPG 정식 도입 | ⏳ 미구현 (계획만) | `02b` jenkinsfile 없음, `cpg_to_falkor.py` 없음 |
| **Dify Workflow 수동 등록** — retrieve-svc 를 External KB API 로 등록하는 자동화 | ⏳ 미자동화 | 현재 provision.sh 가 처리 안 함 — Phase 4 작업 시 추가 |

---

## 8. 변경 영향 매트릭스 — 결정 → 파일

| 결정 | 영향받은 파일 |
|---|---|
| §1.1 WSL 우선 | PLAN §3.5.3 / §6.5 / §6.9 / §6.12 / §10 Phase 0~1 / §11 변경이력 / EXECUTION TL;DR / §1.1 / §3 / §5 / §7 / §8 |
| §1.2 양 머신 동일 + 큰 모델 비채택 | PLAN §6.4 4.3 / 4.5 / §11 / EXECUTION §1.2 / §10 / SPEC_TRACEABILITY_DESIGN §5.6 / §5.7 |
| §1.3 폐쇄망 절대 전제 | PLAN §1 박스 / §4 매트릭스 / §11 / §2.2 SOTA 표 |
| §2.1 Phase 0 인프라 | Dockerfile / supervisord.conf / entrypoint.sh / `.plugins.txt` / retrieve-svc/ 디렉터리 신규 / PLAN §3 / §6 / §10 Phase 0~5 |
| §2.3 모델 태그 정정 | PLAN / EXECUTION 활성 섹션 전체 grep 정리 |
| §2.4 retrieve-svc 포트 9100 | supervisord.conf / PLAN §3.5.3 / §6.3 / §6.8 / §6.9 / §6.12 / §10 Phase 2 / SPEC_TRACEABILITY_DESIGN |
| §3.1 02 ↔ 04 lock | `.plugins.txt` / 02 jenkinsfile / 04 jenkinsfile / PLAN §6.11 / EXECUTION §8.1 T0 |
| §3.2 Joern Phase 8 | PLAN §4 매트릭스 / §10 Phase 별 한눈에 + Phase 8 본문 / §10 의존성 다이어그램 / EXECUTION §5.2 W11~W14 / §7.8 |
| §3.3 도구 매트릭스 airgap | PLAN §1 박스 / §2.2 / §4 매트릭스 / §5 P1.6 / §11 |
| §4 PM 가치 | docs/IMPLEMENTATION_RATE_PM_GUIDE.md 신설 / PLAN TL;DR cross-link |
| §5.1 자동 스크립트 | scripts/setup-host.sh 신규 / scripts/download-rag-bundle.sh 신규 |
| §5.2 README 재작성 | README.md 전면 재작성 (3 차례) |

---

## 9. 산출 문서·코드 목록

### 신규 문서

| 문서 | 무엇 |
|---|---|
| [docs/IMPLEMENTATION_RATE_PM_GUIDE.md](IMPLEMENTATION_RATE_PM_GUIDE.md) | PM·이해관계자용 요구사항 구현율 안내서 |
| [docs/SESSION_DECISIONS_2026-04-26.md](SESSION_DECISIONS_2026-04-26.md) | **본 문서** — 세션 의사결정 통합 정리 |

### 신규 코드

| 파일 | 역할 |
|---|---|
| [retrieve-svc/](../retrieve-svc/) | FastAPI Hybrid Retrieve 어댑터 — Dify External KB API 호환 |
| [retrieve-svc/app/main.py](../retrieve-svc/app/main.py) | FastAPI 앱 + lifespan + `/health` + `/retrieval` |
| [retrieve-svc/app/config.py](../retrieve-svc/app/config.py) | pydantic-settings 환경변수 파싱 |
| [retrieve-svc/app/schema.py](../retrieve-svc/app/schema.py) | Dify External KB API 스키마 (metadata 항상 dict 강제) |
| [retrieve-svc/app/fusion.py](../retrieve-svc/app/fusion.py) | RRF + layer weighting + rerank coordination |
| [retrieve-svc/app/backends/](../retrieve-svc/app/backends/) | qdrant / meilisearch / falkor / ollama_embed / rerank |
| [retrieve-svc/tests/](../retrieve-svc/tests/) | 단위 테스트 15 케이스 (RRF / schema / layer weights / source_layer 우선순위) |
| [scripts/setup-host.sh](../scripts/setup-host.sh) | 호스트 OS 패키지 자동 설치 + Docker/Ollama 검증 |
| [scripts/download-rag-bundle.sh](../scripts/download-rag-bundle.sh) | 외부 자산 일괄 다운로드 (멱등) |

### 갱신 코드

| 파일 | 변경 |
|---|---|
| [Dockerfile](../Dockerfile) | Stage 2.5 FalkorDB / Meilisearch binary COPY / bge-reranker COPY / retrieve-svc venv |
| [scripts/supervisord.conf](../scripts/supervisord.conf) | meilisearch / falkordb / retrieve-svc program 신규 (11→14) |
| [scripts/entrypoint.sh](../scripts/entrypoint.sh) | MEILI key generate-once / Phase 0 헬스 대기 / retrieve-svc 명시 start |
| [.plugins.txt](../.plugins.txt) | `lockable-resources:latest` 추가 |
| [jenkinsfiles/02 코드 사전학습.jenkinsPipeline](../jenkinsfiles/02%20코드%20사전학습.jenkinsPipeline) | `options { lock(ttc-llm-bus) }` 추가 |
| [jenkinsfiles/04 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline](../jenkinsfiles/04%20코드%20정적분석%20결과분석%20및%20이슈등록.jenkinsPipeline) | 동일 lock 추가 |
| [README.md](../README.md) | 3 차례 전면 재작성 — 6 섹션 구조 + 비개발자 친화 + 자동 스크립트 우선 |

### 갱신 문서

| 문서 | 변경 |
|---|---|
| [docs/PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md](PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md) | TL;DR / §1 절대 전제 / §3.5.3 / §4 매트릭스 / §6.4 / §6.5 / §6.8 / §6.9 / §6.10 / §6.11 / §6.12 / §10 Phase 0~5 / Phase 7.4 / **Phase 8 신설** / §11 변경이력 (4 차) |
| [docs/EXECUTION_PLAN.md](EXECUTION_PLAN.md) | TL;DR / §1.1 / §1.2 / §2.1 / §3 / §5 / §7.7 / **§7.8 신설** / §8.1 T0 신규 / §10 변경이력 |
| [docs/SPEC_TRACEABILITY_DESIGN.md](SPEC_TRACEABILITY_DESIGN.md) | retrieve-svc 9100 / spec-svc 9101 / 26b 옵션 → e4b ×3 self-consistency 정정 |

---

## 10. 다음 단계 후보

본 세션 종료 시점에서 다음으로 진행 가능한 작업:

| 우선순위 | 작업 | 예상 기간 |
|---|---|---|
| 🔴 즉시 | 외부망 빌드 1 회 검증 — `bash scripts/setup-host.sh --all` → `bash scripts/build-wsl2.sh` → 헬스체크 14/14 RUNNING + Phase 0 신규 3 health endpoint | 0.5~1일 |
| 🔴 즉시 | Phase 1 폐쇄망 반입 + 컨테이너 기동 검증 | 0.5일 |
| 🟡 단기 | Phase 3 — 02 sink 구현 (`pipeline-scripts/sinks/meili_sink.py` + `falkor_sink.py`) | 2~3일 |
| 🟡 단기 | Phase 4 — Dify Workflow 의 retrieve-svc External KB 등록 (provision.sh 자동화) | 1~2일 |
| 🟢 중기 | Phase 5 측정·튜닝 (A/B 회귀 비교, RRF 가중치 튜닝) | 1~2주 |
| 🟢 장기 | Phase 7 (Spec↔Code 추적성) | 4~6주 |
| 🟢 장기 | Phase 8 (Joern CPG) — Phase 7 와 병렬 가능 | 3~4주 |

---

**문서 작성**: 2026-04-26 협업 세션 종료 시점
**작성 컨텍스트**: 사용자 요청 — *"현재까지의 논의 및 의사결정 사항에 대해 모두 별도 문서로 정리해줘."*
**갱신 방침**: 본 문서는 *2026-04-26 세션 한정 스냅샷*. 이후 결정은 PLAN §11 / EXECUTION §10 의
변경 이력에 누적하고, 다음 세션 종료 시점에 별도 `SESSION_DECISIONS_<날짜>.md` 신설.
