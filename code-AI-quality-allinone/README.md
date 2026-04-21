# TTC 4-Pipeline All-in-One — 에어갭 설치 & 구동 가이드

> **이 스택은 폐쇄망/에어갭 환경에서 작동하도록 설계되었습니다.**
> 전체 흐름: **온라인 준비 머신**에서 필요한 자산 (Docker 이미지 + 플러그인 + Ollama 모델) 을 모두 반출 → 매체(USB/NAS) 로 이동 → **오프라인 운영 머신**에서 복원 후 구동.
>
> 처음부터 인터넷 없는 머신에서 빌드·pull 은 **불가능** 합니다. 반드시 온라인 준비 단계를 선행해야 합니다.

---

## 목차

1. [이 스택이 하는 일](#1-이-스택이-하는-일)
2. [에어갭 배포 전체 그림](#2-에어갭-배포-전체-그림)
3. [온라인 준비 머신 — 자산 수집](#3-온라인-준비-머신--자산-수집)
4. [에어갭 매체 구성 (반출 패키지)](#4-에어갭-매체-구성-반출-패키지)
5. [오프라인 운영 머신 — 런타임 설치](#5-오프라인-운영-머신--런타임-설치)
6. [오프라인 머신에서 스택 기동](#6-오프라인-머신에서-스택-기동)
7. [첫 실행 — 샘플 레포로 파이프라인 돌려보기](#7-첫-실행--샘플-레포로-파이프라인-돌려보기)
8. [각 파이프라인 상세](#8-각-파이프라인-상세)
9. [GitLab Issue 결과물 읽는 법](#9-gitlab-issue-결과물-읽는-법)
10. [접속 정보 & 자격](#10-접속-정보--자격)
11. [자동 프로비저닝 내부 동작](#11-자동-프로비저닝-내부-동작)
12. [트러블슈팅](#12-트러블슈팅)
13. [초기화 & 재시작](#13-초기화--재시작)
14. [파일 구성 레퍼런스](#14-파일-구성-레퍼런스)
15. [프로덕션 전 체크리스트](#15-프로덕션-전-체크리스트)
16. [부록: 온라인 단일 머신 빠른 테스트](#16-부록-온라인-단일-머신-빠른-테스트)

---

## 1. 이 스택이 하는 일

폐쇄망 환경에서 GitLab 레포에 올라간 소스 코드를 자동으로 점검하고, 각 문제에 대한 **해석과 수정 방향까지 포함한 GitLab Issue 로 정리해주는 통합 파이프라인**입니다. 외부 AI 서비스를 사용하지 않으며, 모든 LLM 추론은 사내 하드웨어에서 실행됩니다.

### 1.1 현재 실무에서의 어려움

정적분석 도구(SonarQube 등)는 이미 널리 도입되어 있지만, 결과물을 그대로 개발자에게 전달했을 때 다음과 같은 비효율이 반복됩니다.

**문제 1 — 우선순위 판단 불가.** 대규모 레포를 1회 스캔하면 수백에서 수천 건의 이슈가 쏟아집니다. 어느 이슈부터 손대야 하는지, 어떤 것이 실제 서비스에 영향을 주는지 판단하려면 사람이 직접 코드를 읽어봐야 합니다.

**문제 2 — 맥락 정보의 부재.** 각 이슈는 "bare except 금지" 같은 **규칙 위반 사실**만 알려줍니다. "이 함수가 어디서 호출되는가", "수정하면 무엇이 깨지는가", "팀의 다른 코드는 이 규칙을 어떻게 처리했는가" 같은 **맥락 정보**는 제공되지 않아, 개발자가 매번 IDE 를 열어 조사해야 합니다.

**문제 3 — 오탐(False Positive) 처리 비용.** 이슈의 상당수는 실제로는 문제가 아닌 오탐이지만, 판정을 위해 사람이 일일이 코드를 추적해야 합니다. 수백 건 중 실제 조치가 필요한 것만 골라내는 과정에서 상당한 시간이 소모됩니다.

**문제 4 — 추적 분산.** 이슈는 SonarQube 대시보드에, 작업 지시는 GitLab Issue 에, 논의는 사내 메신저에 흩어집니다. 한 건을 추적하려면 세 곳을 오가야 합니다.

### 1.2 이 스택의 접근 방식

**커밋 SHA 하나를 지정해 Jenkins Job 1 회를 실행하면**, 4 개의 파이프라인이 순차로 동작해 위의 네 가지 문제를 한 번에 해소합니다. 각 파이프라인의 역할과 최종 산출 효과는 다음과 같습니다.

| 파이프라인 | 수행 작업 | 최종 산출 효과 |
|-----------|----------|---------------|
| **00 코드 분석 체인** | 01 → 02 → 03 을 단일 커밋 SHA 기준으로 순차 실행하고 결과를 집계. | 개발자는 **한 번의 버튼 클릭**으로 끝. 중간 단계를 개별 실행할 필요가 없음. |
| **01 코드 사전학습** | 대상 레포의 모든 소스를 함수·메서드 단위로 분해하고, 각 조각에 "이 함수가 하는 일" 요약을 붙여 검색용 지식 창고(Dify Knowledge Base)에 적재. | 이후 단계에서 "비슷한 코드 / 호출 관계" 를 자동으로 검색해 가져올 수 있는 **맥락 데이터베이스 확보**. |
| **02 코드 정적분석** | 지정 커밋 스냅샷에 대해 SonarQube 스캐너를 실행. | 규칙 위반 사항 목록이 확정됨 (기존 Sonar 사용 방식과 동일). |
| **03 정적분석 결과분석 이슈등록** | ① Sonar 이슈에 **사실 정보 보강**(파일/함수/호출관계/커밋이력), ② 01 의 지식 창고에서 **관련 코드 자동 검색**, ③ LLM 에게 "진짜 문제인가, 어떻게 고칠까" 판단 요청, ④ **오탐은 Sonar 에 자동 마킹**, ⑤ 진짜 문제는 **GitLab Issue 로 정리해 등록**. | 개발자에게 도달하는 것은 **조치 가능한 Issue 만**. 오탐 걸러내기·맥락 조사·수정안 초안 작성까지 자동화 완료. |

별도로 제공되는 **04 AI 평가** 파이프라인은 운영 중인 사내 LLM 서비스(챗봇, API 등)의 응답 품질을 Golden Dataset 기반으로 정기 검증하는 독립 도구입니다.

### 1.3 개발자가 받게 되는 결과물

파이프라인이 종료되면 GitLab 프로젝트의 Issues 페이지에 **조치가 필요한 항목만** 새 Issue 로 등록되어 있습니다. 각 Issue 는 표준화된 구조를 가지며, 개발자가 다른 도구로 이동할 필요 없이 **Issue 페이지 하나에서 판단·조치가 가능** 하도록 설계되었습니다.

| Issue 섹션 | 담고 있는 것 | 생성 주체 |
|-----------|-------------|:---------:|
| TL;DR | "어느 파일의 어느 함수에서 무슨 일이 일어나는가" 를 한 줄로 요약 | 템플릿 |
| 위치 테이블 | 파일 경로·함수명·규칙 ID·심각도·커밋 해시 (모두 클릭 가능 링크) | 사실 정보 |
| 문제 코드 | 해당 라인 ±10 줄을 발췌, 문제 라인에 `>>` 마커 | Sonar 원본 |
| 수정 제안 | 어떻게 고쳐야 하는지 자연어 설명 | LLM |
| 수정 Diff | 그대로 적용 가능한 unified diff 패치 | LLM |
| 영향 분석 | "이 함수는 X 에서 호출되므로 Y 에 영향" 같은 파급 효과 해석 | LLM (RAG 기반) |
| 동일 패턴 다른 위치 | 같은 규칙으로 다른 파일에서도 같은 문제가 발견되면 일괄 표로 | 자동 집계 |
| 규칙 상세 | SonarQube 규칙 원문 (접기/펼치기) | Sonar |
| 링크 | SonarQube 상세 · GitLab 파일 해당 라인 · GitLab 커밋 상세 | URL 조합 |
| 라벨 | 심각도 / 진짜문제·오탐 구분 / 확신도 등 | LLM + 자동 |

개발자는 이 Issue 하나만 열면 **문제 코드 확인 → 영향 범위 파악 → 수정 방향 결정 → Diff 적용** 까지 수행할 수 있습니다. IDE·Sonar 대시보드·팀 메신저를 오갈 필요가 없습니다.

### 1.4 기대 효과

| 항목 | 기존 프로세스 (Sonar 단독) | 본 스택 도입 후 |
|------|---------------------------|----------------|
| 이슈 수신 후 1차 분류 | 개발자가 수십 분간 코드 읽고 오탐 걸러냄 | LLM 이 자동 판정·마킹. 오탐은 개발자에게 도달하지 않음 |
| 영향 범위 파악 | IDE 열어 호출처 직접 추적 | Issue 본문의 "영향 분석" 섹션에서 즉시 확인 |
| 수정 방향 결정 | 문서·규칙 설명 직접 조사 | LLM 의 수정 제안 + Diff 를 검토 후 적용 여부 판단 |
| 같은 패턴 반복 이슈 | 각각 별도 이슈로 수십 건 쌓임 | 하나의 대표 Issue + "동일 패턴 다른 위치" 표로 통합 |
| 이슈 추적 위치 | SonarQube · GitLab · 메신저 분산 | GitLab Issues 한 곳으로 통합 |

### 1.5 외부 AI 서비스 미사용 — 에어갭 적합성

- ChatGPT, Gemini 등 외부 LLM API 를 사용하지 않으며, 소스 코드가 외부로 반출되지 않습니다.
- 모든 LLM 추론은 **호스트 Ollama 데몬**에서 수행합니다 (`gemma4:e4b`, `bge-m3`, `qwen3-coder:30b`).
- 초기 자산 반입이 완료되면 인터넷 차단 환경에서 **반복 사용에 제약이 없습니다**.
- 호출 건수에 따른 API 과금이 없으며, 호스트 하드웨어(Apple Metal / NVIDIA CUDA) 자원만으로 처리됩니다.

### 1.6 기술적 실행 개요

Jenkins Pipeline `00 코드 분석 체인` 을 한 번 실행하면 다음 흐름이 자동으로 진행됩니다.

1. 커밋 SHA 해석 (`git ls-remote` 또는 파라미터)
2. tree-sitter 로 함수 단위 AST 청킹 → Ollama bge-m3 임베딩 → Dify Knowledge Base 적재
3. SonarQube 스캔 실행
4. 각 Sonar 이슈에 대해 Dify Workflow 호출 (멀티쿼리 RAG + severity 라우팅)
5. GitLab Issue 자동 등록 (위치·코드·수정제안·영향분석·링크 포함)
6. 오탐 판정 건은 SonarQube 에 자동 전이 시도, 실패 시 라벨로 구분하여 Issue 생성 (Dual-path)

모든 LLM 추론은 호스트 Ollama 에서 처리되어 외부 의존이 없습니다.

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

### 3.1 온라인 준비 머신 요구사항

| 항목 | 권장 |
|------|------|
| OS | macOS (Apple Silicon / Intel) 또는 Linux (amd64) |
| Docker Desktop | ≥ 25.x |
| Docker 메모리 할당 | ≥ 12 GB |
| 여유 디스크 | ≥ **80 GB** (이미지 빌드 + tarball 산출 + Ollama 모델) |
| 인터넷 | Docker Hub + GitHub + ollama.com + marketplace.dify.ai 접근 |

**중요**: 오프라인 운영 머신과 **같은 아키텍처**에서 빌드해야 합니다 (arm64 ↔ arm64, amd64 ↔ amd64). Apple Silicon 운영 → Apple Silicon 준비 / WSL2 운영 → amd64 Linux 준비.

### 3.2 Step 1: 레포 받기 + 디렉터리로 이동

```bash
git clone <이 레포 URL>
cd airgap-test-toolchain/code-AI-quality-allinone
```

**이후 온라인 준비 머신의 모든 명령은 이 `code-AI-quality-allinone` 폴더 안에서 실행합니다.**

### 3.3 Step 2: Docker Desktop + Ollama installer 받기

오프라인 머신에 옮길 installer 들을 미리 다운로드해 두세요. (운영 머신에 이미 설치되어 있으면 스킵)

```bash
mkdir -p installers

# === macOS (Apple Silicon 운영 시) ===
curl -fL -o installers/Docker.dmg \
  "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
curl -fL -o installers/Ollama-darwin.zip \
  "https://ollama.com/download/Ollama-darwin.zip"

# === Windows (WSL2 amd64 운영 시) ===
curl -fL -o installers/Docker-Desktop-Installer.exe \
  "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
curl -fL -o installers/OllamaSetup.exe \
  "https://ollama.com/download/OllamaSetup.exe"

# === Linux amd64 운영 시 (Ollama 바이너리) ===
curl -fL -o installers/ollama-linux-amd64 \
  "https://ollama.com/download/ollama-linux-amd64"
chmod +x installers/ollama-linux-amd64
```

### 3.4 Step 3: Ollama 모델 3종 다운로드

> **역할 분담 한 줄 정리**
> — **사용자 책임**: Ollama 데몬에 모델 바이너리가 있어야 함 (추론을 실제로 수행).
> — **자동 처리**: Dify 에 Ollama 플러그인 설치 + provider 등록 + embedding 등록 + workspace 기본 모델 지정 + workflow publish — 이 전부를 `scripts/provision.sh` 가 최초 기동 시 자동 수행합니다. 사용자는 Dify UI 에서 별도로 모델 이름을 입력할 필요가 없습니다.

운영 머신에 그대로 옮길 수 있도록 준비 머신에 Ollama 를 설치해 모델을 받고, `~/.ollama/models/` 디렉터리를 통째로 반출합니다.

```bash
# 준비 머신에 Ollama 가 없다면 설치 후
brew install ollama           # macOS
# 또는
curl -fsSL https://ollama.com/install.sh | sh    # Linux

# 백그라운드 기동
ollama serve &
# (macOS 는 brew services start ollama 권장)

# 필수 3 모델 (총 ~25GB, 30분 내외 소요)
ollama pull gemma4:e4b          # Dify Workflow 기본 LLM        ~4GB
ollama pull bge-m3              # Dify 임베딩                   ~1GB
ollama pull qwen3-coder:30b     # BLOCKER/CRITICAL severity 라우팅  ~20GB

# 확인
curl http://localhost:11434/api/tags | python3 -m json.tool
```

**모델 바이너리 위치** (반출 대상):

| OS | 경로 |
|----|------|
| macOS (brew) | `~/.ollama/models/` |
| Linux | `/usr/share/ollama/.ollama/models/` 또는 `~/.ollama/models/` |
| Windows | `%USERPROFILE%\.ollama\models\` |

> **용량이 너무 크면** — `qwen3-coder:30b` 는 선택입니다. 받지 않으면 severity 라우팅이 CRITICAL 이슈를 `skip_llm` 템플릿으로 처리하거나, `pipeline-scripts/sonar_issue_exporter.py` 의 `_SEVERITY_ROUTING` 을 수정해 모든 severity 를 `gemma4:e4b` 로 매핑할 수 있습니다. `gemma4:e4b` + `bge-m3` 만으로도 End-to-end 동작합니다.

### 3.5 Step 4: Jenkins + Dify 플러그인 번들 수집

```bash
bash scripts/download-plugins.sh
```

결과:

| 산출물 | 용도 | 크기 |
|--------|------|------|
| `jenkins-plugin-manager.jar` | 빌드 시 재사용 | ~7 MB |
| `jenkins-plugins/*.jpi` | Jenkins 플러그인 전체 (의존성 재귀) | ~40 MB |
| `dify-plugins/langgenius-ollama-*.difypkg` | Dify Ollama provider | ~1 MB |
| `.plugins.txt` | 플러그인 목록 | ~1 KB |

> **왜 이게 필요한가?** 빌드 시 Dockerfile 이 이 파일들을 COPY 합니다. 오프라인 빌드는 불가능하지만, 온라인에서 한 번 받아두면 이후 반복 빌드는 캐시 재사용이 빠릅니다.

### 3.6 Step 5: 통합 이미지 빌드 + tarball 산출

```bash
# arm64 운영용 (macOS Apple Silicon)
bash scripts/offline-prefetch.sh --arch arm64

# amd64 운영용 (WSL2, x86 Linux)
bash scripts/offline-prefetch.sh --arch amd64
```

이 스크립트가 내부적으로 수행하는 것:
1. `docker buildx build` 로 `Dockerfile` 기준 통합 이미지 빌드. 빌드 중 `FROM` 의 **베이스 이미지 5종** (`langgenius/dify-api:1.13.3`, `langgenius/dify-web:1.13.3`, `langgenius/dify-plugin-daemon:0.5.3-local`, `sonarqube:10.7.0-community`, `jenkins/jenkins:lts-jdk21`) 을 자동 pull 해서 최종 이미지 layer 안에 모두 흡수.
2. `docker save` 로 통합 이미지를 tarball 로 저장.
3. GitLab 런타임 이미지 (`gitlab-ce:17.4.2-ce.0` / arm64 는 `yrzr/gitlab-ce-arm64v8`) 를 별도로 pull 후 tarball 저장.

**핵심**: 베이스 이미지들은 **통합 tarball 안에 이미 포함**되므로 오프라인 머신에 따로 반출할 필요가 없습니다. 아래 두 tarball 만으로 완전합니다.

산출물:

```
offline-assets/<arch>/
├── ttc-allinone-<arch>-dev.tar.gz          (~10 GB — 베이스 5종 포함 통합 이미지)
├── ttc-allinone-<arch>-dev.meta            (sha256 + built_at)
├── gitlab-gitlab-ce-17.4.2-ce.0-<arch>.tar.gz    (~1.5 GB — 별도 런타임)
└── gitlab-gitlab-ce-17.4.2-ce.0-<arch>.meta
```

**두 tarball 을 반드시 함께** 반출해야 합니다. `ttc-allinone` 은 통합 런타임, `gitlab-*` 은 별도 서비스입니다.

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

**전제**: 이 머신은 인터넷 접근이 없습니다. 모든 것을 반출 매체에서 복원합니다.

### 5.1 운영 머신 요구사항

| 항목 | 최소 | 권장 |
|------|------|------|
| CPU | arm64 (M1+) 또는 amd64 x86_64 | — |
| 메모리 | 16 GB (Docker 12 GB 할당) | 32 GB |
| 디스크 | 50 GB 여유 | 100 GB |
| OS | macOS 13+ / Windows 11 + WSL2 / Linux (커널 5.x+) | — |

### 5.2 Step 1: Docker Desktop 설치

**macOS (Apple Silicon)**:
```bash
# 반출 매체에서 Docker.dmg 를 마운트한 뒤
open /Volumes/ttc-airgap-bundle/installers/Docker.dmg
# Applications 로 Docker 드래그 → Docker.app 실행 → 초기 설정 중 "Skip" 선택
# 설정 → Resources → Memory 12GB 이상, Disk 60GB 이상 할당
```

**Windows (WSL2)**:
```
installers\Docker-Desktop-Installer.exe 더블클릭 → 설치
WSL2 백엔드 활성화 → 우분투 배포판에 접속
```

설치 확인:
```bash
docker version
# Client + Server 모두 출력되면 OK
```

### 5.3 Step 2: Ollama 설치

**macOS**:
```bash
unzip /Volumes/ttc-airgap-bundle/installers/Ollama-darwin.zip
sudo mv Ollama.app /Applications/
open -a Ollama
# 메뉴바 아이콘이 뜨면 데몬 실행 중
```

**Windows**:
```
installers\OllamaSetup.exe 실행 → 설치 → Ollama 트레이 아이콘 확인
```

**Linux**:
```bash
sudo install -m 755 /media/usb/installers/ollama-linux-amd64 /usr/local/bin/ollama
ollama serve &       # 또는 systemd 유닛 등록
```

설치 확인:
```bash
curl http://localhost:11434/api/tags
# {"models":[]} 같은 JSON 이 돌아오면 OK
```

### 5.4 Step 3: Ollama 모델 복원

**중요**: Ollama 를 **일단 중지**한 뒤 모델 디렉터리를 통째로 덮어씁니다.

```bash
# macOS/Linux
# 1. Ollama 중지
launchctl unload ~/Library/LaunchAgents/com.ollama.*.plist 2>/dev/null || true
pkill -f 'ollama serve' || true

# 2. 모델 디렉터리 복원
mkdir -p ~/.ollama
rsync -av /Volumes/ttc-airgap-bundle/ollama-models/ ~/.ollama/models/
# (Linux 시스템 Ollama 는 /usr/share/ollama/.ollama/models/ 경로)

# 3. 재기동
open -a Ollama                          # macOS
# 또는
ollama serve &                          # Linux

# 4. 확인
curl http://localhost:11434/api/tags | python3 -m json.tool
# "gemma4:e4b", "bge-m3" 등이 나와야 함
```

**Windows**:
```powershell
# PowerShell 관리자
Stop-Service -Name Ollama -ErrorAction SilentlyContinue
Copy-Item -Path "E:\ttc-airgap-bundle\ollama-models\*" `
          -Destination "$env:USERPROFILE\.ollama\models\" -Recurse -Force
Start-Process -FilePath "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
```

**모델 동작 확인**:
```bash
ollama run gemma4:e4b "hello"
# 또는
curl http://localhost:11434/api/generate \
  -d '{"model":"gemma4:e4b","prompt":"hi","stream":false}' | python3 -m json.tool
```

> **여기까지가 사용자 책임의 끝** — Ollama 에 모델만 적재되어 있으면 됩니다. 이후 §6 에서 스택을 기동하면 `provision.sh` 가 자동으로 Dify 에 Ollama 플러그인을 설치하고 `gemma4:e4b` / `bge-m3` 을 provider/embedding 으로 등록하며 workspace 기본 모델까지 지정합니다. Dify UI 에서 수동 설정은 필요 없습니다.

**Docker 컨테이너에서 호스트 Ollama 로 도달 가능한지**:

- macOS / Windows Docker Desktop: `host.docker.internal` 자동 해석됨. 확인:
  ```bash
  docker run --rm curlimages/curl:latest \
    curl -sf http://host.docker.internal:11434/api/tags
  ```
- Linux: Docker Desktop 이면 동일. 네이티브 Docker 면 `docker-compose.*.yaml` 에 `extra_hosts: ["host.docker.internal:host-gateway"]` 추가 필요 (기본 compose 파일에 이미 포함).
- **Ollama 가 localhost 만 listen 하는 경우** (기본 macOS):
  ```bash
  # macOS: 네트워크 모든 인터페이스에서 listen
  launchctl setenv OLLAMA_HOST "0.0.0.0"
  # Ollama 재기동
  ```

### 5.5 Step 4: 반출한 레포 폴더 + 이미지 tarball 복사

```bash
# 반출 매체 → 운영 머신 로컬 디스크로 복사 (매체에서 직접 실행 X — 성능·안정성 이슈)
cp -a /Volumes/ttc-airgap-bundle/code-AI-quality-allinone  ~/code-AI-quality-allinone
cd ~/code-AI-quality-allinone

# tarball 이 제 자리에 있는지 확인
ls -lh offline-assets/<arch>/
# ttc-allinone-*.tar.gz  (~10GB)
# gitlab-*.tar.gz        (~1.5GB)
```

---

## 6. 오프라인 머신에서 스택 기동

### 6.1 Step 1: Docker 이미지 복원

```bash
cd ~/code-AI-quality-allinone

# 자동 (두 tarball 일괄 load)
bash scripts/offline-load.sh --arch arm64          # macOS
# 또는
bash scripts/offline-load.sh --arch amd64          # WSL2/Linux

# 완료되면 이미지가 보임:
docker images | grep -E "ttc-allinone|gitlab"
# ttc-allinone           arm64-dev    10GB
# yrzr/gitlab-ce-arm64v8 17.4.2-ce.0  1.5GB
```

**수동으로도 가능**:
```bash
gunzip -c offline-assets/arm64/ttc-allinone-arm64-dev.tar.gz | docker load
gunzip -c offline-assets/arm64/gitlab-*.tar.gz | docker load
```

### 6.2 Step 2: 이미지 태그 확인 (compose 파일이 찾는 이름)

`docker-compose.mac.yaml` 은 `ttc-allinone:mac-dev` 를 찾지만 offline-prefetch 는 `ttc-allinone:arm64-dev` 로 저장합니다. 태그를 맞춰주거나 env 로 override:

```bash
# 옵션 A: compose 기본 태그로 별칭 추가
docker tag ttc-allinone:arm64-dev ttc-allinone:mac-dev

# 옵션 B: compose 실행 시 env 로 주입
export IMAGE=ttc-allinone:arm64-dev
```

WSL2 도 마찬가지 (`amd64-dev` ↔ `wsl2-dev`):
```bash
docker tag ttc-allinone:amd64-dev ttc-allinone:wsl2-dev
```

### 6.3 Step 3: 스택 기동

```bash
# macOS
bash scripts/run-mac.sh
# 또는
docker compose -f docker-compose.mac.yaml up -d

# WSL2
bash scripts/run-wsl2.sh
# 또는
docker compose -f docker-compose.wsl2.yaml up -d
```

확인:
```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
# ttc-allinone    Up 30 seconds
# ttc-gitlab      Up 30 seconds (health: starting)
```

### 6.4 Step 4: 자동 프로비저닝 대기 (~7분)

```bash
# 실시간 진행 상황
docker logs -f ttc-allinone | grep -E "provision|entrypoint"

# 완료 시그널 대기 (스크립트용)
until docker logs ttc-allinone 2>&1 | grep -q "자동 프로비저닝 완료"; do
    sleep 15
done
echo "PROVISION_DONE"
```

완료 로그:
```
[provision] 자동 프로비저닝 완료.
[provision]   Jenkins    : http://127.0.0.1:28080 (admin / password)
[provision]   Dify       : http://127.0.0.1:28081 (admin@ttc.local / TtcAdmin!2026)
[provision]   SonarQube  : http://localhost:29000 (admin / TtcAdmin!2026)
[provision]   GitLab     : http://localhost:28090 (root / ChangeMe!Pass)
[entrypoint] 앱 프로비저닝 완료.
```

**기동 검증**:

```bash
# 5개 Jenkins Job 이 다 등록됐나?
curl -s -u admin:password 'http://127.0.0.1:28080/api/json?tree=jobs%5Bname%5D' \
  | python3 -c "import json,sys;print(*(j['name'] for j in json.load(sys.stdin)['jobs']),sep='\n')"
# → 00-코드-분석-체인
#   01-코드-사전학습
#   02-코드-정적분석
#   03-정적분석-결과분석-이슈등록
#   04-AI평가

# 프로비저닝 마커 파일 11종이 다 있나?
docker exec ttc-allinone ls /data/.provision/
# → dataset_api_key  dataset_id  default_models.ok  gitlab_root_pat
#   jenkins_sonar_integration.ok  ollama_embedding.ok  ollama_plugin.ok
#   sonar_token  workflow_api_key  workflow_app_id  workflow_published.ok
```

---

## 7. 첫 실행 — 샘플 레포로 파이프라인 돌려보기

GitLab 에 분석 대상 레포가 있어야 합니다. 본 스택은 레포를 자동 생성하지 **않으므로** 한 번만 수동으로 만들어 줍니다.

### 7.1 샘플 레포 만들기 (3 분)

```bash
# 로컬에 샘플 코드 폴더
mkdir -p /tmp/dscore-ttc-sample/src && cd /tmp/dscore-ttc-sample

# Sonar 가 잡을 bare except (python:S5754 CRITICAL) 의도적 포함
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

### 7.2 GitLab 에 프로젝트 생성 + 푸시

```bash
# provision.sh 가 자동 발급한 GitLab root PAT 를 컨테이너에서 가져옴
GITLAB_PAT=$(docker exec ttc-allinone cat /data/.provision/gitlab_root_pat)

# 프로젝트 생성 (REST API)
curl -sS -X POST "http://localhost:28090/api/v4/projects" \
  -H "PRIVATE-TOKEN: $GITLAB_PAT" \
  -d "name=dscore-ttc-sample&visibility=private&initialize_with_readme=false"

# 푸시
cd /tmp/dscore-ttc-sample
git remote add origin "http://oauth2:${GITLAB_PAT}@localhost:28090/root/dscore-ttc-sample.git"
git push -u origin main
```

GitLab UI ([http://localhost:28090/root/dscore-ttc-sample](http://localhost:28090/root/dscore-ttc-sample), `root` / `ChangeMe!Pass`) 에서 파일이 올라갔는지 확인.

### 7.3 00 체인 Job 실행 (Jenkins UI)

1. [http://localhost:28080](http://localhost:28080) 접속 (`admin` / `password`).
2. **`00-코드-분석-체인`** Job 클릭.
3. **"Build with Parameters"** 클릭. (최초 1회는 "Build Now" 로 한 번 눌러 parameter discovery 를 유도해야 할 수 있습니다 — 실패하면 10초 뒤 "Build with Parameters" 가 나타납니다.)
4. 파라미터:
   - `REPO_URL`: `http://gitlab:80/root/dscore-ttc-sample.git`
   - `BRANCH`: `main`
   - `ANALYSIS_MODE`: `full`
   - 나머지 기본값
5. **Build** 클릭.

### 7.4 모니터링

Jenkins Stage View 에서 보거나 CLI:

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

성공 시:
```
status: SUCCESS
  1. Resolve Commit SHA          SUCCESS    2.1s
  2. Trigger P1 (사전학습)        SUCCESS   45.3s
  3. Trigger P2 (정적분석)        SUCCESS   55.2s
  4. Trigger P3 (이슈 등록)       SUCCESS   90.8s
  5. Chain Summary               SUCCESS    1.2s
```

### 7.5 결과 확인

1. **GitLab Issue**: [http://localhost:28090/root/dscore-ttc-sample/-/issues](http://localhost:28090/root/dscore-ttc-sample/-/issues)
   - `[CRITICAL] Specify an exception class to catch or reraise the exception` Issue #1 이 있어야 합니다.
2. **SonarQube 대시보드**: [http://localhost:29000/dashboard?id=dscore-ttc-sample](http://localhost:29000/dashboard?id=dscore-ttc-sample)
3. **Dify Studio**: [http://localhost:28081](http://localhost:28081) → Knowledge → `code-context-kb` → Recall Testing.
4. **상태 파일**:
   ```bash
   docker exec ttc-allinone cat /data/kb_manifest.json
   docker exec ttc-allinone cat /var/knowledges/state/chain_*.json
   ```

### 7.6 재실행 (dedup 작동 확인)

같은 커밋으로 Job 을 한 번 더 눌러보세요:
- `chain_<sha>.json` 의 `p3_summary.skipped=1` 로 dedup 이 잡힙니다.
- GitLab Issues 페이지에 중복 Issue 가 생기지 **않습니다**.

---

## 8. 각 파이프라인 상세

### 8.1 `00-코드-분석-체인` (오케스트레이터)

**역할**: 커밋 SHA 하나를 기준으로 01·02·03 파이프라인을 올바른 순서와 파라미터로 연쇄 실행합니다. 개발자는 이 Job 만 실행하면 되며, 중간 단계를 개별로 호출하거나 매개변수를 맞출 필요가 없습니다.

**성과**: 실행이 완료되면 해당 커밋에 대한 RAG KB, SonarQube 스캔 리포트, GitLab Issue 등록, 체인 요약 JSON 이 모두 준비되어 있습니다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `REPO_URL` | `http://gitlab:80/root/dscore-ttc-sample.git` | 컨테이너 내부 이름 `gitlab` |
| `BRANCH` | `main` | 분석 브랜치 |
| `ANALYSIS_MODE` | `full` | `full` = KB 강제 재빌드 · `commit` = manifest 일치 시 재사용 |
| `COMMIT_SHA` | `(빈 값)` | 지정 시 그 커밋 고정, 빈 값이면 BRANCH HEAD |

**Stage 구조** (총 5 단계, 각 Stage 는 wait+propagate 로 순차 실행):

1. Resolve Commit SHA — `git ls-remote` 로 BRANCH HEAD 해석하거나 파라미터값 사용
2. Trigger P1 — `01-코드-사전학습` 을 `build job:` 로 호출 (COMMIT_SHA 전달)
3. Trigger P2 — `02-코드-정적분석` 호출
4. Trigger P3 — `03-정적분석-결과분석-이슈등록` 호출 (MODE=`full` → `incremental` 매핑)
5. Chain Summary — P3 artifact 에서 `gitlab_issues_created.json` 을 읽어 `p3_summary` 집계 → `/var/knowledges/state/chain_<sha>.json` 에 저장 + `archiveArtifacts`

---

### 8.2 `01-코드-사전학습` (P1) — RAG 지식 창고 구축

**역할**: 분석 대상 레포의 모든 소스를 함수·메서드 단위로 분해하고, 각 조각에 자연어 요약을 붙여 Dify Knowledge Base 에 적재합니다.

**성과**: 이 파이프라인이 완료되면, 이후 P3 에서 특정 코드 조각에 대해 "이 레포 안의 비슷한 함수 / 이 함수를 호출하는 곳 / 같은 패턴의 기존 코드" 를 자연어 질의로 즉시 검색할 수 있게 됩니다. 이 데이터베이스가 있어야 LLM 이 "이 프로젝트의 맥락에 맞는 답" 을 생성할 수 있습니다.

#### 8.2.1 Step 1 — Git Clone

`${REPO_URL}` 를 `/var/knowledges/codes/<repo>` 로 clone. `withCredentials[gitlab-pat]` 로 oauth 주입.

#### 8.2.2 Step 2 — tree-sitter AST 청킹 (`repo_context_builder.py`)

레포를 순회하며 **언어별 구문 트리** 를 파싱해 함수/메서드/클래스 단위로 청크를 생성:

| 언어 | 확장자 | 추출 대상 노드 |
|------|--------|-----------|
| Python | `.py` | `function_definition`, `async_function_definition`, `class_definition` |
| Java | `.java` | `method_declaration`, `constructor_declaration`, `class_declaration`, `interface_declaration`, `enum_declaration` |
| TypeScript | `.ts` | `function_declaration`, `method_definition`, `class_declaration`, `interface_declaration`, `arrow_function` |
| TSX | `.tsx` | `function_declaration`, `method_definition`, `class_declaration` |
| JavaScript | `.js` | `function_declaration`, `method_definition`, `class_declaration`, `arrow_function` |

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
  "callees": ["verify_password", "dict.__getitem__"],
  "callers": [],
  "test_for": ""
}
```

- **`callees`**: 이 함수 안에서 호출하는 다른 함수들 (tree-sitter 가 call site 를 재귀 순회해 수집). 파이썬은 `call` 노드, Java 는 `method_invocation`, JS/TS 는 `call_expression`.
- **`callers`**: P1 단계에선 빈 배열. P3 의 exporter 가 JSONL 전체를 역인덱스로 처리해 `direct_callers` 필드를 별도 계산.

> **왜 함수 단위인가?** 파일 전체를 하나의 문서로 넣으면 임베딩 품질이 떨어지고, "이 이슈가 어떤 함수에서 발생하는지" 검색이 어려워집니다. 함수 단위가 "맥락 일관성 + 검색 정확성" 의 최적 균형점입니다.

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
  "repo_url": "http://gitlab:80/root/dscore-ttc-sample.git",
  "branch": "main",
  "commit_sha": "e38bd123e0630db4cb5cff78272c4710707eeab3",
  "analysis_mode": "full",
  "uploaded_at": 1776796293,
  "document_count": 5,
  "dataset_id": "5156d41f-6e9b-4c43-ae51-0b3b34c1a33d"
}
```

P2/P3 가 이 파일로 KB freshness 를 검증합니다.

---

### 8.3 `02-코드-정적분석` (P2) — SonarQube 스캔

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
4. Sonar 서버에 분석 리포트 전송 → [http://localhost:29000/dashboard?id=dscore-ttc-sample](http://localhost:29000/dashboard?id=dscore-ttc-sample)

---

### 8.4 `03-정적분석-결과분석-이슈등록` (P3) — LLM 분석 + Issue 생성

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

**Multi-query `kb_query` 구성** — 4 줄 조합:

```
<이슈 라인 ±3~4 줄 코드창>
function: <enclosing_function>
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
path: src/auth.py
"SystemExit" should be re-raised
```

이 쿼리가 Dify knowledge_retrieval 노드에서 hybrid_search 로 KB 청크를 끌어옵니다. **단일 rule 이름만 넣던 것보다 RAG 적중률이 크게 향상** — "login 함수 근처 / auth.py 파일 / SystemExit 처리" 의 다중 관점으로 매칭.

**Dify Workflow 노드 구성** (`sonar-analyzer-workflow.yaml`):

```
start
  ↓ (10 inputs: sonar_issue_key, code_snippet, kb_query, enclosing_function, commit_sha, ...)
knowledge_retrieval   [code-context-kb dataset, retrieval_mode=multiple, top_k=6, hybrid]
  ↓ (result — 검색된 청크 top-6)
llm_analyzer          [gemma4:e4b, temperature=0.1, max_tokens=2048]
  ↓ (JSON text)
parameter_extractor   [8 필드 추출: title/labels/impact/fix/classification/fp_reason/confidence/diff]
  ↓
end                   [8 outputs 반환]
```

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

00 체인의 Stage 5 가 이 파일을 읽어 `p3_summary` 로 집계합니다.

---

### 8.5 `04-AI평가` — 골든 데이터셋 기반 LLM 응답 평가 (선택)

**역할**: 사내에서 운영 중인 LLM 서비스(챗봇, API 등)의 응답 품질이 기대 수준을 유지하는지 Golden Dataset 기반으로 정기 검증합니다. P1~P3 의 코드 품질 분석과는 **별개 목적** 의 독립 도구입니다.

**성과**: 운영 중인 LLM 서비스가 일정 기간마다 자동 평가되며, 회귀(품질 저하)가 발생하면 즉시 파악할 수 있습니다. 평가 결과는 HTML/JSON 리포트로 보관됩니다.

#### 8.5.1 구성

```
eval_runner/
├── runner.py              # DeepEval 엔트리포인트
├── adapters/
│   ├── ollama_adapter.py      # TARGET_MODE=local_ollama_wrapper → 호스트 Ollama 직접 호출
│   ├── browser_adapter.py     # TARGET_TYPE=ui_chat → Playwright 로 웹 UI 조작
│   ├── openai_adapter.py      # (placeholder)
│   └── gemini_adapter.py      # (placeholder)
├── metrics/
│   ├── answer_relevancy.py    # DeepEval AnswerRelevancy (0~1 점수)
│   └── llm_judge.py           # Ollama judge 모델로 pass/fail 분류
└── datasets/
    └── golden_<project>.csv   # 질문 ↔ 기대답변 쌍 (수동 작성)
```

#### 8.5.2 주요 파라미터

| 이름 | 예시 | 의미 |
|------|------|------|
| `TARGET_TYPE` | `http_api` / `ui_chat` | 평가 대상이 API 엔드포인트인가, 웹 UI 챗봇인가 |
| `TARGET_MODE` | `local_ollama_wrapper` / `direct` / (미구현 openai/gemini) | 어떤 어댑터로 호출하는가 |
| `TARGET_URL` | `http://api.example.com/chat` | 대상 URL (TARGET_TYPE 에 따라 해석 다름) |
| `JUDGE_MODEL` | `qwen3-coder:30b` | LLM-as-Judge 로 쓸 Ollama 모델 이름 |
| `GOLDEN_DATASET` | `datasets/golden_myproject.csv` | 질문-기대답변 시험지 경로 |
| `BUILD_NUMBER` | Jenkins 자동 | 리포트 디렉터리 suffix |

#### 8.5.3 처리 흐름

```
1. Golden Dataset 로드 (CSV: question, expected_answer)
2. 각 행에 대해:
   ├─ adapter 로 TARGET 에 question 전송 → actual_answer 수집
   ├─ metrics/answer_relevancy.py — 의미 유사도 0~1 점수
   └─ metrics/llm_judge.py — "actual 이 expected 와 같은 의도인가" pass/fail
3. 결과를 /var/knowledges/eval/reports/build-${BUILD_NUMBER}/{report.json,report.html} 로 저장
4. archiveArtifacts 로 Jenkins 에 보관
```

#### 8.5.4 UI 자동화 (ui_chat)

통합 이미지에 **Chromium + Playwright** 가 사전 설치되어 있어 headless 모드 웹 자동화가 즉시 동작:

- `TARGET_TYPE=ui_chat` + `TARGET_URL=https://chatbot.example.com` 지정 시
- `browser_adapter.py` 가 페이지 열기 → 입력창 찾기 → question 타이핑 → 응답 수신까지 자동화

**이 파이프라인은 P1~P3 와 독립** 입니다. 본 문서는 주로 P1~P3 의 코드 품질 파이프라인을 다루며, P4 상세 운영 가이드는 `eval_runner/` 내부 문서를 참고하세요.

---

## 9. GitLab Issue 결과물 읽는 법

P3 는 **사실 정보는 creator 가 deterministic 렌더**, **해석 필요 부분만 LLM** 이 작성합니다. 해결자가 30초 내에 "어디·무엇·왜·어떻게" 를 파악할 수 있게 설계:

```
> **TL;DR** — `src/auth.py:21` `login` 함수 · Specify an exception class...

### 📍 위치 (테이블)
파일(클릭→GitLab 라인) · 함수(라인 범위) · Rule · Severity · Commit(클릭→GitLab 커밋)

### 🔴 문제 코드 (이슈 라인 ±10줄, '>>' 마커)

### ✅ 수정 제안 (LLM — 빈 값이면 섹션 생략)

### 💡 Suggested Diff (unified diff, 기계 적용 가능할 때만)

### 📊 영향 분석 (LLM — RAG 가 찾은 호출 관계 기반)
"이 함수는 src/session.py::check_session 에서 호출되므로..."

### 🧭 Affected Locations (clustering 으로 묶인 유사 이슈)

### 📖 Rule 상세 (<details> 접기)

### 🔗 링크 (Sonar · GitLab blob · GitLab commit)

---
_commit: `e38bd123` (full scan) · sonar: http://localhost:29000/..._
```

**라벨**: `severity:CRITICAL`, `classification:true_positive`, `confidence:high`, LLM 도메인 라벨들, 오탐 전이 실패 시 `fp_transition_failed`, skip_llm 시 `auto_template:true`.

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

## 11. 자동 프로비저닝 내부 동작

`scripts/provision.sh` 가 최초 기동 시 자동 수행 (멱등, `/data/.provision/*.ok` 마커로 재실행 안전).

### 11.1 자동으로 처리되는 것

| 대상 | 세부 작업 | 관련 함수 |
|------|---------|----------|
| **Dify 관리자** | 초기 setup (이메일+비밀번호) → 로그인 (base64 password + cookie jar + X-CSRF-Token) | `dify_setup`, `dify_login` |
| **Dify Ollama 플러그인** | `/opt/dify-assets/langgenius-ollama-*.difypkg` 를 `/plugin/upload/pkg` → `/plugin/install/pkg` 로 설치 | `dify_install_ollama_plugin` |
| **Dify LLM provider** | Ollama provider 를 `gemma4:e4b` 모델명으로 등록 (`http://host.docker.internal:11434`) | `dify_register_ollama_provider` |
| **Dify embedding** | Ollama embedding provider 를 `bge-m3` 로 등록 | `dify_register_ollama_embedding` |
| **Dify 기본 모델** | workspace default model 설정 (llm=gemma4:e4b, embedding=bge-m3) — **이게 없으면 high_quality Dataset 생성 시 400 에러** | `dify_set_default_models` |
| **Dify Dataset** | `code-context-kb` 생성 (high_quality + bge-m3 + hybrid_search) | `dify_create_dataset` |
| **Dify Workflow** | `sonar-analyzer-workflow.yaml` 을 `/console/api/apps/imports` 로 import → Dataset ID 주입 → `/workflows/publish` | `dify_import_workflow` + `dify_patch_workflow_dataset_id` + `dify_publish_workflow` |
| **Dify API 키 2종** | Dataset API key (`dataset-*`) + App API key (Sonar Analyzer Workflow 용) | `dify_create_dataset_key` + `dify_create_app_key` |
| **GitLab root PAT** | reconfigure 대기 → oauth password grant → `/users/1/personal_access_tokens` (유효 364일) | `gitlab_wait_ready` + `gitlab_issue_root_pat` |
| **SonarQube** | ready 대기 → admin 비밀번호 변경 (`admin` → `SONAR_ADMIN_NEW_PASSWORD`) → user token `jenkins-auto` 발급 | `sonar_wait_ready` + `sonar_change_admin_password` + `sonar_issue_token` |
| **Jenkins Credentials 5종** | `dify-dataset-id`, `dify-knowledge-key`, `dify-workflow-key`, `gitlab-pat`, `sonarqube-token` | `jenkins_upsert_string_credential` |
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

### 12.3 `00` Job 이 "is not parameterized" HTTP 400

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

### 12.10 로그 위치

```bash
docker logs ttc-allinone | grep "\[provision\]"
docker exec ttc-allinone cat /data/logs/jenkins.log | tail -50
docker exec ttc-allinone cat /data/logs/sonarqube.log | tail -50
docker exec ttc-allinone cat /data/logs/dify-api.log | tail -50
docker logs ttc-gitlab | tail -20
```

Job 콘솔:
- http://localhost:28080/job/00-코드-분석-체인/lastBuild/console
- http://localhost:28080/job/03-정적분석-결과분석-이슈등록/lastBuild/console

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
│   ├── repo_context_builder.py             # P1 AST 청킹
│   ├── contextual_enricher.py              # P1 gemma4 요약 prepend
│   ├── doc_processor.py                    # P1 Dify 업로드 + kb_manifest
│   ├── sonar_issue_exporter.py             # P3(1) Sonar API + 보강
│   ├── dify_sonar_issue_analyzer.py        # P3(2) Dify Workflow 호출
│   └── gitlab_issue_creator.py             # P3(3) GitLab Issue + Dual-path FP
│
├── eval_runner/                            # 파이프라인 4 (DeepEval + Playwright)
│
├── jenkinsfiles/                           # 5 개 Pipeline 정의
│   ├── 00 코드 분석 체인.jenkinsPipeline
│   ├── 01 코드 사전학습.jenkinsPipeline
│   ├── 02 코드 정적분석.jenkinsPipeline
│   ├── 03 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline
│   └── 04 AI평가.jenkinsPipeline
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

**주의**: 이 단일 머신 테스트로 검증이 끝나면 **반드시 §3~§6 의 에어갭 절차로 실제 운영 환경에 재배포** 하세요. 개발 머신의 `~/ttc-allinone-data` 나 Docker 이미지를 그대로 옮기는 것은 권장하지 않습니다 (서비스별 런타임 상태가 경로·호스트명에 종속).

---

## 확장 포인트

- Dify workflow 수정 → `scripts/dify-assets/sonar-analyzer-workflow.yaml` → 이미지 재빌드 (또는 Dify Studio 에서 runtime 수정 — 재기동 시 YAML 이 덮어씀).
- severity 라우팅 변경 → `pipeline-scripts/sonar_issue_exporter.py` 의 `_SEVERITY_ROUTING`.
- 새 언어 추가 → `pipeline-scripts/repo_context_builder.py` 의 `LANG_CONFIG` 에 tree-sitter grammar 추가.

**이슈·PR 환영**. 개선 아이디어:
- GitLab 프로젝트 자동 생성 provisioning (현재 수동)
- Ollama 모델 자동 sync 스크립트 (오프라인 업데이트 파이프라인)
- Dify 1.14+ 업그레이드 시 workflow YAML 스키마 검증 자동화
