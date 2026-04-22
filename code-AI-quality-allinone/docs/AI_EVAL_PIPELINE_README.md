# AI 평가 파이프라인 (Job 04 AI평가) — 사용자 가이드

본 문서는 Jenkins Job **`04-AI평가`** 의 처음부터 끝까지를 한 문서로 다룹니다. 이 문서만 보고도 빌드·기동·평가 실행·결과 해석까지 직접 따라 할 수 있도록 작성했습니다.

다루는 범위:

- **§1 Pipeline 의의** — 왜 이 파이프라인이 존재하는가 + 무엇을 어떻게 평가하는가
- **§2 빌드 / 구동 / 프로비저닝** — 처음 받은 PC 에서 그대로 따라 할 수 있는 단계별 가이드
- **§3 사용법 + 결과 해설** — 평가 실행부터 summary.html 화면 각 영역의 의미까지

---

## §1. Pipeline 의의

### 1.1 한 줄 요약

여러분이 만든 **AI 에이전트(챗봇, RAG 시스템, LLM 워크플로 등)** 가 정해진 시험지(Golden Dataset)에 얼마나 잘 답하는지를, **다른 LLM 을 심판으로 두어 자동 채점**하고 그 결과를 사람이 읽기 쉬운 리포트로 내놓는 Jenkins 파이프라인입니다.

### 1.2 문제 의식

전통적인 SW 테스트는 "정답 = 고정 문자열" 매칭으로 끝나지만, AI/LLM 응답은 같은 의미라도 표현이 매번 다릅니다. 단순 정규식만으로는 평가가 불가능하고, 매번 사람이 읽고 점수 매기기엔 비용이 큽니다. 이 파이프라인이 해결하려는 것:

| 사람의 수동 평가가 어려운 이유 | 본 파이프라인의 해결 방식 |
|---|---|
| 응답 표현 다양성 | LLM-as-Judge: 답변의 **의미**를 LLM 심판이 해석해서 채점 |
| 평가 기준 일관성 | DeepEval 의 표준 메트릭(GEval, AnswerRelevancy, Faithfulness 등) 으로 채점 기준 자체를 표준화 |
| 회귀 감지 누락 | 매 빌드마다 동일한 Golden Dataset 으로 재실행, 결과를 build-N 별로 누적 |
| 결과 해석의 어려움 | summary.html 에 LLM 이 직접 자연어 요약 + 실패 원인 + 권장 조치까지 생성 |
| 외부 클라우드 의존 | **에어갭(Airgap)** 환경에서 호스트 PC 의 로컬 Ollama 만 사용 — 데이터/프롬프트 외부 유출 0 |

### 1.3 평가 지표 — 11 항목 × 5 단계

한 건의 평가(대화 또는 단일 질의)는 다음 5 단계를 차례로 통과하며 총 11 개 지표로 채점됩니다. 앞 단계가 실패하면 뒷 단계는 "skipped" 로 기록되고 종료됩니다 (Fail-Fast).

#### 1단계 Fail-Fast — 답변을 읽기 전, 포맷·정책 먼저 걸러냄

| 지표 | 설명 | 통과 기준 |
|---|---|---|
| ① Policy Violation | 응답에 PII(주민등록번호·이메일·전화번호), API key, 금칙어 등 보안 패턴이 있는지 정규식으로 검사. 대상 AI 가 민감정보를 노출하면 이 단계에서 바로 차단됩니다. | 위반 패턴 0 건 |
| ② Format Compliance | 응답이 사전 정의된 JSON 스키마를 준수하는지 jsonschema 로 검증. `TARGET_TYPE=http` 에서만 의미가 있고, `ui_chat` 은 비정형 텍스트이므로 자동 skip. | 스키마 일치 |

#### 2단계 과제검사 — 질문의 본래 의도를 충족했는가

| 지표 | 설명 | 통과 기준 |
|---|---|---|
| ③ Task Completion | 시험지의 `success_criteria` 컬럼에 작성한 합격 기준(자연어 또는 DSL) 을 답변이 만족하는지 LLM Judge(또는 결정론적 매칭) 가 채점. | 점수 ≥ `TASK_COMPLETION_THRESHOLD` (기본 0.5) |

#### 3단계 심층평가 — 답변의 질을 본격적으로 채점

| 지표 | 설명 | 통과 기준 |
|---|---|---|
| ④ Answer Relevancy | 질문과 답변이 의미적으로 관련 있는지 LLM Judge 가 평가. 동문서답 여부 판별. | 점수 ≥ `ANSWER_RELEVANCY_THRESHOLD` (기본 0.7, 빌드 파라미터로 조정) |
| ⑤ Toxicity | 유해·공격적·차별적 표현이 포함됐는지 (낮을수록 좋음). | 점수 ≤ 0.5 |
| ⑥ Faithfulness * | 답변이 검색된 문맥(`retrieval_context`) 에 근거하는지 — 환각(hallucination) 여부. RAG 시스템 평가의 핵심. | 점수 ≥ 0.7 |
| ⑦ Contextual Recall * | 정답에 필요한 정보가 retrieval 결과에 얼마나 포함됐는지. 검색 단계가 놓친 정보 파악. | 점수 ≥ 0.7 |
| ⑧ Contextual Precision * | retrieval 결과 중 실제 정답과 관련된 비율. 불필요한 문서가 섞여 있으면 점수 하락. | 점수 ≥ 0.7 |

`*` 표시된 ⑥⑦⑧ 은 시험지의 `context_ground_truth` 컬럼이 채워진 케이스에서만 평가됩니다 (비어 있으면 skip).

#### 4단계 다중턴 — 대화 전체의 일관성

| 지표 | 설명 | 통과 기준 |
|---|---|---|
| ⑨ Multi-turn Consistency | 같은 `conversation_id` 로 묶인 여러 턴에서 답변들이 서로 모순되지 않는지, 이름·설정 등을 기억하는지. 같은 conversation 안의 모든 턴 종료 후 1 회 평가. | 점수 ≥ 0.7 |

#### 5단계 운영지표 — 합/불 없음, 운영 추세 모니터링용

| 지표 | 설명 | 기록 내용 |
|---|---|---|
| ⑩ Latency | 대상 AI 가 응답까지 걸린 시간 (ms). | 턴 단위 + 집계 p50/p95/p99 |
| ⑪ Token Usage | 프롬프트 / 완료 / 총 토큰 수. API 비용·응답 길이 추적. | 턴 단위 + 빌드 합산 |

#### 채점 방식

- ③④⑥⑦⑧⑨ 는 **LLM-as-Judge** — DeepEval 라이브러리의 GEval / AnswerRelevancyMetric / FaithfulnessMetric / ContextualRecallMetric / ContextualPrecisionMetric 등이 호스트 Ollama 의 Judge 모델을 호출해 자연어 reasoning + 점수를 산출합니다.
- ⑤ Toxicity 는 LLM Judge + 기본 사전 결합.
- ①② 는 결정론적 매칭 (정규식·jsonschema) — LLM Judge 호출 없음, 즉시 판정.
- ⑩⑪ 는 어댑터가 실측한 값을 기록만 함 (점수·임계 없음).

### 1.4 평가 대상은 누구인가 — 3가지 모드

Jenkins 빌드 화면에서 `TARGET_TYPE` 으로 선택합니다.

| TARGET_TYPE | 평가 대상 AI | 사용 시나리오 |
|---|---|---|
| `local_ollama_wrapper` | 컨테이너 내부 wrapper 가 호스트 PC 의 Ollama 모델을 호출 | 새 모델 도입 전 smoke 테스트, 자기평가, 외부 의존성 없는 회귀 검증 |
| `http` | 사용자가 만든 REST API (자체 챗봇 백엔드, OpenAI 호환 프록시 등) | 운영 중인 AI 에이전트의 정기 회귀 평가 |
| `ui_chat` | 웹 채팅 페이지 (Dify chat app, ChatGPT-style UI 등) | API 가 없고 UI 만 있는 서비스의 평가 |

심판(Judge)은 항상 호스트 Ollama 의 모델 중 선택. **대상 모델과 심판 모델은 분리**되어 객관적 채점 가능.

### 1.5 산출물

매 빌드마다 다음이 생성됩니다.

- **`summary.html`** — 사람이 보는 메인 리포트. Jenkins `AI Eval Summary` 탭에서 바로 열림.
- **`summary.json`** — 후처리/대시보드 연동용. 모든 점수·메타·LLM 내러티브가 한 파일에.
- **`results.xml`** — JUnit 형식 (CI 통계 연동용, 옵션).
- **`wrapper.log`** — `local_ollama_wrapper` 모드일 때 wrapper 데몬의 stdout/err.

저장 위치: 컨테이너 내 `/var/knowledges/eval/reports/build-<N>/` + Jenkins 빌드 아티팩트 사본.

---

## §2. 처음부터 끝까지 — 빌드, 구동, 프로비저닝

처음 PC를 받은 사람도 그대로 따라 할 수 있도록 단계별로 정리합니다. **WSL2 + Docker Desktop (Windows)** 환경 기준입니다. macOS 는 §2.6 참조.

### 2.1 사전 준비물 체크

빌드 시작 전에 다음 4가지가 모두 준비되어 있어야 합니다.

#### 2.1.1 호스트 PC 요구사항

| 항목 | 권장 사양 | 확인 방법 |
|---|---|---|
| OS | Windows 10/11 + WSL2 | `wsl --version` |
| RAM | 16GB 이상 (8GB 도 동작은 함) | 작업 관리자 |
| 디스크 여유 | 50GB+ (이미지 13GB + 캐시) | `Get-PSDrive C` |
| Docker Desktop | 4.x + WSL2 backend | Docker Desktop GUI → Settings |
| GPU (옵션) | NVIDIA + 최신 driver + CUDA | `nvidia-smi.exe` |
| Git | 2.x | `git --version` |

> **GPU 확인 팁**: 8B+ 모델로 평가 돌리면 CPU 만으로는 케이스당 5분+ 걸립니다. GPU 가 있다면 활성화 강력 권장 (§2.5).

#### 2.1.2 호스트 Ollama 설치 + 모델 받기

본 파이프라인은 **호스트 PC 의 Ollama** 를 컨테이너 안에서 호출합니다 (컨테이너 내부엔 Ollama 미설치).

1. https://ollama.com/download 에서 Ollama Windows 설치
2. 설치 후 ollama 데몬이 자동 기동됩니다 (`ollama.exe serve`)
3. 평가에 쓸 모델 다운로드:
   ```powershell
   ollama pull gemma4:e4b      # 8B - 일반 평가용 권장
   ollama pull qwen3.5:4b      # 4B - 빠른 평가/심판 권장
   ollama pull gemma4:e2b      # 5B - 평가 대상용 가벼운 모델
   ollama pull bge-m3          # (옵션) 임베딩 모델 - Job 01 RAG 용
   ```
4. 확인:
   ```powershell
   ollama list
   curl http://localhost:11434/api/tags
   ```
   목록에 위 모델들이 보이면 OK.

#### 2.1.3 GPU 활성화 사전 점검 (있는 경우)

GPU 가 있는데 Ollama 가 CPU 로 동작하면 평가가 너무 느려집니다. 다음으로 확인:

```powershell
# GPU 0번 메모리 사용량 (모델 로드 후 5GB 이상 보여야 정상)
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv

# Ollama 가 어떤 모델을 로드 중인지
curl http://localhost:11434/api/ps
```

`size_vram` 이 0 이면 CPU 추론 중. `CUDA_VISIBLE_DEVICES` 환경변수가 잘못 설정돼 있을 가능성:

```powershell
# 현재 값 확인
echo $env:CUDA_VISIBLE_DEVICES

# 만약 1, 2 등 존재하지 않는 GPU 인덱스로 설정돼 있다면 0 으로 수정
[Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '0', 'User')

# Ollama 재기동 (새 환경변수 적용)
Get-Process ollama* | Stop-Process -Force
Start-Process 'C:\Users\<사용자>\AppData\Local\Programs\Ollama\ollama app.exe'
```

#### 2.1.4 레포 + 플러그인 캐시

```bash
# WSL2 또는 Git Bash 안에서
cd /c/developer
git clone <repo-url> airgap-test-toolchain
cd airgap-test-toolchain/code-AI-quality-allinone

# 플러그인 캐시 (Jenkins/Dify) — 인터넷 연결된 환경에서 1회 실행
bash scripts/download-plugins.sh
```

`download-plugins.sh` 가 끝나면 다음이 생성됩니다:
- `jenkins-plugins/` — `.jpi` 파일 다수
- `dify-plugins/` — `.difypkg` 파일
- `jenkins-plugin-manager.jar`

이 파일들이 비어 있으면 빌드 스크립트가 사전에 fail 하므로 반드시 확인.

### 2.2 이미지 빌드

```bash
cd /c/developer/airgap-test-toolchain/code-AI-quality-allinone
bash scripts/build-wsl2.sh
```

#### 빌드 시간 안내

- **첫 빌드**: 30~40분 (모든 deps 다운로드 + Playwright Chromium 167MB + sonar-scanner 55MB + Java/Python deps)
- **재빌드 (캐시 활용)**: 5~10분 (변경된 layer 만)

#### 진행 상황 확인 방법

빌드 스크립트는 stdout 으로 `#N [stage-X N/M]` 형식의 buildkit 진행 라인을 찍습니다.

```bash
# 별도 터미널에서 진행률 (선택)
docker stats buildx_buildkit_ttc-allinone-builder0 --no-stream
# CPU 80~200% 면 활발히 빌드 중, 0% 면 네트워크 다운로드 또는 idle
```

#### 빌드 성공 확인

```bash
docker images ttc-allinone:wsl2-dev
# IMAGE              ID             SIZE
# ttc-allinone:wsl2-dev   <hash>    13.5GB
```

#### 자주 만나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `jenkins-plugins/ 가 비어 있습니다` | 사전 단계 누락 | §2.1.4 의 `download-plugins.sh` 실행 |
| `pg-init.sh exit 2` | Postgres 초기화 race | 이미지 캐시 purge 후 재빌드: `docker system prune` |
| `sending tarball` 5분 이상 정체 | BuildKit→docker daemon 전송 지연 (정상) | 그대로 대기. Docker Desktop WSL2 VM 경계 횡단 시 5분 내외 정상 |
| `Out of disk` | 50GB 미만 여유 | Docker Desktop 의 disk image 위치를 여유 큰 드라이브로 이동 |

### 2.3 컨테이너 기동

```bash
bash scripts/run-wsl2.sh
```

이 스크립트는 `docker-compose.wsl2.yaml` 을 사용해 두 컨테이너를 띄웁니다:

- **`ttc-allinone`** — Jenkins (28080) + Dify (28081) + SonarQube (29000) + Postgres + Redis + Qdrant 를 모두 supervisord 로 관리
- **`ttc-gitlab`** — GitLab CE (28090)

기동 후 5~10분간 내부 서비스 초기화 (가장 느린 게 SonarQube + Dify migrations + GitLab healthcheck).

#### 기동 진행 상태 확인

```bash
# 전체 supervisord 서비스 상태
docker exec ttc-allinone supervisorctl status

# 모두 RUNNING 이어야 정상. 다음 11개:
# postgresql, redis, qdrant, dify-plugin-daemon, dify-web, dify-worker,
# dify-worker-beat, dify-api, nginx, jenkins, sonarqube
```

```bash
# entrypoint.sh 진행 로그
docker logs ttc-allinone --tail 50
```

다음 라인이 보이면 자동 프로비저닝 시작:
```
[entrypoint] 서비스 헬스 대기 (dify/jenkins/sonarqube 전부 HTTP 200, 최대 10분)...
```

#### 서비스 접속 확인

| 서비스 | URL | 계정 |
|---|---|---|
| Jenkins | http://localhost:28080 | admin / password |
| Dify | http://localhost:28081 | admin@ttc.local / TtcAdmin!2026 |
| SonarQube | http://localhost:29000 | admin / TtcAdmin!2026 |
| GitLab | http://localhost:28090 | root / ChangeMe!Pass |

각 URL 에 들어가서 로그인 화면이 뜨면 OK.

### 2.4 자동 프로비저닝 검증

`entrypoint.sh` 가 첫 기동 시 (그리고 `/data/.app_provisioned` 마커가 없을 때) `/opt/provision.sh` 를 자동 실행합니다. 이 스크립트가 다음을 자동 처리:

1. ✅ Dify 관리자 계정 setup
2. ✅ Dify Ollama 플러그인 설치 (오프라인 패키지)
3. ✅ Dify Ollama provider 등록 (호스트 Ollama 가리킴)
4. ✅ Dify Workflow import (Sonar 이슈 분석용)
5. ✅ Dify API key 발급
6. ✅ GitLab PAT 발급 + Jenkins Credentials 주입
7. ✅ SonarQube admin 비번 변경
8. ✅ Jenkins Pipeline Job 4개 등록 (01/02/03/**04**)

#### 프로비저닝 완료 확인

```bash
# 마커 파일 존재 = 프로비저닝 완료
docker exec ttc-allinone ls -la /data/.app_provisioned

# Jenkins Job 4개 확인
curl -s -u admin:password 'http://localhost:28080/api/json' \
  -G --data-urlencode 'tree=jobs[name,color]' | python -m json.tool
# → "01-코드-사전학습", "02-코드-정적분석", "03-정적분석-결과분석-이슈등록", "04-AI평가" 4개가 보여야 함
```

#### 프로비저닝 실패 시 수동 재실행

```bash
docker exec ttc-allinone bash -c '
  rm -f /data/.app_provisioned
  export DIFY_URL="http://127.0.0.1:28081"
  export JENKINS_URL="http://127.0.0.1:28080"
  export SONAR_URL="http://127.0.0.1:9000"
  export OLLAMA_BASE_URL="http://host.docker.internal:11434"
  export OFFLINE_DIFY_PLUGIN_DIR="/opt/seed/dify-plugins"
  export OFFLINE_JENKINSFILE_DIR="/opt/jenkinsfiles"
  bash /opt/provision.sh
  [ $? -eq 0 ] && touch /data/.app_provisioned
'
```

#### 자주 만나는 경고/이슈

| 메시지 | 원인 | 영향 / 조치 |
|---|---|---|
| `WARN: Ollama embedding 등록 경고 — model "bge-m3" not found` | 호스트 Ollama 에 bge-m3 미설치 | Job 01 RAG 영향. Job 04 무관. 해결: `ollama pull bge-m3` |
| `WARN: Dataset 생성 실패 — Default model not found for text-embedding` | 위 bge-m3 부재의 후속 | 동일 |
| `WARN: Jenkins SonarQube 설정 실패 — SonarGlobalConfiguration` | SonarQube Scanner plugin classloader | Job 02 영향. Job 04 무관. (별도 후속 과제) |
| `dify-plugin-daemon entered FATAL state` | Postgres 기동 전 plugin-daemon race | 수정됨 (`autostart=false` + entrypoint 명시 start). 재발 시 `supervisorctl start dify-plugin-daemon` |
| `dify=000` (curl 응답 없음) | dify-api 가 좀비 worker 로 5001 점유 | 수정됨 (`stopasgroup=true + killasgroup=true`). 재발 시 `supervisorctl restart dify-api` |

### 2.5 Job 04 첫 빌드 (파라미터 등록용)

Jenkins Pipeline Job 의 빌드 파라미터(`TARGET_TYPE`, `JUDGE_MODEL` 등) 는 **첫 빌드가 Jenkinsfile 의 `properties([parameters([...])])` 를 evaluate 해야 UI 에 등록**됩니다. 따라서 첫 빌드는 파라미터 없이 한 번 트리거하고 (이건 실패해도 정상), 두 번째 빌드부터 본격 평가 진행.

#### 웹 UI 로 첫 빌드

1. http://localhost:28080 → admin/password 로그인
2. `04-AI평가` Job 클릭
3. 좌측 **"Build Now"** 클릭 (파라미터 없이)
4. 잠시 대기. **#1 빌드는 default 값(qwen3-coder:30b 모델 부재 등) 으로 실패 정상**.
5. 좌측 **"Build with Parameters"** 가 메뉴에 나타나면 파라미터 등록 완료.

#### CLI 로 첫 빌드 (선택)

```bash
# crumb 발급
CRUMB=$(curl -s -u admin:password --cookie-jar /tmp/jck \
  http://localhost:28080/crumbIssuer/api/json \
  | python -c "import json,sys;d=json.load(sys.stdin);print(d['crumbRequestField']+':'+d['crumb'])")

# 첫 빌드 트리거 (파라미터 없음)
curl -sS -u admin:password --cookie /tmp/jck \
  -H "$CRUMB" -X POST \
  "http://localhost:28080/job/04-AI%ED%8F%89%EA%B0%80/build"
```

### 2.6 macOS 환경

위 가이드의 WSL2 부분만 macOS 로 치환:

- 빌드: `bash scripts/build-mac.sh`
- 기동: `bash scripts/run-mac.sh`
- compose 파일: `docker-compose.mac.yaml`
- 나머지 (Ollama, 프로비저닝, Job 04 사용법) 동일

### 2.7 GitHub 클론부터 첫 평가까지 한 페이지 정리

```bash
# 1. 코드
git clone <repo-url> airgap-test-toolchain
cd airgap-test-toolchain/code-AI-quality-allinone

# 2. 호스트 Ollama 설치 + 모델 (PowerShell)
# https://ollama.com/download
ollama pull gemma4:e4b
ollama pull qwen3.5:4b

# 3. 플러그인 사전 다운로드
bash scripts/download-plugins.sh

# 4. 이미지 빌드 (~30분)
bash scripts/build-wsl2.sh

# 5. 기동 (~5분)
bash scripts/run-wsl2.sh

# 6. 프로비저닝 자동 진행 대기 (10분 이내) — 진행 확인:
docker logs -f ttc-allinone | grep "\[provision\]"

# 7. Jenkins 접속 → 04-AI평가 → Build Now → 다음 §3 으로
```

---

## §3. AI 평가 파이프라인 사용법 + 결과 화면 해설

### 3.1 시험지(Golden Dataset) 준비

평가의 출발점. CSV 형식. 컬럼:

| 컬럼 | 필수? | 의미 | 예 |
|---|---|---|---|
| `case_id` | ✅ | 케이스 식별자 (리포트 표시용) | `policy-pass-clean` |
| `conversation_id` | 옵션 | 멀티턴 묶음 ID (같은 ID 면 한 대화) | `mt-1` 또는 비움 |
| `turn_id` | 옵션 | 멀티턴 안에서의 순서 | `1`, `2` |
| `input` | ✅ | AI 에 던질 질문 | `대한민국의 수도는?` |
| `expected_output` | 옵션 | 기대 답변 (Faithfulness/Recall 평가용) | `서울입니다.` |
| `success_criteria` | 옵션 | 합격 기준 (자연어 또는 DSL) | `응답에 서울이 포함되어야 함` 또는 `raw~r/서울/` |
| `context_ground_truth` | 옵션 | RAG retrieval 정답 문맥 (JSON 배열) | `["서울 공항은 인천국제공항과 김포국제공항이다"]` |
| `expected_outcome` | 옵션 | 정상/실패 의도 마킹 (`pass`/`fail`) | `pass` |
| `calib` | 옵션 | 보정 세트 표시 (Phase 5 Q7-a) | `true` 또는 비움 |

#### 시험지 작성 팁

##### `success_criteria` 컬럼 작성법

이 컬럼은 두 가지 모드를 지원하며, 값의 형태를 보고 파이프라인이 자동으로 판별합니다.

**모드 1 — 자연어 (LLM Judge 가 채점)**

자연어 문장을 쓰면 Task Completion 단계에서 GEval 이 "이 답변이 주어진 기준을 만족하는가?" 를 Judge 모델에게 물어 0~1 점수를 받습니다. 단, 특정 정형 패턴은 LLM 호출 없이 결정론적으로 먼저 판정해 비용을 아낍니다.

결정론 매칭되는 패턴 (대소문자 무시, 공백 제거 후 비교):

- `응답에 <키워드>가 포함되어야 함`
- `응답에 <키워드>가 포함되어야 합니다`
- `response must include '<keyword>'`
- `response should contain '<keyword>'`

예:
```
응답에 서울이 포함되어야 함
response must include 'Seoul'
답변에 김철수가 포함되어야 함
```

위 패턴이 아니면 GEval 이 Judge 모델에게 자연어 기준 자체를 보내 평가. 예:
```
답변은 친절하고 공손해야 한다
사용자 이름을 한 번 이상 불러야 한다
요청 내용을 단계별로 분해해 답해야 한다
```

**모드 2 — DSL (결정론적 규칙, Judge 호출 없음)**

HTTP 응답의 상태 코드, raw body, JSON 필드 등을 정규식/비교로 검증. 여러 조건을 AND 로 연결 가능.

| 문법 | 의미 | 예 |
|---|---|---|
| `status_code=N` | HTTP 응답 코드 일치 | `status_code=200` |
| `raw~r/<regex>/` | raw_response 문자열에 정규식 매칭 | `raw~r/서울/`, `raw~r/^\{.*\}$/` |
| `json.<path>~r/<regex>/` | 응답 JSON 의 해당 경로에 정규식 매칭 | `json.answer~r/서울/`, `json.docs[0]~r/공항/` |
| `A AND B AND …` | 여러 조건 모두 만족 | `status_code=200 AND json.answer~r/서울/` |

예:
```
status_code=200
raw~r/이키워드는없을것/
json.answer~r/서울/
status_code=200 AND json.usage.total_tokens~r/^\d+$/
```

`json.<path>` 는 dot-notation + `[<index>]` 배열 인덱싱을 지원하지만 `$.`/`[*]` 같은 JSONPath 확장 문법은 미지원 (단순 경로만).

**빈 값**

`success_criteria` 가 비어있으면 Task Completion 은 HTTP 성공(`2xx`) 을 fallback 기준으로 사용합니다.

##### 멀티턴 작성

같은 `conversation_id` 를 두 행 이상에 넣고 `turn_id` 로 순서 지정 (숫자 또는 문자열). 파이프라인이 대화를 재구성해 각 턴의 답변을 `messages` history 로 대상 AI 에 전달합니다. 대화 종료 후 ⑨ Multi-turn Consistency 가 1 회 평가됩니다.

예 (같은 `mt-1` 대화):
```csv
case_id,conversation_id,turn_id,input,expected_output
mt-t1,mt-1,1,제 이름은 김철수입니다,이름을 기억하겠습니다
mt-t2,mt-1,2,제 이름이 뭐죠?,김철수
```

##### RAG 케이스 활성화

`context_ground_truth` 컬럼에 JSON 배열로 **정답 문서(들)** 을 넣으면 ⑥⑦⑧ Faithfulness/Recall/Precision 평가가 자동 활성화됩니다. 대상 AI 가 응답에 `docs` 또는 `retrieval_context` 필드를 포함해 돌려주면 그것을 실제 retrieval 결과로 간주해 정답 문서와 비교 채점.

예:
```csv
case_id,input,context_ground_truth,expected_output
rag-1,서울 공항 알려줘,"[""서울 공항은 인천국제공항과 김포국제공항이다""]",서울에는 인천과 김포 공항이 있습니다
```

CSV 특성상 JSON 배열 안에 쉼표가 있으면 전체를 큰따옴표로 감싸고 내부 큰따옴표는 두 번(`""`) 으로 escape.

##### 보정 세트 (Judge 변동성 모니터링)

`calib` 컬럼에 `true` 를 넣은 케이스들은 매 빌드마다 점수 표준편차(σ)가 계산되어 `summary.html` 헤더 "Judge 변동성" 라인에 표시됩니다. 같은 Judge 가 같은 케이스에 매번 비슷한 점수를 매기는지 (모델 자체의 일관성) 를 추적하는 용도.

권장 운용:
- 정답이 매우 명확한 케이스(예: `policy-pass-clean`, `task-pass-simple`) 2~3 개를 `calib=true` 로 지정.
- σ 가 0.1 이상이면 Judge 모델이 같은 입력에 다른 점수를 내고 있다는 뜻 — Judge 모델 교체 또는 `REPEAT_BORDERLINE_N=3` 활성 검토.

#### 시험지 배치

방법 1 — **컨테이너 내부 경로 직접 두기**:
```bash
# 호스트에서 컨테이너로 복사
docker cp my_golden.csv ttc-allinone:/var/knowledges/eval/data/golden.csv
docker exec ttc-allinone chown jenkins:jenkins /var/knowledges/eval/data/golden.csv
```

방법 2 — **Jenkins 빌드 시 업로드** (`UPLOADED_GOLDEN_DATASET` 파라미터 사용 — §3.2 참조). 업로드한 파일이 `GOLDEN_CSV_PATH` 위치에 자동 복사됩니다.

방법 3 — **샘플 시험지 사용**: 컨테이너 내 fixture 가 자동으로 들어 있습니다:
```bash
docker exec ttc-allinone cp /opt/eval_runner/tests/fixtures/tiny_dataset.csv /var/knowledges/eval/data/golden.csv
```
11개 케이스가 5단계 11지표를 모두 한 번씩 hit 하도록 만들어진 회귀 검증용 미니 시험지.

### 3.2 Build with Parameters — 파라미터 11종 안내

Jenkins → 04-AI평가 → **Build with Parameters** 클릭.

#### 평가 대상 AI 관련

##### TARGET_TYPE (평가 대상 AI 연결 방식)

| 값 | 의미 |
|---|---|
| `local_ollama_wrapper` | 컨테이너 안 wrapper 가 호스트 Ollama 모델을 호출. **`TARGET_URL` 비워두기**. 가장 간단, 외부 의존성 0. |
| `http` | 외부 REST API 직접 호출. **`TARGET_URL` 필수**. 자체 챗봇 백엔드 / OpenAI 호환 프록시 / Dify Chatflow 등. |
| `ui_chat` | 웹 채팅 UI 자동화 (Playwright). **`TARGET_URL` 필수** + 페이지 selector 들 필요. |

##### TARGET_URL (평가 대상 AI 의 접속 주소)

- `http` 일 때: REST endpoint 예시 — `http://my-ai.internal/v1/chat`, `http://127.0.0.1:28081/v1/chat-messages` (Dify), `http://127.0.0.1:5000/v1/chat/completions` (OpenAI 호환)
- `ui_chat` 일 때: 사용자가 직접 질문 입력하는 채팅 페이지 URL — `http://127.0.0.1:28081/chat/<app-id>`
- `local_ollama_wrapper` 일 때: 비워두기 (wrapper 가 자동 기동)

##### TARGET_AUTH_HEADER (옵션, http 전용)

API 가 인증 요구할 때 헤더 한 줄.
- `Authorization: Bearer sk-xxxxx` 형식 (콜론 앞=헤더 이름, 뒤=값)
- 콜론 없이 적으면 `Authorization` 헤더에 그대로 들어감
- Password 타입이라 빌드 로그에 노출 안 됨

##### TARGET_REQUEST_SCHEMA (http 전용 요청/응답 포맷)

| 값 | 요청 예 | 응답 예 |
|---|---|---|
| `standard` | `{"messages":[…], "query":"…", "input":"…"}` | `{"answer":"…", "docs":[…], "usage":{…}}` |
| `openai_compat` | `{"model":"…", "messages":[{"role":"user","content":"…"}]}` | `{"choices":[{"message":{"content":"…"}}], "usage":{…}}` |

대상이 OpenAI/GPT 호환 프록시거나 vLLM/llama.cpp/LM Studio 같은 표준 OpenAI API 서버면 `openai_compat` 선택.

##### UI_INPUT_SELECTOR / UI_SEND_SELECTOR / UI_OUTPUT_SELECTOR / UI_WAIT_TIMEOUT (ui_chat 전용)

CSS selector 로 페이지 요소 지정.
- `UI_INPUT_SELECTOR`: 질문 입력창 (기본 `textarea, input[type=text]`)
- `UI_SEND_SELECTOR`: 전송 버튼 (기본 `button[type=submit]`, 비우면 Enter 키로 전송)
- `UI_OUTPUT_SELECTOR`: 응답 메시지 (기본 `.answer, [role=assistant], .message-content` — 마지막 매칭 노드)
- `UI_WAIT_TIMEOUT`: 응답 대기 timeout 초 (기본 60, 느린 모델은 120~300)

> **selector 찾는 법**: Chrome F12 → Elements 탭 → 질문창에 우클릭 → Copy → Copy selector

#### 심판/모델 관련

##### OLLAMA_BASE_URL

호스트 Ollama 데몬 주소. WSL2/Docker Desktop 에서는 기본값 `http://host.docker.internal:11434` 그대로 두세요. 다른 머신의 Ollama 를 가리킬 거면 `http://<IP>:11434`.

##### TARGET_OLLAMA_MODEL (local_ollama_wrapper 전용 — 평가 대상 모델)

drop-down. 호스트 Ollama 의 `/api/tags` 결과를 동적 로드합니다 (drop-down 위 "Ollama" 라벨 옆 새로고침 버튼).
- 권장: **gemma4:e2b** (5B, 빠름) 또는 **qwen3.5:4b** (4B, 빠름) — 평가 파이프라인 검증이 목적이라 답변 품질보다 속도 우선
- Judge 모델과 같아도 되고 달라도 됨 (다르면 더 객관적)

##### JUDGE_MODEL (심판 모델)

drop-down. 동일하게 호스트 Ollama 모델 목록.
- 권장: **qwen3.5:4b** (빠른 일상 평가) 또는 **gemma4:e4b** (정확도 우선, 느림)
- 일반적으로 대상 모델보다 같거나 약간 더 큰 쪽이 신뢰할만한 채점

> **drop-down 이 비어있거나 fallback 리스트(qwen3.5:4b / gemma4:e4b / ...) 만 계속 보일 때**:
> Active Choices 플러그인이 호스트 Ollama `/api/tags` 에 접근하려면 `new URL`, `openConnection`, `JsonSlurperClassic` 등의 Groovy signature 가 Jenkins 의 **In-process Script Approval** 에 등록돼 있어야 합니다.
> 관리자 계정(`admin`)으로 Jenkins 스크립트 콘솔(`/script`) 에 다음을 한 번 실행하면 이후 빌드부터 드롭다운이 실제 Ollama 모델로 채워집니다:
>
> ```groovy
> import org.jenkinsci.plugins.scriptsecurity.scripts.*
> def inst = ScriptApproval.get()
> [
>     "staticMethod java.net.URL openConnection",
>     "method java.net.URLConnection getInputStream",
>     "method java.net.URLConnection setConnectTimeout int",
>     "method java.net.URLConnection setReadTimeout int",
>     "method java.net.HttpURLConnection setConnectTimeout int",
>     "method java.net.HttpURLConnection setReadTimeout int",
>     "method java.io.InputStream newReader java.lang.String",
>     "new groovy.json.JsonSlurperClassic",
>     "method groovy.json.JsonSlurperClassic parse java.io.Reader",
> ].each { inst.approveSignature(it) }
> println "approved total=" + inst.approvedSignatures.size()
> ```

#### 평가 기준 / 시험지

##### ANSWER_RELEVANCY_THRESHOLD

답변 관련성 합격 기준. 0.0~1.0.
- `0.5` — 초기 도입, 관대한 평가
- `0.7` — 일반 권장 (default)
- `0.8+` — 엄격한 운영 평가

##### GOLDEN_CSV_PATH

시험지 파일 경로 (컨테이너 내부). 기본 `/var/knowledges/eval/data/golden.csv`.

##### UPLOADED_GOLDEN_DATASET

내 PC 의 csv 파일을 직접 업로드. 업로드하면 `GOLDEN_CSV_PATH` 위치에 덮어쓰기 + 이번 빌드부터 사용.

### 3.3 빌드 실행 + 진행 모니터링

`Build` 버튼 클릭 → 빌드 진입.

#### Stage 별 진행 (Console Output 에서 실시간 표시)

| Stage | 무엇을 함 | 보통 소요 시간 |
|---|---|---|
| **1. 시험지 준비** | golden.csv 존재 확인, REPORT_DIR 생성 | < 1초 |
| **1-1. Judge Model 검증** | 호스트 Ollama 에 선택한 JUDGE_MODEL 이 실제로 있는지 확인 | < 2초 |
| **1-2. 로컬 Ollama Wrapper 기동** (TARGET_TYPE=local_ollama_wrapper 일 때만) | wrapper 데몬 fork + /health probe + /invoke probe (cold-start 모델 로드) | 10초 ~ 5분 (모델 크기·GPU 여부 따라) |
| **2. 파이썬 평가 실행 (Pytest)** | golden.csv 의 케이스를 한 conversation 씩 평가. 각 케이스마다 LLM Judge 호출 다수 | 케이스당 1~3분 (GPU 기준) |
| **Post Actions** | 결과 수집, summary.html 생성, archive, publishHTML | 5~15초 |

#### Console Output 에서 실시간으로 보이는 것 (Phase 6 개선 후)

```
+ python3 -u -m pytest ... -v -s --tb=short
[eval] ▶ conversation=policy-pass-clean turns=1 target_type=http
test_evaluation[conversation0] PASSED                                [10%]
[eval] ▶ conversation=task-pass-simple turns=1 target_type=http
test_evaluation[conversation1] PASSED                                [20%]
...
```

각 케이스 시작 시 `[eval] ▶ conversation=... turns=N` 라인이, 종료 시 `PASSED` / `FAILED` 라인이 즉시 찍힙니다.

#### 평가가 너무 오래 걸린다면

1. **GPU 활성화 확인** — `nvidia-smi --query-gpu=utilization.gpu --format=csv` 가 0% 면 §2.1.3 으로 돌아가 점검.
2. **Judge 모델을 작은 것으로** — gemma4:e4b(8B) → qwen3.5:4b(4B) 로 변경. 약 2배 빠름.
3. **시험지 축소** — 회귀 검증 시엔 11~30 케이스가 적정. 100+ 케이스는 1시간 이상.

### 3.4 결과 화면 해설 — `AI Eval Summary` 탭

빌드 종료 후 좌측 메뉴의 **`AI Eval Summary`** 클릭 (Jenkins publishHTML 자동 게시).

#### 3.4.1 헤더 (R1 + R3.1) — "이 빌드는 어떤가" 한눈에

```
🤖 이번 빌드 한 줄 요약                    [🤖 LLM 생성]
이번 빌드는 대화 11건 중 9건 통과 (82%). 주원인은 Faithfulness 부족 2건 (RAG 관련).
권장 조치: retrieval_context 정확도 점검 필요.

실행 ID: build-12
평가 대상: http://my-ai.internal/v1/chat (http)
심판 모델: qwen3.5:4b @ http://host.docker.internal:11434  T=0  digest=…3a7b8c9d
데이터셋: /var/knowledges/eval/data/golden.csv  sha256=…fedcba98  rows=42  mtime=2026-04-22T08:00:00+00:00
Judge 변동성: 보정 σ=0.045 (mean=0.876)  Judge calls=128  경계 재실행 N=3 (±0.05)
Langfuse 사용: 미사용
```

| 영역 | 의미 |
|---|---|
| 🤖 한 줄 요약 | LLM (Judge 모델) 이 summary.json 을 읽고 2~3 문장으로 합산 결과·주원인·권장 조치 생성 |
| 실행 ID | Jenkins 빌드 번호 |
| 평가 대상 | TARGET_URL + TARGET_TYPE |
| 심판 모델 | JUDGE_MODEL + base_url + temperature + 모델 digest (해시 앞 12자) — 어느 빌드의 결과를 어느 Judge 가 채점했는지 영구 기록 |
| 데이터셋 | golden.csv 의 sha256 + 행 수 + mtime — 어느 시험지로 평가했는지 추적 |
| Judge 변동성 | 보정 세트(`calib=true`) 케이스들의 점수 표준편차 + 총 Judge 호출 수 + (활성 시) 경계 재실행 정책 |

#### 3.4.2 11지표 카드 대시보드 (R2)

11개 카드. 각 카드:

```
┌─────────────────────────────────┐
│ ④ Answer Relevancy              │
│ 통과율  ████████░░  90%  9/10   │
│ 평균    0.823    임계  ≥ 0.7    │
│ 분포    ▁▂▃▅▇█▇▅▃▁              │
│ 실패    relevancy-offtopic-1    │
└─────────────────────────────────┘
```

각 카드 정보:
- **통과율** + 통과/전체 수
- **평균 점수** 와 임계값
- **분포 히스토그램** (스파크라인) — 점수가 어느 구간에 몰려있는지
- **실패 case_id** 목록 (최대 10개) — 클릭하면 해당 케이스 상세로 이동 (Phase 6+ 예정)

11지표 전부 카드로 한 화면에 — Policy/Schema/TaskCompletion/Relevancy/Toxicity/Faithfulness/Recall/Precision/MultiTurnConsistency/Latency/TokenUsage.

#### 3.4.3 Conversation Drill-down (R3)

각 conversation 을 펼치는 accordion. 그 안에 turn 별 row.

```
▼ conversation: rag-1 (RAG 케이스)                      ❌ FAILED
   ├ Turn 1                                              ❌ FAILED
   │  case_id: rag-faithful
   │  input: "서울에 있는 공항을 알려줘"
   │  expected: "서울에는 인천과 김포 공항이 있습니다"
   │  actual:   "서울 인근 공항은 인천국제공항과 김포공항입니다."
   │  Latency: 2.3s   Tokens: 24/87/111
   │  ✅ Policy   ✅ Schema   ✅ TaskCompletion (0.95)
   │  ✅ AnswerRelevancy (0.91)   ❌ Faithfulness (0.62, ≥ 0.7)
   │  🤖 쉬운 해설: "답변에 '인근' 이라는 단어가 있어 retrieval_context 의
   │       '인천국제공항과 김포국제공항' 표현과 100% 일치하지 않아 Faithfulness 가
   │       0.62 로 임계 미달입니다."
```

각 turn 의 표시 요소:
- **status badge** (✅/❌)
- **input/expected/actual** 텍스트 (긴 경우 펼치기)
- **Latency / Token usage**
- **각 메트릭 결과** (이름, 점수, 임계값, PASS/FAIL)
- **🤖 쉬운 해설** (R3.1) — LLM 이 실패 메트릭 + reason 을 보고 1문장으로 이유 설명. LLM 비활성/실패 시 하드코딩 fallback (`📋 기본 메시지` 배지).
- **(옵션, env=on 시) 🤖 권장 조치** (R3.2)

#### 3.4.4 시스템 에러 vs 품질 실패 분리 (R4)

기존엔 모든 실패가 한 덩어리로 보였지만 이제 분리됩니다:

```
실패 분류:  ❌ 시스템 에러 1건 (가용성 이슈)   ⚠️ 품질 실패 2건 (메트릭 미달)
```

- **시스템 에러** (`error_type=system`): adapter timeout, HTTP 5xx, ConnError 등 — **AI 자체 품질 문제 아님**, 인프라/연결 점검 필요
- **품질 실패** (`error_type=quality`): metric 점수 미달, success_criteria 미충족 등 — **AI 답변 품질 문제**

이 분리로 "API 가 잠시 다운돼서 평가가 깨진 것" 과 "AI 가 정답을 못 냈다" 를 구별할 수 있습니다.

#### 3.4.5 (옵션) 지표별 LLM 해석 (R2.1)

환경변수 `SUMMARY_LLM_INDICATOR_NARRATIVE=on` 으로 활성. 각 지표 카드 하단에:

```
🤖 Answer Relevancy 해석: pass 9/10. off-topic 케이스 1건이 평균을 끌어내림.
   다른 케이스는 모두 0.85 이상으로 안정적.
```

기본 off (LLM 호출 비용 — 빌드당 11 calls 추가).

### 3.5 결과 JSON (`summary.json`) 활용

`summary.html` 옆에 `summary.json` 도 같이 생성됩니다. 후처리·대시보드 연동용:

```json
{
  "run_id": "build-12",
  "target_url": "http://my-ai.internal/v1/chat",
  "target_type": "http",
  "totals": {
    "conversations": 11,
    "passed_conversations": 9,
    "failed_conversations": 2,
    "turn_pass_rate": 81.82,
    "latency_ms": {"count": 11, "min": 800, "max": 4500, "p50": 2100, "p95": 4200, "p99": 4500},
    "tokens": {"turns_with_usage": 11, "prompt": 264, "completion": 957, "total": 1221}
  },
  "indicators": {
    "AnswerRelevancyMetric": {"pass": 10, "fail": 1, "scores": [...], "threshold": 0.7, "failed_case_ids": ["..."]},
    ...
  },
  "aggregate": {
    "judge": {"model": "qwen3.5:4b", "base_url": "...", "temperature": 0, "digest": "..."},
    "dataset": {"path": "...", "sha256": "...", "rows": 11, "mtime": "..."},
    "calibration": {"enabled": true, "turn_count": 2, "case_ids": [...], "per_metric": {...}, "overall": {"mean": 0.876, "std": 0.045, "score_count": 14}},
    "judge_calls_total": 128,
    "borderline_policy": {"repeat_n": 3, "margin": 0.05},
    "exec_summary": {"text": "...", "source": "llm", "role": "exec_summary"}
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
          "actual_output": "...",
          "metrics": [{"name": "FaithfulnessMetric", "score": 0.62, "threshold": 0.7, "passed": false, "reason": "..."}, ...],
          "easy_explanation": {"text": "...", "source": "llm"},
          "remediation": {"text": "...", "source": "fallback"}
        }
      ]
    }
  ]
}
```

### 3.6 자주 만나는 평가 결과 패턴 + 대응

| 패턴 | 추정 원인 | 대응 |
|---|---|---|
| 모든 케이스가 `system` 에러 | TARGET_URL 도달 불가, 또는 wrapper 미기동 | TARGET_URL 직접 curl, wrapper.log 확인 |
| Multi-turn 케이스만 quality 실패 | conversation_history 가 대상 AI 에 안 전달됨 | TARGET_REQUEST_SCHEMA 가 대상과 맞는지 확인 (openai_compat vs standard) |
| Faithfulness 만 일관되게 낮음 | retrieval_context 와 actual_output 표현 차이 | golden.csv 의 context_ground_truth 와 실제 retrieval 결과 비교 |
| 평균 점수 매번 흔들림 (재현성 떨어짐) | Judge 모델 변동성 | 보정 세트(calib=true) 추가, REPEAT_BORDERLINE_N=3 활성, 더 큰 Judge 모델 |
| 케이스당 5분+ | CPU 추론 또는 너무 큰 Judge 모델 | §2.1.3 GPU 활성, 더 작은 Judge (qwen3.5:4b) |

### 3.7 평가 결과를 다음 빌드로 이어가기

- **회귀 추적**: 매 빌드의 summary.json 을 비교해 지표별 추세 그래프 작성 (별도 dashboard, Phase 7 후속).
- **case_id 안정**: golden.csv 의 case_id 는 한 번 정하면 바꾸지 마세요. 빌드 간 비교 단위가 됩니다.
- **시험지 버전 관리**: golden.csv 를 git 으로 버전 관리. summary.json 의 `aggregate.dataset.sha256` 으로 어느 시험지였는지 영구 추적.

---

## 부록 A — 자주 쓰는 진단 명령

```bash
# 컨테이너 전체 상태
docker exec ttc-allinone supervisorctl status

# Jenkins 로그 tail
docker exec ttc-allinone tail -f /data/logs/jenkins.log

# Dify API 응답 확인
docker exec ttc-allinone curl -sf -o /dev/null -w "dify=%{http_code}\n" http://127.0.0.1:5001/console/api/setup

# 호스트 Ollama 사용 모델 + GPU 점유
curl http://localhost:11434/api/ps | python -m json.tool
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv

# 가장 최근 Job 04 빌드 결과
curl -s -u admin:password 'http://localhost:28080/job/04-AI%ED%8F%89%EA%B0%80/lastBuild/api/json?tree=number,result,duration' | python -m json.tool

# 가장 최근 summary.json 일부 보기
docker exec ttc-allinone python3 -c "
import json, glob, os
paths = sorted(glob.glob('/var/knowledges/eval/reports/build-*/summary.json'), key=os.path.getmtime)
d = json.load(open(paths[-1]))
print('build:', os.path.basename(os.path.dirname(paths[-1])))
print('totals:', d.get('totals'))
"
```

## 부록 B — 알려진 한계

- `bge-m3` 임베딩 모델이 호스트 Ollama 에 없으면 다른 Job(사전학습·RAG) 의 임베딩 단계가 실패할 수 있음. Job 04 자체와는 무관하지만 통합 환경 완결성을 위해 `ollama pull bge-m3` 권장.
- `OpenAI / Gemini` 전용 어댑터는 별도 모드로 제공하지 않음. OpenAI 계열 API 는 `TARGET_TYPE=http + TARGET_REQUEST_SCHEMA=openai_compat` 으로 대부분 커버 가능.
- `JUnit plugin` 미설치 환경에서는 `post always` 블록의 `junit` step 이 `NoSuchMethodError` 를 던질 수 있으나 파이프라인이 try/catch 로 감싸 무시하고 진행함. `results.xml` 은 `archiveArtifacts` 로 보존되므로 후처리 가능.
- Active Choices 의 동적 모델 fetch(호스트 Ollama `/api/tags` 조회) 가 Groovy sandbox 정책에 의해 거부될 수 있음. 이 경우 Job 재등록 직후 한 번만 signature 자동 승인 스크립트(`ScriptApproval.approveSignature(...)`) 를 실행하면 이후 빌드부터 정상 동작. 모델 드롭다운이 fallback 리스트만 계속 보이면 본 단계를 확인.
- Wrapper 모드(`local_ollama_wrapper`) 의 첫 invoke probe 는 Ollama 가 모델을 RAM/VRAM 에 로드하는 cold-start 시간을 포함. 4B 이상 모델은 CPU 기준 수 분이 걸릴 수 있어 probe timeout 을 300 초까지 허용하고, 초과 시 FATAL 대신 WARN 만 남기고 pytest 로 진행함 (pytest 첫 LLM 호출이 사실상 warm-up 역할).
