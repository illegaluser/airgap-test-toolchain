# playwright-allinone_QUICKSTART: 처음 실행 가이드

이 문서는 처음 사용하는 사람이 **빌드 → 컨테이너 기동 → 호스트 agent 연결 → 첫 Pipeline 실행 → Recording UI 확인**까지 한 번 성공하는 데 필요한 절차만 담는다.

목표는 “일단 한 번 성공”이다. 백업, 수동 배포, 모델 변경은 [playwright-allinone_OPERATIONS.md](playwright-allinone_OPERATIONS.md), 포트와 파일 위치는 [playwright-allinone_REFERENCE.md](playwright-allinone_REFERENCE.md)를 본다.

## 시작하기 전에

이 문서의 명령은 모두 `playwright-allinone/` 폴더에서 실행한다.

```bash
cd playwright-allinone
```

예상 소요 시간:

| 단계 | 보통 걸리는 시간 |
| --- | --- |
| 첫 이미지 빌드 | 30-90분 |
| 이후 재빌드 | 3-10분 |
| 컨테이너 첫 기동과 프로비저닝 | 3-10분 |
| 첫 Pipeline 실행 | 수 분 |

명령이 오래 멈춘 것처럼 보여도 Docker 이미지 빌드 중이면 정상일 수 있다. 에러 메시지가 나오지 않았으면 먼저 기다린다.

## 1. 먼저 이해할 것

이 배포본은 두 부분으로 나뉜다.

| 위치 | 실행되는 것 | 이유 |
| --- | --- | --- |
| Docker 컨테이너 | Jenkins, Dify, PostgreSQL, Redis, Qdrant, nginx | 서버 구성 요소를 한 번에 올리기 위해 |
| 호스트 | Ollama, Jenkins agent, Playwright Chromium, Recording UI 서비스 | GPU와 실제 데스크탑 브라우저 창이 필요하기 때문에 |

컨테이너 안에는 Ollama와 Jenkins agent가 없다. 호스트에서 agent 스크립트를 실행해야 Jenkins Pipeline이 실제 브라우저를 띄울 수 있다.

짧은 용어 설명:

| 용어 | 뜻 |
| --- | --- |
| Jenkins | 테스트 실행 버튼과 결과 화면을 제공하는 자동화 서버 |
| Dify | 자연어 요구사항을 테스트 시나리오로 바꾸는 LLM 앱 서버 |
| Ollama | PC에서 LLM 모델을 실행하는 프로그램 |
| agent | Jenkins의 일을 실제 PC에서 실행하는 연결 프로세스 |
| Recording UI | 브라우저 조작을 녹화해서 테스트 시나리오로 바꾸는 화면 |

## 2. 준비물

공통:

- Docker Desktop 4.30+
- RAM 16GB 이상
- 빌드 시 디스크 여유 20GB 이상
- 호스트 Ollama
- JDK 21
- Python 3.11+

Mac 터미널:

```bash
brew install ollama openjdk@21 python@3.12
brew services start ollama
ollama pull gemma4:26b
```

Windows 11:

- Windows에 Docker Desktop과 Ollama를 설치한다.
- Jenkins agent는 WSL2 Ubuntu에서 실행한다.
- Ollama는 Windows 네이티브로 실행하는 것을 권장한다.

PowerShell:

```powershell
wsl --install -d Ubuntu-22.04
winget install Ollama.Ollama
ollama pull gemma4:26b
```

WSL2 Ubuntu:

```bash
sudo apt update
sudo apt install -y openjdk-21-jdk-headless python3.12 python3.12-venv
```

WSL2에서 Python 3.12 패키지가 없으면 `python3.11 python3.11-venv`를 설치해도 된다.

이미 설치되어 있는지 확인하려면:

```bash
docker --version
ollama --version
java -version
python3 --version
```

## 3. 가장 빠른 시작

빌드와 실행을 같은 머신에서 한다면 이 경로가 가장 단순하다.

```bash
chmod +x *.sh
./build.sh --redeploy
```

이 명령은 다음 일을 한다.

1. Docker 이미지 빌드
2. 기존 `dscore.ttc.playwright` 컨테이너 제거 후 새 컨테이너 기동
3. `dscore-data` 볼륨 유지
4. Recording UI용 recordings 디렉토리를 컨테이너 `/recordings`에 bind mount
5. `NODE_SECRET` 대기
6. 호스트 agent setup 스크립트 실행

중간에 관리자 권한이나 Docker 권한 관련 오류가 나오면 그 오류를 먼저 해결한 뒤 같은 명령을 다시 실행한다. `--redeploy`는 기존 운영 데이터 볼륨을 지우지 않는다.

macOS Docker Desktop에서 `docker buildx build --load`가 `sending tarball` 단계에서 오래 멈추면 아래처럼 plain build로 우회할 수 있다.

```bash
docker build --platform linux/arm64 -f Dockerfile -t dscore.ttc.playwright:latest .
```

x86/amd64 머신이면 `--platform linux/amd64`를 사용한다.

## 4. 기동 확인

컨테이너:

```bash
docker ps | grep dscore.ttc.playwright
docker logs -f dscore.ttc.playwright
```

로그에서 아래를 확인한다. `docker logs -f`는 로그를 계속 따라가는 명령이므로, 확인을 마쳤으면 `Ctrl-C`로 빠져나온다. 컨테이너는 중지되지 않는다.

```text
NODE_SECRET: <64자 hex>
프로비저닝 완료
```

서비스 URL:

| 서비스 | URL | 기본 계정 |
| --- | --- | --- |
| Jenkins | http://localhost:18080 | admin / password |
| Dify | http://localhost:18081 | admin@example.com / Admin1234! |
| Recording UI | http://localhost:18092 | 계정 없음, 로컬 서비스 |

## 5. 호스트 agent 연결

`./build.sh --redeploy`가 agent를 자동으로 띄우지 못했거나 새 터미널에서 직접 붙이고 싶으면 아래 중 하나만 실행한다.

Mac:

```bash
./mac-agent-setup.sh
```

Windows 11 / WSL2 Ubuntu:

```bash
./wsl-agent-setup.sh
```

이 스크립트는 JDK/Python 확인, venv 생성, Playwright Chromium 설치, Jenkins agent 연결, Recording UI 서비스 기동을 처리한다.

의존성 자동 설치까지 맡기려면 운영 환경에 맞는 명령 하나만 실행한다.

Mac:

```bash
AUTO_INSTALL_DEPS=true ./mac-agent-setup.sh
```

Windows 11 / WSL2 Ubuntu:

```bash
AUTO_INSTALL_DEPS=true ./wsl-agent-setup.sh
```

agent가 연결되면 Jenkins의 `Build Executor Status`에서 `mac-ui-tester` 또는 `wsl-ui-tester`가 online 상태로 보인다.

## 6. 첫 Pipeline 실행

브라우저에서 Jenkins에 접속한다.

```text
http://localhost:18080
```

로그인:

```text
admin / password
```

처음 실행:

1. Jenkins 첫 화면에서 `ZeroTouch-QA`를 클릭한다.
2. 왼쪽 메뉴에서 `Build with Parameters`를 클릭한다.
3. 값을 바꾸지 말고 `Build`를 클릭한다.
4. 빌드 번호를 클릭한 뒤 `Console Output`에서 진행 상황을 본다.

기본값은 내장 샘플 페이지인 `http://localhost:18081/fixtures/full_dsl.html`을 대상으로 14대 DSL 흐름을 검증한다. 외부 사이트 차단이나 captcha 영향을 받지 않아 첫 검증에 적합하다.

빌드가 끝나면 Console Output 하단에서 아래를 확인한다.

- `Finished: SUCCESS`

빌드 결과 화면의 결과 파일(artifact)에서 아래 파일도 확인할 수 있다.

- `scenario.json`
- `run_log.jsonl`
- `index.html`
- `regression_test.py`
- `llm_calls.jsonl` / `llm_sla.json`

## 7. Recording UI 확인

Recording UI는 호스트에서 도는 서비스다. 컨테이너 포트가 아니다.

```bash
./run-recording-ui.sh status
```

정상이라면:

```text
url: http://127.0.0.1:18092/
health: ok
```

실행 중이 아니라고 나오면 아래처럼 시작한다.

```bash
./run-recording-ui.sh start
```

브라우저에서 접속:

```text
http://localhost:18092
```

처음 녹화:

1. `target_url` 입력
2. **Start Recording** 클릭
3. 열린 Chromium에서 사용자가 직접 조작
4. **Stop & Convert** 클릭
5. `scenario.json` 확인

Start Recording을 누르면 별도 Chromium 창이 열린다. 그 창에서 한 조작이 녹화 대상이다.

녹화 결과는 기본적으로 아래에 저장된다.

```text
~/.dscore.ttc.playwright-agent/recordings/<session-id>/
```

## 8. 다음 문서

상황별로 다음 문서를 보면 된다.

| 해야 할 일 | 문서 |
| --- | --- |
| 수동 `docker run`으로 운영 머신에 배포 | [playwright-allinone_OPERATIONS.md](playwright-allinone_OPERATIONS.md) |
| 백업/복원/업그레이드 | [playwright-allinone_OPERATIONS.md](playwright-allinone_OPERATIONS.md) |
| Recording UI 재기동/로그 확인 | [playwright-allinone_OPERATIONS.md](playwright-allinone_OPERATIONS.md) |
| 포트, 볼륨, env, 파일 구조 확인 | [playwright-allinone_REFERENCE.md](playwright-allinone_REFERENCE.md) |
