# TTC 4-Pipeline All-in-One Integrated Image

4개 파이프라인(+ Phase 1.5 체인 Job)을 **오프라인/폐쇄망**에서 독립 동작시키는 `docker compose` 단일 스택.
형제 폴더 `playwright-allinone/` 스택과 **포트·네트워크·볼륨 모두 격리**되어 공존 가능합니다.

| # | 파이프라인 | 엔진 | Dify 사용 |
|---|-----------|------|-----------|
| **0** | **코드 분석 체인** (오케스트레이터) | `COMMIT_SHA` 한 번 해석 → P1→P2→P3 순차 자동 트리거. `ANALYSIS_MODE` (full\|commit) 로 KB 강제 재빌드 vs 스냅샷 재활용 분기 | — |
| 1 | 코드 사전학습 | `repo_context_builder.py` → Dify Knowledge Base | ✅ |
| 2 | 코드 정적분석 | SonarQube Community + SonarScanner CLI | ❌ |
| 3 | 정적분석 결과분석 + 이슈등록 | `sonar_issue_exporter` → `dify_sonar_issue_analyzer` (Dify Workflow) → `gitlab_issue_creator` | ✅ |
| 4 | AI 평가 | `eval_runner` (DeepEval + Ollama judge + Playwright) | ❌ |

> **파이프라인 0 사용**: Jenkins UI 의 `00-코드-분석-체인` Job 을 `buildWithParameters` 로 실행하면 커밋 하나에 대한 P1(KB 구축)·P2(정적분석)·P3(LLM 분석 + GitLab Issue 등록) 가 사람 개입 없이 완결됩니다. FP 판정 이슈는 Sonar `do_transition` API 로 자동 마킹하되 실패 시 `classification:false_positive` 라벨로 GitLab Issue 를 생성 (Dual-path).

---

## 1. 위치

**모든 자산은 이 디렉터리에 격리되어 있습니다:**
```
airgap-test-toolchain/
└── code-AI-quality-allinone/   ◄── 여기
    ├── Dockerfile
    ├── docker-compose.wsl2.yaml
    ├── docker-compose.mac.yaml
    ├── README.md
    └── scripts/
```

형제 폴더 `playwright-allinone/` 은 별도 독립 스택으로, 본 스택과 포트·볼륨이 격리되어 있어 병렬 공존 가능합니다.

## 2. 스택 구성

```
docker compose -f docker-compose.{wsl2|mac}.yaml up -d
├── ttc-allinone  (통합 이미지: Jenkins + Dify + SonarQube + PG/Redis/Qdrant)
└── gitlab        (amd64: gitlab/gitlab-ce:17.4.2-ce.0 · arm64: yrzr/gitlab-ce-arm64v8:17.4.2-ce.0)
```

### 포트 배정 (형제 스택과 격리)

| 스택 | 호스트 포트 |
|------|------------|
| `playwright-allinone/` | 18080 / 18081 / 50001 |
| **본 스택** | **28080 / 28081 / 29000 / 28090 / 28022 / 50002** |

## 3. 호스트 전제

- **Docker Desktop** (macOS) 또는 **Docker Desktop + WSL2 백엔드** (Windows)
- 호스트에 **Ollama** 데몬 (Mac: Metal / Windows: NVIDIA CUDA)
  - `ollama serve`
  - `ollama pull gemma4:e4b` (Dify Workflow 판단용)
  - `ollama pull qwen3-coder:30b` (AI평가 judge, 선택)
- Docker 에 할당된 메모리 **≥ 12GB**

---

## 4. 빌드

빌드 컨텍스트는 **이 폴더 자체** 입니다 (자체 완결). 스크립트는 위치를 자동으로 계산하니 어디서 실행해도 됩니다.

### WSL2 (Windows)
```bash
cd code-AI-quality-allinone
bash scripts/download-plugins.sh      # 최초 1회 (온라인)
bash scripts/build-wsl2.sh            # → 이미지: ttc-allinone:wsl2-dev
```

### macOS (Apple Silicon)
```bash
cd code-AI-quality-allinone
bash scripts/download-plugins.sh
bash scripts/build-mac.sh
# Intel Mac 또는 amd64 강제:
bash scripts/build-mac.sh --amd64
```

### 오프라인 반출 (tarball)
온라인 머신에서 (**이 폴더 내부**에서 실행):
```bash
cd code-AI-quality-allinone
bash scripts/download-plugins.sh               # 플러그인 바이너리 준비 (온라인)
bash scripts/offline-prefetch.sh --arch amd64  # 빌드 + tarball 산출 (ttc-allinone + gitlab)
# → offline-assets/<arch>/ttc-allinone-<arch>-<tag>.tar.gz
# → offline-assets/<arch>/gitlab-gitlab-ce-<ver>-<arch>.tar.gz
```
`offline-prefetch.sh` 가 **두 이미지 모두** save 합니다 — `ttc-allinone` (통합) + GitLab (분리 런타임). compose 는 후자를 별도 컨테이너로 실행하므로 폐쇄망 반입이 필수입니다. `--arch arm64` 지정 시 GitLab 이미지는 `yrzr/gitlab-ce-arm64v8:17.4.2-ce.0` (arm64 네이티브 커뮤니티 포트) 로 자동 전환됩니다. `--gitlab-image <이미지>` 로 임의 이미지 override 가능.

오프라인 머신에서 (`offline-assets/` 전체를 함께 반입):
```bash
cd code-AI-quality-allinone
bash scripts/offline-load.sh --arch amd64      # 두 tarball 일괄 load
# (또는 수동: docker load -i offline-assets/amd64/*.tar.gz)
docker compose -f docker-compose.wsl2.yaml up -d
```

---

## 5. 기동

compose 파일이 이 디렉터리 안에 있으므로 **이 폴더로 cd 해서** 기동하는 것이 가장 짧습니다.

```bash
cd code-AI-quality-allinone

# 기본 (self-contained)
docker compose -f docker-compose.wsl2.yaml up -d
# 또는 Mac
docker compose -f docker-compose.mac.yaml up -d

# 헬퍼 래퍼
bash scripts/run-wsl2.sh
bash scripts/run-mac.sh
```

데이터 볼륨은 `${HOME}/ttc-allinone-data/{allinone,gitlab}` 에 생성됩니다.

첫 기동 시 **자동 프로비저닝**이 실행되며 15–20분 소요:
- GitLab 초기화(reconfigure) 5–10분
- Dify admin/Provider/Dataset/Workflow/API keys 생성 2분
- Jenkins Credentials 주입 + Job 4개 등록 1분

진행 상황: `docker logs -f ttc-allinone`

---

## 6. 접속

자동 프로비저닝(§7) 완료 **후** 실제로 유효한 자격정보 기준. 모든 비밀번호는 `docker-compose.*.yaml` 의 환경변수로 override 가능.

| 서비스 | URL | ID | 비밀번호 | override env | 용도 |
| ------ | --- | -- | -------- | ------------ | ---- |
| Jenkins | http://localhost:28080 | `admin` | `password` | `jenkins-init/basic-security.groovy` | 4개 Pipeline Job 진입점 |
| Dify | http://localhost:28081 | `admin@ttc.local` | `TtcAdmin!2026` | `DIFY_ADMIN_EMAIL` / `DIFY_ADMIN_PASSWORD` | Workflow/Dataset 편집 |
| SonarQube | http://localhost:29000 | `admin` | `TtcAdmin!2026` *(초기 `admin/admin` 을 provision.sh 가 자동 변경)* | `SONAR_ADMIN_NEW_PASSWORD` | 정적분석 대시보드 |
| GitLab | http://localhost:28090 | `root` | `ChangeMe!Pass` | `GITLAB_ROOT_PASSWORD` | 소스 호스팅 + Issue |
| Ollama | http://host.docker.internal:11434 | — | — | `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | LLM 추론 (호스트 데몬) |

### 6.1 자동 생성·주입된 내부 자격 (Jenkins Credentials)

provision.sh 가 위 서비스들에서 동적 발급해 Jenkins Credentials Store 에 주입하는 자격. 파이프라인은 `credentials('<id>')` 로 참조하며, **리포에는 저장되지 않습니다**.

| Credential ID | 종류 | 발급처 | 사용처 |
| ------------- | ---- | ------ | ------ |
| `gitlab-pat` | GitLab PAT (api, read/write_repository, 유효 364일) | `POST /api/v4/users/1/personal_access_tokens` | 파이프라인 02·03 — git clone / Issue 생성 |
| `sonarqube-token` | SonarQube User Token (`jenkins-auto`) | `POST /api/user_tokens/generate` | 파이프라인 02 — Sonar scanner 인증 |
| `dify-dataset-id` | Dify Knowledge Dataset UUID (`code-context-kb`) | `POST /console/api/datasets` | 파이프라인 01 — 컨텍스트 적재 |
| `dify-knowledge-key` | Dify workspace Dataset API Key (prefix `dataset-`) | `POST /console/api/datasets/api-keys` | 파이프라인 01 — Dataset API 호출 |
| `dify-workflow-key` | Dify App API Key (Sonar Analyzer Workflow) | `POST /console/api/apps/<id>/api-keys` | 파이프라인 03 — Workflow 호출 |

### 6.2 내부 서비스 (컨테이너 내부, 외부 노출 X)

통합 이미지 내에서 supervisord 로 관리되는 보조 서비스. 일반적으로 직접 접근할 필요 없음.

| 서비스 | 접근 | 자격 | override / 경로 |
| ------ | ---- | ---- | --------------- |
| PostgreSQL | `127.0.0.1:5432` (컨테이너 내부) | `postgres` / `difyai123456` | `scripts/pg-init.sh` `PG_PASSWORD` |
| PostgreSQL SonarQube DB user | DB `sonar`, user `sonar` | `sonar` / `sonar` | `scripts/pg-init.sh` `SONAR_PASSWORD` |
| Redis | `127.0.0.1:6379` | (비밀번호 없음) | — |
| Qdrant | `127.0.0.1:6333` | (비밀번호 없음) | — |
| Dify plugin-daemon inner API | `127.0.0.1:5002` | `INNER_API_KEY_FOR_PLUGIN` (scripts/supervisord.conf 내 고정) | supervisord env |
| Dify SECRET_KEY | (Dify 암호화 seed) | `dify-allinone-placeholder-CHANGE-ME-via-env` | **운영 시 반드시 변경** — supervisord env |

### 6.3 프로덕션 전환 시 반드시 교체해야 할 기본값

PoC 단계에서는 그대로 동작하지만, 운영/배포 전에 다음 값들을 교체하세요 (순서 무관).

- `JENKINS_PASSWORD` — `admin/password` 대신 강한 비밀번호. `jenkins-init/basic-security.groovy` 수정 + Dockerfile 재빌드.
- `DIFY_ADMIN_PASSWORD` — 기본 `TtcAdmin!2026`. compose `environment:` 에 `DIFY_ADMIN_PASSWORD` 설정 후 fresh 볼륨으로 재기동.
- `SONAR_ADMIN_NEW_PASSWORD` — 기본 `TtcAdmin!2026`. compose 에 env 추가.
- `GITLAB_ROOT_PASSWORD` — 기본 `ChangeMe!Pass`. compose 에 이미 `GITLAB_ROOT_PASSWORD` env 있음.
- `PG_PASSWORD` / `SONAR_PASSWORD` — `scripts/pg-init.sh` 의 하드코딩 값. 변경 시 Dockerfile 재빌드 + Dify/Sonar 쪽 env 동기화 필요.
- `SECRET_KEY` — `scripts/supervisord.conf` 의 `dify-allinone-placeholder-CHANGE-ME-via-env`. 장기 운영 필수.

## 7. 완전 자동 프로비저닝 범위

`scripts/provision.sh` 가 최초 기동 시 자동 수행 (`/data/.provision/` 상태 캐시로 멱등):

| 대상 | 자동 작업 |
|------|-----------|
| **Dify** | 관리자 setup, 로그인 (base64 password + cookie jar + X-CSRF-Token), Ollama provider 등록, `code-context-kb` Dataset 생성/재사용, `Sonar Issue Analyzer` Workflow import, Dataset API key (workspace-level) + App API key 2종 발급 |
| **GitLab** | oauth password grant → `users/1/personal_access_tokens` 로 root PAT 자동 발급 (만료 364일 — GitLab 17.x 최대 1년 정책 준수) |
| **SonarQube** | ready 대기 → admin 초기 비밀번호 변경 (`admin` → `SONAR_ADMIN_NEW_PASSWORD`) → 사용자 토큰 발급 (`jenkins-auto`) |
| **Jenkins Credentials** | `dify-dataset-id`, `dify-knowledge-key`, `dify-workflow-key`, `gitlab-pat`, **`sonarqube-token`** 5종 주입 |
| **Jenkins Jobs** | 5개 Job 등록 (00 체인 + 01~04) |
| **Jenkinsfile patch** | `GITLAB_PAT = ''` → `credentials('gitlab-pat')` / `GITLAB_TOKEN=""` → `GITLAB_TOKEN="${GITLAB_PAT}"` 런타임 치환 |

### 자동화되지 않는 잔존 수동 작업

- **GitLab 프로젝트 생성**: 파이프라인 1/2/3 이 분석할 대상 프로젝트는 팀 정책에 따라 수동 생성 또는 기존 소스 push.

---

## 8. 파이프라인 4 AI평가 — 내장 Playwright

`TARGET_TYPE=ui_chat` 일 때 UI 자동화가 필요합니다. 통합 이미지에 Chromium + Playwright 설치본이 포함되어 있어 [eval_runner/adapters/browser_adapter.py](eval_runner/adapters/browser_adapter.py) 가 컨테이너 내부 headless 모드로 즉시 동작합니다 — 별도 설정 불필요.

---

## 9. 파일 구성

```
code-AI-quality-allinone/                   ← 빌드 컨텍스트
├── Dockerfile                              # 통합 이미지 정의
├── docker-compose.wsl2.yaml                # WSL2 + gitlab
├── docker-compose.mac.yaml                 # Mac + gitlab
├── README.md                               # 본 문서
├── requirements.txt                        # Python 기반 deps (playwright/deepeval 등)
├── pipeline-scripts/                       # 파이프라인 1·3 Python 스크립트 스냅샷
│   ├── repo_context_builder.py
│   ├── doc_processor.py
│   ├── sonar_issue_exporter.py
│   ├── dify_sonar_issue_analyzer.py
│   └── gitlab_issue_creator.py
├── eval_runner/                            # 파이프라인 4 엔진 (스냅샷)
├── jenkinsfiles/                           # 4개 Jenkins Pipeline 정의 (스냅샷)
│   ├── 00 코드 분석 체인.jenkinsPipeline         # Phase 1.5 — P1→P2→P3 오케스트레이터
│   ├── 01 코드 사전학습.jenkinsPipeline
│   ├── 02 코드 정적분석.jenkinsPipeline
│   ├── 03 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline
│   └── 04 AI평가.jenkinsPipeline
├── jenkins-init/basic-security.groovy      # Jenkins 관리자 초기화
└── scripts/
    ├── download-plugins.sh                 # 빌드 전 플러그인 다운로드 (온라인)
    ├── supervisord.conf                    # 11개 프로세스 관리 (sonarqube 포함)
    ├── nginx.conf                          # Dify gateway (28081)
    ├── pg-init.sh                          # Postgres initdb (dify/dify_plugin/sonar)
    ├── entrypoint.sh                       # 컨테이너 진입점
    ├── provision.sh                        # 완전 자동 프로비저닝
    ├── requirements-pipelines.txt          # 파이프라인 4 추가 deps
    ├── offline-prefetch.sh                 # tarball 산출 (docker save)
    ├── build-wsl2.sh / build-mac.sh        # 빌드 헬퍼
    ├── run-wsl2.sh / run-mac.sh            # 기동 헬퍼
    ├── offline-load.sh                     # 오프라인 머신 일괄 load (ttc-allinone + gitlab)
    ├── dify-assets/
    │   ├── sonar-analyzer-workflow.yaml    # 파이프라인 3 Workflow DSL
    │   └── code-context-dataset.json       # 파이프라인 1 Dataset 스펙
    └── jenkins-seed/                       # (future) JCasC seed

# 빌드 시 생성 (gitignored)
├── jenkins-plugin-manager.jar
├── jenkins-plugins/
├── dify-plugins/
├── .plugins.txt
└── offline-assets/
```

## 10. 자체 완결 구조

이 폴더는 **자체 완결**입니다. 폴더만 압축해서 다른 워크스테이션으로 옮겨도 두 명령으로 빌드 및 기동이 가능합니다:

```bash
bash scripts/download-plugins.sh   # 온라인 필요 (Jenkins + Dify 플러그인 다운로드)
bash scripts/build-wsl2.sh         # (또는 build-mac.sh) — 오프라인 빌드 가능
```

### 내재화된 자산 (스냅샷)

| 자산 | 폴더 내 위치 | 비고 |
|------|--------------|------|
| 파이프라인 Python 스크립트 5개 | `pipeline-scripts/` | 파이프라인 1·3 이 호출 |
| `eval_runner/` | `eval_runner/` | 파이프라인 4 엔진 |
| Jenkinsfile 5개 | `jenkinsfiles/` | 00 체인 + 01~04 Job 정의 |
| `basic-security.groovy` | `jenkins-init/` | Jenkins 관리자 초기화 |
| `requirements.txt` | `requirements.txt` | 파이프라인 1-4 공통 Python 기반 (playwright/deepeval 등) |

### 빌드 시 생성되는 바이너리 (gitignored)

| 산출물 | 생성 스크립트 | 크기 |
|--------|--------------|------|
| `jenkins-plugin-manager.jar` | `scripts/download-plugins.sh` | ~7 MB |
| `jenkins-plugins/*.jpi` | 위 스크립트 [1/2] | ~40 MB |
| `dify-plugins/*.difypkg` | 위 스크립트 [2/2] | ~1 MB |
| `offline-assets/<arch>/ttc-allinone-*.tar.gz` | `scripts/offline-prefetch.sh` | ~8 GB |
| `offline-assets/<arch>/gitlab-gitlab-ce-*.tar.gz` | `scripts/offline-prefetch.sh` | ~1 GB |

형제 폴더 **`playwright-allinone/`** 과 포트·볼륨이 격리되어 **병렬 공존 가능**합니다.

---

## 11. 트러블슈팅

| 증상 | 확인 | 대응 |
|------|------|------|
| SonarQube 기동 실패 | `docker exec ttc-allinone cat /data/logs/sonarqube.log` | ES mmap 관련이면 호스트 `sysctl -w vm.max_map_count=262144` (WSL2 는 `/etc/sysctl.conf` 영구화) |
| GitLab 계속 unhealthy | `docker logs ttc-gitlab` | reconfigure 는 5-10분 소요. `docker exec ttc-gitlab gitlab-ctl status` 로 각 서비스 확인 |
| Dify 자동 Workflow import 실패 | `/data/logs/provision.log` + `/data/.provision/` | 수동 import: Dify Studio → DSL 에서 가져오기 → `/opt/dify-assets/sonar-analyzer-workflow.yaml` |
| Jenkins Job 이 Ollama 에 도달 못함 | `docker exec ttc-allinone curl host.docker.internal:11434/api/tags` | Mac/Windows Docker Desktop 은 자동 해석. Linux 는 compose `extra_hosts` 로 매핑됨 |
| GitLab PAT 발급 실패 | `docker exec ttc-allinone bash /opt/provision.sh` 재실행 | 첫 실행 시 GitLab reconfigure 완료 전이면 발급 실패 가능. 15분 뒤 재시도 |
| Jenkins Credential 주입 반복 실패 | `docker logs ttc-allinone \| grep Credential` | `rm /data/.provision/*` 후 provision 재실행 |

## 12. 재프로비저닝 (완전 초기화)

```bash
cd code-AI-quality-allinone
docker compose -f docker-compose.wsl2.yaml down
rm -rf ~/ttc-allinone-data
docker compose -f docker-compose.wsl2.yaml up -d
```

## 13. 스택 관계도

```
┌─────────────────────────────────────────────────────────┐
│  docker compose (ttc-net)                               │
│                                                          │
│  ┌──────────────────────┐        ┌──────────────────┐  │
│  │ ttc-allinone          │◄──────│ gitlab           │  │
│  │  - Jenkins  :28080   │        │  :28090 / :28022 │  │
│  │  - Dify     :28081   │        │                  │  │
│  │  - Sonar    :29000   │        └──────────────────┘  │
│  │  - PG/Redis/Qdrant    │                              │
│  │  - Chromium(Playwright)│                             │
│  └──────────┬────────────┘                              │
│             │                                            │
│             ▼ host.docker.internal:11434                │
└─────────────┼────────────────────────────────────────────┘
              │
              ▼
     ┌─────────────────┐
     │ 호스트 Ollama    │
     │ (Metal / CUDA)   │
     └─────────────────┘
```
