# TTC 5-Pipeline All-in-One — 에어갭 설치 & 구동 가이드

> **본 가이드는 현재 `main` 브랜치 기준**입니다. 단일 통합 이미지에 Jenkins Job 5개(`01 코드 분석 체인` / `02 사전학습` / `03 정적분석` / `04 결과분석+이슈등록` / `05 AI평가`) 가 공존합니다. 과거 `05 AI평가` 가 없던 구 버전 가이드가 아니라, 현재 저장소 상태를 기준으로 작성했습니다. 레포를 받은 뒤 아래 경로로 진입해 작업:
>
> ```bash
> git clone <repo-url> airgap-test-toolchain
> cd airgap-test-toolchain
> cd code-AI-quality-allinone
> ```
>
> **이 스택은 폐쇄망/에어갭 환경에서 작동하도록 설계되었습니다.**
> 전체 흐름: **온라인 준비 머신**에서 필요한 자산 (Docker 이미지 + 플러그인 + Ollama 모델) 을 모두 반출 → 매체(USB/NAS) 로 이동 → **오프라인 운영 머신**에서 복원 후 구동.
>
> 처음부터 인터넷 없는 머신에서 빌드·pull 은 **불가능** 합니다. 반드시 온라인 준비 단계를 선행해야 합니다.
>
> **본 문서의 독자 대상**: 이 스택을 처음 받아 운영하려는 분 (개발자가 아니어도 따라갈 수 있도록 작성). 설치·구동·각 파이프라인 사용·결과 해석·트러블슈팅이 **모두 본 문서 한 곳에 모여 있습니다** — 다른 문서를 열 필요 없습니다. 단일 운영 정보 원천.

## 0. 현재 구현 상태

2026-04-25 `main` 기준 현재 구현은 아래와 같습니다.

| 항목 | 현재 상태 |
|------|-----------|
| 통합 이미지 베이스 | Jenkins `2.555.1-lts-jdk21`, SonarQube `26.4.0.121862-community`, Dify API/Web `1.13.3`, GitLab CE `18.11.0-ce.0` |
| **Dify Code 노드 sandbox (별도 컨테이너)** | `langgenius/dify-sandbox:0.2.10` — `ttc-sandbox` service 로 분리. Dify Workflow 의 `context_filter` / `json_parser` 같은 Code 노드가 의존. 통합 이미지에 sandbox binary 가 포함되지 않아 별도 service 로 띄우는 구조 (**오프라인 번들에 별도 tarball 로 포함**, §3 참조) |
| 기본 샘플 GitLab 프로젝트 | `root/realworld` (Java/Spring Boot, [gothinkster/spring-boot-realworld-example-app](https://github.com/gothinkster/spring-boot-realworld-example-app) — RealWorld API 사양 ↔ 구현 매핑 명시) — provision 이 vendoring 된 로컬 템플릿에서 자동 생성 + 초기 push (인터넷 fallback 없음) |
| Jenkins 기본 대상 프로젝트 | `01`~`04` 파이프라인 기본값이 모두 `realworld` 기준 |
| 프로비저닝 결과 | Dify 관리자/모델/provider/workflow/dataset, Sonar 토큰, GitLab PAT, Jenkins Job 5개 자동 생성 (provision 재실행 시 동일명 App 삭제 후 재생성) |
| P1 RAG 품질 개선 (2026-04-25) | §8.2 tree-sitter pass 2 (callers / test_paths 역인덱스), §8.4 `context_filter` Code 노드 (self-exclusion + 카테고리 섹션화), `build_kb_query` 가 `callees:` / `test_for:` 구조화 쿼리 라인 추가 |
| **P1.5 효과 증폭 (2026-04-25 저녁)** | **KB 품질**: vendor/minified 제외, trivial/dup 청크 필터 (nodegoat 실측 147→27 청크, vendor 오염 제거). **test heuristic 확장**: mocha/jest 스타일 파일명 기반 test_for 후보 + is_test 쿼리 라인. **retrieval**: weighted_score 재정렬 (semantic 0.7 / keyword 0.3) + score_threshold=0.35. **context filter**: 청크 score 표기 + has_context boolean. **confidence 가드**: RAG 빈 경우 `confidence=low` + `context:empty` 라벨 강제. **진단 리포트**: 04 Jenkinsfile `RAG Diagnostic Report` 탭 신설 (per-이슈 버킷 상태 + LLM citation rate) |
| **P2 추가 액션 (2026-04-25 후속)** | **다언어 지원 확장**: Go/Rust/C#/Kotlin/C/C++ LANG_CONFIG 추가. **docstring 추출**: tree-sitter 로 함수 leading comment + Python docstring 을 청크 footer 의 `doc:` 라인에 추가 (의미 매칭 보강). **retry variation**: attempt 0/1/2 별로 다른 모양의 kb_query (구조화 / 자연어 / 식별자 중심). **HyDE (간소화)**: attempt=2 (마지막 retry) 에서만 호스트 Ollama 로 한 줄 자연어 변환 호출 → kb_query 보강. **임베딩 모델 환경변수 override** (운영자가 코드 특화 임베딩으로 교체 가능, §8.2.4.2). **자동 평가**: golden CSV vs llm_analysis.jsonl 비교 스크립트 `eval_rag_quality.py` (§8.4.2.2) — 변경 전후 metric 비교 |
| **04 안정화 + GitLab UX 패치 (2026-04-25 심야)** | `gemma4:e4b` (thinking 모델) 이 원인이 된 04 파이프라인 "Dify succeeded but outputs empty" 대량 실패 해소. **(1) 진단**: workflow `json_parser` Code 노드에 `parse_status` / `llm_text_preview` / `parse_error_msg` 필드 노출, `dify_sonar_issue_analyzer.py` 가 empty 감지 시 stderr 에 LLM 원문 + 실패 유형 덤프 (`--print-first-errors N` 으로 샘플 수 제한). **(2) 근본 fix**: `<think>...</think>` reasoning block 제거 (종료·미종료 모두 대응), Workflow `max_tokens` 2048→16384, Ollama provider credential `max_tokens` 4096→16384 · `context_size` 8192→32768 (provision.sh 동시 반영), JSON string 내 raw `\n\r\t` + invalid `\escape` (regex 룰의 `\d\s\w` 등) 을 상태기계로 자동 fixup. **(3) UX**: analyzer stdout/stderr + `llm_analysis.jsonl` 을 line-buffered 로 전환 — Jenkins console 실시간 진행 로그 반영. **(4) GitLab 18.x Work Items UI**: description 기본 Truncate 가 본 스택 생성 이슈(3~5K자)에 불리 → webpack chunk 의 `truncationEnabled:!0 → !1` sed 패치 (`scripts/gitlab-truncate-patch.sh`, `run-mac.sh` · `run-wsl2.sh` 가 백그라운드 호출, 멱등). **(5) 로그 잡음**: `tree_sitter_languages` `FutureWarning` 60+ 줄 `warnings.filterwarnings` 로 억제. **결과**: MAX_ISSUES=10 실측 10/10 성공 (FAIL-EMPTY 0), Stage 3 까지 GitLab 이슈 자동 등록. 상세 트러블슈팅 §12.20. |
| **RAG 효용 시리즈 (2026-04-25 새벽)** | 정적분석 이슈 분석에서 사전학습 KB 가 실질적 도움을 주는지 측정·정직화. **citation rate 2.1% → 75%, partial_citation 자동 강등 도입, KB 인텔리전스 PM 카드 신설**. 주요 fix: **(A) Dataset purge** (02 full mode 시 기존 청크 전수 삭제, 프로젝트 격리), **(B/B') segmentation 안정화** (청크 2200자 cap + footer 잘린 segment 의 document.name fallback), **(C/D) 3-tier citation 매칭** (path/symbol/basename) + (path,symbol) 단위 dedup, **(D) 인용 의무 prompt** + **(P5) line-range self-exclusion** (ProfileDAO 같은 동명 method 다중 케이스), **(P7) confidence calibration** (cited<50% → medium 강등 + `partial_citation` 라벨), **(+T1) test_for 다중 후보 + PascalCase prefix 매칭** (`Login` → `LoginHandler`), **(+T2) citation_depth 측정** (distinct backtick 식별자 수). **사이클 1 (D1+D3+B+A1+F1+F2)**: footer 에 `_context_summary` / 자연어 동의어 / e2e description 노출, score_threshold 0.35→0.25, RAG retrieve trace 로그, 실패 패턴 자동 진단 섹션. **사이클 2 (F+C+D+E+H tree-sitter 강화)**: import 기반 caller 그래프 (false-positive 차단), `@app.route` 데코레이터 → endpoint 추출, decorator 텍스트 footer 노출, TS interface/type/enum 청크화, JSDoc `@param/@returns/@throws` 구조 분리. **PM 친화 리포트**: RAG Diagnostic Report v2 — TL;DR + 3축 신호등 + 실무 액션 + **KB 인텔리전스 8 카드** (분석 파일 / 청크 / 호출 관계 / endpoint / 데코레이터 / 테스트 연결 / 도메인 모델 / 문서화). 측정 자가모순 4종 (라벨↔본문, 표본 부족, 임계 관대, 중복) 해소. 사용법 §11.2 참조. |
| **사이클 3 (2026-04-25 후속) — tree-sitter 누수 봉쇄 + 4-stage 학습진단 + PM 친화 GitLab Issue 본문** | 사이클 2 까지 KB 까지의 추출은 풍부했으나 **쿼리/프롬프트/진단 가시화** 단계에서 tree-sitter 신호가 새고 있던 7개 누수 (다언어 callees 부재, imperative route 미검출, Google/NumPy docstring 미지원, kb_query 가 endpoint/decorator/params 미사용, 이슈 함수의 정적 메타가 LLM 프롬프트에 미주입, used_items 메타 가시화 부재, citation 측정이 정적 메타 활용 미감지) 를 모두 봉쇄. **Phase A (추출 보강)**: `collect_callees()` 에 go/rust/c#/kotlin/c/cpp 분기 추가 (생성자·매크로·method_call 포함 — 11개 언어 전부 callers 역인덱스 가능), `extract_imperative_routes()` 신설 (Express/Koa `app.get('/x', handler)` + Flask `add_url_rule` AST 검출 → endpoint 매핑), `parse_docstring_structure()` 에 Google `Args:` / `Raises:` block + NumPy `----` underline 파서 추가 (raw 추출 경로 분리), `_kb_intelligence.json` 사이드카 자동 작성 (scope/depth/quality 3계층 — 진단 리포트 데이터 소스). **Phase B (보존+쿼리+프롬프트)**: `sonar_issue_exporter._enclosing_meta()` 신설 — 이슈 함수의 decorators/endpoint/doc_struct/callees 까지 통째로 청크에서 추출해 enriched 항목에 9개 필드 첨부, analyzer 가 `build_kb_query()` attempt=0/2 에 endpoint/decorators/params 라인 추가 + `format_enclosing_meta()` 로 LLM 프롬프트 멀티라인 텍스트 생성 → Dify start 변수 `enclosing_meta` 로 전달, workflow user 프롬프트에 `## 이슈 함수 정적 메타 (tree-sitter)` 섹션 신설, system 프롬프트에 "정적 메타 활용 의무" 규칙 추가. **Phase C (가시화 두 리포트 모두)**: context_filter Code 노드의 `used_items` 에 `has_decorators`/`has_endpoint`/`has_doc_struct` boolean + raw 메타 첨부, `_compute_tree_sitter_hits()` 신설 — endpoint/decorator/param/RAG-meta 4종 신호의 답변 등장 횟수 측정해 `tree_sitter_hits` 메트릭으로 보관, **RAG Diagnostic Report 4-stage 학습진단 재설계** (기존 8 카드 나열 → Scope/Depth/Quality/Impact 사다리: PM 이 위→아래로 인과 추적, 언어 비대칭 자동 경고, "AI 가 알아낸 우리 프로젝트 API 진입점" 펼침 박스, per-issue 표에 🛡️🌐📝 메타 아이콘 컬럼), **GitLab Issue 본문 PM 친화 재설계** (🚦 Action Verdict 신호등 최상단 + 📌 무엇이 / 🎯 어디서 + 정적 컨텍스트 / ⚠️ 영향 / 🛠️ 수정 / **🔍 AI 판단 근거 NEW** / 📂 같은 패턴 / 📖 기술 상세 ▶ 접기 — 코드 블록·Rule·링크는 모두 펼침 안). 사용법 §9, §10.5 참조. |
| 빌드 방식 | `DOCKER_BUILDKIT=1 docker build` (legacy builder, buildx 미사용). export/load tarball 단계 제거로 같은 캐시 상태에서 14.9GB 이미지 빌드 ≈ 1 분 |
| 최근 실측 검증 | Jenkins `28080`, Dify `28081`, SonarQube `29000`, GitLab `28090` 정상 응답 확인 |

운영 메모:

- Mac/WSL2 각각 native arch 로만 빌드하므로 buildx 는 제거. `scripts/build-mac.sh` / `scripts/build-wsl2.sh` 모두 legacy `docker build` 사용.
- Sonar 이슈 LLM 분석 품질 지표 (RAG self-retrieval 회피 후 LLM 이 실제로 caller/test 섹션을 인용하는 비율) 는 P1.5 에서 측정 로그 도입 예정 — 현재는 수동 관찰.

## 0.1 이 문서를 어떻게 읽으면 되나

상황별로 먼저 펼쳐볼 절을 다음과 같이 권장합니다.

- 빌드부터 결과까지 **단일 흐름** 으로 이해하고 싶다 → **§0.4**
- 오프라인/폐쇄망에 처음 설치 → §2 → §3 → §4 → §5 → §6
- 이미지와 모델이 이미 있고 곧장 기동 → §6
- 스택이 올라온 뒤 샘플로 실제 파이프라인 검증 → §7
- 각 Jenkins 파이프라인의 역할·입력값·산출물 → §8
- AI 분석 결과 리포트(PM 친화) 읽는 법 → **§10.5**
- 실패 원인 조사 → §12
- 데이터 초기화 후 완전 재프로비저닝 → §13

전체 흐름을 한 단락으로 압축하면:

1. 온라인 머신에서 이미지·플러그인·Ollama 모델을 모두 준비해 반출 (§3, §4)
2. 오프라인 머신에서 Docker/Ollama 를 복원하고 `run-mac.sh` 또는 `run-wsl2.sh` 로 스택 기동 (§5, §6)
3. 자동 프로비저닝 (§0.3, §11) 완료 후 Jenkins `01-코드-분석-체인` 실행 (§8.1)
4. GitLab 이슈 + RAG Diagnostic Report 로 결과 검토 (§9, §10.5)

## 0.2 빠른 시작

이미 Docker 이미지와 Ollama 모델이 준비된 환경이라면, 가장 짧은 경로는 아래입니다.

```bash
cd code-AI-quality-allinone

# macOS
bash scripts/run-mac.sh

# 또는 Windows WSL2
bash scripts/run-wsl2.sh
```

기동 후 아래 3가지를 확인하면 됩니다.

1. `docker ps` 에서 `ttc-allinone`, `ttc-gitlab` 이 `Up`
2. `docker logs -f ttc-allinone | grep -E "provision|entrypoint"` 에서 `자동 프로비저닝 완료`
3. 브라우저에서 Jenkins `http://localhost:28080`, GitLab `http://localhost:28090`, SonarQube `http://localhost:29000`, Dify `http://localhost:28081` 접속 가능

## 0.3 자동으로 처리되는 항목

컨테이너가 기동되면 `provision.sh` 가 다음 작업을 자동 수행합니다 (멱등 — 재실행 안전):

- Dify 관리자 계정 setup, Ollama 플러그인 설치 및 LLM/embedding provider 등록
- Dify Workflow (`sonar-analyzer`) + Dataset (`code-context-kb`) import 및 publish
- SonarQube 초기 비밀번호 변경, 분석용 토큰 발급
- GitLab root PAT 발급, 샘플 프로젝트 (`root/realworld`) 생성 및 초기 push
- Jenkins credentials 5종 (Dify dataset/key, GitLab PAT, SonarQube token) 주입
- Jenkins 파이프라인 Job 5개 등록 (`01`~`05`)
- GitLab Work Items UI "Truncate descriptions" 기본값 OFF 패치 (`scripts/gitlab-truncate-patch.sh`, 호스트에서 백그라운드 실행)

운영자가 신경쓰는 단계는 **이미지 준비 → 컨테이너 기동 → 프로비저닝 완료 대기 → Jenkins Job 실행** 4 단계로 정리됩니다. 각 단계의 상세 절차는 §0.4 참조.

## 0.4 전체 운영 절차 — 빌드부터 첫 결과까지

이 절은 빌드 / 구동 / 프로비저닝 / 파이프라인 실행을 단일 흐름으로 보여줍니다. 각 단계의 상세는 명시된 절(§X.Y)을 참조하세요.

### 1단계: 이미지 빌드 (온라인 준비 머신)

빌드 컨텍스트는 `code-AI-quality-allinone/` 디렉터리 자체이며, Dockerfile 한 개로 Jenkins + SonarQube + Dify (api/web/plugin-daemon) + Postgres + Redis 를 단일 이미지(`ttc-allinone`)에 패키징합니다. GitLab 은 별도 컨테이너 (`gitlab/gitlab-ce:18.11.0-ce.0`) 입니다.

```bash
# 1) 사전 작업: Jenkins / Dify 플러그인 다운로드 (최초 1 회)
bash scripts/download-plugins.sh

# 2) 호스트 아키텍처에 맞춰 빌드
bash scripts/build-mac.sh        # Apple Silicon (linux/arm64)
# 또는
bash scripts/build-wsl2.sh       # WSL2 / x86 Linux (linux/amd64)
```

빌드 결과:

- `ttc-allinone:mac-dev` (또는 `wsl2-dev`) — 약 14.9 GB. 같은 캐시 상태에서 약 1 분.
- 상세 옵션 / pinned base 이미지 / 변경점은 §3.6 참조.

에어갭 운영을 위한 tarball 산출 (`offline-prefetch.sh`), 이미지 저장/복원, 매체 구성은 §3 ~ §4 참조.

### 2단계: 스택 기동 (운영 머신)

`docker compose up -d` 를 한 줄로 호출하는 헬퍼 스크립트:

```bash
bash scripts/run-mac.sh          # 또는 run-wsl2.sh
```

내부 동작:

1. `docker compose -f docker-compose.<arch>.yaml up -d` — 컨테이너 4개 (`ttc-allinone`, `ttc-gitlab`, `ttc-sandbox`, 옵션) 기동.
2. 백그라운드로 `scripts/gitlab-truncate-patch.sh` 호출 — GitLab healthy 대기 후 Work Items UI 의 기본 description 접힘 비활성화.

기동 후 컨테이너 상태:

```bash
docker ps --filter "name=ttc-" --format "table {{.Names}}\t{{.Status}}"
# ttc-allinone   Up 1 minute
# ttc-sandbox    Up 1 minute (healthy)
# ttc-gitlab     Up 1 minute (health: starting → healthy 까지 ~2분)
```

상세 (이미지 복원 / 태그 정합성 / 데이터 볼륨 위치) 는 §6 참조.

### 3단계: 자동 프로비저닝 대기

컨테이너 기동 직후 `entrypoint.sh` 가 `provision.sh` 를 호출해 §0.3 항목을 순차 수행합니다. 진행 상황 확인:

```bash
# 한 줄 요약 — 완료 또는 실패 마커 출력 시 종료
docker logs -f ttc-allinone 2>&1 | grep -E "Phase A|Phase B|샘플|Job 등록|자동 프로비저닝 완료|provisioning 실패"
```

표준 완료 시간(첫 기동 기준):

- GitLab 3-phase ready 대기: 약 2~5 분
- Dify 플러그인 / provider / workflow / dataset 등록: 약 30 초
- SonarQube ready + 토큰: 약 30 초
- 샘플 프로젝트 push: 약 10 초
- Jenkins Job 등록: 약 5 초

`자동 프로비저닝 완료.` 로그가 보이면 4단계로 진행. 실패 패턴은 §12 참조.

### 4단계: 파이프라인 실행

Jenkins UI (`http://localhost:28080`, `admin / password`) 에서 직접 트리거하거나 REST API 로 자동 실행 가능합니다. 가장 흔한 사용 케이스 — `01-코드-분석-체인` 을 한 번 실행하면 02 → 03 → 04 → 체인 요약이 자동 chain.

```bash
# 옵션 A — Jenkins UI: 01-코드-분석-체인 → Build with Parameters
#   (기본값이 realworld 이라 그대로 Build 클릭 가능)

# 옵션 B — REST API (검증·자동화 용)
curl -sS -u admin:password \
  -X POST -H "$(curl -sS -u admin:password http://localhost:28080/crumbIssuer/api/json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["crumbRequestField"]+":"+d["crumb"])')" \
  "http://localhost:28080/job/01-%EC%BD%94%EB%93%9C-%EB%B6%84%EC%84%9D-%EC%B2%B4%EC%9D%B8/buildWithParameters" \
  --data-urlencode "REPO_URL=http://gitlab:80/root/realworld.git" \
  --data-urlencode "MAX_ISSUES=10"
```

각 파이프라인의 입력/출력/소요 시간 표는 §8 참조.

### 5단계: 결과 확인

결과물별 확인 위치:

- **GitLab 이슈** (PM 친화 본문 — 🚦 Action Verdict 신호등 + 무엇이 / 어디서 / 영향 / 수정 / **🔍 AI 판단 근거** + 📖 기술 상세 ▶ 접기) — `http://localhost:28090/root/<프로젝트>/-/issues`
- **Jenkins 빌드 콘솔** (단계별 진행 로그, 실패 진단용) — Jenkins UI → 해당 빌드 → Console Output
- **RAG Diagnostic Report** (3축 신호등 + **4-stage 학습진단 사다리** Scope/Depth/Quality/Impact + 이슈별 평가) — `04` 빌드 → "RAG Diagnostic Report" 탭. 읽는 법은 §10.5
- **Pre-training Report** (KB 청킹 통계 HTML) — `02` 빌드 → "Pre-training Report" 탭
- **`chain_summary.json`** (각 단계 commit SHA / 결과 / GitLab 이슈 수) — `01` 빌드 아티팩트

GitLab Issue 본문의 PM 친화 섹션 구조 (Action Verdict 신호등 · 무엇이 · 어디서 + 정적 컨텍스트 · 영향 · 수정 · AI 판단 근거 · 같은 패턴 · 기술 상세 접기) 는 §9 참조.

---

## 목차

빠른 시작:

- [§0.4 전체 운영 절차 — 빌드부터 첫 결과까지](#04-전체-운영-절차--빌드부터-첫-결과까지)
- [§10.5 RAG Diagnostic Report 읽는 법 — 4-stage 학습진단](#105-rag-diagnostic-report-읽는-법--4-stage-학습진단)

상세 절차:

1. [이 스택이 하는 일](#1-이-스택이-하는-일)
2. [에어갭 배포 전체 그림](#2-에어갭-배포-전체-그림)
3. [온라인 준비 머신 — 자산 수집](#3-온라인-준비-머신--자산-수집)
4. [에어갭 매체 구성 (반출 패키지)](#4-에어갭-매체-구성-반출-패키지)
5. [오프라인 운영 머신 — 런타임 설치](#5-오프라인-운영-머신--런타임-설치)
6. [오프라인 머신에서 스택 기동](#6-오프라인-머신에서-스택-기동)
7. [첫 실행 — 샘플 레포로 파이프라인 돌려보기](#7-첫-실행--샘플-레포로-파이프라인-돌려보기)
8. [각 파이프라인 상세](#8-각-파이프라인-상세)
9. [GitLab Issue 결과물 읽는 법 — PM 친화 본문](#9-gitlab-issue-결과물-읽는-법--pm-친화-본문)
10. [접속 정보 & 자격](#10-접속-정보--자격)
11. [자동 프로비저닝 내부 동작](#11-자동-프로비저닝-내부-동작)
12. [트러블슈팅](#12-트러블슈팅)
13. [초기화 & 재시작](#13-초기화--재시작)
14. [파일 구성 레퍼런스](#14-파일-구성-레퍼런스)
15. [프로덕션 전 체크리스트](#15-프로덕션-전-체크리스트)
16. [부록: 온라인 단일 머신 빠른 테스트](#16-부록-온라인-단일-머신-빠른-테스트)

---

## 1. 이 스택이 하는 일

폐쇄망 환경에서 **코드 품질**과 **AI 응답 품질** 두 가지를 통합 점검하는 Jenkins Pipeline 스택입니다. 한쪽은 GitLab 레포의 소스 코드를 정적분석 + LLM 해석으로 정리해 **조치 가능한 GitLab Issue** 로 내놓고, 다른 한쪽은 운영 중인 AI 서비스의 응답을 Golden Dataset 으로 자동 채점해 **품질 회귀 리포트** 를 내놓습니다. 외부 AI 서비스를 사용하지 않으며, 모든 LLM 추론은 사내 하드웨어(호스트 Ollama)에서 실행됩니다.

### 1.1 현재 실무에서의 어려움

본 스택이 해소하는 다섯 가지 반복 비효율:

**문제 1 — 우선순위 판단 불가.** 대규모 레포를 1회 스캔하면 수백에서 수천 건의 이슈가 쏟아집니다. 어느 이슈부터 손대야 하는지, 어떤 것이 실제 서비스에 영향을 주는지 판단하려면 사람이 직접 코드를 읽어봐야 합니다.

**문제 2 — 맥락 정보의 부재.** 각 이슈는 "bare except 금지" 같은 **규칙 위반 사실**만 알려줍니다. "이 함수가 어디서 호출되는가", "수정하면 무엇이 깨지는가", "팀의 다른 코드는 이 규칙을 어떻게 처리했는가" 같은 **맥락 정보**는 제공되지 않아, 개발자가 매번 IDE 를 열어 조사해야 합니다.

**문제 3 — 오탐(False Positive) 처리 비용.** 이슈의 상당수는 실제로는 문제가 아닌 오탐이지만, 판정을 위해 사람이 일일이 코드를 추적해야 합니다. 수백 건 중 실제 조치가 필요한 것만 골라내는 과정에서 상당한 시간이 소모됩니다.

**문제 4 — 추적 분산.** 이슈는 SonarQube 대시보드에, 작업 지시는 GitLab Issue 에, 논의는 사내 메신저에 흩어집니다. 한 건을 추적하려면 세 곳을 오가야 합니다.

**문제 5 — AI 응답 품질 회귀 감지 부재.** 사내 챗봇·RAG 시스템·LLM 워크플로는 모델 교체·프롬프트 수정·RAG 데이터 변경 시마다 답변 품질이 달라지지만, 품질 저하를 사전 감지할 자동화 수단이 없습니다. 대개 사용자 불만이 쌓여야 알게 되며, 품질 검증을 사람이 샘플 질문으로 매번 반복하는 비용이 큽니다.

### 1.2 이 스택의 접근 방식

**5 개의 Jenkins Pipeline** 이 위 다섯 문제를 담당합니다. 문제 1~4 (코드 품질) 는 `01` 체인 1회 클릭으로 `02`→`03`→`04` 이 자동 연쇄 실행되어 해소되고, 문제 5 (AI 품질) 는 `05` 를 별도 주기로 실행해 해소합니다.

| 파이프라인 | 트리거 · 주기 | 수행 작업 | 최종 산출 효과 |
|-----------|-------------|----------|---------------|
| **01 코드 분석 체인** | 커밋 SHA 1개 지정 (수동 / webhook) | `02`→`03`→`04` 을 단일 커밋 SHA 기준으로 순차 실행하고 결과를 집계. | 개발자는 **한 번의 버튼 클릭**으로 끝. 중간 단계를 개별 실행할 필요가 없음. |
| **02 코드 사전학습** | `01` 이 내부적으로 호출 | 대상 레포의 모든 소스를 함수·메서드 단위로 분해하고, 각 조각에 "이 함수가 하는 일" 요약을 붙여 검색용 지식 창고(Dify Knowledge Base)에 적재. | 이후 단계에서 "비슷한 코드 / 호출 관계" 를 자동으로 검색해 가져올 수 있는 **맥락 데이터베이스 확보**. |
| **03 코드 정적분석** | `01` 이 내부적으로 호출 | 지정 커밋 스냅샷에 대해 SonarQube 스캐너를 실행. | 규칙 위반 사항 목록이 확정됨 (기존 Sonar 사용 방식과 동일). |
| **04 정적분석 결과분석 이슈등록** | `01` 이 내부적으로 호출 | ① Sonar 이슈에 **사실 정보 보강**(파일/함수/호출관계/커밋이력), ② 01 의 지식 창고에서 **관련 코드 자동 검색**, ③ LLM 에게 "진짜 문제인가, 어떻게 고칠까" 판단 요청, ④ **오탐은 Sonar 에 자동 마킹**, ⑤ 진짜 문제는 **GitLab Issue 로 정리해 등록**. | 개발자에게 도달하는 것은 **조치 가능한 Issue 만**. 오탐 걸러내기·맥락 조사·수정안 초안 작성까지 자동화 완료. |
| **05 AI 평가** | 평가 주기 (빌드마다 / 일 1회 / 배포 전) | 운영 중 AI 서비스(사내 챗봇·RAG·LLM 워크플로)에 Golden Dataset(시험지)의 질문을 자동 발송하고 답변을 받아, 11 개 지표(정책 준수·포맷·과제 완수·답변 관련성·환각·RAG 정확성·다중턴 일관성·응답속도·토큰 사용량 등)로 **다른 LLM 을 심판으로 삼아** 자동 채점. | 동일 시험지로 빌드마다 재평가해 **AI 품질 회귀 자동 감지**. 리포트(`summary.html`) 에 LLM 이 직접 실패 원인·권장 조치를 자연어로 작성해 비개발자도 결과 해석 가능. |

00~03 은 "코드 품질 체인" (실행 주기: 커밋마다), 04 는 "AI 품질 게이트" (실행 주기: 평가 정책에 따라). 두 흐름은 동일 통합 이미지에서 공존하며 서로 간섭하지 않습니다.

### 1.3 개발자·운영자가 받게 되는 결과물

두 가지 산출물이 생성되며, 각각 역할이 다릅니다.

**① 코드 품질 (`00~03`) — GitLab Issue (PM 친화 본문 구조)**

파이프라인이 종료되면 GitLab 프로젝트의 Issues 페이지에 **조치가 필요한 항목만** 새 Issue 로 등록되어 있습니다. 본문은 **PM 가 첫 줄 신호등만 봐도 우선순위 판단이 가능**하도록 재설계되었으며, 개발자용 코드/Rule/링크는 모두 펼침 섹션 안으로 들어가 PM 시야를 가리지 않습니다.

| Issue 섹션 | 담고 있는 것 | 생성 주체 |
|-----------|-------------|:---------:|
| 🚦 **Action Verdict 신호등** | "🔴 즉시 수정 / 🟡 검토 후 수정 / ⚪ 오탐 가능성 / 🟢 무시 가능 (기술 부채)" 중 하나를 한 줄로. severity + classification + confidence + RAG 근거 정황을 종합 | 자동 |
| 📌 무엇이 문제인가 | LLM 영향 분석의 첫 문장 + 비즈니스 영향 한 줄 | LLM 한 줄 추출 |
| 🎯 어디서 발생하나 | "`src/auth/login.py` (line 42) 의 `verifyPassword` 함수" 자연어 + HTTP route / decorator / 매개변수명 정적 컨텍스트 | 사실 + tree-sitter |
| ⚠️ 영향과 이유 | "이 함수는 X 에서 호출되므로 Y 에 영향" 같은 파급 효과 해석 | LLM (RAG 기반) |
| 🛠️ 어떻게 고치나 | 자연어 수정 방향 + (옵션) 그대로 적용 가능한 unified diff | LLM |
| 🔍 **AI 판단 근거 (NEW)** | "AI 가 받은 청크 N 개 중 M 개 인용 · 사전학습 정적 메타 K 회 활용 · HTTP route, decorator, 매개변수명 인용" — 사전학습 → 답변 인과를 자연어로 노출 | 자동 (RAG 진단) |
| 📂 같은 패턴의 다른 위치 | 같은 규칙으로 다른 파일에서도 같은 문제가 발견되면 일괄 표 (조건부) | 자동 집계 |
| 📖 **기술 상세 (▶ 접기)** | 코드 블록 (이슈 라인 ±10 줄) · 파일/함수/Rule/Severity/Commit 표 · Rule 원문 · SonarQube/GitLab 링크 — **모두 펼침 안** | 사실 + Sonar |
| 라벨 | 심각도 / 진짜문제·오탐 구분 / 확신도 / partial_citation 자동 강등 등 | LLM + 자동 |

PM 은 본문 첫 신호등 + "무엇이 / 어디서 / 영향" 만 읽으면 **우선순위·담당 배정** 결정 가능. 개발자는 펼침 섹션을 열어 코드/Rule/링크까지 따라가면 됩니다. IDE·Sonar 대시보드·팀 메신저를 오갈 필요가 없습니다.

**② AI 응답 품질 (`05`) — `summary.html` / `summary.json` 리포트**

Jenkins 빌드 Artifact 로 빌드마다 `build-<N>/summary.html` (+ `summary.json`) 이 생성됩니다. 운영자/QA 가 다른 도구 없이 이 리포트 한 장으로 "이번 빌드 배포해도 되는가" 를 판단할 수 있도록 설계.

| 리포트 섹션 | 담고 있는 것 | 생성 주체 |
|------------|-------------|:---------:|
| 🤖 LLM 임원 요약 | "이번 빌드는 82% 통과, 주원인은 Faithfulness 부족 2건" 같은 한 단락 총평 + 권고 (pass/investigate/block) | LLM |
| 빌드 메타데이터 | 어느 심판 모델(+digest)이 어느 시험지(sha256)로 채점했는지 | 사실 정보 |
| 11 지표 카드 대시보드 | 정책·포맷·과제완수·답변관련성·환각 등 각 지표 통과율·평균·분포 히스토그램 | 지표 집계 |
| Conversation 드릴다운 | 각 케이스의 질문·기대답변·실제답변·지표별 판정·실패 시 LLM 쉬운 해설 | 테스트 + LLM |
| 실패 분류 | "시스템 에러 vs 품질 실패" 분리 (인프라 문제와 AI 품질 문제 구별) | 자동 |

운영자는 `summary.html` 1장으로 **"이 빌드 배포해도 되나 / 어디서 문제가 났나"** 를 결정할 수 있습니다. 모든 판정 근거(Judge reason)와 숫자가 리포트 안에 있어 QA 회의에 즉시 활용.

### 1.4 기대 효과

코드 품질과 AI 품질 양쪽에서 도입 전/후가 어떻게 달라지는지:

| 영역 | 항목 | 기존 프로세스 | 본 스택 도입 후 |
|------|------|-------------|----------------|
| **코드 품질** | 이슈 수신 후 1차 분류 | 개발자가 수십 분간 코드 읽고 오탐 걸러냄 | LLM 이 자동 판정·마킹. 오탐은 개발자에게 도달하지 않음 |
| | 영향 범위 파악 | IDE 열어 호출처 직접 추적 | Issue 본문의 "영향 분석" 섹션에서 즉시 확인 |
| | 수정 방향 결정 | 문서·규칙 설명 직접 조사 | LLM 의 수정 제안 + Diff 를 검토 후 적용 여부 판단 |
| | 같은 패턴 반복 이슈 | 각각 별도 이슈로 수십 건 쌓임 | 하나의 대표 Issue + "동일 패턴 다른 위치" 표로 통합 |
| | 이슈 추적 위치 | SonarQube · GitLab · 메신저 분산 | GitLab Issues 한 곳으로 통합 |
| **AI 품질** | 답변 품질 회귀 감지 | 사용자 불만이 쌓여야 알게 됨 | 빌드마다 동일 시험지 재실행 → 품질 저하 즉시 경보 |
| | 평가 일관성 | 사람이 샘플 질문으로 주관적 채점 | 표준 메트릭(Answer Relevancy·Faithfulness 등) 11종으로 일관 채점 |
| | 판정 결과 해석 | 점수만 나오고 원인 추적 수동 | LLM 이 실패 케이스마다 "왜 떨어졌는가 + 무엇을 바꾸면 좋은가" 를 자연어로 작성 |
| | 외부 클라우드 의존 | ChatGPT/Gemini API 로 평가 시 비용·유출 위험 | 호스트 Ollama 만 사용 — 외부 API 0, 데이터 반출 0 |

### 1.5 외부 AI 서비스 미사용 — 에어갭 적합성

- ChatGPT, Gemini 등 외부 LLM API 를 사용하지 않으며, 소스 코드가 외부로 반출되지 않습니다.
- 모든 LLM 추론은 **호스트 Ollama 데몬**에서 수행합니다 (`gemma4:e4b`, `bge-m3`, `qwen3-coder:30b`).
- 초기 자산 반입이 완료되면 인터넷 차단 환경에서 **반복 사용에 제약이 없습니다**.
- 호출 건수에 따른 API 과금이 없으며, 호스트 하드웨어(Apple Metal / NVIDIA CUDA) 자원만으로 처리됩니다.

### 1.6 기술적 실행 개요

두 흐름이 동일 통합 이미지에서 공존합니다.

**흐름 A — `01 코드 분석 체인` (`02`→`03`→`04`)**: 커밋 SHA 1 개 기준 자동 연쇄.

1. 커밋 SHA 해석 (`git ls-remote` 또는 파라미터)
2. tree-sitter 로 함수 단위 AST 청킹 → Ollama bge-m3 임베딩 → Dify Knowledge Base 적재
3. SonarQube 스캔 실행
4. 각 Sonar 이슈에 대해 Dify Workflow 호출 (멀티쿼리 RAG + severity 라우팅)
5. GitLab Issue 자동 등록 (위치·코드·수정제안·영향분석·링크 포함)
6. 오탐 판정 건은 SonarQube 에 자동 전이 시도, 실패 시 라벨로 구분하여 Issue 생성 (Dual-path)

**흐름 B — `05 AI 평가`**: 운영 중 AI 서비스에 대한 독립 회귀 평가.

1. Golden Dataset(시험지 CSV) 로드 → 심판 모델·대상 모델·데이터셋 sha256 메타데이터 고정
2. 각 케이스(대화)에 대해 어댑터 선택 — `local_ollama_wrapper` / `http` / `ui_chat` 중 `TARGET_TYPE` 에 따라
3. 대상 AI 에 질문 발송 → 응답 수집 → 정책·포맷 게이트(①②) 결정론적 통과 확인
4. 심판 LLM 호출 → 과제 완수·답변 관련성·환각·RAG 정확성·다중턴 일관성 등 지표별 채점
5. 응답 시간·토큰 사용량 실측 기록 (합/불 없음)
6. `summary.json` 누적 → `summary.html` 렌더 (LLM 임원 요약 + 지표 카드 + 실패 드릴다운)

모든 LLM 추론 — 흐름 A 의 Dify Workflow 및 흐름 B 의 심판·대상 — 은 **호스트 Ollama** 에서 처리되어 외부 의존이 없습니다.

### 1.7 구성 요소 (단일 컨테이너 + GitLab 별도)

| 역할 | 서비스 | 통합 컨테이너 내부 포트 | 호스트 매핑 |
|:---:|---|:---:|:---:|
| CI 오케스트레이터 | Jenkins | 28080 | 28080 |
| LLM Workflow | Dify | 28081 (nginx gateway) | 28081 |
| 정적분석 | SonarQube | 9000 | 29000 |
| Vector DB | Qdrant | 6333 (내부) | — |
| 메타 DB | PostgreSQL | 5432 (내부) | — |
| 큐 | Redis | 6379 (내부) | — |
| 소스 호스팅 | **GitLab (별도 컨테이너)** | 80 / 22 | 28090 / 28022 |
| LLM 추론 | **호스트 Ollama** | 11434 | — (컨테이너 → `host.docker.internal:11434`) |

---

## 2. 에어갭 배포 전체 그림

```
┌────────────────────────────────────────────────────────────────┐
│  ① 온라인 준비 머신  (인터넷 필요, 최초 1회)                    │
│  ────────────────────────────────                              │
│  · 이 레포 clone                                                │
│  · scripts/download-plugins.sh   (Jenkins + Dify 플러그인 번들) │
│  · scripts/offline-prefetch.sh   ← 통합 이미지 1개로 빌드       │
│      ├─ Dockerfile FROM 의 베이스 이미지 5종을 모두 흡수        │
│      └─ ttc-allinone + gitlab tarball 2개만 산출                │
│  · Ollama 모델 3종 반출 (gemma4:e4b, bge-m3, qwen3-coder:30b)   │
│  · Docker Desktop / Ollama installer 도 함께 받아둠             │
└────────┬───────────────────────────────────────────────────────┘
         │
         ▼ ② 반출 매체 (USB / NAS / 외장SSD)
┌────────────────────────────────────────────────────────────────┐
│  반출 패키지 구성                                                │
│  · code-AI-quality-allinone/ 폴더 전체                          │
│    └── offline-assets/<arch>/                                   │
│        ├── ttc-allinone-<arch>-<tag>.tar.gz   (~10GB)           │
│        └── gitlab-*.tar.gz                    (~1.5GB)          │
│    └── jenkins-plugins/*.jpi                  (~40MB)           │
│    └── dify-plugins/*.difypkg                 (~1MB)            │
│  · ollama-models/                             (~25GB)           │
│  · docker-desktop installer                   (~800MB)          │
│  · ollama installer                           (~100MB)          │
└────────┬───────────────────────────────────────────────────────┘
         │
         ▼ ③ 오프라인 운영 머신  (인터넷 없음)
┌────────────────────────────────────────────────────────────────┐
│  · Docker Desktop 오프라인 설치                                  │
│  · Ollama 오프라인 설치 + 모델 복원                              │
│  · offline-load.sh 로 tarball 2개 docker load                   │
│  · run-{mac,wsl2}.sh 로 compose up                              │
│  · 자동 프로비저닝 완주 후 Jenkins UI 접속                       │
└────────────────────────────────────────────────────────────────┘
```

전체 시간 (참고):
- 온라인 준비: **~45분** (모델 다운로드 포함, 네트워크 속도에 따라)
- 반출 매체 이동: 매체 속도 + 용량 (총 ~40GB)
- 오프라인 설치: **~25분** (docker load 10분 + provision 자동 7분 + Ollama 모델 복원 5분)

---

## 3. 온라인 준비 머신 — 자산 수집

> 📋 **이 섹션은 "따라 하기"** — 처음부터 끝까지 순서대로 실행하세요. 각 Step 은 이전 Step 의 결과물을 전제로 합니다. 이 섹션을 완주하면 오프라인 머신으로 옮길 **반출 패키지 1 벌** 이 준비됩니다 (≈ 45 분).

### 3.0 오프라인 빌드/구동 전 준비물 체크리스트

오프라인 현장에 들어가기 전에 아래 준비물이 모두 있는지 먼저 확인하세요. 실제 운영 실패는 대부분 "이미지는 준비했는데 모델이 없음", "설치 파일은 있는데 GitLab tarball 이 없음", "아키텍처가 다름" 같은 누락에서 발생합니다.

**사람이 준비해야 하는 것**

| 분류 | 준비물 | 왜 필요한가 |
|------|--------|-------------|
| 하드웨어 | 온라인 준비 머신 1대 | 인터넷에서 이미지/모델/설치 파일을 받기 위해 필요 |
| 하드웨어 | 오프라인 운영 머신 1대 | 실제 구동 대상 |
| 저장매체 | USB / NAS / 외장 SSD (권장 64GB 이상) | 반출 패키지 전달용 |
| 시간 | 온라인 준비 45분 내외, 오프라인 복원 25분 내외 | 모델 다운로드와 docker load 시간이 길다 |

**파일/자산 준비물**

| 분류 | 반드시 필요한 항목 | 예상 크기 |
|------|-------------------|----------|
| 레포 | `code-AI-quality-allinone/` 폴더 전체 | 수백 MB 이하 |
| 통합 이미지 | `offline-assets/<arch>/ttc-allinone-*.tar.gz` | 약 10GB |
| GitLab 이미지 | `offline-assets/<arch>/gitlab-*.tar.gz` | 약 1.5~1.7GB |
| Jenkins 플러그인 | `jenkins-plugins/*.jpi` | 약 40MB |
| Dify 플러그인 | `dify-plugins/*.difypkg` | 약 1MB |
| Ollama 모델 | `gemma4:e4b`, `bge-m3`, `qwen3-coder:30b` 가 들어있는 `~/.ollama/models/` | 약 25GB |
| 설치 파일 | Docker Desktop / Ollama installer | 약 1GB |

**운영 전에 반드시 맞춰야 하는 조건**

1. 온라인 준비 머신과 오프라인 운영 머신의 아키텍처가 같아야 합니다.
2. 운영 머신에 Docker 와 Ollama 를 오프라인 설치할 수 있어야 합니다.
3. 운영 머신의 Docker Desktop 메모리가 최소 12GB 이상이어야 합니다.
4. 운영 머신에서 `host.docker.internal:11434` 로 호스트 Ollama 접근이 가능해야 합니다.
5. 반출 매체에서 직접 실행하지 말고, 운영 머신 로컬 디스크로 먼저 복사해야 합니다.

### 3.0.1 최종 반출 패키지 목록

온라인 준비가 끝났을 때 최종적으로 챙겨야 하는 항목은 아래입니다.

```text
<반출매체 루트>/
├── code-AI-quality-allinone/
│   ├── offline-assets/<arch>/
│   │   ├── ttc-allinone-<arch>-<tag>.tar.gz
│   │   ├── ttc-allinone-<arch>-<tag>.meta
│   │   ├── gitlab-gitlab-ce-18.11.0-ce.0-<arch>.tar.gz
│   │   └── gitlab-gitlab-ce-18.11.0-ce.0-<arch>.meta
│   ├── jenkins-plugins/
│   ├── dify-plugins/
│   ├── scripts/
│   ├── docker-compose.mac.yaml
│   ├── docker-compose.wsl2.yaml
│   └── README.md
├── ollama-models/
│   └── models/...
└── installers/
    ├── Docker.dmg                      # macOS 인 경우
    ├── Ollama-darwin.zip              # macOS 인 경우
    ├── Docker-Desktop-Installer.exe   # Windows 인 경우
    ├── OllamaSetup.exe                # Windows 인 경우
    └── ollama-linux-amd64             # Linux 인 경우
```

현장 반입 직전에는 아래 5개만 빠르게 다시 확인하세요.

1. `ttc-allinone-*.tar.gz` 가 있다.
2. `gitlab-*.tar.gz` 가 있다.
3. `~/.ollama/models/` 를 복사한 `ollama-models/` 가 있다.
4. 운영 OS 에 맞는 installer 가 있다.
5. `README.md` 와 `scripts/offline-load.sh`, `scripts/run-mac.sh` 또는 `scripts/run-wsl2.sh` 가 있다.

### 3.1 온라인 준비 머신 요구사항

| 항목 | 권장 |
|------|------|
| OS | macOS (Apple Silicon / Intel) 또는 Linux (amd64) |
| Docker Desktop | ≥ 25.x |
| Docker 메모리 할당 | ≥ 12 GB |
| 여유 디스크 | ≥ **80 GB** (이미지 빌드 + tarball 산출 + Ollama 모델) |
| 인터넷 | Docker Hub + GitHub + ollama.com + marketplace.dify.ai 접근 |

**중요**: 오프라인 운영 머신과 **같은 아키텍처**에서 빌드해야 합니다 (arm64 ↔ arm64, amd64 ↔ amd64). Apple Silicon 운영 → Apple Silicon 준비 / WSL2 운영 → amd64 Linux 준비.

### 3.2 Step 1: 레포 받기 + 작업 디렉터리 설정

**이 단계에서 하는 일**: 본 스택의 소스 코드를 온라인 준비 머신에 내려받아, 이후 모든 반출 자산의 작업 기준점이 될 `code-AI-quality-allinone` 폴더로 이동합니다. 이 폴더가 §3.3 부터 §3.6 까지의 모든 명령이 실행되는 **작업 디렉터리** 가 됩니다.

```bash
# 레포 clone + 작업 디렉터리 진입
git clone <이 레포 URL> airgap-test-toolchain
cd airgap-test-toolchain
cd code-AI-quality-allinone
```

**기대 결과** — `pwd` 출력이 `.../code-AI-quality-allinone` 로 끝나고, `ls` 를 쳤을 때 `Dockerfile`, `docker-compose.mac.yaml`, `scripts/`, `pipeline-scripts/`, `eval_runner/` 등이 보이면 정상.

**이후 온라인 준비 머신의 모든 명령은 이 폴더 안에서 실행합니다.** 셸을 새로 열 때마다 `cd` 로 다시 이동해야 합니다.

### 3.3 Step 2: Docker Desktop + Ollama installer 미리 받기

**이 단계에서 하는 일**: 오프라인 머신은 인터넷이 없으므로 Docker Desktop 과 Ollama 설치 파일 자체도 반출 매체에 넣어 가야 합니다. 지금 이 단계에서 운영 머신 OS 에 맞는 installer 를 미리 다운로드해 `installers/` 폴더에 모아둡니다.

> **운영 머신에 이미 Docker / Ollama 가 깔려있다면 이 Step 은 건너뛰어도 됩니다.**

**본인이 준비하는 운영 머신의 OS 를 확인한 뒤, 해당 분기 명령만 실행하세요** — 3 개 다 받을 필요 없습니다.

```bash
# 작업 디렉터리에 installers/ 폴더 생성
mkdir -p installers

# === 분기 ① macOS (Apple Silicon 운영 머신용) ===
curl -fL -o installers/Docker.dmg \
  "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
curl -fL -o installers/Ollama-darwin.zip \
  "https://ollama.com/download/Ollama-darwin.zip"

# === 분기 ② Windows (WSL2 amd64 운영 머신용) ===
curl -fL -o installers/Docker-Desktop-Installer.exe \
  "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
curl -fL -o installers/OllamaSetup.exe \
  "https://ollama.com/download/OllamaSetup.exe"

# === 분기 ③ Linux amd64 운영 머신용 (Ollama 바이너리만) ===
curl -fL -o installers/ollama-linux-amd64 \
  "https://ollama.com/download/ollama-linux-amd64"
chmod +x installers/ollama-linux-amd64
```

**기대 결과** — `ls -lh installers/` 실행 시 본인 OS 의 파일 2 개(Linux 는 1 개)가 각각 수백 MB 이상으로 보여야 정상.

### 3.4 Step 3: Ollama 모델 3 종 다운로드 및 반출 준비

**이 단계에서 하는 일**: 본 스택의 LLM 추론은 전부 호스트 Ollama 에서 수행됩니다. 오프라인 머신에서는 인터넷이 없어 `ollama pull` 이 불가능하므로, 지금 준비 머신에서 모델을 받아 **`~/.ollama/models/` 디렉터리를 통째로 반출 매체에 복사**할 것입니다.

> **역할 분담** — 모델 바이너리를 Ollama 에 적재하는 것은 사용자 책임이지만, Dify 에 Ollama 플러그인을 설치하고 provider/embedding 을 등록하고 Workflow 를 publish 하는 것은 전부 §6 에서 `provision.sh` 가 자동 수행합니다. 사용자가 Dify UI 에서 모델 이름을 입력할 필요 없음.

**Ollama 설치 + 기동** (준비 머신에 이미 있으면 건너뛰기):

```bash
# 준비 머신에 Ollama 설치
brew install ollama                              # macOS
# 또는
curl -fsSL https://ollama.com/install.sh | sh    # Linux

# 데몬 기동
ollama serve &
# (macOS 는 brew services start ollama 권장)
```

**필수 모델 3 종 pull** (총 ≈ 25 GB, 네트워크 속도에 따라 20~40 분):

```bash
ollama pull gemma4:e4b          # Dify Workflow 기본 LLM        ~4 GB
ollama pull bge-m3              # Dify 임베딩                   ~1 GB
ollama pull qwen3-coder:30b     # BLOCKER/CRITICAL 이슈 라우팅용 ~20 GB
```

**기대 결과** — 다음 명령으로 3 개 모델이 전부 잡히면 정상:

```bash
curl http://localhost:11434/api/tags | python3 -m json.tool
# → "models" 배열에 "gemma4:e4b", "bge-m3", "qwen3-coder:30b" 가 모두 있어야 함
```

**반출할 모델 디렉터리 경로** — 이 폴더 통째를 §4 의 반출 패키지에 넣습니다:

| OS | 경로 |
|----|------|
| macOS (brew) | `~/.ollama/models/` |
| Linux | `/usr/share/ollama/.ollama/models/` 또는 `~/.ollama/models/` |
| Windows | `%USERPROFILE%\.ollama\models\` |

> **용량이 너무 크다면** `qwen3-coder:30b` 는 선택입니다. 받지 않으면 CRITICAL 이슈가 `skip_llm` 템플릿으로 처리됩니다. `pipeline-scripts/sonar_issue_exporter.py` 의 `_SEVERITY_ROUTING` 을 수정해 모든 severity 를 `gemma4:e4b` 로 매핑하는 방법도 있습니다. `gemma4:e4b` + `bge-m3` 두 모델만으로도 End-to-end 동작합니다.

### 3.5 Step 4: Jenkins + Dify 플러그인 번들 수집

**이 단계에서 하는 일**: 통합 이미지 빌드 시 Dockerfile 이 Jenkins 플러그인(`.jpi`) 과 Dify Ollama 플러그인(`.difypkg`) 을 이미지 안으로 COPY 합니다. 이 플러그인들은 온라인에서만 받을 수 있으므로 지금 미리 다운로드해 둡니다.

**실행 명령** — 다운로드 스크립트 한 번 실행:

```bash
bash scripts/download-plugins.sh
```

**기대 결과** — 다음 파일/폴더 4 개가 작업 디렉터리 아래에 생성됩니다:

| 산출물 | 용도 | 크기 |
|--------|------|------|
| `jenkins-plugin-manager.jar` | Jenkins 플러그인 다운로드 헬퍼 | ≈ 7 MB |
| `jenkins-plugins/*.jpi` | Jenkins 플러그인 전체 (의존성 재귀 포함) | ≈ 40 MB |
| `dify-plugins/langgenius-ollama-*.difypkg` | Dify Ollama provider 플러그인 | ≈ 1 MB |
| `.plugins.txt` | 설치될 플러그인 목록 | ≈ 1 KB |

`ls jenkins-plugins/ | wc -l` 이 수십 개를 출력하면 성공. 0 이면 §12.4 참고.

### 3.6 Step 5: 통합 이미지 빌드 + tarball 산출

**이 단계에서 하는 일**: 지금까지 준비한 자산(소스 + Ollama 플러그인 + Jenkins 플러그인) + Dockerfile 의 베이스 이미지 5 종을 모두 흡수해 **단일 통합 Docker 이미지** 로 만듭니다. 그 이미지를 `docker save` 로 압축해 **오프라인 머신에 그대로 옮길 수 있는 tarball** 로 뽑습니다. 이 Step 이 §3 에서 가장 오래 걸리는 단계입니다 (≈ 9 분).

> **중요 — 폐쇄망 운영 기준에서는 `build-mac.sh` / `build-wsl2.sh` 만으로 충분하지 않습니다.**
> 이 스택은 `ttc-allinone` 외에 **별도 GitLab 런타임 컨테이너**도 함께 필요합니다. 따라서 폐쇄망 반출용 자산은 반드시 **`scripts/offline-prefetch.sh` 로 생성한 tarball 2개** (`ttc-allinone-*` + `gitlab-*`) 여야 합니다. 이 절차를 생략하고 `compose up` 을 실행하면 GitLab 이미지가 로컬에 없을 때 현장에서 `docker pull` 이 발생합니다. 이는 폐쇄망 운영 시나리오에 맞지 않습니다.

**본인 운영 머신 아키텍처에 맞는 분기 하나만 실행**:

```bash
# 분기 ① arm64 운영용 (macOS Apple Silicon)
bash scripts/offline-prefetch.sh --arch arm64

# 분기 ② amd64 운영용 (WSL2, x86 Linux)
bash scripts/offline-prefetch.sh --arch amd64
```

**이 명령이 실제로 하는 일** — 총 **50 build stages**, M1 Max / 12 GB Docker VM 기준 ≈ 9 분:

1. `docker buildx build` 로 `Dockerfile` 기준 통합 이미지 빌드. 빌드 중 `FROM` 의 **베이스 이미지 5 종** (`langgenius/dify-api:1.13.3`, `langgenius/dify-web:1.13.3`, `langgenius/dify-plugin-daemon:0.5.3-local`, `sonarqube:26.4.0.121862-community`, `jenkins/jenkins:2.555.1-lts-jdk21`) 이 자동 pull 되어 최종 이미지 layer 안에 모두 흡수됩니다.
2. 주요 stage 이정표 (진행 추적용): `pip install deepeval/langchain/pytest` → Playwright Chromium 설치 (stage ≈ 19/50) → Node v22 설치 (21/50) → Sonar + Jenkins plugins + Postgres init (30/50) → OCI export → tarball 압축 (50/50).
3. 완성 이미지를 `docker save` 로 tarball 압축.
4. GitLab 런타임 이미지(`gitlab-ce:18.11.0-ce.0` / arm64 는 `gitlab/gitlab-ce`)를 별도로 pull 후 두 번째 tarball 로 저장.

빌드 도중 `pip` resolver 의 `ERROR:` 경고가 몇 개 나올 수 있지만 비치명적 의존성 경고이며 빌드는 계속 진행됩니다. 무시해도 됩니다.

**기대 결과** — 아래 4 개 파일이 `offline-assets/<arch>/` 아래에 생성됩니다:

```text
offline-assets/<arch>/
├── ttc-allinone-<arch>-dev.tar.gz          ~10 GB   ← 베이스 5 종 포함 통합 이미지
├── ttc-allinone-<arch>-dev.meta                      sha256 + built_at
├── gitlab-gitlab-ce-18.11.0-ce.0-<arch>.tar.gz  ~1.7 GB ← 별도 GitLab 런타임
└── gitlab-gitlab-ce-18.11.0-ce.0-<arch>.meta
```

**핵심 이해** — 베이스 이미지들은 **통합 tarball 안에 이미 포함**되므로 오프라인 머신에 별도로 반출할 필요가 없습니다. 위 **두 tarball 만으로 완전**합니다. 단, `ttc-allinone` 은 통합 런타임 · `gitlab-*` 은 별도 서비스이므로 **반드시 함께** 반출해야 합니다.

**다음 단계** — §4 로 이동해 지금까지 만든 자산을 매체(USB / SSD / NAS) 에 모읍니다.

---

## 4. 에어갭 매체 구성 (반출 패키지)

USB / 외장 SSD / NAS 로 옮길 파일:

```
ttc-airgap-bundle/
├── code-AI-quality-allinone/                # 이 레포 폴더 전체
│   ├── Dockerfile
│   ├── docker-compose.mac.yaml              # arm64 운영 용
│   ├── docker-compose.wsl2.yaml             # amd64 운영 용
│   ├── scripts/
│   ├── pipeline-scripts/
│   ├── jenkinsfiles/
│   ├── jenkins-init/
│   ├── jenkins-plugins/                     # ← Step 3.5
│   ├── dify-plugins/                        # ← Step 3.5
│   ├── offline-assets/<arch>/               # ← Step 3.7
│   │   ├── ttc-allinone-<arch>-dev.tar.gz
│   │   └── gitlab-*.tar.gz
│   └── ... (기타 파일 전부)
│
├── ollama-models/                           # ← Step 3.4
│   └── ... (~/.ollama/models 의 전체 내용)
│
└── installers/                              # ← Step 3.3
    ├── Docker.dmg / Docker-Desktop-Installer.exe
    └── Ollama-darwin.zip / OllamaSetup.exe / ollama-linux-amd64
```

**총 용량** (참고):
- `code-AI-quality-allinone/` + `offline-assets/` ~12 GB
- `ollama-models/` ~25 GB (qwen3-coder 포함) / ~5 GB (qwen3-coder 제외)
- `installers/` ~1 GB

**무결성 검증용**:

```bash
# 준비 머신에서 체크섬 저장
cd ttc-airgap-bundle
find . -type f -name '*.tar.gz' -exec sha256sum {} \; > CHECKSUMS.sha256
sha256sum -b ollama-models/**/* 2>/dev/null > MODELS.sha256
```

오프라인 머신 도착 후:

```bash
sha256sum -c CHECKSUMS.sha256
```

---

## 5. 오프라인 운영 머신 — 런타임 설치

> 📋 **이 섹션은 "따라 하기"** — 반출 매체(USB/NAS/외장 SSD) 로 자산이 옮겨진 운영 머신에서 실행합니다. 이 머신은 **인터넷 접근이 없어야 정상** (airgap 환경). 모든 설치 파일 · 이미지 · 모델은 반출 매체에서만 복원합니다.
>
> 이 섹션 완주 시점에 Docker · Ollama · 모델 3 종 · 이 레포 전체가 운영 머신에 준비되어, §6 의 "스택 기동" 으로 진행할 수 있는 상태가 됩니다.

### 5.1 운영 머신 요구사항 (참고용 체크리스트)

| 항목 | 최소 | 권장 |
|------|------|------|
| CPU | arm64 (M1+) 또는 amd64 x86_64 | — |
| 메모리 | 16 GB (Docker 12 GB 할당) | 32 GB |
| 디스크 | 50 GB 여유 | 100 GB |
| OS | macOS 13+ / Windows 11 + WSL2 / Linux (커널 5.x+) | — |

**주의** — §3.1 의 온라인 준비 머신 아키텍처와 **반드시 일치** (arm64 → arm64, amd64 → amd64). 교차 빌드는 미지원.

### 5.2 Step 1: Docker Desktop 설치

**이 단계에서 하는 일**: 반출 매체에 담아온 Docker Desktop installer 를 운영 머신에 설치합니다. §6 에서 컨테이너를 기동하려면 Docker daemon 이 먼저 돌고 있어야 합니다.

> **이미 Docker 가 깔려 있다면** `docker version` 으로 동작 확인 후 이 Step 전체를 건너뛸 수 있습니다.

**본인 OS 에 맞는 분기 하나만 실행**:

**분기 ① macOS (Apple Silicon)**:

```bash
# 반출 매체의 Docker.dmg 마운트
open /Volumes/ttc-airgap-bundle/installers/Docker.dmg
# → Applications 폴더로 Docker.app 드래그 → 실행 → 초기 설정 중 "Skip" 선택
# → Docker Desktop 환경설정 → Resources → Memory 12 GB 이상, Disk 60 GB 이상 할당
```

**분기 ② Windows (WSL2)**:

반출 매체의 `installers\Docker-Desktop-Installer.exe` 더블클릭 → 설치 마법사 완주 → "WSL 2 backend" 체크 확인 → 재부팅 → WSL2 우분투 터미널 진입.

**분기 ③ Linux**: 사내 배포판에 맞는 Docker CE / Docker Desktop Linux 패키지를 이미 준비했다는 가정 (배포판별 편차가 커 본 가이드 범위 밖).

**기대 결과** — 다음 명령이 Client + Server 정보를 모두 출력하면 정상 설치:

```bash
docker version
# → Client: Docker Engine - ...
#   Server: Docker Engine - ...
```

`Cannot connect to the Docker daemon` 에러면 Docker Desktop GUI 가 실행 중인지 확인 후 재시도.

### 5.3 Step 2: Ollama 설치

**이 단계에서 하는 일**: 본 스택의 LLM 추론은 전부 호스트 Ollama 가 담당합니다. 다음 §5.4 에서 모델 바이너리를 복원하기 **전에 Ollama 데몬 자체를 먼저 설치**합니다.

**본인 OS 에 맞는 분기 하나만 실행**:

**분기 ① macOS**:

```bash
unzip /Volumes/ttc-airgap-bundle/installers/Ollama-darwin.zip
sudo mv Ollama.app /Applications/
open -a Ollama
# → 메뉴바에 Ollama 아이콘이 뜨면 데몬 기동 완료
```

**분기 ② Windows**: 반출 매체의 `installers\OllamaSetup.exe` 실행 → 설치 완료 후 시스템 트레이의 Ollama 아이콘 확인.

**분기 ③ Linux**:

```bash
sudo install -m 755 /media/usb/installers/ollama-linux-amd64 /usr/local/bin/ollama
ollama serve &       # 데몬 기동 (운영 환경은 systemd 유닛 등록 권장)
```

**기대 결과** — Ollama HTTP API 가 응답하면 정상:

```bash
curl http://localhost:11434/api/tags
# → {"models":[]}
# (모델은 §5.4 에서 복원할 것이므로 지금은 빈 배열이 정상)
```

### 5.4 Step 3: Ollama 모델 복원

**이 단계에서 하는 일**: §3.4 에서 온라인 준비 머신에 받아둔 `~/.ollama/models/` 디렉터리 전체를 운영 머신의 같은 위치로 옮깁니다. 이것으로 `gemma4:e4b` · `bge-m3` (· 선택 `qwen3-coder:30b`) 모델이 오프라인 환경에서 사용 가능해집니다.

**절차** — Ollama 데몬을 먼저 끄고, 모델 디렉터리를 덮어쓴 뒤, 다시 켭니다 (덮어쓰기 중 데몬이 인덱스를 잡고 있으면 충돌 가능).

**분기 ① macOS / Linux**:

```bash
# ① Ollama 데몬 중지
launchctl unload ~/Library/LaunchAgents/com.ollama.*.plist 2>/dev/null || true
pkill -f 'ollama serve' || true

# ② 모델 디렉터리 복원 (반출 매체 → 홈 디렉터리)
mkdir -p ~/.ollama
rsync -av /Volumes/ttc-airgap-bundle/ollama-models/ ~/.ollama/models/
# (Linux 시스템 Ollama 는 /usr/share/ollama/.ollama/models/ 경로로 rsync)

# ③ Ollama 재기동
open -a Ollama                          # macOS
# 또는
ollama serve &                          # Linux
```

**분기 ② Windows** (PowerShell 관리자):

```powershell
# ① Ollama 서비스 중지
Stop-Service -Name Ollama -ErrorAction SilentlyContinue

# ② 모델 디렉터리 복원 (E: 는 반출 매체 드라이브)
Copy-Item -Path "E:\ttc-airgap-bundle\ollama-models\*" `
          -Destination "$env:USERPROFILE\.ollama\models\" -Recurse -Force

# ③ Ollama 재기동
Start-Process -FilePath "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
```

**기대 결과** — 아래 두 명령 중 하나로 모델이 적재됐는지 확인:

```bash
# 빠른 확인: 모델 목록
curl http://localhost:11434/api/tags | python3 -m json.tool
# → "models" 배열에 "gemma4:e4b", "bge-m3" 가 보여야 함

# 실제 추론 확인 (옵션, 1~2 분 소요)
ollama run gemma4:e4b "hello"
```

> **여기까지가 사용자 책임의 끝** — Ollama 에 모델 바이너리만 적재되어 있으면 됩니다. 이후 §6 에서 스택을 기동하면 `provision.sh` 가 자동으로 Dify 에 Ollama 플러그인을 설치하고 `gemma4:e4b` / `bge-m3` 을 provider/embedding 으로 등록하며 workspace 기본 모델까지 지정합니다. Dify UI 에서 수동 설정은 필요 없습니다.

**추가 점검 — Docker 컨테이너가 호스트 Ollama 에 도달 가능한지**:

§6 에서 컨테이너가 기동되면 `host.docker.internal:11434` 로 호스트 Ollama 에 접속합니다. 지금 미리 확인하면 §6 진입 후 트러블슈팅 시간을 아낄 수 있습니다.

```bash
# 컨테이너 → 호스트 Ollama 도달 테스트 (모델 목록 응답 오면 OK)
docker run --rm curlimages/curl:latest \
  curl -sf http://host.docker.internal:11434/api/tags
```

- **macOS / Windows Docker Desktop**: `host.docker.internal` 자동 해석됨. 위 명령이 바로 작동해야 정상.
- **Linux 네이티브 Docker**: `docker-compose.*.yaml` 에 `extra_hosts: ["host.docker.internal:host-gateway"]` 추가 필요 (기본 compose 파일에 이미 포함되어 있음).
- **macOS 에서 Ollama 가 `localhost` 만 listen 중이면** (기본 설정):

  ```bash
  # Ollama 를 모든 네트워크 인터페이스에서 listen 하게 변경
  launchctl setenv OLLAMA_HOST "0.0.0.0"
  # Ollama 재기동 필요
  ```

위 명령이 실패하면 §12.1 트러블슈팅 참고.

### 5.5 Step 4: 반출한 레포 + 이미지 tarball 을 로컬 디스크로 복사

**이 단계에서 하는 일**: 반출 매체에 담긴 `code-AI-quality-allinone/` 폴더 (소스 + tarball + 플러그인 일체) 를 운영 머신 로컬 디스크로 옮깁니다. **매체에서 직접 실행하지 마세요** — 이미지 load 시 수 GB I/O 가 발생하는데 USB 는 느리고 끊김이 있어 `docker load` 실패 가능성이 높습니다.

```bash
# 반출 매체 → 운영 머신 홈 디렉터리로 복사 (매체 포맷이 macOS 일 때 경로)
cp -a /Volumes/ttc-airgap-bundle/code-AI-quality-allinone  ~/code-AI-quality-allinone
cd ~/code-AI-quality-allinone
```

**기대 결과** — 2 개 tarball 이 예상 크기로 위치해 있어야 정상:

```bash
ls -lh offline-assets/<arch>/
# → ttc-allinone-<arch>-dev.tar.gz   ≈ 10 GB
#   gitlab-gitlab-ce-18.11.0-ce.0-<arch>.tar.gz  ≈ 1.7 GB
```

둘 중 하나라도 없거나 크기가 비정상(수 MB 수준)이면 반출 매체에서 복사가 중간에 끊긴 것 — 다시 `cp -a` 수행.

**다음 단계** — §6 "스택 기동" 으로 이동.

---

## 6. 오프라인 머신에서 스택 기동

> 📋 **이 섹션은 "따라 하기"** — §5 까지 완료된 운영 머신에서 4 단계만 거치면 Jenkins · Dify · SonarQube · GitLab 이 전부 올라간 상태가 됩니다. 각 단계의 의미와 예상 결과를 단계별로 설명하므로 순서대로 따라가면 ≈ 20 분 안에 "Jenkins UI 에 접속해 첫 빌드를 누를 수 있는 상태" 에 도달합니다.

### 6.1 Step 1: Docker 이미지 복원 (tarball → docker image)

**이 단계에서 하는 일**: §3.6 에서 준비한 2 개 tarball (`ttc-allinone-*.tar.gz` + `gitlab-*.tar.gz`) 을 운영 머신의 Docker 이미지 레지스트리에 적재합니다. Docker 는 네트워크 접근 없이도 로컬 tarball 에서 이미지를 불러올 수 있습니다.

**실행 명령** — 작업 디렉터리로 이동 후 헬퍼 스크립트 실행 (권장):

```bash
cd ~/code-AI-quality-allinone

# 본인 아키텍처에 맞는 하나만 실행
bash scripts/offline-load.sh --arch arm64          # macOS Apple Silicon
bash scripts/offline-load.sh --arch amd64          # Windows WSL2 / Linux
```

`offline-load.sh` 는 `offline-assets/<arch>/` 의 두 tarball 을 `gunzip | docker load` 로 순차 로드합니다.

> **중요 — 여기서 GitLab tarball까지 함께 load 되어야만** 다음 단계 `docker compose up` 이 네트워크 접근 없이 완료됩니다. `ttc-allinone` 만 load 하고 `gitlab-*` tarball 을 빼먹으면 Compose 가 GitLab 이미지를 찾지 못해 pull 을 시도할 수 있습니다.

**기대 결과** — 아래 명령으로 두 이미지가 모두 보이면 정상 (≈ 10 분 소요, tarball 크기 때문):

```bash
docker images | grep -E "ttc-allinone|gitlab"
# → ttc-allinone              arm64-dev       10 GB
#   gitlab/gitlab-ce    18.11.0-ce.0    1.7 GB
```

**헬퍼 스크립트 대신 수동으로 하고 싶다면** (동일한 결과):

```bash
gunzip -c offline-assets/arm64/ttc-allinone-arm64-dev.tar.gz | docker load
gunzip -c offline-assets/arm64/gitlab-*.tar.gz | docker load
```

### 6.2 Step 2: 이미지 태그 정합성 맞추기 (compose 가 찾는 이름)

**이 단계에서 하는 일**: Docker Compose 는 고정된 이미지 태그 (`ttc-allinone:mac-dev` / `ttc-allinone:wsl2-dev`) 를 찾도록 설정되어 있지만, §3.6 의 `offline-prefetch.sh` 는 아키텍처 기반 태그 (`ttc-allinone:arm64-dev` / `ttc-allinone:amd64-dev`) 로 저장합니다. 이 태그 불일치를 맞춰주지 않으면 §6.3 의 `docker compose up` 이 `image not found` 로 실패합니다.

**둘 중 편한 방법 하나 선택** (옵션 A 권장 — 명시적이고 부작용 없음):

**옵션 A**: `docker tag` 로 별칭 추가

```bash
# macOS
docker tag ttc-allinone:arm64-dev ttc-allinone:mac-dev

# WSL2 / Linux
docker tag ttc-allinone:amd64-dev ttc-allinone:wsl2-dev
```

**옵션 B**: compose 실행 시 환경변수로 override (매번 같은 셸에서 실행해야 함)

```bash
export IMAGE=ttc-allinone:arm64-dev
# 이후 같은 셸에서 docker compose up 실행
```

**기대 결과** — 옵션 A 를 쓴 경우, 아래 명령으로 원래 태그와 별칭이 모두 잡히면 정상:

```bash
docker images ttc-allinone
# → ttc-allinone    arm64-dev    (이미지 ID)
#   ttc-allinone    mac-dev      (같은 이미지 ID — 별칭이라 동일)
```

### 6.3 Step 3: 스택 기동 (컨테이너 2 개 띄우기)

**이 단계에서 하는 일**: Docker Compose 로 **`ttc-allinone`** (Jenkins + Dify + SonarQube + Postgres + Redis + Qdrant 통합) + **`ttc-gitlab`** (GitLab CE) 두 컨테이너를 한 번에 기동합니다. 이 명령 1 회가 곧 "전체 스택을 켜는 스위치" — 명령 자체는 즉시 리턴하지만, 컨테이너 **내부에서는 §6.4 에서 지켜볼 자동 프로비저닝이 이 순간부터 백그라운드로 계속 진행**됩니다.

> **사전 체크**: 이 단계에 오기 전에 `docker images | grep -E "ttc-allinone|gitlab"` 결과에 **통합 이미지와 GitLab 이미지가 둘 다** 보여야 합니다. 둘 중 하나라도 없으면 `compose up` 시점에 로컬 이미지 부족으로 pull 이 발생할 수 있습니다.

**본인 OS 에 맞는 분기 하나만 실행** — `run-*.sh` 는 `docker compose` 명령을 감싼 헬퍼 (편의용). 둘 중 무엇을 써도 같은 결과:

```bash
# === 분기 ① macOS ===
bash scripts/run-mac.sh
# 또는 같은 효과
docker compose -f docker-compose.mac.yaml up -d

# === 분기 ② Windows WSL2 ===
bash scripts/run-wsl2.sh
# 또는 같은 효과
docker compose -f docker-compose.wsl2.yaml up -d
```

**기대 결과** — 30 초 이내에 두 컨테이너가 `Up` 상태여야 정상:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
# → ttc-allinone    Up 30 seconds
#   ttc-gitlab      Up 30 seconds (health: starting)
```

`ttc-gitlab` 의 `(health: starting)` 은 **정상** 표시입니다 — GitLab 이 내부에서 reconfigure 중이라 아직 healthcheck 를 통과하지 못한 상태이며, 수 분 안에 자동으로 `(healthy)` 로 바뀝니다. 이상 시 §12.7 참고.

**다음 단계** — 컨테이너는 올라왔지만 Jenkins Job · Dify Workflow · GitLab PAT 등은 아직 자동 생성되지 않은 상태입니다. §6.4 에서 이 프로비저닝 과정을 지켜봅니다.

### 6.4 Step 4: 자동 프로비저닝 대기 (≈ 7 분)

**이 단계에서 하는 일**: §6.3 에서 `ttc-allinone` 이 기동되는 순간 컨테이너 안의 `entrypoint.sh` 가 [`scripts/provision.sh`](scripts/provision.sh) 를 자동 실행합니다. 이 스크립트가 Dify 관리자 계정 setup · Ollama 플러그인 설치 · Workflow publish · GitLab root PAT 발급 · **샘플 GitLab 프로젝트(`realworld`) 자동 생성 + 초기 push** · SonarQube admin 비번 변경 · Jenkins Credentials 5 종 주입 · Jenkins Job 5 개 등록 등 **총 15 개 작업을 자동 완수**합니다. 이 모든 게 끝나야 Jenkins UI 에서 실제로 파이프라인을 돌릴 수 있는 상태가 됩니다.

> **왜 이 단계를 기다려야 하는가** — `docker compose up -d` 는 컨테이너 기동 직후 리턴하므로 "스택이 올라왔다" 는 잘못된 판단을 하기 쉽습니다. 실제로는 내부에서 프로비저닝이 7 분간 백그라운드로 계속 돌고 있으며, 완료 전에 Jenkins UI 에 접속해 Job 을 누르면 "Job 을 찾을 수 없음" / "credential 없음" 같은 오류가 납니다. §6.4 는 **이 내부 작업이 끝날 때까지 기다리는 게이트**.

**진행 상황 실시간 모니터링** (시각적 확인):

```bash
# provision 로그만 필터링해 실시간 출력 (Ctrl+C 로 종료)
docker logs -f ttc-allinone | grep -E "provision|entrypoint"
```

**자동화 스크립트에서 쓰려면** — `자동 프로비저닝 완료` 문자열이 로그에 나올 때까지 blocking 대기:

```bash
until docker logs ttc-allinone 2>&1 | grep -q "자동 프로비저닝 완료"; do
    sleep 15
done
echo "PROVISION_DONE"
```

**기대 결과** — 아래와 같은 "완료 로그" 블록이 출력되면 성공:

```text
[provision] 자동 프로비저닝 완료.
[provision]   Jenkins    : http://127.0.0.1:28080 (admin / password)
[provision]   Dify       : http://127.0.0.1:28081 (admin@ttc.local / TtcAdmin!2026)
[provision]   SonarQube  : http://localhost:29000 (admin / TtcAdmin!2026)
[provision]   GitLab     : http://localhost:28090 (root / ChangeMe!Pass)
[provision]   Sample Repo: http://localhost:28090/root/realworld
[entrypoint] 앱 프로비저닝 완료.
```

---

**기동 검증** — 완료 로그를 확인한 뒤, 아래 3 가지 자동 검증으로 "실제로 Jenkins Job · 프로비저닝 마커 · 서비스 health 가 모두 정상" 인지 이중 확인합니다.

**검증 ① Jenkins Job 5 개 전부 등록됐는지**:

```bash
curl -s -u admin:password 'http://127.0.0.1:28080/api/json?tree=jobs%5Bname%5D' \
  | python3 -c "import json,sys;print(*(j['name'] for j in json.load(sys.stdin)['jobs']),sep='\n')"
```

아래 5 개가 정확히 출력되어야 정상:

```text
01-코드-분석-체인
02-코드-사전학습
03-코드-정적분석
04-정적분석-결과분석-이슈등록
05-AI평가
```

한 개라도 누락되면 `provision.sh` 가 도중에 실패한 것 — §12.10 로그 위치 참고.

**검증 ② 프로비저닝 마커 파일 12 종 존재 여부**:

```bash
docker exec ttc-allinone ls /data/.provision/
```

아래 11 개 파일이 모두 있어야 정상:

```text
dataset_api_key    dataset_id    default_models.ok    gitlab_root_pat
jenkins_sonar_integration.ok    ollama_embedding.ok    ollama_plugin.ok
sample_project_realworld.ok    sonar_token    workflow_api_key    workflow_app_id    workflow_published.ok
```

각 파일은 해당 자산(API key, Workflow publish 상태 등)이 생성됐음을 기록하는 flag 입니다.

**검증 ③ 4 개 외부 노출 서비스 HTTP 응답 확인**:

```bash
for url in \
  "http://localhost:28080/login" \
  "http://localhost:28081/apps" \
  "http://localhost:29000/api/system/status" \
  "http://localhost:28090/users/sign_in"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
    echo "  $code  $url"
done
```

각 URL 이 **`200`** 또는 **`302`** (GitLab login redirect) 를 반환해야 정상. `000` 또는 `5xx` 면 해당 서비스 아직 기동 중이거나 실패 — 2~3 분 더 기다린 후 재시도, 지속되면 §12.1 (Ollama) · §12.7 (GitLab) · §12.10 (로그 위치) 참고.

---

**프로비저닝 내부 단계별 실측 소요** (참고용 — 중간 어느 지점에서 시간이 많이 걸리는지 궁금할 때):

| 단계 | 소요 | 비고 |
|------|------|------|
| 컨테이너 기동 직후 ~ Dify 자동화 진입 | ≈ 1 분 | Jenkins · Sonar · Dify 3 앱 병렬 기동 |
| Dify Ollama 플러그인 설치 + provider 등록 | ≈ 2 분 | `langgenius-ollama-*.difypkg` 업로드 |
| Dify Workflow import + publish | ≈ 30 초 | |
| GitLab reconfigure + root PAT 발급 + 샘플 프로젝트 생성 | ≈ 3 분 | arm64 에서는 5~10 분까지도 정상 |
| SonarQube 초기화 + Jenkins Credentials 5 종 + Jobs 5 종 등록 | ≈ 1 분 | |
| **총계** | **≈ 7 분** | 첫 기동 기준. 컨테이너 재기동은 마커 파일로 skip 되어 ≈ 30 초 |

**다음 단계** — 4 서비스에 브라우저로 접속해볼 수 있습니다. 본격적으로 파이프라인을 돌려보려면 §7 로 이동.

---

## 7. 첫 실행 — 샘플 레포로 파이프라인 돌려보기

> 📋 **이 섹션은 "따라 하기"** — §6 까지 완료된 환경에서는 프로비저닝이 이미 샘플 GitLab 프로젝트 `realworld` 를 자동 생성해 둡니다. 따라서 바로 `01-코드-분석-체인` Job 을 실행해 GitLab Issue 가 자동 생성되는 과정을 확인할 수 있습니다. 필요하면 §7.1~§7.2 로 샘플 레포를 수동 재생성할 수도 있습니다.
>
> §7.7 은 별개 파이프라인(05 AI 평가) 첫 실행 — `01` 체인과 독립이며 Ollama 모델·시험지만 있으면 언제든 시작 가능합니다.

### 7.1 Step 1: 샘플 레포 자동 생성 확인 (≈ 1 분)

**이 단계에서 하는 일**: 프로비저닝이 GitLab 에 **샘플 프로젝트 `realworld`** 를 올립니다 ([gothinkster/spring-boot-realworld-example-app](https://github.com/gothinkster/spring-boot-realworld-example-app) 의 vendoring 사본 — airgap 환경에서 인터넷 fallback 없이 로컬 템플릿만으로 완결).

| 샘플 | 목적 | 언어 | GitLab URL |
| --- | --- | --- | --- |
| `root/realworld` | RealWorld API 사양 ↔ 구현 매핑이 명시된 Spring Boot 예제 — 04 파이프라인의 일반적 안정성 검증 | Java / Spring Boot | [http://localhost:28090/root/realworld](http://localhost:28090/root/realworld) |

DDD 계층 분리 (`application/` · `infrastructure/` · `core/`) + MyBatis Data Mapper + 테스트 코드 포함으로, 04 의 `dependency_tree` / `enclosing_meta` (annotation/route 추출) 가 유의미한 컨텍스트로 자라납니다.

**브라우저 확인** — 위 URL 에 `root` / `ChangeMe!Pass` 로 로그인해서 `src/main/java/`, `src/test/java/`, `build.gradle`, `sonar-project.properties` 가 보여야 정상.

**CLI 확인**:

```bash
GITLAB_PAT=$(docker exec ttc-allinone cat /data/.provision/gitlab_root_pat)

curl -sS -H "PRIVATE-TOKEN: $GITLAB_PAT" \
  "http://localhost:28090/api/v4/projects/root%2Frealworld/repository/tree?ref=main" \
  | python3 -m json.tool
```

> **다른 샘플 추가**: `SAMPLE_PROJECTS` 환경변수로 콤마 구분 override 가능. 현재 vendoring 된 템플릿은 `realworld` 만 — 추가하려면 `sample-projects/<name>/` 디렉토리 신설 + `provision.sh` 의 case 분기에 항목 추가.

### 7.2 Step 2: 샘플 레포를 수동 재생성하고 싶다면 (선택)

**기본 흐름에서는 이 단계가 필요 없습니다.** 프로비저닝이 이미 `realworld` 프로젝트를 자동 생성하고 첫 커밋까지 push 해 두기 때문입니다.

다만 아래 경우에는 수동 재생성이 유용합니다.
- `PROVISION_SAMPLE_PROJECT=false` 로 자동 생성을 꺼둔 경우
- 샘플 프로젝트를 지웠거나 내용을 초기 상태로 되돌리고 싶은 경우
- 문서대로 GitLab 프로젝트 생성 + push 절차 자체를 검증하고 싶은 경우

이 경우 아래 블록 전체를 셸에 붙여넣으면 됩니다:

```bash
cp -R sample-projects/realworld /tmp/realworld
cd /tmp/realworld

git init -q -b main
git add -A
git -c user.email=test@ttc.local -c user.name=tester commit -q -m "initial"

GITLAB_PAT=$(docker exec ttc-allinone cat /data/.provision/gitlab_root_pat)
curl -sS -X POST "http://localhost:28090/api/v4/projects" \
  -H "PRIVATE-TOKEN: $GITLAB_PAT" \
  -d "name=realworld&visibility=private&initialize_with_readme=false"

git remote add origin "http://oauth2:${GITLAB_PAT}@localhost:28090/root/realworld.git"
git push -u origin main
```

### 7.3 Step 3: `01-코드-분석-체인` Job 실행 (Jenkins UI)

**이 단계에서 하는 일**: §7.2 에서 준비한 GitLab 레포를 분석 대상으로 지정해 **`01` 오케스트레이터 Job 을 클릭 한 번** 으로 실행합니다. `01` 이 내부적으로 `02-사전학습` → `03-정적분석` → `04-이슈등록` 을 순서대로 자동 연쇄 호출합니다.

**Jenkins UI 에서 단계별로**:

1. 브라우저로 [http://localhost:28080](http://localhost:28080) 접속 → `admin` / `password` 로 로그인.
2. Job 목록에서 **`01-코드-분석-체인`** 클릭.
3. 좌측 메뉴 **"Build with Parameters"** 클릭.
   - 메뉴가 안 보이면 **"Build Now"** 를 한 번 눌러 실패시킴 (Jenkins Declarative Pipeline 의 parameter discovery 가 최초 1회 실행되어야 `Build with Parameters` 메뉴가 생김). 10초 뒤 Job 페이지 새로고침 → "Build with Parameters" 가 나타나면 다시 클릭.
4. 파라미터 — 모든 기본값이 이미 `realworld` 기준으로 채워져 있음:
   - `REPO_URL` = `http://gitlab:80/root/realworld.git`
   - `GITLAB_PROJECT` = `root/realworld`
   - `BRANCH` = `main`, `ANALYSIS_MODE` = `full` (최초 실행), 나머지 기본값 그대로.
5. **"Build"** 클릭 → Job 실행 시작.

**기대 결과** — 화면에 `#1` 빌드가 좌측 "Build History" 에 나타나고, Stage View 에 5 개 Stage(Resolve Commit SHA / Trigger P1 / Trigger P2 / Trigger P3 / Chain Summary) 가 순차로 초록색으로 채워져야 정상. 총 소요 ≈ 3~4 분.

### 7.4 Step 4: 빌드 진행 모니터링

**이 단계에서 하는 일**: `01` 체인 빌드가 어느 Stage 에서 어떻게 진행 중인지 실시간 확인합니다. Jenkins Stage View 가 가장 직관적이지만, CLI 를 쓰면 자동화 스크립트로도 활용 가능합니다.

**방법 ① 브라우저 — Jenkins Stage View**

Jenkins Job 페이지 상단에 Stage 5 개가 가로로 늘어선 표가 있습니다. 각 Stage 가 **회색 → 파란색 (진행 중) → 초록색 (성공) / 빨간색 (실패)** 로 변합니다. 클릭하면 해당 Stage 의 콘솔 로그가 펼쳐집니다.

**방법 ② CLI — 현재 상태를 한 줄씩 dump**

```bash
curl -s -u admin:password \
  'http://127.0.0.1:28080/job/00-%EC%BD%94%EB%93%9C-%EB%B6%84%EC%84%9D-%EC%B2%B4%EC%9D%B8/lastBuild/wfapi/describe' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('status:', d.get('status'))
for s in d.get('stages',[]):
    print(f\"  {s['name']:30s} {s['status']:>10s}  {s.get('durationMillis',0)/1000:.1f}s\")
"
```

**기대 결과** — 전 Stage 성공 시 다음과 같이 출력:

```text
status: SUCCESS
  1. Resolve Commit SHA          SUCCESS    2.1s
  2. Trigger P1 (사전학습)        SUCCESS   45.3s
  3. Trigger P2 (정적분석)        SUCCESS   55.2s
  4. Trigger P3 (이슈 등록)       SUCCESS   90.8s
  5. Chain Summary               SUCCESS    1.2s
```

어느 Stage 가 `FAILED` 면 해당 행 이름으로 대응: Trigger P1 실패 → `02-코드-사전학습/lastBuild/console` · P2 실패 → `03-코드-정적분석/lastBuild/console` · P3 실패 → `04-정적분석-결과분석-이슈등록/lastBuild/console` 로그를 확인.

### 7.5 Step 5: 결과 확인 (4 곳)

**이 단계에서 하는 일**: `01` 체인이 성공적으로 끝났다면 **4 개의 산출물 위치** 를 한 번씩 훑어 "정말로 원하는 결과가 나왔는지" 확인합니다. 첫 실행에서는 특히 ① GitLab Issue 생성 여부가 가장 중요합니다 — 이게 본 스택의 최종 가치.

**① GitLab Issue** (가장 먼저 확인) — 개발자가 실제로 받게 되는 최종 산출물:

- URL: [http://localhost:28090/root/realworld/-/issues](http://localhost:28090/root/realworld/-/issues)
- 기대: `04-정적분석-결과분석-이슈등록` 실행 후 `root/realworld` 프로젝트에 새 Issue 가 생성되어야 정상. 제목은 실행 시점의 Sonar 탐지 결과와 LLM 분류에 따라 달라질 수 있지만, 본문은 §9 "GitLab Issue 결과물 읽는 법" 구조대로 렌더링되어 있어야 합니다.

**② SonarQube 대시보드** — 원본 이슈 소스:

- URL: [http://localhost:29000/dashboard?id=realworld](http://localhost:29000/dashboard?id=realworld)
- 기대: `realworld` 프로젝트가 생성되고 "Issues" 탭에 Java 관련 탐지 결과가 표시되어야 정상. ①의 GitLab Issue 는 이 Sonar 원본 이슈를 바탕으로 LLM 해석과 보강 정보를 추가한 결과입니다.

**③ Dify Studio — Knowledge Base 확인** (RAG 실제 데이터가 적재됐는지):

- URL: [http://localhost:28081](http://localhost:28081) → Knowledge → `code-context-kb` → 우측 **"Recall Testing"** 클릭
- 질의 예: `login function with error handling`
- 기대: `src/auth.py::login` 청크가 상위 결과로 반환. §7.1 의 `session.check_session` 이 `login` 을 호출하는 관계도 함께 잡히면 RAG 건강.

**④ Jenkins Build 리포트 탭 (P1.5 신규)** — 빌드 페이지에서 바로 클릭:

| 파이프라인 | 탭 이름 | 보는 목적 |
| --- | --- | --- |
| `02-코드-사전학습` 빌드 #N | **Pre-training Report** | KB **빌드** 품질: 청크 수, callers_links, vendor 노이즈 제거 효과, 언어/kind 분포, 샘플 청크 미리보기 |
| `04-정적분석-결과분석-이슈등록` 빌드 #N | **RAG Diagnostic Report** | 이슈 분석 **품질**: 이슈당 context_filter 버킷 상태, **LLM citation rate** (LLM 이 실제로 RAG 결과 인용했는지), context:empty 발동률, retry exhausted 비율 |

두 리포트는 **서로 다른 질문에 답합니다** — Pre-training 은 "KB 가 제대로 빌드됐는지", Diagnostic 은 "LLM 이 KB 를 실제로 활용했는지". RAG Diagnostic Report 는 사전학습 결과를 **4-stage narrative 사다리** (학습 범위 → 학습 깊이 → 학습 품질 → 분석 영향) 로 PM 시각으로 노출하므로, 비개발자도 위→아래로 따라 읽으면 인과를 체감할 수 있습니다. §10.5 / §8.2.4.1 / §8.4.2.1 에서 각 카드 해석 가이드 참조.

**⑤ 상태 파일 — 파이프라인 내부 메타**:

```bash
# P1 이 만든 KB 매니페스트 (어느 커밋·몇 개 청크로 적재됐는지)
docker exec ttc-allinone cat /data/kb_manifest.json

# 01 체인이 집계한 실행 요약 (P1/P2/P3 각 결과 + 카운트)
docker exec ttc-allinone cat /var/knowledges/state/chain_*.json
```

기대 결과 요지: `chain_<sha>.json` 의 `p3_summary.created` 값이 1 (GitLab Issue 1 개 생성) 이면 정상 완주.

### 7.6 Step 6: 재실행으로 dedup 동작 확인

**이 단계에서 하는 일**: 같은 커밋에 대해 체인을 **한 번 더** 실행해 "이미 본 이슈는 중복 Issue 를 만들지 않는다" 는 dedup 로직이 정상 작동하는지 확인합니다. 실제 운영에서 여러 번 커밋-체인이 돌 때 GitLab Issues 가 중복으로 부풀지 않도록 하는 핵심 메커니즘.

**작업** — §7.3 의 Jenkins UI 단계를 **동일 파라미터** (`REPO_URL`/`BRANCH`/`ANALYSIS_MODE=full`) 로 한 번 더 반복합니다. 파라미터는 그대로 두고 "Build" 버튼만 다시 클릭.

**기대 결과**:

- `chain_<sha>.json` 에 `p3_summary.skipped=1` 기록 (기존 Issue 는 skip 됐다는 뜻).

  ```bash
  docker exec ttc-allinone cat /var/knowledges/state/chain_*.json | python3 -m json.tool | grep -E "created|skipped"
  # → "created": 0,
  #   "skipped": 1,
  ```

- GitLab Issues 페이지에 **중복 Issue 가 생기지 않음** — 여전히 #1 한 개만 존재. 브라우저 새로고침으로 확인.

dedup 은 Sonar 원본 이슈 key + GitLab Issue title 을 비교해 같은 것이면 작성을 건너뛰는 방식으로 동작합니다. 다음 단계 — 별개 파이프라인인 05 AI 평가를 돌려보려면 §7.7 로 이동.

### 7.7 `05-AI평가` 파이프라인 첫 실행

> **TL;DR** — AI 에이전트/챗봇의 응답 품질을 Golden Dataset(시험지)로 자동 채점하는 파이프라인입니다. `01` 코드 분석 체인과는 **트리거 주기·대상이 다른** 동등한 일급 파이프라인 — 코드 품질 체인이 커밋 SHA 1회로 작동한다면 `05` 는 평가 정책(빌드마다 / 일 1회 / 배포 전)에 따라 별도 실행됩니다. 실행 전 **호스트 Ollama 에 평가 대상 모델 1개 + 심판 모델 1개** 가 내려와 있어야 합니다. 시험지·파라미터를 지정해 Jenkins Job `05-AI평가` 를 실행하면 `summary.html` 과 `summary.json` 이 생성되어 "이 AI 가 얼마나 잘 답했는가" 를 한눈에 볼 수 있습니다.
>
> Step 0 사전 점검 → Step 1 시험지 준비 → Step 2 첫 빌드(파라미터 등록) → Step 3 본 빌드 → Step 4 모니터링 → Step 5 리포트 읽기 → Step 6 의사결정 → Step 7 회귀 추적. 10-row 시험지 기준 총 **45~90 분** 소요.

#### Step 0 — 호스트 Ollama + GPU 사전 점검

**이 단계에서 무엇을 하는가**: 평가에 쓸 모델이 호스트에 실제로 있는지, GPU 가속이 제대로 동작하는지 확인합니다. 이걸 빠뜨리면 빌드가 빠르게 실패하거나(모델 없음) 조용히 CPU 로 돌아 한없이 느려집니다.

**공통 — 호스트 Ollama 모델 확인** (macOS 터미널 또는 Windows PowerShell 에서):

```bash
# 호스트에 실제로 설치된 모델 목록
ollama list
# 예시 출력:
# NAME                    SIZE
# gemma4:e4b              12 GB   ← 평가 대상 후보 (8B급)
# gemma4:26b              21 GB   ← 심판(Judge) 후보 (대형)
# qwen3-coder:30b         19 GB   ← 심판(Judge) 후보
# qwen3.5:4b               4 GB   ← 빠른 심판 후보 (정확도↓)
# bge-m3:latest            1 GB   ← 임베딩 전용, 평가 Judge 로는 부적합
```

부족하면 인터넷 연결된 머신에서 추가 pull:

```bash
# 실무 권장 조합 — 평가 대상 1개 + 심판 1개
ollama pull gemma4:e4b       # 평가 대상 (8B)
ollama pull qwen3.5:4b       # 심판 (4B, 빠름)
# 더 정확한 판정을 원하면
ollama pull qwen3-coder:30b  # 심판 (30B, 느리지만 정확)
```

**현재 어떤 모델이 메모리에 로드돼 있고 어떤 프로세서를 쓰는지** (실행 중 GPU/CPU 여부 확인용):

```bash
# Ollama HTTP API 직접 조회
curl -s http://localhost:11434/api/ps | python3 -m json.tool
# PROCESSOR 필드가 "GPU" 또는 "CUDA" → 가속 OK
# "CPU" 또는 size_vram=0 → 추론이 매우 느려짐. 아래 분기 참고.
```

**분기 ① — macOS Apple Silicon (M1~M4)**:

- GPU 가속은 **Metal backend** 로 자동 동작 (추가 설정 불필요, NVIDIA CUDA 없음).
- `nvidia-smi` 명령 없음 — `ollama ps` 출력의 `size_vram` 값으로 판단.
- Docker Desktop Resources → Memory 12 GB 이상 할당 권장 (모델 2개 VRAM 공유).
- 모델 swap 이 잦으면 macOS가 메모리 압박으로 느려질 수 있으므로 16 GB 머신은 `qwen3.5:4b` 심판 권장.

**분기 ② — Windows WSL2 + NVIDIA GPU**:

```powershell
# PowerShell — GPU 실제 사용량
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv
# memory.used 가 0 MB 근처면 Ollama 가 CPU fallback 중.

# Ollama 프로세스가 점유 중인 VRAM
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

**⚠ 함정 — `CUDA_VISIBLE_DEVICES` 가 잘못 설정되면 Ollama 는 경고 없이 CPU 로 동작**:

```powershell
# 현재 값 확인 (비어 있거나 "0" 이어야 정상)
echo $env:CUDA_VISIBLE_DEVICES
# 존재하지 않는 인덱스(예: 1, 2)면 "0" 으로 교정
[Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '0', 'User')
# Ollama 재기동
Get-Process ollama* | Stop-Process -Force
Start-Process 'C:\Users\<사용자>\AppData\Local\Programs\Ollama\ollama app.exe'
```

**선택 — Cold-start 예열** (첫 평가 호출 시 대형 모델이 VRAM 에 로드되는 60~120 초를 미리 끝내두면 Step 3 본 빌드에서 첫 conversation 대기 없음):

```bash
# Judge 모델을 한 번 ping
curl -sX POST http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-coder:30b","prompt":"hello","stream":false}' | head -c 200
```

#### Step 1 — Golden Dataset (시험지) 준비

**이 단계에서 무엇을 하는가**: AI 에게 낼 질문 + 기대 답 + 합격 기준을 CSV 한 벌로 준비합니다. 처음이면 내장 fixture 를 그대로 써서 파이프라인이 살아있는지 smoke 한 뒤, 실무용 시험지로 교체하는 순서가 안전합니다.

**최초 한 번 — 내장 fixture 복사**:

```bash
# 컨테이너 안의 평가 작업 디렉터리 확인
docker exec ttc-allinone ls /var/knowledges/eval/data/

# 내장 11-케이스 fixture 를 golden.csv 로 복사 (5단계 11지표를 한 번씩 건드리도록 설계됨)
docker exec ttc-allinone bash -c '
  cp /opt/eval_runner/tests/fixtures/tiny_dataset.csv /var/knowledges/eval/data/golden.csv
  chown jenkins:jenkins /var/knowledges/eval/data/golden.csv
  wc -l /var/knowledges/eval/data/golden.csv
'
```

**실무용 시험지를 직접 쓸 때 — CSV 컬럼 완전 레퍼런스**:

| 컬럼 | 필수? | 예시 | 설명 |
|------|-------|------|------|
| `case_id` | ✅ | `policy-pass-clean` | 케이스 고유 식별자. 리포트에 그대로 표시되며, **한 번 정하면 절대 재사용/변경 금지** (빌드 간 회귀 비교 단위) |
| `conversation_id` | ⬜ | `mt-1` | 멀티턴 대화 묶음 ID. 비우면 단일턴 |
| `turn_id` | ⬜ | `1` | 멀티턴 안에서의 순서 (1-base). `conversation_id` 가 있을 때만 필요 |
| `input` | ✅ | `대한민국의 수도는?` | AI 에 보낼 질문 |
| `expected_output` | ⬜ | `서울입니다.` | 기대 답변. Faithfulness/Recall 등에서 참조 |
| `success_criteria` | ⬜ | `응답에 서울이 포함되어야 함` | 합격 기준 (자연어 또는 DSL — 아래 §DSL 참고). 비면 HTTP 200 만으로 통과 판정 |
| `context_ground_truth` | ⬜ | `["서울 공항은 인천국제공항과 김포국제공항이다"]` | RAG retrieval 정답 (JSON 배열). 채워져 있으면 ⑥ Faithfulness / ⑦ Recall / ⑧ Precision 지표가 자동 활성 |
| `expected_outcome` | ⬜ | `pass` / `fail` | 케이스 의도 마킹 (실패를 의도한 안전 테스트는 `fail`) |
| `calib` | ⬜ | `true` | `true` 로 마킹된 케이스는 Judge 변동성 측정용 (리포트에 σ 표시) |

> **Tip** — 처음에는 5~10 케이스로 충분합니다. 11+ 케이스는 1시간 이상 소요.

**`success_criteria` DSL — 먼저 예시, 다음에 규칙**:

```text
# 예시 1 — 자연어 (심판 LLM 이 채점)
응답에 서울이 포함되어야 함
답변은 친절하고 공손해야 한다
response must include 'Seoul'

# 예시 2 — 정규식 (raw 응답 본문 대상, LLM 호출 없음)
raw~r/서울/
raw~r/\d{1,3}(,\d{3})+원/

# 예시 3 — JSON 경로 + 정규식 (응답이 JSON 일 때)
json.answer~r/서울/
json.data[0].price~r/^\d+$/
json.issue_key~r/^[A-Z]+-\d+$/

# 예시 4 — HTTP 상태 코드
status_code=200

# 예시 5 — AND 조합 (대문자 ' AND ')
status_code=200 AND json.answer~r/서울/
```

규칙:

1. **자연어 우선 매칭** — `응답에 <키워드>가 포함되어야 함` / `응답에 <키워드>가 포함되어야 합니다` / `response must include '<키워드>'` / `response should contain '<키워드>'` 패턴은 LLM 호출 없이 **문자열 포함 여부로 즉시 판정**됩니다. 나머지 자연어는 심판 LLM (GEval) 에게 맡김.
2. **DSL 연산자** — `status_code=N` (HTTP 코드 일치) · `raw~r/<regex>/` (응답 본문 정규식) · `json.<path>~r/<regex>/` (JSON 경로 값 정규식) · ` AND ` (공백+대문자, 여러 조건 AND 조합).
3. **JSON 경로** — dot (`a.b.c`) + 배열 인덱스 (`items[0]`) 지원. `$.`/`[*]` 같은 JSONPath 확장은 **미지원**.
4. **빈 값** — `success_criteria` 가 비면 HTTP `2xx` 를 fallback 통과 기준으로 씀.
5. **우선순위** — 같은 row 에 `expected_output` 과 `success_criteria` 가 모두 있으면 Task Completion 판정은 `success_criteria` 가 이깁니다.

**멀티턴 케이스 예시** (같은 `conversation_id` = `mt-1`):

```csv
case_id,conversation_id,turn_id,input,expected_output,success_criteria
mt-t1,mt-1,1,제 이름은 김철수입니다.,이름을 기억하겠습니다.,
mt-t2,mt-1,2,제 이름이 뭔가요?,김철수,응답에 김철수가 포함되어야 함
```

멀티턴은 `turn_id` 오름차순으로 실행되며 이전 턴의 답변이 다음 턴 호출에 누적됩니다. 대화 전체가 끝나면 ⑨ Multi-turn Consistency 를 한 번 채점.

**RAG 케이스 예시**:

```csv
case_id,input,context_ground_truth,expected_output,success_criteria
rag-1,서울 공항을 알려줘,"[""서울 공항은 인천국제공항과 김포국제공항이다""]",서울에는 인천과 김포 공항이 있습니다,응답에 인천이 포함되어야 함
```

CSV 특성 상 JSON 배열은 전체를 큰따옴표로 감싸고 내부 큰따옴표는 두 번(`""`) 으로 escape.

**시험지를 직접 작성했다면 컨테이너로 복사**:

```bash
# 로컬에서 편집한 my_golden.csv 를 컨테이너 안으로
docker cp my_golden.csv ttc-allinone:/var/knowledges/eval/data/golden.csv
docker exec ttc-allinone chown jenkins:jenkins /var/knowledges/eval/data/golden.csv
```

또는 Step 3 의 `UPLOADED_GOLDEN_DATASET` 파라미터로 Jenkins UI 에서 직접 업로드할 수 있습니다.

#### Step 2 — 파라미터 로딩용 첫 빌드 (최초 1회만)

**이 단계에서 무엇을 하는가**: Jenkins Pipeline Job 은 `properties([parameters([...])])` 블록을 **한 번 실행해야** 파라미터가 UI 에 등록됩니다. 그래서 첫 빌드는 파라미터 없이 "Build Now" 로 돌려 실패하는 것이 정상 — 두 번째 빌드부터 "Build with Parameters" 가 나타납니다.

1. Jenkins UI ([http://localhost:28080](http://localhost:28080)) 에 `admin` / `password` 로 로그인.
2. **`05-AI평가`** Job 클릭.
3. 좌측 **"Build Now"** 클릭 (파라미터 미제공). **빌드 #1 은 실패하거나 기본값(호스트에 없는 모델)으로 실패해도 정상**.
4. 10초 뒤 좌측 메뉴에 **"Build with Parameters"** 가 나타나면 등록 완료.

> **CascadeChoice 드롭다운이 비어 있다면** — `JUDGE_MODEL`/`TARGET_OLLAMA_MODEL` 드롭다운은 호스트 Ollama 의 `/api/tags` 를 빌드 시점에 실시간 조회해 채웁니다. 처음 1회 Jenkins 스크립트 승인이 필요하며 provision.sh 가 자동 시도합니다. 그래도 비어 있으면 §12.15 참고.

#### Step 3 — 실제 파라미터 지정 후 본 빌드

**이 단계에서 무엇을 하는가**: 이제 시험지와 모델, 판정 기준을 지정해 진짜 평가를 돌립니다.

Jenkins → `05-AI평가` → **"Build with Parameters"** → 아래 값 지정:

| 파라미터 | 기본값 | 이 예시에서는 | 의미 |
|---------|--------|-------------|------|
| `TARGET_TYPE` | `local_ollama_wrapper` | `local_ollama_wrapper` | 평가 대상 AI 와 어떻게 말 걸지 (아래 §8.5.2 참고) |
| `TARGET_URL` | (빈 값) | 비움 | `http`/`ui_chat` 모드일 때만 필수 |
| `TARGET_AUTH_HEADER` | (빈 값, 암호) | 비움 | `http` 에서 API 인증 헤더 (예: `Authorization: Bearer sk-...`) |
| `TARGET_REQUEST_SCHEMA` | `standard` | `standard` | `http` 의 요청/응답 포맷 (`openai_compat` 는 OpenAI 호환) |
| `UI_INPUT_SELECTOR` | `textarea, input[type=text]` | 건드리지 않음 | `ui_chat` 전용 입력창 CSS selector |
| `UI_SEND_SELECTOR` | `button[type=submit]` | 건드리지 않음 | `ui_chat` 전송 버튼 (빈값이면 Enter) |
| `UI_OUTPUT_SELECTOR` | `.answer, [role=assistant], .message-content` | 건드리지 않음 | `ui_chat` 응답 노드 |
| `UI_WAIT_TIMEOUT` | `60` (초) | 건드리지 않음 | `ui_chat` 응답 대기 |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | 그대로 | 심판 모델이 사는 Ollama 데몬 주소 |
| `TARGET_OLLAMA_MODEL` | (드롭다운, 호스트 실재 모델) | **`gemma4:e4b`** | 평가 대상. 실무 운용 모델과 일치시켜야 회귀 검증 의미 |
| `JUDGE_MODEL` | (드롭다운, 호스트 실재 모델) | **`gemma4:e4b`** | 심판 기본값. 현재 운영 표준이며 호스트 표준 모델과 맞춰 즉시 실행하기 쉬움 |
| `ANSWER_RELEVANCY_THRESHOLD` | `0.7` | `0.7` | ④ 답변 관련성 합격 기준 (0~1) |
| `GOLDEN_CSV_PATH` | `/var/knowledges/eval/data/golden.csv` | 그대로 | 시험지 경로 (컨테이너 내부) |
| `UPLOADED_GOLDEN_DATASET` | — | (선택) | 내 PC 의 CSV 업로드 → 위 경로에 덮어쓰기 |

**"Build" 클릭**. 이후 Step 4 로.

> **⚠ 드롭다운이 fallback 리스트만 보일 때** — `JUDGE_MODEL`/`TARGET_OLLAMA_MODEL` 가 `qwen3.5:4b`, `gemma4:e2b` 등 고정 5개만 반복해서 보인다면 호스트 Ollama `/api/tags` 조회가 막힌 상태입니다. §12.15 의 Script Approval 승인 절차 수행.

#### Step 4 — 진행 모니터링

**이 단계에서 무엇을 하는가**: 빌드가 살아있는지, 어느 conversation/지표가 처리 중인지 실시간 확인합니다. 로컬 Ollama Judge 는 case 당 수 분~수십 분이 걸릴 수 있어, **진행이 "멈춘 것처럼" 보여도 정상인 구간이 많습니다**.

**빌드 Stage 는 5 단계** (Jenkins Console Output 에 실시간 표시):

| Stage | 하는 일 | 일반 소요 |
|-------|--------|---------|
| 1. 시험지 준비 | `golden.csv` 존재 확인, 리포트 디렉터리 생성 | < 1초 |
| 1-1. Judge Model 검증 | 호스트 Ollama 에 선택한 `JUDGE_MODEL` 이 있는지 확인 | < 2초 |
| 1-2. 로컬 Ollama Wrapper 기동 | `TARGET_TYPE=local_ollama_wrapper` 일 때만. wrapper 데몬 fork + /health probe + cold-start 예열 | 10초 ~ 5분 |
| 2. Python 평가 실행 (pytest) | golden.csv 한 conversation 씩 평가 | case 당 1~10분 |
| Post Actions | summary.html 생성, archive, publishHTML | 5~15초 |

**pytest 실시간 로그 예시** (Phase 6 이후 `-u -v -s --tb=short + PYTHONUNBUFFERED=1`):

```text
+ python3 -u -m pytest ... -v -s --tb=short
[eval] ▶ conversation=policy-pass-clean turns=1 target_type=local_ollama_wrapper
test_evaluation[conversation0] PASSED                                [10%]
[eval] ▶ conversation=task-pass-simple turns=1 target_type=local_ollama_wrapper
test_evaluation[conversation1] PASSED                                [20%]
[eval] ▶ conversation=rag-1 turns=1 target_type=local_ollama_wrapper
test_evaluation[conversation2] FAILED                                [30%]
...
```

각 케이스 시작 시 `[eval] ▶ conversation=... turns=N` 가, 종료 시 `PASSED`/`FAILED` 가 즉시 찍힙니다.

**실측 소요 시간 가이드** (M1 Max · 10-row tiny_dataset 기준):

| 이벤트 | 실측 (참고값) |
|--------|-------------|
| 빌드 시작 → 첫 conversation Judge 호출 | ≈ 30 초 (모델 VRAM 로드) |
| VRAM 동시 점유 | gemma4:26b (21 GB) + gemma4:e4b (12 GB) ≈ **33 GB** |
| 단일턴 conversation 당 | 1~4 분 (지표 2~3 개) |
| RAG 계열 (지표 5개) conversation 당 | 4~10 분 |
| Multi-turn (2턴) conversation | 5~8 분 |
| 10-row dataset 총 소요 | **45~90 분** |

**생존 확인 명령** (`멈춘 것 같다` 의심될 때):

```bash
# 1. pytest 가 실제로 CPU 를 쓰고 있나
docker exec ttc-allinone ps -eo pid,etime,pcpu,pmem,cmd | grep -E "pytest|ollama_wrapper" | grep -v grep

# 2. 호스트 Ollama 에 모델이 로드돼 있나 (추론 중이면 VRAM 점유 중)
curl -s http://host.docker.internal:11434/api/ps | python3 -m json.tool

# 3. 최근 pytest 라이브 출력
curl -s -u admin:password 'http://127.0.0.1:28080/job/05-AI%ED%8F%89%EA%B0%80/lastBuild/consoleText' | tail -60
```

세 가지가 모두 "뭔가 움직이는 중" 이면 정상 — 계속 대기.

#### Step 5 — 결과 확인

**이 단계에서 무엇을 하는가**: 빌드가 끝나면 `summary.html` 을 먼저 읽고(사람용), 필요하면 `summary.json` 을 후처리(감사·대시보드용) 합니다.

**5-A. 리포트 위치 확인**:

```bash
# 방금 빌드 번호
BUILD_N=$(curl -s -u admin:password \
  'http://127.0.0.1:28080/job/05-AI%ED%8F%89%EA%B0%80/lastBuild/api/json' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['number'])")

# 리포트 디렉터리
docker exec ttc-allinone ls -la /var/knowledges/eval/reports/build-${BUILD_N}/
# build-7/
# ├── summary.html        ← 사람이 먼저 보는 리포트
# ├── summary.json        ← 후처리용 전체 데이터
# ├── results.xml         ← JUnit 형식 (옵션)
# └── wrapper.log         ← local_ollama_wrapper 모드 wrapper 로그
```

Jenkins UI 에서는 좌측 메뉴의 **"AI Eval Summary"** 탭 클릭 (publishHTML 로 자동 게시).

**5-B. `summary.html` 4 구역 읽는 법**:

```text
┌──────────────────────────────────────────────────────────────────┐
│ ① 🤖 이번 빌드 한 줄 요약                         [🤖 LLM 생성]  │
│ "이번 빌드는 대화 11건 중 9건 통과 (82%).                        │
│  주원인은 Faithfulness 부족 2건 (RAG 관련).                      │
│  권장 조치: retrieval_context 정확도 점검 필요."                 │
│ 권고: investigate                                                │
├──────────────────────────────────────────────────────────────────┤
│ ② 📦 빌드 메타데이터 (이 빌드를 "어떤 조건에서" 돌렸는가)        │
│ 실행 ID:    build-12                                             │
│ 평가 대상:  local_ollama_wrapper (gemma4:e4b)                    │
│ 심판 모델:  qwen3.5:4b  T=0  digest=…3a7b8c9d                    │
│ 데이터셋:   golden.csv  sha256=…fedcba98  rows=11                │
│ Judge 변동성: 보정 σ=0.045 (mean=0.876)  Judge calls=128         │
├──────────────────────────────────────────────────────────────────┤
│ ③ 📊 11지표 카드 대시보드                                        │
│ ┌─────────────────────────────────┐                              │
│ │ ④ Answer Relevancy              │                              │
│ │ 통과율  ████████░░  90%  9/10   │                              │
│ │ 평균    0.823    임계  ≥ 0.7    │                              │
│ │ 분포    ▁▂▃▅▇█▇▅▃▁              │                              │
│ │ 실패    relevancy-offtopic-1    │                              │
│ └─────────────────────────────────┘                              │
│ (11개 카드: Policy / Format / Task / Relevancy / Toxicity /      │
│  Faithfulness / Recall / Precision / MultiTurn / Latency /       │
│  Token Usage)                                                    │
├──────────────────────────────────────────────────────────────────┤
│ ④ 🔍 Conversation 드릴다운 (각 케이스별 상세)                    │
│ ▼ conversation: rag-1                              ❌ FAILED     │
│    Turn 1                                          ❌ FAILED     │
│    case_id:  rag-faithful                                        │
│    input:    "서울에 있는 공항을 알려줘"                         │
│    expected: "서울에는 인천과 김포 공항이 있습니다"              │
│    actual:   "서울 인근 공항은 인천국제공항과 김포공항입니다."   │
│    Latency: 2.3s  Tokens: 24/87/111                              │
│    ✅ Policy  ✅ Schema  ✅ TaskCompletion (0.95)                │
│    ✅ AnswerRelevancy (0.91)  ❌ Faithfulness (0.62, ≥ 0.7)      │
│    🤖 쉬운 해설: "답변에 '인근' 이라는 단어가 있어 retrieval_context │
│         의 '인천국제공항과 김포국제공항' 표현과 일치하지 않아       │
│         Faithfulness 가 0.62 로 임계 미달입니다."                   │
│                                                                  │
│  ⚠ 실패 분류: ❌ 시스템 에러 0건  ⚠️ 품질 실패 2건                │
└──────────────────────────────────────────────────────────────────┘
```

**어디를 먼저 봐야 하는가**:

1. ① 한 줄 요약의 **권고** (pass / investigate / block) — 빌드가 통과냐, 점검이냐, 배포 차단이냐.
2. ② **메타데이터의 `Judge digest` + `dataset sha256`** — 지난 빌드와 **같은 심판·같은 시험지** 인지 확인. 다르면 지난 빌드와 점수 비교 불가.
3. ③ 카드 대시보드에서 **통과율이 낮거나 평균이 임계 근처인 지표** 빨간불 — 주의.
4. ④ 실패 케이스의 **"시스템 에러 vs 품질 실패"** 분류:
   - **시스템 에러** (`error_type=system`) — adapter timeout, HTTP 5xx 등. **AI 자체 품질 문제가 아님**, 인프라/연결 점검.
   - **품질 실패** (`error_type=quality`) — metric 점수 미달. **AI 답변 품질 문제**, 모델이나 시험지 조정.

**5-C. `summary.json` 스키마** (후처리·감사·대시보드 연동용):

```json
{
  "run_id": "build-12",
  "target_url": "http://127.0.0.1:8000/invoke",
  "target_type": "local_ollama_wrapper",
  "totals": {
    "conversations": 11,
    "passed_conversations": 9,
    "failed_conversations": 2,
    "turn_pass_rate": 81.82,
    "latency_ms": {"count": 11, "min": 800, "max": 4500, "p50": 2100, "p95": 4200, "p99": 4500},
    "tokens": {"turns_with_usage": 11, "prompt": 264, "completion": 957, "total": 1221}
  },
  "indicators": {
    "AnswerRelevancyMetric": {
      "pass": 10, "fail": 1, "scores": [0.91, 0.88, ...],
      "threshold": 0.7, "failed_case_ids": ["relevancy-offtopic-1"]
    },
    "FaithfulnessMetric": {"pass": 1, "fail": 1, "threshold": 0.7, ...}
  },
  "aggregate": {
    "judge": {"model": "qwen3.5:4b", "base_url": "http://host.docker.internal:11434",
              "temperature": 0, "digest": "sha256:3a7b8c9d..."},
    "dataset": {"path": "/var/knowledges/eval/data/golden.csv",
                "sha256": "fedcba98...", "rows": 11, "mtime": "2026-04-22T11:00:00+00:00"},
    "calibration": {
      "enabled": true, "turn_count": 2, "case_ids": ["policy-pass-clean","task-pass-simple"],
      "per_metric": {"AnswerRelevancyMetric": {"mean": 0.88, "std": 0.04, ...}},
      "overall": {"mean": 0.876, "std": 0.045, "score_count": 14}
    },
    "judge_calls_total": 128,
    "borderline_policy": {"repeat_n": 3, "margin": 0.05},
    "exec_summary": {"text": "이번 빌드는...", "source": "llm", "role": "exec_summary"}
  },
  "conversations": [
    {
      "conversation_key": "rag-1",
      "status": "failed",
      "turns": [
        {
          "case_id": "rag-faithful",
          "status": "failed",
          "error_type": "quality",
          "actual_output": "서울 인근 공항은...",
          "metrics": [
            {"name": "FaithfulnessMetric", "score": 0.62, "threshold": 0.7,
             "passed": false, "reason": "답변이 retrieval_context 의 표현과..."}
          ],
          "easy_explanation": {"text": "...", "source": "llm"}
        }
      ]
    }
  ]
}
```

최상위 키 의미:

- `totals` — 대화/턴 단위 합산 + latency p50/p95/p99 + 토큰 총합
- `indicators` — 11 지표 각각의 통과/실패/평균/임계/실패 case_id 목록
- `aggregate.judge` — **Phase 3 immutable 메타데이터** — 어느 심판 모델(+digest)이 채점했는지 영구 기록
- `aggregate.dataset` — 어느 시험지 버전(sha256)으로 평가했는지 영구 기록
- `aggregate.calibration` — `calib=true` 케이스들의 Judge 변동성 통계 (σ 가 0.1 이상이면 심판이 같은 입력에 다른 점수를 매기고 있다는 뜻)
- `aggregate.exec_summary` — LLM 이 생성한 한 줄 요약 원본
- `conversations[*].turns[*].error_type` — `null` / `"system"` / `"quality"` 중 하나

#### Step 6 — 리포트로 의사결정

**이 단계에서 무엇을 하는가**: 숫자만 보지 말고 "이 빌드를 배포해도 되는가" 를 판단합니다.

**판정 기본 원칙**:

| 상황 | 권고 | 근거 |
|------|-----|-----|
| 전 지표 통과 & calibration σ < 0.1 | **pass** | 품질 + 판정 일관성 OK |
| 일부 지표 실패 OR σ ≥ 0.1 | **investigate** | 사람이 실패 케이스 확인 필요 |
| system error 다수 OR 주요 지표(Policy·Faithfulness 등) 전면 실패 | **block** | 배포 금지 |

**빌드 간 회귀 비교 원칙**:

| A 빌드 vs B 빌드 | 비교 가능? | 액션 |
|-----------------|----------|-----|
| `judge.digest` 다름 | ❌ 불가 | 심판 모델이 바뀌었으므로 점수 차이가 모델 차인지 품질 차인지 구분 못함. 같은 Judge 로 재실행. |
| `dataset.sha256` 다름 | ❌ 불가 | 시험지가 바뀜. 같은 시험지로 재실행. |
| 둘 다 같음 | ✅ 가능 | `indicators.*.pass_rate`, `aggregate.overall` 비교 |

**Judge 변동성 관리**:

- `aggregate.calibration.overall.std` 가 0.1 이상이면 심판 LLM 이 같은 입력에 들쭉날쭉한 점수를 내는 중.
- 대응 ①: 더 큰 Judge 모델 (`qwen3.5:4b` → `gemma4:e4b` → `qwen3-coder:30b`).
- 대응 ②: 경계 케이스 재평가 — 기본 threshold ±`BORDERLINE_MARGIN` (기본 0.05) 범위 케이스를 N 회 재평가 후 중앙값 사용. 현재는 환경변수 `REPEAT_BORDERLINE_N=3` (+ 선택적 `BORDERLINE_MARGIN=0.05`) 로 활성 — eval_runner Jenkinsfile stage 에 env 블록으로 주입하면 적용됨 (Jenkins UI 파라미터로 승격은 후속 과제).

#### Step 7 — 빌드 간 회귀 추적 실무

**이 단계에서 무엇을 하는가**: 매 빌드의 `summary.json` 을 누적해 품질 추이를 감시합니다. 6단계의 "pass 인지" 가 단일 빌드 판정이라면, 7단계는 "이전 대비 나빠졌는지" 추적.

**원칙**:

1. **`golden.csv` 는 git 으로 버전 관리** — 어느 커밋 시점에 시험지가 어떻게 바뀌었는지 추적. 변경 시 `sha256` 도 자동으로 바뀌어 `summary.json` 에 기록됨.
2. **`case_id` 절대 재사용/변경 금지** — 삭제된 케이스 이름도 재사용 금지. 그래야 빌드 간 case 수준 diff 가능.
3. **매 빌드의 `summary.json` 영구 보관** — Jenkins `archiveArtifacts` 가 기본 수행. 외부 오브젝트 스토리지 백업 권장.

**최소 회귀 감지 스크립트** (두 빌드의 지표별 pass_rate 비교):

```bash
# 사용: ./diff_builds.sh <OLD_BUILD_N> <NEW_BUILD_N>
OLD=$1; NEW=$2
docker exec ttc-allinone python3 - <<PY
import json
old = json.load(open(f"/var/knowledges/eval/reports/build-$OLD/summary.json"))
new = json.load(open(f"/var/knowledges/eval/reports/build-$NEW/summary.json"))
if old["aggregate"]["judge"]["digest"] != new["aggregate"]["judge"]["digest"]:
    print("⚠ Judge 모델 다름 — 점수 비교 불가"); exit(2)
if old["aggregate"]["dataset"]["sha256"] != new["aggregate"]["dataset"]["sha256"]:
    print("⚠ 시험지 다름 — 점수 비교 불가"); exit(2)
for name in old["indicators"]:
    oi, ni = old["indicators"][name], new["indicators"].get(name, {})
    o_rate = oi["pass"] / max(1, oi["pass"] + oi["fail"])
    n_rate = ni.get("pass",0) / max(1, ni.get("pass",0) + ni.get("fail",0))
    if n_rate < o_rate - 0.05:
        print(f"❌ REGRESSION  {name}: {o_rate:.2f} → {n_rate:.2f}")
PY
```

**향후 옵션** — 속도 개선을 위한 외부 API Judge (`JUDGE_PROVIDER ∈ {gemini, openai, anthropic}`) 는 **현재 미구현**. Gemini free tier (15 req/min, 1500 req/day) 가 가장 낮은 비용으로 도입 가능한 경로.

---

## 8. 각 파이프라인 상세

### 8.0 5 파이프라인 한 눈에 보기

각 파이프라인의 역할 / 핵심 입력 / 핵심 출력 / 평균 소요:

- **01 코드 분석 체인 (오케스트레이터)** — 02→03→04 를 단일 커밋 SHA 기준으로 순차 실행. 입력: `REPO_URL` · `BRANCH` · `ANALYSIS_MODE`. 출력: `chain_summary.json` (전 단계 결과 통합 + GitLab 이슈 수). 소요: 전체 약 10~15 분 (MAX_ISSUES=10 기준).
- **02 코드 사전학습 (P1)** — 레포를 함수·메서드 단위 청크로 분해 후 Dify Knowledge Base 에 적재 (`tree-sitter` AST + 자연어 요약 enrichment). 입력: `REPO_URL` · `BRANCH` · `ANALYSIS_MODE`. 출력: Dify Dataset (`code-context-kb`) + `Pre-training Report` HTML. 소요: 약 3~5 분.
- **03 코드 정적분석 (P2)** — SonarQube CLI 로 정적분석 실행, 이슈 목록 발행. 입력: `REPO_URL` · `BRANCH` · `SONAR_PROJECT_KEY`. 출력: SonarQube 프로젝트의 issues/measures. 소요: 약 1~2 분.
- **04 정적분석 결과분석·이슈등록 (P3)** — Sonar 이슈를 Dify Workflow 로 LLM 분석 후 GitLab Issue 자동 생성. 입력: `SONAR_PROJECT_KEY` · `GITLAB_PROJECT` · `MAX_ISSUES`. 출력: `llm_analysis.jsonl` · GitLab 이슈 · `RAG Diagnostic Report` HTML. 소요: 이슈당 약 1~2 분.
- **05 AI 평가** — 11 지표 × 5 단계로 AI 응답 품질 자동 평가 (deepeval 기반). 입력: `TARGET_TYPE` · `TARGET_URL` · `GOLDEN_CSV_PATH`. 출력: `eval_report.json` · 11 지표 통과/실패 표. 소요: 케이스당 약 30~60 초.

각 파이프라인의 파라미터 전체 / Stage 구조 / 산출물 경로는 아래 §8.1 ~ §8.5 에서 상세히 다룹니다.

### 8.1 `01-코드-분석-체인` (오케스트레이터)

**역할**: 커밋 SHA 하나를 기준으로 02·03·04 파이프라인을 올바른 순서와 파라미터로 연쇄 실행합니다. 개발자는 이 Job 만 실행하면 되며, 중간 단계를 개별로 호출하거나 매개변수를 맞출 필요가 없습니다. 체인 구조상 **P1 은 Dify 의 KB 인덱싱이 실제로 끝날 때까지 내부적으로 대기** 후 SUCCESS 를 반환하므로, P3 의 RAG 검색이 빈 결과를 반환하는 race condition 이 발생하지 않습니다 (상세: §8.2.5).

**성과**: 실행이 완료되면 해당 커밋에 대한 RAG KB, SonarQube 스캔 리포트, GitLab Issue 등록, 체인 요약 JSON 이 모두 준비되어 있습니다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `REPO_URL` | `http://gitlab:80/root/realworld.git` | 컨테이너 내부 이름 `gitlab` |
| `BRANCH` | `main` | 분석 브랜치 |
| `ANALYSIS_MODE` | `full` | `full` = KB 강제 재빌드 · `commit` = manifest 일치 시 재사용 |
| `COMMIT_SHA` | `(빈 값)` | 지정 시 그 커밋 고정, 빈 값이면 BRANCH HEAD |

**Stage 구조** (총 5 단계, 각 Stage 는 wait+propagate 로 순차 실행):

1. Resolve Commit SHA — `git ls-remote` 로 BRANCH HEAD 해석하거나 파라미터값 사용
2. Trigger P1 — `02-코드-사전학습` 을 `build job:` 로 호출 (COMMIT_SHA 전달)
3. Trigger P2 — `03-코드-정적분석` 호출
4. Trigger P3 — `04-정적분석-결과분석-이슈등록` 호출 (MODE=`full` → `incremental` 매핑)
5. Chain Summary — P3 artifact 에서 `gitlab_issues_created.json` 을 읽어 `p3_summary` 집계 → `/var/knowledges/state/chain_<sha>.json` 에 저장 + `archiveArtifacts`

---

### 8.2 `02-코드-사전학습` (P1) — RAG 지식 창고 구축

**역할**: 분석 대상 레포의 모든 소스를 함수·메서드 단위로 분해하고, 각 조각에 자연어 요약을 붙여 Dify Knowledge Base 에 적재합니다.

**성과**: 이 파이프라인이 완료되면, 이후 P3 에서 특정 코드 조각에 대해 "이 레포 안의 비슷한 함수 / 이 함수를 호출하는 곳 / 같은 패턴의 기존 코드" 를 자연어 질의로 즉시 검색할 수 있게 됩니다. 이 데이터베이스가 있어야 LLM 이 "이 프로젝트의 맥락에 맞는 답" 을 생성할 수 있습니다.

#### 8.2.1 Step 1 — Git Clone

`${REPO_URL}` 를 `/var/knowledges/codes/<repo>` 로 clone. `withCredentials[gitlab-pat]` 로 oauth 주입.

#### 8.2.2 Step 2 — tree-sitter AST 청킹 (`repo_context_builder.py`)

레포를 순회하며 **언어별 구문 트리** 를 파싱해 함수/메서드/클래스 단위로 청크를 생성.

**P1.5 KB 품질 필터** (2026-04-25):

- **경로 기반 제외** — `EXCLUDE_DIRS` 에 `vendor`, `vendors`, `3rdparty`, `assets/vendor`, `bower_components` 등 번들 자산 디렉터리 추가. nodegoat 기준 bootstrap/raphael/morris 1자 심볼 (`e`, `m`, `a`) 오염 제거.
- **파일명 기반 제외** — `*.min.js`, `*.bundle.js`, `chunk-<hash>.js`, `*.umd.js` 등 미니파이 산출물 정규식 필터.
- **trivial chunk skip** — body 문자 수 `< 20` 또는 비-공백 라인 `< 2` 인 빈 껍데기 함수 제외. 환경변수 `REPO_CTX_MIN_BODY_CHARS`, `REPO_CTX_MIN_BODY_LINES` 로 튜닝.
- **중복 청크 제거** — `(symbol, body_sha1)` 키로 레포 전역 dedup. vendor 복사본 / generated code 중복 방지.

실측 nodegoat: 147 chunks → 27 chunks (80% 감소), callers_links 141 → 11 (거짓 동명 매칭 130건 제거).

| 언어 | 확장자 | 추출 대상 노드 |
|------|--------|-----------|
| Python | `.py` | `function_definition`, `async_function_definition`, `class_definition` |
| Java | `.java` | `method_declaration`, `constructor_declaration`, `class_declaration`, `interface_declaration`, `enum_declaration` |
| TypeScript | `.ts` | `function_declaration`, `method_definition`, `class_declaration`, `interface_declaration`, `arrow_function` |
| TSX | `.tsx` | `function_declaration`, `method_definition`, `class_declaration` |
| JavaScript | `.js` | `function_declaration`, `method_definition`, `class_declaration`, `arrow_function` |
| Go (P2) | `.go` | `function_declaration`, `method_declaration`, `type_declaration` |
| Rust (P2) | `.rs` | `function_item`, `impl_item`, `struct_item`, `enum_item`, `trait_item` |
| C# (P2) | `.cs` | `method_declaration`, `constructor_declaration`, `class_declaration`, `interface_declaration`, `struct_declaration`, `enum_declaration` |
| Kotlin (P2) | `.kt` | `function_declaration`, `class_declaration`, `object_declaration` |
| C (P2) | `.c`, `.h` | `function_definition` |
| C++ (P2) | `.cpp`, `.hpp`, `.cc` | `function_definition`, `class_specifier`, `struct_specifier` |

> **새 언어의 callees 추출 한계**: P2 에서 추가된 언어들은 청크 추출 + symbol 매칭은 즉시 작동하지만, `callees` 는 `collect_callees()` 의 언어별 분기 (Python `call`, Java `method_invocation`, JS/TS `call_expression`) 에 해당 언어가 없으면 빈 배열로 채워집니다. 호출 그래프가 필요하면 `collect_callees()` 에 해당 언어의 call site 노드 타입을 추가해주세요.

각 청크에 메타데이터 부착:

```json
{
  "path": "src/auth.py",
  "symbol": "login",
  "kind": "function",
  "lang": "python",
  "lines": "16-23",
  "commit_sha": "e38bd123...",
  "code": "def login(username, password, user_store): ...",
  "doc": "Verify user credentials against user_store and return True on match.",
  "callees": ["verify_password", "dict.__getitem__"],
  "callers": ["src/handlers/auth_handler.py#handle_login",
              "src/cli/admin.py#reset_and_login"],
  "test_paths": ["test/auth.test.py#test_login_success"],
  "is_test": false,
  "test_for": null
}
```

- **`doc`** (P2 K-4 신규): tree-sitter 가 추출한 leading docstring (Python triple-quote) 또는 직전 연속 comment (`/** */`, `///`, `//`, `#` 등). 200자 cap, 한 줄. 코드 식별자만으로는 약했던 dense 임베딩 매칭을 자연어 의도로 보강.

- **`callees`** (pass 1, 로컬 AST): 이 함수 안에서 호출하는 다른 함수들 (tree-sitter 가 call site 를 재귀 순회해 수집). 파이썬 `call` 노드, Java `method_invocation`, JS/TS `call_expression`.
- **`callers`** (pass 2, P1 신규): 이 함수를 **부르는** 쪽 목록. pass 1 이 끝난 뒤 전체 청크 풀에서 `symbol_name → [정의 청크]` 역인덱스를 구축하고, 각 caller 의 callees 를 순회해 target 청크에 `caller_path#caller_symbol` 을 push 한다. 동명 심볼 중의성에 대해 **recall 우선** — 모든 후보 정의에 링크 (import resolution 은 tree-sitter 만으론 불가하므로 precision 희생). 상한 20개.
- **`test_paths`** (pass 2, P1 신규): 이 심볼을 대상으로 하는 테스트 청크 역링크. 테스트 청크의 `test_for` 가 가리키는 심볼의 모든 후보에 `test_path#test_symbol` push. 상한 5개. heuristic 특성상 Python `test_X` / `X_test.py` 컨벤션에 강한 반면 JS mocha/jest 의 anonymous `describe`/`it` 스타일은 매칭 약함.
- **`is_test`** (P1 신규): 테스트 디렉토리/파일 내 청크면 `true`. build_kb_query 가 `test_for: X` 라인을 쿼리에 포함할 때 BM25 가 이 플래그와 함께 매칭한다.

> **왜 함수 단위인가?** 파일 전체를 하나의 문서로 넣으면 임베딩 품질이 떨어지고, "이 이슈가 어떤 함수에서 발생하는지" 검색이 어려워집니다. 함수 단위가 "맥락 일관성 + 검색 정확성" 의 최적 균형점입니다.
>
> **pass 2 역인덱스의 효과**: callers / test_paths 필드는 `doc_processor.py` 가 청크 본문 뒤 `--- path: ... callers: ... test_paths: ...` 형태로 text footer 에 병합한다. Dify hybrid search 의 BM25 가 metadata 까지 토큰화하므로, P3 의 `build_kb_query` 가 `callers: loginUser` 같은 구조화 쿼리 라인을 보내면 **"loginUser 를 부르는 정의 청크"** 가 직접 매칭된다. 별도 metadata filter API 에 의존하지 않는다.

#### 8.2.3 Step 2.5 — Contextual Enrichment (`contextual_enricher.py`, 선택)

**Anthropic Contextual Retrieval** 기법 적용: 각 청크의 코드 앞에 **"이 함수가 무슨 일을 하는지 1~2 줄 요약"** 을 붙입니다.

실행 조건: `ENRICH_CONTEXT=true` (기본값). Ollama `gemma4:e4b` 로 청크당 1회 호출 (temperature=0.2, num_predict=120).

요약 prepend 포맷 예시:

```
[src/auth.py:16-23] 역할: 사용자 이름/비밀번호로 로그인 검증. bare except 로 모든 예외를 삼키는 문제가 있음.

def login(username: str, password: str, user_store: dict) -> bool:
    try:
        stored = user_store[username]
        return verify_password(password, stored)
    except:
        return False
```

**실패 정책**: 요약 생성 실패 (Ollama 장애 등) 해도 청크는 요약 없이 원본 코드 그대로 보존 (graceful fallback). 전체 파이프라인은 계속 진행.

> **왜 이 과정이 필요한가?** 임베딩 검색은 "의미 유사성" 만 봅니다. 코드 자체는 변수명·문법 기호로 이루어져 임베딩이 약합니다. 앞에 "자연어 요약" 을 붙이면 **"로그인 검증"** 같은 질의어로도 이 청크가 검색됩니다 — RAG 적중률 20~40% 개선 (Anthropic 보고).

#### 8.2.4 Step 3 — Dify Dataset 업로드 (`doc_processor.py`)

JSONL 각 라인 (= 1 청크) 을 **Dify 의 1 document** 로 업로드:

- **Dataset**: `code-context-kb` (provision.sh 가 자동 생성)
- **인덱싱 모드**: `high_quality` — Dify 1.13+ 의 개선된 청킹 + 청크 오버랩. 경제 모드보다 저장 용량·시간은 늘지만 검색 정확성 우선.
- **임베딩**: **`bge-m3`** — 다국어(한글 포함) 지원 SOTA 임베딩. 코드 주석/식별자에 한국어 섞여도 대응.
- **검색 모드**: **`hybrid_search`** (BM25 + 벡터) — 코드는 **정확 키워드 매칭 (BM25)** 이 중요한 동시에 **의미 유사성 (벡터)** 도 필요. Dify 가 두 점수를 가중합.

업로드 완료 후 `/data/kb_manifest.json` 기록:

```json
{
  "repo_url": "http://gitlab:80/root/realworld.git",
  "branch": "main",
  "commit_sha": "ee17e31aafe733d98c4853c8b9a74d7f2f6c924a",
  "analysis_mode": "full",
  "uploaded_at": 1776796293,
  "document_count": 5,
  "dataset_id": "5156d41f-6e9b-4c43-ae51-0b3b34c1a33d"
}
```

P2/P3 가 이 파일로 KB freshness 를 검증합니다.

#### 8.2.4.1 Pre-training Report (KB **빌드** 지표 대시보드)

Stage 2 의 `repo_context_builder.py` 가 `--report-html` 옵션으로 **self-contained HTML 리포트** 를 생성합니다 (`pretraining-report/pretraining-report.html`). Jenkinsfile 02 의 post 블록이 `publishHTML` 로 빌드 페이지에 "Pre-training Report" 탭을 만들어 주므로, 05 AI평가 의 Evaluation Summary 처럼 **해당 Jenkins Build 페이지에서 바로 클릭해 탐색** 할 수 있습니다.

> **⚠ 용도 범위 명확화 (P1.5)**: 이 리포트는 **KB 빌드 품질** 지표입니다 — "tree-sitter pass 2 역인덱스가 채워졌는가, vendor 노이즈가 걸러졌는가, 지원 언어가 얼마나 커버됐는가" 를 숫자로 보여줍니다.
>
> **"Sonar 이슈 분석이 실제로 개선됐는가" 를 보려면 § 8.4.2.1 의 RAG Diagnostic Report 를 보세요.** Pre-training Report 는 "KB 가 빌드되긴 했다" 는 전제 조건 체크 — 이슈 분석 품질은 다른 리포트가 측정합니다.

리포트에 들어가는 섹션:

| 섹션 | 목적 |
| --- | --- |
| 헤더 | repo / commit / 실행 시각 |
| 통계 카드 6종 | `files`, `chunks`, `callers_links`, `test_links`, `is_test chunks`, `orphan ratio` |
| 언어 분포 표 | `.py / .java / .ts / .tsx / .js` 청크 비율 (bar 포함) |
| Kind 분포 표 | function / method / class / interface / enum 비율 |
| 상위 caller hub | callers 배열이 긴 심볼 top-10 — 레포 공용 유틸 식별용 |
| 고아 심볼 비율 | callers 가 0인 청크 비율. 80% 초과 시 파싱/역인덱싱 결함 의심 |
| 언어별 샘플 | 언어별 첫 3 청크 미리보기 (code + metadata + 링크 카운트) |

pass 2 역인덱스의 실효성을 **숫자로 즉시 확인** 할 수 있게 하는 게 주목적입니다. `callers_links` 가 0 에 가깝거나 orphan_ratio 가 과도하게 높으면 (예: 90%+) 동명 심볼 매칭이 의도대로 작동하지 않는 신호 — 레포 전수 스캔이 제대로 안 됐거나 (언어 미지원), 심볼이 모두 import 해서 쓰는 모듈로만 되어 있어 로컬 callees 집합이 비는 구조일 가능성이 있습니다.

placeholder 동작: Stage 2 실패로 리포트 파일이 생성 못 되면 post 블록이 "왜 빈지" 안내하는 HTML 을 대신 써서 탭이 비어 있는 이유를 바로 보여줍니다. artifact 로도 `pretraining-report/pretraining-report.html` 이 archive 되므로 URL 공유 / 다운로드 가능.

> **왜 HTML 인가?** Stage View 의 텍스트 로그 한 줄 (`files=25 chunks=147 callers_links=141 test_links=0`) 은 빌드마다 묻혀 P1 개선이 실제로 효과 있는지 체감이 안 됩니다. 리포트 탭은 build URL 만 공유하면 팀이 즉시 RAG KB 품질 추세를 공유할 수 있습니다. 05 AI평가의 Evaluation Summary 와 동일한 UX 패턴.

#### 8.2.4.2 임베딩 / reranker 모델 교체 가이드 (P2 K-5 / R-1 운영자 경로)

기본 임베딩은 **Ollama bge-m3** (다국어 일반 임베딩) 이고, reranker 는 **`weighted_score`** (cross-encoder 모델 없이 keyword 0.3 / vector 0.7 가중) 입니다. 이는 오프라인 환경에서 Ollama 가 코드 특화 임베딩 / cross-encoder reranker 를 공식 제공하지 않는 제약 때문입니다. 운영자가 별도 임베딩/reranker 인프라 (Xinference / LocalAI / sentence-transformers 서비스) 를 추가할 수 있을 때 다음 절차로 교체합니다.

**임베딩 모델 교체** — `provision.sh` 가 환경변수로 받습니다:

```bash
# docker-compose.mac.yaml 의 ttc-allinone 서비스 environment 에 추가:
OLLAMA_EMBEDDING_MODEL: nomic-embed-code   # 또는 jina-embeddings-v2-base-code 등
OLLAMA_EMBEDDING_PROVIDER: langgenius/openai_compat/openai_compat   # Xinference 가 OpenAI 호환 API 제공 시
```

호스트에 해당 임베딩 서비스를 띄우고 (예: `xinference launch -n nomic-embed-code -t embedding`), Dify provider 페이지에서 OpenAI-Compatible 또는 Xinference 플러그인으로 모델 등록한 뒤 `~/ttc-allinone-data` 를 wipe + 재기동하면 새 임베딩으로 KB 가 재생성됩니다. (기존 dataset 의 임베딩을 swap 할 수는 없고 **재생성** 만 가능 — Dify high_quality 모드 제약.)

**reranker 모델 도입** — `sonar-analyzer-workflow.yaml` 의 `knowledge_retrieval` 노드:

```yaml
multiple_retrieval_config:
  reranking_enable: true
  reranking_mode: reranking_model      # weighted_score → reranking_model
  reranking_model:
    provider: langgenius/xinference/xinference
    model: bge-reranker-v2-m3
```

provider/model 이름은 Dify provider 등록 시 노출된 정확한 식별자를 써야 합니다. 변경 후 provision 재실행 (workflow 재import) 으로 반영. 효과는 **§8.4.2.1 RAG Diagnostic Report** 의 `avg citation rate` 변화로 측정합니다 — reranker 도입 전후 같은 golden set 으로 §8.4.2.2 의 자동 평가를 돌려 비교하는 것이 가장 정확합니다.

> **현재 안 하는 이유**: bge-reranker / nomic-embed-code 같은 모델은 GGUF 패키지가 안정적이지 않거나 Ollama 가 reranker API 자체를 미제공. 별도 서비스 (Xinference) 를 통합 이미지에 묶으면 이미지 사이즈 증가 + 운영 표면 확대. 효과 측정 데이터를 모은 뒤 도입 결정하는 것이 합리적이며, 이 가이드는 그 전환을 1단계 작업으로 만들기 위한 사전 정비.

#### 8.2.5 Step 4 — Dify Indexing 완료 대기 (`dify_kb_wait_indexed.py`)

`doc_processor.py` 업로드가 반환하는 시점에 Dify 는 문서를 **비동기 임베딩 큐**에 올려 둡니다. bge-m3 임베딩 + Qdrant 색인이 실제로 끝나기 전에 P3 의 `knowledge_retrieval` 노드를 호출하면 top_k 검색이 **빈 결과** 를 돌려주고, LLM 입력의 RAG 컨텍스트가 통째로 비어 분석 품질이 급격히 저하됩니다.

Stage 4 는 이 race condition 을 제거합니다:

- `/v1/datasets/{id}/documents` 를 10초 간격으로 polling
- 모든 문서가 **terminal 상태** (`completed` 또는 `error` / `disabled` / `archived` / `paused`) 가 될 때까지 대기
- 기본 timeout 30분 (`--timeout 1800`)
- `completed` 이외 상태가 섞여 있어도 **WARN 로그만 남기고 파이프라인은 계속** — RAG 결과가 축소될 뿐, 전체 실패로 이어지지 않음

이 스테이지가 완료되어야 P1 Job 이 SUCCESS 를 반환하고, 체인 오케스트레이터 `01-코드-분석-체인` 이 P2 로 넘어갑니다.

> **왜 Stage 별도인가?** `doc_processor.py` 안에서 polling 하지 않는 이유는 (1) upload 실패와 indexing 실패를 **별도 Stage 실패** 로 관측하기 위함이고, (2) Jenkins Stage View 에서 "업로드 20초 · 색인 4분" 같은 단계별 소요 시간을 그대로 드러내 문제 진단이 쉽기 때문입니다.

---

### 8.3 `03-코드-정적분석` (P2) — SonarQube 스캔

**역할**: 지정된 커밋 스냅샷에 대해 SonarQube 스캐너를 실행해 규칙 위반 사항을 SonarQube 서버에 등록합니다. 기존 SonarQube 사용 방식과 동일하나, 커밋 SHA 가 명시적으로 고정되고 P1 의 KB 와 일관성이 보장된다는 점이 다릅니다.

**성과**: SonarQube 대시보드에 해당 커밋의 이슈 레코드가 적재됩니다. 이 결과는 다음 P3 파이프라인의 입력이 됩니다.

#### 8.3.1 Step 0 — KB Bootstrap Guard

COMMIT_SHA 가 전달된 경우에만 작동:

| 실행 경로 | ANALYSIS_MODE | manifest 상태 | 동작 |
|:----:|:----:|:---:|:---|
| 체인 경유 | `full` | 일치 | 진행 |
| 체인 경유 | `full` | 불일치 | **fail loud** (체인 Stage 2 P1 실패 의심 — 원인 제공) |
| 단독 실행 | `commit` | 일치 | 진행 |
| 단독 실행 | `commit` | 불일치/부재 | **P1 자동 트리거** (wait+propagate) |
| (COMMIT_SHA 빈 값) | — | — | Guard skip (하위호환) |

> **왜 full 모드는 재트리거 대신 fail 로 끝내나?** full 모드 체인은 이미 Stage 2 에서 P1 을 wait 하며 executor 1 개를 잡고 있습니다. 여기서 또 wait 로 P1 을 호출하면 Jenkins 기본 executor 2 개를 모두 소진해 deadlock. 체인을 신뢰하고 guard 는 검증만 담당.

#### 8.3.2 Step 1~4 — Checkout + Sonar 분석

1. Git clone → `git checkout ${COMMIT_SHA}` 로 고정 (분석 스냅샷 확정)
2. Node.js v22 준비 (SonarJS 용, 최초 1 회 캐시)
3. `withSonarQubeEnv('dscore-sonar')` + `tool 'SonarScanner-CLI'` 로 SonarScanner 실행
4. Sonar 서버에 분석 리포트 전송 → [http://localhost:29000/dashboard?id=realworld](http://localhost:29000/dashboard?id=realworld)

---

### 8.4 `04-정적분석-결과분석-이슈등록` (P3) — LLM 분석 + Issue 생성

**역할**: SonarQube 에 쌓인 규칙 위반 이슈들을 하나씩 처리합니다. 각 이슈에 대해 (1) 파일·함수·호출 관계·커밋 이력 같은 사실 정보를 보강하고, (2) P1 에서 만든 RAG KB 에서 관련 코드를 검색해 LLM 에게 분석을 맡기고, (3) 오탐은 SonarQube 에 자동 마킹하며, (4) 진짜 문제는 GitLab Issue 로 등록합니다.

**성과**: 개발자에게 도달하는 것은 **조치가 필요한 GitLab Issue 만** 입니다. 각 Issue 는 문제 코드·위치·영향 범위·수정 방향·관련 링크를 모두 포함해, 별도 조사 없이 바로 수정 판단으로 넘어갈 수 있는 상태로 정리되어 있습니다.

이 파이프라인은 3 개 Python 스크립트가 릴레이로 실행되며, 각 단계는 다음과 같습니다.

#### 8.4.1 Stage (1) Export — `sonar_issue_exporter.py`

**역할**: Sonar API 에서 이슈를 수집한 뒤 RAG·LLM 이 활용할 수 있도록 **대대적 보강**.

처리 순서:

1. Sonar `/api/issues/search` 페이지네이션 순회 (severity / status 필터)
2. 각 이슈의 `rule` 을 `/api/rules/show` 로 조회 (캐싱)
3. 각 이슈 라인 ±50 줄 코드를 `/api/sources/lines` 로 가져와 `>>` 마커 부착
4. **보강 필드 추가**:

| 필드 | 공식 / 소스 | 용도 |
|------|-----------|------|
| `relative_path` | `component` 에서 프로젝트키 prefix 제거 | 이슈 위치 표시 |
| `enclosing_function` / `enclosing_lines` | P1 의 `extract_chunks_from_file` 재사용 → 이슈 라인을 포함하는 함수의 symbol/lines | "어느 함수인지" 식별 |
| `git_context` | `git blame -L <line>,<line> --porcelain` + `git log -1 --format='%an\|%ar\|%s'` | "누가 언제 썼는지" 맥락 |
| `direct_callers` | P1 JSONL 전체를 로딩해 `callees` 역인덱스 → `symbol` 을 호출하는 `path::symbol` 목록 (최대 10) | "이 함수가 어디서 호출되는지" 파급효과 분석 |
| `cluster_key` | `sha1(rule_key + enclosing_function + dirname(component))[:16]` | 같은 패턴 이슈 묶기 |
| `judge_model` | severity 라우팅 맵 | 어떤 LLM 을 쓸지 |
| `skip_llm` | severity 가 MINOR/INFO/UNKNOWN 이면 `true` | Dify 호출 생략 여부 |
| `affected_locations` | clustering 결과 — 대표 이슈에 다른 위치 리스트 부착 | 중복 이슈 1건으로 통합 |

**Severity 라우팅** 매핑:

| Sonar Severity | judge_model | skip_llm | 의미 |
|----------------|-------------|:--------:|------|
| `BLOCKER` | `qwen3-coder:30b` | false | 가장 심각 — 정밀 모델로 분석 |
| `CRITICAL` | `qwen3-coder:30b` | false | 위와 동일 |
| `MAJOR` | `gemma4:e4b` | false | 빠른 모델로 분석 |
| `MINOR` / `INFO` / 기타 | `skip_llm` | true | LLM 호출 생략, 템플릿 응답으로 GitLab Issue 생성 |

**Clustering** — 같은 `cluster_key` 이슈들을 묶음:

- 대표 1건만 top-level 로 emit → 나머지는 대표의 `affected_locations` 배열로 접힘
- 대표 선정: severity 가장 심각 → line 번호 작은 순
- **효과**: 같은 함수 안의 같은 규칙 위반 여러 건이 1 개 GitLab Issue + 테이블로 정리 → LLM 호출 비용 절감 + 이슈 가독성 향상

**Diff-mode** (`--mode incremental`):

- `/var/knowledges/state/last_scan.json` 에 저장된 직전 스냅샷 이슈 키 집합과 symmetric diff
- 기존 키는 emit 건너뛰고 신규 이슈만 처리 → **반복 실행 시 이미 본 이슈 재분석 방지**
- 실행 끝에 현재 이슈 키 집합으로 `last_scan.json` 덮어씀 (다음 실행의 baseline)

#### 8.4.2 Stage (2) Analyze — `dify_sonar_issue_analyzer.py`

**역할**: 각 이슈를 Dify `Sonar Issue Analyzer` Workflow 에 전달해 LLM 판단 결과를 받음.

**Multi-query `kb_query` 구성** (P1 구조화 확장 + P2 R-4 retry variation) — attempt 별로 다른 모양의 쿼리를 보낸다. 동일 쿼리 3회 반복의 효율 한계를 넘어 누적 recall 확보:

| attempt | 모양 | 의도 |
| --- | --- | --- |
| 0 (1차) | 풀 구조화 6 라인 (코드창 + function/callees/test_for + is_test + path + rule_name) | 평소 케이스 |
| 1 (2차) | rule_name + sonar_message + function + path | 자연어 의미 매칭 가중 |
| 2 (3차 — 최후) | enclosing_function + callees/callers + (옵션) **HyDE 자연어** | 식별자 정확 매칭 + LLM 생성 자연어 보강 |

**HyDE (P2 R-3, 간소화)**: attempt=2 에 한해 `analyzer.py` 가 호스트 Ollama 로 한 줄 자연어 변환 호출 (`이 코드는 X 를 Y 한다` 형태) 후 kb_query 에 추가. 일반 케이스 latency 영향 0, retry 까지 가는 ~10% 케이스만 부담. Jenkins 환경변수 `--hyde-ollama-base-url` 빈 값으로 두면 비활성. 효과는 §8.4.2.2 자동 평가로 measure.

**기본 (attempt=0) 6 라인 예시**:

```
<이슈 라인 ±3~4 줄 코드창>
function: <enclosing_function>
callees: <enclosing_function>        ← P1 신규 — 이 함수를 "부르는" caller 정의 유도
test_for: <enclosing_function>       ← P1 신규 — 이 함수를 "테스트하는" 청크 유도
path: <relative_path>
<rule_name>
```

예시:
```
      19 |         stored = user_store[username]
      20 |         return verify_password(password, stored)
>>    21 |     except:
      22 |         return False
function: login
callees: login
test_for: login
path: src/auth.py
"SystemExit" should be re-raised
```

`callees: login` 라인은 "누군가의 callees 목록에 login 이 들어있는 청크" 를 유도한다 — 즉 **login 을 호출하는 caller 정의**. Dify hybrid search 의 BM25 가 청크 text footer 의 `callees: verify_password, login` 라인과 매칭하기 때문에 가능 (pass 2 역인덱스와 별도로 작동). `test_for: login` 은 login 을 대상으로 한 테스트 청크의 footer `test_for: login` 또는 `is_test: true` 와 매칭.

**Dify Workflow 노드 구성** (`sonar-analyzer-workflow.yaml`, P1 개편 후):

```
start
  ↓ (12 inputs: sonar_issue_key, code_snippet, kb_query, enclosing_function,
  ↓             commit_sha, issue_file_path (P1 신규), retry_hint, ...)
knowledge_retrieval   [code-context-kb, retrieval_mode=multiple, top_k=15 (P1 확장)]
  ↓ (result — 검색된 청크 top-15)
context_filter        [P1 신규 — Code 노드]
  │   · self-exclusion: issue_file_path 와 동일 path 청크 제거 (자기 자신 반환 회피)
  │   · metadata footer 파싱 → 카테고리 분류
  │       callers 버킷  : 다른 곳에서 호출되는 정의 청크 (callers footer 비어있지 않음)
  │       tests 버킷    : 테스트 청크 (is_test=true)
  │       others 버킷   : 위 둘에 안 걸리는 일반 청크
  │   · 카테고리별 top-3 → markdown 섹션화 (총 최대 9 청크)
  ↓ (filtered_context — 섹션화 markdown 문자열)
llm_analyzer          [gemma4:e4b, temperature=0.1, max_tokens=2048]
  ↓ (JSON text)
json_parser           [Code 노드 — json.loads deterministic 파싱 + 안전 기본값]
  ↓
end                   [8 outputs 반환]
```

> **Context 노드 도입 이유**: 이전에는 `knowledge_retrieval.result` 를 LLM 프롬프트에 그대로 주입했는데, 이 KB 가 **동일 repo** 로 구성되어 있어 top-1 이 거의 항상 이슈 파일 자기 자신이었다 ("self-retrieval degenerate case"). LLM 은 방금 본 code_snippet 을 RAG 결과에서 다시 받아 새 정보량 0. P1 에서 `context_filter` Code 노드가 `issue_file_path` 와 동일 path 청크를 제거하고 나머지를 카테고리별로 섹션화해 LLM 에 전달하도록 바꿨다.

**LLM 출력 8 필드**:

| 필드 | 타입 | 설명 |
|------|------|------|
| `title` | string (80자) | GitLab Issue 제목 |
| `labels` | array[string] | 도메인 라벨 (예: Authentication, Code Smell) |
| `impact_analysis_markdown` | string (3~6줄) | **RAG 결과 녹여 작성 — 호출 관계 기반 해석** |
| `suggested_fix_markdown` | string | 수정안. 코드펜스 1개. 불명확하면 빈 값 |
| `classification` | enum | `true_positive` / `false_positive` / `wont_fix` |
| `fp_reason` | string | classification=false_positive 시만 이유 기록 |
| `confidence` | enum | `high` / `medium` / `low` — 본 판정 확신도 |
| `suggested_diff` | string | unified diff (있을 때만). `suggested_fix_markdown` 과 별개 |

**skip_llm 분기** — `skip_llm=true` 이슈는 Dify 호출 자체 생략:

```python
outputs = {
    "title": f"[{severity}] {sonar_message}",
    "labels": [f"severity:{severity}", "classification:true_positive",
               "confidence:low", "auto_template:true"],
    "impact_analysis_markdown": "(자동 템플릿 — MINOR/INFO Severity. 수동 리뷰 권장.)",
    "suggested_fix_markdown": "",
    "classification": "true_positive",
    "fp_reason": "",
    "confidence": "low",
    "suggested_diff": "",
}
```

→ 콘솔에 `[SKIP_LLM] {key}` 출력 + `out_row["llm_skipped"] = True`.

결과물: `llm_analysis.jsonl` — 이슈당 1 줄 JSON (exporter 의 사실 정보 + LLM outputs 통합).

#### 8.4.2.1 RAG Diagnostic Report (P1.5 — 이슈 분석 **품질** 지표)

04 Jenkinsfile 의 `post { always }` 가 `diagnostic_report_builder.py` 로 `llm_analysis.jsonl` 을 집계해 **per-이슈 진단 리포트** 를 생성하고 `publishHTML` 로 "RAG Diagnostic Report" 탭을 띄웁니다.

**상단 카드 지표** — 이슈 N 개에 대한 집계:

| 카드 | 의미 |
| --- | --- |
| `total issues` | Sonar 이슈 총 수 (분석 대상) |
| `LLM calls` | Dify 호출이 실제로 일어난 이슈 수 (skip_llm 제외) |
| `callers bucket filled` | 이슈 중 **callers 청크를 최소 1개라도 받은 비율** — 낮으면 pass 2 역인덱스가 실 이슈와 매칭을 못 했다는 신호 |
| `tests bucket filled` | 동일. test heuristic 한계 가시화 |
| `others bucket filled` | fallback 매칭 비율 |
| `avg citation rate` | **LLM 이 `impact_analysis_markdown` 에서 used_items 의 path/symbol 을 실제로 인용한 비율**. 이게 높을수록 RAG 정보가 답변에 녹아들었다는 뜻 — 낮으면 LLM 이 컨텍스트를 무시한 것 |
| `context:empty` | C-3 가드가 발동한 이슈 비율 (RAG 가 비어서 confidence=low 강제) |
| `retry exhausted` | 3회 재시도 후에도 빈 응답이 돌아온 이슈 비율 |

**이슈별 상세** — 이슈마다 한 블록:

- Severity / confidence / classification 뱃지
- `retrieved / excluded_self / kept / used` 단계별 카운트
- 버킷별 `used/total` (예: callers 3/4 = 4개 후보 중 3개 사용)
- `used_items` 테이블: bucket · `path::symbol` · score · **cited?** (LLM 이 실제 인용했는지 ✓)
- `impact_analysis_markdown` 접기 섹션

**이 리포트가 Pre-training Report 와 다른 점**:

| 측면 | Pre-training Report | RAG Diagnostic Report |
| --- | --- | --- |
| 대상 | KB 전체 (거시) | 이슈 하나하나 (미시) |
| 측정 | 청크 수, callers_links, 언어 분포 | LLM 이 RAG 를 **얼마나 사용했는가** |
| 언제 보나 | 파이프라인 01 직후 — "KB 빌드 OK?" | 파이프라인 04 직후 — "이슈 분석 품질 OK?" |
| 실패 신호 | callers_links=0, orphan 90%+ | citation rate 5%↓, tests bucket 0%, retry exhausted 30%+ |

> **해석 가이드**: 좋은 상태는 `callers bucket filled > 60%`, `avg citation rate > 30%`, `retry exhausted < 10%`. 이 값들이 악화되면 §8.2 의 KB 필터 / retrieval 가중치 / prompt 를 순서대로 의심하세요.

#### 8.4.2.2 RAG Quality 자동 평가 (P2 M-6 — 변경 전후 metric 비교)

Diagnostic Report 가 "현재 어떤 상태인가" 를 보여준다면, `eval_rag_quality.py` 는 **사람이 라벨링한 정답지 (golden CSV)** 와 분석 결과를 비교해 "변경 전후가 얼마나 좋아졌나" 를 숫자로 만듭니다. KB 필터 / reranker / 프롬프트를 바꿀 때 baseline 측정 → 변경 적용 → 다시 측정으로 회귀/개선을 객관적으로 판단할 때 씁니다.

**Step 1 — Golden CSV 작성**: `sample-projects/golden_template.csv` 를 시작점으로 복사. 컬럼:

| 컬럼 | 의미 |
| --- | --- |
| `sonar_issue_key` | Sonar 이슈 key (`sonar_issues.json` 의 `key` 필드) |
| `expected_classification` | `true_positive` / `false_positive` / `wont_fix` |
| `expected_confidence_min` | `high` / `medium` / `low` — LLM 답이 최소 이 이상이어야 통과 |
| `expected_keywords` | impact_analysis_markdown 에 등장해야 할 키워드 (`;` 구분) |
| `expected_cited_paths` | LLM 이 인용해야 할 파일 path (`;` 구분, 빈 값이면 검사 skip) |

20~50 행이면 metric 이 안정적입니다. 처음에는 BLOCKER / CRITICAL severity 이슈만으로 시작해도 충분.

**Step 2 — 분석 실행 + 평가**:

```bash
# 04 파이프라인을 한 번 돌려 llm_analysis.jsonl 생성한 뒤
docker exec ttc-allinone python3 /opt/pipeline-scripts/eval_rag_quality.py \
  --golden /var/knowledges/eval/data/golden.csv \
  --analysis /data/jenkins/workspace/04-정적분석-결과분석-이슈등록/llm_analysis.jsonl \
  --output /tmp/eval-result.json

# 콘솔에 한 줄 요약, --output JSON 에 이슈별 상세
# matched=18/20 classification_accuracy=72.2% confidence_pass_rate=88.9% \
#  keyword_coverage_avg=54.3% citation_precision_avg=41.7%
```

**Step 3 — 변경 전후 비교**: 같은 golden 으로 다른 시점 (또는 다른 KB 설정) 의 결과를 비교. 예: reranker 도입 전 `eval-baseline.json`, 도입 후 `eval-after.json` 두 파일의 metric 차이가 효과 측정.

**해석 가이드**:

- `classification_accuracy` 가 70% 미만이면 LLM 프롬프트 (분류 기준 명시) 를 의심.
- `keyword_coverage_avg` 가 30% 미만이면 RAG 가 충분한 컨텍스트를 안 주고 있음 — KB 빌드 (vendor 필터 / 청크 단위) 를 점검.
- `citation_precision_avg` 가 20% 미만이면 retrieval 자체가 무관한 청크를 가져오는 중 — score_threshold 를 올리거나 reranker 도입 검토.
- 4개 metric 이 모두 동시 개선되어야 진짜 개선. 한두 개만 좋아지고 다른 게 떨어지면 trade-off 일 가능성.

#### 8.4.3 Stage (3) Create — `gitlab_issue_creator.py`

**역할**: `llm_analysis.jsonl` 를 읽어 GitLab Issue 로 자동 등록.

**Dual-path FP 처리**:

```
classification == "false_positive" ?
  │
  ├─ Yes → Sonar POST /api/issues/do_transition?transition=falsepositive
  │         ├─ 성공 → GitLab Issue 생성 skip. fp_transitioned++
  │         └─ 실패 → GitLab Issue 는 생성하되 `fp_transition_failed` 라벨 추가.
  │                     fp_transition_failed++
  │
  └─ No → 정상 GitLab Issue 생성
```

**Dedup**: 같은 Sonar key 를 가진 기존 Issue 가 있으면 skip (`p3_summary.skipped++`).

**Labels 병합**:

```
LLM 제안 라벨 + severity:<sev> + classification:<cls> + confidence:<conf>
  + (skip_llm 이면) auto_template:true
  + (FP 전이 실패 시) fp_transition_failed
  → 중복 제거 → GitLab 에 쉼표 구분 문자열로 POST
```

**본문 렌더 — `render_issue_body()` 8 섹션 구조** (고정 순서, 조건부 생략):

| 순서 | 섹션 | 생성 조건 | 내용 출처 |
|:---:|------|-----------|:---------:|
| 1 | TL;DR callout (`> **TL;DR** — ...`) | **항상** | row 의 사실 정보 |
| 2 | 📍 위치 테이블 | **항상** | row + 공개 URL 조합 |
| 3 | 🔴 문제 코드 | `snippet != "(Code not found)"` | exporter snippet (±10줄로 trim) |
| 4 | ✅ 수정 제안 | `outputs.suggested_fix_markdown` 비어있지 않음 | LLM |
| 5 | 💡 Suggested Diff | `outputs.suggested_diff` 가 null/empty/"none" 아님 | LLM |
| 6 | 📊 영향 분석 | **항상** (LLM 값 없으면 placeholder) | LLM |
| 7 | 🧭 Affected Locations | `row.affected_locations` 비어있지 않음 (최대 20건 표) | exporter clustering |
| 8 | 📖 Rule 상세 | `rule_description` 비어있지 않음 (`<details>` 접기) | Sonar rule detail |
| 9 | 🔗 링크 | 공개 URL 구성 가능 시 | 자동 조합 |
| footer | `commit: <sha> (<mode> scan) · sonar: <url>` | `commit_sha` 있을 때 | row |

**결과물**: `gitlab_issues_created.json`:

```json
{
  "created": [{"key": "...", "title": "..."}],
  "skipped": [{"key": "...", "reason": "Dedup"}],
  "failed": [],
  "fp_transitioned": [],
  "fp_transition_failed": []
}
```

01 체인의 Stage 5 가 이 파일을 읽어 `p3_summary` 로 집계합니다.

---

### 8.5 `05-AI평가` — 11 지표 × 5 단계 AI 응답 자동 평가

> **TL;DR** — 운영 중인 AI 에이전트(챗봇·RAG 시스템·LLM 워크플로) 의 응답 품질을 **다른 LLM 에게 채점을 맡겨(LLM-as-Judge)** Golden Dataset 기반으로 자동 재검증합니다. `00~03` 코드 품질 체인과는 **대상(소스 코드 vs 운영 AI)과 트리거 주기(커밋 vs 평가 정책)가 다른** 동등한 일급 파이프라인 — 동일 통합 이미지에서 공존 실행됩니다. 같은 시험지로 반복 실행하므로 품질 회귀를 즉시 잡아냅니다. **모든 추론은 호스트 Ollama** 에서 수행 — 외부 API 의존·데이터 유출 0.
>
> 실제 사용법은 §7.7 에 Step 0~7 로 정리되어 있습니다. 여기서는 각 지표·모드·파라미터·결과물·내부 동작을 **참조용**으로 자세히 설명합니다.

#### 8.5.1 평가 지표 — 11 항목 × 5 단계 (Fail-Fast)

한 건의 평가(대화 또는 단일 질의)는 아래 5 단계를 차례로 통과합니다. 앞 단계가 실패하면 뒷 단계는 `skipped` 로 기록 후 종료됩니다 (Fail-Fast — LLM 호출 비용 절감).

```text
Stage 1 포맷·정책 ─┬─ PASS ─► Stage 2 과제검사 ─┬─ PASS ─► Stage 3 심층평가 ─┬─ PASS ─► Stage 4 다중턴 ─► Stage 5 운영지표
                   └─ FAIL ─► (2~5 skipped)        └─ FAIL ─► (3~5 skipped)       └─ FAIL ─► (4~5 skipped)
```

**1단계 — 포맷·정책** (LLM 호출 없음, 결정론적):

| # | 지표 | 이 지표가 하는 일 | 언제 중요한가 | 통과 기준 |
|:-:|------|------------------|-------------|----------|
| ① | Policy Violation | 응답에 PII(주민번호·이메일·전화)·API key·금칙어가 있는지 정규식으로 검사 | 대상 AI 가 민감정보를 노출하는지 사전 차단 | 위반 0건 |
| ② | Format Compliance | 응답이 사전 정의 JSON 스키마를 준수하는지 jsonschema 검증 | `http` 모드의 JSON API 응답에만 의미. `ui_chat` 은 자동 skip | 스키마 일치 |

**2단계 — 과제검사**:

| # | 지표 | 이 지표가 하는 일 | 언제 중요한가 | 통과 기준 |
|:-:|------|------------------|-------------|----------|
| ③ | Task Completion | 시험지 `success_criteria` 를 답변이 만족했는지 — DSL 패턴은 결정론, 자연어는 LLM Judge 가 채점 | "이 답변이 질문의 본래 의도를 충족했는가" | 점수 ≥ 0.5 (내부 기본) |

**3단계 — 심층평가** (모두 DeepEval 라이브러리의 LLM-as-Judge 메트릭):

| # | 지표 | 이 지표가 하는 일 | 언제 중요한가 | 통과 기준 |
|:-:|------|------------------|-------------|----------|
| ④ | Answer Relevancy | 질문과 답변이 의미상 관련 있는지 | 동문서답 / off-topic 감지 | 점수 ≥ `ANSWER_RELEVANCY_THRESHOLD` (기본 0.7) |
| ⑤ | Toxicity | 유해·공격·차별 표현 포함 여부 (낮을수록 좋음) | 서비스 배포 전 안전성 | 점수 ≤ 0.5 |
| ⑥ | Faithfulness * | 답변이 검색 문맥(`retrieval_context`)에 충실했는지 | **RAG 시스템 환각(hallucination) 감지 핵심** | 점수 ≥ 0.7 |
| ⑦ | Contextual Recall * | 정답에 필요한 정보가 retrieval 결과에 얼마나 포함됐는지 | 검색 단계가 정보 누락했는지 판단 | 점수 ≥ 0.7 |
| ⑧ | Contextual Precision * | retrieval 결과 중 정답에 실제 관련된 비율 | 검색이 불필요한 문서를 끼워넣었는지 판단 | 점수 ≥ 0.7 |

`*` = 시험지의 `context_ground_truth` 컬럼이 채워진 케이스에서만 평가 (비면 skip).

**4단계 — 다중턴**:

| # | 지표 | 이 지표가 하는 일 | 언제 중요한가 | 통과 기준 |
|:-:|------|------------------|-------------|----------|
| ⑨ | Multi-turn Consistency | 같은 `conversation_id` 의 여러 턴 답변이 서로 모순되지 않는지, 이름·설정 등을 기억하는지 | 대화 맥락 유지 능력 검증. 대화 전체 종료 후 1회 채점 | 점수 ≥ 0.7 |

**5단계 — 운영지표** (합/불 없음, 추세 모니터링):

| # | 지표 | 이 지표가 하는 일 | 언제 중요한가 |
|:-:|------|------------------|-------------|
| ⑩ | Latency | 응답까지 걸린 시간 (ms) | 응답 속도 p50/p95/p99 추세 추적 |
| ⑪ | Token Usage | 프롬프트/완료/총 토큰 수 | API 비용·응답 길이 추세 추적 |

**채점 주체 요약**: ③④⑥⑦⑧⑨ 는 **LLM-as-Judge** (DeepEval 메트릭이 호스트 Ollama 의 Judge 모델 호출). ⑤ 는 LLM + 사전 결합. ①② 는 결정론적 (LLM 호출 없음). ⑩⑪ 는 어댑터 실측값 기록만.

#### 8.5.2 평가 대상 지원 방식 — 3 가지 모드

Jenkins 빌드 화면에서 `TARGET_TYPE` 으로 선택.

| TARGET_TYPE | 평가 대상 AI | 어댑터 파일 | 사용 시나리오 |
|-------------|------------|----------|-------------|
| `local_ollama_wrapper` | 컨테이너 내부 wrapper 가 호스트 Ollama 모델을 호출 | `ollama_wrapper_api.py` (자동 기동) + `http_adapter.py` | 새 모델 도입 전 smoke / 자기평가 / 외부 의존성 없는 회귀 검증 |
| `http` | 사용자 REST API (자체 챗봇 백엔드, OpenAI 호환 프록시 등) | `http_adapter.py` | 운영 중 AI 에이전트 정기 회귀 평가 |
| `ui_chat` | 웹 채팅 페이지 (Dify chat app, 챗봇 UI) | `browser_adapter.py` (내장 Playwright Chromium) | API 가 없고 UI 만 있는 서비스의 평가 |

**`http` 모드 요청/응답 포맷** — `TARGET_REQUEST_SCHEMA` 파라미터로 분기:

| 값 | 요청 규격 | 응답 규격 |
|----|---------|---------|
| `standard` | `{"messages": [...], "query": "...", "input": "..."}` | `answer` / `response` / `text` / `output` 중 하나에 답변 |
| `openai_compat` | `{"model": "...", "messages": [{"role": "user", "content": "..."}]}` | `{"choices": [{"message": {"content": "..."}}], "usage": {...}}` |

OpenAI/GPT 호환 프록시, vLLM, llama.cpp server, LM Studio 등 표준 OpenAI API 형식이면 `openai_compat` 선택.

**심판(Judge)은 모든 모드에서 항상 호스트 Ollama** 의 모델 중 선택. 대상과 심판이 다른 모델이면 자기 환각까지 감지 가능 (같은 모델이면 자기평가 구조).

#### 8.5.3 모듈 구성

README 만 보고도 내부 동작을 이해할 수 있도록 각 파일 역할을 1~2줄로 정리합니다.

```text
eval_runner/
├── adapters/
│   ├── base.py              ← UniversalEvalOutput DTO (input/output/raw/latency_ms/
│   │                           tokens_in/tokens_out/error_type ∈ {null,"system","quality"})
│   ├── http_adapter.py      ← HTTP POST 로 대상 AI 호출, response → UniversalEvalOutput 정규화
│   ├── browser_adapter.py   ← Playwright 로 채팅 UI 자동 조작 (입력 selector → 전송 → 응답 대기)
│   └── registry.py          ← TARGET_TYPE 값에 맞는 어댑터 인스턴스 반환
├── configs/
│   ├── schema.json          ← Format Compliance 스키마 (jsonschema)
│   └── security.yaml/.py    ← Policy Violation 금칙 패턴 DB (정규식 세트)
├── dataset.py               ← golden.csv → Python DTO. NaN → None 수동 치환. sha256 계산
├── policy.py                ← Policy Violation 결정론적 매칭
├── reporting/
│   ├── html.py              ← summary.json → summary.html 렌더 (카드 + 드릴다운)
│   ├── llm.py               ← Judge LLM 으로 한 줄 요약 / 지표 해석 / 실패 해설 생성
│   ├── narrative.py         ← 내러티브 삽입 위치 결정 + fallback 문구
│   └── translate.py         ← 메트릭 이름 한글화 (Answer Relevancy 등)
├── tests/
│   ├── test_runner.py       ← pytest 진입점. conversation 단위로 어댑터 호출 → 지표 채점 → summary.json 축적
│   └── fixtures/tiny_dataset.csv  ← 11-케이스 스모크 시험지
├── ollama_wrapper_api.py    ← 로컬 Ollama `/api/generate` 를 OpenAI-compatible HTTP 로 래핑.
│                              Stage 1-2 에서 자동 fork, /health + cold-start probe (300s).
├── Jenkinsfile              ← 내부 서브 파이프라인 (Declarative)
└── SUCCESS_CRITERIA_GUIDE.md   ← Golden Dataset DSL 상세 (README §7.7 Step 1 에 핵심 발췌)
```

#### 8.5.4 주요 파라미터 — 완전 레퍼런스

`05-AI평가` Jenkins Job 의 "Build with Parameters" 에서 설정할 수 있는 모든 파라미터.

| 파라미터 | 종류 | 기본값 | 의미 / 쓰임 |
|---------|-----|--------|-----------|
| `TARGET_TYPE` | choice | `local_ollama_wrapper` | 대상 AI 호출 방식. 3 모드 중 선택 |
| `TARGET_URL` | string | (빈 값) | `http`: REST endpoint. `ui_chat`: 채팅 페이지 URL. `local_ollama_wrapper`: 비움 |
| `TARGET_AUTH_HEADER` | password | (빈 값) | `http` 전용. 예: `Authorization: Bearer sk-...`. 빌드 로그에 노출 안됨 |
| `TARGET_REQUEST_SCHEMA` | choice | `standard` | `http` 전용. `openai_compat` 으로 OpenAI 호환 서버 지원 (Phase 6 신규) |
| `UI_INPUT_SELECTOR` | string | `textarea, input[type=text]` | `ui_chat` 전용. 질문 입력창 CSS selector |
| `UI_SEND_SELECTOR` | string | `button[type=submit]` | `ui_chat` 전용. 전송 버튼 (빈값 → Enter 키) |
| `UI_OUTPUT_SELECTOR` | string | `.answer, [role=assistant], .message-content` | `ui_chat` 전용. 응답 노드 (마지막 매칭 요소) |
| `UI_WAIT_TIMEOUT` | string (초) | `60` | `ui_chat` 전용. 응답 대기 최대 시간 |
| `OLLAMA_BASE_URL` | string | `http://host.docker.internal:11434` | 심판 모델이 사는 Ollama 데몬 (WSL2/Docker Desktop 에선 기본값 그대로) |
| `TARGET_OLLAMA_MODEL` | choice (동적) | 드롭다운 | `local_ollama_wrapper` 전용. 평가 대상 Ollama 모델. 호스트 `/api/tags` 실시간 조회 |
| `JUDGE_MODEL` | choice (동적) | 드롭다운 | 심판 LLM. 호스트 `/api/tags` 실시간 조회 |
| `ANSWER_RELEVANCY_THRESHOLD` | string | `0.7` | ④ 통과 기준 (0.0~1.0). 0.5 관대 / 0.7 권장 / 0.8+ 엄격 |
| `GOLDEN_CSV_PATH` | string | `/var/knowledges/eval/data/golden.csv` | 시험지 경로 (컨테이너 내부) |
| `UPLOADED_GOLDEN_DATASET` | file (업로드) | — | 내 PC 의 CSV 업로드 → 위 경로에 덮어쓰기 후 이번 빌드부터 사용 |

**⚠ CascadeChoice 동적 드롭다운 — 최초 1회 스크립트 승인 필요**:

`JUDGE_MODEL`/`TARGET_OLLAMA_MODEL` 은 빌드 시점에 호스트 Ollama `/api/tags` 를 `new URL.openConnection()` + `JsonSlurperClassic` 으로 조회합니다. Jenkins Groovy sandbox 정책 상 최초 1회 In-process Script Approval 에서 9개 시그니처 승인이 필요하며, provision.sh 가 자동 승인 Groovy 를 실행합니다. 드롭다운이 fallback 리스트(`qwen3.5:4b`, `gemma4:e2b` 등 5개)만 계속 보이면 자동 승인이 실패한 것 — §12.15 수동 승인 절차 참고.

**⚠ 기본값 주의 — fallback 모델이 호스트에 없을 수 있음**:

동적 조회가 실패해 fallback 리스트가 쓰일 때, 그 리스트(`qwen3.5:4b`, `gemma4:e2b`, `gemma4:e4b`, `llama3.2-vision:latest`, `qwen3-coder:30b`) 중 호스트 Ollama 에 실제로 설치된 모델이 없으면 빌드가 초반에 실패합니다. 운영 전 §7.7 Step 0 의 `ollama list` 확인 + 없으면 pull.

**⚠ 리소스 예상**:

| Judge + Target 조합 | VRAM 동시 점유 | conversation 당 | 10-row dataset 총 |
|---------------------|--------------|--------------|-----------------|
| `gemma4:26b` + `gemma4:e4b` | ≈ 33 GB | 4~10 분 | 45~90 분 |
| `qwen3-coder:30b` + `gemma4:e4b` | ≈ 31 GB | 4~8 분 | 40~80 분 |
| `qwen3.5:4b` + `gemma4:e4b` | ≈ 16 GB | 1~3 분 | 15~30 분 |
| `qwen3.5:4b` + `qwen3.5:4b` | ≈ 4 GB (shared) | 0.5~2 분 | 10~20 분 |

속도 민감 환경은 Judge 경량화로 단축 가능하나 판정 정확도 trade-off. 외부 API Judge 지원(`JUDGE_PROVIDER`) 은 현재 미구현.

#### 8.5.5 결과물 — artifact 전체

```text
/var/knowledges/eval/reports/build-<N>/
├── summary.html         ← 사람이 먼저 보는 리포트 (Jenkins "AI Eval Summary" 탭)
├── summary.json         ← 후처리/감사/대시보드 연동용 (Phase 3 immutable 메타데이터 포함)
├── results.xml          ← JUnit 형식 (CI 통계 연동용, 옵션 — JUnit plugin 있을 때만)
└── wrapper.log          ← `local_ollama_wrapper` 모드의 wrapper 데몬 stdout/err
```

Jenkins `archiveArtifacts` + `publishHTML` 로 빌드 단위 영구 보관. `summary.html` 의 4 구역 읽는 법 + `summary.json` 스키마는 §7.7 Step 5 참고.

#### 8.5.6 내부 구현에서 알아야 할 운영 사실

README 단일 원천 원칙 상, 사용자가 문제 파악에 필요한 구현 결정/제약을 여기 정리합니다.

- **deepeval 3.9.7 사용** (1.3.5 아님). import 경로: `deepeval.models.llms.ollama_model.OllamaModel`. langchain pin 은 해제됨 (deepeval 3.9.7 이 `langchain_core` 최신 호환).
- **dify-plugin-daemon 기동 순서** (본 이미지 특유) — supervisord 에서 `dify-plugin-daemon` 을 `autostart=false` + `startretries=20` 으로 두고 entrypoint.sh 가 PostgreSQL ready 확인 후 명시적으로 `supervisorctl start dify-plugin-daemon` 실행. 재기동 시 FATAL 잔존하면 reset 필요 (§12.17).
- **dify-api graceful stop** — `stopasgroup=true + killasgroup=true + stopwaitsecs=30 + GUNICORN_CMD_ARGS="--preload --graceful-timeout 30"` 로 좀비 worker 방지.
- **`ollama_wrapper_api.py` cold-start probe** — 첫 `/invoke` 호출 시 Ollama 가 VRAM 에 모델 로드하는 60~120초 (대형 모델은 최대 300초) 허용. **probe 실패 시 FATAL 이 아니라 WARN 만 내고 pytest 로 진행** — 첫 LLM 호출이 사실상 warm-up. 따라서 `local_ollama_wrapper` 모드 첫 conversation 이 수 분 걸려도 정상.
- **Phase 3 Metadata lockdown** — `summary.json.aggregate.judge.digest` 는 Ollama `/api/show` POST 의 sha256 결과. Ollama 버전에 따라 필드 부재 가능 → None 허용 (빌드 안 깨짐). `dataset.sha256` 은 CSV 로드 시점에 계산 후 immutable.
- **Phase 5 Q7 Calibration** — `golden.csv` 에 `calib: true` 마킹된 케이스는 매 빌드 `aggregate.calibration.per_metric.{count,mean,std}` 에 집계. σ 가 0.1 이상이면 Judge 변동성 경고 대상.
- **Phase 5 Borderline Repeat** — 환경변수 `REPEAT_BORDERLINE_N` (기본 `1` = 끔) 으로 활성. threshold ±`BORDERLINE_MARGIN` (기본 `0.05`) 범위 케이스를 N 회 재평가 후 중앙값으로 원 점수 대체. `eval_runner/tests/test_runner.py` 의 `os.environ.get("REPEAT_BORDERLINE_N", ...)` 에서 읽음. Jenkins UI 파라미터로 노출은 후속 과제.
- **pandas NaN 처리** — `dataset.py::load_dataset()` 가 숫자형 컬럼의 NaN 을 수동으로 None 으로 치환. CSV 편집 시 빈 셀 그대로 둬도 안전 (pandas `where()` 의 dtype 보존 이슈 회피).
- **pytest 실시간 로그** — Jenkinsfile 에 `python3 -u -m pytest ... -v -s --tb=short` + `PYTHONUNBUFFERED=1` 로 한 줄씩 즉시 Jenkins Console 에 전달 (Phase 6 개선). 예전엔 pytest 종료 시 한 번에 찍혀 "멈췄나?" 오해 많았음.
- **JUnit plugin 부재 시** — `post always` 의 `junit` step 이 `NoSuchMethodError` 던질 수 있으나 try/catch 로 무시하고 진행. `results.xml` 은 `archiveArtifacts` 로 보존되니 후처리 가능.

---

## 9. GitLab Issue 결과물 읽는 법 — PM 친화 본문

> 04 파이프라인이 GitLab 에 등록하는 Issue 본문은 **PM 이 첫 줄 신호등만 봐도
> 우선순위 판단이 가능**하도록 재설계되었습니다. 개발자가 필요로 하는 코드
> 블록 / Rule key / Severity / 모든 외부 링크는 본문 하단의 펼침 섹션 안으로
> 묶여 PM 시야를 가리지 않습니다. 사실 정보는 creator 가 deterministic 으로,
> 해석은 LLM 이, 신호등·근거 섹션은 RAG 진단 결과에서 자동 생성됩니다.

### 9.1 본문 구조 한눈에 보기

```text
🚦 [신호등 한 줄]   즉시 수정 / 검토 후 수정 / 오탐 가능성 / 무시 가능
   └ severity + classification + confidence + RAG 근거 정황 종합 (1줄)

📌 무엇이 문제인가     LLM 영향 분석의 첫 문장 (PM 한 줄 요약)

🎯 어디서 발생하나
   ├ 자연어: "src/auth/login.py (line 42) 의 verifyPassword 함수"
   ├ 🌐 외부 노출: HTTP `POST /login` 엔드포인트로 접근 가능합니다.
   ├ 🛡️ 적용된 정적 의도: `@require_auth`, `@app.post`
   └ 📝 입력 매개변수: `email`, `password`

⚠️ 영향과 이유                LLM impact_analysis_markdown
🛠️ 어떻게 고치나               LLM suggested_fix_markdown (+ optional diff)

🔍 AI 판단 근거 (NEW)
   ├ 🔎 참조한 프로젝트 코드: 호출하는 함수 1 곳 (`handleLogin`) · 관련 테스트 1 개
   ├ ✅/🟡/⚠️ AI 가 받은 청크 N 개 중 M 개 인용 (X%). 근거 충실 / 일부 활용 / 약함
   └ 📚 사전학습 정적 메타 활용: HTTP route, decorator, 매개변수명 (3 회 인용)

📂 같은 패턴의 다른 위치        조건부 (cluster 가 있을 때만)

📖 기술 상세 (▶ 접기)
   ├ 메타 정보 표 (파일/함수/Rule/Severity/Commit — 모두 클릭 가능 링크)
   ├ 문제 코드 (이슈 라인 ±10 줄, ">>" 마커)
   ├ Rule 전체 설명 (SonarQube 원문)
   └ 링크 (SonarQube · GitLab blob · GitLab commit)

---
_commit: `e38bd123` (full scan) · sonar: http://localhost:29000/..._
```

### 9.2 🚦 Action Verdict 신호등 — PM 의 1초 판단

| 신호등 | 메시지 예 | 판정 조건 |
|--------|---------|----------|
| 🔴 **즉시 수정** | "심각도 높음 + AI 근거 충실. 본 이슈는 우선 처리 대상입니다." | severity ∈ {BLOCKER, CRITICAL} + classification=true_positive + RAG 근거 충실 |
| 🟠 **즉시 수정 권장 (단, AI 근거 약함)** | "심각도가 높아 즉시 조치 권장. 다만 AI 답변이 일반 원칙에 의존했으니 개발자가 코드 컨텍스트를 직접 확인 후 수정하세요." | 위 + RAG 빈 결과 또는 cited=0 + confidence low/empty |
| 🟡 **검토 후 수정** | "중간 심각도 — 다음 스프린트 내 처리 권장." | severity=MAJOR |
| 🟢 **여유 처리** | "낮은 심각도 — 코드 정리 시 함께 처리." | severity ∈ {MINOR, INFO} |
| 🟢 **무시 가능 (기술 부채)** | "실제 문제는 맞으나 비용 대비 우선순위 낮음. 백로그 등록 후 차후 처리." | classification=wont_fix |
| ⚪ **오탐 가능성 (확신도 높음)** | "AI 가 분석 결과 SonarQube 의 오판으로 판정. 무시 또는 sonar 측 mark FP 처리." | classification=false_positive + confidence=high |
| ⚪ **오탐 가능성** | "AI 가 오판으로 추정 — 개발자 검토 후 mark FP 또는 fix 결정." | classification=false_positive + 그 외 |
| ⚪ **추가 검토 필요** | "심각도 정보 부재 — 개발자가 직접 우선순위 평가 권장." | severity 없음 |

### 9.3 🎯 어디서 발생하나 — 정적 컨텍스트 자동 노출

`enclosing_function` (tree-sitter 추출) 의 **decorator / HTTP route / docstring 매개변수**가 있으면 자연어 라인으로 자동 노출됩니다. 비어 있으면 해당 라인은 생략.

| 라인 | 트리거 |
|------|--------|
| 🌐 외부 노출 | enclosing 함수에 `endpoint` 가 추출됐을 때 (decorator 기반 `@app.get('/x')` 또는 imperative `app.get('/x', handler)`) |
| 🛡️ 적용된 정적 의도 | `decorators` 가 1개 이상 추출됐을 때 — `@require_auth`, `@cached`, `@admin_only` 등 |
| 📝 입력 매개변수 | docstring 의 `doc_params` 가 추출됐을 때 (JSDoc / Sphinx / Google `Args:` / NumPy 모두 지원) |

이 정보를 PM 이 직접 보면 "이 함수가 외부 API 인지", "권한 검사가 적용돼 있는지", "어떤 입력을 받는지" 가 코드를 안 열고도 파악됩니다.

### 9.4 🔍 AI 판단 근거 — 사전학습 → 답변 인과 노출

PM 이 본문을 읽으면서 **"AI 가 정말로 우리 프로젝트를 보고 답한 건지, 일반 원칙으로 답한 건지"** 를 검증할 수 있는 섹션입니다. 자동 생성 라인 3종:

```
- 🔎 참조한 프로젝트 코드: 호출하는 함수 1 곳 (`handleLogin`) · 관련 테스트 1 개 (`testLogin`)
- ✅ AI 가 받은 청크 2 개 중 2 개를 답변에 직접 인용 (100%). 근거 충실.
- 📚 사전학습 정적 메타 활용: HTTP route, decorator, 매개변수명 (3 회 인용).
  AI 답변이 우리 프로젝트의 어휘·구조를 직접 반영했습니다.
```

| 라인 | 의미 |
|------|------|
| 🔎 참조한 프로젝트 코드 | RAG 가 retrieve 해서 LLM 에 넘긴 청크 — bucket 별 (callers / tests / others) 로 묶어 자연어로 노출 |
| ✅ / 🟡 / ⚠️ 인용 정황 | 받은 청크 대비 답변에 실제로 인용한 비율. 60%↑ ✅ · 30%↑ 🟡 · 0% ⚠️ (직접 검토 권장) |
| 📚 정적 메타 활용 | LLM 답변 본문에서 enclosing 함수의 `endpoint` URL · decorator 식별자 · 매개변수명 · RAG 청크 메타가 인용된 횟수 (사전학습 → 답변 인과) |

`rag_diagnostic` 데이터가 없으면 (skip_llm 케이스 등) 짧은 안내 문장으로 대체됩니다.

### 9.5 📖 기술 상세 — 개발자 펼침 영역

GitLab 의 `<details>` 태그를 사용해 **기본 접힘**. PM 본문 상단을 코드/링크로 가리지 않기 위함. 펼치면 다음이 한꺼번에 노출:

- 메타 정보 표 (파일·함수·Rule·Severity·Commit — 모두 GitLab/Sonar 클릭 링크)
- 문제 코드 블록 (이슈 라인 ±10 줄, `>>` 마커)
- Rule 전체 설명 (SonarQube 원문)
- 외부 링크 3종 (SonarQube 이슈 상세 / GitLab 파일 라인 / GitLab 커밋)

### 9.6 라벨

- `severity:CRITICAL` / `MAJOR` / `MINOR` / `INFO` — Sonar 원본 심각도
- `classification:true_positive` / `false_positive` / `wont_fix` — LLM 분류
- `confidence:high` / `medium` / `low` — LLM 본인 판정 확신도
- `partial_citation` — 받은 RAG 청크 절반 미만 인용 시 자동 부착 (confidence high → medium 자동 강등)
- `auto_template:true` — skip_llm 경로 (현재 비활성, 모든 severity 분석)
- `fp_transition_failed` — false_positive 인데 SonarQube mark FP 가 실패한 경우

---

## 10. 접속 정보 & 자격

### 10.1 외부 노출 서비스

| 서비스 | URL | ID | 비밀번호 | override env | 용도 |
|--------|-----|----|---------|-------------|------|
| Jenkins | http://localhost:28080 | `admin` | `password` | `jenkins-init/basic-security.groovy` | Pipeline Job 진입점 |
| Dify | http://localhost:28081 | `admin@ttc.local` | `TtcAdmin!2026` | `DIFY_ADMIN_EMAIL` / `_PASSWORD` | Workflow/Dataset 편집 |
| SonarQube | http://localhost:29000 | `admin` | `TtcAdmin!2026` | `SONAR_ADMIN_NEW_PASSWORD` | 정적분석 대시보드 |
| GitLab | http://localhost:28090 | `root` | `ChangeMe!Pass` | `GITLAB_ROOT_PASSWORD` | 소스 호스팅 + Issue |
| Ollama | http://host.docker.internal:11434 | — | — | `OLLAMA_BASE_URL` | LLM 추론 (호스트 데몬) |

### 10.2 자동 발급된 Jenkins Credentials

provision.sh 가 각 서비스 API 로 동적 발급해 Jenkins 에 주입. **리포에 저장되지 않습니다**.

| Credential ID | 종류 | 발급처 | 사용처 |
|---------------|------|--------|--------|
| `gitlab-pat` | GitLab PAT (유효 364일) | `POST /api/v4/users/1/personal_access_tokens` | P2·P3 clone/Issue |
| `sonarqube-token` | Sonar User Token | `POST /api/user_tokens/generate` | P2 scanner + P3 FP 전이 |
| `dify-dataset-id` | Dify Dataset UUID | `POST /console/api/datasets` | P1 적재 |
| `dify-knowledge-key` | Dify Dataset API Key | `POST /console/api/datasets/api-keys` | P1 API |
| `dify-workflow-key` | Dify App API Key | `POST /console/api/apps/<id>/api-keys` | P3 Workflow |

꺼내 보기:
```bash
docker exec ttc-allinone cat /data/.provision/gitlab_root_pat
docker exec ttc-allinone cat /data/.provision/sonar_token
```

---

## 10.5 RAG Diagnostic Report 읽는 법 — 4-stage 학습진단

> 04 파이프라인 (`정적분석 결과분석 및 이슈등록`) 빌드 후 Jenkins 에서
> **RAG Diagnostic Report** 탭으로 노출되는 HTML 리포트입니다. 이 리포트
> 한 장으로 **"AI 가 우리 프로젝트에서 무엇을 배웠고, 그 학습 결과를 이번
> 이슈 분석에 얼마나 정직하게 활용했는가"** 를 비개발자 / 스폰서 / 운영
> 책임자도 즉시 판단할 수 있도록 설계됐습니다. 사전학습 결과는 PM 시각의
> **4-stage narrative 사다리** 로 재구성되어, 위→아래로 읽으면서 인과를
> 자연스럽게 추적할 수 있습니다.

### 1) 한 장에 보는 구성

```text
┌─ 종합 판정 (TL;DR)              어느 축이 worst 인지 명시 ──────────────┐
├─ 신호등 3 축                    근거충분도 / 참고자료 다양성 / 응답품질  ┤
├─ 📋 실무 권장 액션              CRITICAL/MAJOR 재검토 등 구체 지시       ┤
├─ 📚 사전학습 진단 (4-stage)     ───────────────────────────────────────  ┤
│    Stage 1 학습 범위            AI 가 본 것 (파일 / 청크 / 언어 분포)    ┤
│    Stage 2 학습 깊이            호출 매핑 / endpoint / 테스트 연결       ┤
│    Stage 3 학습 품질            파서 성공률 / 노이즈 제외 / 언어 비대칭 ┤
│    Stage 4 분석 영향            답변에 정적 메타가 어떻게 반영됐나       ┤
├─ 🔧 기술 상세 지표 (접기)       기존 카드 + 분포 + 실패 패턴 자동 진단   ┤
└─ 📄 이슈별 상세                 ⚠️/✓ 한 줄 평가 + 인용 + 메타 아이콘    ┘
```

비개발자는 상단 4 블록 (TL;DR → 신호등 → 액션 → 4-stage 사다리) 만 읽으면 충분.
Stage 4 가 빨간색이면 **"사전학습은 잘됐는데 활용이 새고 있다"** 는 신호 — 즉,
이슈별 RAG 검색 / 프롬프트 점검이 필요하다는 뜻.

### 2) 종합 판정 (TL;DR) — 어느 축이 worst 인가 정직 표기

3 축의 worst-of-3 색상으로 종합 판정. **본문 메시지가 worst 축의 정체성을 명시**해 모순 없게:

| worst 축 | 본문 메시지 (요약) |
|----------|--------------------|
| AI 분석 근거 충분도 (citation) | "AI 가 받은 RAG 자료를 답변에 거의 인용하지 않아 분석이 일반 원칙에 의존" |
| 참고자료 다양성 (RAG 검색) | "인용 자체는 평균 X% 로 정상이지만 RAG 검색 단계에서 호출 관계·테스트 청크를 충분히 회수 못 함" |
| AI 응답 기술적 품질 | "재시도 N% / 빈 컨텍스트 N% — RAG retrieval 또는 LLM 응답 파이프라인 점검" |

전체 색상은 4 단계: 🟢 충분 · 🟡 보통 · 🔴 부족 · ⚪ 측정 불충분 (n < 3).

### 3) 신호등 3 축 — 의미

| 신호등 | 측정 | 임계 | 비개발자 해석 |
|--------|------|------|---------------|
| **AI 분석 근거 충분도** | citation rate (n 명시) | ≥60% 🟢 / ≥30% 🟡 / <30% 🔴 / n<3 ⚪ | AI 가 RAG 자료를 답변에 실제 backtick 인용한 비율 |
| **참고자료 다양성** | callers + tests 평균 | ≥60% 🟢 / ≥30% 🟡 / <30% 🔴 | 호출 관계 / 테스트 청크가 이슈마다 제공된 비율 |
| **AI 응답 기술적 품질** | retry / context:empty | retry≤5%·ctx≤25% 🟢 / ≤20%·≤50% 🟡 / 그 외 🔴 | LLM 호출 자체가 안정적인가 (Dify 응답 파이프라인) |

### 4) 📚 사전학습 진단 — 4-stage narrative 사다리

기존의 "8 개 카드 나열" 방식은 PM 입장에서 숫자만 던져 놓고 판정이 없어 모호했습니다. 신규 4-stage 사다리는 **Scope → Depth → Quality → Impact** 순서로 위에서 아래로 읽으면서 "사전학습 → 답변 활용" 의 인과를 체감하도록 재설계되었습니다.

각 Stage 는 **신호등 1개 + 한 줄 평어 + mini-card 묶음** 으로 구성됩니다.

#### Stage 1 — 학습 **범위** (Scope) "AI 가 본 것"

| 신호등 | 조건 |
|--------|------|
| 🟢 충분 | 파서 성공률 ≥70% + 청크 1개 이상 |
| 🟡 부분 학습 | 파서 성공률 <70% — 일부 파일 정의 패턴이 특수해 분해되지 않음 |
| 🔴 분석 실패 | 청크 0 — 지원 언어 파일 부재 |

mini-card: 전체 파일 수 / 학습 가능한 파일 / 학습된 코드 조각 수 + **언어별 비율 막대그래프** (Python 60%, JS 35%, ...) + 노이즈 자동 제외 카운트 (벤더·미니파이·중복).

#### Stage 2 — 학습 **깊이** (Depth) "이해의 풍부함"

4 개 정적 신호 (호출 관계 매핑률 / HTTP 라우트 수 / 테스트 연결률 / 도메인 모델·문서화) 중 몇 개가 양호한가로 신호등 결정.

| 신호등 | 조건 |
|--------|------|
| 🟢 풍부 | 4 개 신호 중 3 개 이상 충실 |
| 🟡 보통 | 2 개만 충실 |
| 🔴 얕음 | 1 개 이하 |

mini-card 6 종:

- **함수 간 호출 관계 매핑률** (%) + 막대 — "이 함수가 어디서 호출되는가" 추적 기반
- **HTTP API 진입점** (개수) — `@app.route` / `app.get('/x', handler)` 자동 검출 (decorator + imperative 양쪽)
- **테스트 ↔ 본문 자동 연결률** (%) + 막대
- **정적 의도 정보 (데코레이터)** — `@require_role` 등
- **도메인 모델 (타입·인터페이스)** — TS interface / enum
- **문서화된 함수 / 클래스** — docstring / JSDoc / Javadoc / Google `Args:` / NumPy 모두 지원

`▶ AI 가 알아낸 우리 프로젝트의 API 진입점 보기` 펼침 박스 — 검출된 endpoint 예시 5 건 표 노출 (PM 가 "AI 가 진짜 우리 라우트를 알았나" 즉시 검증).

#### Stage 3 — 학습 **품질** (Quality) "학습이 깨끗한가"

| 신호등 | 조건 |
|--------|------|
| 🟢 정상 | 파서 성공률 ≥50% + 언어 비대칭 없음 |
| 🟡 비대칭 | 일부 언어 (예: Go 42%) 가 청크는 있으나 호출 관계 추출 0 |
| 🔴 낮음 | 파서 성공률 <50% |

mini-card: tree-sitter 파서 성공률 + 막대 / 자동 제외된 비-원본 파일 / 중복 제거 청크. 언어 비대칭 발견 시 **⚠️ 경고 카드** 가 자동 추가되어 "해당 언어의 이슈는 caller 청크 회수가 부족할 수 있습니다" 안내.

#### Stage 4 — 분석에 미친 **영향** (Impact) "답변에 어떻게 쓰였나"

사전학습이 LLM 답변에 실제로 반영됐는지 측정. **이 Stage 가 빨강이면 사전학습은 잘됐는데 활용이 새고 있다는 뜻**.

| 신호등 | 조건 |
|--------|------|
| 🟢 잘 활용됨 | 답변 중 정적 메타 1회 이상 인용한 이슈 비율 ≥50% |
| 🟡 부분 활용 | ≥20% |
| 🔴 거의 미반영 | <20% — RAG retrieval / LLM 프롬프트 점검 필요 |
| ⚪ 측정 불충분 | RAG 진단 데이터 보유 이슈 0 |

mini-card 3 종 + 보조:

- **답변에 정적 메타가 등장한 이슈 비율** (%) + 막대 — endpoint · decorator · 매개변수명 · RAG 청크 메타 중 하나라도 인용된 이슈 비율
- **이슈당 평균 정적 메타 인용 수** — 구체성 깊이
- **RAG 청크 인용률 (평균)** + 막대 — 받은 청크 대비 답변에 실제 인용된 비율
- 보조 카드 4 종: HTTP route 인용 이슈 / 데코레이터 인용 이슈 / 매개변수명 인용 이슈 / RAG 청크 메타 인용 이슈 (각 카운트)

### 5) 📋 실무 권장 액션

worst 축에 따라 자동 분기되는 구체 지시:

- **citation 🔴**: 중요 이슈(CRITICAL/MAJOR) 직접 재검토 + false_positive 신중 재평가
- **citation ⚪ (측정 불충분)**: RAG 빈 결과 이슈 줄이기 우선 (threshold 완화 / kb_query 변형 / self-exclusion 정밀화)
- **buckets 🔴/🟡**: 02 사전학습 단계 KB 메타데이터 확인 (callers/tests/symbol 필드)
- **quality 🔴**: 빈 컨텍스트 비율 점검, self-exclusion 정밀화 검토 (§12.18)
- **Stage 4 🔴 (4-stage 진단)**: 사전학습은 충실한데 답변 활용이 안 됨 — kb_query 빌더의 endpoint/decorator 라인 누락 / `enclosing_meta` Dify 변수 미주입 / context_filter 의 `has_*` 플래그 부재 등 점검
- 모두 🟢: 신뢰 가능 — 결과 활용 권장

### 6) 🔧 기술 상세 (개발자용)

기본 접힘. 펼치면:

- 10 카드: total_issues / LLM calls / callers·tests·others bucket filled / **avg citation rate** / **avg citation depth (구체성)** / **partial_citation %** (자동 강등) / context:empty / retry exhausted
- confidence 분포 + classification 분포
- **🔬 RAG 검색 실패 패턴 자동 진단** — A(context:empty) / B(callers=0) / C(tests=0) / D(partial_citation) 4 카테고리 별 카운트 + 예시 path/line.

### 7) 📄 이슈별 상세 — 한 줄 평가 + 메타 아이콘

각 이슈 카드 상단의 자동 평가 5 종:

- **⚠️ skip_llm** — severity 자동 템플릿. LLM 미호출, 수동 리뷰 권장 (현재 비활성)
- **⚠️ retry_exhausted** — 3 회 재시도 실패. 직접 리뷰 필수
- **⚠️ context:empty** — RAG 빈 결과. 답변이 일반 원칙만, 재검토 권장
- **⚠️ partial_citation** — cited/total < 50%. confidence medium 강등됨
- **✓ cited X/Y · 구체 인용 N 개** — 정상 인용 + citation_depth. 신뢰 가능

각 이슈의 used_items 표 컬럼:

| bucket | symbol | score | cited? | meta |
|--------|--------|-------|--------|------|
| callers / tests / others | path::symbol | 0.0~1.0 | ✓ 또는 빈칸 | 🛡️ decorator · 🌐 endpoint · 📝 docstring 구조 보유 |

`meta` 컬럼의 아이콘은 **해당 청크가 tree-sitter 정적 메타를 들고 있었는지** 를 보여줘 — "AI 가 인용 가능한 신호가 청크에 있었는데 답변에 반영했나" 를 이슈 단위에서 검증할 수 있게 합니다.

### 8) 진단 메트릭 자동 강등 정책

> "AI 가 자신감 있는 만큼 근거가 깊은가" 정직성 확보 장치

- **partial_citation 라벨링**: 받은 RAG 청크 절반 미만만 답변에 인용했는데 confidence=high 면, **자동으로 medium 으로 강등하고 `partial_citation` 라벨 부착**. 검수자가 즉시 "근거 약한 자신감" 식별
- **표본 부족 시 회색 신호**: measurable n < 3 (RAG 가 빈 결과 이슈가 다수) 인 경우 신호등 ⚪ "측정 불충분"
- **(path,symbol) dedup**: Dify segmentation 으로 같은 청크가 여러 번 retrieve 되어도 인용 카운트가 부풀려지지 않음

### 9) KB 인텔리전스 사이드카 (Phase A 신규)

`02 코드 사전학습` 빌드 시 `repo_context_builder.py` 가 KB 디렉토리에 `_kb_intelligence.json` 사이드카를 자동 작성합니다. 진단 리포트 빌더는 이 사이드카를 우선 읽어 4-stage 데이터 소스로 활용. 사이드카 부재 시 (구버전 KB) JSONL 직접 스캔으로 fallback.

```bash
# 사이드카 위치 확인
docker exec ttc-allinone ls /var/knowledges/docs/result/_kb_intelligence.json

# 핵심 통계 한 줄 요약
docker exec ttc-allinone python3 -c "
import json
d = json.load(open('/var/knowledges/docs/result/_kb_intelligence.json'))
print('chunks:', d['depth']['total_chunks'],
      '| callers_rate:', f\"{d['depth']['callees_present_rate_pct']:.0f}%\",
      '| endpoints:', d['depth']['endpoints_count'],
      '| parser_ok:', f\"{d['quality']['parser_success_rate_pct']:.0f}%\")
"
```

### 10) 새 CLI 옵션 / 환경변수

```bash
# 04 Jenkinsfile 가 자동 전달 — 수동 실행 시 참고
python3 diagnostic_report_builder.py \
  --input llm_analysis.jsonl \
  --output report.html \
  --kb-dir /var/knowledges/docs/result   # ← 사이드카 우선, JSONL 스캔 fallback

# RAG retrieve 결과를 stderr 한 줄로 trace (사후 분석용)
RAG_TRACE=1 python3 dify_sonar_issue_analyzer.py ... \
  # → [RAG-TRACE] e6662232 retr=4 excl=2 kept=2 used=2 items=[[oth]Handler ...]

# Dataset purge — 02 사전학습이 ANALYSIS_MODE=full 일 때 자동
python3 doc_processor.py upload <KEY> <DSID> --purge   # 수동 실행 시
```

### 11) RAG 효용 측정 추적 (사이클 누적)

사이클 별 측정 변화:

- 베이스라인 (KB 혼재) — citation 2.1%, callers fill 0%, tests fill 0%
- Fix R + 4종 리포트 — citation 60%, callers 25%, tests 50%
- P1+P3+P4 (검색 다양성) — citation 60%, callers 20%, tests 0%
- P5+P7+T1+T2 (정직화) — citation 65%, depth 3.10, partial 2/10, callers 10%, tests 0%
- 사이클 1 (D1+D3+B+A1+F1+F2) — citation 65%, depth 3.70, partial 1/10, callers 20%, tests 10%
- 사이클 2 (F+C+D+E+H tree-sitter) — citation 75%, depth 4.20, partial 0/10, callers 0%, tests 10%
- **사이클 3 (Phase A+B+C — tree-sitter 누수 봉쇄 + 4-stage 학습진단 + PM 친화 GitLab Issue 본문)** — 다언어 callees 분기 (go/rust/c#/kotlin/c/cpp) + imperative route 검출 + Google/NumPy docstring 파서 + KB 인텔리전스 사이드카 + enclosing_meta 를 LLM 프롬프트로 구조화 주입 + kb_query 에 endpoint/decorators/params 추가 + context_filter has_* 메타 플래그 + tree_sitter_hits 메트릭. 효과는 컨테이너 재빌드 후 다음 빌드부터 측정 (구체 수치는 빌드 결과 기준 추가 예정)

🎯 누적 효과:

- citation rate **2.1% → 75%** (35배)
- citation_depth (구체성) 신규 도입 **3.10 → 4.20**
- partial_citation 자동 강등 **2 → 0** (모두 정직)
- KB 메타데이터 `?::?` 비율 **79% → 0%**
- 사이클 3 신규 메트릭: `tree_sitter_hits` (endpoint / decorator / param / RAG-meta 4종 신호의 답변 등장 횟수) — Stage 4 핵심 지표

## 11. 자동 프로비저닝 내부 동작

`scripts/provision.sh` 가 최초 기동 시 자동 수행 (멱등, `/data/.provision/*.ok` 마커로 재실행 안전).

### 11.1 자동으로 처리되는 것

| 대상 | 세부 작업 | 관련 함수 |
|------|---------|----------|
| **Dify 관리자** | 초기 setup (이메일+비밀번호) → 로그인 (base64 password + cookie jar + X-CSRF-Token) | `dify_setup_admin` · `dify_login` |
| **Dify Ollama 플러그인** | `/opt/dify-assets/langgenius-ollama-*.difypkg` 를 `/plugin/upload/pkg` → `/plugin/install/pkg` 로 설치 | `dify_install_ollama_plugin` |
| **Dify LLM provider** | Ollama provider 를 `gemma4:e4b` 모델명으로 등록 (`http://host.docker.internal:11434`) | `dify_register_ollama_provider` |
| **Dify embedding** | Ollama embedding provider 를 `bge-m3` 로 등록 | `dify_register_ollama_embedding` |
| **Dify 기본 모델** | workspace default model 설정 (llm=gemma4:e4b, embedding=bge-m3) — **이게 없으면 high_quality Dataset 생성 시 400 에러** | `dify_set_default_models` |
| **Dify Dataset** | `code-context-kb` 생성 (high_quality + bge-m3 + hybrid_search) | `dify_create_dataset` |
| **Dify Workflow** | `sonar-analyzer-workflow.yaml` 을 `/console/api/apps/imports` 로 import → Dataset ID 주입 → `/workflows/publish` | `dify_import_workflow` · `dify_patch_workflow_dataset_id` · `dify_publish_workflow` |
| **Dify API 키 2종** | Dataset API key (`dataset-*`) 발급 + App API key (Sonar Analyzer Workflow 용) 발급 | `dify_issue_dataset_api_key` · `dify_issue_app_api_key` |
| **GitLab root PAT** | reconfigure 대기 → oauth password grant → `/users/1/personal_access_tokens` (유효 364일) | `gitlab_wait_ready` · `gitlab_issue_root_pat` |
| **SonarQube** | ready 대기 → admin 비밀번호 변경 (`admin` → `SONAR_ADMIN_NEW_PASSWORD`) → user token `jenkins-auto` 발급 | `sonar_wait_ready` · `sonar_change_initial_password` · `sonar_generate_token` |
| **Jenkins Credentials 5종** | `dify-dataset-id`, `dify-knowledge-key`, `dify-workflow-key`, `gitlab-pat`, `sonarqube-token` upsert | `jenkins_upsert_string_credential` |
| **Jenkins Sonar 통합** | SonarQube server `dscore-sonar` 등록 + SonarScanner-CLI tool 등록 (Groovy 스크립트) | `jenkins_configure_sonar_integration` |
| **Jenkins Jobs 5종** | 00 체인 + 01~04 Pipeline Job 등록 | `jenkins_create_pipeline_job` |
| **Jenkinsfile patch** | `GITLAB_PAT = ''` → `credentials('gitlab-pat')` / `GITLAB_TOKEN=""` → `GITLAB_TOKEN="${GITLAB_PAT}"` 런타임 sed 치환 | `patch_jenkinsfile_gitlab_credentials` |

### 11.2 자동화되지 않는 잔존 수동 작업

| 작업 | 빈도 | 참고 |
|------|-----|------|
| **Ollama 에 모델 바이너리 배포** | 운영 머신 최초 설정 1회 | §5.4 (Dify 쪽 등록은 자동) |
| **GitLab 프로젝트 생성 + 소스 push** | 분석 대상 레포마다 1회 | §7.1 ~ §7.2 |
| **첫 Job 의 Parameter Discovery** | Jenkins Job 최초 1회 | §7.3 (최초는 "Build Now" 실패 후 "Build with Parameters") |

---

## 12. 트러블슈팅

### 12.1 Docker Desktop 이 "Ollama 에 연결 못함"

컨테이너에서 호스트 Ollama 도달 테스트:
```bash
docker exec ttc-allinone curl -sf http://host.docker.internal:11434/api/tags | head -c 100
```

**실패 시**:
- macOS: Ollama 가 localhost 만 listen → `launchctl setenv OLLAMA_HOST "0.0.0.0"` 후 Ollama 재기동.
- Linux 네이티브 Docker: `docker-compose.*.yaml` 에 `extra_hosts: ["host.docker.internal:host-gateway"]` 추가 (기본값에 이미 포함되어 있는지 확인).

### 12.2 Jenkins Job 이 "No item named 01-ì½ë..." 로 실패

Jenkins JVM 이 UTF-8 로 기동되지 않아 Korean Job 이름 mojibake.

```bash
docker exec ttc-allinone ps ax | grep jenkins.war | grep -oE "Dfile.encoding=[A-Z0-9-]+"
# → Dfile.encoding=UTF-8   (이게 있어야 정상)
```

이 스택은 `scripts/supervisord.conf` 에 `-Dfile.encoding=UTF-8` 이 이미 반영되어 있습니다. 예전 이미지라면 재빌드 필요.

### 12.3 `01` Job 이 "is not parameterized" HTTP 400

Declarative pipeline 의 parameters 블록이 아직 Jenkins config.xml 에 등록되지 않음 (최초 1회).

**해결**: 한 번 "Build Now" (파라미터 없이) 로 돌려 실패한 뒤 "Build with Parameters" 재실행.

### 12.4 P2 가 `withSonarQubeEnv` 를 모른다

Sonar Jenkins plugin 미설치. 반출 전에 준비 머신에서:
```bash
ls jenkins-plugins/ | grep -E "^(sonar|pipeline-build-step)"
```
둘 다 있어야 하며, 없으면 온라인에서 `bash scripts/download-plugins.sh` 재실행 후 재빌드/재반출.

### 12.5 P1 의 tree-sitter 가 0 청크

`tree-sitter` 와 `tree-sitter-languages` 버전 불일치. Dockerfile 에 `tree-sitter<0.22` 핀이 들어있어야 함.

```bash
docker exec ttc-allinone pip show tree-sitter | grep Version
# → Version: 0.21.x
```

### 12.6 Dify Workflow 가 "not published" (HTTP 400)

`dify_publish_workflow` 실패. 수동 publish:
- Dify Studio → Sonar Issue Analyzer → 우측 상단 **Publish** 버튼.

### 12.6.0 04 Stage (2) Analyze 가 모두 "sandbox service is running?" 에러로 실패

**증상**: 04 Jenkinsfile 의 `(2) Analyze by Dify Workflow` 단계에서 모든 이슈에 대해:

```text
Dify Internal Fail: ... 'status': 'failed',
'error': 'Failed to execute code, which is likely a network issue,
please check if the sandbox service is running. ( Error: [Errno -2] Name or service not known )',
'total_steps': 3
```

**원인**: Dify Workflow 의 Code 노드 (`context_filter`, `json_parser`) 는 `langgenius/dify-sandbox` 라는 별도 컨테이너에서 격리 실행되어야 한다. 통합 이미지에는 sandbox binary 가 포함되어 있지 않아 별도 service `ttc-sandbox` 로 띄우는 구조. 이 service 가 누락된 환경에서 Code 노드가 실행 시점에 sandbox 호스트명을 resolve 못해 실패.

**확인 방법**:

```bash
# ttc-sandbox 컨테이너 상태
docker ps --filter name=ttc-sandbox --format 'table {{.Names}}\t{{.Status}}'
# Up + healthy 가 정답. 안 보이면 compose 의 ttc-sandbox service 확인.

# dify-api 의 환경변수
docker exec ttc-allinone bash -c \
  'cat /proc/$(supervisorctl -c /etc/supervisor/supervisord.conf pid dify-api)/environ \
   | tr "\0" "\n" | grep CODE_EXECUTION'
# CODE_EXECUTION_ENDPOINT=http://ttc-sandbox:8194 + CODE_EXECUTION_API_KEY 가
# 보여야 정상. 비어 있으면 docker-compose env 미설정.

# sandbox health
docker exec ttc-allinone curl -sf http://ttc-sandbox:8194/health && echo OK
```

**해결**:

```bash
# 1) docker-compose.{mac,wsl2}.yaml 에 ttc-sandbox service 가 있는지 확인 (있어야 정상).
# 2) ttc-allinone 의 environment 에 CODE_EXECUTION_ENDPOINT / CODE_EXECUTION_API_KEY 가
#    선언되어 있는지 확인.
# 3) compose 재기동 (이미지 재빌드 불필요):
docker compose -f docker-compose.mac.yaml up -d
# 4) 기다린 뒤 Jenkins 04 파이프라인 재실행.
```

**오프라인 머신**: `langgenius/dify-sandbox:0.2.10` 이미지가 반드시 반출 자산에 포함되어 있어야 한다. `bash scripts/offline-prefetch.sh` 가 자동으로 같이 받음. 누락 시 `bash scripts/offline-load.sh` 가 sandbox 이미지가 없다고 경고.

### 12.6.1 02 Stage 3 Upload 가 첫 청크 외 모두 HTTP 500 — Dify 1.13.3 SQLAlchemy bug

**증상**: 사전학습 파이프라인의 `3. Upload Context` 에서 첫 청크는 SUCCESS, 그 후 모든 청크가 같은 자리에서 `HTTP 500 - {"message":"Internal Server Error","code":"unknown","status":500}` 로 실패. 3회 retry 모두 동일 에러.

**원인**: Dify 1.13.3 의 dataset 모델 `hit_count` property (`/opt/dify-api/api/models/dataset.py:586`) 가 SQLAlchemy `db.session.scalar(...)` cache_key 생성 도중 `'async for' requires an iterator with __anext__ method, got method` TypeError 를 일으킴. 첫 청크 indexing background task 가 dataset 객체 상태를 변경한 직후 후속 호출의 SQLAlchemy session 이 corrupt 됨. 우리 코드 무관 — Dify 자체 버그.

**자동 회복**: Jenkinsfile 02 Stage 3 가 1차 시도에서 `[Upload:FAIL]` 라인을 감지하면 `supervisorctl restart dify-api` 후 자동 재시도. 별도 조치 불필요.

**수동 회복** (자동 회복도 실패한 경우):

```bash
# dify-api 재시작 (SQLAlchemy session pool flush)
docker exec ttc-allinone supervisorctl -c /etc/supervisor/supervisord.conf restart dify-api

# 60s 대기 후 상태 확인
sleep 30
docker exec ttc-allinone curl -sf http://127.0.0.1:5001/console/api/setup && echo OK

# Jenkins 02 파이프라인 재실행 — Stage 1 부터 다시 (clone+청크 재생성).
```

**진단 명령**:

```bash
# 최근 traceback 확인
docker exec ttc-allinone bash -c 'awk "/Traceback/{flag=1} flag{print} /^$/&&flag{flag=0;print \"---\"}" /data/logs/dify-api.log | tail -50'
```

**근본 해결**: Dify 1.13.4+ 로 업그레이드 (오프라인 번들 재구성 필요) 또는 Dify 측 patch.

### 12.7 GitLab 계속 `(health: starting)`

arm64 이미지의 reconfigure 는 5-10분. 정상.

```bash
docker exec ttc-gitlab gitlab-ctl status
curl -sf http://localhost:28090/users/sign_in && echo "OK"
```

### 12.8 SonarQube 가 flood_stage → read-only

호스트 디스크 > 95% 사용률. 정리 필요. macOS 는 entrypoint.sh 가 overlay 로 피합니다.

### 12.9 Executor 부족

Jenkins 기본 executor 2 개. 체인 + 하위 Job 이 대기하는 경우:

Jenkins → Manage Jenkins → Nodes → **built-in** → 설정 → "Number of executors" 를 4 로.

### 12.10 로그 위치 + 자주 쓰는 진단 명령어

**주요 로그**:

```bash
# provision.sh 전체 진행
docker logs ttc-allinone | grep "\[provision\]"

# 개별 서비스 로그
docker exec ttc-allinone cat /data/logs/jenkins.log | tail -50
docker exec ttc-allinone cat /data/logs/sonarqube.log | tail -50
docker exec ttc-allinone cat /data/logs/dify-api.log | tail -50
docker exec ttc-allinone cat /data/logs/dify-plugin-daemon.log | tail -50
docker logs ttc-gitlab | tail -20

# supervisord 관리 11개 프로세스 상태 한 번에
docker exec ttc-allinone supervisorctl status
```

**Jenkins Job 콘솔** (브라우저에서 바로 확인):

- http://localhost:28080/job/01-코드-분석-체인/lastBuild/console
- http://localhost:28080/job/04-정적분석-결과분석-이슈등록/lastBuild/console
- http://localhost:28080/job/05-AI%ED%8F%89%EA%B0%80/lastBuild/console

**자주 쓰는 진단 명령** (한 곳에 모음 — Job 04 / 호스트 Ollama / 서비스 health):

```bash
# 호스트 Ollama 상태 (어떤 모델이 로드됐고 어떤 프로세서를 쓰는지)
curl -s http://localhost:11434/api/ps | python3 -m json.tool

# Dify API 헬스 (응답코드만)
docker exec ttc-allinone curl -sf -o /dev/null -w "dify=%{http_code}\n" \
  http://127.0.0.1:5001/console/api/setup

# 4개 외부 노출 서비스 전수 확인
for url in http://localhost:28080/login http://localhost:28081/apps \
           http://localhost:29000/api/system/status http://localhost:28090/users/sign_in; do
    printf "%3d  %s\n" $(curl -s -o /dev/null -w '%{http_code}' "$url") "$url"
done

# Job 04 최근 빌드 결과 요약
curl -s -u admin:password \
  'http://localhost:28080/job/05-AI%ED%8F%89%EA%B0%80/lastBuild/api/json?tree=number,result,duration' \
  | python3 -m json.tool

# Job 04 최근 summary.json 핵심 지표 한 줄 요약
docker exec ttc-allinone python3 -c "
import json, glob, os
paths = sorted(glob.glob('/var/knowledges/eval/reports/build-*/summary.json'), key=os.path.getmtime)
if not paths: print('no reports yet'); raise SystemExit
d = json.load(open(paths[-1]))
print('build:', os.path.basename(os.path.dirname(paths[-1])))
print('totals:', d.get('totals', {}).get('passed_conversations'), '/', d.get('totals', {}).get('conversations'))
print('judge:', d.get('aggregate', {}).get('judge', {}).get('model'))
print('dataset sha256:', d.get('aggregate', {}).get('dataset', {}).get('sha256', '')[:12])
"

# Job 04 pytest 실시간 로그 (진행 중 빌드 확인용)
curl -s -u admin:password \
  'http://localhost:28080/job/05-AI%ED%8F%89%EA%B0%80/lastBuild/consoleText' | tail -60

# 컨테이너 안에서 돌아가는 pytest / wrapper 프로세스
docker exec ttc-allinone ps -eo pid,etime,pcpu,pmem,cmd \
  | grep -E "pytest|ollama_wrapper" | grep -v grep
```

### 12.11 `05-AI평가` 가 "model not found" 로 즉시 실패

Jenkinsfile 기본값이 호스트 Ollama 에 없는 모델을 가리킴. 증상: 빌드 콘솔 첫 수 초 이내에 `pull model manifest: model "xxx" not found` 또는 404.

```bash
# 1. 호스트에 실제로 있는 모델 확인
ollama list

# 2. "Build with Parameters" 에서 JUDGE_MODEL / TARGET_OLLAMA_MODEL 을 위 목록의 이름으로 교체 후 재실행
#    (예: JUDGE_MODEL=gemma4:e4b, TARGET_OLLAMA_MODEL=gemma4:e4b)

# 3. 또는 누락 모델을 다운로드 (온라인 머신에서만 가능)
ollama pull gemma4:e4b
ollama pull qwen3-coder:30b
```

### 12.12 `05-AI평가` 빌드가 비정상적으로 느림 / "다운된 건 아닌지" 의심

증상: conversation 1 개에 4~10 분, 10-row dataset 이 1 시간 이상. **실제로는 정상 동작** — DeepEval 이 지표당 2~3 LLM call 을 체인으로 돌리며 Judge 모델이 크면(24~30 GB) call 당 20~40 초 소요.

살아 있는지 확인:

```bash
# pytest 프로세스가 CPU 를 쓰고 있나
docker exec ttc-allinone ps -eo pid,etime,pcpu,pmem,cmd | grep -E "pytest|ollama_wrapper" | grep -v grep

# Ollama 에 모델이 로드되어 있나 (VRAM 점유 중이면 작업 중)
curl -s http://host.docker.internal:11434/api/ps | python3 -m json.tool

# 최근 pytest 출력 라인
curl -s -u admin:password 'http://127.0.0.1:28080/job/05-AI%ED%8F%89%EA%B0%80/lastBuild/consoleText' | tail -30
```

**단축 옵션**:

- **Judge 경량화**: `JUDGE_MODEL=qwen3.5:4b` 처럼 4B 급 모델로 교체 → Judge 호출당 5~10 초로 단축 (판정 정확도 하락 trade-off).
- **Dataset 축소**: fixture 의 `tiny_dataset.csv` 대신 2~3 row 만 가진 smoke CSV 로 회귀 감지만 수행.
- **외부 API Judge**: `JUDGE_PROVIDER ∈ {gemini, openai, anthropic}` 지원은 현재 **미구현** (후속 과제). Gemini free tier 가 가장 빠르게 도입 가능한 경로.

### 12.13 `05-AI평가` 에서 `rag-*` conversation 만 계속 FAILED

`golden.csv` 의 `context_ground_truth` 와 실제 RAG 응답이 동떨어져 있거나, Faithfulness / Contextual Recall 임계값이 엄격함. 리포트의 실패 케이스 심층 분석 섹션에서 Judge 의 사유를 확인 후:

- fixture 가 임의 예시라면 `ANSWER_RELEVANCY_THRESHOLD=0.5` 등으로 낮춰 smoke 용도로만 사용.
- 실제 평가라면 `golden.csv` 의 `expected_output` / `context_ground_truth` 정제.

### 12.14 `05-AI평가` 실패 패턴 → 원인 매핑 (종합 진단표)

`summary.html` 의 "실패 케이스 드릴다운" 에서 증상을 찾고 해당 행의 조치 수행.

| 증상 | 유력 원인 | 조치 |
|------|---------|------|
| 모든 케이스가 `error_type=system` | TARGET_URL 도달 불가, wrapper 미기동, 또는 호스트 Ollama 다운 | TARGET_URL 직접 curl 확인 · `docker exec ttc-allinone curl http://127.0.0.1:8000/health` · `wrapper.log` 확인 |
| Multi-turn Consistency 만 실패 | 대화 이력(history)이 대상 AI 에 전달 안 됨 또는 `conversation_id`/`turn_id` 뒤섞임 | `http` 모드면 `TARGET_REQUEST_SCHEMA` 가 대상과 맞는지 (`openai_compat` vs `standard`) 확인 · CSV 의 `conversation_id` + `turn_id` 정합성 점검 |
| Faithfulness 전반 낮음 | `context_ground_truth` 와 실제 응답 표현 차이 | 리포트의 🤖 쉬운 해설 내용 확인 → context 정제 또는 threshold 하향 |
| 첫 conversation 만 timeout | Judge/Target 모델 cold-start (VRAM 로드) | §7.7 Step 0 의 cold-start 예열 `curl /api/generate` 수행 후 재실행 |
| Score 가 매번 크게 변동 (재현성 낮음) | Judge LLM 변동성 (calibration σ > 0.1) | `calib=true` 케이스 2~3개 추가 · 더 큰 Judge 모델 · env `REPEAT_BORDERLINE_N=3` 활성 |
| `output` JSON 파싱 실패 (② Format Compliance 계속 실패) | 대상 API 응답이 JSON 아님 또는 스키마 불일치 | `eval_runner/configs/schema.json` 에서 실제 응답 구조와 맞게 수정. `ui_chat` 모드면 자동 skip |
| pytest 출력이 빌드 끝에 한꺼번에 | `PYTHONUNBUFFERED=1` 또는 `pytest -u` 미적용 | Jenkinsfile 이 Phase 6 이전 버전 — 이미지 재빌드 필요 |
| CPU 에서만 도는 것 같음 (case 당 10분+) | GPU fallback | §12.16 |
| 드롭다운에 호스트 모델 안 보임 | CascadeChoice Script Approval 미승인 | §12.15 |
| `JUnit plugin 없음` WARN | 정상 — JUnit plugin 미설치 | 무시 가능. `results.xml` 은 `archiveArtifacts` 로 보존됨 |

### 12.15 `05-AI평가` CascadeChoice 드롭다운이 비거나 fallback 만 표시됨

**증상**: "Build with Parameters" 의 `JUDGE_MODEL` / `TARGET_OLLAMA_MODEL` 이 비어 있거나, `ollama list` 에 없는 고정 5개 (`qwen3.5:4b`, `gemma4:e2b`, `gemma4:e4b`, `llama3.2-vision:latest`, `qwen3-coder:30b`) 만 계속 보임.

**원인**: CascadeChoice 가 `sandbox=false` Groovy 로 호스트 Ollama `/api/tags` 를 조회하려는데, Jenkins In-process Script Approval 에서 `URL.openConnection`, `JsonSlurperClassic.parse` 등 9개 시그니처 승인이 필요한 상태. provision.sh 가 자동 승인을 시도하지만 Jenkins 기동 타이밍에 따라 실패 가능.

**수동 승인** — Jenkins 관리자 계정(`admin`)으로 스크립트 콘솔 (http://localhost:28080/script) 에서 실행:

```groovy
import org.jenkinsci.plugins.scriptsecurity.scripts.*
def inst = ScriptApproval.get()
[
    "staticMethod java.net.URL openConnection",
    "method java.net.URLConnection getInputStream",
    "method java.net.URLConnection setConnectTimeout int",
    "method java.net.URLConnection setReadTimeout int",
    "method java.net.HttpURLConnection setConnectTimeout int",
    "method java.net.HttpURLConnection setReadTimeout int",
    "method java.io.InputStream newReader java.lang.String",
    "new groovy.json.JsonSlurperClassic",
    "method groovy.json.JsonSlurperClassic parse java.io.Reader",
].each { inst.approveSignature(it) }
println "approved total=" + inst.approvedSignatures.size()
```

또는 Jenkins 재기동 (`docker exec ttc-allinone supervisorctl restart jenkins`) 후 provision.sh 자동 승인 Groovy 재실행.

승인 후 Job 페이지를 새로고침하면 드롭다운이 호스트 실재 모델 목록으로 채워집니다.

### 12.16 `05-AI평가` 가 GPU 가속 없이 CPU 로 돌아 매우 느림

**증상**: conversation 당 10 분 이상, `ollama ps` 의 `PROCESSOR` 컬럼이 `CPU` 또는 `size_vram=0`.

**분기 ① — macOS Apple Silicon** (Metal backend 사용, NVIDIA CUDA 없음):

```bash
# 현재 로드된 모델의 프로세서 확인
curl -s http://localhost:11434/api/ps | python3 -m json.tool
# size_vram 이 모델 크기에 가까우면 Metal GPU 사용 중 (정상).
# size_vram == 0 이면:
#   1. Docker Desktop → Settings → Resources → Memory 를 12 GB 이상으로
#   2. Ollama 재기동: `brew services restart ollama` 또는 앱 재시작
```

macOS 는 별도 CUDA 설정 불필요 — Metal 은 Apple Silicon 에서 자동. 느리다면 단순히 VRAM 부족이거나 모델이 너무 큰 경우.

**분기 ② — Windows WSL2 + NVIDIA GPU**:

```powershell
# PowerShell — GPU 실제 사용 상태
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv
# memory.used 0 MB 근처 + utilization.gpu 0% → CPU fallback 중

# Ollama 프로세스가 점유한 VRAM
nvidia-smi --query-compute-apps=pid,used_memory --format=csv

# CUDA_VISIBLE_DEVICES 값 (존재하지 않는 인덱스로 설정되면 Ollama 가 조용히 CPU fallback!)
echo $env:CUDA_VISIBLE_DEVICES
```

**조치**:

```powershell
# 잘못된 값이면 "0" (첫 GPU) 으로 교정
[Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '0', 'User')

# Ollama 재기동으로 새 환경변수 적용
Get-Process ollama* | Stop-Process -Force
Start-Process 'C:\Users\<사용자>\AppData\Local\Programs\Ollama\ollama app.exe'

# 확인 — 모델 로드 후 size_vram 이 예상 VRAM 에 도달해야 정상
curl http://localhost:11434/api/ps
```

### 12.17 `dify-plugin-daemon` FATAL 로 프로비저닝 실패

**증상**: `docker logs ttc-allinone` 에 `dify-plugin-daemon entered FATAL state` + provision 이 Dify 플러그인 설치 단계에서 멈춤.

**원인**: supervisord 가 PostgreSQL 준비 전 plugin-daemon 을 기동했거나 (race), 이전 기동의 FATAL 이 남아 재시작 불가 상태.

**조치**:

```bash
# 1. 상태 확인
docker exec ttc-allinone supervisorctl status dify-plugin-daemon
# dify-plugin-daemon   FATAL   Exited too quickly (process log may have details)

# 2. FATAL 상태 reset + 수동 start
docker exec ttc-allinone supervisorctl reset-fail dify-plugin-daemon
docker exec ttc-allinone supervisorctl start dify-plugin-daemon

# 3. 로그 확인
docker exec ttc-allinone tail -50 /data/logs/dify-plugin-daemon.log
# → "connected to postgres" 가 나오면 정상. 
# → "connection refused" 면 PostgreSQL 이 아직 기동 중일 수 있음 — 30초 대기 후 재시도.

# 4. 프로비저닝 재실행
docker exec ttc-allinone rm -rf /data/.provision/
docker exec ttc-allinone bash /opt/provision.sh
```

본 이미지는 supervisord 의 `autostart=false` + entrypoint.sh 명시 start 패턴으로 race 를 근본 차단했지만, 이미 기동된 컨테이너에서 FATAL 이 남았다면 위 절차로 복구.

### 12.18 04 Stage (2) 가 "Dify succeeded but outputs empty (impact_analysis_markdown missing)" 로 대량 실패

**증상**: `04-정적분석-결과분석-이슈등록` 의 Stage 2 "Analyze by Dify Workflow" 에서 analyzer 가 이슈마다

```
-> Dify succeeded but outputs empty (impact_analysis_markdown missing) [parse_status=<사유코드>]
```

로그를 반복해 찍고, 3회 재시도 모두 빈 outputs 로 끝나 `[FAIL-EMPTY]` 로 마감됨. Dify workflow 자체는 HTTP 200 + `status=succeeded` 를 반환해 겉보기엔 정상.

**근본 원인 3종**:

1. **Thinking 모델의 reasoning block**. `gemma4:e4b` 는 추론 시 `<think>...</think>` 블록을 먼저 출력한 뒤 JSON 을 내놓는다. 이 블록 내부에 `{` 가 섞이면 파싱 정규식이 thinking 문맥부터 잡아 `json.loads` 가 실패.
2. **max_tokens 부족**. 기본 2048 은 thinking 이 길어지면 `</think>` 도 나오기 전에 잘린다. 동시에 Dify Ollama provider credential 이 `max_tokens=4096` 을 num_predict 상한으로 강제하므로, workflow 의 `completion_params.max_tokens` 만 올려도 provider 가 400 을 뱉는다.
3. **JSON string escape 위반**. 모델이 `suggested_diff` 같은 긴 문자열에 raw `\n\r\t` 를 그대로 넣거나, regex 룰 메시지의 `\d\s\w` 를 그대로 JSON 문자열에 넣는다. JSON 스펙은 `\"\\/bfnrt` 와 `\uXXXX` 만 허용 — 그 외 `\X` 는 "Invalid \escape".

**현재 워크플로 대응 (자동)**:

- `json_parser` Code 노드가 `<think>` 블록(종료·미종료 모두) 제거 → 코드펜스 제거 → `{...}` 추출 → 1차 `json.loads` → 실패 시 상태기계 fixup (raw control char escape + invalid backslash doubling) → 2차 `json.loads`. 최종 실패 시에도 `parse_status` / `parse_error_msg` / `llm_text_preview` 세 필드를 End 로 노출해 `analyzer.py` 가 stderr 에 원인 + LLM 원문을 덤프 (`--print-first-errors N` 으로 덤프 건수 제한).
- `parse_status` 값 해석:
  - `ok` — 정상
  - `parsed_but_empty` — JSON 은 OK 인데 `impact_analysis_markdown` 이 비었음. 프롬프트 지시 재강조 (analyzer 의 retry_hint 가 자동 처리)
  - `no_json_block` — 모델이 JSON 없이 자유 서술. system prompt 에 스키마 강제
  - `json_decode_error` — `{...}` 있으나 파싱 실패. `parse_error_msg` 로 위치 확인, fixup 보강 여부 검토
  - `thinking_unterminated` — `<think>` 는 있으나 `</think>` 없음 (max_tokens 소진). max_tokens 상향 필요
  - `empty_llm_text` — LLM 빈 응답. Ollama 헬스·타임아웃 확인

**GitLab Issue 본문에 해결방안·영향분석·소나큐브 링크 등이 안 보이는 문제는 §12.19 참조.**

### 12.19 GitLab 18.x Work Items UI 에서 이슈 본문이 접혀 "Read more" 클릭 후에야 전체가 보임

**증상**: `04` Stage 3 이 GitLab 이슈를 정상 생성했고 REST `GET /api/v4/projects/:p/issues/:iid` 로 description 이 3,000~5,000자 들어있는 것을 확인할 수 있지만, 웹 UI (`/-/issues/:iid` 또는 `/-/work_items/:iid`) 에서는 **제목 + 간단 라벨만 보이고 본문의 📍 위치·🔴 문제 코드·✅ 수정 제안·💡 Suggested Diff·📊 영향 분석·🔗 링크 섹션이 전혀 렌더되지 않은 것처럼** 보인다.

**원인**: GitLab 18.x Work Items UI 의 per-user 설정 "Truncate descriptions" 가 **기본 ON**. 저장 위치는 서버가 아닌 브라우저 localStorage (`work_item_truncate_descriptions`) — 서버 API / Admin 설정으로 기본값을 바꿀 방법이 없다. description 이 본 스택의 LLM 출력(3~5K자) 처럼 길면 첫 몇 줄만 접혀 노출되고 "Read more" 클릭해야 전체가 보인다. 처음 사용자는 이슈에 내용이 없다고 오해.

**대응 (영구 기본값 OFF)**: 본 스택은 `scripts/gitlab-truncate-patch.sh` 가 `run-mac.sh` · `run-wsl2.sh` 에서 백그라운드로 자동 실행되어, `ttc-gitlab` 컨테이너의 webpack chunk 14 개에 박혀있는 minified 초기값 `truncationEnabled:!0` (true) 을 `!1` (false) 로 sed 치환한다. 멱등 — 이미 패치된 chunk 는 `already_patched` 로 카운트만 올라간다.

**수동 확인 / 재실행**:

```bash
bash code-AI-quality-allinone/scripts/gitlab-truncate-patch.sh
# → [gitlab-truncate-patch]   patched=N already_patched=M minified_variant_mismatch=0
```

- `patched=N` (N>0): 최초 실행 — 다음 브라우저 강력 새로고침 (Cmd+Shift+R) 이후 description 바로 전체 노출.
- `already_patched=14`: 이미 적용됨.
- `minified_variant_mismatch > 0`: GitLab 버전업으로 minified 변수명 변경 — 본 섹션 재검증 필요.

**개별 사용자 대응** (패치 안 하고 그 자리에서만 끄고 싶을 때): 이슈 우측 상단 **View options** → **Truncate descriptions** 체크 해제. 해당 브라우저 localStorage 에만 기록됨.

### 12.20 이번 04 대량 실패 케이스의 종합 타임라인 (참고용)

2026-04-25 심야에 쌓은 진단·수정 내역을 순서대로 정리. 같은 증상 재발 시 참고.

1. **관찰**: 04 Stage 2 가 94 이슈 대부분에서 `Dify succeeded but outputs empty` 를 뱉고 `[FAIL-EMPTY]`.
2. **1차 진단 추가**: `json_parser` 에 `parse_status` + `llm_text_preview` + `parse_error_msg` 필드 노출 → analyzer stderr 에 덤프.
3. **1차 원인 확정**: preview 첫 줄이 `<think>` — thinking 모델의 reasoning block 을 regex 가 처리 못함. `<think>...</think>` 제거 로직 투입.
4. **2차 원인**: `</think>` 도 나오기 전에 출력이 잘림 (max_tokens=2048). workflow max_tokens 16384 로 상향 시도 → Dify Ollama plugin 이 `num_predict ≤ 4096` 스키마 강제로 400.
5. **Ollama provider credential 업데이트**: `/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials` PUT → `max_tokens: 16384`, `context_size: 32768`. `scripts/provision.sh` 의 `dify_register_ollama_provider` 도 동시 반영.
6. **3차 원인**: 성공 비율 오르긴 하지만 여전히 일부 실패 — `parse_error_msg` 가 "Invalid control character". diff 문자열 내 raw 개행. 상태기계로 `\n\r\t` escape 자동화.
7. **4차 원인**: 잔여 실패가 전부 "Invalid \escape". regex 룰 (javascript:S6353 등) 의 `\d\s\w` 메시지가 문자열에 섞임. 상태기계에 invalid backslash → `\\X` doubling 추가.
8. **UX 보완**: analyzer stdout/jsonl 버퍼링으로 Jenkins console 진행이 뭉쳐 "멈춘 듯" 보임 — `sys.stdout.reconfigure(line_buffering=True)` + `open(buffering=1)` 로 라인 단위 즉시 반영.
9. **GitLab UX**: 이슈 본문이 Work Items UI 기본 Truncate 로 안 보인다는 제보 → webpack chunk `truncationEnabled:!0 → !1` 패치 (`scripts/gitlab-truncate-patch.sh`, 멱등, run 스크립트에서 자동 호출).
10. **검증**: MAX_ISSUES=10 으로 재실행 → 10/10 success, `FAIL-EMPTY=0`, `Dify Internal Fail=0`, Stage 3 까지 도달해 `created=9 fp_transitioned=1 failed=0`. 이슈 description 은 브라우저에서 접힘 없이 바로 노출.

---

## 13. 초기화 & 재시작

### 13.1 완전 초기화 (모든 데이터 지움)

```bash
cd code-AI-quality-allinone
docker compose -f docker-compose.mac.yaml down -v      # 또는 wsl2
rm -rf ~/ttc-allinone-data
bash scripts/run-mac.sh        # 다시 기동 → provision 재실행
```

### 13.2 프로비저닝만 재실행

```bash
docker exec ttc-allinone rm -rf /data/.provision/
docker exec ttc-allinone bash /opt/provision.sh
```

### 13.3 이미지 업데이트 반영 (온라인 머신에서 재빌드)

1. 온라인 준비 머신에서 코드 변경 후:
   ```bash
   bash scripts/offline-prefetch.sh --arch arm64
   ```
2. 새 tarball 을 오프라인 머신에 반출 → `offline-load.sh` 로 replace.
3. 오프라인 머신:
   ```bash
   docker compose -f docker-compose.mac.yaml down
   bash scripts/run-mac.sh
   ```

---

## 14. 파일 구성 레퍼런스

```
code-AI-quality-allinone/
├── Dockerfile                              # 통합 이미지 정의 (14GB 결과)
├── docker-compose.mac.yaml                 # Mac (arm64) compose
├── docker-compose.wsl2.yaml                # WSL2 (amd64) compose
├── README.md                               # 본 문서
├── requirements.txt                        # 통합 Python deps
│
├── pipeline-scripts/                       # 파이프라인 1·3 Python (컨테이너에 COPY)
│   ├── repo_context_builder.py             # P1 AST 청킹 + callers pass 2 + K-4 doc
│   ├── contextual_enricher.py              # P1 gemma4 요약 prepend
│   ├── doc_processor.py                    # P1 Dify 업로드 + kb_manifest + footer
│   ├── sonar_issue_exporter.py             # P3(1) Sonar API + 보강
│   ├── dify_sonar_issue_analyzer.py        # P3(2) Dify Workflow 호출 + HyDE
│   ├── gitlab_issue_creator.py             # P3(3) GitLab Issue + Dual-path FP
│   ├── diagnostic_report_builder.py        # P1.5 RAG Diagnostic Report HTML 생성
│   ├── eval_rag_quality.py                 # P2 M-6 — golden CSV 대비 자동 평가
│   └── dify_kb_wait_indexed.py             # P1 indexing 완료 polling
│
├── sample-projects/                        # GitLab 에 자동 push 될 샘플 레포들 (vendoring)
│   ├── realworld/                          # gothinkster/spring-boot-realworld-example-app (Java/Spring Boot)
│   │   ├── src/main/java/ src/test/java/   # RealWorld API 사양 구현 (DDD)
│   │   ├── sonar-project.properties        # Java source-only 분석 설정
│   │   └── SOURCE_PROVENANCE.md            # upstream commit + 라이선스 추적
│   └── golden_template.csv                 # eval_rag_quality.py 출발점
│
├── eval_runner/                            # 파이프라인 4 (DeepEval + Playwright)
│
├── jenkinsfiles/                           # 5 개 Pipeline 정의
│   ├── 01 코드 분석 체인.jenkinsPipeline
│   ├── 02 코드 사전학습.jenkinsPipeline
│   ├── 03 코드 정적분석.jenkinsPipeline
│   ├── 04 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline
│   └── 05 AI평가.jenkinsPipeline
│
├── jenkins-init/basic-security.groovy      # admin/password 초기화
│
├── jenkins-plugins/                        # ⚠ download-plugins.sh 생성 — 반출 필수
├── dify-plugins/                           # ⚠ download-plugins.sh 생성 — 반출 필수
├── offline-assets/<arch>/                  # ⚠ offline-prefetch.sh 생성 — 반출 필수
│   ├── ttc-allinone-<arch>-*.tar.gz
│   └── gitlab-*.tar.gz
│
└── scripts/
    ├── download-plugins.sh                 # (온라인) 플러그인 다운로드
    ├── offline-prefetch.sh                 # (온라인) tarball 산출
    ├── offline-load.sh                     # (오프라인) tarball 로드
    ├── build-mac.sh / build-wsl2.sh        # 로컬 빌드 헬퍼 (prefetch 내부에서도 호출)
    ├── run-mac.sh / run-wsl2.sh            # compose up 헬퍼
    ├── gitlab-truncate-patch.sh            # P3 후속: GitLab 18 Work Items UI 기본 Truncate OFF 패치
    ├── supervisord.conf                    # 11 프로세스 (UTF-8 JVM 포함)
    ├── nginx.conf                          # Dify gateway
    ├── pg-init.sh                          # Postgres initdb
    ├── entrypoint.sh                       # 컨테이너 진입점
    ├── provision.sh                        # 완전 자동 프로비저닝
    └── dify-assets/
        ├── sonar-analyzer-workflow.yaml    # P3 Dify Workflow DSL
        └── code-context-dataset.json       # P1 Dataset 스펙
```

### 파이프라인 런타임 생성 파일

| 경로 | 생성자 | 용도 |
|------|--------|------|
| `/data/kb_manifest.json` | P1 doc_processor | P2/P3 KB freshness 검증 |
| `/var/knowledges/docs/result/*.jsonl` | P1 repo_context_builder | 청크 + P3 callgraph 소스 |
| `/var/knowledges/state/last_scan.json` | P3 exporter | diff-mode baseline |
| `/var/knowledges/state/chain_<sha>.json` | 00 Chain Summary | P1/P2/P3 요약 + p3_summary |
| `${HOME}/ttc-allinone-data/` | 호스트 바인드 마운트 | 전체 persistent 상태 |

---

## 15. 프로덕션 전 체크리스트

PoC 단계에선 기본값 그대로 동작하지만, 운영 전 다음을 교체하세요:

- [ ] `JENKINS_PASSWORD` — `jenkins-init/basic-security.groovy` 수정 + 이미지 재빌드.
- [ ] `DIFY_ADMIN_PASSWORD` — compose env 에 강한 값. fresh 볼륨으로 재기동.
- [ ] `SONAR_ADMIN_NEW_PASSWORD` — compose env 추가. **프로비저닝 전에** 교체해야 적용.
- [ ] `GITLAB_ROOT_PASSWORD` — compose 의 `GITLAB_OMNIBUS_CONFIG` 안 `initial_root_password` 교체.
- [ ] PostgreSQL / Sonar DB 비밀번호 — `scripts/pg-init.sh` 수정 + 재빌드 + Dify/Sonar env 동기화.
- [ ] `SECRET_KEY` (Dify 암호화 seed) — `scripts/supervisord.conf` 의 placeholder 교체. 장기 운영 필수.
- [ ] 네트워크 격리 — 28080/28081/29000/28090 외부 인터넷 직접 노출 금지.
- [ ] HTTPS — 이 스택은 HTTP 전용. 외부 LB/Ingress 에서 TLS termination.
- [ ] GitLab PAT 만료 — 364일 후 자동 만료. 재발급 자동화 검토 (`provision.sh` 의 `gitlab_issue_root_pat()` 참고).
- [ ] Ollama 모델 업데이트 경로 — 모델 교체 시 온라인 머신에서 `ollama pull` → `~/.ollama/models` 재반출 → 오프라인 머신에 rsync.

---

## 16. 부록: 온라인 단일 머신 빠른 테스트

개발/평가 목적으로 한 머신에서 인터넷 연결 상태로 빠르게 돌려보고 싶을 때:

```bash
# 1) 레포 clone
git clone <이 레포 URL>
cd airgap-test-toolchain/code-AI-quality-allinone

# 2) 호스트 Ollama (§3.4 Step 3 참고)
brew install ollama && brew services start ollama
ollama pull gemma4:e4b
ollama pull bge-m3
# qwen3-coder:30b 는 선택

# 3) 플러그인 다운로드 + 이미지 빌드 (온라인)
bash scripts/download-plugins.sh
bash scripts/build-mac.sh      # 또는 build-wsl2.sh

# 4) 기동
bash scripts/run-mac.sh        # 또는 run-wsl2.sh

# 5) provision 완주 대기 후 §7 첫 실행 흐름 진행
```

이 흐름에서는 `offline-prefetch.sh` / `offline-load.sh` 를 건너뜁니다. 빌드된 이미지가 바로 Docker 데몬에 적재되어 `compose up` 이 찾습니다.

**주의 1**: 이 단일 머신 테스트 흐름에서 `run-mac.sh` / `run-wsl2.sh` 를 바로 실행하면, 로컬 Docker 데몬에 GitLab 이미지가 없는 경우 Compose 가 GitLab 이미지를 pull 합니다. 즉 이 부록 흐름은 **온라인 또는 사내 네트워크 연결이 가능한 개발 검증용**이지, 폐쇄망 반입 검증 절차가 아닙니다.

**주의 2**: 실제 폐쇄망 운영 검증은 **반드시 §3~§6 의 에어갭 절차**를 따르세요. 특히 `scripts/offline-prefetch.sh` 로 `ttc-allinone-*` 과 `gitlab-*` tarball 2개를 만들고, 운영 머신에서 `offline-load.sh` 로 둘 다 load 한 뒤 `compose up` 해야 합니다.

**주의 3**: 개발 머신의 `~/ttc-allinone-data` 나 Docker 이미지를 그대로 옮기는 것은 권장하지 않습니다 (서비스별 런타임 상태가 경로·호스트명에 종속).

---

## 확장 포인트

- Dify workflow 수정 → `scripts/dify-assets/sonar-analyzer-workflow.yaml` → 이미지 재빌드 (또는 Dify Studio 에서 runtime 수정 — 재기동 시 YAML 이 덮어씀).
- severity 라우팅 변경 → `pipeline-scripts/sonar_issue_exporter.py` 의 `_SEVERITY_ROUTING`.
- 새 언어 추가 → `pipeline-scripts/repo_context_builder.py` 의 `LANG_CONFIG` 에 tree-sitter grammar 추가.

**이슈·PR 환영**. 개선 아이디어:
- GitLab 샘플 프로젝트 (`realworld` 외) 추가 vendoring 후보 발굴
- Ollama 모델 자동 sync 스크립트 (오프라인 업데이트 파이프라인)
- Dify 1.14+ 업그레이드 시 workflow YAML 스키마 검증 자동화
