# QAExecutor.execute 의 asyncio loop 격리

## 배경

### 사례
`PLAN_ANNOTATOR_LOOP_ISOLATION.md` 후속 — annotator 격리 검증 중 `test_auth.py::test_auth_login_form_success` 등도 동일 패턴으로 실패.

```
File "shared/zero_touch_qa/executor.py:341" in execute
  with sync_playwright() as p:
playwright._impl._errors.Error: It looks like you are using Playwright Sync API
  inside the asyncio loop.
```

### 문제
- `test/native/*` 가 pytest-playwright 의 `[chromium]` fixture 로 main thread 에 asyncio loop 활성화.
- 후속 `test_auth.py` 등이 `QAExecutor.execute()` 를 호출 → 안에서 `sync_playwright()` → 동일 reject.
- 본 issue 의 영향 범위는 annotator 보다 큼 (executor 가 production 핵심 경로).

### 사용자 결정
> 전부 처리해

## 의사결정

### 1. `execute()` 본체를 별 thread 에서 실행
**채택**: annotator 와 동일 패턴 — `asyncio.get_running_loop()` 가 thread-local 임을 활용. main 에 loop 가 있어도 worker thread 에는 없음 → reject 통과.

```python
def execute(self, scenario, headed=True, storage_state_in=None, storage_state_out=None):
    """thread 격리 wrapper — 본체는 _execute_inproc."""
    q = Queue(maxsize=1)
    def worker():
        try: q.put(("ok", self._execute_inproc(...)))
        except Exception as e: q.put(("err", e))
    t = Thread(target=worker, name="qa-executor", daemon=True)
    t.start(); t.join()
    kind, payload = q.get_nowait()
    if kind == "err": raise payload
    return payload
```

본체는 기존 `execute()` 를 `_execute_inproc()` 로 rename — 한 줄도 안 바꾼다 (rename + wrapper 추가만).

**기각된 대안**:
- (a) `sync_playwright()` 호출만 thread 로 떼기: `with` 블록을 thread 로 옮기면 yield 패턴 + 결과 회수가 복잡 → execute 전체를 thread 로 가는 게 surgical.
- (b) subprocess 격리: signal handling / GIL 영향 없지만 spawn overhead + storage_state 파일 IPC 비용. 본 사례에선 thread 로 충분.

### 2. join 무제한 — timeout 은 안쪽에 위임
- `t.join()` 에 timeout 안 둠. 무한 hang 위험은 시나리오 자체의 timeout (`config.scenario_timeout_sec`, page action timeout) 이 처리.
- 외부 KeyboardInterrupt 는 main 의 join 을 깨움 → daemon thread 자동 종료. cleanup (browser.close) 은 안 되지만 일반 종료 경로는 `_execute_inproc` 의 `with sync_playwright()` 가 처리.

### 3. signal handling
- Python 의 signal handler 는 main thread 에서만 호출. `_execute_inproc` 가 worker thread 에서 도는 동안 SIGINT 발생 시 main 의 join 이 KeyboardInterrupt 받아 propagate.
- daemon 이라 worker 는 process 종료 시 자동 정리. browser cleanup 은 손해지만 abort 경로라 허용.

## 구현 범위

| # | 파일 | 변경 |
|---|---|---|
| 1 | `shared/zero_touch_qa/executor.py` | `execute` 를 thread wrapper 로 변환, 기존 본체는 `_execute_inproc` 로 rename. |

코드 줄 수: 추가 ~25줄, rename 1곳.

## 검증

- 단독 회귀: `test_auth.py`, `test_executor_full_dsl.py`, `test_healing_fallback.py` 등 PASS
- 합쳐 회귀: `native/* + test_auth.py + test_executor_full_dsl.py` 등 PASS (이전 fail set)
- 광범위 회귀: e2e 류 제외한 큰 set 한 번에 실행 PASS

## 미해결 / 향후

- production CLI 도 같은 wrapper 통과 — 실측 overhead 확인. 평소 ~ms 단위 무시 가능 추정.
- 만약 thread 격리도 부족한 환경 발견 시 subprocess 로 승격.
