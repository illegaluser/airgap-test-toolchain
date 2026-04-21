# TTC 4-Pipeline All-in-One Integrated Image

> **한 줄 요약** — 폐쇄망/에어갭 환경에서 **Jenkins + Dify + SonarQube + GitLab + Ollama RAG** 를 한 번에 띄우고, GitLab 레포에 커밋이 들어오면 "AI 가 코드 품질을 분석해 GitLab Issue 로 자동 등록" 하는 오프라인 코드 품질 파이프라인 스택입니다.

---

## 목차

1. [이 스택이 해주는 것](#1-이-스택이-해주는-것)
2. [전체 흐름 한눈에 보기](#2-전체-흐름-한눈에-보기)
3. [사전 준비 — 호스트에 무엇이 필요한가](#3-사전-준비--호스트에-무엇이-필요한가)
4. [빠른 시작 — 3 명령으로 빌드 + 기동](#4-빠른-시작--3-명령으로-빌드--기동)
5. [첫 실행 — 샘플 레포로 파이프라인 돌려보기](#5-첫-실행--샘플-레포로-파이프라인-돌려보기)
6. [각 파이프라인 상세](#6-각-파이프라인-상세)
7. [결과물 읽는 법 — GitLab Issue 의 7 섹션](#7-결과물-읽는-법--gitlab-issue-의-7-섹션)
8. [접속 정보 & 자격](#8-접속-정보--자격)
9. [자동 프로비저닝이 무엇을 하는가](#9-자동-프로비저닝이-무엇을-하는가)
10. [트러블슈팅](#10-트러블슈팅)
11. [초기화 & 재시작](#11-초기화--재시작)
12. [에어갭 배포 (오프라인 반출)](#12-에어갭-배포-오프라인-반출)
13. [파일 구성 레퍼런스](#13-파일-구성-레퍼런스)
14. [프로덕션 전 체크리스트](#14-프로덕션-전-체크리스트)

---

## 1. 이 스택이 해주는 것

개발팀에 흔한 고민:

- "정적분석은 돌리는데, Sonar 이슈가 수백 개 쌓여서 해결자가 어디부터 봐야 할지 모른다."
- "AI 리뷰를 붙이고 싶은데 폐쇄망이라 ChatGPT API 를 못 쓴다."
- "이슈 관리가 Sonar 대시보드 따로, GitLab 이슈 따로라 일관되지 않는다."

이 스택은 위 문제를 이렇게 해결합니다:

1. **레포 하나 + 커밋 SHA 하나**를 입력하면
2. Jenkins 가 자동으로 (a) 코드를 **tree-sitter 로 함수 단위 청킹** → (b) **Ollama + bge-m3 임베딩**으로 Dify Knowledge Base 에 저장 → (c) **SonarQube 스캔** → (d) 각 Sonar 이슈를 **Dify Workflow 로 LLM 분석** (멀티쿼리 RAG + qwen3-coder/gemma4 severity 라우팅) → (e) **GitLab Issue 로 자동 등록** (위치·코드·수정제안·영향분석·링크 포함)
3. LLM 이 "이건 오탐" 으로 판정하면 **Sonar 에서도 자동 false-positive 마킹**을 시도하고, 실패하면 `classification:false_positive` 라벨을 달아 GitLab Issue 를 만듭니다 (Dual-path).

**모든 처리가 컨테이너 내부 + 호스트 Ollama 에서만 일어나기 때문에 인터넷이 없어도 동작합니다.**

---

## 2. 전체 흐름 한눈에 보기

```
┌──────────────────────────────────────────────────────────────────┐
│  사용자:  Jenkins UI 에서 "00-코드-분석-체인" 1회 클릭            │
│           (REPO_URL + BRANCH + ANALYSIS_MODE 지정)                │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 1  COMMIT_SHA 해석 (git ls-remote)                         │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 2  →  P1 "01-코드-사전학습" Job 호출                       │
│              · git clone                                           │
│              · tree-sitter AST 청킹 (py/java/ts/tsx/js)            │
│              · Ollama gemma4:e4b 로 청크 요약 prepend              │
│              · Dify Dataset 에 bge-m3 로 업로드                    │
│              · /data/kb_manifest.json 기록 (commit_sha 포함)       │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 3  →  P2 "02-코드-정적분석" Job 호출                       │
│              · git checkout ${COMMIT_SHA}                          │
│              · SonarScanner CLI → SonarQube 서버에 리포트 전송     │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 4  →  P3 "03-정적분석-결과분석-이슈등록" Job 호출          │
│              (1) sonar_issue_exporter  — Sonar API 에서 이슈 수집  │
│                  + tree-sitter enclosing_function 추출             │
│                  + git blame/log (git_context)                     │
│                  + callgraph 역인덱스 (direct_callers)             │
│                  + clustering (같은 규칙+함수 묶음)                │
│                  + severity routing (BLOCKER/CRITICAL→qwen3-coder, │
│                    MAJOR→gemma4, MINOR/INFO→skip_llm 템플릿)       │
│                  + diff-mode (last_scan.json 과 symmetric diff)    │
│              (2) dify_sonar_issue_analyzer  — Dify Workflow 호출   │
│                  + multi-query kb_query (4줄: 코드창 + fn + path   │
│                    + rule name)                                    │
│                  + LLM 출력: title / labels / impact /             │
│                    suggested_fix / classification / confidence /   │
│                    fp_reason / suggested_diff (8 필드)             │
│              (3) gitlab_issue_creator  — GitLab Issue 생성         │
│                  + Dual-path FP 처리 (Sonar 전이 시도 → 실패 시    │
│                    라벨)                                             │
│                  + 본문 7~8 섹션 deterministic 렌더                │
│                  + dedup (같은 Sonar key 재실행 시 skip)            │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 5  Chain Summary                                            │
│   /var/knowledges/state/chain_<sha>.json 기록 (p3_summary 포함)    │
└──────────────────────────────────────────────────────────────────┘
```

| 파이프라인 | Job 이름 | 주요 역할 | Dify 사용 |
|:--:|----|----|:--:|
| **0** | `00-코드-분석-체인` | 오케스트레이터 — 커밋 SHA 1회 해석 후 P1→P2→P3 순차 트리거 | — |
| 1 | `01-코드-사전학습` | 레포 → AST 청킹 → 임베딩 → Dify KB | ✅ |
| 2 | `02-코드-정적분석` | SonarQube 스캔 | — |
| 3 | `03-정적분석-결과분석-이슈등록` | Sonar 이슈 → LLM 분석 → GitLab Issue | ✅ |
| 4 | `04-AI평가` | DeepEval + Ollama judge + Playwright (선택) | — |

---

## 3. 사전 준비 — 호스트에 무엇이 필요한가

### 3.1 필수 소프트웨어

| 항목 | macOS | Windows (WSL2) | 비고 |
|------|-------|---------------|------|
| Docker Desktop | 설치 | 설치 + WSL2 백엔드 활성화 | ≥ 25.x 권장 |
| Docker 메모리 할당 | ≥ **12 GB** | ≥ **12 GB** | SonarQube ES + Jenkins + Dify 동시 구동 |
| 디스크 여유 공간 | ≥ **40 GB** | ≥ **40 GB** | 이미지(14GB) + 데이터(5~10GB) + tarball 반출 시(~20GB) |
| Git | 호스트에 1개 | 호스트에 1개 | 샘플 레포 준비용 |

### 3.2 호스트 Ollama (LLM 런타임)

**왜 컨테이너 안이 아니라 호스트에 설치하나?** — Apple Silicon 은 Metal GPU, NVIDIA 는 CUDA 를 써야 gemma4/qwen3-coder 가 빠릅니다. 도커 GPU 패스스루는 플랫폼마다 다르므로 호스트 데몬이 가장 단순합니다.

```bash
# macOS (Homebrew)
brew install ollama
brew services start ollama

# Windows (WSL2 우분투 내부)
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &

# 공통 — 모델 받기 (최초 1회, 약 20 분)
ollama pull gemma4:e4b          # Dify Workflow 기본 판단 모델 (~4GB)
ollama pull bge-m3              # 임베딩 (~1GB)
ollama pull qwen3-coder:30b     # BLOCKER/CRITICAL severity 라우팅용 (~20GB, 선택)
```

확인:
```bash
curl http://localhost:11434/api/tags | grep -oE '"name":"[^"]+"'
# → "name":"gemma4:e4b", "name":"bge-m3" ...
```

> **팁** — `qwen3-coder:30b` 가 디스크를 많이 먹거나 너무 느리면 당장은 받지 마세요. severity 라우팅이 자동으로 `gemma4:e4b` 로 폴백되지는 않지만, `sonar_issue_exporter.py` 의 `_SEVERITY_ROUTING` 딕셔너리를 수정해 모든 severity 를 gemma4 로 매핑할 수 있습니다.

### 3.3 포트 충돌 체크

본 스택이 사용하는 호스트 포트:

| 포트 | 서비스 | 외부 접속 URL |
|------|--------|---------------|
| 28080 | Jenkins | http://localhost:28080 |
| 28081 | Dify | http://localhost:28081 |
| 29000 | SonarQube | http://localhost:29000 |
| 28090 | GitLab HTTP | http://localhost:28090 |
| 28022 | GitLab SSH | `git@localhost:28022` |
| 50002 | Jenkins Agent JNLP | (내부용) |

형제 스택 `playwright-allinone/` 은 18080/18081/50001 을 쓰므로 **같이 띄워도 충돌 없습니다**.

```bash
# 포트 점유 체크 (macOS/Linux)
lsof -i :28080,28081,29000,28090,28022
```

---

## 4. 빠른 시작 — 3 명령으로 빌드 + 기동

### 4.1 리포지토리 클론

```bash
git clone <이 레포 URL>
cd airgap-test-toolchain/code-AI-quality-allinone
```

**이후 모든 명령은 이 `code-AI-quality-allinone` 폴더 안에서 실행합니다.**

### 4.2 빌드 (최초 1회, ~15 분)

```bash
# 1) 플러그인 다운로드 (온라인 필요, 약 3분)
bash scripts/download-plugins.sh

# 2) 이미지 빌드 (오프라인 가능, 약 12분)
#    macOS (Apple Silicon / Intel 자동 감지)
bash scripts/build-mac.sh
#    Windows (WSL2)
bash scripts/build-wsl2.sh
```

빌드가 끝나면 로컬에 `ttc-allinone:mac-dev` (Mac) 또는 `ttc-allinone:wsl2-dev` (WSL2) 이미지가 생깁니다.

```bash
docker images ttc-allinone
# → REPOSITORY     TAG       SIZE     CREATED
#   ttc-allinone   mac-dev   14GB     2 minutes ago
```

### 4.3 기동 (최초 1회 프로비저닝 약 7 분)

```bash
# macOS
bash scripts/run-mac.sh
# WSL2
bash scripts/run-wsl2.sh
```

두 개의 컨테이너가 뜹니다:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
# ttc-allinone    Up 30 seconds
# ttc-gitlab      Up 30 seconds (health: starting)
```

**프로비저닝 진행 상황**을 보려면:

```bash
docker logs -f ttc-allinone | grep -E "provision|entrypoint"
```

완료 메시지가 뜨면 준비 끝:

```
[provision] 자동 프로비저닝 완료.
[provision]   Jenkins    : http://127.0.0.1:28080 (admin / password)
[provision]   Dify       : http://127.0.0.1:28081 (admin@ttc.local / TtcAdmin!2026)
[provision]   SonarQube  : http://localhost:29000 (admin / TtcAdmin!2026)
[provision]   GitLab     : http://localhost:28090 (root / ChangeMe!Pass)
[entrypoint] 앱 프로비저닝 완료.
```

**기동이 다 되었는지 빠르게 확인**:

```bash
# 5개 Jenkins Job 이 다 등록됐나?
curl -s -u admin:password 'http://127.0.0.1:28080/api/json?tree=jobs%5Bname%5D' \
  | python3 -c "import json,sys;print(*(j['name'] for j in json.load(sys.stdin)['jobs']),sep='\n')"
# → 00-코드-분석-체인
#   01-코드-사전학습
#   02-코드-정적분석
#   03-정적분석-결과분석-이슈등록
#   04-AI평가

# 자동 프로비저닝 마커 파일 11개가 다 있나?
docker exec ttc-allinone ls /data/.provision/
# → dataset_api_key  dataset_id  default_models.ok  gitlab_root_pat
#   jenkins_sonar_integration.ok  ollama_embedding.ok  ollama_plugin.ok
#   sonar_token  workflow_api_key  workflow_app_id  workflow_published.ok
```

---

## 5. 첫 실행 — 샘플 레포로 파이프라인 돌려보기

GitLab 에 분석 대상 레포가 있어야 합니다. 본 스택은 레포를 자동 생성하지 않으므로 **한 번만 수동으로 만들어 줍니다**.

### 5.1 분석용 샘플 레포 만들기 (3 분)

```bash
# 로컬에 샘플 코드 폴더 만들기
mkdir -p /tmp/dscore-ttc-sample/src && cd /tmp/dscore-ttc-sample

# 의도적으로 Sonar 가 잡을만한 bare except (python:S5754 CRITICAL) 를 심음
cat > src/auth.py <<'PY'
"""Simple authentication helpers."""
import hashlib, os

def hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def verify_password(raw: str, stored: str) -> bool:
    return hash_password(raw) == stored

def login(username: str, password: str, user_store: dict) -> bool:
    try:
        stored = user_store[username]
        return verify_password(password, stored)
    except:  # noqa: E722 — Sonar python:S5754 CRITICAL
        return False
PY

cat > src/session.py <<'PY'
"""Session helpers that call login() — RAG 가 호출 관계를 잡아내는지 확인용."""
from src.auth import login

def check_session(token: str, user_store: dict) -> bool:
    if not token or ":" not in token:
        return False
    user, pw = token.split(":", 1)
    return login(user, pw, user_store)
PY

touch src/__init__.py

cat > sonar-project.properties <<'CFG'
sonar.projectKey=dscore-ttc-sample
sonar.projectName=dscore-ttc-sample
sonar.sources=src
sonar.python.version=3.11
sonar.sourceEncoding=UTF-8
CFG

git init -q -b main
git add -A
git -c user.email=test@ttc.local -c user.name=tester commit -q -m "initial"
```

### 5.2 GitLab 에 레포 생성 + 푸시

```bash
# GitLab PAT 를 컨테이너에서 가져오기 (provision.sh 가 자동 발급해둠)
GITLAB_PAT=$(docker exec ttc-allinone cat /data/.provision/gitlab_root_pat)

# GitLab 에 프로젝트 생성 (REST API)
curl -sS -X POST "http://localhost:28090/api/v4/projects" \
  -H "PRIVATE-TOKEN: $GITLAB_PAT" \
  -d "name=dscore-ttc-sample&visibility=private&initialize_with_readme=false"

# 푸시
cd /tmp/dscore-ttc-sample
git remote add origin "http://oauth2:${GITLAB_PAT}@localhost:28090/root/dscore-ttc-sample.git"
git push -u origin main
```

GitLab UI ([http://localhost:28090/root/dscore-ttc-sample](http://localhost:28090/root/dscore-ttc-sample), `root` / `ChangeMe!Pass`) 에서 파일이 올라갔는지 확인하세요.

### 5.3 00 체인 Job 실행 (Jenkins UI)

1. [http://localhost:28080](http://localhost:28080) 접속 (`admin` / `password`).
2. **`00-코드-분석-체인`** Job 클릭.
3. **왼쪽 메뉴 → "Build with Parameters"** 클릭. (최초 1회는 "Build Now" 로 한 번 눌러 parameter discovery 를 유도해야 할 수 있습니다 — 실패하면 10초 뒤 "Build with Parameters" 가 나타납니다.)
4. 파라미터 입력:
   - `REPO_URL`: `http://gitlab:80/root/dscore-ttc-sample.git`
   - `BRANCH`: `main`
   - `ANALYSIS_MODE`: `full`
   - 나머지는 기본값
5. **Build** 클릭.

### 5.4 실행 모니터링

Jenkins UI 의 **Stage View** 또는 아래 CLI:

```bash
# 체인의 현재 단계 확인
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

처음 실행은 약 **3~5 분** 걸립니다 (P1: 1분, P2: 1분, P3 LLM 분석: 1~3분).

완료되면:

```
status: SUCCESS
  1. Resolve Commit SHA          SUCCESS    2.1s
  2. Trigger P1 (사전학습)        SUCCESS   45.3s
  3. Trigger P2 (정적분석)        SUCCESS   55.2s
  4. Trigger P3 (이슈 등록)       SUCCESS   90.8s
  5. Chain Summary               SUCCESS    1.2s
```

### 5.5 결과 확인

1. **GitLab Issue 생성 확인**: [http://localhost:28090/root/dscore-ttc-sample/-/issues](http://localhost:28090/root/dscore-ttc-sample/-/issues)
   - `[CRITICAL] Specify an exception class to catch or reraise the exception` 같은 제목의 Issue #1 이 있어야 합니다.
2. **SonarQube 대시보드**: [http://localhost:29000/dashboard?id=dscore-ttc-sample](http://localhost:29000/dashboard?id=dscore-ttc-sample)
   - 프로젝트가 등장하고 CRITICAL 이슈 1건이 보입니다.
3. **Dify Studio**: [http://localhost:28081](http://localhost:28081) (`admin@ttc.local` / `TtcAdmin!2026`)
   - Knowledge → `code-context-kb` → Recall Testing 에서 `login function` 검색해보면 청크가 retrieve 됩니다.
4. **상태 파일**:
   ```bash
   docker exec ttc-allinone cat /data/kb_manifest.json
   docker exec ttc-allinone cat /var/knowledges/state/chain_*.json
   ```

### 5.6 재실행 (dedup 확인)

같은 커밋으로 00 체인 Job 을 한 번 더 눌러보세요. 이번에는:

- P3 exporter 가 `[diff-mode] skipped N cached issues` 또는 `created=0` 를 출력
- `chain_<sha>.json` 의 `p3_summary.skipped=1` 로 dedup 이 정확히 잡히는 것이 보입니다.
- GitLab Issues 페이지에 중복 Issue 가 **생기지 않습니다**.

---

## 6. 각 파이프라인 상세

### 6.1 `00-코드-분석-체인` (Phase 1.5 오케스트레이터)

**역할**: 사용자 클릭 1회 → P1 → P2 → P3 → 체인 요약까지 자동 실행.

**파라미터** (중요한 것만):
| 이름 | 기본값 | 설명 |
|------|--------|------|
| `REPO_URL` | `http://gitlab:80/root/dscore-ttc-sample.git` | 분석 대상 Git URL (컨테이너 내부 이름 `gitlab` 사용) |
| `BRANCH` | `main` | 분석 브랜치 |
| `ANALYSIS_MODE` | `full` | `full` = KB 강제 재빌드, `commit` = `kb_manifest.commit_sha` 일치 시 재사용 |
| `COMMIT_SHA` | `(빈 값)` | 지정 시 그 커밋으로 체크아웃, 빈 값이면 BRANCH HEAD |

**결과물**:
- 각 서브 Job 이 자체 산출물 생성 (각 Job 섹션 참조).
- `/var/knowledges/state/chain_<sha>.json` — 체인 전체 요약 + p3_summary.

### 6.2 `01-코드-사전학습` (P1 — RAG KB 구축)

**핵심 처리**:

1. **Git Clone** — `${REPO_URL}` 를 `/var/knowledges/codes/<repo>` 에 clone.
2. **AST 청킹** — `repo_context_builder.py` 가 tree-sitter 로 py/java/ts/tsx/js 파일을 **함수/클래스 단위 청크** 로 분해. 각 청크에 `path`, `symbol`, `lines`, `callers`, `callees`, `commit_sha` 메타 부착.
3. **Contextual Enrichment** (선택, 기본 ON) — `contextual_enricher.py` 가 gemma4:e4b 로 각 청크에 "이 함수가 하는 일 2줄 요약" 을 prepend.
4. **Dify Dataset 업로드** — `doc_processor.py` 가 `code-context-kb` Dataset 에 청크별 document 업로드 (bge-m3 임베딩 자동).
5. **kb_manifest 기록** — `/data/kb_manifest.json` 에 `{repo_url, branch, commit_sha, document_count, dataset_id, uploaded_at}` 저장.

**왜 중요한가**: P3 의 LLM 분석이 이 KB 를 multi-query RAG 로 retrieve 합니다. 샘플 레포의 `src/session.py::check_session` 이 `src/auth.py::login` 을 호출한다는 호출 관계를 LLM 이 언급하는 이유는 이 단계에서 청크를 넣어뒀기 때문입니다.

### 6.3 `02-코드-정적분석` (P2 — SonarQube 스캔)

**핵심 처리**:

1. **(0) KB Bootstrap Guard** (체인 경로에서만 작동):
   - `ANALYSIS_MODE=full` + 체인 경로 → manifest 검증만. 불일치면 fail loud (이전 단계 실패 의미).
   - `ANALYSIS_MODE=commit` + 단독 실행 + manifest 불일치/부재 → P1 자동 트리거.
   - `COMMIT_SHA` 빈 값 → guard 전체 skip (수동 단독 실행 하위호환).
2. **Checkout** — GitLab 에서 clone → `git checkout ${COMMIT_SHA}` 로 고정.
3. **Node.js 준비** — SonarJS 용 Node v22 설치 (최초 1회 캐시).
4. **SonarScanner 실행** — `withSonarQubeEnv('dscore-sonar')` + `tool 'SonarScanner-CLI'` 로 Sonar 서버에 리포트 전송.

**확인**: [http://localhost:29000/dashboard?id=dscore-ttc-sample](http://localhost:29000/dashboard?id=dscore-ttc-sample)

### 6.4 `03-정적분석-결과분석-이슈등록` (P3 — LLM 분석 + 이슈 등록)

가장 복잡한 파이프라인. 4 개 스테이지로 나뉘어 있습니다.

#### Stage (0) Resolve Commit SHA + Fetch Repo + Freshness Assert

- `COMMIT_SHA` 가 파라미터로 전달되면 그 값, 아니면 `git ls-remote` 로 BRANCH HEAD 해석.
- `/var/knowledges/codes/<repo>` 에 얕게 clone/fetch 하고 해당 SHA 로 체크아웃 (git_context / enclosing_function 추출용).
- `ANALYSIS_MODE=commit` 이면 `/data/kb_manifest.json` 과 SHA 일치 검증 (불일치 시 fail).

#### Stage (1) Export Sonar Issues — `sonar_issue_exporter.py`

Sonar API 에서 이슈를 수집하고 **대대적으로 보강** 합니다:

| 필드 | 설명 |
|------|------|
| `enclosing_function` / `enclosing_lines` | tree-sitter 로 "이 이슈 라인을 포함하는 함수" 추출 |
| `git_context` | `git blame -L` + `git log` 요약 3 줄 (누가 언제 썼는지) |
| `direct_callers` | P1 이 남긴 JSONL 청크에서 callgraph 역인덱스 구축 → 최대 10명 caller |
| `cluster_key` | `sha1(rule_key + enclosing_function + dirname)` — 같은 패턴 이슈 묶기용 |
| `affected_locations` | clustering 으로 묶인 나머지 이슈 위치 (대표 1건에 부착) |
| `judge_model` / `skip_llm` | severity 라우팅 결과 (BLOCKER/CRITICAL → qwen3-coder, MAJOR → gemma4, 그 외 → skip) |

**diff-mode** — `--mode incremental` 지정 시 `/var/knowledges/state/last_scan.json` 과 symmetric diff 해서 이미 본 이슈는 skip. `--mode full` 은 전수 재분석.

#### Stage (2) Analyze by Dify Workflow — `dify_sonar_issue_analyzer.py`

각 이슈를 Dify `Sonar Issue Analyzer` workflow 에 전달:

- **Multi-query `kb_query`**: `이슈 라인 ±3줄 코드` + `function: {enclosing_function}` + `path: {relative_path}` + `rule name` 4줄 조합. 단순히 rule 이름만 넣던 것보다 RAG 적중률이 크게 올라갑니다.
- **skip_llm 분기**: `skip_llm=true` 인 이슈는 Dify 호출 자체를 건너뛰고 템플릿 응답으로 대체 (MINOR/INFO 비용 절감).
- **LLM 출력 8 필드**: `title`, `labels`, `impact_analysis_markdown`, `suggested_fix_markdown`, `classification` (true_positive | false_positive | wont_fix), `fp_reason`, `confidence` (high/medium/low), `suggested_diff` (unified diff).

#### Stage (3) Create GitLab Issues (Dedup)

`gitlab_issue_creator.py`:

- **Dual-path FP 처리**:
  1. `classification == "false_positive"` 이면 Sonar `POST /api/issues/do_transition?transition=falsepositive` 시도.
  2. **성공** → GitLab Issue 생성 skip, 로그 `[FP-TRANSITION]`, `p3_summary.fp_transitioned++`.
  3. **실패** (권한/네트워크) → GitLab Issue 는 생성하되 `fp_transition_failed` 라벨 추가, `p3_summary.fp_transition_failed++`.
- **Dedup**: 같은 Sonar key 를 가진 기존 Issue 가 있으면 skip (`p3_summary.skipped++`).
- **본문 deterministic 렌더** — 다음 섹션 참조.

### 6.5 `04-AI평가` (선택)

DeepEval + Ollama judge + Playwright. UI 자동화 (Chromium 내장). 본 문서 범위 외.

---

## 7. 결과물 읽는 법 — GitLab Issue 의 7 섹션

P3 가 만든 GitLab Issue 는 **사실 정보는 creator 가 deterministic 하게 렌더** 하고, **해석이 필요한 두 섹션 (영향 분석 + 수정 제안) 만 LLM 이 작성** 합니다. 해결자 입장에서 "어디·무엇·왜·어떻게" 를 30초 내 파악할 수 있도록 설계됐습니다.

```
> **TL;DR** — `src/auth.py:21` `login` 함수 · Specify an exception class to catch or reraise the exception

### 📍 위치
| 항목 | 값 |
|------|-----|
| 파일 | [`src/auth.py:21`](http://localhost:28090/.../blob/main/src/auth.py#L21) |  ← 클릭 시 GitLab 파일 해당 라인
| 함수 | `login` *(line 16-23)* |
| Rule | `python:S5754` · "SystemExit" should be re-raised |
| Severity | `CRITICAL` |
| Commit | [`e38bd123`](http://.../commit/e38bd123...) |  ← 클릭 시 GitLab 커밋 상세

### 🔴 문제 코드
```
      18 |     try:
      19 |         stored = user_store[username]
      20 |         return verify_password(password, stored)
>>    21 |     except:  # 문제 라인에 '>>' 마커
      22 |         return False
```

### ✅ 수정 제안
(LLM 생성 — 빈 값이면 섹션 통째로 생략)

### 💡 Suggested Diff
```diff
-    except:
+    except KeyError:
+        return False
+    except Exception as e:
+        logger.error(...)
+        return False
```

### 📊 영향 분석
(LLM 생성 — RAG 가 찾아낸 호출 관계 기반 해석)
"이 함수는 src/session.py::check_session 에서 호출되므로 ..."

### 🧭 Affected Locations  ← clustering 으로 묶인 유사 이슈
| component | line | sonar key |
| ... | ... | ... |

### 📖 Rule 상세
<details>
<summary>python:S5754 전체 설명</summary>
(Sonar rule description 원문)
</details>

### 🔗 링크
- SonarQube 이슈 상세
- GitLab 파일 (line 21)
- GitLab 커밋

---
_commit: `e38bd123` (full scan) · sonar: http://localhost:29000/...
```

**라벨 (labels)**: `severity:CRITICAL`, `classification:true_positive`, `confidence:high`, + LLM 이 생성한 도메인 라벨 (예: `Authentication`, `Code Smell`). 오탐 전이 실패 시 `fp_transition_failed`, skip_llm 처리 시 `auto_template:true` 추가.

---

## 8. 접속 정보 & 자격

### 8.1 외부 노출 서비스

| 서비스 | URL | ID | 비밀번호 | override env | 용도 |
|--------|-----|----|---------|--------------|------|
| Jenkins | http://localhost:28080 | `admin` | `password` | `jenkins-init/basic-security.groovy` | 파이프라인 Job 진입점 |
| Dify | http://localhost:28081 | `admin@ttc.local` | `TtcAdmin!2026` | `DIFY_ADMIN_EMAIL`/`_PASSWORD` | Workflow/Dataset 편집 |
| SonarQube | http://localhost:29000 | `admin` | `TtcAdmin!2026` | `SONAR_ADMIN_NEW_PASSWORD` | 정적분석 대시보드 |
| GitLab | http://localhost:28090 | `root` | `ChangeMe!Pass` | `GITLAB_ROOT_PASSWORD` | 소스 호스팅 + Issue |
| Ollama | http://host.docker.internal:11434 | — | — | `OLLAMA_BASE_URL` | LLM 추론 (호스트 데몬) |

### 8.2 자동 발급·주입된 Jenkins Credentials

`provision.sh` 가 서비스별 API 로 동적 발급해 Jenkins Credentials Store 에 넣어두는 자격. **리포에 저장되지 않습니다**.

| Credential ID | 종류 | 발급처 | 사용처 |
|---------------|------|--------|--------|
| `gitlab-pat` | GitLab PAT (api, 유효 364일) | `POST /api/v4/users/1/personal_access_tokens` | P2·P3 — clone / Issue 생성 |
| `sonarqube-token` | SonarQube User Token | `POST /api/user_tokens/generate` | P2 scanner 인증 + P3 FP 전이 |
| `dify-dataset-id` | Dify Dataset UUID | `POST /console/api/datasets` | P1 컨텍스트 적재 |
| `dify-knowledge-key` | Dify Dataset API Key | `POST /console/api/datasets/api-keys` | P1 Dataset API 호출 |
| `dify-workflow-key` | Dify App API Key | `POST /console/api/apps/<id>/api-keys` | P3 Workflow 호출 |

꺼내 보고 싶으면:
```bash
docker exec ttc-allinone ls /data/.provision/
docker exec ttc-allinone cat /data/.provision/gitlab_root_pat     # 평문 PAT
docker exec ttc-allinone cat /data/.provision/sonar_token         # 평문 Sonar token
```

### 8.3 내부 서비스 (컨테이너 외부 노출 X)

| 서비스 | 접근 | 자격 | 비고 |
|--------|------|------|------|
| PostgreSQL | `127.0.0.1:5432` (컨테이너 내부) | `postgres` / `difyai123456` | Dify 메타데이터 + Sonar DB |
| Redis | `127.0.0.1:6379` | (없음) | Dify 큐 |
| Qdrant | `127.0.0.1:6333` | (없음) | Dify 벡터 DB (법정 mode) |
| Dify plugin-daemon | `127.0.0.1:5002` | `INNER_API_KEY_FOR_PLUGIN` | plugin 등록 API |

---

## 9. 자동 프로비저닝이 무엇을 하는가

`scripts/provision.sh` 가 최초 기동 시 자동 수행 (멱등, `/data/.provision/*.ok` 마커로 재실행 안전):

| 대상 | 작업 |
|------|------|
| Dify | 관리자 setup → 로그인 → Ollama 플러그인 설치 (.difypkg) → Ollama provider 등록 (gemma4:e4b) → Ollama embedding 등록 (bge-m3) → workspace 기본 모델 설정 → `code-context-kb` Dataset 생성 (bge-m3 + hybrid_search + high_quality) → `Sonar Issue Analyzer` Workflow import → Workflow publish → Dataset/App API 키 2종 발급 |
| GitLab | Reconfigure 대기 → oauth password grant → root PAT 발급 (만료 364일) |
| SonarQube | ready 대기 → admin 비밀번호 변경 (`admin` → `TtcAdmin!2026`) → user token `jenkins-auto` 발급 |
| Jenkins | 5 Credentials 주입 → SonarQube 서버 + SonarScanner tool Groovy 로 등록 (`dscore-sonar` 이름) → 5 Pipeline Job 등록 (00 체인 + 01~04) → Jenkinsfile 의 `GITLAB_PAT = ''` 를 `credentials('gitlab-pat')` 로 sed 치환 |

### 자동화되지 않는 잔존 수동 작업

- **GitLab 프로젝트 생성 + 소스 push** — 팀 정책에 따라 다르므로 수동 (§5.1~5.2 참고).
- **큰 레포의 초기 KB 빌드** — tree-sitter 청킹은 1000 파일당 ~1분. Dify embedding 업로드는 파일당 ~1초.

---

## 10. 트러블슈팅

### 10.1 Jenkins Job 이 "No item named 01-ì½ë..." 로 실패

**원인**: Jenkins JVM 이 UTF-8 로 기동되지 않아 Korean Job 이름이 깨짐.

**확인**:
```bash
docker exec ttc-allinone ps ax | grep jenkins.war | grep -oE "Dfile.encoding=[A-Z0-9-]+"
# → Dfile.encoding=UTF-8   (이게 있어야 정상)
```

**수정**: 이미 `scripts/supervisord.conf` 에 반영되어 있습니다. 예전 컨테이너라면 재빌드 + 기동.

### 10.2 `00` Job 이 "is not parameterized" HTTP 400 으로 실패

**원인**: Declarative pipeline 의 parameters 블록이 아직 Jenkins config.xml 에 등록되지 않음 (최초 1회 문제).

**수정**: 한 번 "Build Now" (파라미터 없이) 로 돌려 실패 로그를 낸 뒤 "Build with Parameters" 로 다시 실행.

### 10.3 P2 (SonarScanner) 가 `withSonarQubeEnv` 를 모른다고 실패

**원인**: Sonar Jenkins plugin 미설치 — `download-plugins.sh` 결과물이 구버전이면 발생.

**확인**:
```bash
ls code-AI-quality-allinone/jenkins-plugins/ | grep -E "^(sonar|pipeline-build-step)"
# 두 파일 모두 있어야 함
```

**수정**: `bash scripts/download-plugins.sh` 재실행 → 이미지 재빌드.

### 10.4 P1 의 tree-sitter 가 0 청크를 만듦

**원인**: `tree-sitter` 와 `tree-sitter-languages` 버전 불일치. `Dockerfile` 에 `tree-sitter<0.22` 핀이 있어야 합니다.

**확인**:
```bash
docker exec ttc-allinone pip show tree-sitter | grep Version
# → Version: 0.21.x
```

### 10.5 P3 의 Dify Workflow 가 "Workflow not published" (HTTP 400)

**원인**: provision.sh 의 `dify_publish_workflow` 가 실패.

**수정**:
```bash
# 수동 publish
docker exec ttc-allinone bash -c '
source /opt/provision.sh # (함수만 로드 — 전체 재실행은 idempotent 하지만 시간 소요)
'
# 또는 Dify Studio → Sonar Issue Analyzer → Publish 버튼 클릭
```

### 10.6 GitLab 계속 `(health: starting)` 에서 멈춤

**원인**: arm64 이미지의 reconfigure 가 5-10분 소요. 정상.

**확인**:
```bash
docker exec ttc-gitlab gitlab-ctl status
# 모든 서비스가 "run: ..." 상태여야 함
curl -sf http://localhost:28090/users/sign_in && echo "GitLab OK"
```

### 10.7 SonarQube 가 flood_stage 로 read-only 모드

**원인**: 호스트 `/System/Volumes/Data` 디스크 사용률 > 95% → Docker VM 내 ES 가 flood_stage.

**수정**: 호스트 디스크 정리. macOS 는 `scripts/entrypoint.sh` 가 `SONAR_DATA_HOST=/var/lib/sonarqube_data` overlay 로 피함 (Mac 전용 분기).

### 10.8 Executor 부족으로 체인이 대기

**증상**: `02-코드-정적분석 #N` 에서 `build job: 01-코드-사전학습` 호출이 "Still waiting to schedule task" 에서 대기.

**원인**: Jenkins 기본 executor 수 2. 체인 (00) + 하위 Job 이 2개 이상 잡으면 deadlock.

**현재 설계**: Step A 후 02 Bootstrap Guard 를 재설계하여 full 모드는 체인이 P1 을 담당하고 guard 는 검증만 — deadlock 없음. commit 모드 단독 실행만 P1 자동 트리거.

**그래도 막히면**: Jenkins → Manage Jenkins → Nodes → built-in → 설정에서 "Number of executors" 를 4 로 증가.

### 10.9 Ollama 연결 실패

**확인**:
```bash
# 컨테이너 내부에서 호스트 Ollama 에 도달?
docker exec ttc-allinone curl -sf http://host.docker.internal:11434/api/tags | head -c 200
```

**실패 시**:
- Mac/Windows Docker Desktop: `host.docker.internal` 자동 해석됨. Ollama 가 localhost 전용(기본) 이면 `launchctl setenv OLLAMA_HOST "0.0.0.0"` 후 `brew services restart ollama`.
- Linux: compose `extra_hosts` 로 매핑 필요 (`host.docker.internal:host-gateway`).

### 10.10 디버깅용 로그 위치

```bash
docker logs ttc-allinone | grep "\[provision\]"    # 프로비저닝 상세
docker exec ttc-allinone cat /data/logs/jenkins.log | tail -50
docker exec ttc-allinone cat /data/logs/sonarqube.log | tail -50
docker exec ttc-allinone cat /data/logs/dify-api.log | tail -50
docker logs ttc-gitlab | tail -20
```

Jenkins Build 콘솔:
- 00 체인: [http://localhost:28080/job/00-코드-분석-체인/lastBuild/console](http://localhost:28080/job/00-코드-분석-체인/lastBuild/console)
- P3: [http://localhost:28080/job/03-정적분석-결과분석-이슈등록/lastBuild/console](http://localhost:28080/job/03-정적분석-결과분석-이슈등록/lastBuild/console)

---

## 11. 초기화 & 재시작

### 11.1 완전 초기화 (모든 데이터 지움)

```bash
cd code-AI-quality-allinone
docker compose -f docker-compose.mac.yaml down -v     # macOS
# 또는
docker compose -f docker-compose.wsl2.yaml down -v    # WSL2
rm -rf ~/ttc-allinone-data
bash scripts/run-mac.sh        # 다시 기동 → provision 재실행
```

### 11.2 프로비저닝만 재실행 (데이터 유지)

프로비저닝 마커 `/data/.provision/*.ok` 가 있어서 멱등이지만, 강제로 다시 하려면:

```bash
docker exec ttc-allinone rm -rf /data/.provision/
docker exec ttc-allinone bash /opt/provision.sh
```

### 11.3 특정 Jenkins Job 만 재등록 (config 변경 후)

```bash
docker exec ttc-allinone bash -c '
CRUMB=$(curl -s -u admin:password "http://127.0.0.1:28080/crumbIssuer/api/xml?xpath=concat(//crumbRequestField,\":\",//crumb)")
SCRIPT=$(python3 -c "
with open(\"/opt/jenkinsfiles/00 코드 분석 체인.jenkinsPipeline\", \"r\", encoding=\"utf-8\") as f:
    s = f.read()
print(s.replace(\"&\",\"&amp;\").replace(\"<\",\"&lt;\").replace(\">\",\"&gt;\"))
")
cat > /tmp/cfg.xml <<XML
<?xml version=\"1.1\" encoding=\"UTF-8\"?>
<flow-definition plugin=\"workflow-job\">
  <actions/>
  <definition class=\"org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition\">
    <script>${SCRIPT}</script>
    <sandbox>true</sandbox>
  </definition>
</flow-definition>
XML
curl -sS -u admin:password -H "$CRUMB" -H "Content-Type: application/xml; charset=utf-8" \
  --data-binary @/tmp/cfg.xml \
  "http://127.0.0.1:28080/job/00-%EC%BD%94%EB%93%9C-%EB%B6%84%EC%84%9D-%EC%B2%B4%EC%9D%B8/config.xml"
'
```

---

## 12. 에어갭 배포 (오프라인 반출)

### 12.1 온라인 머신에서 tarball 산출

```bash
cd code-AI-quality-allinone
bash scripts/download-plugins.sh
bash scripts/offline-prefetch.sh --arch arm64    # arm64 (Mac)
# 또는
bash scripts/offline-prefetch.sh --arch amd64    # amd64 (x86)
```

산출물 (~ 10GB):

```
offline-assets/<arch>/
├── ttc-allinone-<arch>-<tag>.tar.gz    # 통합 이미지
└── gitlab-gitlab-ce-<ver>-<arch>.tar.gz # GitLab 이미지
```

### 12.2 오프라인 머신에서 로드

```bash
# offline-assets/ 디렉터리를 이 폴더 아래에 함께 반입한 뒤
cd code-AI-quality-allinone
bash scripts/offline-load.sh --arch arm64
docker compose -f docker-compose.mac.yaml up -d
```

---

## 13. 파일 구성 레퍼런스

```
code-AI-quality-allinone/
├── Dockerfile                              # 통합 이미지 정의 (14GB 결과)
├── docker-compose.mac.yaml                 # Mac (arm64) compose
├── docker-compose.wsl2.yaml                # WSL2 (amd64) compose
├── README.md                               # 본 문서
├── requirements.txt                        # Python 기반 deps (playwright/deepeval 등)
│
├── pipeline-scripts/                       # 파이프라인 1·3 Python 스크립트 (컨테이너에 COPY)
│   ├── repo_context_builder.py             # P1 — tree-sitter AST 청킹
│   ├── contextual_enricher.py              # P1 — gemma4 요약 prepend
│   ├── doc_processor.py                    # P1 — Dify Dataset 업로드 + kb_manifest
│   ├── sonar_issue_exporter.py             # P3 Stage 1 — Sonar API + 강화
│   ├── dify_sonar_issue_analyzer.py        # P3 Stage 2 — Dify Workflow 호출
│   └── gitlab_issue_creator.py             # P3 Stage 3 — GitLab Issue 생성 + Dual-path FP
│
├── eval_runner/                            # 파이프라인 4 엔진 (DeepEval + Playwright)
│
├── jenkinsfiles/                           # 5 개 Pipeline 정의
│   ├── 00 코드 분석 체인.jenkinsPipeline        # 오케스트레이터
│   ├── 01 코드 사전학습.jenkinsPipeline
│   ├── 02 코드 정적분석.jenkinsPipeline
│   ├── 03 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline
│   └── 04 AI평가.jenkinsPipeline
│
├── jenkins-init/
│   └── basic-security.groovy               # admin/password 초기화
│
├── jenkins-plugins/                        # (gitignored) 빌드 시 생성
├── dify-plugins/                           # (gitignored) 빌드 시 생성
│
└── scripts/
    ├── download-plugins.sh                 # 빌드 전 플러그인 다운로드
    ├── supervisord.conf                    # 11 프로세스 (sonarqube 포함, UTF-8 JVM)
    ├── nginx.conf                          # Dify gateway (28081)
    ├── pg-init.sh                          # Postgres initdb (dify, dify_plugin, sonar)
    ├── entrypoint.sh                       # 컨테이너 진입점
    ├── provision.sh                        # 완전 자동 프로비저닝
    ├── build-mac.sh / build-wsl2.sh        # 빌드 헬퍼
    ├── run-mac.sh / run-wsl2.sh            # 기동 헬퍼
    ├── offline-prefetch.sh / offline-load.sh # 에어갭 반출/반입
    └── dify-assets/
        ├── sonar-analyzer-workflow.yaml    # P3 Dify Workflow DSL (import 대상)
        └── code-context-dataset.json       # P1 Dataset 스펙
```

### 파이프라인에서 생성되는 런타임 파일

| 파일 | 생성자 | 용도 |
|------|--------|------|
| `/data/kb_manifest.json` | P1 `doc_processor.py` | P2/P3 의 KB freshness 검증 |
| `/var/knowledges/docs/result/*.jsonl` | P1 `repo_context_builder.py` | 청크 원본 (P3 callgraph 역인덱스 소스) |
| `/var/knowledges/state/last_scan.json` | P3 `sonar_issue_exporter.py` | diff-mode baseline |
| `/var/knowledges/state/chain_<sha>.json` | 00 Chain Summary | P1/P2/P3 결과 요약 + p3_summary |
| Jenkins artifacts | 각 Job의 `archiveArtifacts` | `sonar_issues.json`, `llm_analysis.jsonl`, `gitlab_issues_created.json`, `chain_summary.json` |

---

## 14. 프로덕션 전 체크리스트

PoC 단계에서는 기본값 그대로 동작하지만, 운영/배포 전에 다음을 교체하세요:

- [ ] `JENKINS_PASSWORD` — `jenkins-init/basic-security.groovy` 수정 + 이미지 재빌드.
- [ ] `DIFY_ADMIN_PASSWORD` — compose `environment:` 에 강한 값. fresh 볼륨으로 재기동.
- [ ] `SONAR_ADMIN_NEW_PASSWORD` — compose 에 env 추가. **프로비저닝 전에** 값 교체해야 적용.
- [ ] `GITLAB_ROOT_PASSWORD` — compose 의 `GITLAB_OMNIBUS_CONFIG` 안 `initial_root_password` 교체.
- [ ] Postgres / Sonar DB 비밀번호 — `scripts/pg-init.sh` 수정 + 이미지 재빌드 + Dify/Sonar 쪽 env 동기화.
- [ ] `SECRET_KEY` (Dify 암호화 seed) — `scripts/supervisord.conf` 의 `dify-allinone-placeholder-CHANGE-ME-via-env` 교체. 장기 운영 필수.
- [ ] 네트워크 격리 — 28080/28081/29000/28090 을 외부 인터넷에 직접 노출 금지. Reverse proxy + 인증 연동 권장.
- [ ] HTTPS — 이 스택은 HTTP 전용. 운영 시 외부 LB/Ingress 로 TLS termination.
- [ ] GitLab PAT 만료 — 364일 후 자동 만료되므로 재발급 자동화 검토 (`provision.sh` 의 `gitlab_issue_root_pat()` 함수 참고).

---

## 15. 더 알아보기

- Dify workflow DSL 수정 → [`scripts/dify-assets/sonar-analyzer-workflow.yaml`](scripts/dify-assets/sonar-analyzer-workflow.yaml) 편집 → 이미지 재빌드 (또는 runtime 에 Dify Studio 에서 수정).
- severity 라우팅 변경 → [`pipeline-scripts/sonar_issue_exporter.py`](pipeline-scripts/sonar_issue_exporter.py) 의 `_SEVERITY_ROUTING` 딕셔너리.
- 새 언어 추가 → [`pipeline-scripts/repo_context_builder.py`](pipeline-scripts/repo_context_builder.py) 의 `LANG_CONFIG` 에 tree-sitter grammar 추가 + `contextual_enricher.py` 도 대응.

**이슈·PR 환영** 합니다. 개선 아이디어: 
- GitLab 프로젝트 자동 생성 provisioning (현재는 수동)
- Dify 1.14+ 업그레이드 시 workflow YAML 스키마 점검
- 영향 분석 LLM 프롬프트 A/B 테스트
