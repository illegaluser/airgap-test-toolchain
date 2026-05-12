# PLAN — 녹화 중복 click 정리 & popup 캡처 race 회피

## 배경 — 사례 ddb7dad1fc7d → e89d0fe245aa

dpg 포털의 "페르소나 ChatBot" 카드를 한 번 클릭한 시나리오에서 재생이 실패.
원인은 두 가지가 겹쳐서 발생:

1. **녹화 중복** — DOM 이 `<button class="...card"><a href="javascript:window.open(...)">`
   구조라 codegen 이 같은 사용자 클릭을 outer button (step 2) + inner link (step 3)
   양쪽으로 기록. 재생 시 두 step 다 fallback 끝에 JS dispatch 까지 가서
   `window.open()` 두 번 발사 → 챗봇 탭 2개.

2. **popup 캡처 timing race** — Playwright `locator.click(timeout=10000)` 이
   actionability 문제로 10초를 다 쓴 뒤에야 JS dispatch fallback 으로 진입.
   그러나 step 을 감싼 `expect_popup(timeout=10000)` 도 10초라 이미 만료.
   결과 — `window.open()` 자체는 실행되지만 alias `page1` 미등록 →
   step 4 의 `page="page1"` lookup 실패 → 최종 FAIL.

### 진단 로그 발췌

```text
02:50:18 [Step 3] click 시작
02:50:35 (17s 후) Playwright click timeout (10s) → JS dispatch fallback
02:50:36 [Step 3] popup_to=page1 마킹됐으나 popup 발생 안 함 — alias 등록 skip.
02:50:55 [Step 4] page alias 'page1' 미등록 — fallback 사용.
02:51:48 [Step 4] FAIL — 모든 치유 실패
```

## 결정 — 두 layer 에서 교정

녹화 단계와 재생 단계 둘 다 손이 닿아야 robust 해진다.

### 결정 1 — 변환기에서 동일-타깃 연속 click dedupe

**위치** — [shared/zero_touch_qa/converter_ast.py](../zero_touch_qa/converter_ast.py)
의 `convert_via_ast` post-processing 단계 (steps 직렬화 직전).

**규칙** — 연속한 두 click step (i, i+1) 이 다음을 모두 만족하면 i 또는 i+1 중
하나만 남기고 다른 하나를 drop:

- `action == "click"` (둘 다)
- 같은 `page` 값
- 동일 accessible name — selector 의 `name=...` (role-locator) 또는 text 셀렉터의
  텍스트가 normalize 후 동일 (정규화 = 공백/개행/중복토큰 압축, "X X" → "X" 포함)

**유지 우선순위** — `popup_to` 가 있는 쪽 우선. 둘 다 같으면 뒤쪽 (i+1) 우선
(codegen 이 보통 inner element 를 뒤에 emit, 그쪽이 실제 핸들러 보유 가능성
↑).

**범위** — click 만. fill/press/select 는 의도적 반복일 수 있어 손대지 않는다.

**검증** — converter unit test 두 개:

1. 동일 name 의 button + link 연속 → 1개로 수렴 + popup_to 보존
2. 다른 name 의 연속 click → 변동 없음

### 결정 2 — popup 캡처 pages-diff fallback

**위치** — [shared/zero_touch_qa/executor.py](../zero_touch_qa/executor.py) 의
`_run_step_maybe_capture_popup`.

**현재 동작** — `with active_page.expect_popup(timeout=10000)` wrap. timeout
시 alias 등록 skip.

**변경** — expect_popup timeout 만료여도, step 실행 이후 `active_page.context.pages`
diff 로 새 page 가 발견되면 그걸 popup_to 에 등록.

**구체** —

```python
before_pages = list(active_page.context.pages)
try:
    with active_page.expect_popup(timeout=10000) as popup_info:
        result = self._execute_step(...)
    new_page = popup_info.value          # 정상 경로
except PlaywrightTimeoutError:
    # JS dispatch race fallback — 실행 직후 새 page 가 생겼는지 확인
    after_pages = [p for p in active_page.context.pages if p not in before_pages]
    if not after_pages:
        log.warning("... popup 발생 안 함 — alias 등록 skip.")
        return self._execute_step(...)   # 현 동작 유지: 재실행해 step 결과 회수
    new_page = after_pages[-1]
    log.info("[Step %s] popup pages-diff fallback 으로 alias 등록", ...)
```

**나머지 분기** (URL 검사 / 봇 차단 / alias 등록) 는 그대로.

**왜 timeout 자체를 늘리지 않는가** — expect_popup timeout 을 30s 로 늘리면
정상 케이스에서도 실패 시 30s 기다려 시나리오 재생이 느려진다. pages-diff
는 즉시 판정.

**왜 popup_info.value 를 retry 하지 않는가** — `expect_popup` context manager
는 timeout 시 그 안의 step 실행 결과 (`result`) 도 잃는다 (예외가 raise 되며
finally 블록의 result 가 unbound). 그래서 except 블록에서 `_execute_step` 을
한 번 더 부르거나 (현 동작), result 를 expect_popup 바깥에서 미리 캡처할 수
있도록 구조 변경이 필요.

**선택안** — except 블록에서 step 을 재실행하는 현 동작을 유지하되, 재실행
직후의 pages-diff 로 새 page 를 등록. 재실행 비용은 받아들임 (이미 timeout
fallback 경로 — 정상 케이스 영향 0).

**검증** — executor unit test 한 개 — 가짜 context 의 pages 목록을 mock,
expect_popup 이 timeout 나도 새 page 가 있으면 alias 등록되는지 확인.

## 결정 3 — 한글 IME 사이트 codegen 노이즈 필터 (사례 d13ea6c9320c)

**위치** — `converter_ast.py` 의 post-processing 에 dedupe 다음 단계.

**규칙** —

1. `press` value ∈ {`CapsLock`, `Unidentified`, `Process`, `Compose`, `Dead`}
   → drop. 모두 IME composition / 한영 토글 부산물.
   - `Unidentified` — Playwright 가 "Unknown key" 로 거부 (실패 강제 발생).
   - `CapsLock` — 재생 시 IME 상태와 무관, 효과 없음.
2. 빈 `fill` (value="") 직후 같은 page/target 에 non-empty `fill` 이 오면
   빈 fill drop. codegen 이 IME composition reset 으로 끼워 넣는 케이스.

**비범위 (결정 3 한정)** —

- 빈 fill 단독 (후속 fill 없음) → 보존. validator 거부는 별도 PLAN.
- 사용자가 의도적으로 누른 modifier (Ctrl/Shift/Alt 조합) → 영향 없음
  (해당 키들은 `_IME_NOISE_KEYS` 에 미포함).

**검증** — converter unit test 6건:

1. press Unidentified drop
2. press CapsLock drop
3. 빈 fill + 다음에 non-empty fill (같은 target) → 빈 fill drop
4. 빈 fill 단독 → 보존
5. 빈 fill + 다음 fill 의 target 다름 → 보존
6. d13ea6c9320c 의 실제 패턴 (5 step → 2 step)

## 비범위

- Playwright click 자체의 actionability timeout 단축 — 별도 이슈.
- transient 로딩 텍스트 (step 11 "마무리 내용을 정리하고 있습니다") 자동
  필터링 — 본 PLAN 의 범위 아님.

## 트레이드오프

- **converter dedupe 가 의도된 더블클릭을 죽일 수 있는가** — codegen 은 user
  의 더블클릭을 `dblclick()` 으로 emit (별도 액션). 연속 `click()` 두 번은
  거의 항상 wrapper/inner 중복이거나 사용자 연타 (재생 시 한 번이면 충분).
  안전한 단순화.
- **pages-diff fallback 이 노이즈 popup 을 잘못 등록하는가** — popup_to 가
  명시된 step 한정으로만 동작. 다른 step 의 부수효과 popup 은 영향 없음.
  새로 생긴 page 의 URL 은 기존 봇 차단 / 빈 페이지 검사 통과 후에만 alias
  등록.
