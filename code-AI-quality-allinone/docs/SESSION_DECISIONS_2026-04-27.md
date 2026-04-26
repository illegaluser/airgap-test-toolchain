# 협업 세션 의사결정 통합 정리 — 2026-04-27

> **이 문서가 무엇인가**
> 2026-04-27 협업 세션 (`feat/code-rag-hybrid-retrieve` 브랜치) 동안 수행한
> *호스트 사전 설치 → 자산 다운로드 → 통합 이미지 빌드* 까지의 모든 의사결정·정정·구현·시스템 변경을 한 곳에 통합 정리.
>
> **선행 문서**: [SESSION_DECISIONS_2026-04-26.md](SESSION_DECISIONS_2026-04-26.md) (Phase 0 인프라·모델 라인업·README 전면 재작성).
> **본 문서**: 2026-04-26 의 의사결정을 *실제 환경에 적용 + 빌드 산출물 생성* 한 시점의 누적 스냅샷.

---

## 0. 한 페이지 요약 (TL;DR)

| 영역 | 결과 |
|---|---|
| **호스트 사전 설치** | `setup-host.sh` 4곳 패치 (apt 사전 체크, venv 기반 hf-cli, Case B Ollama 검증, PATH 보강) — Ubuntu 24.04 PEP 668 / non-interactive shell PATH 트랩 모두 회피 |
| **WSL2 네트워킹** | Windows Ollama 를 `0.0.0.0:11434` 로 바인딩 + `~/.wslconfig` mirrored networking 활성 → WSL2 `localhost:11434` 가 호스트 daemon 에 직접 도달 (Case B 정상 동작) |
| **자산 다운로드** | `download-rag-bundle.sh` URL 핀 정정 (`v1.42` 미존재 → `v1.42.1`) + non-interactive PATH self-prepend. 4/5 단계 완료 (플러그인·Meilisearch·bge-reranker·FalkorDB), Ollama 모델은 Case B 로 호스트에서 별도 pull 로 위임 |
| **통합 이미지 빌드** | `ttc-allinone:wsl2-dev` (6.44 GB) 빌드 완료 — WSL2 native ext4 (`~/ttc-build`) + BuildKit, ~5분 |
| **Docker 29.4 buildx 정책 확인** | `docker build` 가 사실상 `docker buildx build` 의 alias 임을 대화 중 실증 (`docker build --help` → `Usage: docker buildx build`). 따라서 build-wsl2.sh / offline-prefetch.sh 모두 buildx 사용 — 의도된/강제된 동작 |
| **GitLab 반출 정책** | All-in-one 이미지는 그대로(미포함). 반출 패키지(offline-prefetch) 가 GitLab + Dify Sandbox 도 함께 별도 tarball 로 묶음. 폐쇄망에서 `docker load` 후 `docker compose up` — 기존 운용 유지 |
| **Dify 워크플로우 & 지식체계** | 변경 없음. 이미 `/opt/dify-assets/` 에 COPY (Dockerfile:339) + `provision.sh` 가 컨테이너 기동 시 자동 import — 빌드된 이미지 안에 그대로 포함됨 |

---

## 1. 호스트 사전 설치 (`setup-host.sh`) 패치

### 1.1 발단
사용자 환경 (Windows + WSL2 Ubuntu 24.04) 에서 `setup-host.sh` 를 처음 돌리면서 두 가지 막힘 발생:
- `sudo apt update` 가 비대화식 환경에서 비밀번호 대기로 멈춤 (이미 모든 패키지 설치된 경우에도)
- `pip install --user huggingface_hub[cli]` 가 PEP 668 (externally-managed-environment) 정책에 막힘

### 1.2 패치 4건 ([scripts/setup-host.sh](../scripts/setup-host.sh))

| # | 함수 | 변경 |
|---|---|---|
| 1 | `install_packages_wsl2_linux()` | `dpkg -s` 사전 체크 추가 — 9개 패키지 모두 설치되어 있으면 `sudo` 호출 자체를 건너뜀. 누락된 것만 골라 `sudo apt install` |
| 2 | `install_hf_cli()` | `pip install --user` → **venv 기반** (`~/.local/share/ttc-venv`) + `~/.local/bin/huggingface-cli` symlink. PEP 668 회피. `huggingface_hub<1.0` 으로 핀 (1.x 부터 `huggingface-cli` 명령 deprecated, `hf` 로 교체됨 — `download-rag-bundle.sh` 가 `huggingface-cli download` 사용하므로 0.x 유지 필수) |
| 3 | `verify_all()` Ollama 검증 | `ollama --version` 호출만 → CLI 가 없어도 daemon 이 응답하면 OK 처리 (Case B 정상). 이전엔 Windows GUI + WSL2 셸 케이스에서 무조건 "ollama 누락" 경고 발생 |
| 4 | `verify_all()` PATH 보강 | `~/.local/bin` 을 명시적으로 PATH 에 prepend. `.bashrc` 가 비대화식 셸에서 early-return 하는 점 보완 |

### 1.3 검증 방법론 정정
초기에 `wsl.exe -e bash -c "..."` (비대화식) 로 검증했고, 매번 `PATH=$HOME/.local/bin:$PATH` 를 명시 prepend 한 환경에서만 통과 — *사용자가 실제로 새 WSL 터미널에서 `huggingface-cli` 입력하는 흐름* 은 단 한 번도 검증하지 않았음. 사용자 지적 후 `.bashrc:5-8` 의 early-return 가드를 직접 확인하고 `bash -ic` 로 재검증.

→ **교훈**: 이후 검증은 사용자 흐름(새 셸·비대화식 호출 양쪽)을 그대로 재현해 확인.

### 1.4 sudo 1회 입력 후 멱등 동작 확인
누락 패키지 4종(`python3-pip`, `python3-venv`, `unzip`, `build-essential`) 설치를 위해 사용자 sudo 비밀번호 1회 입력 → 이후 `bash scripts/setup-host.sh` 재실행 시 모든 단계 sudo 없이 통과 (apt 사전 체크 + huggingface-cli skip).

---

## 2. WSL2 네트워킹 — Case B 활성화

### 2.1 문제
Windows 호스트의 Ollama daemon (GUI 설치) 이 기본 `127.0.0.1:11434` 에만 바인딩 → WSL2 NAT 모드에서 `localhost:11434` 도달 불가 → `setup-host.sh --check` 의 Ollama 검증 실패.

### 2.2 해결 — 시스템 설정 2건

| 설정 | 내용 | 영향 |
|---|---|---|
| Windows 사용자 환경변수 `OLLAMA_HOST=0.0.0.0:11434` | `setx`-equivalent 영구 등록 + Ollama 재기동 | daemon 이 모든 인터페이스에서 LISTEN |
| `C:\Users\csr68\.wslconfig` 신규 (`[wsl2]\nnetworkingMode=mirrored`) | `wsl --shutdown` 후 재기동 | WSL2 가 Windows 호스트의 `localhost` 와 네트워크 네임스페이스를 공유 |

### 2.3 결과
WSL2 셸에서 `curl http://localhost:11434/api/tags` 가 즉시 응답 + `bge-m3` 모델 노출 확인. 이로써 [setup-host.sh:156-168](../scripts/setup-host.sh#L156-L168) 의 Case B (CLI 없음 + daemon 응답) 가 정상 트리거됨.

### 2.4 대안 분석 (참고)
- (대안) WSL2 안에 Ollama 도 별도 설치 — daemon 2개 공존, `bge-m3` 모델 재다운로드 필요. 모델 자산 중복 → 채택 안 함.
- (대안) `setup-host.sh` 에 게이트웨이 IP fallback 검사 추가 — 프로젝트 코드 침습 → 채택 안 함.
- 채택안: 시스템 설정 2건 (위 2.2). 한 번 설정 후 영구. WSL2 전체 재시작 1회 필요.

---

## 3. 자산 다운로드 (`download-rag-bundle.sh`) 패치

### 3.1 패치 ([scripts/download-rag-bundle.sh](../scripts/download-rag-bundle.sh))

| 변경 | 이유 |
|---|---|
| 스크립트 시작에 `[[ -d ~/.local/bin ]] && export PATH=...` prepend | 비대화식 셸에서 `.bashrc` 가 early-return 되는 트랩 회피. `require huggingface-cli` 가 fresh non-interactive 호출에서도 통과 |
| Meilisearch URL: `v1.42` → `v1.42.1` | 실제 GitHub 릴리스 태그가 `v1.42.0/v1.42.1` 이라 404 발생. 최신 패치로 핀. README §3 의 수동 설치 코드블록도 동일 정정 |

### 3.2 다운로드 결과
| # | 항목 | 결과 |
|---|---|---|
| 1/5 | Jenkins(58) / Dify(1) 플러그인 | 기존 (skip) |
| 2/5 | Meilisearch v1.42.1 binary (linux-amd64) | 135 MB |
| 3/5 | bge-reranker-v2-m3 weight (BAAI) | 2.2 GB (model.safetensors 등 13 파일) |
| 4/5 | FalkorDB Docker 이미지 | docker pull 완료 |
| 5/5 | Ollama 모델 | Case B 자동 skip (호스트 GUI 사용 중) |

### 3.3 Ollama 모델은 호스트에서 별도 pull
폐쇄망 반출 시 `offline-prefetch.sh` 가 호스트의 `~/.ollama/models/` (또는 Windows `%USERPROFILE%\.ollama\models\`) 에서 모델을 읽어 패키지에 포함. 본 시점 호스트에 `bge-m3` 만 풀 완료, `gemma4:e4b`+`qwen3-embedding:0.6b` 는 사용자가 별도 pull 필요.

---

## 4. 통합 이미지 빌드 — 3차례 시도 끝에 성공

### 4.1 빌드 시도 타임라인

| # | 환경 | 빌더 | 결과 | 학습 |
|---|---|---|---|---|
| 1 | `/mnt/c/...` (NTFS 마운트) | BuildKit | 사용자 의사결정으로 중단 (legacy 시도로 분기) | /mnt/c 빌드 가능하지만 5-10x 느림 (~60-90분 예상) |
| 2 | `~/ttc-build` (WSL2 native ext4, rsync 사본) | `DOCKER_BUILDKIT=0` (legacy) | **실패** — `Step 13/70: TARGETARCH: parameter not set` | 스크립트 주석 ("legacy builder 가 빌드한다") 과 코드(`DOCKER_BUILDKIT=1`) 의 모순을 사용자와 함께 발견. legacy 는 BuildKit 이 자동 주입하던 `TARGETARCH` 미지원 |
| 3 | `~/ttc-build`, BuildKit 복귀 | `DOCKER_BUILDKIT=1` (= `docker buildx build` alias) | **성공** — `ttc-allinone:wsl2-dev` 6.44 GB, ~5분 | Docker 29.4 에서 `docker build` 자체가 buildx alias 임을 실증 |

### 4.2 Docker 29.4 의 buildx 정책 (대화 중 확정)
```
$ docker build --help
Usage:  docker buildx build [OPTIONS] PATH | URL | -
```
**Docker 29.4 부터 `docker build` 는 `docker buildx build` 의 alias**. 즉:
- 우리가 `DOCKER_BUILDKIT=1 docker build` 만 실행해도 내부적으로 buildx 가 처리
- `DOCKER_BUILDKIT=0` 도 deprecation 경고와 함께 점진 제거 예정 (`legacy builder is deprecated and will be removed in a future release`)
- "buildx 안 쓴다" 는 표현은 **cross-platform manifest 운영 (`buildx build --platform=... --output=type=tar`) 을 안 한다** 는 의미로만 유효 — 빌더 자체는 buildx 임

→ build-wsl2.sh / build-mac.sh 의 헤더 주석을 *BuildKit 사용 명시 + legacy 제거 예고 명시* 로 정정.

### 4.3 build-wsl2.sh / build-mac.sh 정정 ([scripts/build-wsl2.sh](../scripts/build-wsl2.sh) / [build-mac.sh](../scripts/build-mac.sh))
**코드 net-zero** (`DOCKER_BUILDKIT=1` 그대로). **주석만 정정**:
- "legacy builder 가 빌드한다" → "**BuildKit 이 빌드한다**"
- "buildx 는 사용하지 않는다" → "**buildx (멀티플랫폼 manifest 플러그인) 는 사용하지 않는다**" (의미 한정)
- legacy builder 제거 예고 추가

### 4.4 빌드 산출물

| 항목 | 값 |
|---|---|
| 이미지 | `ttc-allinone:wsl2-dev` |
| Image ID | `e619df3d9578` |
| 크기 | 6.44 GB |
| 빌드 위치 | `~/ttc-build` (WSL2 native ext4) — `/mnt/c` 에서 native 로 rsync (2.4 GB, 18초) |
| 빌드 시간 | ~5분 (BuildKit 병렬 + native FS) |
| Layer export | 82.2초 |
| 모든 Dockerfile 단계 | 70개 STEP, 59 BuildKit DONE 마커 |

### 4.5 빌드 컨텍스트 결정 — `/mnt/c` vs WSL2 native
사용자가 옵션 B (WSL2 native rsync) 선택. 효과: 빌드 시간 60-90분 → 5분.

→ **권고**: 향후 WSL2 빌드는 항상 native FS 에서 수행. `/mnt/c` 직접 빌드는 디버깅/일회성에만 사용.

---

## 5. 빌드 후 점검 — Dify 워크플로우/지식체계는 *이미* 통합됨

### 5.1 자산 위치
[scripts/dify-assets/](../scripts/dify-assets/)
- `sonar-analyzer-workflow.yaml` (58 KB) — Dify Workflow DSL v0.1.3 (파이프라인 4 의 LLM 분석 그래프, 입력 8 / 출력 8 필드)
- `code-context-dataset.json` — RAG knowledge dataset 시드

### 5.2 빌드 단계
[Dockerfile:339](../Dockerfile#L339) 에서 `COPY scripts/dify-assets/ /opt/dify-assets/` — 방금 빌드한 이미지 안에 포함됨.

### 5.3 컨테이너 기동 시 자동 import ([scripts/provision.sh](../scripts/provision.sh))
- `dify_import_workflow()` (line 572) → `/console/api/apps/imports` API 호출
- `dify_publish_workflow()` (line 645) → publish + API key 발급
- 그 키를 Jenkins credential `dify-workflow-key` 로 자동 주입 (line 1196)

→ 본 세션에서 Dify 워크플로우/지식체계 관련 *코드/DSL 변경 0건*. 단 사용자 지적으로 *현황 보고 누락* 을 인지하고 명시적으로 보고함.

---

## 6. GitLab 반출 정책 (재확인)

### 6.1 사용자 요지
> "TARBALL 안에 우리가 빌드한 이미지와 GitLab 이미지를 함께 압축하고, 폐쇄망에서 이 이미지들을 전부 로드한 후에 docker compose 를 해왔던 거"

### 6.2 현행 설계 (그대로 유지)
[offline-prefetch.sh](../scripts/offline-prefetch.sh) 가 산출하는 반출 패키지:

| # | tarball | 내용 |
|---|---|---|
| 1 | `ttc-allinone-<arch>-<tag>.tar.gz` | 통합 이미지 (Jenkins+Dify+SonarQube+Postgres+Redis+Qdrant+Meili+FalkorDB+retrieve-svc) |
| 2 | `gitlab-gitlab-ce-18.11.0-ce.0-<arch>.tar.gz` | GitLab CE 공식 이미지 (별도 컨테이너) |
| 3 | `langgenius-dify-sandbox-0.2.10-<arch>.tar.gz` | Dify Code 노드 sandbox |
| + | `*.meta` 3개 | sha256 / size / 빌드시각 |

폐쇄망 머신에서 `docker load -i` 3회 (또는 `offline-load.sh` 1회) → `docker compose up`.

### 6.3 (Docker 이미지 자체에 GitLab 을 박지 않는 이유)
- GitLab 자체가 무거운 자원 (Postgres·Redis·Sidekiq·nginx 자체 포함) 을 가져 supervisor 트리에 합치면 시작 순서·재시작 정책이 복잡
- 공식 `gitlab/gitlab-ce` 이미지가 자체 omnibus 패키지로 self-contained 운영 (config/data/logs volume 분리 깨끗)
- compose 의 `depends_on` 으로 `ttc-allinone` 이 GitLab 시작 후 기동 — 분리가 오히려 깔끔

### 6.4 offline-prefetch.sh 재빌드 처리 정책
[offline-prefetch.sh:73-80](../scripts/offline-prefetch.sh#L73-L80) 가 `docker buildx build --platform $PLATFORM --load` 로 이미지를 *다시 빌드* 함 — `--platform` 명시로 cross-arch 일관성 보장 + tag 분리 (`amd64-dev` vs `wsl2-dev`).

**사용자 결정**: 그대로 유지. 재빌드 5분 추가지만 산출물 명시성 확보.

---

## 7. 본 세션의 코드/문서 변경 — 5 파일

| # | 파일 | 변경 분량 | 성격 |
|---|---|---|---|
| 1 | [scripts/setup-host.sh](../scripts/setup-host.sh) | +71/-8 (79 lines) | functional — apt 사전 체크 / venv 기반 hf-cli / Case B Ollama / PATH 보강 |
| 2 | [scripts/download-rag-bundle.sh](../scripts/download-rag-bundle.sh) | +7/-1 (8 lines) | functional — PATH self-prepend / Meilisearch URL 정정 |
| 3 | [scripts/build-wsl2.sh](../scripts/build-wsl2.sh) | +6/-3 (9 lines) | **주석만** — 코드 net-zero, BuildKit 정책 정렬 |
| 4 | [scripts/build-mac.sh](../scripts/build-mac.sh) | +4/-2 (6 lines) | **주석만** — 동상 |
| 5 | [README.md](../README.md) §2.3 / §3.1-b | +14/-6 (20 lines) | doc — stale 셸 함정 안내 / 0.x 호환 검증 명령 / Meilisearch URL |

---

## 8. 본 세션의 시스템 변경 — 코드 외부

| 영역 | 변경 |
|---|---|
| Windows 환경변수 | `OLLAMA_HOST=0.0.0.0:11434` (User scope, 영구). Ollama 재기동 |
| Windows 사용자 홈 | `C:\Users\csr68\.wslconfig` 신규 (`networkingMode=mirrored`). WSL2 1회 재기동 |
| WSL2 (Ubuntu 24.04) | `~/.bashrc` 마지막에 `~/.local/bin` PATH 추가 (setup-host.sh 가 1회만 추가) |
| WSL2 venv | `~/.local/share/ttc-venv/` 신규 (huggingface_hub 0.36.2 + 의존성) |
| WSL2 symlink | `~/.local/bin/huggingface-cli` → venv |
| WSL2 빌드 작업 디렉토리 | `~/ttc-build/` 신규 (rsync 사본, 2.4 GB, .git 제외) |
| Docker 이미지 | 6 base 이미지 pull (jenkins, sonarqube, dify-api/web/plugin-daemon, falkordb) + `ttc-allinone:wsl2-dev` 6.44 GB 빌드 |

---

## 9. 다음 단계

| 단계 | 명령 | 예상 소요 |
|---|---|---|
| 9.1 (선택) 컨테이너 부팅 검증 | `bash scripts/run-wsl2.sh` | ~10분 (provision.sh 가 Dify import + Jenkins credential 주입까지 자동) |
| 9.2 호스트 Ollama 모델 pull | Windows PowerShell 에서 `ollama pull gemma4:e4b; ollama pull qwen3-embedding:0.6b` | ~10-30분 (회선) |
| 9.3 반출 패키지 생성 | `bash scripts/offline-prefetch.sh --arch amd64` (재빌드 + GitLab/Sandbox pull + tarball 3개) | ~10-15분 |
| 9.4 B 머신 반입·기동·검증 | (다음 세션) | — |

---

## 10. 본 세션에서 학습한 것 (앞으로 재발 방지)

| 교훈 | 적용 방침 |
|---|---|
| 검증할 때 *사용자 흐름 그대로* 재현 (interactive 셸·비대화식 호출 양쪽) | `wsl.exe -e bash -c "..."` (non-interactive) 만으로 끝내지 말 것. `bash -ic` 또는 신규 터미널 시뮬레이션 추가 |
| 사용자 표현이 코드/문서와 *언어상 충돌* 할 때 진행 전 재확인 | 직전 의사결정의 *문자 표면* 만 따르지 말고 *합리적 의도* 를 한 번 더 검증. 이번 빌드 builder 결정에서 한 번 사고함 |
| README/Dockerfile 의 "by design" 주석을 그대로 인용해 답하지 말 것 | 사용자 의도 vs 문서 표현이 다를 수 있음. 의문 시 사용자 의도부터 확인 |
| 버전 핀은 *실제 릴리스 태그* 로 (v1.42 → v1.42.1) | 메이저.마이너만 적힌 URL 은 GitHub 에서 미존재 가능. 핀 시 실제 태그 확인 |
| Ubuntu 24.04+ 의 PEP 668 회피는 *venv 가 표준* | `pip install --user` 대신 venv + symlink 패턴 ([setup-host.sh:284-309](../scripts/setup-host.sh#L284-L309)) |
| `.bashrc` early-return 가드 인지 | 비대화식 셸은 `~/.bashrc` 의 PATH export 를 못 받음. 스크립트는 자체적으로 PATH self-prepend 권장 ([download-rag-bundle.sh:27-31](../scripts/download-rag-bundle.sh#L27-L31)) |
| Docker 29.4: `docker build` = `docker buildx build` alias | "buildx 안 쓴다" 표현은 *manifest 운영 안 한다* 의미로만 사용. 빌더 자체는 buildx |

---

## 11. 본 문서 자체의 갱신 이력

| # | 시점 | 갱신 내용 |
|---|---|---|
| 1 | 2026-04-27 (초안) | 신설 — §0~§10 11 섹션. 본 세션 setup-host 패치 / WSL2 네트워킹 / 자산 다운로드 / 빌드 3차 시도 / Dify·GitLab 정책 재확인 / 5 파일 변경 / 시스템 변경 / 다음 단계 / 학습사항 통합 |

---

**문서 작성**: 2026-04-27 협업 세션
**작성 컨텍스트**: 사용자 요청 — *"현재까지의 대화내역, 의사결정 및 구현사항 모두 문서화하고 현행화해."*
**갱신 방침**: 2026-04-26 문서 패턴 계승. 본 세션 추가 의사결정 시 §11 갱신 이력에 행 추가, 본문은 *증분 추가*. 다른 일자 세션은 별도 `SESSION_DECISIONS_<날짜>.md` 신설.
