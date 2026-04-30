# playwright-allinone_OPERATIONS

이 문서는 이미 한 번 실행에 성공한 운영자가 반복적으로 수행하는 절차를 정리한다. 처음 설치하는 사람은 먼저 [playwright-allinone_QUICKSTART.md](playwright-allinone_QUICKSTART.md)를 본다. 포트, 파일, 환경변수의 정확한 목록은 [playwright-allinone_REFERENCE.md](playwright-allinone_REFERENCE.md)를 본다.

## 1. 표준 실행 방식

같은 머신에서 빌드와 실행을 함께 한다면 아래가 표준이다.

```bash
cd playwright-allinone
./build.sh --redeploy
```

자주 쓰는 옵션:

| 명령 | 데이터 보존 | 이미지 정의 반영 | 용도 |
| --- | --- | --- | --- |
| `./build.sh --redeploy` | 보존 | 기존 provision 결과 재사용 | 일반 재배포 |
| `./build.sh --redeploy --reprovision` | 보존 | chatflow/job/provider 재생성 | 운영 표준 재프로비저닝 |
| `./build.sh --redeploy --fresh` | 삭제 | 전체 재생성 | 개발/초기화 |
| `./build.sh --redeploy --no-agent` | 보존 | 기존 provision 결과 재사용 | 컨테이너만 기동하고 agent는 수동 연결 |

처음 운영 환경에서 잘 모르겠으면 `./build.sh --redeploy`를 사용한다. `--fresh`는 데이터를 지우므로 개발 초기화가 필요할 때만 쓴다. `--reprovision` 전에는 `./backup-volume.sh`로 백업을 먼저 만든다.

## 2. 수동 `docker run` 배포

빌드 머신과 실행 머신이 다르거나 폐쇄망으로 옮기는 경우에는 수동으로 실행한다.

### 2.1 이미지 빌드

온라인 빌드 머신:

```bash
cd playwright-allinone
chmod +x *.sh
./build.sh
```

산출물:

```text
dscore.ttc.playwright-YYYYMMDD-HHMMSS.tar.gz
```

실행 머신에는 이미지 tarball과 `playwright-allinone/` 폴더 전체를 같이 가져간다. agent 스크립트와 운영 문서가 이 폴더에 있기 때문이다.

### 2.2 컨테이너 기동

실행 머신:

```bash
docker load -i dscore.ttc.playwright-*.tar.gz

case "$(uname -s)" in
  Darwin) AGENT_NAME=mac-ui-tester ;;
  *)      AGENT_NAME=wsl-ui-tester ;;
esac

HOST_RECORDINGS_DIR="${RECORDING_HOST_ROOT:-$HOME/.dscore.ttc.playwright-agent/recordings}"
mkdir -p "$HOST_RECORDINGS_DIR"

docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  -v "$HOST_RECORDINGS_DIR":/recordings:rw \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:26b \
  -e AGENT_NAME="$AGENT_NAME" \
  --restart unless-stopped \
  dscore.ttc.playwright:latest

docker logs -f dscore.ttc.playwright
```

중요한 점:

- `AGENT_NAME`은 Mac이면 `mac-ui-tester`, WSL/Linux면 `wsl-ui-tester`로 둔다.
- `-v "$HOST_RECORDINGS_DIR":/recordings:rw`는 Recording UI의 Stop & Convert에 필요하다.
- `--add-host host.docker.internal:host-gateway`는 컨테이너가 호스트 Ollama에 접근하는 데 필요하다.
- `docker logs -f`에서 확인을 마쳤으면 `Ctrl-C`로 빠져나온다. 컨테이너는 계속 실행된다.

## 3. 호스트 agent 운영

컨테이너가 올라와도 agent가 붙지 않으면 Jenkins Pipeline은 대기한다.

Mac:

```bash
./mac-agent-setup.sh
```

Windows 11 / WSL2:

```bash
./wsl-agent-setup.sh
```

agent 재연결은 같은 스크립트를 다시 실행하면 된다. 스크립트가 기존 agent 프로세스와 중복 setup 인스턴스를 정리한다.

상태 확인:

```bash
case "$(uname -s)" in
  Darwin) NODE=mac-ui-tester ;;
  *)      NODE=wsl-ui-tester ;;
esac

curl -sS -u admin:password "http://localhost:18080/computer/$NODE/api/json" \
  | grep -oE '"offline":(true|false)'
```

`"offline":false`이면 연결된 상태다.

## 4. Recording UI 운영

Recording UI는 호스트에서 도는 FastAPI 서비스다.

```bash
./run-recording-ui.sh start
./run-recording-ui.sh status
./run-recording-ui.sh doctor
./run-recording-ui.sh logs
./run-recording-ui.sh restart
./run-recording-ui.sh stop
```

무엇부터 볼지 모르겠으면 `doctor`로 의존성을 확인하고, `status`로 실행 상태를 본다.

기본값:

| 항목 | 값 |
| --- | --- |
| URL | `http://localhost:18092` |
| health | `http://localhost:18092/healthz` |
| log | `~/.dscore.ttc.playwright-agent/recording-service.log` |
| pid | `~/.dscore.ttc.playwright-agent/recording-service.pid` |
| sessions | `~/.dscore.ttc.playwright-agent/recordings` |

환경변수:

```bash
RECORDING_PORT=18192 ./run-recording-ui.sh start
RECORDING_PYTHON=~/.dscore.ttc.playwright-agent/venv/bin/python3 ./run-recording-ui.sh start
RECORDING_HOST_ROOT=/path/to/recordings ./run-recording-ui.sh start
```

R-Plus 기능은 `done` 상태 세션에서 기본 활성화된다. Replay, Generate Doc, Compare with Doc-DSL은 `/experimental/*` API를 사용한다.

## 5. 백업과 복원

운영 데이터는 Docker volume `dscore-data`에 있다. 호스트 agent workspace와 Recording UI session은 재생성 가능하지만, 필요한 경우 별도로 파일 백업 정책을 둔다.

백업:

```bash
cd playwright-allinone
./backup-volume.sh
```

복원:

```bash
docker stop dscore.ttc.playwright 2>/dev/null || true
docker rm dscore.ttc.playwright 2>/dev/null || true

cd playwright-allinone
./restore-volume.sh /path/to/dscore-data-YYYYMMDD-HHMMSS.tar.gz
```

복원 후에는 2.2의 `docker run` 명령으로 컨테이너를 띄운다.

기존 볼륨을 지우고 복원해야 할 때만:

```bash
./restore-volume.sh --fresh /path/to/dscore-data-YYYYMMDD-HHMMSS.tar.gz
```

## 6. 업그레이드 절차

운영 데이터를 보존하면서 이미지와 정의를 갱신하는 안전한 순서:

```bash
cd playwright-allinone

./backup-volume.sh
./build.sh --redeploy --reprovision
```

그 다음 운영 환경에 맞는 agent 스크립트 하나만 실행한다.

```bash
./mac-agent-setup.sh       # Mac
./wsl-agent-setup.sh       # WSL2
```

`--redeploy`만 쓰면 기존 `/data/.app_provisioned` 때문에 chatflow YAML, Jenkins job 정의, provider 등록 변경이 반영되지 않을 수 있다. 이 정의들이 바뀐 릴리스라면 `--reprovision`을 사용한다.

## 7. 모델 변경

컨테이너에는 Ollama가 없다. 모델 작업은 호스트에서 한다.

```bash
ollama list
ollama pull llama3.1:8b
```

모델을 영구 변경하려면 다음을 같이 맞춘다.

1. 호스트에 모델 pull
2. `dify-chatflow.yaml`의 Planner/Healer 모델명 수정
3. 컨테이너를 새 `OLLAMA_MODEL`로 재기동
4. `--reprovision` 또는 `.app_provisioned` 제거 후 재기동
5. agent 재연결

간단한 개발 환경이면:

```bash
OLLAMA_MODEL=llama3.1:8b ./build.sh --redeploy --reprovision
```

운영 환경에서는 먼저 백업한다.

## 8. Test Planning RAG 운영

프로비저닝 후 Dify에는 두 앱이 생성된다.

| 앱 | 용도 |
| --- | --- |
| `ZeroTouch QA Brain` | Jenkins Pipeline에서 14대 DSL 생성/치유 |
| `Test Planning Brain` | 기획서/테스트 이론 기반 테스트 계획 생성 |

Test Planning KB:

| KB | 용도 |
| --- | --- |
| `kb_project_info` | spec, 기획서, API 문서 |
| `kb_test_theory` | 테스트 설계 기법, V-model, 회귀 정책 |

baseline 문서는 `examples/test-planning-samples/`에서 자동 업로드된다. 운영자가 문서를 갱신하면 Dify console에서 다시 인덱싱한다.

## 9. Smoke Check

서비스:

```bash
curl -fsS http://localhost:18080/login >/dev/null && echo "Jenkins ok"
curl -fsS http://localhost:18081/ >/dev/null && echo "Dify ok"
curl -fsS http://localhost:18092/healthz >/dev/null && echo "Recording UI ok"
```

컨테이너:

```bash
docker ps | grep dscore.ttc.playwright
docker exec dscore.ttc.playwright supervisorctl status
```

Ollama:

```bash
ollama list
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null
```

테스트 수집:

```bash
python3 -m pytest --collect-only -q test
```

2026-04-30 기준 현재 테스트 컬렉션은 699건이다.

## 10. 자주 막히는 지점

| 증상 | 확인 | 조치 |
| --- | --- | --- |
| Jenkins Pipeline이 agent offline에서 대기 | `NODE=... curl .../computer/$NODE/api/json` | `mac-agent-setup.sh` 또는 `wsl-agent-setup.sh` 재실행 |
| Dify 호출 timeout | `ollama list`, `docker exec ... curl host.docker.internal:11434/api/tags` | 호스트 Ollama 기동, `--add-host` 포함 여부 확인 |
| Recording UI Stop & Convert 실패 | session log와 docker mount 확인 | 수동 run에 `/recordings` bind mount 추가 |
| WSL에서 브라우저 창이 안 뜸 | `echo $DISPLAY $WAYLAND_DISPLAY` | `wsl --update`, `wsl --shutdown`, WSLg 확인 |
| Dify share URL에 포트가 빠짐 | `APP_WEB_URL`/`DIFY_PUBLIC_URL` | 표준 `18081:18081` 매핑 사용 또는 `DIFY_PUBLIC_URL` 지정 |
| 빌드 디스크 부족 | Docker disk usage 확인 | `docker builder prune -a -f` |

더 깊은 원인 분석이 필요하면 [playwright-allinone_REFERENCE.md](playwright-allinone_REFERENCE.md)의 로그와 파일 위치를 확인한다.
