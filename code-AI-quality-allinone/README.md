# TTC 5-Pipeline All-in-One — 폐쇄망 코드분석·AI평가 통합 스택

> Jenkins · Dify · SonarQube · GitLab · Postgres · Redis · Qdrant · Meilisearch · FalkorDB ·
> retrieve-svc 를 단일 Docker 이미지에 통합한 *에어갭 (인터넷 차단) 환경* 전용 스택.
> 외부망에서 1회 빌드 → tarball 반입 → 폐쇄망에서 `docker load` + `docker compose up` 으로 끝.

> **본 문서를 처음 읽으시는 분께** — 컴퓨터를 켜고 명령어를 한 번도 쳐본 적 없는 분도 따라할 수
> 있도록 작성했습니다. 모르는 단어가 나오면 *그대로 진행* 하세요. 각 단계는 (1) **무엇을 하는지**,
> (2) **명령어**, (3) **정상 결과 신호** 3 가지를 모두 안내합니다. 막히는 곳이 생기면 [§6.1
> 트러블슈팅](#61-자주-발생하는-문제-트러블슈팅) 을 먼저 펼쳐 보세요.

---

## 1. 문서 존재 목적

본 문서는 **폐쇄망 SI 환경에서 본 스택을 처음 받아 빌드·구동·운영할 수 있도록** 단계별로 안내합니다.
독자 대상은 *개발자가 아니어도* 따라갈 수 있는 수준이며, 각 단계의 *왜·무엇·어떻게* 가 모두 한 문서에
모여 있습니다.

본 스택이 제공하는 것:

- **5 개 Jenkins 파이프라인** — 1: 코드 분석 체인 / 2: 코드 사전학습 / 3: 코드 정적분석 / 4: 결과분석+이슈등록 / 5: AI 평가
- **GitLab 이슈 자동 등록** — Sonar 가 찾은 이슈를 LLM 으로 분석하고 PM 친화적 이슈 본문으로 GitLab 에 자동 등록
- **AI 응답 품질 자동 평가** — Golden Dataset 으로 운영 중 AI 서비스의 회귀를 자동 채점
- **외부 API 호출 0** — 모든 LLM 추론은 호스트 Ollama 에서. 외부 클라우드 (OpenAI / Claude / Gemini 등) 호출 없음

본 문서가 아닌 곳:

- *왜 이런 설계인가* (결정 로그) → [docs/PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md](docs/PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md)
- *언제·어떻게 굴리는가* (운영 일정·게이트) → [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md)
- *PM 시점에서 측정 결과를 어떻게 해석하는가* → [docs/IMPLEMENTATION_RATE_PM_GUIDE.md](docs/IMPLEMENTATION_RATE_PM_GUIDE.md)

---

## 2. 필수 사전 준비물

> **이 섹션의 흐름** — §2.1 (처음이라면 OS 자체 셋업) → §2.2 (머신 두 종류 구분) → §2.3 / §2.4
> (머신별 호스트 도구 설치) → §2.5 (머신별 차이).

### 2.1 처음이라면 — OS 환경 준비 (한 번만)

이 절은 *컴퓨터를 새로 받았거나 개발 환경이 처음인 분* 을 위한 가이드입니다. 이미 Docker /
WSL2 / Xcode CLT 가 깔려 있다면 [§2.2](#22-머신-두-종류) 로 건너뛰세요.

#### 2.1.1 본인 OS 확인

| OS | 어떻게 알 수 있나 | 다음 단계 |
|---|---|---|
| **macOS** | 화면 좌상단 사과 🍎 메뉴 | §2.1.2 |
| **Windows** | 시작 메뉴 / Windows 로고 | §2.1.3 (WSL2 필수) |
| **Linux** (Ubuntu 등) | 별도 안내 없으면 보통 시작 메뉴에 "Terminal" | §2.1.4 |

#### 2.1.2 (macOS 인 경우) Xcode Command Line Tools

🎯 **무엇을 하는가**: macOS 에 `git` / 컴파일러 / 기본 개발 도구가 들어 있는 *Xcode CLT* 를 설치한다.
*Homebrew 와 Python·git·curl 등은 §2.3 의 `setup-host.sh` 가 자동 처리* 하므로 여기서는 다루지 않음.

**(1) 터미널 열기**: ⌘+스페이스 → "터미널" / "Terminal" 입력 → Enter

**(2) Xcode CLT 설치**:

```bash
xcode-select --install
```

✅ **정상 신호**: 팝업창이 뜨고 "설치" 버튼 → 약 5~15분 후 자동 종료. 이미 깔려있으면
`xcode-select: error: command line tools are already installed` 메시지 (이것도 정상).

> Homebrew 가 없어도 OK — `setup-host.sh` 가 §2.3 에서 자동 설치합니다.

#### 2.1.3 (Windows 인 경우) WSL2 + Ubuntu 설치

🎯 **무엇을 하는가**: Windows 에서 *Linux 환경* 을 사용할 수 있게 해주는 WSL2 (Windows Subsystem
for Linux 2) 와 Ubuntu 배포판을 설치한다. **본 스택은 Windows 네이티브가 아니라 WSL2 안에서
구동된다** — 이 단계 누락 시 진행 불가.

**(1) PowerShell 관리자 모드로 실행**:
- 시작 메뉴 검색창에 "PowerShell" → **마우스 오른쪽 클릭** → "관리자 권한으로 실행" → "예"

**(2) WSL2 + Ubuntu 한 줄 설치**:

```powershell
wsl --install -d Ubuntu
```

⏱ **소요 시간**: 약 5~15분 (Ubuntu 다운로드 + 압축 해제). 진행 막대가 보입니다.

✅ **정상 신호**: 설치 끝에 *"재부팅이 필요합니다"* 메시지 → 컴퓨터 재시작.

**(3) 재부팅 후 Ubuntu 첫 실행** (자동 실행 또는 시작 메뉴 → "Ubuntu" 클릭):
- *"Enter new UNIX username:"* → 영문 소문자 사용자명 (예: `dscore`) 입력
- *"New password:"* → 비밀번호 설정 (입력해도 화면에 *안 보입니다 — 정상*)
- 비밀번호는 이후 `sudo` 명령에서 자주 묻습니다. **잊지 마세요**.

✅ **정상 신호**: `dscore@hostname:~$` 같은 프롬프트가 보이면 WSL2 진입 성공.

**(4) WSL2 메모리 한도 설정** (B 머신 운영 시 필수):

PowerShell 또는 메모장에서 `%USERPROFILE%\.wslconfig` 파일 생성:

```ini
[wsl2]
memory=56GB
processors=12
swap=0
```

> 본인 PC RAM 의 70~80% 정도로 설정. 32GB RAM 머신이면 `memory=24GB`, 64GB 면 `memory=56GB`.
> 설정 적용: PowerShell 관리자 모드에서 `wsl --shutdown` 실행 후 Ubuntu 재실행.

#### 2.1.4 (Linux 인 경우) Docker 그룹 권한

🎯 **무엇을 하는가**: 매번 `sudo docker` 를 안 치도록 본인 사용자를 docker 그룹에 추가.

```bash
sudo usermod -aG docker $USER
newgrp docker
docker version    # sudo 없이 응답하면 OK
```

#### 2.1.5 Docker Desktop 설치 + 첫 실행 (양 머신 공통)

🎯 **무엇을 하는가**: 본 스택의 컨테이너를 돌릴 *Docker Desktop* 을 설치하고 첫 실행 시 메모리를
24GB 이상으로 할당.

**(1) 다운로드**:
- macOS: https://www.docker.com/products/docker-desktop/ → "Download for Mac (Apple Silicon)"
- Windows: https://www.docker.com/products/docker-desktop/ → "Download for Windows"

**(2) 설치 실행**:
- macOS: 다운로드한 `Docker.dmg` 더블클릭 → 응용프로그램 폴더로 드래그 → Docker.app 실행
- Windows: `Docker Desktop Installer.exe` 더블클릭 → 마법사 따라가기 → ⚠️ "Use WSL 2 instead of Hyper-V" *반드시 체크* → 재부팅

**(3) 첫 실행 시 화면 안내**:
- *"Docker Subscription Service Agreement"* → "Accept"
- *"Sign in"* / *"Sign up"* → **"Skip"** 또는 *"Continue without signing in"* 클릭 (계정 불필요)
- *"Welcome survey"* → "Skip"
- 메뉴바 (macOS) 또는 트레이 (Windows) 에 🐳 고래 아이콘이 나타나면 정상

**(4) 메모리 24GB+ 할당**:
- 🐳 아이콘 클릭 → "Settings" (또는 ⚙️) → "Resources" → "Advanced"
- "Memory" 슬라이더를 **24GB 이상** 으로 (운영 머신 권장 — 빌드만 할 때는 12GB OK)
- "Apply & Restart" 클릭

✅ **정상 신호**: 터미널에서 `docker version` 실행 시 Client + Server 두 블록 모두 응답.

#### 2.1.6 Ollama 설치 (양 OS — UI 또는 명령행)

🎯 **무엇을 하는가**: 본 스택의 LLM 추론을 담당할 Ollama 를 호스트에 설치. *Docker Desktop 과
함께 본 README 의 "사용자가 직접 설치하는 두 가지" 중 하나*. 설치 방식은 OS 별:

**macOS (둘 중 하나 선택)**:

| 방법 | 명령 / 절차 | 비고 |
|---|---|---|
| UI 설치 (권장) | https://ollama.com/download/mac → `Ollama-darwin.zip` 다운로드 → 압축 해제 → `Ollama.app` 을 응용프로그램 폴더로 드래그 → 실행 | 메뉴바에 🦙 아이콘 |
| brew 명령행 | `brew install ollama` (Homebrew 가 §2.3 에서 자동 설치된 후 실행 가능) | brew 가 있으면 한 줄 |

**Windows + WSL2 — ⭐ 권장 방식 (선택 A)**:

Windows host 에 GUI 로 설치하면 *충분합니다*. WSL2 안에 CLI 따로 설치 안 해도 OK.

| 단계 | 절차 |
|---|---|
| 1. GUI 설치 | https://ollama.com/download/windows → `OllamaSetup.exe` 다운로드 → 더블클릭 → 설치 |
| 2. 트레이 확인 | 작업 표시줄 우측 트레이에 🦙 아이콘 (data 자동 기동) |
| 3. 모델 pull 위치 | **Windows PowerShell** 에서 (WSL2 가 아님): `ollama pull gemma4:e4b` 등 |
| 4. WSL2 셸 검증 | WSL2 안에서도 daemon 은 호출 가능: `curl http://localhost:11434/api/tags` |

> **왜 PowerShell 에서?** — Windows GUI 설치 시 ollama 명령은 Windows host PATH 에만 등록됨 (WSL2
> Ubuntu PATH 와 분리됨). WSL2 셸에서는 `ollama` 명령이 *없지만* daemon 호출 (port 11434) 은
> WSL2 ↔ Windows localhost 공유로 정상 동작. 본 스택의 빌드 스크립트도 이 케이스를 자동 감지해
> 모델 다운로드 단계만 skip 하고 빌드를 계속합니다.

> **모델 저장 위치** — `C:\Users\<Windows유저명>\.ollama\models\`
> (WSL2 에서 접근 시 `/mnt/c/Users/<Windows유저명>/.ollama/models/`).
> §3.4 의 모델 export 단계에서 이 경로를 참조합니다.

**Windows + WSL2 — 선택 B (모든 명령을 WSL2 셸에서 통일하고 싶을 때)**:

| 단계 | 절차 |
|---|---|
| WSL2 안에 CLI + daemon 별도 설치 | `setup-host.sh --install-ollama` 또는 직접 `curl -fsSL https://ollama.com/install.sh \| sh` |
| 모델 저장 위치 | WSL2 home `~/.ollama/models/` (Windows 측 daemon 과 *별개* 의 namespace) |
| 단점 | 모델을 두 번 받게 됨 (Windows + WSL2). 디스크 22+ GB 추가 |

> 일반적으로 **선택 A 가 권장** 합니다 — Windows GUI Ollama 의 모델 관리 UI 도 그대로 활용 가능.

**Linux (네이티브)**:

| 방법 | 명령 |
|---|---|
| 자동 (`setup-host.sh --install-ollama` 가 처리) | §2.3 |
| 수동 | `curl -fsSL https://ollama.com/install.sh \| sh` |

✅ **정상 신호** (모든 OS 공통 — daemon 응답이 핵심):

```bash
# daemon 응답 확인 (이게 가장 중요)
curl -sf http://localhost:11434/api/tags
# → {"models":[]} 또는 모델 목록 JSON

# CLI 도 확인 (macOS GUI 또는 WSL2 native 설치 시 응답, Windows GUI + WSL2 셸 면 'not found' 가 정상)
ollama --version 2>/dev/null || echo "CLI 없음 — daemon 응답이 OK 면 진행 가능 (선택 A)"
```

> Ollama 가 *완전히 미설치* 인 상태로 §2.3 의 setup-host.sh 를 실행하면 명확한 에러 메시지와 함께 종료됩니다.
> Linux/WSL2 라면 `--install-ollama` 플래그로 자동 설치하도록 위임할 수도 있습니다.

#### 2.1.7 셸 (터미널) 여는 법 — 앞으로 모든 명령어 입력 위치

| OS | 어떻게 열기 | 프롬프트 예시 |
|---|---|---|
| macOS | ⌘+스페이스 → "Terminal" → Enter | `dscore@MacBook ~ %` |
| Windows | 시작 메뉴 → "Ubuntu" 클릭 (WSL2 진입) | `dscore@hostname:~$` |
| Linux | 시작 메뉴 또는 Ctrl+Alt+T → "Terminal" | `dscore@host:~$` |

> 🔑 **본 문서의 모든 `bash` 명령은 위 셸에 *복사·붙여넣기* 후 Enter** 로 실행합니다. Windows 라면
> *반드시 WSL2 Ubuntu 셸* (PowerShell 아님). 마우스 오른쪽 클릭 또는 Ctrl+Shift+V 로 붙여넣기.

### 2.2 머신 두 종류

| 머신 | 용도 | 인터넷 |
|---|---|---|
| **A 머신** (외부망 빌드 머신) | 이미지 빌드, 자산 다운로드, tarball 산출 — 최초 1회만 | 필요 |
| **B 머신** (폐쇄망 운영 머신) | 실제 운영 — 빌드된 tarball 을 받아 `docker compose up` | 차단됨 |

> 두 머신은 **반드시 같은 CPU 아키텍처**여야 합니다 (arm64 ↔ arm64, amd64 ↔ amd64).
> 본 스택은 1차 검증 머신을 **WSL2 / Intel + RTX 4070 (amd64)** 로 두고, 2차 이식 머신을
> **macOS / M4 Pro (arm64)** 로 운영합니다 — 양 머신 동일 모델·동일 이미지·동일 supervisor 구성.

### 2.3 A 머신 (외부망 빌드용) — 자동 설치 스크립트 한 번

§2.1 에서 OS 부트스트랩 (Docker Desktop · Ollama · WSL2 / Xcode CLT) 이 끝났다면, 이제부터는
**터미널 한 줄로 모든 호스트 도구를 자동 설치** 합니다.

#### ⭐ 권장 — 한 줄 명령

```bash
# 레포 clone 후 진입
git clone <이 레포 URL> airgap-test-toolchain
cd airgap-test-toolchain/code-AI-quality-allinone

# === 본 명령 1줄로 모든 호스트 도구 + 자산 다운로드 + PATH 등록 ===
bash scripts/setup-host.sh --all
```

⏱ **소요 시간**: 호스트 패키지 설치 5~10분 + 자산 다운로드 30분~3시간 (회선 속도 의존, gemma4:e4b 가 9.6GB).

🎯 **무엇을 하는가** — `setup-host.sh` 가 자동으로:

| 단계 | 동작 |
|---|---|
| OS 자동 감지 | macOS / WSL2 / Linux 분기 (`uname -s` + `/proc/version` 의 `microsoft`) |
| Docker 검증 | 명령 + daemon 응답 — 없으면 설치 안내 후 명확한 에러로 종료 (UI 설치 필요해서 *스크립트가 안 함*) |
| Ollama 검증 | 없으면 설치 안내 (macOS) 또는 `--install-ollama` 시 자동 (Linux/WSL2) |
| (macOS) | Homebrew 자동 설치 → `brew install python@3.12 git curl` |
| (WSL2/Linux) | `sudo apt install python3 python3-pip python3-venv git curl bash unzip build-essential` |
| huggingface-cli | `--user` 모드 설치 + `~/.bashrc` 또는 `~/.zshrc` 에 PATH 자동 등록 |
| `--all` 시 | `download-rag-bundle.sh` 호출 → 5 종 자산 일괄 다운로드 (§3.2) |

✅ **정상 신호**: 마지막에 `[setup-host] ✓ 호스트 사전 설치 완료` 메시지 + 자산 다운로드 결과 표.

📦 **결과**:
- 호스트에 Python 3.11+ / pip / huggingface-cli / git / curl 모두 동작
- 빌드 컨텍스트 (`offline-assets/`, `jenkins-plugins/`, `dify-plugins/`) 가 모두 채워짐
- `~/.ollama/models/` 에 `gemma4:e4b` + `qwen3-embedding:0.6b` + `bge-m3` 적재됨
- → 바로 §3.3 의 이미지 빌드 (`bash scripts/build-{wsl2,mac}.sh`) 진행 가능

#### 다른 사용 방식

```bash
# 호스트 패키지만 설치 (자산 다운로드는 §3.2 에서 별도)
bash scripts/setup-host.sh

# Ollama 도 자동 (Linux/WSL2 한정)
bash scripts/setup-host.sh --install-ollama

# 검증만 (변경 없이 현재 상태 점검)
bash scripts/setup-host.sh --check
```

#### ❌ 자주 막히는 곳

- *`docker: command not found`* → §2.1.5 Docker Desktop 설치 미완료 또는 WSL2 backend 미활성화. 스크립트가 명확한 에러 메시지로 안내하므로 따라가면 됨.
- *`ollama: command not found`* → §2.1.6 Ollama 설치 미완료. macOS 는 UI/.dmg 또는 `brew install ollama` (Homebrew 설치 후), Linux/WSL2 는 스크립트 재실행 시 `--install-ollama` 플래그 추가.
- *huggingface-cli rate limit (HTTP 429)* → 자산 다운로드 단계에서 발생. `huggingface-cli login` 으로 무료 계정 토큰 등록 후 재실행. [§6.1.3](#613-huggingface-cli-rate-limit-403--429) 참조.
- *macOS 첫 brew 설치 시 관리자 비밀번호 요구* → Mac 로그인 비밀번호 입력 (입력 시 화면 미표시 — 정상).

#### 부록 — 수동 설치 항목 (학습·디버깅용)

스크립트가 안 통하는 환경이거나 *무엇이 설치되는지* 직접 확인하려는 경우:

| # | 도구 | 용도 | 최소 버전 | 수동 설치 명령 |
|---|---|---|---|---|
| 1 | Docker Desktop | 통합 이미지 빌드 / tarball 산출 | 24.x+ | §2.1.5 (UI) |
| 2 | Ollama | 모델 pull / 호스트 LLM | 0.4.x+ | §2.1.6 (UI 또는 brew/curl) |
| 3 | Python 3 | huggingface-cli 실행 | 3.11+ | macOS: `brew install python@3.12` · WSL2: `sudo apt install -y python3 python3-pip python3-venv` |
| 4 | huggingface_hub[cli] | bge-reranker weight 다운로드 | 0.24+ | `python3 -m pip install --user "huggingface_hub[cli]"` |
| 5 | Git | 레포 clone | 2.x+ | macOS: Xcode CLT (§2.1.2) · WSL2: `sudo apt install -y git` |
| 6 | curl | Meilisearch binary 다운로드 | 표준 | OS 표준 |
| 7 | bash | 빌드/run 스크립트 | 4.x+ | macOS/Linux/WSL2 표준 |

수동으로 했다면 검증:

> ⚠️ **setup-host.sh 직후 같은 셸에서는 `huggingface-cli` 가 안 보일 수 있음** —
> setup-host.sh 가 `~/.bashrc` 에 PATH 를 추가하지만, 기존 셸은 자동 재로드가 안 됨.
> 검증 전에 `source ~/.bashrc` 또는 새 터미널을 열 것.

```bash
docker --version
# Ollama: CLI 가 있으면 버전, 없으면 daemon 응답 확인 (Case B: 호스트 GUI + WSL2 셸)
command -v ollama >/dev/null && ollama --version || \
  curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null && echo "ollama daemon: OK"
python3 --version
# huggingface-cli 는 venv 안에 설치되며 0.x 라 --version 인자 없음
command -v huggingface-cli && \
  python3 -c "import sys; sys.path.insert(0, '$HOME/.local/share/ttc-venv/lib/python3.12/site-packages'); import huggingface_hub; print('huggingface_hub', huggingface_hub.__version__)"
git --version
curl --version | head -1
```

✅ **정상**: 모든 줄이 OK / 버전 출력. (참고: `huggingface-cli` 는 `~/.local/bin/` 에 symlink — `./` 접두사 없이 PATH 로 호출)

### 2.4 B 머신 (폐쇄망 운영용) — 호스트에 직접 설치할 도구

| # | 도구 | 용도 | 최소 버전 | 설치 |
|---|---|---|---|---|
| 1 | **Docker Desktop** | `docker load` + `docker compose up` + supervisor 14 process 구동 | 24.x+ | 반출 매체 `installers/` 의 `Docker.dmg` (macOS) / `Docker-Desktop-Installer.exe` (Windows) |
| 2 | **Docker 메모리 할당** | 컨테이너 11GB + LLM KV cache 여유 | **24 GB 이상** | macOS: §2.1.5 화면대로. WSL2: `~/.wslconfig` 에 `memory=56GB` |
| 3 | **Ollama** | 호스트 LLM 추론 | 0.4.x+ | 반출 매체 `installers/Ollama-darwin.zip` / `OllamaSetup.exe` / `ollama-linux-amd64` |
| 4 | **(WSL2 / RTX 4070 한정) NVIDIA 드라이버** | CUDA passthrough — `nvidia-smi` 가 RTX 4070 인식 | 535+ | Windows host 에 NVIDIA 공식 드라이버 설치 → WSL2 자동 인식 |
| 5 | **bash** | `offline-load.sh`, `run-{wsl2,mac}.sh` 실행 | 4.x+ | macOS/Linux/WSL2 표준 |

**검증**:

```bash
docker version                 # Client + Server 모두 응답
docker info | grep -i memory   # Total Memory ≥ 24 GB
ollama --version
ollama serve > /dev/null 2>&1 &
curl -sf http://localhost:11434/api/tags
# WSL2 / RTX 4070 한정:
nvidia-smi                     # RTX 4070 인식 + 드라이버 535+
```

### 2.5 양 머신 차이 — 환경변수 1줄 + arch 1개

본 스택은 양 머신 동일 모델·동일 이미지·동일 supervisord 를 원칙으로 합니다. 차이는:

| 항목 | 1차 (WSL2 / RTX 4070) | 2차 (M4 Pro) |
|---|---|---|
| 호스트 arch | `linux/amd64` | `linux/arm64` |
| `OLLAMA_NUM_PARALLEL` | `1` (직렬) | `3` (동시 3 이슈, throughput ↑) |
| `OLLAMA_MAX_LOADED_MODELS` | `1` (양 머신 공통) | `1` |
| 모델 / 이미지 / supervisord / entrypoint / 포트 / 볼륨 | 모두 동일 | — |

> **호스트에서 직접 설치하지 않는 것**: Jenkins · Dify · SonarQube · Postgres · Redis · Qdrant ·
> Meilisearch · FalkorDB · retrieve-svc · bge-reranker-v2-m3 · sentence-transformers ·
> lockable-resources Jenkins 플러그인 — *모두 단일 이미지에 통합* 되어 자동 설치.

---

## 3. 플랫폼별 빌드 (A 머신)

### 3.1 빌드 흐름 한눈에

```
[A 머신, 인터넷 ON]                                       ⌜ 한 줄 명령으로 ⌝
                                                          ⌞   자동 처리   ⌟
  §2.3) bash scripts/setup-host.sh --all
        - OS 패키지 / Python / huggingface-cli 자동
        - download-rag-bundle.sh 자동 호출 (3-1 자산)
       ↓                     ⏱ 5~10분 (호스트) + 30분~3시간 (자산)
  3-1) 자산 다운로드   ←──────────── (위 명령으로 이미 완료)
                              💾 ~12GB
       ↓
  3-2) 통합 이미지 빌드 (bash scripts/build-{wsl2,mac}.sh)
       ↓                     ⏱ 30분~90분 / 💾 ~25GB peak
  3-3) 반입 패키지 산출 (bash scripts/offline-prefetch.sh)
       ↓                     ⏱ 10~20분 / 💾 ~22GB
[USB / NAS / 외장 SSD]       (USB 라면 §3.5 매체 가이드)
       ↓
[B 머신으로 이동, §4 진행]
```

### 3.2 단계 3-1 · 자산 다운로드

🎯 **무엇을 하는가**: 폐쇄망에서는 인터넷이 없으므로, A 머신에서 *모든 외부 자산을 미리 받아* 이미지에
넣을 준비를 한다.

#### ⭐ 권장 — 한 줄 명령 (또는 §2.3 에서 이미 처리됨)

§2.3 에서 `bash scripts/setup-host.sh --all` 로 시작했다면 이 단계는 **이미 끝났습니다** —
바로 [§3.3 통합 이미지 빌드](#33-단계-3-2--통합-이미지-빌드--3090분---25-gb-peak) 로 가도 됩니다.

별도로 자산만 (재)다운로드 하려는 경우:

```bash
cd code-AI-quality-allinone
bash scripts/download-rag-bundle.sh

# 옵션:
bash scripts/download-rag-bundle.sh --skip-models      # Ollama 모델 다운로드 생략
bash scripts/download-rag-bundle.sh --skip-plugins     # Jenkins/Dify 플러그인 생략
```

⏱ **소요 시간**: 30분 ~ 3시간 (회선 속도 의존, `gemma4:e4b` 가 9.6 GB 라 가장 오래).

📦 **자동으로 받는 5 항목**:

| # | 자산 | 위치 | 크기 |
|---|---|---|---|
| 1 | Jenkins / Dify 플러그인 번들 (`download-plugins.sh` 호출) | `jenkins-plugins/`, `dify-plugins/` | ~50 MB |
| 2 | Meilisearch v1.42 binary (호스트 native arch 한 개) | `offline-assets/meilisearch/` | ~100 MB |
| 3 | bge-reranker-v2-m3 weight (BAAI Apache 2.0) | `offline-assets/rerank-models/bge-reranker-v2-m3/` | ~1.1 GB |
| 4 | FalkorDB Docker 이미지 (multi-stage 차용) | Docker daemon (별도 파일 X) | ~350 MB |
| 5 | Ollama 모델 — `gemma4:e4b` + `qwen3-embedding:0.6b` + `bge-m3` | macOS/Linux: `~/.ollama/models/` · Windows GUI: `C:\Users\<Win>\.ollama\models\` | ~11 GB |

✅ **정상 신호**: 스크립트 마지막에 `[bundle] ✓ 자산 일괄 다운로드 완료` 메시지 + 다운로드 결과 표.

> **Windows + WSL2 사용자 (선택 A 권장)** — Ollama 를 Windows host 에 GUI 로 설치한 경우, WSL2 셸에는
> ollama CLI 가 없어 자동 모델 다운로드가 불가합니다. `download-rag-bundle.sh` 가 이 케이스를 *자동
> 감지* 해 모델 다운로드 단계만 skip 하고 나머지 (1~4) 는 정상 진행합니다. **모델 pull 은 Windows
> PowerShell 에서 직접**:
>
> ```powershell
> ollama pull gemma4:e4b
> ollama pull qwen3-embedding:0.6b
> ollama pull bge-m3
> ```
>
> 이후 §3.4 의 "Ollama 모델 export" 단계에서 `/mnt/c/Users/<Win>/.ollama/models/` 경로를 참조해
> 반입 패키지에 포함시킵니다.

❌ **자주 막히는 곳**:
- *`bge-reranker` 가 403 / 429* → HuggingFace rate limit. `huggingface-cli login` 후 토큰 등록 → 재실행. [§6.1.3](#613-huggingface-cli-rate-limit-403--429) 참조.
- *`gemma4:e4b` 가 멈춘 것 같다* → 9.6 GB 라 회선 속도에 따라 30분~3시간. [§6.1.2](#612-ollama-pull-이-매우-느리거나-멈춘-것-같다) 참조.

#### 부록 — 수동 다운로드 (스크립트 안 쓸 때 또는 학습용)

각 자산을 개별 명령으로 받습니다. 아래 3-1-a 부터 3-1-d 까지 차례로 실행.

#### 3-1-a · Jenkins / Dify 플러그인 번들 ⏱ 2~5분 / 💾 ~50MB

🎯 **무엇을 하는가**: Jenkins 가 사용할 플러그인 (.jpi) 과 Dify 의 Ollama provider 플러그인 (.difypkg)
을 인터넷에서 받아 빌드 컨텍스트에 둔다.

```bash
bash scripts/download-plugins.sh
```

📦 **결과**:
- `jenkins-plugins/*.jpi` — Jenkins 플러그인 8 종 (workflow-aggregator / file-parameters / htmlpublisher / plain-credentials / uno-choice / sonar / pipeline-build-step / **lockable-resources**)
- `dify-plugins/langgenius-ollama-*.difypkg` — Dify Ollama provider 플러그인
- `jenkins-plugin-manager.jar`, `.plugins.txt` (헬퍼 + 매니페스트)

✅ **정상 확인**:
```bash
ls jenkins-plugins/ | wc -l    # 수십 개 (8+ 의존성 포함)
ls dify-plugins/                # langgenius-ollama-*.difypkg 한 개
```

#### 3-1-b · Phase 0 신규 자산 — Meilisearch + bge-reranker + FalkorDB 이미지

🎯 **무엇을 하는가**: 하이브리드 검색 인프라용 binary 와 모델 weight 를 받아 둔다.

**(1) Meilisearch v1.42 binary** ⏱ 30초~2분 / 💾 ~100 MB:

```bash
# 호스트 native arch 한 개만 받음
mkdir -p offline-assets/meilisearch
case "$(uname -m)" in
  arm64|aarch64) MEILI_TAG=aarch64 ;;
  x86_64)        MEILI_TAG=amd64   ;;
esac
curl -fL -o "offline-assets/meilisearch/meilisearch-linux-${MEILI_TAG}" \
  "https://github.com/meilisearch/meilisearch/releases/download/v1.42.1/meilisearch-linux-${MEILI_TAG}"
chmod +x offline-assets/meilisearch/meilisearch-linux-*
```

✅ **정상**: `ls -lh offline-assets/meilisearch/` 시 `meilisearch-linux-amd64` 또는 `-aarch64`
한 파일이 약 100 MB.

**(2) bge-reranker-v2-m3 weight (BAAI, Apache 2.0)** ⏱ 5~30분 / 💾 ~1.1 GB:

```bash
mkdir -p offline-assets/rerank-models
huggingface-cli download BAAI/bge-reranker-v2-m3 \
  --local-dir offline-assets/rerank-models/bge-reranker-v2-m3 \
  --local-dir-use-symlinks=False
```

> 🛑 **느리거나 실패 시** — HuggingFace 가 비로그인 IP 에 rate limit 적용. `huggingface-cli login`
> 으로 무료 계정 토큰 등록 후 재시도. 자세한 가이드: [§6.1.3](#613-huggingface-cli-rate-limit-403--429).

✅ **정상**: `ls offline-assets/rerank-models/bge-reranker-v2-m3/` 에 `model.safetensors`,
`config.json`, `tokenizer.json` 모두 존재. `du -sh` 가 약 1.1 GB.

**(3) FalkorDB Docker 이미지** ⏱ 1~3분 / 💾 ~350 MB:

```bash
docker pull falkordb/falkordb:latest
docker inspect falkordb/falkordb:latest --format '{{.Architecture}}'
```

✅ **정상**: `Architecture` 가 `arm64` 또는 `amd64` (호스트와 일치). 별도 파일 export 불필요 —
Dockerfile 의 multi-stage 가 자동 차용.

#### 3-1-c · Ollama 모델 ⏱ 10분~3시간 / 💾 ~11 GB

🎯 **무엇을 하는가**: 호스트 LLM 추론용 모델 2 종을 받는다. 본 스택의 LLM 추론은 *전부* 이 호스트
Ollama 가 담당한다.

```bash
# Ollama 데몬 기동 (이미 떠 있으면 무시됨)
ollama serve &
sleep 3

# 모델 pull
ollama pull gemma4:e4b              # 분석 + enricher 공유 LLM
                                    # ⏱ 5~120분 (회선 속도) / 💾 9.6 GB
ollama pull qwen3-embedding:0.6b    # retrieve-svc 임베딩
                                    # ⏱ 1~5분 / 💾 0.6 GB
ollama pull bge-m3                  # Dify 내장 retrieve 의 임베딩
                                    # ⏱ 1~5분 / 💾 1 GB
                                    # (provision.sh 가 등록 — Phase 4 미완 동안 사용)
```

> ⏱ **gemma4:e4b 가 멈춘 것 같다면** — 9.6 GB 다운로드는 회선 속도에 따라 **최대 2~3시간** 걸릴 수 있음.
> 진행 막대가 천천히 움직여도 정상. 완전히 멈춘 경우 [§6.1.2](#612-ollama-pull-이-매우-느리거나-멈춘-것-같다)
> 참조.

✅ **정상**:
```bash
ollama list
# → gemma4:e4b              ~9.6 GB
#   qwen3-embedding:0.6b    ~640 MB
#   bge-m3                  ~1.2 GB
```

> **모델 선정 (양 머신 동일)** — `gemma4:26b` 등 큰 모델은 RTX 4070 8GB VRAM 에 fit 하지 않아 *영구
> 비채택*. 정확도 부족 시 `gemma4:e4b` 의 *self-consistency 다수결* 로 우회. 결정 근거: PLAN §6.4.

#### 3-1-d · Docker Desktop / Ollama installer ⏱ 5~15분 / 💾 ~1 GB

🎯 **무엇을 하는가**: B 머신 (폐쇄망) 에서 Docker / Ollama 를 설치할 *오프라인 installer* 를 미리 받아둔다.

```bash
mkdir -p installers
# === macOS 운영 머신용 ===
curl -fL -o installers/Docker.dmg "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
curl -fL -o installers/Ollama-darwin.zip "https://ollama.com/download/Ollama-darwin.zip"

# === Windows (WSL2) 운영 머신용 ===
# curl -fL -o installers/Docker-Desktop-Installer.exe "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
# curl -fL -o installers/OllamaSetup.exe "https://ollama.com/download/OllamaSetup.exe"
```

✅ **정상**: `ls -lh installers/` 출력 시 본인 OS 의 파일 2 개가 각각 수백 MB 이상.

### 3.3 단계 3-2 · 통합 이미지 빌드 ⏱ 30~90분 / 💾 ~25 GB peak

🎯 **무엇을 하는가**: Dockerfile 한 개를 사용해 Jenkins · Dify · SonarQube · Postgres · Redis ·
Qdrant · Meilisearch · FalkorDB · retrieve-svc · bge-reranker weight 등 *모든 컴포넌트를 단일
이미지로* 통합한다.

```bash
# 1차 머신 (WSL2 / amd64) — 정본
bash scripts/build-wsl2.sh

# 2차 머신 (M4 Pro / arm64) — 동일 절차, 스크립트 이름만 다름
# bash scripts/build-mac.sh
```

📦 **결과**: `ttc-allinone:wsl2-dev` 또는 `ttc-allinone:mac-dev` 이미지 (약 10~11 GB).

✅ **정상**:
```bash
docker images | grep ttc-allinone
# → ttc-allinone   wsl2-dev   <hash>   <date>   ~10.5GB
```

❌ **자주 막히는 곳**:
- *디스크 공간 부족* (`no space left on device`) → 빌드 peak 디스크 25 GB. `docker system prune -a` 로 사용 안 하는 캐시 정리. 또는 Docker Desktop → Resources → Disk image size 늘림.
- *base 이미지 다운로드 실패* (Docker Hub timeout) → 사내 프록시 환경에서 `https_proxy` 환경변수 설정 후 재실행.

> 두 빌드 스크립트는 *arch tag 와 image tag 외 동일* — Dockerfile / requirements / pipeline-scripts /
> jenkins-init 모두 단일 소스. 빌드 방식은 legacy `DOCKER_BUILDKIT=1 docker build` (buildx 미사용).

### 3.4 단계 3-3 · 반입 패키지 산출 ⏱ 10~20분 / 💾 ~22 GB

🎯 **무엇을 하는가**: 빌드한 이미지 + GitLab + Sandbox 3 종을 tarball 로 export 하고, Ollama 모델
디렉터리를 호스트에서 복사한다. 이 결과물 묶음이 폐쇄망으로 들어갈 *반입 패키지* 가 된다.

**(1) 이미지 tarball export**:

```bash
bash scripts/offline-prefetch.sh --arch amd64    # 또는 arm64
```

📦 **결과** (`offline-assets/${ARCH}/`):

| 파일 | 크기 | 출처 |
|---|---|---|
| `ttc-allinone-${ARCH}-${TAG}.tar.gz` | ~10~11 GB | 방금 빌드한 단일 이미지 |
| `gitlab-gitlab-ce-18.11.0-ce.0-${ARCH}.tar.gz` | ~1.5~1.7 GB | GitLab CE 공식 이미지 |
| `dify-sandbox-langgenius-dify-sandbox-0.2.10-${ARCH}.tar.gz` | ~0.5 GB | Dify Code 노드 sandbox |
| `*.meta` | (작음) | 각 tarball 의 SHA256 + 빌드 시각 |

**(2) Ollama 모델 export** — *모델이 어디에 저장되어 있는지에 따라 분기*:

```bash
mkdir -p offline-assets/ollama-models

# === macOS / Linux native / WSL2 native 설치 (선택 B) — 모델이 WSL2 home 에 있음 ===
cp -r ~/.ollama/models/{blobs,manifests} offline-assets/ollama-models/

# === Windows GUI 설치 + WSL2 빌드 (선택 A — §2.1.6) — 모델이 Windows 에 있음 ===
# Windows 사용자명 자동 감지 (cmd.exe 호출):
WIN_USER=$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')
WIN_OLLAMA="/mnt/c/Users/${WIN_USER}/.ollama/models"
[ -d "$WIN_OLLAMA" ] && cp -r "$WIN_OLLAMA/"{blobs,manifests} offline-assets/ollama-models/
ls offline-assets/ollama-models/   # blobs/ manifests/ 두 디렉터리가 생성되어야 정상
```

> **Windows + WSL2 사용자 주의** — 모델 pull 을 *Windows PowerShell* 에서 진행했다면 (선택 A 권장),
> WSL2 의 `~/.ollama/models/` 는 *비어 있습니다*. 위의 두 번째 블록을 실행해 `/mnt/c/Users/<Win유저>/.ollama/models/`
> 에서 복사하세요. 첫 번째 블록 (`~/.ollama/models/`) 만 실행하면 모델이 안 들어갑니다.

✅ **정상**:
```bash
du -sh offline-assets/${ARCH}/   # 약 12~13 GB
du -sh offline-assets/ollama-models/   # 약 11 GB
```

### 3.5 USB / 외장 매체 반입 가이드

🎯 **무엇을 하는가**: 위에서 만든 자산 묶음을 *물리 매체* (USB / 외장 SSD / NAS) 에 복사해 폐쇄망
B 머신으로 옮긴다.

#### 3.5.1 매체 포맷 — 반드시 exFAT 또는 ext4 권장

| 포맷 | 단일 파일 한도 | 권장 여부 |
|---|---|---|
| **FAT32** | 4 GB | ❌ 본 스택 tarball 11 GB → 못 들어감 |
| **exFAT** | 16 EB | ✅ macOS / Windows / Linux 모두 인식 — **권장** |
| **NTFS** | 256 TB | △ macOS 는 읽기만 가능, 쓰기 불가 |
| **ext4** | 16 TB | ✅ Linux ↔ Linux 만 |

> 매체가 새것이면 OS 의 디스크 유틸리티 / Disk Management 에서 **exFAT 으로 포맷** 후 사용.

#### 3.5.2 매체 마운트 경로 (OS 별)

| OS | USB 가 자동 마운트되는 경로 |
|---|---|
| macOS | `/Volumes/<USB이름>/` (예: `/Volumes/AIRGAP/`) |
| Windows | Explorer 에서 `D:\` 같은 드라이브 문자 |
| Windows + WSL2 셸에서 접근 | `/mnt/d/` (Windows D: → WSL2 에서) |
| Linux (Ubuntu) | `/media/<사용자>/<USB이름>/` (예: `/media/dscore/AIRGAP/`) |

#### 3.5.3 자산 복사

```bash
# === macOS ===
cp -r offline-assets installers /Volumes/AIRGAP/
cp -r ~/.ollama/models /Volumes/AIRGAP/ollama-models  # Ollama models 통째 (이미 §3.4 에서 export 했으면 생략)

# === WSL2 (D: 가 USB 인 경우) ===
cp -r offline-assets installers /mnt/d/
cp -r ~/.ollama/models /mnt/d/ollama-models

# === Linux ===
cp -r offline-assets installers /media/$USER/AIRGAP/
```

#### 3.5.4 무결성 검증 (선택, 권장)

A 머신에서 SHA256 체크섬 생성:

```bash
cd /Volumes/AIRGAP    # 또는 /mnt/d/, /media/$USER/AIRGAP/
find . -type f -name '*.tar.gz' -exec sha256sum {} \; > CHECKSUMS.sha256
```

B 머신에서 검증 (§4 진행 직후):

```bash
cd ~/airgap-bundle/
sha256sum -c CHECKSUMS.sha256    # 모든 줄 끝에 ": OK" 가 나와야 정상
```

❌ **막히는 곳**:
- *"file too large"* (FAT32 한계) → §3.5.1 의 exFAT 으로 재포맷.
- *macOS 가 NTFS USB 에 쓰기 안 됨* → exFAT 로 포맷하거나 별도 마운트 도구 사용.

### 3.6 빌드 과정에서 단일 이미지에 *통합되는 것들*

위 빌드 결과물 `ttc-allinone-${ARCH}-${TAG}.tar.gz` 안에 들어 있는 컴포넌트:

| 카테고리 | 컴포넌트 | 출처 / 적재 경로 |
|---|---|---|
| 베이스 OS | Debian (Jenkins LTS 베이스) | `jenkins/jenkins:2.555.1-lts-jdk21` |
| CI | Jenkins LTS + JDK 21 | 베이스 그대로 |
| LLM Workflow | Dify API · Web · plugin-daemon | `langgenius/dify-{api,web,plugin-daemon}:1.13.3` 에서 multi-stage COPY |
| Vector DB | Qdrant v1.8.3 | GitHub releases binary |
| 정적분석 | SonarQube Community 26.4 + JDK | `sonarqube:26.4.0.121862-community` 에서 multi-stage COPY |
| SonarScanner CLI | sonar-scanner-cli 6.2.1.4610 | sonarsource binaries.sonarsource.com |
| 메타 DB | PostgreSQL 15 (pgdg) | apt 패키지 |
| 큐 | Redis (Dify 용) | apt 패키지 |
| 게이트웨이 | nginx | apt 패키지 |
| Process Manager | supervisor | apt 패키지 |
| **Sparse 인덱스 (Phase 0)** | Meilisearch v1.42 | `offline-assets/meilisearch/` 에서 COPY |
| **Graph 인덱스 (Phase 0)** | FalkorDB Redis module (`falkordb.so`) | `falkordb/falkordb:latest` 에서 multi-stage COPY |
| **Hybrid Retrieve (Phase 0)** | retrieve-svc (FastAPI) — 별도 venv | `retrieve-svc/` 디렉터리 → `/opt/retrieve-svc/.venv` |
| **Reranker weight (Phase 0)** | bge-reranker-v2-m3 (568M, Apache 2.0) | `offline-assets/rerank-models/` 에서 COPY |
| Python 런타임 | Python 3.12 + tree-sitter / jedi / sentence-transformers / torch CPU | `pip install` |
| Node.js | v22.22.1 | nodejs.org |
| Playwright | Chromium (eval_runner UI 모드 용) | pip + `playwright install --with-deps chromium` |
| Jenkins 플러그인 seed | `jenkins-plugins/*.jpi` | 빌드 컨텍스트 COPY |
| Dify 플러그인 seed | `dify-plugins/*.difypkg` | 빌드 컨텍스트 COPY |
| 파이프라인 스크립트 | `pipeline-scripts/*.py` (repo_context_builder · sonar_issue_exporter · dify_sonar_issue_analyzer · 등) | `/opt/pipeline-scripts/` |
| eval_runner | AI 평가 모듈 | `/opt/eval_runner` |
| 5 Jenkinsfile | `01`~`05` Pipeline 정의 | `/opt/jenkinsfiles/` |
| supervisord 설정 | 14 program 매니페스트 | `/etc/supervisor/supervisord.conf` |
| entrypoint + provision | 자동 부팅·프로비저닝 | `/entrypoint.sh`, `/opt/provision.sh` |

> **호스트에 별도 설치하지 않음** — 위 표의 모든 컴포넌트는 단일 tarball 1 개에 들어가 있으며,
> B 머신에서는 `docker load` 한 줄로 모두 복원됩니다.

---

## 4. 플랫폼별 서비스 구동 및 프로비저닝 (B 머신)

### 4.1 구동 흐름 한눈에

```
[B 머신, 인터넷 OFF]
  4-1) 매체 → 로컬 디스크 복사    ⏱ 5~20분 / 💾 ~22GB
       ↓
  4-2) 이미지 로드               ⏱ 5~15분
       ↓
  4-3) Ollama 모델 복원          ⏱ 1~3분
       ↓
  4-4) 컨테이너 기동             ⏱ 10초 (즉시 리턴, 안에서 백그라운드 진행)
       ↓
  4-5) 자동 프로비저닝 대기       ⏱ ~7분
       ↓
  4-6) 헬스체크                  ⏱ 1~2분
       ↓
  파이프라인 사용 가능 (§5)
```

### 4.2 단계 4-1 · 매체에서 로컬 디스크로 복사

🎯 **무엇을 하는가**: USB 등 반출 매체에서 *직접 실행* 하면 매체 속도가 병목이 되어 매우 느림. 먼저
B 머신의 로컬 디스크로 복사한다.

```bash
# === macOS ===
mkdir -p ~/airgap-bundle
cp -r /Volumes/AIRGAP/* ~/airgap-bundle/

# === WSL2 ===
mkdir -p ~/airgap-bundle
cp -r /mnt/d/* ~/airgap-bundle/

# === Linux ===
mkdir -p ~/airgap-bundle
cp -r /media/$USER/AIRGAP/* ~/airgap-bundle/
```

📦 **결과**: `~/airgap-bundle/` 안에 `code-AI-quality-allinone/`, `offline-assets/`, `ollama-models/`,
`installers/` 가 들어 있다.

✅ **정상**:
```bash
ls ~/airgap-bundle/
du -sh ~/airgap-bundle/*       # 각 디렉터리 크기 확인
```

이후 모든 명령은 레포 폴더 안에서 실행:

```bash
cd ~/airgap-bundle/code-AI-quality-allinone
```

### 4.3 단계 4-2 · 이미지 로드 ⏱ 5~15분

🎯 **무엇을 하는가**: tarball 형태의 Docker 이미지를 호스트 Docker daemon 에 *적재* (load) 한다.

```bash
# 양 머신 동일 스크립트, --arch 인자만 다름
bash scripts/offline-load.sh --arch amd64       # 1차 (WSL2). 2차: --arch arm64.
```

📦 **내부 동작**: `offline-assets/${ARCH}/*.tar.gz` 를 차례로 `docker load`.

✅ **정상**:
```bash
docker images | grep -E "ttc-allinone|gitlab|dify-sandbox"
# → ttc-allinone:wsl2-dev (또는 :mac-dev)
#   gitlab/gitlab-ce:18.11.0-ce.0
#   langgenius/dify-sandbox:0.2.10
```

세 줄 모두 보이면 통과.

### 4.4 단계 4-3 · Ollama 모델 복원 ⏱ 1~3분

🎯 **무엇을 하는가**: A 머신에서 가져온 Ollama 모델 디렉터리를 B 머신의 `~/.ollama/models/` 에
복원한다.

```bash
mkdir -p ~/.ollama
cp -r ~/airgap-bundle/ollama-models/* ~/.ollama/models/
ollama list
```

✅ **정상**: `ollama list` 출력에 `gemma4:e4b`, `qwen3-embedding:0.6b`, `bge-m3` 세 모델이 모두 보임.

**환경변수 (양 머신 공통 1 + 차이 1)** — `~/.bashrc` 또는 `~/.zshrc` 에 영구 등록 권장:

```bash
echo 'export OLLAMA_MAX_LOADED_MODELS=1' >> ~/.bashrc      # 양 머신 공통
# 1차 (RTX 4070):
echo 'export OLLAMA_NUM_PARALLEL=1' >> ~/.bashrc
# 2차 (M4 Pro):
# echo 'export OLLAMA_NUM_PARALLEL=3' >> ~/.bashrc

source ~/.bashrc

# Ollama 데몬 기동 (이미 떠 있으면 무시됨)
ollama serve &
```

### 4.5 단계 4-4 · 컨테이너 기동 ⏱ 10초 (백그라운드 진행)

🎯 **무엇을 하는가**: Docker Compose 로 `ttc-allinone` (메인 통합 컨테이너) + `ttc-gitlab` (GitLab) +
`ttc-sandbox` (Dify Code 노드) 3 개를 한 번에 띄운다.

```bash
# 양 머신 동일 흐름, 스크립트 이름만 다름
bash scripts/run-wsl2.sh     # 1차 (WSL2). 내부적으로 docker compose -f docker-compose.wsl2.yaml up -d
# 또는
bash scripts/run-mac.sh      # 2차 (M4 Pro)
```

> 명령은 ~10초 안에 리턴됩니다. 그러나 *컨테이너 안* 에서는 §4.6 의 자동 프로비저닝이 백그라운드로
> 계속 진행됩니다.

📦 **결과**: 3 컨테이너 기동.

✅ **정상**:
```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
# NAMES           STATUS
# ttc-allinone    Up 30 seconds
# ttc-gitlab      Up 30 seconds (health: starting)
# ttc-sandbox     Up 30 seconds
```

> `ttc-gitlab` 의 `(health: starting)` 은 정상 — GitLab reconfigure 가 5~10 분 소요. 자동으로
> `(healthy)` 로 전환됩니다.

### 4.6 단계 4-5 · 자동 프로비저닝 대기 ⏱ ~7분

🎯 **무엇을 하는가**: `ttc-allinone` 의 `entrypoint.sh` 가 첫 부팅에서 자동으로 `provision.sh` 를
호출 — Dify 관리자 setup, Ollama plugin 설치, Workflow publish, GitLab root PAT 발급, SonarQube
admin 비번 변경, Jenkins Job 5 개 등록 등 **15 가지 작업** 을 자동 수행.

**진행 모니터링** (실시간):

```bash
docker logs -f ttc-allinone | grep -E "provision|entrypoint"
# Ctrl+C 로 종료
```

**자동화 스크립트용 — `자동 프로비저닝 완료` 가 보일 때까지 대기**:

```bash
until docker logs ttc-allinone 2>&1 | grep -q "자동 프로비저닝 완료"; do
    sleep 15
done
echo "PROVISION DONE"
```

✅ **완료 신호**:

```
[provision] 자동 프로비저닝 완료.
[provision]   Jenkins    : http://127.0.0.1:28080 (admin / password)
[provision]   Dify       : http://127.0.0.1:28081 (admin@ttc.local / TtcAdmin!2026)
[provision]   SonarQube  : http://localhost:29000 (admin / TtcAdmin!2026)
[provision]   GitLab     : http://localhost:28090 (root / ChangeMe!Pass)
[provision]   Sample Repo: http://localhost:28090/root/realworld
```

❌ **7분 넘어도 완료 메시지가 안 나오면** — `docker logs ttc-allinone | tail -100` 으로 마지막
로그 확인. 가장 흔한 원인은 메모리 부족 (B 머신 Docker 메모리 24 GB 미만) 또는 호스트 Ollama 미연결.

### 4.7 단계 4-6 · 헬스체크 ⏱ 1~2분

🎯 **무엇을 하는가**: 4 개 외부 서비스 + 14 개 내부 process + 3 개 Phase 0 신규 컴포넌트 모두
정상인지 확인.

#### 외부 노출 4 포트 — 브라우저 접속

| 서비스 | URL | 자격증명 |
|---|---|---|
| Jenkins | http://localhost:28080 | admin / password |
| Dify | http://localhost:28081 | admin@ttc.local / TtcAdmin!2026 |
| SonarQube | http://localhost:29000 | admin / TtcAdmin!2026 |
| GitLab | http://localhost:28090 | root / ChangeMe!Pass |

✅ **정상**: 4 사이트 모두 로그인 화면이 뜨고, 위 자격증명으로 진입 가능.

#### 컨테이너 내부 14 supervisor process

```bash
docker exec ttc-allinone supervisorctl status
```

✅ **정상**: 14 줄 모두 `RUNNING` 상태.

| program | priority | autostart | 역할 |
|---|---|---|---|
| postgresql | 100 | true | Dify · SonarQube 의 DB |
| redis | 100 | true | Dify Celery broker |
| qdrant | 100 | true | Dense vector DB (Dify 내장) |
| meilisearch | 100 | true | **Sparse BM25 인덱스 (Phase 0)** |
| falkordb | 100 | true | **Graph 인덱스 — 별도 redis :6380 + falkordb.so (Phase 0)** |
| sonarqube | 150 | false (entrypoint manual start) | 정적분석 엔진 |
| dify-plugin-daemon | 200 | false | Dify 플러그인 런타임 |
| dify-api | 300 | false | Dify API (gunicorn) |
| dify-worker | 300 | true | Dify Celery worker |
| dify-worker-beat | 300 | true | Dify Celery beat |
| dify-web | 300 | true | Dify Next.js UI |
| nginx | 400 | true | Dify 게이트웨이 (28081) |
| retrieve-svc | 400 | false (entrypoint manual start) | **Hybrid Retrieve 어댑터 (Phase 0)** |
| jenkins | 500 | true | CI 마스터 |

#### Phase 0 신규 3 program 헬스 endpoint

```bash
docker exec ttc-allinone curl -sf http://127.0.0.1:7700/health     # Meilisearch
docker exec ttc-allinone redis-cli -p 6380 ping                     # FalkorDB
docker exec ttc-allinone curl -sf http://127.0.0.1:9100/health      # retrieve-svc
docker exec ttc-allinone curl -sf http://host.docker.internal:11434/api/tags | head -c 200
```

✅ **정상 응답**:
- Meilisearch: `{"status":"available"}` 또는 200
- FalkorDB: `PONG`
- retrieve-svc: `{"status":"ok","rerank_loaded":true}` (rerank_loaded=true 면 sentence-transformers + bge-reranker-v2-m3 정상 로드)
- Ollama: 모델 목록 JSON

### 4.8 서비스 구동 / 프로비저닝 과정에서 *설정되는 것들*

`provision.sh` 가 첫 부팅 시 자동 수행하는 15 가지 작업 (`/data/.app_provisioned` 마커로 멱등):

| # | 영역 | 동작 |
|---|---|---|
| 1 | **Jenkins** | HTTP ready 대기 |
| 2 | **Dify** | 관리자 setup (`admin@ttc.local` / `TtcAdmin!2026`) + 로그인 |
| 3 | **Dify** | Ollama 플러그인 설치 (`/opt/dify-assets/langgenius-ollama-*.difypkg` 사용) |
| 4 | **Dify** | Ollama LLM provider 등록 — `gemma4:e4b` (`http://host.docker.internal:11434`) |
| 5 | **Dify** | Ollama 임베딩 provider 등록 — `bge-m3` (현행 04 Workflow 가 사용하는 임베딩) |
| 6 | **Dify** | workspace 기본 모델 지정 (LLM / Embedding) |
| 7 | **Dify** | Knowledge Dataset `code-context-kb` 생성 (high_quality + bge-m3 + hybrid_search) |
| 8 | **Dify** | Dataset API key 발급 |
| 9 | **Dify** | Workflow `sonar-analyzer-workflow.yaml` import (dataset_id 자동 주입) |
| 10 | **Dify** | Workflow draft → published 전환 |
| 11 | **Dify** | Workflow App API key 발급 |
| 12 | **GitLab** | 3-phase ready 대기 (HTTP → Rails → Git HTTP) → root 비밀번호 (`ChangeMe!Pass`) → root PAT 발급 |
| 13 | **GitLab** | 샘플 프로젝트 `root/realworld` 자동 생성 (Spring Boot RealWorld vendoring) + 초기 push |
| 14 | **SonarQube** | admin 비밀번호 변경 (`admin` → `TtcAdmin!2026`) → user token (`jenkins-auto`) 발급 |
| 15 | **Jenkins** | Credentials 5 종 주입 (`dify-dataset-id`, `dify-knowledge-key`, `dify-workflow-key`, `gitlab-pat`, `sonarqube-token`) → SonarQube 서버 등록 → SonarScanner CLI tool 등록 → Job 5 개 등록 (`01`~`05`) → Jenkinsfile 의 GITLAB_PAT placeholder 를 `credentials('gitlab-pat')` 로 치환 |

> **현행 임베딩의 이중 트랙 (정직한 안내)** — provision.sh 의 #5 가 *현재 운영 default 인 `bge-m3`*
> 를 Dify 에 등록합니다. Phase 0 의 `qwen3-embedding:0.6b` 는 **retrieve-svc 의 Hybrid Retrieve 안에서만**
> 사용 (supervisord.conf 의 `OLLAMA_EMBED_MODEL` env). 04 Workflow 가 retrieve-svc 를 호출하도록
> External KB API 로 datasource 를 교체하는 작업 (Phase 4) 은 본 시점에서 *수동 등록 필요* —
> [§6.3 현재 구현 상태](#63-현재-구현-상태) 참조. Phase 0 컴포넌트가 *기동만 되어 있어도 04 는
> 정상 동작* 합니다 (retrieve-svc 가 사용되지 않을 뿐).

---

## 5. 각 파이프라인 이용방법

### 5.1 5 파이프라인 한눈에

| Job | 역할 | 입력 | 산출 |
|---|---|---|---|
| **01 코드 분석 체인** | 02→03→04 자동 연쇄 (오케스트레이터) | 커밋 SHA 1 개 | GitLab Issue + chain_summary.json |
| **02 코드 사전학습** | RAG KB 빌드 (tree-sitter 청킹 → Dify Dataset) | 레포 URL | Pre-training Report (HTML) + Dify Dataset 청크 |
| **03 코드 정적분석** | SonarQube 스캔 | SonarQube Project Key | Sonar 서버에 이슈 적재 |
| **04 결과분석 + 이슈등록** | LLM 분석 → GitLab Issue 생성 | Sonar 이슈 셋 | GitLab Issue + RAG Diagnostic Report |
| **05 AI 평가** | Golden Dataset 으로 AI 회귀 채점 | 평가 대상 모델 + Golden CSV | summary.html (지표 카드 + 실패 드릴다운) |

**최단 경로 (대부분 PM·운영자가 쓰는 흐름)**: `01` 한 번으로 끝. `01` 이 `02→03→04` 를 순서대로 호출.

### 5.2 Jenkins UI 첫 사용 — 5 단계 클릭 가이드

🎯 **무엇을 하는가**: 처음 보는 Jenkins UI 에서 파이프라인을 실행하는 방법.

**(1) 브라우저 접속 + 로그인**:
- 주소창에 `http://localhost:28080` 입력 → Enter
- *"Username"* `admin` / *"Password"* `password` → "Sign in"

**(2) Job 화면 구조**:

```
┌────────────────────────────────────────────────────────────────┐
│ 🔧 Jenkins                                  admin │ logout    │  ← 상단 헤더
├──────────┬──────────────────────────────────────────────────────┤
│ Dashboard│ All Jobs                                             │
│          │ ┌────────────────────────────────────────────────┐ │
│ ➕ New   │ │ S  W  Name                  Last Success ...   │ │  ← 좌측 메뉴 + Job 목록
│ Item     │ │ ✅ 🌧 01 코드 분석 체인     N/A               │ │     클릭할 Job 이름
│          │ │ ✅ 🌧 02 코드 사전학습      N/A               │ │
│ 👥 People│ │ ✅ 🌧 03 코드 정적분석      N/A               │ │
│          │ │ ✅ 🌧 04 결과분석+이슈등록  N/A               │ │
│ 📊 Build │ │ ✅ 🌧 05 AI 평가            N/A               │ │
│ History  │ └────────────────────────────────────────────────┘ │
└──────────┴──────────────────────────────────────────────────────┘
```

**(3) Job 클릭** — 예: `01 코드 분석 체인`

```
┌────────────────────────────────────────────────────────────────┐
│ Dashboard > 01 코드 분석 체인                                   │
├──────────┬──────────────────────────────────────────────────────┤
│ ⬅️ Back  │ Project 01 코드 분석 체인                            │
│          │                                                      │
│ ▶️ Build │ ← 첫 실행 시 보이는 버튼 (파라미터 없는 빌드)         │
│ Now      │                                                      │
│          │ Recent builds                                        │
│ ⚙️ Build │ #1  Today @ 14:30                                    │
│ with     │                                                      │
│ Param-   │                                                      │
│ eters    │ ← 첫 빌드 1회 후 나타나는 버튼 (권장)                │
└──────────┴──────────────────────────────────────────────────────┘
```

> 🔑 **첫 실행 시** — `Build with Parameters` 가 안 보이고 `Build Now` 만 보일 수 있음. 한 번
> `Build Now` 로 실행해 (실패해도 OK) Jenkins 가 parameters 블록을 읽도록 한 뒤, 다음부터
> `Build with Parameters` 가 나옵니다.

**(4) Build with Parameters → 파라미터 입력**:
- 좌측 메뉴 `⚙️ Build with Parameters` 클릭
- 화면에 입력 필드들이 나타남 → 파라미터 표 ([§5.3](#53-01-코드-분석-체인-오케스트레이터) 등) 참고해 값 확인/수정
- 화면 하단 **`Build`** 버튼 클릭

**(5) 빌드 진행 모니터링**:
- 좌하단 *"Build History"* 에서 방금 시작한 빌드 (예: `#1`) 클릭
- 좌측 메뉴에서 `📃 Console Output` 클릭 → 실시간 로그 스트림
- 빌드 종료 시 *"Finished: SUCCESS"* (✅) 또는 *"Finished: FAILURE"* (❌) 메시지

### 5.3 `01-코드-분석-체인` (오케스트레이터)

`02 → 03 → 04` 를 순서대로 호출. PM·운영자가 가장 자주 쓰는 진입점.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `REPO_URL` | `http://gitlab:80/root/realworld.git` | 분석 대상 Git 레포 |
| `BRANCH` | `main` | 브랜치 |
| `ANALYSIS_MODE` | `full` | `full` (KB 전체 재빌드) / `commit` (커밋 SHA 와 KB manifest 일치 시 재사용) |
| `COMMIT_SHA` | (빈 값) | 특정 커밋 지정 — 빈 값이면 BRANCH HEAD 자동 해석 |
| `SONAR_PROJECT_KEY` | `realworld` | SonarQube 프로젝트 key |
| `GITLAB_PROJECT_PATH` | `root/realworld` | 02 가 checkout 할 GitLab 프로젝트 경로 |
| `GITLAB_PROJECT` | `root/realworld` | 04 가 이슈를 등록할 GitLab 프로젝트 경로 |
| `SEVERITIES` | `BLOCKER,CRITICAL,MAJOR,MINOR,INFO` | Sonar 이슈 severity 필터 |
| `STATUSES` | `OPEN,REOPENED,CONFIRMED` | Sonar 이슈 status 필터 |
| `MAX_ISSUES` | `0` | Dify 분석 이슈 수 cap (0 = 전체) |
| `SONAR_PUBLIC_URL` | `http://localhost:29000` | GitLab 이슈 본문 링크용 외부 URL |
| `GITLAB_PUBLIC_URL` | `http://localhost:28090` | 동일 |

**기본값으로 `Build` 만 누르면** — `root/realworld` 프로젝트 (provision.sh 가 자동 생성한 샘플 Spring
Boot 레포) 의 `main` HEAD 가 분석되어 GitLab 에 이슈가 등록됨. ⏱ 첫 실행 ~30분.

### 5.4 `02-코드-사전학습`

Git → tree-sitter AST 청킹 → Dify Knowledge Base 적재.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `REPO_URL` | `http://gitlab:80/root/realworld.git` | 분석 대상 |
| `ENRICH_CONTEXT` | `true` | `gemma4:e4b` 로 청크당 1 회 요약 prepend (Contextual Retrieval) |
| `COMMIT_SHA` | (빈 값) | 체인 Job 이 전달. 빈 값이면 HEAD 자동 해석 |
| `ANALYSIS_MODE` | `full` | KB manifest 메타 (full = 전수 재빌드 + Dataset purge, commit = 스냅샷) |
| `BRANCH` | `main` | manifest 메타 기록용 |

> **`options { lock(resource: 'ttc-llm-bus') }` 적용** — 02·04 가 동시 구동되지 않습니다 (host
> Ollama 단일 자원 보호). 다른 Job 이 lock 점유 중이면 queue 에서 대기. [§5.8](#58-02--04-동시-구동-차단-정책) 참조.

산출 (Jenkins UI 의 빌드 페이지에서):
- `Pre-training Report` 탭 (HTML) — 청크 통계 + KB 인텔리전스 카드. **읽는 법: §5.9.2**.
- Dify Dataset `code-context-kb` 의 청크 (Qdrant 인덱스 갱신).

### 5.5 `03-코드-정적분석`

SonarScanner CLI 실행. LLM 미사용 (lock 미적용).

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `SONAR_PROJECT_KEY` | `realworld` | SonarQube 프로젝트 key |
| `GITLAB_PROJECT_PATH` | `root/realworld` | 분석 대상 GitLab 경로 |
| `BRANCH` | `main` | 브랜치 |
| `COMMIT_SHA` | (빈 값) | 체인 Job 이 전달 — 빈 값이면 KB bootstrap guard 생략 |
| `ANALYSIS_MODE` | `full` | 02 와 동일 의미 |

산출: SonarQube 서버 (`http://localhost:29000`) 에 이슈 적재. **읽는 법: §5.9.4**.

### 5.6 `04-결과분석-+-이슈등록`

Sonar 이슈를 Dify Workflow 로 분석 → GitLab Issue 자동 생성.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `SONAR_PROJECT_KEY` | `realworld` | — |
| `SEVERITIES` | `BLOCKER,CRITICAL,MAJOR,MINOR,INFO` | — |
| `STATUSES` | `OPEN,REOPENED,CONFIRMED` | — |
| `MAX_ISSUES` | `0` | 0 = 전체 |
| `GITLAB_PROJECT` | `root/realworld` | 이슈 등록 대상 |
| `BRANCH` | `main` | — |
| `SONAR_PUBLIC_URL` | `http://localhost:29000` | 이슈 본문 외부 링크 |
| `GITLAB_PUBLIC_URL` | `http://localhost:28090` | — |
| `COMMIT_SHA` | (빈 값) | 체인 Job 전달 |
| `ANALYSIS_MODE` | `full` | `commit` 모드 시 KB manifest 의 `commit_sha` 일치 검증 |
| `MODE` | `full` | `full` (last_scan 리셋 후 전수) / `incremental` (last_scan 이후 차이분만) |

> **`options { lock(resource: 'ttc-llm-bus') }` 적용** — 02 와 동시 구동 차단.

산출:
- **GitLab Issue** (PM 친화 본문). **읽는 법: §5.9.1**.
- **RAG Diagnostic Report** (Jenkins publishHTML 탭). **읽는 법: §5.9.3**.

### 5.7 `05-AI-평가`

운영 중 AI 서비스의 응답을 Golden Dataset 으로 자동 채점. 다른 4 개와 독립적인 평가 파이프라인.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `TARGET_TYPE` | `local_ollama_wrapper` | 평가 대상 — `local_ollama_wrapper` (호스트 Ollama 직접) / `http` (HTTP 엔드포인트) / `ui_chat` (Playwright 로 UI 자동화) |
| `TARGET_URL` | (빈 값) | API 또는 UI URL |
| `TARGET_AUTH_HEADER` | (빈 값) | 옵션 — 인증 헤더 (예: `Authorization: Bearer ...`) |
| `TARGET_REQUEST_SCHEMA` | `standard` | `standard` / `openai_compat` (OpenAI Chat Completions 호환) |
| `UI_INPUT_SELECTOR` | `textarea, input[type=text]` | UI 모드 전용 — 입력란 CSS selector |
| `UI_SEND_SELECTOR` | `button[type=submit]` | 전송 버튼 |
| `UI_OUTPUT_SELECTOR` | `.answer, [role=assistant], .message-content` | 응답 영역 |
| `UI_WAIT_TIMEOUT` | `60` | 응답 대기 (초) |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | — |
| `JUDGE_MODEL` | (드롭다운, 호스트 Ollama 모델 동적 조회) | 채점관 모델 |
| `TARGET_OLLAMA_MODEL` | (드롭다운) | `local_ollama_wrapper` 모드의 평가 대상 모델 |

**Golden Dataset 위치**: `/var/knowledges/eval/data/golden.csv` (entrypoint 가 `eval_runner/tests/fixtures/tiny_dataset.csv` 를
초기 부팅 시 자동 복사). 사용자가 자체 평가셋을 두려면 컨테이너 안 같은 경로에 CSV 덮어쓰기:

```bash
docker cp my-golden.csv ttc-allinone:/var/knowledges/eval/data/golden.csv
```

산출: `summary.html` — LLM 임원 요약 + 11 지표 카드 + 실패 드릴다운. **읽는 법: §5.9.5**.

### 5.8 02 ↔ 04 동시 구동 차단 정책

호스트 Ollama 는 *단일 LLM bus* — 02 (enricher LLM) 와 04 (분석 LLM) 가 시간 겹치면 모델 swap /
KV cache 경합 / OOM 위험. 두 Job 모두 동일 lock 자원 (`ttc-llm-bus`) 을 잡으므로 시간 겹칠 수 없음:

```groovy
// 02 / 04 jenkinsfile 의 options 블록
options {
    disableConcurrentBuilds()
    lock(resource: 'ttc-llm-bus')
}
```

- ✅ 02 (사전학습) · 04 (분석) 에 lock 적용 — 동시 trigger 시 두 번째 빌드는 첫 번째 종료까지 queue 대기
- ❌ 03 (Sonar) 은 LLM 미사용이라 lock 없음
- ❌ 01 (chain) 자체는 lock 없음 — 02→03→04 를 *순차* 호출만 하고, 각 child 가 자체 lock 점유
- 의존: `lockable-resources` Jenkins 플러그인 (`.plugins.txt` 에 명시, `download-plugins.sh` 가 자동 설치)

### 5.9 결과 해석 — 어디서 무엇을 보나

🎯 **무엇을 하는가**: 위 파이프라인이 만든 산출물을 *어떻게 읽고 의사결정에 활용* 하는지 안내.

#### 5.9.1 GitLab Issue 본문 (04 산출 — PM 친화)

http://localhost:28090/root/realworld/-/issues 에 자동 등록된 이슈 클릭 시 본문 구조:

```
🚦 Action Verdict 신호등         ← PM 의 1초 판단용 (FIX_NEEDED / FALSE_POSITIVE / NEEDS_REVIEW)
─────────────────────────────────
📌 무엇이 문제인가                  ← 1~2 줄 핵심 (LLM 요약)
🎯 어디서 발생하나                  ← 파일 + 라인 + 함수 + 정적 컨텍스트 (decorators / endpoint / param)
⚠️ 영향                            ← LLM 이 추론한 비즈니스/보안 영향
🛠️ 수정 방안                       ← 코드 patch 제안
🔍 AI 판단 근거                    ← 사전학습 KB 의 어떤 청크를 인용했는지 (citation)
📂 같은 패턴 (caller / test 등)    ← 호출자·테스트 함수 N-hop 매핑
📖 기술 상세 ▶ (접기)              ← Sonar Rule ID / 메시지 / SonarQube · GitLab blob 링크
```

**PM 의 사용법**:

| 신호등 | 의미 | 의사결정 |
|---|---|---|
| 🟥 `FIX_NEEDED` | LLM 이 *진짜 문제* 로 판정 | 다음 스프린트 백로그에 추가 |
| 🟩 `FALSE_POSITIVE` | LLM 이 *오탐* 으로 강등 | Sonar 에 자동 transition (Won't Fix) 시도, 실패 시 라벨로 표시 |
| 🟨 `NEEDS_REVIEW` | LLM 이 *판단 불가* (컨텍스트 부족) | 사람 검수 필요 — 보통 4~10% |

#### 5.9.2 Pre-training Report (02 산출)

Jenkins UI → `02 코드 사전학습` 빌드 페이지 → 좌측 *"Pre-training Report"* 탭

내용:

| 카드 | 무엇을 보나 |
|---|---|
| 분석 파일 | 청킹 대상 파일 수 / 라인 수 / 언어 분포 |
| 청크 통계 | 총 청크 수 / 평균 길이 / 분포 |
| 호출 관계 | tree-sitter 가 추출한 호출자·callees 수 (mapping rate %) |
| 엔드포인트 | `@app.route` / `@RestController` 등 검출된 API 엔드포인트 |
| 데코레이터 | 함수에 붙은 데코레이터 분포 |
| 테스트 연결 | test_for 매핑 (mocha/jest/pytest) |
| 도메인 모델 | 명시적 도메인 클래스 (ER / DTO / Entity) |
| 문서화 | docstring / JSDoc 추출 비율 |

#### 5.9.3 RAG Diagnostic Report (04 산출 — 4-stage 학습진단)

Jenkins UI → `04 결과분석+이슈등록` 빌드 페이지 → 좌측 *"RAG Diagnostic Report"* 탭

PM 친화 4 단계 사다리 (**위→아래로 인과 추적**):

```
① Scope        ← KB 가 분석 대상 프로젝트의 어느 영역까지 커버하는가
   (📁 분석 파일 / 🌐 검출된 엔드포인트 / 🛡️ 보안 룰)
   ↓ (이 영역이 좁다면 Depth 도 빈약할 수 있음)
② Depth        ← 한 영역에 대한 KB 인덱싱이 얼마나 깊은가
   (호출 매핑 / 테스트 연결 / 데코레이터 / docstring)
   ↓ (Depth 가 부족하면 Quality 가 영향 받음)
③ Quality      ← KB 청크의 본문 자체 품질
   (parser 성공률 / 중복 제거 / vendor 청크 필터)
   ↓ (이 셋이 모두 좋아야 LLM 이 인용 가능)
④ Impact       ← LLM 응답에 얼마나 인용되었나 (citation rate)
   (전체 75% / dense 45% / sparse 18% / graph 35%)
```

PM 의 사용법:
- ④ Impact 의 citation rate 가 낮으면 → ② Depth 부족 → 02 사전학습 KB 강화 필요
- ④ 의 source_layer 분포에서 graph 비율이 높으면 → 호출 관계 신호가 효과적
- "AI 가 알아낸 우리 프로젝트 API 진입점" 펼침 박스 — 자동 검출된 엔드포인트 목록

#### 5.9.4 SonarQube UI (03 산출)

http://localhost:29000 → 로그인 → "Projects" → `realworld` → "Issues"

내용: Sonar 가 정적분석으로 찾은 이슈 목록. severity (BLOCKER / CRITICAL / MAJOR / MINOR / INFO) +
status (OPEN / CONFIRMED / RESOLVED 등) + Rule 별 분류.

> 본 스택의 04 가 GitLab 으로 *번역해서* 등록하므로, PM 은 보통 GitLab 이슈만 보면 됩니다.
> SonarQube UI 는 *원본 Rule 룰북·과거 이력 추적* 용.

#### 5.9.5 AI 평가 summary.html (05 산출)

Jenkins UI → `05 AI 평가` 빌드 페이지 → 좌측 *"AI 평가 Summary"* 탭

내용:

| 섹션 | 무엇을 보나 |
|---|---|
| 임원 요약 (LLM) | 한 단락 자연어 요약 |
| 11 지표 카드 | 답변 관련성 / 환각 / RAG 정확성 / 다중턴 일관성 / 응답 시간 / 토큰 사용량 등 |
| 실패 드릴다운 | 채점 실패한 케이스 목록 + 심판 모델의 판정 이유 |
| 회귀 비교 | 직전 빌드 대비 지표 변화 (회귀 / 개선 / 무변화) |

#### 5.9.6 chain_summary.json (01 산출)

Jenkins UI → `01 코드 분석 체인` 빌드 페이지 → 좌측 *"Build Artifacts"* → `chain_summary.json` 클릭

내용 예시:

```json
{
  "commit_sha": "a1b2c3d4...",
  "stages": {
    "02_pretraining": {"status": "SUCCESS", "duration_min": 5.2, "chunks_indexed": 327},
    "03_static_analysis": {"status": "SUCCESS", "duration_min": 3.1, "issues_found": 47},
    "04_issue_enrichment": {"status": "SUCCESS", "duration_min": 18.4, "gitlab_issues_created": 41}
  },
  "started_at": "2026-04-26T10:00:00Z",
  "finished_at": "2026-04-26T10:30:00Z"
}
```

CI / 외부 모니터링 연동에 활용.

---

## 6. 기타

### 6.1 자주 발생하는 문제 (트러블슈팅)

#### 6.1.1 Docker 메모리 부족 (`out of memory`)

증상: 컨테이너 기동 직후 OOM kill, supervisor program 무한 RESTART.

```bash
docker stats --no-stream    # ttc-allinone 메모리 사용량 확인
```

해결:
- macOS: Docker Desktop → Settings → Resources → Memory 24 GB+
- WSL2: `~/.wslconfig` 의 `memory=56GB`. PowerShell 관리자 모드에서 `wsl --shutdown` 후 재실행.

#### 6.1.2 Ollama pull 이 매우 느리거나 멈춘 것 같다

⏱ `gemma4:e4b` 9.6 GB 는 회선 속도에 따라 30 분 ~ 3 시간. 진행률 막대가 천천히 움직이는 게 정상.

```bash
# 다른 셸에서 진행 확인
du -sh ~/.ollama/models/blobs/  # 받은 만큼 늘어남
```

완전히 멈춘 경우 (`du` 가 5분 이상 변화 없음):

```bash
pkill -f ollama
ollama serve &
sleep 3
ollama pull gemma4:e4b   # 재시도 — Ollama 가 미완료 chunk 부터 이어받음
```

#### 6.1.3 huggingface-cli rate limit (403 / 429)

증상: `huggingface-cli download` 가 `403 Forbidden` 또는 `429 Too Many Requests` 로 실패.

원인: HuggingFace 가 비로그인 IP 에 rate limit 적용. 사내 NAT 공유 IP 면 자주 발생.

해결:
1. https://huggingface.co/join 에서 무료 계정 생성 (없는 경우)
2. https://huggingface.co/settings/tokens 에서 *Read* 권한 토큰 발급
3. 셸에서:
   ```bash
   huggingface-cli login
   # 프롬프트에 토큰 붙여넣기 (입력 시 화면 미표시 - 정상)
   ```
4. 재시도:
   ```bash
   huggingface-cli download BAAI/bge-reranker-v2-m3 \
     --local-dir offline-assets/rerank-models/bge-reranker-v2-m3 \
     --local-dir-use-symlinks=False
   ```

#### 6.1.4 WSL2 에서 `nvidia-smi` 가 RTX 4070 인식 안 됨

증상: WSL2 Ubuntu 셸에서 `nvidia-smi` 가 `Command 'nvidia-smi' not found` 또는 GPU 미검출.

해결 단계:
1. **Windows host 에 NVIDIA 드라이버 설치** (≥ 535) — https://www.nvidia.com/Download/index.aspx → "Game Ready Driver" 또는 "Studio Driver" 둘 다 OK
2. **WSL2 kernel update**:
   ```powershell
   # PowerShell 관리자 모드
   wsl --update
   wsl --shutdown
   ```
3. **WSL2 Ubuntu 셸 재진입 후 검증**:
   ```bash
   nvidia-smi    # RTX 4070 + 드라이버 버전 표시되어야 정상
   ```

> **추가 점검** — `nvidia-smi` 가 *나오긴 하는데 driver/library version mismatch* 라면, Windows
> 호스트의 NVIDIA 드라이버를 최신으로 재설치 후 `wsl --update`.

#### 6.1.5 USB 가 FAT32 라 11 GB tarball 이 안 들어감

증상: `cp` 시 `File too large` 또는 `Operation not supported`.

해결:
- USB 백업 → exFAT 으로 재포맷:
  - macOS: 디스크 유틸리티 → USB 선택 → "지우기" → "포맷: exFAT"
  - Windows: 파일 탐색기 → USB 마우스 우클릭 → "포맷..." → "파일 시스템: exFAT"
- 외장 SSD / NAS 사용도 가능 (단일 파일 한계 없음)

#### 6.1.6 컨테이너에서 호스트 Ollama 에 연결 못함

```bash
docker exec ttc-allinone curl -sf http://host.docker.internal:11434/api/tags
```

실패 시:
- macOS: Ollama 가 localhost 만 listen → `launchctl setenv OLLAMA_HOST "0.0.0.0"` 후 Ollama 재기동
- WSL2: compose 파일에 `extra_hosts: ["host.docker.internal:host-gateway"]` 적용 확인 (기본값에 포함)

#### 6.1.7 Phase 0 신규 컴포넌트 헬스 실패

```bash
docker exec ttc-allinone supervisorctl status meilisearch falkordb retrieve-svc
```

- **Meilisearch `STARTING`** → `/data/logs/meilisearch.err.log` 확인. binary 누락 시 빌드 시 `offline-assets/meilisearch/` 가 비어 있었을 가능성.
- **FalkorDB `loadmodule` 실패** → `/usr/local/lib/falkordb.so` 부재 또는 `libgomp1` 누락. `docker exec ttc-allinone ldd /usr/local/lib/falkordb.so` 로 의존성 확인.
- **retrieve-svc `STARTING` 1 분+** → sentence-transformers + bge-reranker-v2-m3 모델 로드 중 (~10~20초 정상). 1 분 넘으면 `/data/logs/retrieve-svc.err.log` 확인.

**임시 폴백** — retrieve-svc 만 끄고 Dify 내장 retrieve 로 동작 (현재 기본 동작 모드):

```bash
docker exec ttc-allinone supervisorctl stop retrieve-svc
```

#### 6.1.8 02 ↔ 04 동시 구동 (lock 미동작)

증상: 02 실행 중 04 manual trigger 가 *기다리지 않고 동시 실행*.

```bash
docker exec ttc-allinone ls /opt/seed/jenkins-plugins/ | grep -i lockable
# → lockable-resources.jpi 가 보여야 정상
```

미발견 시 — `.plugins.txt` 에 `lockable-resources:latest` 라인 있는지 확인 → A 머신에서
`bash scripts/download-plugins.sh` 재실행 → 이미지 재빌드 + 재반입.

#### 6.1.9 GitLab `(health: starting)` 가 5 분 이상 지속

`docker logs ttc-gitlab | tail -50` 으로 reconfigure 진행 확인. WSL2 에서 흔한 원인은 메모리 부족 — `.wslconfig` 의 `memory=` 값 점검.

#### 6.1.10 SonarQube `flood_stage` → read-only

`docker exec ttc-allinone du -sh /data` 로 디스크 사용량 확인. 95% 이상이면 ES flood watermark 발동. 호스트 Docker Desktop disk image 크기 확장 또는 macOS 의 `SONAR_DATA_HOST` overlay 사용 (compose mac 기본 적용됨).

#### 6.1.11 첫 빌드 실행 시 "Job is not parameterized" HTTP 400

Declarative pipeline 의 parameters 블록이 아직 Jenkins config.xml 에 등록되지 않은 상태. **한 번
`Build Now` 로 돌려 실패한 뒤** `Build with Parameters` 재실행.

#### 6.1.12 Korean Job 이름 mojibake (`No item named 01-ì½ë...`)

Jenkins JVM 이 UTF-8 로 기동되지 않음:

```bash
docker exec ttc-allinone ps ax | grep jenkins.war | grep -oE "Dfile.encoding=[A-Z0-9-]+"
# → Dfile.encoding=UTF-8   (이게 있어야 정상)
```

본 스택은 supervisord.conf 에 이미 반영. 옛 이미지면 재빌드 필요.

#### 6.1.13 sudo 비밀번호를 잊어버렸다 (WSL2 Ubuntu)

```powershell
# PowerShell 관리자 모드
wsl -u root
# 이제 root 셸 진입 — 본인 사용자 비밀번호 재설정
passwd <본인_사용자명>
exit
wsl --shutdown
```

이후 Ubuntu 다시 열고 새 비밀번호로 `sudo` 실행.

#### 6.1.14 디스크 부족 (`no space left on device`)

```bash
docker system df          # 사용량 확인
docker system prune -a    # 사용 안 하는 이미지·컨테이너·캐시 제거 (확인 메시지에 y)
```

또는 Docker Desktop → Resources → Disk image size 늘림.

### 6.2 데이터 초기화 / 재프로비저닝

전체 데이터 초기화 (다음 기동 시 provision.sh 가 다시 실행됨):

```bash
docker compose -f docker-compose.{wsl2,mac}.yaml down -v   # -v 로 named volume 삭제
bash scripts/run-{wsl2,mac}.sh                              # 재기동
```

provision 만 재실행 (컨테이너 살린 채):

```bash
docker exec ttc-allinone rm -f /data/.app_provisioned
docker exec ttc-allinone bash /opt/provision.sh
```

### 6.3 현재 구현 상태

| 영역 | 상태 |
|---|---|
| **5 파이프라인** (01~05) | ✅ 운영 가능 |
| **단일 이미지 + supervisor 14 process** | ✅ 운영 가능 |
| **Phase 0 인프라** (Meilisearch + FalkorDB + retrieve-svc + bge-reranker weight) | ✅ 컨테이너 안 구동 (헬스 endpoint 응답) |
| **Phase 0 Hybrid Retrieve 사용** — 02 sink (Meili/Falkor 적재) + 04 Workflow datasource 교체 | ⏳ **미완** — 현재 04 는 여전히 Dify 내장 dense retrieve (Qdrant + bge-m3) 사용. retrieve-svc 는 *기동만 됨*, Dify Workflow 와 연결은 Phase 4 작업 |
| **02 ↔ 04 동시 구동 lock** | ✅ 02 / 04 jenkinsfile 에 적용 |
| **양 머신 동일 모델 (gemma4:e4b · qwen3-embedding:0.6b · bge-reranker-v2-m3)** | ✅ 적용 (3-1-c 의 `ollama pull` 단계) |
| **Phase 8 — Joern CPG 정식 도입** (`02b 코드 CPG batch` Job + taint·data flow 질의) | ❌ **미구현** — 계획만 존재. PLAN §10 Phase 8 참조 |

> **결과 영향** — Phase 0 인프라가 *기동만 되어 있어도* 본 스택의 5 파이프라인은 모두 정상 동작합니다.
> retrieve-svc 가 사용되지 않는 동안에도 04 는 Dify 내장 dense retrieve 로 작동 — 사용자 시점에서
> 차이는 *분석 정확도가 미래 Phase 4 적용 후보다 약간 낮을 수 있음* 정도.

### 6.4 관련 문서

| 문서 | 무엇이 있나 |
|---|---|
| [docs/SESSION_DECISIONS_2026-04-26.md](docs/SESSION_DECISIONS_2026-04-26.md) | **세션 의사결정 통합 정리** — 한 페이지로 보는 세션 전체 결정·정정·산출물 |
| [docs/PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md](docs/PLAN_CODE_RAG_AND_SPEC_TRACEABILITY.md) | 시스템 설계 결정 로그 (왜 이렇게 만들었나) |
| [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) | 운영 일자 캘린더 + GO/NO-GO 게이트 + 리스크 레지스터 |
| [docs/IMPLEMENTATION_RATE_PM_GUIDE.md](docs/IMPLEMENTATION_RATE_PM_GUIDE.md) | 요구사항 구현율 측정 — 비개발자·PM 시점 안내서 |
| [docs/SPEC_TRACEABILITY_DESIGN.md](docs/SPEC_TRACEABILITY_DESIGN.md) | Phase 7 (Spec↔Code 추적성) 시스템 설계 |
| [docs/PIPELINE_04_DESIGN_AND_EVOLUTION.md](docs/PIPELINE_04_DESIGN_AND_EVOLUTION.md) | 04 정적분석 결과분석 + 사이클별 회고 |
| [docs/PLAN_AI_EVAL_PIPELINE.md](docs/PLAN_AI_EVAL_PIPELINE.md) | 05 AI 평가 파이프라인 설계 |

### 6.5 라이선스 (주요 컴포넌트)

| 컴포넌트 | 라이선스 |
|---|---|
| Jenkins | MIT |
| Dify | Apache 2.0 |
| SonarQube Community | LGPL v3 |
| GitLab CE | MIT |
| Qdrant | Apache 2.0 |
| Meilisearch | MIT |
| FalkorDB | Apache 2.0 |
| Ollama | MIT |
| `gemma4:e4b` (Google Gemma) | Gemma Terms of Use |
| `qwen3-embedding:0.6b` | Apache 2.0 |
| `bge-reranker-v2-m3` (BAAI) | Apache 2.0 |
| sentence-transformers | Apache 2.0 |
| tree-sitter | MIT |

본 스택의 자체 코드 (`pipeline-scripts/`, `retrieve-svc/`, `eval_runner/`, `scripts/`) 라이선스는
별도 명시가 없으면 사용 조직 정책에 따릅니다.

---

**문서 버전**: 3.0 (2026-04-26 — 비개발자 친화 전면 보강)
**대상 독자**: 본 스택을 *제로베이스에서* 처음 받아 폐쇄망 환경에서 운영하려는 분 — 컴퓨터 명령어를
거의 처음 사용하는 분도 따라갈 수 있는 수준.
**유지보수 방침**: 코드 변경 시 본 README 의 *현재 구현 상태 표 (§6.3)* 와 *파라미터 표 (§5)* 가
가장 먼저 갱신되어야 합니다.
