# WSL2 빌드 검증에서 발견된 결함과 수정 (2026-05-06)

WSL2 환경 (`verify/wsl2-build-20260506-082223` 브랜치) 에서 `./build.sh --redeploy --reprovision` 으로 끝까지 가보며 드러난 5건의 사전 결함을 모두 잡았다. Mac 빌드는 영향 없음.

## 한눈에 보기

| # | 증상 | 사용자 영향 | 수정 |
|---|---|---|---|
| 1 | LLM 모델이 Mac 기준 `gemma4:26b` 로 고정 — WSL2/Windows 호스트에 없으면 Dify 가 응답 못 함 | WSL2 빌드 실패 | OS 분기 + WSL2 기본 `qwen3.5:9b`. chatflow 도 자동 치환. |
| 2 | `--reprovision` 옵션이 동작하지 않음 — 재실행해도 새 설정이 반영 안 됨 | "왜 안 바뀌지?" 무한 루프 | Git Bash 의 경로 변환 우회 |
| 3 | KB (Test Planning RAG) 자동 생성 실패 — 기본 임베딩 모델이 호스트에 없는 이름 | 챗봇은 떠도 RAG 트랙은 빈 KB | 기본 임베딩 → `bge-m3:latest` (호스트 실존) |
| 4 | Jenkins Pipeline Job 자동 등록이 매번 실패 — 로그에 원인 안 찍힘 | 잡 정의 수동 등록 부담 | 한글 문자 인코딩 헤더 보강 + 응답 본문 노출 |
| 5 | 워크스페이스 기본 모델 설정이 재실행 시 500 에러 | 재 provision 마다 빨간 줄 | 기존 등록 row 정리 후 재등록 (멱등화) |

## 하나씩

### 1. LLM 모델 OS 분기 (Mac=gemma4:26b / WSL2=qwen3.5:9b)

**문제**
- `build.sh`, `wsl-agent-setup.sh`, `mac-agent-setup.sh` 모두 기본값이 `gemma4:26b`.
- WSL2/Windows 호스트 Ollama 에 `gemma4:26b` 가 pull 되어 있지 않으면 Dify 가 LLM 응답을 받지 못해 모든 챗봇이 멎음.
- Chatflow YAML 두 개 (`dify-chatflow.yaml`, `test-planning-chatflow.yaml`) 의 노드 본문에 `gemma4:26b` 가 하드코딩되어, 모델을 바꾸려면 사용자가 YAML 을 직접 편집해야 했음.

**해결**
- `build.sh`: `uname -s` 로 호스트 OS 판별 → Mac 은 `gemma4:26b`, 그 외 (WSL2/Linux/Windows) 는 `qwen3.5:9b` 를 기본값으로 채택.
- `wsl-agent-setup.sh` 기본값을 `qwen3.5:9b` 로 변경. `mac-agent-setup.sh` 는 기존 `gemma4:26b` 유지.
- chatflow YAML 두 개의 모델명을 placeholder 로 보존하되, `provision.sh` 의 `dify_import_chatflow()` 가 import 직전에 환경변수 `OLLAMA_MODEL` 값으로 일괄 치환하도록 추가.
- `OLLAMA_MODEL` env 로 사용자 override 는 그대로 가능.

**확인 방법** — provision 로그 `LLM 모델명 substitute: gemma4:26b → qwen3.5:9b` 그리고 `2-3b. 모델 공급자 등록: qwen3.5:9b`.

### 2. `--reprovision` 옵션 무력화 (Windows Git Bash 한정)

**문제**
- `./build.sh --redeploy --reprovision` 실행 시 빌드 로그에는 "마커 wipe 완료" 가 찍히는데, 실제로 컨테이너 안 `/data/.app_provisioned` 마커는 그대로 남아 provision 이 스킵됨.
- 결과적으로 chatflow / Jenkins 잡 같은 baked-in 정의가 새 이미지로 갱신되지 않음.

**원인**
- Windows Git Bash 의 MSYS 자동 경로 변환이 `docker run ... busybox rm -f /data/.app_provisioned` 명령의 `/data/.app_provisioned` 를 `C:/Program Files/Git/data/.app_provisioned` 로 둔갑시킴.
- busybox 는 그 경로에서 파일을 못 찾고, `rm -f` 는 missing file 에 에러 안 내므로 silent success.

**해결** — 해당 한 줄에 `MSYS_NO_PATHCONV=1` prefix 만 붙임. Linux/Mac 에서는 변수 자체를 무시하므로 부작용 없음.

**확인 방법** — `--reprovision` 후 컨테이너 재기동 시 entrypoint 로그에 `앱 프로비저닝 시작 (provision-apps.sh)` 가 찍혀야 함.

### 3. KB 자동 생성 실패 (Test Planning RAG 트랙)

**문제** — provision 로그:
```
KB 'kb_project_info' 생성 실패: {"code":"invalid_param",
  "message":"Default model not found for text-embedding","status":400}
```

**원인** — `provision.sh` 가 기본 임베딩 모델을 `bona/bge-m3-korean:latest` 로 등록 시도하지만, 호스트 Ollama 에 없는 이름. 실제로 호스트에 있는 것은 `bge-m3:latest`. Dify 는 등록되지 않은 임베딩 모델을 default 로 잡지 못해 KB 생성을 거부.

**해결** — `provision.sh` 의 `EMBEDDING_MODEL` 기본값을 `bge-m3:latest` 로 변경 (3 군데 + 주석 1 군데). 사용자 override 는 `EMBEDDING_MODEL` env 로 그대로 가능.

**확인 방법** — provision 로그 `Embedding 모델 등록: bge-m3:latest ✓` → `KB 'kb_project_info' 신규 생성 ✓` + `KB 'kb_test_theory' 신규 생성 ✓` + 시드 문서 4건 업로드.

### 4. Jenkins Pipeline Job 자동 등록 실패

**문제 1 — 진단 불가** — 로그에 `Pipeline Job 업데이트 실패` 한 줄만 찍히고 응답 본문이 없어 원인 추적 불가능.

**문제 2 — 진짜 원인** — Jenkins 서버 로그에 다음:
```
javax.xml.transform.TransformerException:
  An invalid XML character (Unicode: 0x8c) was found in the element content
  lineNumber: 3; columnNumber: 131
```
잡 description 의 한국어 ("좌측 사이드바...") 의 UTF-8 multi-byte 시퀀스 (예: `좌` = `EC A2 8C`) 가 Jenkins 의 XML 파서에 단일 바이트로 잘못 들어감. 원인은 curl 의 `Content-Type: application/xml` 에 charset 명시가 없어 Jenkins 가 latin-1 등으로 디코딩 시도한 것.

**해결**
- `provision.sh` 의 모든 Jenkins POST 헤더를 `application/xml; charset=utf-8` 로 통일.
- 분기 조건도 강화 — Jenkins 2.555 는 "이미 존재" 응답이 평문 메시지가 아닌 HTML 페이지로 오는 경우가 있어, HTTP `400/409` status 만으로 update 경로 분기.
- 실패 시 응답 본문 (앞 400자) 을 로그에 노출해 다음 사고 진단 가능.

**확인 방법** — provision 로그 `Pipeline Job 업데이트 완료 ✓`. Jenkins 서버 로그에 SAXParseException 사라짐.

### 5. 워크스페이스 기본 모델 설정 — 재실행 500 에러

**문제** — 재 provision 시:
```
workspace 기본 모델 설정 이상: {"message":"Internal Server Error","status":500}
```
KB 자동 생성은 어쨌든 진행되지만 매 실행마다 빨간 줄이 나옴.

**원인** — Dify 1.13 의 `POST /workspaces/current/default-model` 이 INSERT-only API. 재실행 시 postgres `unique_tenant_default_model_type` 위반으로 500. DB 에는 이전 provision 이 남긴 stale row (`llm = gemma4:e4b`) 가 그대로 박혀 있어 `qwen3.5:9b` 로 덮어쓰기 불가.

**해결** — POST 직전에 현재 tenant 의 default-model row 들을 psql 로 일괄 DELETE (DB 컬럼이 `llm` / `embeddings` 인 점 + API 가 `text-embedding` 을 쓰는 점 모두 고려). 기존 line 361 의 PGPASSWORD psql 호출 패턴 그대로 채택.

**확인 방법** — provision 로그 `2-3g ... ✓ workspace 기본 모델 설정 완료` (이전엔 `⚠ ... 500`).

## 영향받는 파일

| 파일 | 변경 |
|---|---|
| `playwright-allinone/build.sh` | OS 분기 + 도움말 + MSYS_NO_PATHCONV |
| `playwright-allinone/wsl-agent-setup.sh` | 기본 모델 → `qwen3.5:9b` |
| `playwright-allinone/provision.sh` | chatflow 모델 치환 / 임베딩 기본값 / Jenkins XML charset / Pipeline Job 응답 노출 / default-model 멱등화 |
| `playwright-allinone/dify-chatflow.yaml` | 헤더 주석 — placeholder 운영 안내 |
| `playwright-allinone/test-planning-chatflow.yaml` | 헤더 주석 — placeholder 운영 안내 |

문서 동기화: `README.md`, `playwright-allinone_QUICKSTART.md`, `playwright-allinone_REFERENCE.md`, `playwright-allinone_OPERATIONS.md`.

## 사용자 체크리스트 (다음 빌드 전)

호스트에 다음 두 모델이 pull 되어 있어야 한다.

| 호스트 | LLM | 임베딩 |
|---|---|---|
| Mac | `ollama pull gemma4:26b` | `ollama pull bge-m3` |
| WSL2 / Windows | `ollama pull qwen3.5:9b` | `ollama pull bge-m3` |

`ollama list` 로 실존 확인 후 `./build.sh --redeploy --reprovision` 진행.
