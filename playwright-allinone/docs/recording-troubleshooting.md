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
| `/usr/local/bin/python: No module named zero_touch_qa` | (회귀) `converter_proxy.py` 가 default `python` + workdir 미지정으로 호출 | **2026-04-29 fix 완료** — `docker exec -w /opt ... /opt/qa-venv/bin/python -m zero_touch_qa ...` 로 호출. `recording_service/converter_proxy.py:83-92` 참조. recording_service 재시작 필요 |
| `ModuleNotFoundError: No module named 'requests'` | `/usr/local/bin/python` 에는 zero_touch_qa 의존성 미설치 (의존성은 `/opt/qa-venv/bin/python` 에만 있음) | **2026-04-29 fix 완료** — 위 fix 와 묶여 있음 |

### 직접 호출로 격리

UI 거치지 않고 컨테이너 CLI 만 호출해 격리 테스트:

```bash
SID=test_xxx
# 2026-04-29 이후 표준 호출 (workdir=/opt + qa-venv 인터프리터)
docker exec -w /opt -e ARTIFACTS_DIR=/recordings/$SID dscore.ttc.playwright \
  /opt/qa-venv/bin/python -m zero_touch_qa --mode convert --convert-only \
  --file /recordings/$SID/original.py
```

`/usr/local/bin/python` (default) 으로는 `No module named zero_touch_qa` 또는
`No module named requests` 가 난다. zero_touch_qa 패키지는 `/opt/zero_touch_qa/`
에 위치하고 의존성은 `/opt/qa-venv/` 에만 설치되어 있다.

## 4-1. Stop & Convert 가 60초 hang 후 timeout / 응답 없음

### 증상

- Recording UI 의 "■ Stop & Convert" 클릭 후 응답이 안 옴 (또는 curl `-m 60` 으로 타임아웃)
- `~/.dscore.ttc.playwright-agent/recording-service.log` 에 `[codegen] SIGTERM PID=...` 만 찍히고
  `[convert-proxy] docker exec ...` 는 안 찍힘
- 세션 state 가 `recording` 그대로 박제 (converting/done/error 어느 쪽도 아님)
- 재시도하면 `409 활성 codegen 핸들이 없습니다` (handle 은 이미 `_pop_handle` 로 빠짐)

### 원인

`codegen_runner.stop_codegen` 이 SIGTERM 후 `proc.stdout.read()` / `proc.stderr.read()`
로 codegen 의 표준 출력을 회수하는데, Playwright 가 띄운 자식 Chromium 프로세스
들이 같은 pipe FD 를 상속·보유한 채 살아 있으면 EOF 가 안 와서 `pipe.read()` 가
무기한 블록된다. → /recording/stop 핸들러가 SIGTERM 직후 멈춰 docker exec
변환 단계까지 도달하지 못함.

### 해결 (2026-04-29 fix 완료)

[recording_service/codegen_runner.py](../recording_service/codegen_runner.py) 변경:

1. `start_codegen` 의 `Popen` 에 `start_new_session=True` 추가 → codegen + Chromium
   자식들이 같은 process group 에 묶임
2. `stop_codegen` 이 `os.killpg(os.getpgid(pid), SIGTERM)` 으로 group 단위 종료
   (실패 시 단일 PID fallback). 유예 후에도 살아 있으면 `os.killpg(..., SIGKILL)`
3. SIGTERM/SIGKILL 후 stdout/stderr 를 read 하지 않고 close 만. `handle.stdout` /
   `handle.stderr` 는 어디서도 사용되지 않으므로 빈 문자열로 둠

### 검증

```bash
# 호스트 venv 에 설치된 recording_service 가 새 코드인지 확인
grep -n "start_new_session\|killpg\|_close_pipes" recording_service/codegen_runner.py

# end-to-end (start → stop) 가 1초 안에 끝나야 함
SID=$(curl -s -X POST http://localhost:18092/recording/start \
  -H "Content-Type: application/json" \
  -d '{"target_url":"https://example.com"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
sleep 5
curl -s -m 30 -X POST http://localhost:18092/recording/stop/$SID -w "\nHTTP %{http_code} elapsed %{time_total}s\n"
# state=done, step_count >= 1, scenario.json 생성 확인
ls ~/.dscore.ttc.playwright-agent/recordings/$SID/
```

### 임시 우회 (recording_service 재시작 불가 환경)

브라우저 창을 직접 닫으면 codegen 이 자연 종료되어 pipe 가 풀린다. 다만
`/recording/stop` 가 안 불리므로 변환은 트리거되지 않고 세션은 `state=recording`
박제 → 재시작 시 `state=error, "server 재시작으로 codegen subprocess 가 끊겼습니다 (orphan)"`
으로 마킹됨. 이후 §4 의 "직접 호출로 격리" 스니펫으로 수동 변환.

## 4-2. popup/새 탭에서 한 액션이 scenario.json 에 누락

### 증상

- codegen 의 [original.py](https://playwright.dev/docs/codegen-intro) 에는 `page1.click(...)` /
  `page2.click(...)` 형태로 popup 탭 액션이 있는데
- 변환된 scenario.json 에는 **page (메인 탭) 의 액션만** 들어 있음
- 예: 사용자가 "뉴스홈" 링크 클릭 (popup → page1) 후 page1 에서 "엔터" 클릭, "김연아" 링크 클릭 했을 때
  → original.py: 6 액션, scenario.json: 4 스텝 (popup 액션 2개 손실)

### 원인

[zero_touch_qa/converter.py](../zero_touch_qa/converter.py) 의 라인 필터가 `page.` /
`expect(` 로 시작하는 라인만 받는다. codegen 은 popup 탭을 `with page.expect_popup() as page1_info:`
컨텍스트로 받아 `page1` 변수에 바인딩하므로 후속 액션이 `page1.click(...)` 으로 시작한다.
이 라인은 `page.` 로 시작하지 않아 통째로 스킵됨.

### 해결 (2026-04-29 fix 완료)

[converter.py:51-58](../zero_touch_qa/converter.py#L51-L58) — 필터 직전에
`re.sub(r"\bpage\d+\.", "page.", line)` 한 줄로 popup 변수를 메인 page 변수로 정규화.

executor 는 매 스텝 후 `context.pages` 변화를 감지해 활성 page 를 자동 전환하므로
([executor.py:166-201](../zero_touch_qa/executor.py#L166-L201)) 평탄화된 단일 시퀀스로
넘겨도 popup 탭에서의 클릭이 정상 매칭된다.

### 검증

```bash
# 기존 popup 포함 original.py 로 재변환 — pageN 라인 픽업 확인
docker exec -w /opt -e ARTIFACTS_DIR=/recordings/<sid> dscore.ttc.playwright \
  /opt/qa-venv/bin/python -m zero_touch_qa --mode convert --convert-only \
  --file /recordings/<sid>/original.py
# scenario.json step 수가 popup 액션 수만큼 늘어났는지 확인
```

### 영구 반영

호스트 측 converter.py 만 수정하면 다음 이미지 빌드 (`./build.sh --redeploy --fresh`)
부터 컨테이너 baked-in. 핫패치만 한 경우 컨테이너 재기동 시 사라지므로
주의.

### 알려진 후속 이슈 (TR.x backlog) — **2026-04-29 closed (T-A 완료)**

~~`.nth(N)` (같은 텍스트의 N 번째 매칭) 정보는 정규화 후에도 보존되지 않음~~ →
T-A (P0.4 / `zero_touch_qa/converter_ast.py` 신설) 로 해소. AST 기반 converter
가 `.nth(N)` / `.first` / `.filter(has_text=...)` / `frame_locator` chain /
nested locator 를 모두 14-DSL `target` 의 후미 modifier (`, nth=N` /
`, has_text=T`) 또는 `>>` chain 으로 손실 없이 보존한다. 동일 텍스트 다중 매칭
disambiguation 도 자동 해결.

resolver 측 (`zero_touch_qa/locator_resolver.py`) 도 `_split_modifiers` /
`_apply_modifiers` 로 receiver-side 에서 동일하게 해석. `_resolve_raw` 헬퍼는
`.first` 미적용 multi-element locator 를 반환해 `.nth(N).first` race 회피.

**남은 후속 이슈 (T-C 범위)**: `frame=...>>...` chain 의 executor 측
frame_locator traversal 은 T-C (P0.2) 범위 — converter 는 target 문자열에
정확히 보존하지만 실 실행은 T-C 의 resolver 확장 후 가능.

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

## 8. R-Plus (Replay / Generate Doc / Compare) 활성화

R-Plus 는 **백엔드만 분리**되어 있고 UI 는 메인 결과 화면에 함께 노출된다 — 평가 단계 (rubric / 5-sample / 90% replay DoD) 통과 전까지는 환경변수로 OFF.

**진입**: `http://localhost:18092/` — 녹화 후 결과 패널이 열리면 `state=done` AND `rplus_enabled=true` 조건일 때 [Replay] / [Generate Doc] / [Compare with Doc-DSL] 버튼이 동일 페이지에 함께 표시된다.

### 활성화 방법

```bash
# recording-service 데몬 환경에서 RPLUS_ENABLED=1 필요
RPLUS_ENABLED=1 ./mac-agent-setup.sh
# 또는
RPLUS_ENABLED=1 uvicorn recording_service.server:app --host 0.0.0.0 --port 18092
```

미설정 상태에서는:

- `/healthz` 응답 `rplus_enabled=false`
- `/experimental/sessions/{sid}/replay|enrich|compare` 모든 요청 → 404 (`R-Plus 기능이 비활성 상태입니다`)
- 메인 UI 결과 화면의 R-Plus 섹션이 hidden 으로 노출되지 않음

활성화 후 페이지 새로고침하면 결과 패널 아래에 R-Plus 섹션이 자동으로 나타난다.

### R-Plus 버튼이 안 보이는 경우

| 증상 | 원인 | 해결 |
|---|---|---|
| 버튼 자체가 안 보임 | `rplus_enabled=false` | env 적용 후 데몬 재시작 |
| 버튼 클릭 시 `RPLUS_ENABLED 미설정` 알림 | 프론트 캐시가 지난 healthz 응답 사용 | 페이지 새로고침 |
| 결과 패널은 보이는데 R-Plus 만 hidden | `state` 가 `done` 이 아님 (recording / error 등) | 녹화 종료 후 변환 완료 (state=done) 까지 대기 |
| `/healthz.rplus_enabled` 가 false | env 가 데몬 프로세스에 안 들어감 | `launchctl print` / `systemctl show` 로 환경변수 확인 |

## 9. 로그 위치 정리

| 파일 | 내용 |
|---|---|
| `~/.dscore.ttc.playwright-agent/recording-service.log` | 호스트 데몬 stdout/stderr |
| `~/.dscore.ttc.playwright-agent/recording-service.pid` | 호스트 데몬 PID |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/metadata.json` | 세션 메타 |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/original.py` | codegen 원본 |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/scenario.json` | 14-DSL 변환 결과 |
