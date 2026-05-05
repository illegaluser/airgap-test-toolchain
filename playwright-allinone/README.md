# playwright-allinone — 문서 입구

**Zero-Touch QA All-in-One** 은 테스트 자동화에 필요한 Jenkins / Dify / DB 를 한 docker 컨테이너로 올리고, Ollama 와 실제 브라우저는 사용자 PC 에서 처리하는 배포본이다.

> **이 문서가 처음이면** — [playwright-allinone_QUICKSTART.md](playwright-allinone_QUICKSTART.md) 한 페이지만 따라가면 빌드 → 컨테이너 기동 → 첫 녹화·재생까지 한 번 성공할 수 있다.

## 시스템 한눈에

```text
┌─────────────────────────── docker 컨테이너 ───────────────────────────┐
│  Jenkins (18080)   Dify (18081)   PostgreSQL · Redis · Qdrant · nginx │
└────────────────────────────────────────────────────────────────────────┘
                                  ↕ HTTP
┌─────────────────────────────── 호스트 PC ────────────────────────────┐
│  Ollama        Jenkins agent       Recording UI (18092)               │
│  (LLM 추론)    (실제 Chromium)     (녹화·재생 GUI 서비스)             │
└────────────────────────────────────────────────────────────────────────┘
```

| 위치 | 무엇이 도는가 | 왜 |
| --- | --- | --- |
| 컨테이너 | Jenkins · Dify · DB · nginx | 서버 요소를 한 번에 |
| 호스트 | Ollama · Jenkins agent · Playwright Chromium · Recording UI | GPU + 실제 데스크탑 브라우저 창이 필요 |

## 두 가지 사용 경로

| 경로 | 어떨 때 | 입구 |
| --- | --- | --- |
| **Jenkins Pipeline** | 자연어 요구사항 → LLM 이 시나리오 작성 → 자동 실행 | <http://localhost:18080> (admin/password) |
| **Recording UI** | 사용자가 직접 브라우저 조작을 녹화 → DSL 시나리오 → 재생·치유 | <http://localhost:18092> |

두 경로는 같은 14대 DSL 시나리오 형식을 공유하므로 한쪽에서 만든 결과를 다른 쪽이 그대로 받을 수 있다.

## 가장 짧은 시작

```bash
cd playwright-allinone
chmod +x *.sh
./build.sh --redeploy
```

이 명령은 image 빌드 + 컨테이너 기동 + 호스트 agent 연결까지 한 번에 한다 (첫 빌드 30~90분, 이후 3~10분). 완료 후 브라우저로 다음에 접속:

| 서비스 | URL | 기본 계정 |
| --- | --- | --- |
| Jenkins | <http://localhost:18080> | admin / password |
| Dify | <http://localhost:18081> | `admin@example.com` / `Admin1234!` |
| Recording UI | <http://localhost:18092> | (로컬 서비스, 계정 없음) |

자세한 설치 절차 (사전 요구사항, agent 자동/수동 연결, 첫 Pipeline 실행, 첫 녹화·재생) 는 [QUICKSTART](playwright-allinone_QUICKSTART.md) 에 단계별로 있다.

## 문서 안내

| 문서 | 언제 본다 |
| --- | --- |
| [playwright-allinone_QUICKSTART.md](playwright-allinone_QUICKSTART.md) | **처음 한 번 성공시키기** — 사전 요구사항, 빌드, 기동 확인, 첫 Pipeline, 첫 녹화·재생 |
| [playwright-allinone_RECORDING_UI.md](playwright-allinone_RECORDING_UI.md) | Recording UI 의 6개 카드 (Login Profile / Discover / Recording / Play / 결과 / 세션) 카드별 사용법 |
| [playwright-allinone_OPERATIONS.md](playwright-allinone_OPERATIONS.md) | 재배포 / 백업·복원 / 모델 변경 / Recording UI 서비스 재기동 / 장애 대응 |
| [playwright-allinone_REFERENCE.md](playwright-allinone_REFERENCE.md) | 포트 / 볼륨 / 환경변수 / 데이터 구조 / DSL 액션 14종 / API 계약 |
| [docs/recording-troubleshooting.md](docs/recording-troubleshooting.md) | 자주 발생하는 녹화·재생 에러 모음 |
| [docs/](docs/) | 결정 문서 (PLAN_*.md) — 설계 배경 / 트레이드오프 / 검증 |

## Recording UI 의 핵심 능력 (요약)

자세한 건 [RECORDING_UI 문서](playwright-allinone_RECORDING_UI.md) 에. 요약하면:

- **🔐 Login Profile** — 테스트 대상의 로그인 세션을 한 번 시드 → 매 녹화·재생마다 재사용.
- **🔍 Discover URLs** — 사이트 URL 자동 수집 → tour 시나리오 자동 생성.
- **🎬 Recording** — Playwright codegen 으로 사용자 조작 녹화 → 14-DSL 변환.
- **▶️ Play & more** — 두 가지 재생 모드 (codegen 자가 치유 / Dify LLM 치유).
- **자동 정리** — 중복 click 압축, IME 노이즈 키 제거 (CapsLock/Unidentified), popup race fallback, transient alert skip 등 codegen 부산물을 자동으로 정리해 시나리오가 깨지지 않게 한다.

## 자주 묻는 것

**Q. Jenkins 와 Recording UI 중 어느 쪽을 써야 하나?**
요구사항이 글로 잘 정의되어 있으면 Jenkins (LLM 이 시나리오 작성). 화면을 직접 보면서 클릭으로 정의하는 게 빠르면 Recording UI. 둘은 같은 시나리오 포맷을 공유.

**Q. 호스트 Ollama 가 꼭 필요한가?**
Jenkins Pipeline 의 LLM 단계 (시나리오 생성 / 치유) 가 호스트 Ollama 를 호출한다. 모델은 기본 `gemma4:26b`. Recording UI 의 Play (codegen) 만 쓸 거면 Ollama 미기동도 가능.

**Q. 컨테이너 안 코드를 바꾸려면?**
호스트 코드 수정 후 `./build.sh --redeploy` 로 image 재빌드 + 컨테이너 swap. 데이터 볼륨은 보존됨. `--reprovision` 옵션을 추가하면 provision 재실행 (KB·Jenkins 이력 등 데이터는 유지하되 Jenkins job 정의 / Dify chatflow 같은 baked-in 정의는 새 image 기준으로 재생성).

**Q. Recording UI 만 재기동하려면?**

```bash
./run-recording-ui.sh restart
```

호스트 venv 의 코드만 바꾼 경우 (executor / scenario validator 등) 컨테이너 재빌드 없이 이거면 충분.
