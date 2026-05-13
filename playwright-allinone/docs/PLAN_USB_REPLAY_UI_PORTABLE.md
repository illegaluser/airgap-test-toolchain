# PLAN — USB 휴대용 Replay UI 끝까지 동작하게 만들기 (rev 5)

> **rev 5 변경분 (재리뷰 반영)**
> 1. **구조 분리** — 본 plan 을 **Plan B (설치형 모델 제거) → Plan A (휴대용 모델 완성)** 두 단계로 명확히 나눔. Plan B 가 완전히 commit·검증 통과한 *후에야* Plan A 시작. 본문의 Phase 0 = Plan B, Phase A·B·C·D·E = Plan A 매핑.
> 2. **CI workflow 처리** — [.github/workflows/monitor-runtime-build.yml](../../.github/workflows/monitor-runtime-build.yml) 이 `monitor-build/build-monitor-runtime.sh` 를 직접 호출하므로 Plan B 에서 *반드시* 함께 처리해야 한다. Phase 0 에 B-1 신설.
> 3. **테스트 stub 보정** — [test_auth_profiles.py:1366](../test/test_auth_profiles.py#L1366) 의 stub 이 `cmd[0] == "playwright"` 를 *직접 검사*. A.1 변경 후 stub 이 매칭 안 해서 실 subprocess 호출 → 테스트 깨짐. A.1 에 stub 보정을 *의무 작업* 으로 명시.
> 4. **requirements 경로 정정** — 저장소 루트가 아니라 [playwright-allinone/requirements.txt](../requirements.txt). 본문 경로 수정.
> 5. **잔여 grep 검증 예외** — Plan B 의 검증 grep 이 본 plan 문서 자체를 매칭하지 않도록 `--exclude` 규칙 명시.
> 6. **export-airgap OS 분기 정책** — Windows pack 은 PowerShell 전용, macOS pack 은 Mac 머신 전용. 단일 bash 스크립트가 양쪽 산출 불가. Plan B 에 정책 명시.
> 7. **궁극 목표 vs 이번 release 범위 분리** — §0.2 (제품 비전) 와 §0.3 (본 release 산출) 분리.
>
> **rev 4 에서 가져온 것 (유효):**
> - R1: Windows `Launch-ReplayUI.bat` 에 stderr/stdout redirect 추가
> - R2: `README.txt` (Windows + macOS) 의 `AUTH_PROFILES_DIR` 공유 안내 삭제
> - R3: helper venv 의 playwright + `requirements.txt` 모두 `1.59.0` 핀
> - R4: 테스트 베이스라인 (A.0) + 변경 후 동등성 + (rev 5 신규) stub 보정
> - R5: macOS 빌드 머신 접근 가능 여부 분기

## 0. 이 문서가 다루는 범위와 읽는 법

### 0.1 누가 읽나
- **이 문서를 따라 작업할 사람** — 이 저장소를 처음 보는 엔지니어, 또는 휴대용 Replay UI 가 왜 깨져있는지 모르는 상태에서 끝까지 동작시켜야 하는 사람.
- 따라서 매 단계가 "어떤 명령을 친다 → 무엇이 나와야 한다 → 안 나오면 어디를 본다" 로 닫혀 있어야 한다. 중간에 "알아서 잘 해라" 가 없다.

### 0.2 궁극 목표 (제품 비전)

**exFAT 포맷 USB 한 개**를 들고 Windows PC 든 macOS arm64 PC 든 어디든 꽂아서, 그 안의 폴더 하나를 풀고 `Launch-ReplayUI.bat` (Windows) 또는 `Launch-ReplayUI.command` (macOS) **딱 한 번** 더블클릭하면 다음 4가지가 다 동작한다:

1. 브라우저에 Replay UI 가 뜬다 (`http://127.0.0.1:18094/`).
2. 「+ 새 프로파일」 → 사이트 주소 입력 → 자동으로 chromium 창이 떠서 사람이 로그인하고 닫으면 로그인 상태가 USB 안에 저장된다.
3. 시나리오 `.py` 파일을 업로드하고 ▶ 실행 누르면 chromium 이 그 시나리오를 그대로 재생한다.
4. 실행 결과 / 스크린샷 / HTML 리포트가 USB 안 `data/` 폴더에 누적된다.

**받는 사람 PC 에는 Python 도 Playwright 도 Chrome 도 아무것도 미리 설치돼 있지 않다고 가정한다.** 인터넷도 안 된다고 가정한다. 관리자 권한도 없다고 가정한다.

### 0.2b 이번 release 범위 (실제 산출)

위 비전 4가지를 **Windows zip 으로 끝까지** 달성하는 것이 본 release 의 *필수 통과 조건*. macOS arm64 zip 은 빌드 머신 (M1/M2/M3 Mac) 접근 가능 시 *권장 통과* (§10.5 의 분기 정책). 비전과 release 범위의 의도된 갭 — Mac 머신 없이 출시를 막지 않기 위함.

- 필수 (Windows-only 출시): Plan B 완료 + Plan A 의 코드 변경 5건 + Phase B.1~B.8 통과 + Phase D.4 통과 (별도 Windows PC 1대 또는 임시 폴더 두 위치 sanity).
- 권장 (양 OS 출시): 위 + Phase C.1·C.2 + Phase D.3 통과.

### 0.3 OS 와 파일시스템 가정

- **Windows 폴더 + macOS arm64 폴더**를 같은 exFAT USB 안에 두 개 따로 둔다 (한 폴더로 합치지 않는다).
- exFAT 가정 — Windows 와 macOS 가 R/W 양쪽에서 다 읽고 쓸 수 있는 가장 흔한 포맷.

### 0.4 본 plan 의 큰 결정 사항

이번 작업으로 저장소에서 **"1회 설치형 monitor-runtime 모델"** 을 *완전히 제거* 한다. 즉 `install-monitor.{cmd,ps1,sh}`, `dscore.replay-ui.plist.template`, `run-replay-ui.sh`, `monitor-build/` 폴더 자체가 사라지고 휴대용 모델만 남는다. 캐시 빌더 (`build-monitor-runtime.sh`) 의 *캐시 채움 기능* 은 보존하되, 이름·위치를 휴대용 영역으로 옮기고 (`replay-ui-portable-build/build-cache.sh`), 캐시 디렉토리도 `.monitor-runtime-cache/` → `.replay-ui-cache/` 로 개명한다.

### 0.4b 본 plan 의 구조 — Plan B 먼저, Plan A 나중

본 문서의 모든 단계는 두 묶음으로 나뉜다. 묶음 사이에 **commit 으로 끊고 검증 통과 후 다음** 으로 간다.

| 단원 | 묶음 | 무엇 | 완료 commit |
|---|---|---|---|
| Phase 0 (§4) | **Plan B** | 1회 설치형 모델 흔적·CI·문서·캐시 경로를 모두 제거·이동·개명. 끝나면 저장소가 "휴대용만" 일관됨. | "1회 설치형 monitor-runtime 모델 제거 + 캐시 빌더 이동·개명" |
| Phase A (§5) | **Plan A** 의 코드 변경 | 휴대용에서 받는 사람 PC 가 깨지는 5건 (G3·G6·G7·R1·R2). | "휴대용 Replay UI — 받는 PC 에서 끝까지 동작하도록 차단·진단 5 건 해소" |
| Phase B (§6) | **Plan A** 의 Windows 빌드·검증 | 코드 변경 위에서 win64 zip 산출 + 임시 폴더 시드/실행/결과/HTML/재시작. | (commit 없음 — 검증만) |
| Phase C (§7) | **Plan A** 의 macOS 빌드·검증 | 조건부 (Mac 머신 있을 때). | (commit 없음 — 검증만) |
| Phase D (§8) | **Plan A** 의 USB 통합 운용 | 양 OS 폴더 USB 1개에 합쳐 이동성 검증. | (commit 없음 — 검증만) |
| Phase E (§9) | 조건부 | 검증 실패 시 fallback. | 발동 시에만 별도 commit |

**Plan B 가 완전히 commit·검증 통과한 *후에야* Plan A 시작.** Plan B 가 깨진 상태로 Plan A 에 들어가면 저장소가 두 모델의 흔적이 섞인 채로 코드 변경이 들어가 회귀 추적이 어려워진다.

### 0.5 작업 원칙 (이 원칙을 어기지 말 것)

각 단계 (task) 가 끝날 때마다 **실측 검증** 을 수행한다. 검증은 사람 눈으로만 "되는 것 같아" 가 아니라, 다음 중 하나로 측정 가능해야 한다:

- 특정 파일이 존재 / 존재하지 않음
- 특정 HTTP endpoint 가 200 응답
- 특정 process 가 떠 있음
- 특정 로그 라인이 있음 / 없음
- 특정 명령의 종료 코드가 0

검증이 실패하면 **다음 task 로 절대 넘어가지 않는다.** 원인을 찾아 그 task 안에서 해결한다. "일단 끝까지 가보고 나중에 모아서 디버깅" 은 이 문서의 정신을 정면으로 위배한다.

---

## 1. 우리가 풀려는 문제 — 휴대용 Replay UI 가 뭔지

### 1.1 Replay UI 가 하는 일

[playwright-allinone/docs/replay-ui-guide.md](replay-ui-guide.md) 가 사용자 관점 가이드이고, 본 문서는 *그 가이드대로 동작하게 만들기 위한 빌드/검증/정리 절차서*다.

요약: Recording PC 에서 누가 사이트 한 번 녹화 → Playwright `.py` 시나리오 1개 생성. 그 `.py` 를 모니터링 PC 에 가져다 두면 모니터링 PC 가 자동으로 그 시나리오를 재생 + 결과 검증. Replay UI 는 이 모니터링 PC 측에 떠 있는 웹 UI 다.

### 1.2 배포 모델 — 휴대용 하나뿐

본 작업 이후 Replay UI 의 배포 모델은 **휴대용 (`replay-ui-portable-<OS>-<날짜>.zip`)** 하나뿐이다. 받는 사람은 zip 풀고 폴더 안 launcher 더블클릭. 폴더 안 `data/` 가 데이터 위치. USB 로 옮기면 데이터까지 같이 따라감.

### 1.3 휴대용 zip 안에 들어가야 하는 것

받는 사람 PC 에 아무것도 없다는 가정이라, zip 안에 다 들어 있어야 한다:

```
replay-ui/                          ← 받는 사람이 풀 때 이 폴더가 나옴
├── embedded-python/                ← Python 3.11.9 인터프리터 (Windows)
│   또는 python/                     ← Python 3.11.x (macOS)
├── site-packages/                  ← fastapi / uvicorn / playwright / pywin32 등 pip 패키지
├── chromium/                       ← Playwright 가 띄울 Chromium 브라우저
│   └── chromium-1217/              ← playwright 1.59.0 이 기대하는 revision
├── replay_service/                 ← Replay UI 서버 코드 (FastAPI)
├── monitor/                        ← CLI (`python -m monitor replay-script ...`)
├── recording_shared/               ← trace 분석·실행 래퍼 (shared/ 에서 카피됨)
├── zero_touch_qa/                  ← 시나리오 엔진·자가치유·locator (shared/ 에서 카피됨)
├── Launch-ReplayUI.bat / .command  ← 더블클릭 진입점
├── Stop-ReplayUI.bat / .command    ← 종료
├── README.txt                      ← 받는 사람용 안내
├── data/                           ← 사용자 데이터 (zip 풀린 직후엔 빈 폴더 4개)
│   ├── auth-profiles/
│   ├── scenarios/
│   ├── scripts/
│   └── runs/
└── .pack-stamp                     ← 빌드 도구가 자산 일관성 추적용으로 쓰는 SHA
```

### 1.4 빌드 도구 (Phase 0 이후 상태)

zip 을 만드는 도구는 다음 3개로 정리된다:

- [playwright-allinone/replay-ui-portable-build/build-cache.sh](../replay-ui-portable-build/build-cache.sh) — wheels + chromium 캐시를 인터넷에서 받아 `.replay-ui-cache/` 에 채워두는 사전 작업. Mac · Linux · Git Bash (Windows) 모두 동작. *Phase 0 에서 이 위치·이름으로 옮긴다 — 현재는 `monitor-build/build-monitor-runtime.sh` 에 있음.*
- [playwright-allinone/replay-ui-portable-build/pack-windows.ps1](../replay-ui-portable-build/pack-windows.ps1) — Windows 빌드 머신에서 PowerShell 로. 위 캐시를 소비해 받는 사람용 Windows zip 산출.
- [playwright-allinone/replay-ui-portable-build/pack-macos.sh](../replay-ui-portable-build/pack-macos.sh) — macOS arm64 빌드 머신에서 bash 로. 위 캐시 + Apple python-build-standalone 으로 macOS zip 산출.

세 도구가 자동으로 하는 일:

1. Python 인터프리터 (embeddable Python / python-build-standalone) 풀어넣기.
2. `pip install --target site-packages` 로 의존 패키지 채워넣기.
3. `playwright install chromium` 으로 Chromium revision 받기 (cache 활용).
4. `shared/recording_shared/` 와 `shared/zero_touch_qa/` 를 폴더 루트로 카피.
5. templates 의 launcher / README 카피.
6. smoke (핵심 모듈 import) 1회 자체 검증.
7. (옵션 `-MakeZip`/`--make-zip`) 폴더 전체를 zip 으로 압축.

이 중 한 단계라도 빠지면 받는 사람 PC 에서 깨진다.

---

## 2. 현재 진단 — 차단점 7개 + 정리 부담 1개

### 2.1 동작 차단점 (받는 사람이 깨지는 원인) — 코드/자산 측

| # | 차단 지점 | 받는 사람 PC 에서 나타나는 증상 | 위치 |
|---|---|---|---|
| G1 | `recording_shared/`, `zero_touch_qa/` 디렉토리 누락 | Launch 후 uvicorn 기동 직후 `ModuleNotFoundError: No module named 'recording_shared'` 로 즉사 | [replay-ui/](../replay-ui/) (.gitignore 대상, pack-* 가 채워야 함) |
| G2 | `.pack-stamp` stale (자산이 최신 소스보다 뒤처짐) | UI 의 일부 카드 / 회귀 동작이 19개 commit 분량 옛날 코드대로 동작 | [replay-ui/.pack-stamp](../replay-ui/.pack-stamp) |
| G3 | `pack-windows.ps1:182` 에서 `$EmbeddedPy` 변수가 정의 전에 사용됨 | chromium revision 보정 step 이 조용히 no-op → 깨끗한 캐시에서 빌드 시 chromium 1217 미동봉 | [pack-windows.ps1:178-187](../replay-ui-portable-build/pack-windows.ps1#L178-L187) |
| G4 | 캐시 (`chromium/win64/chromium-1217/`) 디렉토리 없음 | G3 와 같은 결과 | `.replay-ui-cache/` (Phase 0 이후 명) |
| G5 | `build-out/*.zip` 자체가 한 번도 산출된 적 없음 | 반출할 zip 파일이 없음 | [replay-ui-portable-build/](../replay-ui-portable-build/) |
| **G6** | 시드·버전체크가 bare `playwright` CLI 를 부름 — 받는 사람 PATH 에 없으면 즉사 | UI 「+ 새 프로파일」 클릭 → `SeedSubprocessError` 또는 `ChipsNotSupportedError` 즉시. chromium 창 안 뜸 | [auth_profiles.py:734-740](../shared/zero_touch_qa/auth_profiles.py#L734-L740), [auth_profiles.py:1525-1528](../shared/zero_touch_qa/auth_profiles.py#L1525-L1528) |
| **G7** | macOS Gatekeeper — zip 내부 binary 의 quarantine 비트 | `Launch-ReplayUI.command` 자체는 우회해도, ▶ 실행 시점에 `python3` 와 `Chromium.app` 이 다시 "developer cannot be verified" 로 차단 | [Launch-ReplayUI.command](../replay-ui-portable-build/templates/Launch-ReplayUI.command) |

### 2.2 정리 부담 — 1회 설치형 흔적

| # | 부담 | 위치 |
|---|---|---|
| **C8** | 1회 설치형 산출물·문서·hook 의 자국이 22개 파일에 남아 *코드 일관성* 을 깨고, 휴대용 plan 을 따라가는 사람을 헷갈리게 한다. 사용자 결정: 모두 제거. | 아래 §3 정리 |

### 2.3 리뷰 반영 차단·정리 추가 (rev 4)

코드 대조로 확인한 사실 4건. G* 와 동등한 비중으로 다루되, "받는 사람의 첫 실행 즉시 실패" 까지는 아니고 *진단 가능성·재빌드 안정성·정책 일관성* 측면.

| # | 사항 | 증상 | 위치 |
|---|---|---|---|
| **R1** | Windows `Launch-ReplayUI.bat` 가 uvicorn 의 stderr / stdout 을 redirect 안 함. macOS 는 `nohup ... > stdout.log 2> stderr.log` 로 redirect 함 (대조) | UI 가 안 뜨거나 시드/실행이 실패해도 받는 사람·작업자가 *어디를 봐야 할지* 알 수 없음. `data/runs/replay-ui.stderr.log` 가 생성되지 않음. plan 의 "stderr 로그 확인" 절차 자체가 Windows 에선 동작 안 함 | [Launch-ReplayUI.bat:27](../replay-ui-portable-build/templates/Launch-ReplayUI.bat#L27) |
| **R2** | [README.txt:28-29](../replay-ui-portable-build/templates/README.txt#L28-L29) 가 `set AUTH_PROFILES_DIR=...` 로 공유 경로 설정 가능하다고 안내하지만, [Launch-ReplayUI.bat:8](../replay-ui-portable-build/templates/Launch-ReplayUI.bat#L8) 가 unconditional `set "AUTH_PROFILES_DIR=%ROOT%data\auth-profiles"` 로 *호출자 값을 덮어씀*. README 의 안내가 실제로는 작동 안 함 | 받는 사람이 README 대로 설정해도 효과 없음. 휴대용 모델의 *공식 정책* (= USB 안 데이터만) 과도 충돌. | [README.txt](../replay-ui-portable-build/templates/README.txt), [Launch-ReplayUI.bat:8](../replay-ui-portable-build/templates/Launch-ReplayUI.bat#L8) |
| **R3** | 캐시 빌더의 helper venv 생성 단계에서 `pip install playwright` 가 **unpinned**. wheels 디렉토리의 `playwright-1.59.0-*.whl` 은 결과적으로 핀돼 있지만, helper 의 chromium 다운로드는 helper venv 의 playwright 버전이 결정한다 | 미래 빌드 머신에서 build-cache.sh 재실행 시 PyPI 의 playwright 최신 버전이 잡혀 chromium revision 이 1217 에서 표류. wheels 의 1.59.0 과 helper 가 받은 chromium revision 의 mismatch → pack 의 G3 보정 step 으로도 못 메우는 회귀 가능 | [build-monitor-runtime.sh:117](../monitor-build/build-monitor-runtime.sh#L117) (Phase 0 후 → `build-cache.sh`), [playwright-allinone/requirements.txt](../requirements.txt) (저장소 루트 아님 — 리뷰 지적 4) |
| **R4** | A.1 의 `auth_profiles.py` 변경이 [test_auth_profiles.py:1366](../test/test_auth_profiles.py#L1366) 의 stub 과 *직접 충돌*. stub 이 `cmd[0] == "playwright"` 로 매칭하는데 변경 후 cmd[0] = `sys.executable` 이라 매칭 못 함 → 실 subprocess 호출 → 테스트 깨짐. 다른 stub (line 798, 812 의 `setattr`) 은 인자 검사 안 해 무관 | 변경 후 즉시 테스트 fail. A.1 의 코드 변경과 함께 stub 의 매칭 조건도 같이 보정해야 함 (의무) | [test_auth_profiles.py:1366](../test/test_auth_profiles.py#L1366) |

### 2.4 macOS 빌드 머신 의존성 (rev 4)

본 저장소는 git log 기준 단일 개발자 운영 (commit author 분포: Kyungsuk Lee 400+, 그 외 소수). CI workflow 는 [.github/workflows/monitor-runtime-build.yml](../../.github/workflows/monitor-runtime-build.yml) 1개이며 Ubuntu 만 회전한다 — **macOS arm64 빌드는 로컬 머신 의존**. 본 plan 의 Plan B 에서 이 workflow 자체가 삭제 대상.

본 plan 의 정책: macOS 빌드 머신 (M1/M2/M3 Mac) 접근 가능 여부에 따라 출시 완료 조건이 갈린다. §10 참고.

### 2.5 비차단으로 확인된 부분 — 손대지 말 것

- **Dify LLM 자가치유** — [executor.py:1473-1475](../shared/zero_touch_qa/executor.py#L1473-L1475) 에서 `DifyConnectionError` catch → local healing 으로 계속. 휴대용 모드에서 의도된 graceful degradation.
- **`replay_service.server` → child process 호출** — [server.py:430](../replay-ui/replay_service/server.py#L430), [orchestrator.py:146](../replay-ui/replay_service/orchestrator.py#L146) 모두 `sys.executable` 로 child 띄움. PATH 의존 없음.
- **env 4종 전파** — Launch-ReplayUI.{bat,command} 가 PYTHONPATH / PLAYWRIGHT_BROWSERS_PATH / MONITOR_HOME / AUTH_PROFILES_DIR 4개를 child process 까지 상속시킴.
- **exFAT 락 호환성** — portalocker 가 OS 커널 레벨 락 (Windows msvcrt.locking, macOS/Linux fcntl.flock) 이라 파일시스템과 무관. 실측 검증에서 OSError 가 안 떨어지면 그대로 두고, 떨어지면 Phase E (조건부).
- **pack-macos.sh** — G3 동일 버그 없음 (python 경로를 인라인으로 직접 씀).

---

## 3. 손댈 파일 정리

### 3.1 삭제하는 파일

| 파일 | 사유 |
|---|---|
| [playwright-allinone/monitor-build/install-monitor.cmd](../monitor-build/install-monitor.cmd) | 1회 설치형 installer (Windows) |
| [playwright-allinone/monitor-build/install-monitor.ps1](../monitor-build/install-monitor.ps1) | 1회 설치형 installer (Windows, ps1) |
| [playwright-allinone/monitor-build/install-monitor.sh](../monitor-build/install-monitor.sh) | 1회 설치형 installer (Mac/Linux) |
| [playwright-allinone/monitor-build/dscore.replay-ui.plist.template](../monitor-build/dscore.replay-ui.plist.template) | macOS LaunchAgent 템플릿 (1회 설치형 부속) |
| [playwright-allinone/replay-ui/run-replay-ui.sh](../replay-ui/run-replay-ui.sh) | 1회 설치형 launcher (휴대용은 Launch-ReplayUI.{bat,command} 가 진입점) |
| [playwright-allinone/monitor-build/](../monitor-build/) (폴더 자체) | Phase 0 후 비어 있음 |
| [.github/workflows/monitor-runtime-build.yml](../../.github/workflows/monitor-runtime-build.yml) | 1회 설치형 빌드 CI — 모델 자체가 사라지므로 CI 호출 대상 부재. 0.0 에서 git rm. |
| `C:/Users/csr68/.claude/projects/c--developer-airgap-test-toolchain/memory/project_windows_replay_ui_install_failure.md` | 1회 설치형 회귀 메모 — 모델 자체가 사라지므로 의미 없음 |

### 3.2 이동·개명하는 파일

| 원래 | 새 위치·이름 | 변경 사유 |
|---|---|---|
| [playwright-allinone/monitor-build/build-monitor-runtime.sh](../monitor-build/build-monitor-runtime.sh) | `playwright-allinone/replay-ui-portable-build/build-cache.sh` | 캐시 채움만 남기고 1회 설치형 zip 산출 부분 (line 228~277) 삭제. 이름·위치를 휴대용 영역으로. |

### 3.3 캐시 디렉토리 개명

| 원래 | 새 이름 |
|---|---|
| `.monitor-runtime-cache/` | `.replay-ui-cache/` |

내부 sub-dir 도 정리:
- `.monitor-runtime-cache/monitor-runtime-build-cache/` → `.replay-ui-cache/cache/`
- `.monitor-runtime-cache/monitor-runtime-build-<TS>/` 시리즈 (옛 임시 폴더) → 더 이상 생성 안 함 (build-cache.sh 가 cache 폴더만 씀)

기존 사용자 PC 의 `.monitor-runtime-cache/` 폴더는 자동 삭제하지 않고 그대로 둠 — `.gitignore` 만 갱신. 사용자가 디스크 공간이 필요하면 수동 정리.

### 3.4 코드·자산 변경 (rev 5 — 총 9건, Plan B 와 Plan A 가 섞여 있음)

> Plan B 안에서 처리: 9번째 행 (workflow 삭제). Plan A 안에서 처리: 1~5번째 (G3·G6·G7·R1·R2) + 7번째 일부 (build-cache 의 pip 호출 핀) + 8번째 (테스트 stub). 6번째 (build-cache.sh 신설) 와 7번째 (requirements 핀) 는 Plan B 의 일부. `pack-*` 의 캐시 경로 갱신 (1·2번째) 도 Plan B.

| 파일 | 변경 | 차단점 |
|---|---|---|
| [pack-windows.ps1](../replay-ui-portable-build/pack-windows.ps1) | `$EmbeddedPy` 정의를 109 줄로 이동. 캐시 경로 상수 `.monitor-runtime-cache` → `.replay-ui-cache` 갱신. sub-dir 상수 `monitor-runtime-build-cache` → `cache` 갱신. | G3 + 캐시 경로 일관성 |
| [pack-macos.sh](../replay-ui-portable-build/pack-macos.sh) | 캐시 경로 상수만 갱신 (코드 자체엔 G3 없음). | 캐시 경로 일관성 |
| [auth_profiles.py](../shared/zero_touch_qa/auth_profiles.py) | bare `playwright` CLI 호출 2곳을 `sys.executable -m playwright` 로. | G6 |
| [Launch-ReplayUI.command](../replay-ui-portable-build/templates/Launch-ReplayUI.command) | 시작부에 `xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true` 한 줄 추가. | G7 |
| [Launch-ReplayUI.bat](../replay-ui-portable-build/templates/Launch-ReplayUI.bat) | uvicorn 기동 라인을 `start /b cmd /c "...uvicorn... > %ROOT%data\runs\replay-ui.stdout.log 2> %ROOT%data\runs\replay-ui.stderr.log"` 패턴으로 변경. macOS .command 와 동등한 진단성 확보. | **R1** |
| [README.txt](../replay-ui-portable-build/templates/README.txt) (Windows + 별도 README-macos.txt 도) | "같은 PC 의 Recording UI / 설치본 Replay UI 와 로그인 프로파일을 공유하려면 ..." 단락 (라인 27~31) 삭제. 휴대용 모델의 데이터 정책은 *USB 안 `data/`* 가 유일. | **R2** |
| [build-cache.sh](../replay-ui-portable-build/build-cache.sh) (Phase 0 신설) + [playwright-allinone/requirements.txt](../requirements.txt) | helper venv 생성 시 `pip install "playwright==1.59.0"` 로 핀. `playwright-allinone/requirements.txt` (저장소 루트 아님) 의 `playwright>=1.51` 도 `playwright==1.59.0` 으로 핀. 미래 재빌드 안정성. | **R3** |
| [test_auth_profiles.py:1366](../test/test_auth_profiles.py#L1366) | stub 의 cmd 매칭 조건을 새 `sys.executable -m playwright` 형태와 옛 `playwright` 형태 양쪽 인식하도록 보정. | **R4** |
| [.github/workflows/monitor-runtime-build.yml](../../.github/workflows/monitor-runtime-build.yml) | 삭제 (`git rm`). 1회 설치형 빌드 CI 자체 제거. | 리뷰 지적 2 |

### 3.5 문서 갱신

| 파일 | 갱신 내용 |
|---|---|
| [/README.md](../../README.md) (root) | "오프라인 반출/복원 흐름" §3 의 1회 설치형 행 (2-A) 삭제, monitor-runtime / install-monitor 언급 모두 제거. 휴대용 (2-B) 만 남김. |
| [playwright-allinone/README.md](../README.md) | 동일 정리. |
| [playwright-allinone/docs/operations.md](operations.md) | 1회 설치형 운영 절차 부분 삭제. |
| [playwright-allinone/docs/reference.md](reference.md) | monitor-build 항목 (line 181~182) 삭제, build-cache.sh 항목 1줄 추가. |
| [playwright-allinone/docs/replay-ui-guide.md](replay-ui-guide.md) | §4 의 "두 모델 중 하나" 표 단순화 (휴대용만 남김). §4.1~§4.6 (1회 설치형 단계) 삭제. §4.7 → §4 로 흡수. §10·§11 의 "install-monitor 재설치 / legacy 마이그레이션" 언급 제거. |
| [playwright-allinone/docs/replay-ui-integration-tests.md](replay-ui-integration-tests.md) | T6 / T7 (1회 설치형 e2e) 삭제. |
| [/.githooks/README.md](../../.githooks/README.md) | monitor-runtime / install-monitor 언급 정리. |
| [/.githooks/pre-push](../../.githooks/pre-push) | `build-monitor-runtime.sh` 호출 자국이 있으면 갱신 (현재 직접 호출 없으나 stamp 산출 SHA 인풋의 `replay-ui-portable-build/templates` 는 그대로 유효). 동작은 안 바뀜. |
| [/.gitignore](../../.gitignore) | `.monitor-runtime-cache/` → `.replay-ui-cache/` 갱신. |
| [/export-airgap.sh](../../export-airgap.sh) | `--monitor-only` 옵션 (1회 설치형 zip) 삭제. `--recording-only` 와 default (휴대용 산출) 만 남김. monitor-runtime zip 산출 호출도 제거 — 휴대용 빌드는 `pack-windows.ps1` / `pack-macos.sh` 가 한다. |
| `C:/Users/csr68/.claude/projects/c--developer-airgap-test-toolchain/memory/MEMORY.md` | `[Windows monitor-runtime 1회 설치본 실패 사례 (2026-05-11)]` 항목 삭제. |

### 3.6 수정 안 하는 파일

`Launch-ReplayUI.bat`, `Stop-ReplayUI.bat`, `Stop-ReplayUI.command`, `README.txt`, `replay_service/*.py`, `monitor/*.py`, `recording-ui/` 일체. 그리고 `recording-ui/run-recording-ui.sh` 는 Recording UI 의 launcher 라 손대지 않음 (Recording UI 는 본 plan 의 범위 밖).

---

## 4. Phase 0 — 1회 설치형 흔적 제거 + 캐시 빌더 이동·개명 (= Plan B)

이 Phase 가 끝나야 Phase A~D 의 코드/빌드 변경이 *깨끗한 베이스* 위에서 진행된다. 순서가 중요하다 — 캐시 빌더 이동 (0.1) 보다 install-monitor 삭제 (0.2) 가 먼저 가면 build-monitor-runtime.sh 안의 install-monitor 카피 코드가 실패한다. 그리고 0.0 (CI workflow 삭제) 보다 0.3 (monitor-build/ 폴더 삭제) 이 먼저 가면 다음 push 의 CI 가 즉시 깨진다. 아래 순서 그대로.

### 0.0 CI workflow 삭제 (B-1 — 누락됐던 R-리뷰 지적 2)

**무엇을 한다**

[.github/workflows/monitor-runtime-build.yml](../../.github/workflows/monitor-runtime-build.yml) 은 `monitor-runtime-v*` 태그 push 또는 수동 트리거 시 `playwright-allinone/monitor-build/build-monitor-runtime.sh --target all` 을 호출해 `monitor-runtime-*.zip` 을 GitHub Release 에 첨부한다. 본 plan 에서 1회 설치형 모델을 제거하므로 *이 workflow 도 함께 제거* 한다. 그렇지 않으면 Plan B 의 다른 단계에서 `monitor-build/` 폴더가 사라진 직후 첫 태그 push 또는 수동 dispatch 시 CI 가 fail.

대체 — 휴대용 빌드는 [.github/workflows/](../../.github/workflows/) 에 새 workflow 를 *지금* 만들지 않는다 (별도 ticket). 이유: Windows 휴대용 빌드는 `pack-windows.ps1` 가 PowerShell 전용이라 `runs-on: windows-latest` 가 필요하고, macOS 휴대용 빌드는 `runs-on: macos-latest` 가 필요해 *기존 ubuntu-latest 단일 workflow 로 대체 불가*. 본 plan 의 범위는 로컬 빌드 머신 시나리오로 한정.

```bash
git rm .github/workflows/monitor-runtime-build.yml
```

**검증 0.0**

```bash
# 1. 파일 사라짐 확인
test ! -f .github/workflows/monitor-runtime-build.yml && echo OK

# 2. 다른 workflow 가 monitor-build/ 를 참조하지 않는지
grep -RIn --exclude-dir=docs "monitor-build\|monitor-runtime" .github/ 2>&1
# 출력 비어 있어야 함

# 3. 저장소 다른 자동화 hook 도 마찬가지
grep -RIn --exclude-dir=docs --exclude-dir=.git "monitor-build" . 2>&1 | head
# 출력에 .github/ 또는 메인 코드의 매칭 없음 (docs/ 의 본 plan 문서 매칭은 검색 대상 외)
```

### 0.1 `build-monitor-runtime.sh` 를 `build-cache.sh` 로 이동·정리

**무엇을 한다**

1. `git mv playwright-allinone/monitor-build/build-monitor-runtime.sh playwright-allinone/replay-ui-portable-build/build-cache.sh`

2. 새 파일 안에서 다음을 수정:
   - 헤더 코멘트 갱신 — "build-cache.sh — Replay UI 휴대용 빌드 캐시 (.replay-ui-cache/) 채움. pack-windows.ps1 / pack-macos.sh 가 이 캐시를 zip 안으로 옮긴다."
   - `BUILD_DIR_ROOT="${BUILD_DIR_ROOT:-$ROOT/.monitor-runtime-cache}"` → `BUILD_DIR_ROOT="${BUILD_DIR_ROOT:-$ROOT/.replay-ui-cache}"`
   - `BUILD_DIR="$BUILD_DIR_ROOT/monitor-runtime-build-cache"` → `BUILD_DIR="$BUILD_DIR_ROOT/cache"`
   - `BUILD_DIR="$BUILD_DIR_ROOT/monitor-runtime-build-$TS"` 줄과 그 위 `TS=...` 정의 삭제 (임시 폴더 모델 사라지고 캐시 폴더 단일 모델로).
   - `--no-package` 플래그를 default 동작으로 (=항상 캐시만 채움). 옵션 자체는 deprecated alias 로 받되 NO_PACKAGE=1 로 셋팅.
   - line 228~232 (install-monitor.{sh,ps1,cmd} + dscore.replay-ui.plist.template 카피) 삭제.
   - line 234~248 (README.txt 생성) 삭제.
   - line 256~277 (zip 산출) 삭제.
   - `head -25 "$0"` 의 25 가 헤더 코멘트 라인 수와 맞도록 갱신.
   - `--no-chromium`, zip 산출 관련 옵션·코드 제거 (휴대용 빌드는 항상 chromium 동봉).
   - **R3 — helper venv 의 playwright 버전 핀**: 현재 `pip install playwright` (unpinned) 라인을 `pip install "playwright==1.59.0"` 로 변경. 핀 버전은 `replay-ui-portable-build/playwright-version` 같은 단일 source-of-truth 파일에 적어두고 build-cache.sh / pack-windows.ps1 / pack-macos.sh 가 모두 그 파일을 읽도록 해도 좋다 (선택). 최소한 build-cache.sh 안에 명시 핀.

3. `git rm playwright-allinone/monitor-build/build-monitor-runtime.sh` (이미 mv 됐으면 자동).

**검증 0.1**

```bash
# 1. 파일 위치 확인
test -f playwright-allinone/replay-ui-portable-build/build-cache.sh && echo OK || echo FAIL
test -f playwright-allinone/monitor-build/build-monitor-runtime.sh && echo FAIL || echo OK

# 2. syntax check
bash -n playwright-allinone/replay-ui-portable-build/build-cache.sh
echo "exit=$?"

# 3. install-monitor 카피 / zip 산출 코드가 정말 사라졌는지
grep -E "install-monitor|monitor-runtime.*zip|dscore.replay-ui.plist" playwright-allinone/replay-ui-portable-build/build-cache.sh
# 출력 없어야 함 (exit 1)

# 4. 새 경로 상수 확인
grep -E "\.replay-ui-cache|/cache" playwright-allinone/replay-ui-portable-build/build-cache.sh | head -3
# .replay-ui-cache 와 cache sub-dir 둘 다 나와야 함
```

### 0.2 1회 설치형 installer 4개 + run-replay-ui.sh 삭제

**무엇을 한다**

```bash
git rm playwright-allinone/monitor-build/install-monitor.cmd
git rm playwright-allinone/monitor-build/install-monitor.ps1
git rm playwright-allinone/monitor-build/install-monitor.sh
git rm playwright-allinone/monitor-build/dscore.replay-ui.plist.template
git rm playwright-allinone/replay-ui/run-replay-ui.sh
```

**검증 0.2**

```bash
ls playwright-allinone/monitor-build/ 2>&1
# "No such file or directory" 또는 빈 출력
ls playwright-allinone/replay-ui/run-replay-ui.sh 2>&1
# "No such file or directory"

# 코드 기반에서 잔여 참조 없는지
grep -rln "install-monitor\|dscore.replay-ui.plist\|run-replay-ui\.sh" \
  --include="*.sh" --include="*.ps1" --include="*.cmd" --include="*.bat" \
  --include="*.py" \
  c:/developer/airgap-test-toolchain/ 2>&1 | grep -v "\.git/"
# 출력 비어 있어야 함 (.replay-ui-cache 의 옛 .monitor-runtime-cache 폴더 안 정도는 무시 — git 추적 안 됨)
```

매칭이 나오면 그 파일을 §3.5 문서 갱신 작업으로 처리.

### 0.3 `monitor-build/` 폴더 자체 삭제

0.1·0.2 후 폴더는 비어 있다. `git status` 가 그 폴더를 보여주지 않으면 이미 정리된 것. POSIX 빈 폴더는 git 추적 안 되니 별도 명령 없이 자연 정리됨. Windows 의 경우 빈 폴더가 남을 수 있으니 명시 삭제:

```powershell
Remove-Item -Recurse -Force playwright-allinone/monitor-build/ -ErrorAction SilentlyContinue
```

**검증 0.3**

```bash
test -d playwright-allinone/monitor-build/ && echo FAIL || echo OK
```

### 0.4 `pack-windows.ps1` / `pack-macos.sh` 의 캐시 경로 상수 갱신

**무엇을 한다**

[pack-windows.ps1:44-50](../replay-ui-portable-build/pack-windows.ps1#L44-L50):
```powershell
# 변경 전
$CacheRoot       = Join-Path $RepoRoot ".monitor-runtime-cache"
$CacheBuildDir   = Join-Path $CacheRoot "monitor-runtime-build-cache"
$WheelsDir       = Join-Path $CacheBuildDir "wheels\win64"
$ChromiumSrcDir  = Join-Path $CacheBuildDir "chromium\win64"
# 변경 후
$CacheRoot       = Join-Path $RepoRoot ".replay-ui-cache"
$CacheBuildDir   = Join-Path $CacheRoot "cache"
$WheelsDir       = Join-Path $CacheBuildDir "wheels\win64"
$ChromiumSrcDir  = Join-Path $CacheBuildDir "chromium\win64"
```

또한 [pack-windows.ps1:22-24](../replay-ui-portable-build/pack-windows.ps1#L22-L24) 의 헤더 주석에서 `monitor-build/build-monitor-runtime.sh --target win64 --no-package` 안내를 `replay-ui-portable-build/build-cache.sh --target win64` 로 갱신.

[pack-macos.sh:42-45](../replay-ui-portable-build/pack-macos.sh#L42-L45) 의 동등한 변경.

**검증 0.4**

```bash
grep -E "monitor-runtime|monitor-build" playwright-allinone/replay-ui-portable-build/pack-windows.ps1 playwright-allinone/replay-ui-portable-build/pack-macos.sh
# 출력 비어 있어야 함
```

### 0.5 `.gitignore` 갱신

[/.gitignore](../../.gitignore) 의 `.monitor-runtime-cache/` 줄을 `.replay-ui-cache/` 로 변경. 기존 `.monitor-runtime-cache/` 도 그대로 ignore 유지하려면 둘 다 적기 (사용자 PC 의 옛 폴더 보호용).

**검증 0.5**

```bash
grep -E "replay-ui-cache|monitor-runtime-cache" .gitignore
# 두 줄 모두 출력 OK
```

### 0.6 `.githooks/pre-push` 검토 + 필요 시 갱신

[/.githooks/pre-push](../../.githooks/pre-push) 의 `git rev-parse HEAD:playwright-allinone/...` 인풋에 `monitor-build/` 가 들어있는지 확인. 없으면 손댈 게 없음 (현재 코드 기준 — replay_service, monitor, shared, templates 만 보고 있음).

**검증 0.6**

```bash
grep -E "monitor-build|build-monitor-runtime" .githooks/pre-push
# 출력 비어 있어야 함
```

### 0.6b `requirements.txt` 의 playwright 버전 핀 (R3 — 경로 정정)

**무엇을 한다**

대상 파일은 *저장소 루트가 아니라* [playwright-allinone/requirements.txt](../requirements.txt). 그 안의 `playwright>=1.51` 줄을 `playwright==1.59.0` 로 정확히 핀. 휴대용 모델은 단일 버전만 지원하므로 range 가 의미 없음. wheels 디렉토리의 `playwright-1.59.0-*.whl` 과 일관.

**검증 0.6b**

```bash
grep -n "^playwright" playwright-allinone/requirements.txt
# 출력: 33:playwright==1.59.0   (라인 번호는 파일 상태에 따라 다를 수 있음 — 본 검증의 핵심은 우변)
```

`git diff playwright-allinone/requirements.txt` 가 *그 한 줄* 만 변경. 다른 라인 변경 없음.

### 0.7 `export-airgap.sh` 정리 + OS 분기 정책 명시 (리뷰 지적 6)

**무엇을 한다**

[/export-airgap.sh](../../export-airgap.sh) 안의 `--monitor-only` 옵션 + 그 분기 코드 삭제. 그 함수가 호출하던 `build-monitor-runtime.sh` 도 제거. 휴대용 빌드 자체는 `pack-windows.ps1` / `pack-macos.sh` 가 책임지므로 `export-airgap.sh` 가 그쪽 호출만 하도록.

**OS 분기 정책 (이 정책을 스크립트 헤더 코멘트와 README 양쪽에 박는다):**

`export-airgap.sh` 는 bash 스크립트이므로 호출하는 *빌드 머신의 OS* 에 따라 산출이 갈린다:

| 빌드 머신 | 호출 가능 자식 | 산출물 |
|---|---|---|
| **macOS (arm64)** | `pack-macos.sh` (bash) + `pack-windows.ps1` (pwsh 7 설치 필요 — 미설치면 skip) | macos-arm64 zip 필수. win64 zip 은 pwsh 있을 때만. |
| **Linux** | 둘 다 호출 불가 (Linux 용 pack 스크립트 없음 — 휴대용 빌드는 OS native 필수) | export-airgap 이 에러 메시지로 "Mac 또는 Windows 빌드 머신에서 실행하세요" 출력 후 exit 1. |
| **Windows (Git Bash / WSL2 Ubuntu)** | `pack-windows.ps1` (Git Bash 에서 `powershell.exe` 호출 가능). `pack-macos.sh` 는 macOS arm64 native API (python-build-standalone install_only tarball) 의존이라 호출 불가. | win64 zip 필수. macos-arm64 zip 은 산출 안 함 (별도 Mac 머신 필요). |

즉 한 머신에서 *양 OS zip 동시 산출* 은 macOS 빌드 머신에서만 가능. Windows 빌드 머신은 win64 zip 만 산출 (현재 본 plan 의 release 통과 조건은 §0.2b 의 "필수" 만 만족). Linux 는 휴대용 빌드 머신으로 사용 불가.

스크립트는 위 표대로 OS 감지 후 가능한 산출만 호출 (`uname -s` + `command -v powershell.exe`), 불가한 케이스는 명확한 에러 메시지로 종료.

또한 `--recording-only` 옵션은 그대로 두되 (= 녹화 PC 용 tarball — 휴대용 모델 정리와 무관), 헤더 사용법 코멘트의 1회 설치형 zip 산출 안내만 삭제.

**검증 0.7**

```bash
bash -n export-airgap.sh && echo "syntax OK"
grep -E "monitor-only|build-monitor-runtime|install-monitor" export-airgap.sh
# 출력 비어 있어야 함

# OS 분기 정책 안내가 스크립트 안에 박혀있는지 (정확한 문구는 작업 시 결정)
grep -E "Mac.*빌드|Windows.*빌드|macOS.*Linux|uname" export-airgap.sh
# 매칭 있음 (OS 감지 분기 자체)
```

### 0.8 문서 갱신 (§3.5 표대로)

10개 문서를 표 기준으로 일괄 갱신. 각각 grep 으로 잔여 자국 검출 → Edit 으로 제거.

문서별 세부:

- [/README.md](../../README.md) — line 117~152 의 "오프라인 반출/복원 흐름" 표 + 본문에서 2-A 행, monitor-runtime 설명 단락 3개 삭제. 단일 휴대용 모델로 단순화.
- [playwright-allinone/README.md](../README.md) — monitor-runtime / install-monitor 언급 줄 찾아 삭제.
- [docs/operations.md](operations.md) — line 294 의 "1회 설치형 일상 기동" 섹션, line 304 의 표 행 삭제.
- [docs/reference.md](reference.md) — line 181~182 의 monitor-build 항목 삭제, line 1줄 추가: `| replay-ui-portable-build/build-cache.sh | 예 | Replay UI 휴대용 빌드 캐시 채움 |`.
- [docs/replay-ui-guide.md](replay-ui-guide.md) — §4 표를 단일 모델로 단순화, §4.1~§4.6 통째 삭제, §4.7 의 본문을 §4 로 흡수 (제목·앵커 정리). §10 의 "Windows 에서 install-monitor.cmd 로 재설치" 행 삭제. §11.4 (legacy 마이그레이션) 삭제.
- [docs/replay-ui-integration-tests.md](replay-ui-integration-tests.md) — T6 / T7 두 항목 삭제, 번호 재정렬.
- [/.githooks/README.md](../../.githooks/README.md) — monitor-runtime / install-monitor 언급 찾아 정리.

**검증 0.8** (리뷰 지적 5 — 자기 참조 예외)

본 plan 문서 자체가 위 단어들을 *역사 설명* 으로 다수 포함하므로 검증 grep 에서 plan 문서를 *제외* 해야 한다. 또한 git history 의 commit 메시지는 검색 대상이 아니다 (현재 코드/문서 상태만 본다).

```bash
# 전체 매칭 0 이어야 함 (.replay-ui-cache/ 디렉토리 안 매칭은 git ignore 라 검색 대상 아님)
# 본 plan 문서 (PLAN_USB_REPLAY_UI_PORTABLE.md) 는 의도된 잔여 참조이므로 제외
grep -rln "install-monitor\|monitor-runtime\|dscore.replay-ui.plist\|run-replay-ui\.sh\|monitor-build/" \
  --include="*.md" --include="*.sh" --include="*.ps1" --include="*.cmd" --include="*.bat" \
  --exclude="PLAN_USB_REPLAY_UI_PORTABLE.md" \
  c:/developer/airgap-test-toolchain/
# 검색 결과 비어 있어야 함
```

매칭이 나오면 해당 파일을 마저 정리. 매칭 0 이 될 때까지 0.8 안에서 끝낸다.

(plan 문서가 매칭에 잡혔으면 `--exclude` 누락 — 본 명령 그대로 실행하면 자동으로 빠짐.)

### 0.9 메모리 정리

`C:/Users/csr68/.claude/projects/c--developer-airgap-test-toolchain/memory/project_windows_replay_ui_install_failure.md` 삭제. `MEMORY.md` 의 해당 줄 삭제.

**검증 0.9**

```bash
test -f "C:/Users/csr68/.claude/projects/c--developer-airgap-test-toolchain/memory/project_windows_replay_ui_install_failure.md" && echo FAIL || echo OK
grep "project_windows_replay_ui_install_failure\|monitor-runtime" "C:/Users/csr68/.claude/projects/c--developer-airgap-test-toolchain/memory/MEMORY.md"
# 출력 비어 있어야 함
```

### 0.10 Phase 0 전체 sanity — single commit 직전 마지막 점검

`git status` 가 깨끗하게 정리된 모양인지 확인. 한 commit 으로 묶기:

```bash
git add -A   # 단 자동 add 의 위험을 알므로 git status 로 사전 검토
git status   # 삭제·이동·수정 파일 목록 명확히 확인 — 의도하지 않은 파일 없으면 add
git commit -m "1회 설치형 monitor-runtime 모델 제거 + 캐시 빌더 이동·개명"
```

commit 메시지 본문엔 다음을 풀어 적기 (CLAUDE.md 규칙 — 비개발자 이해 가능 + 항목별):
- 휴대용 모델 하나로 운영 단순화
- 1회 설치형 설치 스크립트·플랫폼별 plist·자체 launcher 모두 삭제
- 캐시 빌더는 이름·위치만 휴대용 영역으로 이동 (기능 동일)
- 캐시 디렉토리 `.monitor-runtime-cache/` → `.replay-ui-cache/`
- 관련 문서 6개 + memory 1개 일괄 정리

**검증 0.10**

```bash
# 1. tree 상의 최종 모양 sanity
test -d playwright-allinone/monitor-build/ && echo "FAIL — monitor-build/ 남음"
test -f playwright-allinone/replay-ui-portable-build/build-cache.sh && echo OK
test -f playwright-allinone/replay-ui/run-replay-ui.sh && echo "FAIL"

# 2. 잔여 매칭 grep (본 plan 문서 자체는 의도된 잔여 — 제외)
grep -rln "install-monitor\|monitor-runtime\|monitor-build/\|dscore.replay-ui.plist\|run-replay-ui\.sh" \
  --include="*.md" --include="*.sh" --include="*.ps1" --include="*.cmd" --include="*.bat" --include="*.py" \
  --include="*.yml" --include="*.yaml" \
  --exclude="PLAN_USB_REPLAY_UI_PORTABLE.md" \
  c:/developer/airgap-test-toolchain/ | grep -v "\.git/" | grep -v "\.replay-ui-cache/\|\.monitor-runtime-cache/"
# 비어 있어야 함

# 3. 캐시 빌더 syntax check
bash -n playwright-allinone/replay-ui-portable-build/build-cache.sh

# 4. pack-windows.ps1 / pack-macos.sh 도 syntax check
$null = [scriptblock]::Create((Get-Content -Raw .\playwright-allinone\replay-ui-portable-build\pack-windows.ps1))
bash -n playwright-allinone/replay-ui-portable-build/pack-macos.sh
```

모두 통과해야 Phase A 로 넘어간다.

---

## 5. Phase A — 코드 측 수정 (rev 4 — 6건)

### A.0 변경 *전* 기존 테스트 sanity (R4)

코드 변경 *전* 의 베이스라인 통과 기록. 변경 후 같은 명령으로 다시 돌렸을 때 동등 결과여야 한다.

**무엇을 한다**

```bash
# 1. 대상 테스트 파일 위치 확인 — Phase 0 후 경로 기준
ls playwright-allinone/test/test_auth_profiles*.py 2>&1

# 2. 베이스라인 실행 (변경 전)
cd playwright-allinone
python -m pytest test/test_auth_profiles.py -x -q --no-header 2>&1 | tee /tmp/baseline-auth_profiles.log
```

**검증 A.0** — 베이스라인 로그의 마지막에 `passed` 표시 + fail 0. 실패가 있으면:
1. 그 fail 이 사전 회귀라는 점을 먼저 별도 ticket 으로 기록.
2. A.0 의 sanity 자체는 "변경 전후가 동등하다" 가 기준이므로, 미리 깨진 테스트가 있어도 A.1 이후 *같은 테스트가 같은 방식으로 깨져 있어야* 진행 가능. 새 fail 이 생기면 회귀.

### A.1 `auth_profiles.py` 의 playwright CLI 호출 두 곳 변경 + 테스트 stub 보정 (G6 + R4)

**무엇을 한다 — 코드 두 곳 변경**

[auth_profiles.py:734-740](../shared/zero_touch_qa/auth_profiles.py#L734-L740) (`current_playwright_version`):
```python
# 변경 전
result = subprocess.run(["playwright", "--version"], ...)
# 변경 후
result = subprocess.run([sys.executable, "-m", "playwright", "--version"], ...)
```

[auth_profiles.py:1525-1528](../shared/zero_touch_qa/auth_profiles.py#L1525-L1528) (`_do_seed_io`):
```python
# 변경 전
cmd = ["playwright", "open", seed_url, "--save-storage", str(storage_path)]
# 변경 후
cmd = [sys.executable, "-m", "playwright", "open", seed_url, "--save-storage", str(storage_path)]
```

**왜 이렇게 한다**

받는 사람 PC 의 PATH 에 `playwright` 가 있을 리 없다. `sys.executable -m playwright` 는 *현재 실행 중인 Python* 의 module 로 playwright 를 호출하므로 zip 안 embedded python 의 playwright 가 무조건 잡힌다.

**무엇을 한다 — 테스트 stub 보정 (R4, 의무 단계)**

[test_auth_profiles.py:1366](../test/test_auth_profiles.py#L1366) 의 `_seed_subprocess_writes` stub 은 `cmd[0] == "playwright"` 로 *직접 매칭* 한다. 위 코드 변경 후 cmd[0] 이 `sys.executable` 이 되므로 stub 은 더 이상 매칭 못 하고 real `subprocess.run` 으로 passthrough → 실제 playwright 가 호출돼 테스트 깨짐 (또는 사이트 접속 시도).

변경:
```python
# 변경 전 (line 1366)
if isinstance(cmd, list) and cmd and cmd[0] == "playwright":
# 변경 후
if isinstance(cmd, list) and (
    (cmd and cmd[0] == "playwright")
    or (len(cmd) >= 3 and cmd[1:3] == ["-m", "playwright"])
):
```

이중 분기인 이유 — 다른 테스트가 *변경 전 형태* 의 cmd 를 만들지 않는다는 보장이 없음 (예: 외부에서 `["playwright", ...]` 를 직접 주입하는 단위 테스트). 양쪽 형태를 모두 인식.

같은 파일 안에 *동일 패턴의 다른 stub* 이 있을 수 있음 — grep 으로 확인:
```bash
grep -n 'cmd\[0\] == "playwright"\|cmd and cmd\[0\] == .playwright.' playwright-allinone/test/
```
매칭 모두 위 형태로 일괄 보정.

**검증 A.1**

1. git diff 가 정확히 *세 군데* 만 바뀜 (auth_profiles.py 2곳 + test_auth_profiles.py 1곳, multi-line cmd 는 비례):
   ```bash
   git diff playwright-allinone/shared/zero_touch_qa/auth_profiles.py
   git diff playwright-allinone/test/test_auth_profiles.py
   ```
   다른 함수 / import / 공백 변경 없음.

2. import 가 깨지지 않는지:
   ```bash
   python -c "import ast, pathlib; ast.parse(pathlib.Path('playwright-allinone/shared/zero_touch_qa/auth_profiles.py').read_text(encoding='utf-8')); ast.parse(pathlib.Path('playwright-allinone/test/test_auth_profiles.py').read_text(encoding='utf-8')); print('parse OK')"
   ```
   `parse OK`. SyntaxError 면 변경 잘못 — 원위치.

3. **A.0 의 베이스라인과 동등** (R4):
   ```bash
   cd playwright-allinone
   python -m pytest test/test_auth_profiles.py -x -q --no-header 2>&1 | tee /tmp/post-A1-auth_profiles.log
   diff /tmp/baseline-auth_profiles.log /tmp/post-A1-auth_profiles.log
   ```
   *행 단위 동등* (PASS 개수·실패 케이스 동일). 새 fail 이 생기면 stub 보정이 미흡한 것 — 위 grep 으로 다른 stub 찾아 같이 보정.

검증 fail 시 A.2 로 넘어가지 않음.

### A.2 `Launch-ReplayUI.command` 에 quarantine 청소 한 줄 추가 (G7)

**무엇을 한다**

[Launch-ReplayUI.command:3](../replay-ui-portable-build/templates/Launch-ReplayUI.command#L3) (`ROOT="$(...)"` 직후) 에 추가:
```bash
# 첫 실행 시 macOS Gatekeeper quarantine 비트 제거 — zip 내부 python3 / chromium 이
# 시그니처 미검증으로 차단되는 회귀 차단. xattr 미설치 / 권한 없음은 silent skip.
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true
```

**검증 A.2**

```bash
# 1. git diff 한 줄 (+코멘트 2줄) 만 변경
git diff playwright-allinone/replay-ui-portable-build/templates/Launch-ReplayUI.command

# 2. bash syntax check
bash -n playwright-allinone/replay-ui-portable-build/templates/Launch-ReplayUI.command
echo "exit=$?"
```

실제 동작 검증은 Mac 머신에서 Phase C.2.

### A.3 `pack-windows.ps1` 의 `$EmbeddedPy` 정의 위치 이동 (G3)

**무엇을 한다**

[pack-windows.ps1:212](../replay-ui-portable-build/pack-windows.ps1#L212) 의 한 줄 `$EmbeddedPy = Join-Path $EmbeddedPyDir "python.exe"` 를 109 줄 (`$EmbeddedPyDir = Join-Path $ReplayUiDir "embedded-python"` 정의 직후) 로 이동. 원래 212 줄 위치의 중복 정의 삭제.

**검증 A.3**

```bash
# 1. git diff 가 한 줄 이동만 보임
git diff playwright-allinone/replay-ui-portable-build/pack-windows.ps1

# 2. PowerShell parse 체크
powershell -NoProfile -Command "$null = [scriptblock]::Create((Get-Content -Raw .\playwright-allinone\replay-ui-portable-build\pack-windows.ps1)); 'parse OK'"
```

실제 실행은 Phase B 에서.

### A.4 `Launch-ReplayUI.bat` 에 stderr/stdout redirect 추가 (R1)

**무엇을 한다**

[Launch-ReplayUI.bat:27](../replay-ui-portable-build/templates/Launch-ReplayUI.bat#L27) 의 uvicorn 기동 라인:

```bat
:: 변경 전
start "ReplayUI" /min "%ROOT%embedded-python\python.exe" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094
:: 변경 후
start "ReplayUI" /min cmd /c ""%ROOT%embedded-python\python.exe" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094 > "%ROOT%data\runs\replay-ui.stdout.log" 2> "%ROOT%data\runs\replay-ui.stderr.log""
```

핵심:
- `cmd /c` 로 한 번 감싸 child process 안에서 redirect 가 동작하게 함 (`start` 자체는 redirect 직접 안 됨).
- 따옴표 nesting 주의 — 외부 `"..."` 안에서 경로의 `"` 를 또 쓰는 패턴. `cmd /c "<command line>"` 패턴에서 안쪽 따옴표는 `cmd /c` 의 quote-aware 파싱이 알아서 처리.
- macOS 의 [Launch-ReplayUI.command:23-24](../replay-ui-portable-build/templates/Launch-ReplayUI.command#L23-L24) 와 의미·결과 동등.

**왜 이렇게 한다**

`%ROOT%data\runs\replay-ui.stderr.log` 가 *반드시* 생성돼야 plan 의 모든 "stderr 로그 확인" 절차 (B.4 실패 진단, B.5 의 SeedSubprocessError 검색, etc.) 가 실제로 동작한다. 현재는 그 파일이 안 만들어져서 진단 자체 불가능.

**검증 A.4**

1. git diff 한 줄 변경 (혹은 컨티뉴 라인 포함 2~3줄):
   ```bash
   git diff playwright-allinone/replay-ui-portable-build/templates/Launch-ReplayUI.bat
   ```

2. 실제 동작 검증은 Phase B.4 에서 한꺼번에 (`Test-Path "$Tmp\replay-ui\data\runs\replay-ui.stderr.log"` 가 True 여야 함).

### A.5 `README.txt` 두 개의 AUTH_PROFILES_DIR 공유 안내 삭제 (R2)

**무엇을 한다**

[playwright-allinone/replay-ui-portable-build/templates/README.txt](../replay-ui-portable-build/templates/README.txt) 의 라인 27~31:

```text
같은 PC 의 Recording UI / 설치본 Replay UI 와 로그인 프로파일을 공유하려면,
Launch-ReplayUI.bat 호출 전에 명령창에서:
    set AUTH_PROFILES_DIR=%USERPROFILE%\ttc-allinone-data\auth-profiles

그 다음 Launch-ReplayUI.bat 을 실행하세요 (2026-05-13 부터 두 UI 의 기본 공유 경로).
```

→ 전체 단락 삭제. 이유 — Launch-ReplayUI.bat:8 이 호출자의 값을 무조건 덮어쓰므로 안내가 실제로는 작동 안 함. 또한 휴대용 모델의 *공식 정책* 은 **USB 안 `data/` 가 유일한 데이터 위치** (다른 폴더와 공유하지 않음).

[README-macos.txt](../replay-ui-portable-build/templates/README-macos.txt) 에 같은 단락이 있으면 동일 삭제.

**왜 이렇게 한다**

위 R2 분석 그대로. 추가로, 1회 설치형 ("설치본 Replay UI") 자체가 Phase 0 에서 사라지므로 그 단락의 전제 자체가 무너짐.

**검증 A.5**

```bash
grep -n "AUTH_PROFILES_DIR" playwright-allinone/replay-ui-portable-build/templates/README*.txt
# 출력 비어 있어야 함 (= 안내 모두 삭제)

grep -n "공유\|share\|설치본" playwright-allinone/replay-ui-portable-build/templates/README*.txt
# 출력 비어 있어야 함
```

### A.6 commit 1건 (rev 5 — 6 변경 묶음)

Phase A 의 여섯 변경 (A.1 코드 + A.1 테스트 stub + A.2 + A.3 + A.4 + A.5) 을 한 commit 으로:
```bash
git add playwright-allinone/shared/zero_touch_qa/auth_profiles.py \
        playwright-allinone/test/test_auth_profiles.py \
        playwright-allinone/replay-ui-portable-build/templates/Launch-ReplayUI.command \
        playwright-allinone/replay-ui-portable-build/templates/Launch-ReplayUI.bat \
        playwright-allinone/replay-ui-portable-build/templates/README.txt \
        playwright-allinone/replay-ui-portable-build/templates/README-macos.txt \
        playwright-allinone/replay-ui-portable-build/pack-windows.ps1
git status   # 위 7파일 외에 의도하지 않은 변경 없는지 점검
git commit -m "휴대용 Replay UI — 받는 PC 에서 끝까지 동작하도록 차단·진단 5 건 해소"
```

commit 본문 (CLAUDE.md 규칙 — 비개발자 이해 가능 + 항목별):
- 로그인 프로파일 등록 시 시스템 PATH 의 `playwright` 가 없으면 즉사하던 회귀 해소 (G6 — auth_profiles.py 의 두 곳)
- 위 변경에 맞춰 기존 단위 테스트 stub 의 인자 매칭 조건을 새 형태 / 옛 형태 양쪽 인식하도록 보정 (R4 — test_auth_profiles.py)
- macOS 에서 첫 실행 후 시나리오 실행 시점에 Gatekeeper 가 zip 내부 chromium 을 다시 차단하던 회귀 해소 (G7 — Launch-ReplayUI.command)
- Windows 휴대용 빌드 스크립트의 embedded-python 경로 변수가 chromium revision 보정 단계에서 사용 전에 정의되어 있지 않던 회귀 해소 (G3 — pack-windows.ps1)
- Windows 진입 스크립트가 서버 stderr 를 파일로 남기지 않아 문제 발생 시 진단 불가능하던 회귀 해소 (R1 — Launch-ReplayUI.bat)
- 받는 사람용 안내 문구의 공유 경로 설정 항목이 실제로는 동작하지 않던 점 제거 + 휴대용 데이터 정책을 USB 안 폴더로 단일화 (R2 — README.txt / README-macos.txt)

---

## 6. Phase B — Windows 빌드 + 검증 (현재 PC)

### B.1 chromium 1217 캐시 보강 (G4)

**무엇을 한다**

Git Bash 에서 (Phase 0 후 새 위치):
```bash
bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target win64
```

이 스크립트가 한 번도 안 돌았으면 약 5~10분. helper venv 만들고 `playwright install chromium` 으로 캐시에 받음.

**왜 이렇게 한다**

캐시가 끝나야 [.replay-ui-cache/cache/wheels/win64/](../../.replay-ui-cache/cache/wheels/win64/) 에 pip wheels, [.replay-ui-cache/cache/chromium/win64/](../../.replay-ui-cache/cache/chromium/win64/) 에 Chromium 이 들어간다.

**검증 B.1**

```bash
ls .replay-ui-cache/cache/chromium/win64/
```

다음 디렉토리 모두 보여야 함:
- `chromium-1217` ← 핵심
- `chromium_headless_shell-1217`
- `ffmpeg-1011`
- `winldd-1007`

`chromium-1217` 가 없으면 → helper venv 의 playwright 가 wheels 의 1.59.0 과 다른 버전.

1. helper venv 위치 확인 (build-cache.sh 안에서 어떻게 만드는지 코드 보고):
   ```bash
   ls .replay-ui-cache/cache/.helper-venv/ 2>/dev/null || find .replay-ui-cache/ -name "playwright" -path "*/Scripts/*" -o -name "playwright" -path "*/bin/*"
   ```
2. 그 venv 의 playwright 버전 확인:
   ```bash
   <helper venv 경로>/Scripts/python.exe -m pip show playwright | grep Version    # Windows
   <helper venv 경로>/bin/python -m pip show playwright | grep Version             # POSIX
   ```
3. 1.59.0 이 아니면, helper venv 를 1.59.0 으로 핀:
   ```bash
   <helper venv 경로>/Scripts/python.exe -m pip install --upgrade playwright==1.59.0
   <helper venv 경로>/Scripts/python.exe -m playwright install chromium
   ```
4. `chromium-1217` 디렉토리 생성 재확인.

검증 통과 못 하면 B.2 로 넘어가지 않음.

### B.2 pack-windows.ps1 실행 (zip 아직 안 만들고 자산만 채움)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File playwright-allinone/replay-ui-portable-build/pack-windows.ps1 `
  -ReuseCache
```

**검증 B.2** — 다음 6개 *모두* 통과:

1. `Test-Path playwright-allinone/replay-ui/recording_shared/trace_parser.py` → True
2. `Test-Path playwright-allinone/replay-ui/zero_touch_qa/auth_profiles.py` → True
3. 카피된 `auth_profiles.py` 에 A.1 변경 반영:
   ```bash
   grep -n "sys.executable.*-m.*playwright" playwright-allinone/replay-ui/zero_touch_qa/auth_profiles.py
   ```
   *최소 2개* 라인 출력.
4. `ls playwright-allinone/replay-ui/chromium/` 에 `chromium-1217` 디렉토리.
5. `.pack-stamp` 가 현재 HEAD source SHA 와 일치:
   ```bash
   cat playwright-allinone/replay-ui/.pack-stamp
   git rev-parse \
     HEAD:playwright-allinone/shared \
     HEAD:playwright-allinone/replay-ui/replay_service \
     HEAD:playwright-allinone/replay-ui/monitor \
     HEAD:playwright-allinone/replay-ui-portable-build/templates \
     | tr -d '\n' | sha256sum
   ```
   두 SHA 일치.
6. pack 스크립트 출력 마지막 부분에 `Smoke OK`.

하나라도 fail → B.3 안 함. 원인 파악 후 재실행.

### B.3 zip 산출 (G5)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File playwright-allinone/replay-ui-portable-build/pack-windows.ps1 `
  -ReuseCache -MakeZip
```

**검증 B.3**

1. `ls -la playwright-allinone/replay-ui-portable-build/build-out/` 에 `DSCORE-ReplayUI-portable-win64-<ts>.zip`.
2. 크기 200~300MB:
   ```bash
   stat -c '%s %n' playwright-allinone/replay-ui-portable-build/build-out/*.zip
   ```
3. 출력에 `sha256:` 줄.

### B.4 임시 폴더에서 더블클릭 (UI 가 뜨는지)

```powershell
$Tmp = New-Item -ItemType Directory -Path "$env:TEMP\replayui-test-$(Get-Random)"
Expand-Archive `
  -Path (Get-ChildItem playwright-allinone/replay-ui-portable-build/build-out/*.zip | Select-Object -First 1).FullName `
  -DestinationPath $Tmp.FullName
Start-Process -FilePath "$($Tmp.FullName)\replay-ui\Launch-ReplayUI.bat"
```

**검증 B.4**

1. 15초 이내 HTTP 응답:
   ```powershell
   (Invoke-WebRequest http://127.0.0.1:18094/ -UseBasicParsing -TimeoutSec 15).StatusCode
   ```
   200. timeout → `$Tmp\replay-ui\data\runs\replay-ui.stderr.log` 확인.

2. 브라우저 자동 오픈 + 4개 카드 표시.

3. embedded python 이 임시 폴더 안에서 떠 있음:
   ```powershell
   Get-Process | Where-Object { $_.Path -like "*$($Tmp.FullName)*" } | Select-Object Id, Path
   ```
   `embedded-python\python.exe` 가 임시 폴더 경로로 떠 있음.

4. 홈 폴더 오염 없음:
   ```powershell
   Test-Path $env:USERPROFILE\.dscore.ttc.monitor
   ```
   False (또는 B.4 전후 mtime 변화 없음).

### B.5 시드 — G6 픽스 실증

UI 의 「+ 새 프로파일」 → 이름 `smoke-test`, URL `https://example.com/` → `✓ 로그인 시작`.

**검증 B.5**

1. chromium 창 자동 오픈:
   ```powershell
   Get-Process chrome*, chromium* | Select-Object Id, Path
   ```
   Path 가 `$Tmp\replay-ui\chromium\chromium-1217\...`.

2. example.com 페이지 로드 완료 (사람 눈).

3. 창을 사람이 닫음 → 카드에 `smoke-test = 등록됨`.

4. storage 파일:
   ```powershell
   Test-Path "$($Tmp.FullName)\replay-ui\data\auth-profiles\smoke-test.storage.json"
   ```
   True.

5. stderr 에러 없음:
   ```powershell
   Get-Content "$($Tmp.FullName)\replay-ui\data\runs\replay-ui.stderr.log" | Select-String -Pattern "SeedSubprocessError|ChipsNotSupportedError|FileNotFoundError|playwright.*not found"
   ```
   결과 비어 있음.

fail → A.1 변경이 실제 저장됐는지 + B.2 검증 3 가 통과했는지 재확인.

### B.6 시나리오 .py 실행 (▶ 실행)

테스트 .py 작성:
```python
# C:\Users\<you>\Desktop\smoke.py
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://example.com/")
    assert "Example" in page.title()
    browser.close()
```

UI 의 `📄 시나리오 스크립트` 카드 → `⬆ 업로드 (.py)` → 위 파일 → 프로파일 *비로그인* → 저장 → `[▶ 실행]`.

**검증 B.6**

1. 실시간 로그 흐름.
2. 결과 카드 `✓ PASS`.
3. 결과 폴더:
   ```powershell
   Get-ChildItem "$($Tmp.FullName)\replay-ui\data\runs\" -Recurse | Select-Object FullName
   ```
   최근 `<runId>/` 안에 `run_log.jsonl`, `trace.zip`, `screenshots/`, `exit_code` (내용 `0`), `meta.json`.

### B.7 결과 상세 + HTML 리포트

`[상세→]` → 좌 스텝 / 우 스크린샷. `[📥 HTML 리포트]` 다운로드.

**검증 B.7**

1. 좌측 스텝 클릭마다 우측 스크린샷 변경.
2. 다운로드 HTML 을 인터넷 차단 환경에서 열어도 인라인 스크린샷 그대로:
   ```powershell
   # 인터넷 차단 시뮬레이션 — 다운로드 폴더의 HTML 더블클릭
   Start-Process (Get-ChildItem $env:USERPROFILE\Downloads\report*.html | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
   ```

### B.8 재시작 시 데이터 유지

```powershell
Start-Process -FilePath "$($Tmp.FullName)\replay-ui\Stop-ReplayUI.bat" -Wait
Start-Sleep 2
Start-Process -FilePath "$($Tmp.FullName)\replay-ui\Launch-ReplayUI.bat"
```

**검증 B.8**

1. `smoke-test` 카드 그대로.
2. 결과 행 그대로.

---

## 7. Phase C — macOS 측 빌드 + 검증 (별도 Mac 빌드 머신)

### C.1 Mac 빌드 머신에서 캐시 + pack

```bash
cd <repo>
bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target macos-arm64
bash playwright-allinone/replay-ui-portable-build/pack-macos.sh --reuse-cache --make-zip
```

**검증 C.1**

1. `ls playwright-allinone/replay-ui-portable-build/build-out/` 에 `DSCORE-ReplayUI-portable-macos-arm64-<ts>.zip` (200~300MB).
2. pack-macos.sh 출력에 `Smoke OK`.
3. `.pack-stamp` 가 Windows 측과 같은 HEAD SHA.
4. `Launch-ReplayUI.command` 에 A.2 줄 들어있음:
   ```bash
   grep "xattr -dr com.apple.quarantine" playwright-allinone/replay-ui/Launch-ReplayUI.command
   ```
   매칭 1줄.

### C.2 Mac 임시 폴더 더블클릭 (G7 픽스 실증)

```bash
mkdir -p /tmp/replayui-mac-test
unzip -q playwright-allinone/replay-ui-portable-build/build-out/DSCORE-ReplayUI-portable-macos-arm64-*.zip -d /tmp/replayui-mac-test/
# Finder 에서 Launch-ReplayUI.command 우클릭 → 열기 (Gatekeeper 1회 우회)
open /tmp/replayui-mac-test/replay-ui/Launch-ReplayUI.command
```

**검증 C.2**

1. 15초 이내:
   ```bash
   for i in $(seq 1 15); do curl -sf http://127.0.0.1:18094/ -o /dev/null && echo OK && break; sleep 1; done
   ```
   OK.

2. 4개 카드 표시.

3. 「+ 새 프로파일」 → example.com → chromium 창 오픈 시 Gatekeeper 추가 차단 *없음*. 차단 떨어지면 → A.2 변경이 launcher 에 카피되지 않은 것.

4. ▶ 실행 → PASS.

5. 홈 폴더 미오염:
   ```bash
   test -d "$HOME/.dscore.ttc.monitor" && echo "FAIL (회귀)" || echo "OK"
   test -d "$HOME/ttc-allinone-data" && echo "FAIL (회귀)" || echo "OK"
   ```

---

## 8. Phase D — 같은 USB 한 개로 양 OS 운용

### D.1 USB 디렉토리 구조

```
<USB 루트>/
  ├── ReplayUI-Windows/
  │   └── replay-ui/
  │       ├── Launch-ReplayUI.bat
  │       └── ...
  └── ReplayUI-macOS/
      └── replay-ui/
          ├── Launch-ReplayUI.command
          └── ...
```

폴더 분리 이유 — 두 OS 의 자산이 같은 이름 (`Launch-*`, `data/`) 이라 합치면 충돌. 물리적 분리.

### D.2 Windows PC 실행

```cmd
E:\ReplayUI-Windows\replay-ui\Launch-ReplayUI.bat
```

**검증 D.2** — B.4·B.5·B.6 검증 항목 동일. 데이터: `E:\ReplayUI-Windows\replay-ui\data\`. exFAT 확인:
```powershell
Get-Volume -DriveLetter E | Select-Object FileSystem
```
`exFAT`.

### D.3 macOS PC 실행

```bash
open /Volumes/<USB 이름>/ReplayUI-macOS/replay-ui/Launch-ReplayUI.command
```

**검증 D.3** — C.2 검증 항목 동일. 데이터: `/Volumes/<USB>/ReplayUI-macOS/replay-ui/data/`.

### D.4 USB 이동성 검증

D.2 후 USB 안전 제거 → *다른* Windows PC 에 꽂고 같은 .bat.

**검증 D.4**

1. `smoke-test` 프로파일 그대로 카드 표시.
2. 결과 행 그대로.
3. 새 PC 홈에 새 폴더 생성 없음:
   ```powershell
   $before = Get-ChildItem $env:USERPROFILE | Select-Object Name
   # 실행 후
   $after = Get-ChildItem $env:USERPROFILE | Select-Object Name
   Compare-Object $before $after
   ```
   비어 있음.

---

## 9. Phase E — 조건부 (특정 검증 실패 시에만)

### E.1 exFAT 위 portalocker OSError

**언제 발동** — Phase B.5 / D.4 stderr 에서 `_index.lock` 관련 OSError.

**무엇을 한다** — [auth_profiles.py:_index_lock](../shared/zero_touch_qa/auth_profiles.py#L167) 의 portalocker 호출을 try/except OSError 로 감싸고, 실패 시 in-process threading.Lock 만으로 fallback. stderr 에 1회 warning.

**검증 E.1** — B.5/D.4 재실행, OSError 없음 + 시드 정상.

### E.2 chromium 1217 캐시 미진입

**언제 발동** — Phase B.1 에서 1217 안 받힘.

**무엇을 한다** — B.1 의 fallback 절차 (helper venv 의 playwright 를 1.59.0 으로 핀, 재실행).

**검증 E.2** — B.1 재확인, 1217 디렉토리 생성.

---

## 10. 최종 산출물 체크리스트 (rev 5)

### 10.1 commit (총 2건)

- [ ] Phase 0 commit — "1회 설치형 monitor-runtime 모델 제거 + 캐시 빌더 이동·개명"
- [ ] Phase A commit — "휴대용 Replay UI — 받는 PC 에서 끝까지 동작하도록 차단·진단 5 건 해소"

### 10.2 빌드 산출물

- [ ] `playwright-allinone/replay-ui-portable-build/build-out/DSCORE-ReplayUI-portable-win64-<ts>.zip` (200~300MB, sha256 기록)
- [ ] **(조건부)** `playwright-allinone/replay-ui-portable-build/build-out/DSCORE-ReplayUI-portable-macos-arm64-<ts>.zip` — Mac 빌드 머신 접근 가능 시. 접근 불가 시 §10.5 의 분기 정책 참조.

### 10.3 검증 통과 (필수)

- [ ] Phase 0.10 — 1회 설치형 잔여 매칭 0
- [ ] A.0 베이스라인과 A.1 후 pytest 결과 동등 (R4)
- [ ] Phase B.1~B.8 — Windows 측 끝까지 (= 시드 + 실행 + 결과 + HTML + 재시작 후 유지)
- [ ] Phase D.4 — USB 이동성 (별도 Windows PC 1대 확보 시. 단일 PC 만 있으면 임시 폴더 두 위치로 sanity)

### 10.4 검증 통과 (macOS — 조건부)

- [ ] Phase C.1 — pack-macos.sh 산출 + smoke
- [ ] Phase C.2 — Mac 임시 폴더 시드·실행·결과
- [ ] Phase D.3 — Mac 에서 USB 폴더 실행

위 3개는 Mac 빌드 머신 접근 가능 시에만 진행. 불가 시 §10.5.

### 10.5 macOS 빌드 머신 접근 불가 시의 분기 정책 (R5)

본 저장소는 단일 개발자 운영이고, CI 는 Ubuntu 만 있다. macOS arm64 빌드 머신이 손에 없는 시점에 출시가 막히면 안 되므로 다음 정책을 따른다:

- **Windows-only 출시** — Phase B + Phase D.4 통과만으로 `DSCORE-ReplayUI-portable-win64-<ts>.zip` 1개 출시. macOS 산출은 별도 ticket 으로 분리. README / docs/replay-ui-guide.md 에 *현재 지원 OS = Windows* 만 명시.
- **분리된 macOS ticket** — Mac 머신 확보 시 별도 ticket 으로 Phase C / D.3 진행. 이때 본 plan 의 Phase A 5건은 *이미 main 에 반영* 됐으므로 별도 코드 변경 없음. `bash build-cache.sh --target macos-arm64 && bash pack-macos.sh --reuse-cache --make-zip` 한 사이클 + Phase C.2 검증.
- **완료 기준 (Windows-only 출시 모드)** — §10.1 commit 2건 + §10.2 의 Windows zip + §10.3 의 모든 항목.
- **완료 기준 (양 OS 출시 모드)** — 위 + §10.2 의 macOS zip + §10.4 의 모든 항목.

### 10.6 정리된 파일 / 디렉토리

- [ ] `.github/workflows/monitor-runtime-build.yml` 삭제됨 (리뷰 지적 2)
- [ ] `playwright-allinone/monitor-build/` 폴더 삭제됨
- [ ] `install-monitor.{cmd,ps1,sh}`, `dscore.replay-ui.plist.template`, `run-replay-ui.sh` 삭제됨
- [ ] `replay-ui-portable-build/build-cache.sh` 신설 (혹은 이동 완료) + helper venv 의 playwright 핀 (R3)
- [ ] `playwright-allinone/requirements.txt` 의 playwright 핀 (R3)
- [ ] `.gitignore` 의 캐시 경로 갱신
- [ ] `Launch-ReplayUI.bat` 의 stderr/stdout redirect (R1)
- [ ] `README.txt` / `README-macos.txt` 의 AUTH_PROFILES_DIR 공유 안내 삭제 (R2)
- [ ] `test_auth_profiles.py:1366` 의 stub 매칭 보정 (R4)
- [ ] 문서 7개 + memory 1개 갱신·삭제 완료
- [ ] `export-airgap.sh` OS 분기 정책 명시 (리뷰 지적 6)

---

## 11. 부록

### 11.1 비범위

- `recording-ui/run-recording-ui.sh` — Recording UI 의 launcher. 본 plan 의 범위 밖.
- `code-AI-quality-allinone/` — 별도 패키지. 무관.
- Linux 휴대용 빌드 — pack-linux.sh 미구현. OS 가정 (Windows + macOS arm64) 밖.
- LLM 자가치유 (Dify 의존) — 휴대용 모드 graceful degradation 으로 충분.
- 캐시 디렉토리 마이그레이션 — 사용자 PC 의 옛 `.monitor-runtime-cache/` 폴더는 자동 삭제하지 않음. `.gitignore` 만 갱신해 git 추적 안 됨을 보장. 사용자가 디스크 공간 필요 시 수동 삭제.

### 11.2 자주 헷갈리는 점

**Q. 받는 사람도 빌드 머신 같은 셋업이 필요한가?**
A. 아니. 받는 사람은 zip 풀고 더블클릭만. 빌드 머신만 인터넷 + 캐시 + pack 도구.

**Q. Recording UI 는?**
A. 다른 PC (녹화 PC) 에서 돈다. 본 plan 의 USB 휴대용은 모니터링 PC 측 Replay UI 만 다룬다. 녹화는 본 plan 범위 밖.

**Q. Dify / Jenkins / DB 같은 컨테이너 스택?**
A. 휴대용 Replay UI 와 무관. 컨테이너 스택은 녹화 PC 에서 돈다. 모니터링 PC 는 가벼운 데몬 1개 (Replay UI) 만.

**Q. 시드한 로그인 상태의 보안?**
A. zip 풀린 폴더 안 `data/auth-profiles/<name>.storage.json` 에 쿠키 + localStorage 저장. 평문 ID/PW 는 어디에도 저장 안 됨. USB 자체가 분실 위협이라 BitLocker / FileVault 같은 USB 암호화는 별도 고려해야 하나 본 plan 범위 밖.

### 11.3 작업 중 막혔을 때 보는 순서

1. 현재 task 의 검증 항목을 *순서대로* 다시 본다. 어느 검증이 fail 했는지 정확히 짚는다.
2. fail 한 검증의 "검증 실패 시" 안내를 따른다.
3. 안내가 없거나 막히면 → §2 의 차단점 표 (G* / C8) 로 돌아가 해당 원인 / 위치를 다시 본다.
4. 그래도 안 되면 stderr 로그 (`<폴더>/replay-ui/data/runs/replay-ui.stderr.log`) 의 traceback 첫 줄을 검색.
5. 절대 *건너뛰고* 다음 task 로 넘어가지 않는다.

### 11.4 핵심 파일 빠른 링크 (Phase 0 이후 상태 기준)

- 빌드 도구: [build-cache.sh](../replay-ui-portable-build/build-cache.sh) (Phase 0 으로 신설) · [pack-windows.ps1](../replay-ui-portable-build/pack-windows.ps1) · [pack-macos.sh](../replay-ui-portable-build/pack-macos.sh)
- 진입점: [Launch-ReplayUI.bat](../replay-ui-portable-build/templates/Launch-ReplayUI.bat) · [Launch-ReplayUI.command](../replay-ui-portable-build/templates/Launch-ReplayUI.command)
- 핵심 코드 변경 대상: [auth_profiles.py](../shared/zero_touch_qa/auth_profiles.py)
- 사용자 가이드 (Phase 0 갱신 대상): [replay-ui-guide.md](replay-ui-guide.md)
- 자동 갱신 hook: [pre-push](../../.githooks/pre-push) (본 plan 의 범위 밖이지만 개발 흐름 이해용)
