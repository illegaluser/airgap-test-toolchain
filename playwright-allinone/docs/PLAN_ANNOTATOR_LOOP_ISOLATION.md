# Dynamic annotator sandbox 의 asyncio loop 격리

## 배경

### 사례
2026-05-05, `pytest -x -q` 로 광범위 회귀를 돌리면 `test_annotator_dynamic.py::test_dynamic_dropdown_hover_prepended` 가 실패. 단독 실행 시 PASS.

```
WARNING [annotate dynamic] sandbox 실패 → static fallback:
  It looks like you are using Playwright Sync API inside the asyncio loop.
```

### 문제
- `test/native/*` (27개) 가 pytest-playwright 의 `[chromium]` fixture 사용 → 세션 단위 asyncio loop 활성화.
- 같은 process 에서 후속 실행되는 `annotate_script_dynamic` 의 `_run_dynamic_pass` 가 **main thread** 에서 `sync_playwright()` 호출 → Playwright Sync API 가 running loop 감지 → reject.
- 결과: dynamic 패스가 RuntimeError → static fallback → test assertion 실패.

### 사용자 결정
> A (subprocess 격리) 진행

→ 검토 결과 **thread 격리** 로 충분 (Sync API 의 reject 검사가 thread-local 이므로). subprocess spawn overhead 없이 동등 효과.

## 의사결정

### 1. thread 로 sandbox 격리
**채택**: `_run_dynamic_pass` 안에서 `threading.Thread` 로 inproc 본체를 호출, main thread 와 격리.

근거:
- Playwright Sync API 의 거부는 `asyncio.get_running_loop()` 검사. 이 함수는 **현재 thread 에서 실행 중인 loop** 만 반환 (thread-local).
- 새 thread 는 default 로 loop 가 없음 → reject 통과.

**기각된 대안**:
- (a) `multiprocessing.get_context("spawn")` 으로 subprocess 격리: 동등 효과지만 spawn overhead (~1~2초/호출) + dataclass pickle 의존. annotate 가 codegen 직후 1회 호출이라 비용은 허용 가능했지만, thread 가 더 simple/저비용.
- (b) test 측에서 asyncio loop hack: production 영향 없음이지만 향후 다른 테스트 환경에서 같은 문제 재발 가능 — 책임 분리 안 됨.
- (c) CI 에서 native/* 와 annotator_dynamic 을 별 invocation 분리: 코드 변경 0 이지만 환경 의존 — 본질적 해결 아님.

### 2. timeout / 예외 전파
- thread 가 120 초 안에 끝나지 않으면 RuntimeError. 호출자는 static fallback 으로 graceful degrade (기존 흐름).
- worker 안 예외는 main 으로 re-raise — 호출자 try/except 가 그대로 잡음.

## 구현 범위

| # | 파일 | 변경 |
|---|---|---|
| 1 | `recording_service/annotator.py` | `_run_dynamic_pass` 을 thread wrapper 로 변경. 본체는 `_run_dynamic_pass_inproc` 로 rename. |

코드 줄 수: 추가 ~30줄, 수정 ~5줄.

## 검증

- 단독 실행: `pytest test_annotator_dynamic.py` 5개 PASS (기존 회귀)
- 합쳐 실행: `pytest -x -q` (native + annotator_dynamic 포함) PASS
- 실측: production CLI 실행 시 thread 격리 overhead 측정 (예상 무시 가능)

## 미해결 / 향후

- 만약 thread 격리도 부족한 환경 발견 시 (예: 일부 라이브러리가 process-global state 가짐) subprocess 로 승격. 그 시점에 별도 PLAN.
