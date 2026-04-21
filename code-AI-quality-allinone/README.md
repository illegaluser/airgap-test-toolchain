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

**시나리오**: 폐쇄망에 있는 GitLab 의 소스 코드를 자동으로 품질 분석하고 싶다. 인터넷 없이도.

**제공 기능**:
1. **Jenkins Pipeline 00 코드 분석 체인** 을 1회 클릭하면
2. 자동으로 (a) 커밋 SHA 해석 → (b) tree-sitter 로 **함수 단위 청킹** → Ollama bge-m3 임베딩 → Dify Knowledge Base 적재 → (c) **SonarQube 스캔** → (d) 각 Sonar 이슈를 Dify Workflow 로 **LLM 분석** (멀티쿼리 RAG + severity 라우팅) → (e) **GitLab Issue 자동 등록** (위치·코드·수정제안·영향분석·링크 포함) → (f) LLM 이 오탐 판정한 건은 Sonar 자동 전이 + 실패 시 `classification:false_positive` 라벨로 GitLab Issue 생성 (Dual-path).
3. **모든 LLM 추론은 호스트 Ollama** 가 담당 — 외부 API 의존 없음.

**구성 요소** (단일 컨테이너 + GitLab 별도):

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
│  ① 온라인 준비 머신  (인터넷 필요)                              │
│  ────────────────────────────────                              │
│  · 이 레포 clone                                                │
│  · Docker 베이스 이미지 5종 pull (Dockerfile 의 FROM)           │
│  · scripts/download-plugins.sh (Jenkins + Dify 플러그인)        │
│  · scripts/offline-prefetch.sh (통합 이미지 빌드 + tarball)      │
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

운영 머신에 그대로 옮길 수 있도록 준비 머신에 설치해서 모델을 받고, `~/.ollama/models/` 디렉터리 통째로 반출합니다.

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

### 3.6 Step 5: Docker 베이스 이미지 pre-pull (선택, 권장)

`offline-prefetch.sh` 는 빌드 중에 베이스 이미지를 자동 pull 하지만, 네트워크가 느리면 미리 받아두는 게 안정적입니다.

```bash
# amd64 운영 용
PLATFORM=linux/amd64
for img in \
    langgenius/dify-api:1.13.3 \
    langgenius/dify-web:1.13.3 \
    langgenius/dify-plugin-daemon:0.5.3-local \
    sonarqube:10.7.0-community \
    jenkins/jenkins:lts-jdk21 \
    gitlab/gitlab-ce:17.4.2-ce.0 ; do
    docker pull --platform "$PLATFORM" "$img"
done

# arm64 운영 용 (GitLab 만 교체)
PLATFORM=linux/arm64
for img in \
    langgenius/dify-api:1.13.3 \
    langgenius/dify-web:1.13.3 \
    langgenius/dify-plugin-daemon:0.5.3-local \
    sonarqube:10.7.0-community \
    jenkins/jenkins:lts-jdk21 \
    yrzr/gitlab-ce-arm64v8:17.4.2-ce.0 ; do
    docker pull --platform "$PLATFORM" "$img"
done
```

### 3.7 Step 6: 통합 이미지 빌드 + tarball 산출

```bash
# arm64 운영용 (macOS Apple Silicon)
bash scripts/offline-prefetch.sh --arch arm64

# amd64 운영용 (WSL2, x86 Linux)
bash scripts/offline-prefetch.sh --arch amd64
```

산출물:

```
offline-assets/<arch>/
├── ttc-allinone-<arch>-dev.tar.gz          (~10 GB, gzip 압축)
├── ttc-allinone-<arch>-dev.meta            (sha256 + built_at)
├── gitlab-gitlab-ce-17.4.2-ce.0-<arch>.tar.gz    (~1.5 GB)
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

사용자 클릭 1회로 P1 → P2 → P3 → Chain Summary 까지 자동 실행.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `REPO_URL` | `http://gitlab:80/root/dscore-ttc-sample.git` | 컨테이너 내부 이름 `gitlab` |
| `BRANCH` | `main` | 분석 브랜치 |
| `ANALYSIS_MODE` | `full` | `full` = KB 강제 재빌드 · `commit` = manifest 일치 시 재사용 |
| `COMMIT_SHA` | `(빈 값)` | 지정 시 그 커밋 고정, 빈 값이면 BRANCH HEAD |

### 8.2 `01-코드-사전학습` (P1)

1. Git clone → `/var/knowledges/codes/<repo>`
2. `repo_context_builder.py` — tree-sitter AST 청킹 (py/java/ts/tsx/js), 청크별 `path`/`symbol`/`lines`/`callers`/`callees`
3. `contextual_enricher.py` — gemma4:e4b 로 청크 요약 prepend (선택, 기본 ON)
4. `doc_processor.py` — Dify Dataset `code-context-kb` 에 bge-m3 임베딩으로 업로드
5. `/data/kb_manifest.json` 기록 (commit_sha 포함)

### 8.3 `02-코드-정적분석` (P2)

1. **(0) KB Bootstrap Guard** — full 모드 + 체인 경로 → manifest 검증만 (불일치 시 fail loud). commit 모드 + 단독 실행 + manifest 불일치 → P1 자동 트리거.
2. Git checkout `${COMMIT_SHA}` 로 고정
3. Node.js 준비 (SonarJS 용)
4. SonarScanner 실행 → Sonar 서버에 리포트 전송

### 8.4 `03-정적분석-결과분석-이슈등록` (P3)

가장 복잡한 파이프라인. 3 스테이지:

#### (1) Export Sonar Issues — `sonar_issue_exporter.py`

Sonar API 수집 + 대대적 보강:

| 필드 | 설명 |
|------|------|
| `enclosing_function` / `enclosing_lines` | tree-sitter 로 이슈 라인을 포함하는 함수 추출 |
| `git_context` | `git blame -L` + `git log` 요약 3줄 |
| `direct_callers` | P1 JSONL 에서 callgraph 역인덱스 → 최대 10 caller |
| `cluster_key` | sha1(rule + function + dir) 묶기용 |
| `affected_locations` | clustering 으로 묶인 나머지 이슈 (대표에 부착) |
| `judge_model` / `skip_llm` | severity 라우팅 (BLOCKER/CRITICAL→qwen3, MAJOR→gemma4, 그 외→skip) |

**diff-mode** — `--mode incremental` 시 `last_scan.json` 과 symmetric diff.

#### (2) Analyze by Dify Workflow — `dify_sonar_issue_analyzer.py`

각 이슈를 Dify `Sonar Issue Analyzer` 에 전달:

- **Multi-query kb_query**: 코드창 + function + path + rule name 4줄 → RAG 적중률 향상.
- **skip_llm 분기**: MINOR/INFO 는 Dify 호출 생략 + 템플릿 응답.
- **LLM 출력 8필드**: `title`, `labels`, `impact_analysis_markdown`, `suggested_fix_markdown`, `classification` (true_positive|false_positive|wont_fix), `fp_reason`, `confidence`, `suggested_diff`.

#### (3) Create GitLab Issues — `gitlab_issue_creator.py`

- **Dual-path FP**: `classification == "false_positive"` → Sonar 전이 시도 → 성공 시 GitLab Issue skip / 실패 시 라벨링 후 생성.
- **Dedup**: 같은 Sonar key 기존 Issue 있으면 skip.
- **Deterministic 본문 렌더** — 다음 섹션 참조.

### 8.5 `04-AI평가` (선택)

DeepEval + Ollama judge + Playwright. UI 자동화 내장. 본 문서 범위 외.

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

`scripts/provision.sh` 가 최초 기동 시 자동 수행 (멱등, `/data/.provision/*.ok` 마커):

| 대상 | 작업 |
|------|------|
| **Dify** | 관리자 setup → 로그인 → Ollama 플러그인 설치 (`.difypkg`) → Ollama provider 등록 (gemma4:e4b) → embedding 등록 (bge-m3) → 기본 모델 설정 → `code-context-kb` Dataset 생성 (high_quality + bge-m3 + hybrid_search) → `Sonar Issue Analyzer` Workflow import → Workflow publish → Dataset/App API 키 2종 발급 |
| **GitLab** | reconfigure 대기 → oauth password grant → root PAT 발급 |
| **SonarQube** | ready 대기 → admin 비밀번호 변경 → user token `jenkins-auto` 발급 |
| **Jenkins** | 5 Credentials 주입 → SonarQube server + SonarScanner tool Groovy 등록 → 5 Pipeline Job 등록 → Jenkinsfile 의 `GITLAB_PAT = ''` → `credentials('gitlab-pat')` sed 치환 |

### 자동화되지 않는 잔존 작업

- **GitLab 프로젝트 생성 + push** — §7.1~7.2 참고 (팀 정책 차이).
- **첫 Job의 Parameter discovery** — Declarative pipeline 특성상 최초 1회는 "Build Now" 실패 후 "Build with Parameters" 가능.

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
