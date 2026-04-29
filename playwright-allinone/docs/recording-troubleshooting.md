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

## 8. R-Plus (Replay / Generate Doc / Compare)

TR.4+.4 (2026-04-29) — **R-Plus 게이트 폐기, 항상 활성**. 메인 결과 화면에서 `state=done` 이면 R-Plus 섹션이 자동 노출되어 [Replay] / [Generate Doc] / [Compare with Doc-DSL] 버튼이 함께 표시된다.

백엔드는 `recording_service/rplus/router.py` 모듈로 분리되어 `/experimental/sessions/{sid}/replay|enrich|compare` URL prefix 를 사용 — 코드 조직상 분리이지 실행 차단은 없음.

### R-Plus 버튼이 안 보이는 경우

| 증상 | 원인 | 해결 |
|---|---|---|
| 결과 패널은 보이는데 R-Plus 만 hidden | `state` 가 `done` 이 아님 (recording / error 등) | 녹화 종료 후 변환 완료 (state=done) 까지 대기 |
| 페이지 캐시 | 메인 페이지가 이전 코드 (게이트 시절) | 강력 새로고침 (Cmd+Shift+R) |
| 결과 패널 자체가 안 보임 | 세션이 열려 있지 않음 | 세션 row 의 [열기] 또는 stop 직후 자동 표시 |

### Replay / Enrich / Compare 가 502 에러

| 기능 | 의존성 | 운영자 점검 |
|---|---|---|
| Replay | 컨테이너 측 executor (`docker exec`) | `docker ps` 로 `dscore.ttc.playwright` 가 Up 상태인지 |
| Generate Doc | Ollama (`OLLAMA_BASE_URL`) | `ollama serve` 기동 + `gemma4:26b` pull 완료 |
| Compare | recording-service 단독 (외부 의존성 0) | n/a |

## 9. 결과 화면의 다운로드 / 열람 (TR.4+.1, TR.4+.2)

`state=done` 또는 codegen 산출물이 있을 때 결과 화면 아래 두 카드가 노출된다:

| 카드 | 내용 | 다운로드 |
|---|---|---|
| Scenario JSON | 변환된 14-DSL (`scenario.json`) | ⬇ → `<sid>-scenario.json` |
| Original Script (.py) | codegen 원본 (`original.py`) | ⬇ → `<sid>-original.py` |

엔드포인트 직접 호출도 가능:

```bash
curl -sS http://127.0.0.1:18092/recording/sessions/<sid>/scenario             # 본문
curl -sS http://127.0.0.1:18092/recording/sessions/<sid>/scenario?download=1  # 첨부
curl -sS http://127.0.0.1:18092/recording/sessions/<sid>/original             # 본문
curl -sS http://127.0.0.1:18092/recording/sessions/<sid>/original?download=1  # 첨부
```

## 8.5 hidden-click 자동 복구 (T-H, 2026-04-29)

녹화한 .py 가 드롭다운/메뉴 안의 항목을 click 할 때 hover 가 누락되거나 GNB 가 페이지 로드 직후 collapsed 되어 `element is not visible` timeout 으로 실패하는 케이스에 대응. 4 layer + visible-first + 5단계 healer 가 서로 다른 실행 경로/실패 패턴을 보호:

| Layer | 적용 경로 | 동작 |
|---|---|---|
| (B) visible-first selector | resolver 본체 (Play with LLM) | `role=X, name=Y` 다중 매치 시 `filter(visible=True).first` 우선. 모두 hidden 이면 `.first` 폴백. |
| Visibility Healer | **Play with LLM** (executor) | 1차 click 시도 직전 5단계 (D scroll_into_view → ancestor hover → E page-level activator → F size poll → C sibling swap). DOM 직접 분석으로 가장 강력. |
| Converter heuristic | LLM 변환 시점 (codegen → 14-DSL) | chain 안에 `nav` / `menu` / `dropdown` / `gnb` / `aria-haspopup` 같은 신호가 보이면 click 앞에 hover step 자동 prepend. 정적이라 codegen 이 ancestor 를 chain 안에 emit 한 경우만 동작. |
| Static Annotator | **Codegen Output Replay** (원본 .py 직접 실행) | play-codegen 진입 시 자동 호출. AST 분석으로 `<chain>.click()` 의 chain 안 hover-trigger ancestor 를 찾아 `<ancestor>.hover()` 라인을 click 직전에 삽입한 `original_annotated.py` 생성. 결과 패널에 `annotate: examined N → injected M` 표시. |

### Visibility Healer 5단계 + Click 폴백 (LLM 경로)

각 단계에서 visible 되면 즉시 단축, 모두 실패해도 silently None 반환 (후속 fallback_targets / LocalHealer / Dify 치유 진입):

1. **(D) scroll_into_view_if_needed** — Intersection Observer 트리거 (lazy menu / scroll-pinned nav).
2. **ancestor hover** — `aria-haspopup` / `aria-expanded=false` / `role=menu/menubar/listbox` / `nav`/`details`/`summary` / `data-state=closed` / `:hover` CSS rule trigger 후보 5개까지 hover.
3. **(E) page-level activator probe** — `<header>` / `<nav>` / `<main>` / `<body>` 순환 hover. ktds.com 처럼 페이지 어디든 mouseover 들어오면 GNB 가 활성화되는 사이트.
4. **(F) size-aware poll** — `is_visible()` 200ms 간격 × 10회. 폰트/CSS 비동기 로딩으로 늦게 expand.
5. **(C) sibling swap** — `filter(visible=True).first` 로 같은 selector 의 visible 형제 교체.

총 추가 시간: hidden 케이스에서 ~6s 한도. 정상 케이스 영향 0.

#### (G) JS dispatch click 폴백 — click 시점 마지막 수단

위 5단계 + Playwright 의 자체 stability retry (~30s) 가 모두 통과 못한 마지막 케이스. `_perform_action` 의 click 분기에서 actionability 거부 메시지 (`not visible` / `outside of the viewport` / `intercepts pointer events` / `Element is not stable`) 잡으면 **element 가 anchor/button 류일 때만** `locator.evaluate("el => el.click()")` 로 DOM click event 직접 발화.

- **적용 조건**: `<a>` / `<button>` / `<input type="button"|submit>` / `[role=button|link|menuitem|tab|option|checkbox]` / 명시적 `onclick` listener 보유.
- **ktds.com 같은 케이스**: GNB anchor 의 computed `height:0 / line-height:0` 으로 normal/force click 모두 거부. JS click 은 DOM event 만 발화 → href 처리 + listener 동작.
- **위험 통제**: 일반 `<div>` 같은 비-clickable 에는 발사 안 함 (false-positive PASS 방지).

### Annotate 가 `injected: 0` 을 반환할 때

**버그가 아니라 입력의 한계**. annotate 는 codegen 이 emit 한 chain 의 selector 텍스트만 본다:

| codegen 출력 형태 | Annotate | 다른 layer |
|---|---|---|
| `page.locator('nav#gnb').locator('li')...click()` (chain 에 ancestor 보임) | ✅ hover prepend | converter heuristic 도 동일 prepend |
| `page.get_by_role('link', name='회사소개').click()` (flat selector — chain 에 ancestor 없음) | ❌ anchor 0건 | Visibility Healer 가 runtime DOM 분석으로 처리 |
| 호버 메뉴인데 codegen 이 hover 도 emit 안 함 + flat selector | ❌ | Visibility Healer 만 가능 |

### 운영 권장 흐름

1. **[▶ Codegen Output Replay]** — annotate 자동 + 원본 실행. annotate injected 가 0이어도 정상 (다른 layer 가 처리).
2. 실패하면 **[▶ Play with LLM]** — Visibility Healer 5단계가 가동.
3. 둘 다 실패 + stderr "element is not visible" → 사이트의 활성화 트리거가 위 5단계로 못 잡는 특수 패턴 (예: 햄버거 클릭으로만 열리는 모바일 드로어). 녹화를 다시 — GNB 항목 위에 1~2초 머무른 후 클릭하면 codegen 이 hover step 을 캡처할 가능성↑.

## 9. iframe / Shadow DOM 한계 (T-C / P0.2)

### iframe 접근

DSL `target` 의 `frame=<selector> >> ...` 체인으로 iframe 안의 element 에 접근. 중첩 iframe 도 같은 방식으로 누적.

```json
{"action": "click", "target": "frame=#payment-iframe >> role=button, name=Pay"}
{"action": "click", "target": "frame=#outer >> frame=#inner >> #deep-btn"}
```

codegen 으로 녹화한 시나리오의 `page.frame_locator(...)` 체인은 [`converter_ast.py`](zero_touch_qa/converter_ast.py) 의 `_segments_to_target` 가 자동으로 `frame=...` prefix 로 보존한다.

### Open shadow DOM

Playwright 가 piercing 을 자동 처리. 일반 selector (`#submit-btn`) 만으로 매치된다. 명시적으로 표시하려면 `shadow=<host> >> ...` chain 사용.

```json
{"action": "fill", "target": "shadow=my-form >> #name-input", "value": "alice"}
```

### Closed shadow DOM — 자동화 불가

`mode: "closed"` 로 attach 된 shadow root 는 브라우저 정책상 외부에서 접근 불가. `shadow=<host> >> ...` chain 으로 시도하면 resolver 가 `ShadowAccessError` 를 던지고 step 이 즉시 FAIL (timeout hang 없음).

| 증상 | 원인 | 해결 |
|---|---|---|
| `closed shadow root — automation 불가` 에러 | `attachShadow({mode:'closed'})` 컴포넌트 | (1) 앱이 open mode 로 attach 하도록 수정 (가능하면 권장) (2) 해당 흐름을 frame/popup 으로 우회 (3) 시나리오 재설계로 우회 |
| `frame=...` 에 매치 0건 | iframe id/selector 오타, 또는 iframe 이 아직 로드 안 됨 | `wait` 액션으로 idle 대기 + selector 재확인 |
| nested iframe 에서 inner 매치 실패 | 외부 iframe 의 srcdoc 안의 inner iframe 은 cross-document — 복합 selector 필요 | `frame=#outer >> frame=#inner >> ...` chain 사용 |

회귀 가드: [`test/test_iframe_shadow.py`](../test/test_iframe_shadow.py) 10/10. fixture 는 [`test/fixtures/iframe_payment.html`](../test/fixtures/iframe_payment.html), `iframe_nested.html`, `shadow_open.html`, `shadow_closed.html`.

## 10. 로그 위치 정리

| 파일 | 내용 |
|---|---|
| `~/.dscore.ttc.playwright-agent/recording-service.log` | 호스트 데몬 stdout/stderr |
| `~/.dscore.ttc.playwright-agent/recording-service.pid` | 호스트 데몬 PID |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/metadata.json` | 세션 메타 |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/original.py` | codegen 원본 |
| `~/.dscore.ttc.playwright-agent/recordings/<id>/scenario.json` | 14-DSL 변환 결과 |
