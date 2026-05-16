# RELEASE_CHECKLIST.md — 릴리스 직전 수동 가드

자동화 슬롯 (pre-commit / pre-push / build-time selftest / receiving-PC selftest)
이 잡지 못하는 P0/P1 회귀 — 사람이 릴리스 직전 한 번 확인.

설계 근거: [PLAN_E2E_REWRITE.md](PLAN_E2E_REWRITE.md) §5 — 자동화 불가 잔여 + §10 위험 분포.

## 1. 릴리스 직전 수동 확인 항목 (P0/P1, 자동화 불가)

### 1.1 popup Stop&Convert hang 검증 (P0, 316a132)

자동화 불가 사유: 사용자 녹화 패턴 의존. 특정 사이트의 popup 트리거 → 닫기 흐름이 필요.

수동 절차:
1. Recording UI 에서 popup 을 띄우는 외부 사이트 1건 녹화 (예: 사내 포털 챗봇 popup).
2. Stop 후 Convert 호출.
3. 변환이 완료되어 시나리오 JSON 이 디스크에 떨어지는지 확인 (hang 회귀 없음).
4. 회귀 .py 도 생성되는지 확인.

### 1.2 R-Plus replay 수동 (P0, b9b26dc)

자동화 불가 사유: 호스트에서 original.py 직접 실행 (headed) 흐름 — 받는 PC 와 동일한 환경 매트릭스가 필요.

수동 절차:
1. 임의 시나리오의 original.py 를 host 의 Replay UI 에서 R-Plus 옵션으로 재실행.
2. 컨테이너 system python 호출이 아닌 host venv 사용이 보장되는지 확인 (248bf40 회귀).

### 1.3 agent 자동연결 네트워크 토폴로지 (P0, 308129e 잔여)

자동화 불가 사유: WSL2 / Mac native 의 호스트-네트워크 토폴로지 차이. 단일 PC 빌드 후 검증으로 부족.

수동 절차:
1. `./build.sh` 직후 `docker logs dscore.ttc.playwright | grep NODE_SECRET` 로 64자 hex 시크릿 확인.
2. `NODE_SECRET=<위 값> ./mac-agent-setup.sh` (또는 wsl-agent-setup.sh) 실행.
3. agent 가 Jenkins 에 연결되어 Pipeline Stage 3 가 정상 동작하는지 확인 (Jenkins 18080 → 빌드 1회).

## 2. 외부 SUT 벤치 (P2, release 직전 수동)

자동화 슬롯 시간 예산 안에 못 들어감. 외부 사이트 의존이라 결정론도 떨어짐.

### 2.1 외부 SUT 50개 벤치 실행

```bash
cd code-AI-quality-allinone  # 또는 playwright-allinone/test/bench (필요 시)
# (실제 벤치 명령은 해당 패키지 README 참조)
```

릴리스 노트에 통과 비율 기록. 외부 사이트 변동으로 인한 실패는 *기록만* 하고 release 차단하지 않음. 패턴 회귀 (예: 동시 다발 자동완성 사이트 실패) 는 별도 조사.

## 3. 휴대용 빌드 round-trip (D 그룹 selftest 가 부분 커버, 잔여 수동)

### 3.1 받는 PC selftest 결과 확인

받는 사람이 `Launch-ReplayUI.{bat,command}` 첫 실행 시 자동으로 `selftest-receive.py` 가 돌고 결과를 stdout 에 찍는다. 받는 사람에게 "터미널 출력 사진 한 장" 만 받아 확인:

- `[OK] python`
- `[OK] playwright_module`
- `[OK] chromium_binary`
- `[OK] replay_service`
- `[OK] writable_data_dir`
- `[OK] chromium_launch`

위 6개 모두 `[OK]` 면 release 안전. 한 개라도 `[FAIL]` 이면 receiving-PC 환경 차이 — 휴대용 zip 무결성 / Chromium 자산 / OS 보안 정책 점검.

### 3.2 Windows / Mac 매트릭스

빌드 PC 한 곳에서 둘 다 못 만든다는 제약 (pack-windows.ps1 은 Windows PowerShell 또는 pwsh7 ≥, pack-macos.sh 는 macOS arm64). 사용자 결정 2026-05-14: Mac 빌드 머신에서 양쪽 산출이 가능하면 양쪽 둘 다 zip 산출, 그렇지 않으면 빌드 PC 가용 OS 한쪽만.

## 4. 릴리스 노트 템플릿

```markdown
## v<X.Y.Z> — <YYYY-MM-DD>

### 변경 사항
- (PR 제목들)

### 자동 가드 결과
- pre-commit / pre-push: 통과 (last commit <hash>)
- build-time selftest: PASS=N FAIL=N
- 외부 SUT 벤치: <통과 비율>

### 수동 확인 (RELEASE_CHECKLIST.md)
- [ ] popup Stop&Convert
- [ ] R-Plus replay
- [ ] agent 자동연결
- [ ] 받는 PC selftest 6/6 OK

### 휴대용 빌드 산출
- DSCORE-ReplayUI-portable-{win64|macos-arm64}-<ts>.zip
```

## 5. 본 체크리스트 갱신 시점

- 새 P0 회귀 발견 + 자동화 불가 영역에 매핑되면: 1.X 에 추가.
- 자동 슬롯이 잡을 수 있게 된 항목은: 1.X 에서 제거 + PLAN_E2E_REWRITE.md 에 해당 슈트 추가.
- 외부 SUT 벤치 패턴 변화: 2.X 에 신규 패턴 기록.
