# Recording 서비스 트러블슈팅 (Phase R-MVP TR.10)

호스트 데몬 `recording_service` (port 18092) 의 자주 발생하는 문제와 해결 절차.

## 1. UI 가 안 뜸 (`http://localhost:18092/` 응답 없음)

### 진단

```bash
# 헬스체크
curl -v http://127.0.0.1:18092/healthz

# 프로세스 확인
ps aux | grep "uvicorn.*18092" | grep -v grep

# PID 파일
cat ~/.dscore.ttc.playwright-agent/recording-service.pid

# 로그
tail -50 ~/.dscore.ttc.playwright-agent/recording-service.log
```

### 자주 발생하는 원인

| 증상 | 원인 | 해결 |
|---|---|---|
| `connection refused` | 데몬 미기동 | `./mac-agent-setup.sh` (또는 `wsl-agent-setup.sh`) 재실행 |
| 로그에 `Address already in use` | 18092 포트 점유 | `lsof -nP -iTCP:18092 -sTCP:LISTEN` 후 PID kill |
| 로그에 `ModuleNotFoundError: recording_service` | PYTHONPATH 누락 | setup 스크립트 재실행 (`PYTHONPATH=$ROOT_DIR` 자동 주입) |
| 로그에 `ModuleNotFoundError: fastapi` | venv deps 미설치 | `mac-agent-setup.sh` 의 REQ_PKGS 에 `fastapi`, `uvicorn` 포함되었는지 확인. 미포함이면 setup 스크립트가 구버전 |

## 2. `playwright codegen` 명령 못 찾음

### 증상

`/recording/start` 가 503 + `playwright 실행 파일을 찾을 수 없습니다.`

### 진단

```bash
# venv 의 playwright 확인
~/.dscore.ttc.playwright-agent/venv/bin/playwright --version

# PATH 확인
which playwright
```

### 해결

setup 스크립트의 venv (`~/.dscore.ttc.playwright-agent/venv/`) 에 playwright 가
설치되어야 한다. nohup 으로 띄운 uvicorn 도 같은 venv 의 python 을 사용해야
PATH 가 일관. setup 스크립트 재실행으로 자동 해결.

## 3. 헤디드 Chromium 안 뜸

### mac

- 시스템 권한 — Privacy → Screen Recording / Accessibility 에 Terminal /
  Chrome for Testing 허용
- `playwright install chromium` 한 번 실행 (setup 스크립트가 자동 수행하지만,
  upstream 변경 시 누락 가능)

### WSL

- WSLg 활성화 필수 (Windows 11 + WSL2). `wslg --version` 으로 확인.
- WSL 안에서 GUI 가 뜨려면 X-server (VcXsrv 등) 가 fallback. 본 R-MVP 는 WSLg
  우선이고 X-server fallback 은 backlog.

## 4. 변환 실패 — `docker exec` 단계

### 증상

`/recording/stop` 응답이 `state=error` + `stderr` 에 docker 메시지.

### 자주 발생하는 원인

| 증상 | 원인 | 해결 |
|---|---|---|
| `docker 실행 파일을 찾을 수 없습니다` | host 에 docker CLI 미설치 | docker desktop 설치 또는 `brew install docker` |
| `Error response from daemon: No such container: dscore.ttc.playwright` | 컨테이너 미기동 | `./build.sh --redeploy` 실행 |
| `--mode convert --convert-only` 가 unrecognized arg | 컨테이너 이미지가 구버전 | `./build.sh --redeploy --reprovision` 또는 `--fresh` 로 이미지 재배포 |
| `_validate_scenario` 에러 | codegen 결과의 14-DSL 호환성 문제 | 원본 `original.py` 보존됨 — scenario.json 직접 편집 또는 다시 녹화 |

### 직접 호출로 격리

UI 거치지 않고 컨테이너 CLI 만 호출해 격리 테스트:

```bash
SID=test_xxx
docker exec -e ARTIFACTS_DIR=/recordings/$SID dscore.ttc.playwright \
  python -m zero_touch_qa --mode convert --convert-only \
  --file /recordings/$SID/original.py
```

## 5. 마운트 — 호스트 ↔ 컨테이너 경로 불일치

### 증상

컨테이너 측 `--file /recordings/<id>/original.py` 가 `FileNotFoundError`.

### 해결

build.sh 의 docker run 옵션에 다음 마운트 라인이 있는지 확인.

```text
-v "$HOST_RECORDINGS_DIR":/recordings:rw
```

기본 `$HOST_RECORDINGS_DIR` 는 `~/.dscore.ttc.playwright-agent/recordings`.
`docker inspect dscore.ttc.playwright | jq '.[0].Mounts'` 로 마운트 확인.

## 6. server 재시작 후 세션 목록 안 뜸

`recording_service` 가 재시작되면 in-memory 레지스트리는 초기화. `@app.on_event("startup")`
의 디스크 흡수 로직이 호스트 영속화 디렉토리(`~/.dscore.ttc.playwright-agent/recordings/<id>/metadata.json`)
를 읽어 자동 복원. 다음을 확인.

```bash
ls -la ~/.dscore.ttc.playwright-agent/recordings/

# 각 세션 디렉토리의 metadata.json 존재 확인
for d in ~/.dscore.ttc.playwright-agent/recordings/*/; do
  echo "$d → $(test -f $d/metadata.json && echo OK || echo MISSING)"
done
```

`metadata.json` 이 누락된 디렉토리는 흡수 대상에서 제외. 디렉토리 내용 살리고
싶으면 metadata.json 을 직접 작성 (id / target_url / state 최소 3개).

## 7. Jenkins 잡 페이지의 Recording UI 링크가 텍스트로 보임

### 증상

`http://localhost:18080/job/ZeroTouch-QA/` 의 description 영역에 `<a href=...>...</a>` 가
escape 된 채 텍스트로 보임.

### 원인

Jenkins Markup Formatter 가 Plain Text 로 설정됨.

### 해결

`jenkins-init/markup-formatter.groovy` 가 init 단계에서 Safe HTML formatter 를
적용한다. 이미지가 구버전이면 적용 안 됐을 수 있음. `./build.sh --redeploy
--reprovision` 또는 Jenkins 관리 페이지에서 직접:

1. <http://localhost:18080/manage/configure>
2. **Markup Formatter** → "Safe HTML" 선택 → Save

## 8. R-Plus 버튼들이 클릭 안 됨

정상 동작. R-MVP 단계에서는 **Replay / Generate Doc / Compare with Doc-DSL**
가 회색 비활성 (`disabled`) 상태. R-Plus 게이트 통과 후 활성화.

## 9. 로그 위치 정리

| 파일 | 내용 |
|---|---|
| `~/.dscore.ttc.playwright-agent/recording-service.log` | 호스트 데몬 stdout/stderr |
| `~/.dscore.ttc.playwright-agent/recording-service.pid` | 호스트 데몬 PID |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/metadata.json` | 세션 메타 |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/original.py` | codegen 원본 |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/scenario.json` | 14-DSL 변환 결과 |
