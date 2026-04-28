# 코드 RAG & Spec↔Code 추적성 — 수행 계획 (Execution Playbook)

> **이 문서가 무엇인가**
> [PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md](PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md) 가 *무엇을 짓는가*
> (설계서) 라면, 본 문서는 *어떻게 굴리는가* (런북) 다. 매일 무엇을 하는지, 어디서 멈출지, 어떻게
> 테스트하고 합/불을 판정할지를 못 박는다.
>
> **PLAN 과 EXECUTION 의 분리** — PLAN 은 결정 로그 (왜, 무엇), EXECUTION 은 작업 캘린더 (언제, 어떻게).
> 새로운 기술 결정은 PLAN §11 에, 진행 상황·테스트 결과·블로커는 본 문서 §10 변경 이력에 적는다.

## 한 페이지 요약 (TL;DR)

- **범위** — Phase 0~7 전체 (1차 목표 RAG + 최종 목표 Spec↔Code 추적성).
- **머신 우선순위** — **M4 Pro 먼저** 검증 끝낸 뒤 RTX 4070 으로 이식.
- **테스트 데이터** — `realworld` (baseline / 회귀 비교용) + `spring-petclinic-rest` (sanity / Phase 7
  ground truth 용).
- **총 일정** — 1차 목표 (Phase 0~5) **3~4 주**, 최종 목표 (Phase 7) **약 3 개월**.
- **GO/NO-GO 게이트** — 각 Phase 종료 시 정량 기준으로 합/불 판정 — 미달 시 다음 Phase 진입 금지.
- **롤백** — 모든 변경은 try/except + 별도 supervisor program → 새 컴포넌트만 stop 하면 기존 흐름 복귀.
- **다운로드 합계** — Meilisearch + FalkorDB + bge-reranker + Ollama 모델 + Python wheels = **약
  17~18 GB** (외부망에서 사전 다운로드, 폐쇄망 반입 대상). 절차는 §3 참조.

---

## 목차

1. [범위 & 가정](#1-범위--가정)
2. [Day-0 사전 점검](#2-day-0-사전-점검-반나절)
3. [다운로드 & 설치 (외부망 빌드 머신)](#3-다운로드--설치-외부망-빌드-머신)
4. [테스트 데이터 준비](#4-테스트-데이터-준비-day-0-1)
5. [Phase 별 일자 캘린더](#5-phase-별-일자-캘린더)
6. [테스트 계획 — 4 레이어](#6-테스트-계획--4-레이어)
7. [GO / NO-GO 게이트](#7-go--no-go-게이트)
8. [리스크 레지스터](#8-리스크-레지스터)
9. [운영 시나리오 (정착 후)](#9-운영-시나리오-정착-후)
10. [변경 이력](#10-변경-이력)

---

## 1. 범위 & 가정

### 1.1 범위

| 구분 | 포함 | 제외 |
|---|---|---|
| Phase | 0 ~ 7 (전체) | 없음 |
| 머신 | M4 Pro 48GB (1순위), RTX 4070 8GB (2순위 이식) | 클라우드 |
| 데이터 | realworld + spring-petclinic-rest | 고객사 실데이터 |
| 모델 | gemma4:e4b, qwen3-embedding:0.6b, bge-reranker-v2-m3 | 외부 API |
| 통합 | ttc-allinone 단일 이미지 + supervisor + tar 배포 | 별도 docker compose 분리 |

### 1.2 핵심 가정

- **운영 모델 보존** — 폐쇄망 단일 이미지 + tar 배포 패턴은 절대 깨지 않는다 (PLAN §6.8 보강분).
- **SI NDA** — L2b cross-project KB 영구 제외, L2c 는 고객사-무관 자료만 (PLAN §6.4 / §11 2026-04-27).
- **양 머신 동일 구성** — LLM·임베더·리랭커 모두 동일, `OLLAMA_NUM_PARALLEL` 만 차등.
- **외부망 빌드 가능** — Day-0 점검에서 도달성 검증 후 외부망에서 단일 이미지 빌드.

### 1.3 작업 환경

- **외부망 머신** — 빌드 + tar export 전용. 인터넷 도달.
- **폐쇄망 머신** — tar 반입 → docker load → supervisor 기동. M4 Pro 가 1차 검증 머신.
- **브랜치** — `feat/code-rag-hybrid-retrieve` (현재). Phase 별 PR 분리.

### 1.4 산출물 분류

| 분류 | 위치 | 예시 |
|---|---|---|
| 코드 | `code-AI-quality-allinone/` | retrieve-svc/, sinks/, Dockerfile 보강 |
| 문서 | `code-AI-quality-allinone/docs/` | PLAN, EXECUTION (본 문서), SPEC_TRACEABILITY |
| 테스트 데이터 | `code-AI-quality-allinone/test-fixtures/` | sample-repos/, sample-prds/, golden/ |
| 측정 리포트 | `code-AI-quality-allinone/measurements/` | phase5-ab/, phase7-precision/ |

---

## 2. Day-0 사전 점검 (반나절)

> **목적** — 본격 구현 시작 전 *환경 자체* 가 OK 임을 못 박는다. 점검 누락 시 Phase 0 중간에서
> 수일을 잃는 패턴을 방지.

### 2.1 하드웨어·런타임 점검

PLAN §6.12 의 즉시 점검 체크리스트를 실행하고, 결과를 표로 기록.

```bash
# Docker
docker --version                         # 24.x+
docker info | grep -i memory             # 메모리 limit
docker system df                         # 디스크 여유

# Ollama
ollama --version                         # 0.4.x+
ollama list                              # 기존 모델 목록

# 디스크 — 단일 이미지가 ~11GB, ollama-models ~5GB → 최소 30GB 여유
df -h ~/Developer ~/.ollama

# 메모리 (M4 Pro)
top -l 1 | head -10                      # 사용 가능 메모리
```

**기록 파일**: `measurements/day0-precheck-m4pro.md`

| 항목 | 기준 | 측정값 | 합/불 |
|---|---|---|---|
| Docker 메모리 limit | ≥ 32GB | _ | _ |
| 디스크 여유 (~/Developer) | ≥ 30GB | _ | _ |
| 디스크 여유 (~/.ollama) | ≥ 10GB | _ | _ |
| Ollama 동작 | `ollama list` 응답 | _ | _ |

### 2.2 외부망 도달성 검증

PLAN P0.1 의 도달성 명령 일괄 실행 + 결과 기록.

```bash
# Meilisearch v1.42 binary
curl -fIs https://github.com/meilisearch/meilisearch/releases/download/v1.42/meilisearch-linux-amd64

# FalkorDB Docker image (multi-stage 차용)
docker pull falkordb/falkordb:latest

# bge-reranker-v2-m3 weight (~1.1GB)
huggingface-cli download BAAI/bge-reranker-v2-m3 --local-dir /tmp/test-rerank-probe

# Ollama 모델
ollama pull gemma4:e4b-it-q4_K_M
ollama pull qwen3-embedding:0.6b
```

**4 개 모두 OK 일 때만** Day 1 (Phase 0) 착수.

### 2.3 작업 디렉터리 정렬

```bash
cd /Users/luuuuunatic/Developer/airgap-test-toolchain
git fetch origin
git checkout feat/code-rag-hybrid-retrieve
git status                               # working tree clean 확인

# 작업용 하위 디렉터리 (이미 일부 존재)
mkdir -p code-AI-quality-allinone/{retrieve-svc,test-fixtures/sample-repos,
  test-fixtures/sample-prds,test-fixtures/golden,measurements}
```

### 2.4 Day-0 종료 기준

- §2.1 표 4 항목 모두 합.
- §2.2 4 개 도달성 모두 OK.
- 문서 `measurements/day0-precheck-m4pro.md` 작성·커밋.
- **NO-GO 시 대응** — Docker 메모리 부족이면 Docker Desktop 설정 → Resources → Memory 32GB+. HF 토큰
  401 이면 `huggingface-cli login` 후 재시도.

---

## 3. 다운로드 & 설치 (외부망 빌드 머신)

> **목적** — 폐쇄망 단일 이미지 빌드에 필요한 *모든* 외부 의존성 (binary / 모델 / wheel / 이미지) 을
> 외부망 머신에서 사전 다운로드. 이미지 빌드 시점엔 인터넷 도달 0.
>
> **소요 시간** — 다운로드 자체는 1~2시간 (네트워크 속도 의존), 설치·검증 합쳐 **0.5~1일**.
> **총 다운로드 용량** — 약 **18~22 GB** (모델 + binary + wheels + base 이미지).

### 3.1 작업 머신 요건

| 항목 | 기준 | 비고 |
|---|---|---|
| OS | macOS (M-series) 또는 Linux x86_64 | M4 Pro 본인 머신에서 직접 빌드 권장 |
| 인터넷 | HF / GitHub / Docker Hub / Ollama 도달 | 사내 프록시 시 환경변수 (`HTTPS_PROXY`) |
| 디스크 여유 | ≥ **50 GB** | 다운로드 22GB + 빌드 중간 산출물 + tar export |
| Docker | 24.x+ | `docker version` 확인 |
| Python | 3.11+ | retrieve-svc requirements 빌드용 |
| Git | 2.x | sample 레포 clone |

### 3.2 사전 도구 설치 (한 번만)

#### 3.2.1 Ollama (host 측 LLM 런타임)

```bash
# macOS
brew install ollama
ollama --version              # ≥ 0.4.x

# 백그라운드 실행
ollama serve &
```

#### 3.2.2 huggingface-cli (모델 weight 다운로드)

```bash
python3 -m pip install --user "huggingface_hub[cli]"
huggingface-cli --version

# (선택) HF 토큰 — gated 모델 또는 rate limit 회피
huggingface-cli login        # ~/.cache/huggingface/token 저장
```

#### 3.2.3 docker / git / curl — 시스템 표준 (보통 이미 있음)

```bash
docker --version
git --version
curl --version
```

### 3.3 디렉터리 레이아웃 (offline-assets/)

빌드 컨텍스트 안에서 통일된 위치에 배치 — Dockerfile 의 `COPY` 가 이 경로를 참조한다.

**원칙** — 각 머신은 *자기 native arch* 의 산출물만 보유한다 (M4 Pro = arm64, WSL2 = amd64).
별도 빌드 스크립트가 두 머신에서 따로 돌아 두 개의 tar 가 나온다. 멀티-아키 manifest 미운영.

```text
code-AI-quality-allinone/
├── offline-assets/
│   ├── arm64/                          # 기존 — ttc-allinone tar (M4 Pro)
│   │   └── ttc-allinone-arm64-dev.tar.gz   (~9~11 GB, Phase 0 산출)
│   ├── amd64/                          # 기존 — ttc-allinone tar (WSL2/Intel)
│   ├── ollama-models/                  # 신규 — ollama 모델 (host 복원용, 이미지 안 X)
│   │   ├── blobs/
│   │   └── manifests/
│   ├── rerank-models/                  # 신규 — bge-reranker (이미지 안 COPY)
│   │   └── bge-reranker-v2-m3/
│   ├── meilisearch/                    # 신규 — Meilisearch binary (이미지 안 COPY)
│   │   └── meilisearch-linux-arm64     #   (M4 Pro 의 경우 — WSL2 면 -amd64)
│   └── python-wheels/                  # 신규 — retrieve-svc pip 의존성 (이미지 안 COPY)
│       └── (호스트 arch 에 맞는 wheel 30~50 개)
# FalkorDB 는 docker pull 결과가 로컬 daemon 에 잔존 → 별도 파일 X (multi-stage 자동 차용)
```

**용량 예상 표** (외부망 다운로드 후 합계):

| 항목 | 크기 | 출처 |
|---|---|---|
| Ollama 모델 (gemma4:e4b + qwen3-embedding:0.6b) | ~ 5 GB | ollama pull |
| bge-reranker-v2-m3 weight | ~ 1.1 GB | huggingface-cli download |
| Meilisearch binary (단일 arch) | ~ 100 MB | GitHub releases |
| Python wheels (sentence-transformers + torch CPU + 외) | ~ 2.5 GB | pip download |
| FalkorDB Docker 이미지 (multi-stage source) | ~ 350 MB | docker pull |
| 베이스 이미지 (gitlab / dify / sonarqube — 기존 절차) | ~ 8 GB | docker pull (download-plugins.sh) |
| **합계 (오프라인 반입 대상)** | **~ 17~18 GB** | (Docker 이미지는 컨테이너 daemon 에 잔존, tar 만 USB) |
| 최종 단일 이미지 tar (build 결과) | ~ 9~11 GB | `docker save` + gzip |

### 3.4 다운로드 항목별 절차

#### 3.4.1 Meilisearch v1.42 binary

각 머신은 **자기 native arch 만** 받는다 (M4 Pro = arm64, WSL2 = amd64). `docker build` 가 native
빌드 한 번이고 두 머신은 별개 빌드.

```bash
cd code-AI-quality-allinone/
mkdir -p offline-assets/meilisearch

# M4 Pro 인 경우만:
curl -fL -o offline-assets/meilisearch/meilisearch-linux-arm64 \
  https://github.com/meilisearch/meilisearch/releases/download/v1.42/meilisearch-linux-aarch64

# WSL2 / Intel 인 경우만:
# curl -fL -o offline-assets/meilisearch/meilisearch-linux-amd64 \
#   https://github.com/meilisearch/meilisearch/releases/download/v1.42/meilisearch-linux-amd64

chmod +x offline-assets/meilisearch/meilisearch-linux-*
file offline-assets/meilisearch/meilisearch-linux-arm64    # ELF 64-bit LSB executable, ARM aarch64
```

**Dockerfile 패턴** — glob 으로 단일 파일 매칭 (각 머신엔 자기 arch 한 개만 존재):

```dockerfile
COPY offline-assets/meilisearch/meilisearch-linux-* /usr/local/bin/meilisearch
RUN chmod +x /usr/local/bin/meilisearch
```

**검증** — `--help` 가 컨테이너 내부에서 호출 가능 (이미지 빌드 후).

#### 3.4.2 FalkorDB — multi-stage source

GitHub releases 가 Debian glibc binary 를 제공하지 않아 **공식 Docker 이미지에서 .so 추출** (PLAN
§11 2026-04-28 검증). 각 머신에서 native arch 만 pull (`docker pull` 은 기본적으로 호스트 arch).

```bash
# 머신 native arch 그대로 pull
docker pull falkordb/falkordb:latest

# 추출 위치는 컨테이너 안 — Dockerfile 이 multi-stage 로 차용:
#   FROM falkordb/falkordb:latest AS falkordb-src
#   COPY --from=falkordb-src /var/lib/falkordb/bin/falkordb.so /usr/local/lib/falkordb.so
# 별도 파일로 export 불필요 (이미지가 로컬 daemon 에 있으면 빌드 시 자동 차용).
```

**검증** — `docker inspect falkordb/falkordb:latest --format '{{.Architecture}}'` 가 호스트 arch 와
일치 (M4 Pro = arm64, WSL2 = amd64).

#### 3.4.3 bge-reranker-v2-m3 weight (HuggingFace)

```bash
mkdir -p offline-assets/rerank-models
huggingface-cli download BAAI/bge-reranker-v2-m3 \
  --local-dir offline-assets/rerank-models/bge-reranker-v2-m3 \
  --local-dir-use-symlinks=False     # 심볼릭 링크 X — Docker COPY 호환
```

**검증** —

```bash
ls offline-assets/rerank-models/bge-reranker-v2-m3/
# 확인: model.safetensors, tokenizer.json, config.json, special_tokens_map.json 존재
du -sh offline-assets/rerank-models/bge-reranker-v2-m3/
# ≈ 1.1 GB
```

#### 3.4.4 Ollama 모델 export (host 복원용)

이미지 안에 들어가지 않고, **폐쇄망 host 의 `~/.ollama` 에 복원**. 단일 이미지 패턴 보존 — Ollama 는
host 프로세스 (PLAN §6.4 결정).

```bash
# 외부망 host 에서 pull
ollama pull gemma4:e4b-it-q4_K_M           # ~ 4.2 GB
ollama pull qwen3-embedding:0.6b           # ~ 0.6 GB

# (옵션 — M4 Pro Phase 7 야간 batch) 
ollama pull gemma4:26b-it-q4_K_M           # ~ 16 GB

# export
mkdir -p offline-assets/ollama-models
cp -r ~/.ollama/models/blobs offline-assets/ollama-models/
cp -r ~/.ollama/models/manifests offline-assets/ollama-models/

du -sh offline-assets/ollama-models/
# ≈ 5 GB (e4b + qwen3-embedding)
# ≈ 21 GB (+ 26b 옵션)
```

**검증** — `manifests/registry.ollama.ai/library/gemma4/e4b-it-q4_K_M` 등의 매니페스트 파일 존재.

#### 3.4.5 Python wheels (retrieve-svc 의존성)

폐쇄망에서 `pip install --no-index` 가능하도록 모든 의존성 wheel 사전 다운로드.

```bash
cd code-AI-quality-allinone/retrieve-svc/
cat requirements.txt
# fastapi==0.115.x
# uvicorn[standard]==0.32.x
# httpx==0.27.x
# redis==5.x          # FalkorDB 호환 redis-py
# qdrant-client==1.12.x
# meilisearch==0.31.x
# sentence-transformers==3.3.x
# torch==2.5.x        # CPU 휠 (--index-url https://download.pytorch.org/whl/cpu)
# pydantic==2.x

# 호스트 native arch 만 다운로드. M4 Pro 인 경우 (arm64):
mkdir -p ../offline-assets/python-wheels
pip download \
  -r requirements.txt \
  --platform manylinux2014_aarch64 \
  --python-version 311 \
  --only-binary=:all: \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  -d ../offline-assets/python-wheels

# WSL2 / Intel 인 경우만 (amd64) — `--platform manylinux2014_x86_64` 로 변경.
```

**검증** —

```bash
ls offline-assets/python-wheels/ | wc -l      # 30~50 개
du -sh offline-assets/python-wheels/          # ≈ 2.5 GB

# Dockerfile 의 retrieve-svc venv 빌드 단계가:
#   pip install --no-index --find-links=/opt/python-wheels -r requirements.txt
# 로 동작.
```

> **주의** — `torch` 는 CPU 전용 wheel (~700MB). GPU wheel 받으면 +2GB · 컨테이너 안 미사용.
> `--extra-index-url https://download.pytorch.org/whl/cpu` 를 *반드시* 지정.

#### 3.4.6 (기존 절차) 베이스 이미지 + Dify 플러그인

이미 존재하는 [scripts/download-plugins.sh](../scripts/download-plugins.sh) 가 Dify / SonarQube /
GitLab / dify-sandbox 의 베이스 이미지·플러그인을 처리한다.

```bash
bash scripts/download-plugins.sh
# 산출: scripts/dify-assets/, scripts/patches/ 등 — 빌드 컨텍스트에 이미 자동 통합
```

**검증** — `scripts/dify-assets/plugins/` 안에 `.difypkg` 파일들이 존재.

#### 3.4.7 Sample 레포 clone (테스트 데이터)

§4 와 중복이지만 *Phase 0 까지의 사전 준비물* 로도 미리 받아둠 (D2~D3 빌드 검증에서 활용).

```bash
mkdir -p code-AI-quality-allinone/test-fixtures/sample-repos
cd code-AI-quality-allinone/test-fixtures/sample-repos

git clone --depth=1 https://github.com/gothinkster/spring-boot-realworld-example-app realworld-spring
git clone --depth=1 -b v4.0.2 https://github.com/spring-petclinic/spring-petclinic-rest petclinic-rest
```

> `--depth=1` 으로 git history 제외 — 디스크 절약 (~50MB → ~5MB).

### 3.5 다운로드 일괄 스크립트

위 절차를 한 번에 실행하는 헬퍼. 누락 항목 자동 감지 + 재실행 시 idempotent.

**위치**: `code-AI-quality-allinone/scripts/download-rag-bundle.sh` (신규 작성 예정 — Phase 0 P0.2~P0.5
에서 만든다).

```bash
#!/usr/bin/env bash
set -euo pipefail

ALLINONE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ALLINONE_DIR"

# 호스트 native arch 감지 — 머신마다 자기 arch 만 받음
case "$(uname -m)" in
  arm64|aarch64) ARCH=arm64; MEILI_TAG=aarch64; PIP_PLAT=manylinux2014_aarch64 ;;
  x86_64)        ARCH=amd64; MEILI_TAG=amd64;   PIP_PLAT=manylinux2014_x86_64  ;;
  *) echo "unsupported arch: $(uname -m)" >&2; exit 2 ;;
esac

# 1. Meilisearch (단일 arch)
mkdir -p offline-assets/meilisearch
[ -s "offline-assets/meilisearch/meilisearch-linux-${ARCH}" ] || \
  curl -fL -o "offline-assets/meilisearch/meilisearch-linux-${ARCH}" \
    "https://github.com/meilisearch/meilisearch/releases/download/v1.42/meilisearch-linux-${MEILI_TAG}"
chmod +x offline-assets/meilisearch/meilisearch-linux-*

# 2. FalkorDB Docker image (로컬 daemon — 호스트 arch 자동)
docker image inspect falkordb/falkordb:latest >/dev/null 2>&1 || \
  docker pull falkordb/falkordb:latest

# 3. bge-reranker
[ -s offline-assets/rerank-models/bge-reranker-v2-m3/model.safetensors ] || \
  huggingface-cli download BAAI/bge-reranker-v2-m3 \
    --local-dir offline-assets/rerank-models/bge-reranker-v2-m3 \
    --local-dir-use-symlinks=False

# 4. Ollama 모델 (host 측)
ollama list | grep -q gemma4:e4b-it-q4_K_M || ollama pull gemma4:e4b-it-q4_K_M
ollama list | grep -q qwen3-embedding:0.6b || ollama pull qwen3-embedding:0.6b

# export (idempotent — rsync 변경분만 갱신)
mkdir -p offline-assets/ollama-models
rsync -a ~/.ollama/models/ offline-assets/ollama-models/

# 5. Python wheels (호스트 arch)
mkdir -p offline-assets/python-wheels
[ "$(ls offline-assets/python-wheels/ 2>/dev/null | wc -l)" -gt 20 ] || \
  pip download \
    -r retrieve-svc/requirements.txt \
    --platform "$PIP_PLAT" --python-version 311 --only-binary=:all: \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -d offline-assets/python-wheels

echo "[download-rag-bundle] OK (arch=$ARCH)"
du -sh offline-assets/{meilisearch,rerank-models,ollama-models,python-wheels}/
```

### 3.6 검증 체크리스트

다운로드 완료 후 일괄 점검:

```bash
bash scripts/verify-rag-bundle.sh    # (신규 작성 예정)
```

검증 항목 (스크립트가 자동 검사):

| # | 항목 | 합 기준 |
|---|---|---|
| 1 | Meilisearch binary 존재 + 실행 가능 | `file` 출력에 ELF executable + chmod +x |
| 2 | FalkorDB 이미지 로컬 daemon | `docker image inspect falkordb/falkordb:latest` 성공 |
| 3 | bge-reranker weight | `model.safetensors`, `config.json`, `tokenizer.json` 모두 존재 + 합계 ≥ 1GB |
| 4 | Ollama 모델 manifests | `manifests/registry.ollama.ai/library/{gemma4,qwen3-embedding}/` 존재 |
| 5 | Python wheels 개수 | `offline-assets/python-wheels/` 안 wheel ≥ 25 개 (호스트 arch) |
| 6 | torch CPU wheel | `torch-*-cp311-*.whl` 의 파일명에 `+cu` 없음 (GPU 빌드 아님) |
| 7 | sample 레포 clone | `petclinic-rest/pom.xml` 존재 (realworld 는 기존 `sample-projects/realworld/` 재사용) |

7 항목 모두 OK 일 때 Phase 0 P0.6 (단일 이미지 빌드) 진입.

### 3.7 폐쇄망 반입 패키징

빌드 결과물 (단일 이미지 tar) + Ollama 모델만 폐쇄망으로 가져간다. 그 외는 외부망 머신에서 *빌드용*
으로만 쓰이고 반입 불필요.

#### 3.7.1 반입 대상 (USB / 사내 파일전송)

```text
offline-assets/
├── arm64/ttc-allinone-arm64-dev.tar.gz   ~ 9~11 GB   ← 단일 이미지
├── arm64/yrzr-gitlab-ce-arm64v8-*.tar.gz ~ 2 GB      ← GitLab (기존)
├── arm64/dify-sandbox-*.tar.gz           ~ 0.5 GB    ← Sandbox (기존)
└── ollama-models/                         ~ 5 GB     ← gemma4 + qwen3-embedding
                                          (~ 21 GB if 26b 옵션 포함)

총합:  ~ 17~18 GB (M4 Pro 기본)
       ~ 33 GB    (M4 Pro 26b 옵션 포함)
```

#### 3.7.2 USB 16GB 한계 시 분할

```bash
# 분할 — 4GB 단위
cd offline-assets/arm64/
split -b 4G ttc-allinone-arm64-dev.tar.gz ttc-allinone-arm64-dev.tar.gz.part-

# 폐쇄망 머신 측 재조립
cat ttc-allinone-arm64-dev.tar.gz.part-* > ttc-allinone-arm64-dev.tar.gz
sha256sum ttc-allinone-arm64-dev.tar.gz   # 외부망 측 hash 와 일치 확인
```

#### 3.7.3 폐쇄망 측 적용 절차

```bash
# (1) 단일 이미지 load — 기존 [scripts/offline-load.sh](../scripts/offline-load.sh) 그대로
bash scripts/offline-load.sh --arch arm64

# (2) Ollama 모델 복원 (host 측)
mkdir -p ~/.ollama
rsync -a /media/usb/offline-assets/ollama-models/ ~/.ollama/models/
ollama list   # gemma4:e4b-it-q4_K_M, qwen3-embedding:0.6b 확인

# (3) Ollama 환경변수 (M4 Pro)
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=3
ollama serve &

# (4) 컨테이너 기동
bash scripts/run-mac.sh
```

### 3.8 Day 종료 기준 (다운로드 완료)

- §3.6 검증 7 항목 모두 OK.
- `du -sh offline-assets/` 합계가 §3.3 표 예상치 ±20% 이내.
- 검증 결과를 `measurements/day1-bundle.md` 에 기록 (각 항목 크기·해시).
- **NO-GO 시 대응** — 네트워크 timeout 이면 재시도. HF 401 이면 토큰 등록. pip wheel 빌드 실패면
  `--no-build-isolation` 또는 sentence-transformers 버전 고정.

---

## 4. 테스트 데이터 준비 (Day 0~1)

> **3 종 데이터** — baseline 회귀용 / sanity 동작용 / Phase 7 ground truth 용. 각각 역할이 다름.

### 4.1 baseline 레포 — `realworld` (Spring Boot)

| 항목 | 값 |
|---|---|
| 출처 | gothinkster/spring-boot-realworld-example-app |
| 규모 | ~25 controller, ~30 service, ~30 entity |
| 청크 (예상) | ~400~600 (2 단계 빌드 후 측정) |
| 역할 | Phase 5 A/B 회귀 비교 (기존 단일 dense vs hybrid) baseline |
| Phase 7 활용 | △ — PRD 가 README 수준이라 atomic req 추출은 부정확 |

**준비**:

```bash
git clone https://github.com/gothinkster/spring-boot-realworld-example-app \
  code-AI-quality-allinone/test-fixtures/sample-repos/realworld-spring
```

### 4.2 sanity 레포 — `spring-petclinic-rest`

| 항목 | 값 |
|---|---|
| 출처 | spring-petclinic/spring-petclinic-rest |
| 라이선스 | Apache 2.0 |
| 최신 | v4.0.2 (2026-02) |
| 규모 | REST controller 7 개, ~40+ 엔드포인트 (Owner / Pet / Vet / Visit / Specialty / PetType / User) |
| 청크 (예상) | ~150~250 |
| 역할 | Phase 1~3 sanity (작은 레포로 끝-끝 동작 확인), Phase 7 ground truth |

**선정 근거**:
- **REST API 가 명확** — 엔드포인트 = atomic requirement 1:1 매핑이 가능. Phase 7 PRD 추적성 정답지
  수동 작성이 현실적 (10~15 req).
- **Spring Boot** — Korean SI 의 가장 흔한 스택. realworld 와 비교군.
- **Apache 2.0** — 라이선스 제약 없음.
- **활성 유지** — 2026-02 릴리스로 의존성 최신.

**준비**:

```bash
git clone https://github.com/spring-petclinic/spring-petclinic-rest \
  code-AI-quality-allinone/test-fixtures/sample-repos/petclinic-rest
```

### 4.3 Phase 5 A/B 측정용 이슈 셋

`realworld` + `petclinic-rest` 두 레포에 SonarQube 04 분석 1 회 실행 → rule violation 100+ 개 수집 →
이 중 **샘플 50 개** 를 `phase5-issue-set.json` 으로 동결 (재현 가능 측정).

```text
test-fixtures/golden/
└── phase5-issue-set.json     # 50 issues × {repo, file, line, rule, severity}
```

| 측정 항목 | 정의 |
|---|---|
| citation rate | 50 이슈 중 LLM 응답에 retrieve-svc 청크가 인용된 비율 |
| layer 분포 | dense / sparse / graph 인용 비율 |
| partial_citation | 부분 인용 (cited_symbols ≥ 1 but score < 0.5) 비율 |

### 4.4 Phase 7 PRD ground truth (수동 작성)

`petclinic-rest` 의 README + REST 엔드포인트 표 → **사람이 작성** 한 atomic requirement 12 개와 정답
매핑.

```text
test-fixtures/sample-prds/
└── petclinic-rest-prd.md     # 12 atomic requirements (REQ-001 ~ REQ-012)

test-fixtures/golden/
└── petclinic-rest-mapping.json     # {req_id → expected verdict + cited symbols}
```

**예시 (1 개)**:
```yaml
- id: REQ-001
  text: "관리자가 신규 Owner 를 등록할 수 있어야 한다 (이름·주소·전화번호 필수)."
  category: owner
  acceptance_criteria:
    - "POST /owners 엔드포인트 존재"
    - "필수 필드 누락 시 400 반환"
  expected_verdict: implemented
  expected_symbols:
    - "OwnerRestController.addOwner"
    - "Owner.firstName / lastName / address / telephone"
```

12 req 작성에 약 0.5일 소요. **이게 빠지면 Phase 7 정확도 측정 불가** — 정답지 없는 LLM-as-Judge 는
믿을 수 없다.

### 4.5 Day 1 종료 기준

- 두 sample-repos clone 완료.
- `phase5-issue-set.json` 50 항목 동결 (Phase 5 직전까지 재사용).
- `petclinic-rest-prd.md` 12 atomic requirement 작성.
- `petclinic-rest-mapping.json` 12 정답 매핑 작성.

---

## 5. Phase 별 일자 캘린더

> **전제** — 1 인 풀타임 기준. M4 Pro 1대에서 실행. 외부망 빌드 작업은 인터넷 가능 시점에 합쳐서.

### 5.1 1차 목표 일정 (Phase 0~5) — 약 3~4 주

| Day | Phase | 작업 |
|---|---|---|
| **D0** | 사전 점검 | §2 Day-0 점검 + §3 다운로드·설치 시작 + §4 테스트 데이터 준비 |
| D1 | P0.1~P0.2 | 외부망 도달성 + bge-reranker weight 다운로드 |
| D2 | P0.3 | Ollama 모델 export → `offline-assets/ollama-models/` |
| D3 | P0.4 (병렬 시작) | retrieve-svc 디렉터리 스켈레톤 + requirements.txt |
| D4 | P0.5 | Dockerfile + supervisord.conf 보강 |
| D5 | P0.6 | 단일 이미지 빌드 + 외부망 1 회 기동 헬스체크 |
| **D6** | **GO/NO-GO 1** | §7.1 Phase 0 게이트 — 16 process RUNNING 확인 |
| D7 | P1 | (M4 Pro 폐쇄망) tar 반입 + docker load + 헬스체크 |
| D8~D10 | P2.1~P2.3 | retrieve-svc 본체 (fusion, backends, schema) |
| D11 | P2.4 | 단위 테스트 (외부망 머신) |
| D12 | P2.5 | 이미지 재빌드 + 컨테이너 헬스체크 |
| **D13** | **GO/NO-GO 2** | §7.2 Phase 2 게이트 — `/retrieval` 200 + valid schema |
| D14 | P3.1~P3.2 | meili_sink 구현 |
| D15 | P3.3 | falkor_sink 구현 (tree-sitter callees → CALLS edge) |
| D16 | P3.4 | repo_context_builder 통합 + Jenkinsfile environment |
| D17 | P3.5 | petclinic-rest 로 02 빌드 1 회 → Meili / Falkor 수치 검증 |
| **D18** | **GO/NO-GO 3** | §7.3 Phase 3 게이트 — 청크 수 ≈ 적재 수 |
| D19 | P4.1~P4.2 | Dify Studio External KB 등록 + 04 Workflow datasource 교체 |
| D20 | P4.3 | E2E 1 사이클 (01→02→03→04) on petclinic-rest |
| **D21** | **GO/NO-GO 4** | §7.4 Phase 4 게이트 — source_layer 3 축 모두 0+ |
| D22~D26 | P5.1~P5.2 | layer-별 citation rate 도입 + RRF 가중치 1차 튜닝 |
| D27~D30 | P5.3 | A/B 회귀 비교 (realworld + petclinic-rest, 50 이슈 셋) |
| **D30** | **GO/NO-GO 5** | §7.5 Phase 5 게이트 — citation rate +5pp 이상 |

**누적 영업일** — Phase 0~5 = 30 영업일 ≈ 6 주 (인터럽트 고려). 단순 합산 22 일은 *최단* 시나리오.

### 5.2 최종 목표 일정 (Phase 6~7) — Phase 5 종료 후

| Week | Phase | 작업 |
|---|---|---|
| W7 | P6.1~P6.2 | L2c canonical 큐레이션 (Spring docs / OWASP / CWE 발췌) + 별도 dataset |
| W8 | P6.3~P6.5 | layer mix 활성화 + confidence calibration + 측정 |
| W9 | P7.1 | spec_ingest.py — PRD → atomic requirement (LLM 보조) |
| W10 | P7.2 | bidirectional index + retrieve-svc 호출 통합 |
| W11 | P7.3~P7.4 | LLM-as-Judge (3-state) + self-verification (e4b ×2) |
| W12 | P7.5 | Implementation Rate 리포트 (Jenkins publishHTML) |
| W13~W14 | P7.6 | petclinic-rest 12 req ground truth 비교 → precision/recall 측정 → 임계 튜닝 |
| W15 | RTX 이식 | M4 Pro 검증 끝난 단일 이미지 → RTX 머신 tar 반입 + `OLLAMA_NUM_PARALLEL=1` |

**최종 목표 완료** — D0 부터 약 **15 주 (3.5 개월)**.

### 5.3 병렬화 가능 구간

```text
D3 (retrieve-svc 코드 작성) ─────┐
                                ▼
D8~D12 (retrieve-svc 본체)      │  병렬 가능 — 외부망 머신에서 코드 작성하는
D14~D17 (sink 구현)              │  동시에 다른 작업자/시간대에 sink 구현
                                ▼
D19~ (Dify Workflow 전환)
```

1 인 작업이라 *시간 분할* 로 활용. retrieve-svc 코드 작성을 앞당겨두면 Phase 2 가 D3~D12 → D3~D9 로
단축 가능.

---

## 6. 테스트 계획 — 4 레이어

> **레이어 분리 원칙** — 단위 → 통합 → E2E → 회귀. 각 레이어가 *어디서 깨지면 어떤 책임* 인지 명확히.

### 6.1 단위 테스트 (Unit) — retrieve-svc / sinks

**대상**: 순수 함수 / 외부 의존성 없는 로직.

| 모듈 | 테스트 파일 | 검증 |
|---|---|---|
| `retrieve-svc/app/fusion.py` | `tests/test_fusion.py` | RRF 결합 정확성 (수동 hits 3 셋 → 기대 순위) |
| `retrieve-svc/app/schema.py` | `tests/test_schema.py` | Dify 응답 pydantic — `metadata` 빈 dict 직렬화 |
| `retrieve-svc/app/backends/qdrant.py` | `tests/test_qdrant.py` | mock client 로 search 파라미터 전달 |
| `pipeline-scripts/sinks/meili_sink.py` | `tests/test_meili_sink.py` | chunks → docs 정규화 (id 충돌 없음) |
| `pipeline-scripts/sinks/falkor_sink.py` | `tests/test_falkor_sink.py` | callees → CALLS Cypher 생성 |

**실행**:

```bash
cd code-AI-quality-allinone/retrieve-svc
.venv/bin/pytest tests/ -v --cov=app --cov-report=term

cd ../pipeline-scripts
.venv/bin/pytest tests/sinks/ -v
```

**합/불 기준** — 100% pass + fusion / schema 모듈 커버리지 ≥ 80%.

### 6.2 통합 테스트 (Integration) — 컨테이너 안

**대상**: retrieve-svc 가 실제 Qdrant / Meili / Falkor 와 통신.

```bash
# 컨테이너 안에서 실행 (테스트 데이터 포함된 dataset 사용)
docker exec ttc-allinone bash -c '
  cd /opt/retrieve-svc
  .venv/bin/pytest tests/integration/ -v
'
```

**시나리오**:

| 시나리오 | 절차 | 기대 |
|---|---|---|
| dense only | qdrant 만 hit, meili / falkor 0 결과 | 응답 records ≥ 1, source_layer="dense" |
| sparse only | meili 만 hit | source_layer="sparse" |
| graph only | falkor 1-hop callers 만 hit | source_layer="graph" |
| 3 축 모두 hit | RRF + rerank 수행 | 상위 5 개에 3 layer 모두 등장 |
| 빈 인덱스 | 모든 backend 0 결과 | records 빈 배열, 200 OK |
| backend 1 개 down | 예: meili stop 후 호출 | dense + graph 만으로 응답 |

**합/불** — 6 시나리오 모두 통과.

### 6.3 E2E 테스트 — 01 → 02 → 03 → 04 한 사이클

**대상**: petclinic-rest 1 개 레포에 대한 정적분석 끝-끝.

```bash
# Jenkins UI 또는 jenkins-cli
jenkins-cli build "01-코드-분석-체인" \
  -p REPO_URL=file:///var/test-fixtures/sample-repos/petclinic-rest \
  -p BRANCH=main
```

**검증 포인트**:

| Stage | 검증 |
|---|---|
| 02 종료 | Dify Dataset 청크 수 = Meili numberOfDocuments = Falkor Function 노드 수 |
| 04 시작 | retrieve-svc `/retrieval` 호출 로그 발생 (컨테이너 로그) |
| 04 종료 | GitLab 이슈 본문에 `cited_symbols` ≥ 1 인 항목 존재 |
| 04 리포트 | RAG Diagnostic Report 의 source_layer 분포 합 = 100% |

**합/불** — 4 항목 모두 OK + 04 빌드 시간 기존 ±20% 이내.

### 6.4 회귀 비교 (A/B) — Phase 5 핵심

**대상**: 같은 레포 + 같은 50 이슈 셋 → branch A (기존 단일 dense) vs branch B (신규 hybrid).

```bash
# Branch A — Dify 내장 retrieve
git checkout main
bash measurements/run-phase5-ab.sh --variant=A --output=measurements/phase5-A.json

# Branch B — retrieve-svc hybrid
git checkout feat/code-rag-hybrid-retrieve
bash measurements/run-phase5-ab.sh --variant=B --output=measurements/phase5-B.json

# 비교 리포트
python measurements/compare-ab.py phase5-A.json phase5-B.json \
  > measurements/phase5-report.md
```

**측정 KPI** (50 이슈 기준):

| KPI | A (baseline) | B (hybrid) | Δ |
|---|---|---|---|
| citation rate | _ % | _ % | _ pp |
| layer 분포 (dense/sparse/graph) | 100/0/0 | _ / _ / _ | _ |
| partial_citation 비율 | _ % | _ % | _ |
| 평균 응답 시간 | _ s | _ s | _ |
| LLM 토큰 소비 (총합) | _ k | _ k | _ |

**합/불** — citation rate +5pp 이상 (이상적으로 +10pp). 미달 시 Phase 5 *반복* (RRF 가중치·BM25 토크
나이저 점검) — 다음 Phase 진입 보류.

### 6.5 Phase 7 정확도 측정 (별도 메트릭)

`petclinic-rest` 12 atomic req → LLM-as-Judge 자동 판정 → 사람 정답지 비교.

```bash
# Implementation Rate 측정
jenkins-cli build "06-구현률-측정" -p REPO=petclinic-rest -p PRD=petclinic-rest-prd.md

# 정확도 비교
python measurements/spec-traceability-eval.py \
  --predicted=output/petclinic-rest-impl-rate.json \
  --golden=test-fixtures/golden/petclinic-rest-mapping.json \
  > measurements/phase7-precision.md
```

**KPI**:

| 항목 | 목표 (1 차) | 목표 (튜닝 후) |
|---|---|---|
| verdict 일치율 | ≥ 50% | ≥ 70% |
| cited_symbols precision | ≥ 0.6 | ≥ 0.75 |
| cited_symbols recall | ≥ 0.5 | ≥ 0.7 |
| needs-review 비율 | ≤ 30% | ≤ 15% |

---

## 7. GO / NO-GO 게이트

> **원칙** — 정성적 인상 (*"잘 되는 것 같다"*) 으로 다음 Phase 진입 금지. 정량 게이트 미달 시
> 그 Phase *반복*.

### 7.1 Phase 0 게이트 (D6)

| # | 항목 | 합 기준 | NO-GO 시 |
|---|---|---|---|
| 1 | supervisor 16 process RUNNING | 16 / 16 | 어떤 program 이 실패했는지 → 로그 분석 |
| 2 | 신규 5 health endpoint 응답 | 5 / 5 = 200 | 해당 health 미응답 program 디버깅 |
| 3 | 이미지 크기 | ≤ 12 GB | offline-assets 안 쓰는 weight 제거 |
| 4 | 빌드 재현성 | 동일 입력 → 동일 hash 의 tar | non-determinism 제거 (timestamps 등) |

### 7.2 Phase 2 게이트 (D13)

| # | 항목 | 합 기준 |
|---|---|---|
| 1 | 단위 테스트 | pytest 100% pass + 커버리지 ≥ 80% |
| 2 | `/retrieval` POST | 200 + Dify 스키마 valid |
| 3 | 빈 인덱스 응답 | records=[] 정상 응답 (오류 아님) |
| 4 | 응답 시간 | p95 ≤ 800ms (top_k=5 기준, 인덱스 1k chunks) |

### 7.3 Phase 3 게이트 (D18)

| # | 항목 | 합 기준 |
|---|---|---|
| 1 | 02 빌드 통과 | sink 추가 후에도 기존 흐름 통과 (graceful) |
| 2 | Dify Dataset 청크 수 = Meili numberOfDocuments | ±5% 이내 |
| 3 | Falkor Function 노드 수 | 청크 중 function/method 비율 ×0.9 이상 |
| 4 | CALLS 엣지 수 | callees 합계 × 0.7 이상 (정확도는 후속 측정) |

### 7.4 Phase 4 게이트 (D21)

| # | 항목 | 합 기준 |
|---|---|---|
| 1 | 04 E2E 통과 | Workflow 전환 후 빌드 성공 |
| 2 | source_layer 분포 | dense / sparse / graph 모두 0% 초과 |
| 3 | citation rate | 기존 baseline 대비 동급 이상 (회귀 없음) |

### 7.5 Phase 5 게이트 (D30) — 1차 목표 종료 판정

| # | 항목 | 합 기준 |
|---|---|---|
| 1 | citation rate Δ | A/B 비교 +5pp 이상 (이상적 +10pp) |
| 2 | partial_citation 비율 | ≤ 12% |
| 3 | 평균 응답 시간 | baseline ±20% 이내 (성능 회귀 없음) |
| 4 | LLM 토큰 소비 | baseline ±15% 이내 |

**NO-GO 시** — Phase 5 추가 1 주 튜닝 (RRF 가중치, BM25 토크나이저, layer weight). 2 주 누적 후에도
미달 시 PLAN §6.11 의 정직한 평가에 도달 — 결과 그대로 보고 + Phase 6/7 진행 여부 재검토.

### 7.6 Phase 6 게이트 (W8 종료)

| # | 항목 | 합 기준 |
|---|---|---|
| 1 | L2c 인용 비율 | source_layer 분포에 l2c ≥ 1% |
| 2 | inconclusive 비율 | Phase 5 baseline 대비 감소 |
| 3 | 응답 시간 | l2a only 대비 +30% 이내 |

### 7.7 Phase 7 게이트 (W14 종료) — 최종 목표 판정

| # | 항목 | 1 차 목표 | 튜닝 후 |
|---|---|---|---|
| 1 | verdict 일치율 | ≥ 50% | ≥ 70% |
| 2 | cited_symbols precision | ≥ 0.6 | ≥ 0.75 |
| 3 | needs-review 비율 | ≤ 30% | ≤ 15% |
| 4 | RTX 이식 통과 | M4 결과와 동일 verdict ≥ 90% | — |

---

## 8. 리스크 레지스터

> **사전 리스크 식별** — 발생 시 *대응* 을 미리 못 박아둔다. 발생 후 즉흥 결정 금지.

### 8.1 기술 리스크

| # | 리스크 | 가능성 | 영향 | 트리거 | 대응 |
|---|---|---|---|---|---|
| T1 | bge-reranker-v2-m3 weight 다운로드 실패 (HF rate limit) | 중 | 중 | Phase 0 P0.2 timeout | HF 토큰 사용 + 재시도 / 사내 미러 (있으면) |
| T2 | FalkorDB.so 가 Debian glibc 버전 호환 안됨 | 중 | 고 | 컨테이너 안 redis-server `loadmodule` 실패 | multi-stage `FROM falkordb/falkordb` 검증 / 폴백: Falkor 컨테이너 분리 (PLAN §6.10) |
| T3 | Meilisearch v1.42 한국어 토크나이저 빈약 | 중 | 중 | sparse layer citation rate 0% | BM25 stopwords 한국어 추가 / `nori` 같은 한국어 analyzer 검토 |
| T4 | OLLAMA_NUM_PARALLEL=3 에서 메모리 OOM | 저 | 고 | 04 빌드 중 ollama 프로세스 kill | =2 로 하향 + 재측정 |
| T5 | retrieve-svc rerank in-process 가 응답 시간 p95 > 800ms 초과 | 중 | 중 | Phase 2 게이트 4 미달 | top-N 100 → 50 으로 축소 / CrossEncoder 배치 사이즈 튜닝 |
| T6 | 단일 이미지 크기 12GB 초과 | 중 | 저 | docker save tar > 12GB | 사용 안 하는 SonarQube 플러그인 제거 / Layer 정리 |
| T7 | Dify External KB schema 변경 (Dify 업그레이드) | 저 | 고 | 04 빌드 시 retrieve-svc 응답을 Dify 가 거절 | Dify 버전 핀 (현행) + Dify 업그레이드 시 재검증 |

### 8.2 데이터·운영 리스크

| # | 리스크 | 가능성 | 영향 | 트리거 | 대응 |
|---|---|---|---|---|---|
| O1 | petclinic-rest PRD 12 req 가 LLM 추출 결과와 차이 큼 | 고 | 중 | Phase 7 verdict 일치율 < 50% | 정답지 재검토 (사람 vs LLM 어느 쪽이 맞나) → 둘 다 합리적이면 룰 명세 보강 |
| O2 | realworld 50 이슈 셋이 graph 신호에 둔감한 종류 (단순 코드 스타일 위주) | 중 | 중 | Phase 5 graph layer 비율 < 5% | 50 이슈 중 *구조성 이슈* (NPE / cycle / unused) 비율 점검 → 셋 재구성 |
| O3 | 외부망 ↔ 폐쇄망 tar 반입 절차 (USB / scp) 가 11GB 에서 실패 | 저 | 중 | docker load 중간 실패 | tar 분할 (`split -b 4G`) + 폐쇄망 측 재조립 |
| O4 | M4 Pro 검증 후 RTX 이식에서 verdict 차이 > 10% | 중 | 고 | Phase 7 W15 게이트 미달 | RTX 측 OLLAMA_NUM_PARALLEL=1 + temperature=0 강제 / 그래도 차이 크면 *재현성 한계* 로 명시 |

### 8.3 일정·결정 리스크

| # | 리스크 | 가능성 | 영향 | 트리거 | 대응 |
|---|---|---|---|---|---|
| S1 | Phase 5 게이트 미달 → 추가 2 주 튜닝 | 고 | 중 | citation rate Δ < +5pp | PLAN §6.11 정직 평가 인용 + Phase 6/7 진행 여부 재검토 (이해관계자 합의) |
| S2 | reranker 라이선스 결정 지연 (bge Apache 2.0 vs jina CC-BY-NC) | 중 | 저 | Phase 0 D5 까지 미결 | bge 로 진행 (Apache 2.0 안전) + Phase 5 측정 후 재검토 |
| S3 | Phase 7 PRD 형식 표준화 결정 지연 | 중 | 중 | W9 P7.1 시작 시 미결 | Markdown (frontmatter + atomic req block) 으로 단일화 + 후속 Confluence import 지원 |

### 8.4 발생 시 통보 절차

- 게이트 NO-GO → `measurements/phaseN-gate.md` 에 결과 + 재시도 계획 기록 → 결정 로그 업데이트.
- 리스크 발현 (위 표 항목) → PLAN §11 변경 이력에 *"리스크 X 발현 — 대응 Y 적용"* 1 줄 추가.
- 미예측 리스크 (위 표에 없음) → 본 §8 에 신규 행 추가 + 동일 절차.

---

## 9. 운영 시나리오 (정착 후)

> Phase 5 종료 후 *일상 운영* 에서 마주칠 작업들. 매뉴얼 수준이 아니라 *순서* 만 명시 — 자세한 명령은
> PLAN §6.x 참조.

### 9.1 신규 레포 추가

```bash
# 1. Jenkins 02 빌드 트리거 (REPO_URL 만 변경)
jenkins-cli build "02-코드-사전학습" -p REPO_URL=<new-repo>

# 2. Dify External KB 에 신규 knowledge_id 등록 (현행 Dify Studio UI)
#    knowledge_id 는 dataset 명과 동일 (e.g., code-kb-<repo-slug>)

# 3. 04 Workflow 의 retrieval 노드 metadata.knowledge_id 매핑 (Workflow 변수)
```

### 9.2 모델 갱신 (gemma4 / qwen3 새 버전)

```bash
# 외부망 머신
ollama pull gemma4:e4b-it-q4_K_M-NEW
cp -r ~/.ollama/models/* offline-assets/ollama-models/

# 폐쇄망 머신
cp -r offline-assets/ollama-models/* ~/.ollama/models/
ollama list

# 새 모델로 한 번 회귀 측정 (Phase 5 50 이슈 셋 재실행)
bash measurements/run-phase5-ab.sh --variant=new-model
```

새 모델이 기존 대비 KPI 회귀 시 **롤백** — 구 모델 manifests 보존 → switch.

### 9.3 dataset purge (고객 프로젝트 종료)

```bash
# Dify Dataset 삭제 (Dify Studio UI 또는 API)
# Meilisearch 인덱스 삭제
docker exec ttc-allinone curl -X DELETE \
  http://127.0.0.1:7700/indexes/code-kb-<customer> \
  -H "Authorization: Bearer $MEILI_MASTER_KEY"

# Falkor 그래프 삭제
docker exec ttc-allinone redis-cli -p 6380 GRAPH.DELETE code-kb-<customer>

# Qdrant collection 삭제 (Dify 가 관리하지만 잔여 시 직접)
```

**SI NDA 의무** — 고객 프로젝트 종료 시 위 4 단계 모두 수행 + 운영 로그에 시각·수행자 기록.

### 9.4 단일 이미지 재빌드 (분기별 / 보안 패치)

PLAN §6.9 외부망 빌드 절차 그대로. 신규 tar 가 폐쇄망에 반입되면 **현행 컨테이너 stop → docker load
신규 → 기동 → 헬스체크**. 데이터 (Qdrant / Meili / Falkor / Jenkins / GitLab) 는 모두 named volume 에
보존되므로 손실 없음.

### 9.5 Phase 7 정기 실행 (월 1 회 권장)

```bash
# cron — 매월 1 일 02:00 (M4 Pro 야간 batch)
0 2 1 * * jenkins-cli build "06-구현률-측정" -p REPO=<active-project>
```

리포트는 publishHTML → Drift 알림 (Implementation Rate 가 전월 대비 -10pp 이상 하락 시).

---

## 10. 변경 이력

> 본 문서 (EXECUTION_PLAN) 의 변경만 기록. 기술 결정은 PLAN §11 에. 작업 진행·테스트 결과·블로커는
> 여기에.

### 2026-04-26 (저녁) — §3 다운로드 & 설치 절차 신설

**컨텍스트** — 사용자 요청 *"실제로 다운로드받아서 준비해야할 것들에 대해 어떻게 다운로드하고 설치할지에
대해서도 내용을 추가해줘"*. Day-0 도달성 점검만으로는 실제 무엇을 어떻게 받아 어디에 배치하는지가
모호 → 별도 §3 으로 분리.

**추가 내용**:

- §3.1 작업 머신 요건 (디스크 ≥ 50GB, 도구 버전).
- §3.2 사전 도구 설치 — Ollama / huggingface-cli / docker / git.
- §3.3 디렉터리 레이아웃 (`offline-assets/{rerank-models,ollama-models,meilisearch,python-wheels,
  arm64,amd64}`) + 용량 예상 표.
- §3.4 항목별 상세 (Meilisearch v1.42 binary / FalkorDB multi-stage / bge-reranker / Ollama 모델 /
  Python wheels with `--platform manylinux2014_aarch64` / sample 레포 clone).
- §3.5 일괄 다운로드 스크립트 골격 (`scripts/download-rag-bundle.sh` 신규 작성 예정).
- §3.6 검증 체크리스트 7 항목 (binary 실행 가능 / FalkorDB 이미지 inspect / weight 합계 / wheel 개수 /
  torch CPU 빌드 확인 / sample 레포 pom.xml 확인).
- §3.7 폐쇄망 반입 패키징 (USB 16GB 한계 시 `split -b 4G` 분할).
- §3.8 Day 종료 기준.

**번호 재정렬**: 기존 §3~§9 → §4~§10 (subsection 포함). TOC, calendar `§7.x` 게이트 참조, 본문
cross-ref 일괄 갱신.

**핵심 결정**:

- **Python wheel 사전 다운로드 추가** — 폐쇄망에서 `pip install --no-index --find-links` 패턴.
  `torch` CPU wheel 강제 (`--extra-index-url https://download.pytorch.org/whl/cpu`).
- **Ollama 모델은 host 측, 이미지에는 미포함** — 단일 이미지 패턴 보존 (PLAN §6.4 결정 재확인).
- **bge-reranker 는 이미지 안 COPY** — sentence-transformers 가 in-process 로 import → 폐쇄망에서
  HF API 호출 0.

---

### 2026-04-26 — 문서 신설

**컨텍스트** — 사용자 요청 *"사전준비부터 테스트에 이르기까지 구체적인 수행계획을 작성해보자"*. PLAN
§10 의 Phase 0~7 위에 운영 레벨 런북을 분리 작성.

**결정**:
- 범위: Phase 0~7 전체 (1차 목표 + 최종 목표).
- 머신 우선순위: M4 Pro 먼저 검증 끝낸 뒤 RTX 4070 이식 (W15 분리).
- 테스트 데이터: realworld (baseline) + spring-petclinic-rest (sanity + Phase 7 ground truth 12 req).
- PLAN 과 분리 작성 — PLAN 이 이미 2,479 줄로 비대해 가독성 저하 우려.

**spring-petclinic-rest 선정 근거**:
- Apache 2.0 라이선스, v4.0.2 (2026-02) 활성 유지.
- REST 엔드포인트 ~40 개 → atomic requirement 1:1 매핑 가능 (Phase 7 정답지 작성 현실적).
- Spring Boot — Korean SI 의 가장 흔한 스택, realworld 와 비교군.

**산출**: 본 문서 (9 섹션, 약 700 줄). PLAN 목차에 EXECUTION 참조 링크 추가 예정.
