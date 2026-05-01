# Mock 경계 audit (개선책 #2)

## 배경
2026-05-02 사용자 보고:
- `LLM 적용 코드 실행` 이 첫 사용에서 `FrozenInstanceError` 로 즉사. 110+ e2e 통과한 상태였음.
- `headless` 체크박스가 BFS 크롤링에 무시되던 dead control. payload/모델 wiring 이 양쪽 다 없었음.

원인은 모두 동일 패턴 — **`monkeypatch.setattr(...)` 으로 stub 한 함수 *아래* 의 코드 path 가 한 번도 실행되지 않음**. mock 위에서만 검증된 "통과"가 사용자 환경에선 의미가 없음.

## audit 표

| stub 대상 | 호출하는 e2e | 비-mock 호출 시나리오 | 사각지대 위험 |
|---|---|---|---|
| `recording_service.server._start_codegen_impl` | `test_recording_service.py` 다수 | **없음** | high — codegen subprocess 자체 깨짐 감지 못함 |
| `recording_service.server._run_convert_impl` (= `converter_proxy.run_convert`) | `test_recording_service.py` 다수 | **없음** | medium — docker exec 부재/실패 시점 못 잡음 |
| `recording_service.rplus.router._run_codegen_replay_impl` | `test_recording_service.py` 7곳 | `test_smoke_subprocess.py` (개선 #1) | low (개선 #1 로 보강됨) |
| `recording_service.rplus.router._run_llm_play_impl` | `test_recording_service.py` 2곳 | `test_smoke_subprocess.py` (개선 #1) | low |
| `recording_service.rplus.router._run_enrich_impl` (Ollama) | `test_recording_service.py` 2곳 | **없음** (의도 — 외부 LLM) | low (외부 의존성, mock 정책상 OK) |
| `recording_service.rplus.router._run_diff_analysis_impl` (Ollama) | `test_recording_service.py` 2곳 | **없음** (외부) | low (위와 동일) |
| `recording_service.server._load_profile_for_browser` | 단위 2곳 | `test_auth_profiles.py` / `e2e_p1_auth_profiles.py` / `test_auth_profile_ui_e2e.py` | low |
| `recording_service.replay_proxy._resolve_auth_for_replay` | smoke 우회 시 stub 가능 | wrapper smoke (`test_smoke_subprocess.py`) 가 간접 커버 | low |

## 사각지대 결론

### high
1. **`_start_codegen_impl`** — codegen subprocess 자체가 한 번도 실 실행되지 않음. 내부 변경 (예: `playwright codegen` 인자, env 처리, stdout 파싱 형식) 회귀 사각.
2. **`_run_convert_impl`** — docker 내 변환기 자체. docker 미설치/이미지 변경/볼륨 마운트 실패 등은 실 호출 e2e 가 있어야 잡힘.

### medium
없음 (위 두 항목 외 mock 위에서 검증돼도 충분한 단위 테스트 보강이 있음).

### low (현재 충분히 커버됨)
- replay/play 흐름은 개선 #1 의 `test_smoke_subprocess.py` 로 wrapper + zero_touch_qa 양쪽 실 실행 보장.
- auth profile 흐름은 단위 + e2e 양쪽 정착.
- Ollama 류는 외부 LLM 의존성이라 mock 이 정공.

## 후속 개선 작업

### 우선 — 본 audit 후 즉시 처리 항목

3. **`_start_codegen_impl` 실 실행 smoke** — 가짜 target_url 대신 fixture HTTP 사이트로 codegen subprocess 를 ~5s 띄웠다 종료. 출력 .py 가 정상 생성됐는지만 확인.
   - 위치: `test/test_smoke_codegen_subprocess.py` (신설).
   - 비용: ~10s. CI 단계에서 상시 가능.

4. **`_run_convert_impl` 실 호출 smoke** — docker 가 호스트에 있을 때만 도는 분기. 작은 .py → docker 내 변환기 → scenario.json 정상 생성 단언.
   - 위치: `test/test_smoke_convert_docker.py` (신설).
   - 비용: ~30s + docker daemon 의존.
   - skipif: docker 미가용 환경.

### 정책 — 신규 mock 추가 시 필수 체크리스트

- 신규 `monkeypatch.setattr(X, ...)` 추가하면, 다음 중 하나가 같이 들어와야 한다:
  1. `X` 함수에 대한 **비-mock 호출 시나리오 e2e** 가 이미 존재하거나
  2. 새 mock 과 함께 **비-mock smoke 1건** 도 같이 추가하거나
  3. `X` 가 **외부 의존성 (LLM/docker registry/외부 API)** 이라 명시적 sufficient
- mock 추가하면서 위 3 중 어느 것도 안 만족하면 PR 보류.

이 정책은 `docs/PR_MERGE_RULES.md` (개선책 #6) 에 통합 예정.

## 이번 audit 가 잡은 사고

- 2026-05-02 `FrozenInstanceError` — `_run_llm_play_impl` 위에서 mock 으로만 검증돼 zero_touch_qa __main__ 의 frozen Config 위반을 못 잡음. 개선 #1 의 smoke 가 양방향 검증 (fix 제거 시 fail) 으로 회귀 가드.
- 2026-05-02 `headless` dead control — 어떤 mock 도 아니고 단순히 wiring 누락. 다만 BFS 크롤링의 *headless 동작 자체* 를 검증하는 e2e 가 없었던 것이 동일 본질의 사각지대.
