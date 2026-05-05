# Dynamic annotator — codegen replay 의 dropdown/menu hover 자동 보정

## 배경

### 사례
2026-05-05, recording `7dc61c99e8f9` 의 raw codegen 실행:

```
[codegen-trace] → https://portal.koreaconnect.kr/user/ma/main
playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
Call log:
  - waiting for get_by_role("button", name="사용신청 관리")

annotate: examined 8 clicks → 0 hover 주입 (원본 그대로 사용)
returncode: 1
```

같은 시나리오를 `zero_touch_qa --mode execute` (healing 모드) 로 돌리면 9/9 PASS — step 2 / step 5 가 LocalHealer 의 동적 ancestor hover 로 HEALED.

### 문제
[recording_service/annotator.py](../recording_service/annotator.py) 의 `annotate_script` 는 **정적 분석** 만:
- 입력: codegen `.py` 의 selector chain 문자열
- 판단: `_seg_looks_like_hover_trigger(seg)` — `nav` / `menu` / `dropdown` / `gnb` 같은 패턴 매칭
- 한계: chain 이 단일 segment (`page.get_by_role("button", name="사용신청 관리")`) 면 ancestor 정보 없어 추정 불가 → hover 주입 0

코드 자체에 후속 작업 명시 ([annotator.py:15-16](../recording_service/annotator.py#L15-L16)):
> dynamic 변형 (실 페이지 visibility probe 후 annotate) 은 향후 `run_replay` 훅에 결합하여 별도 모듈에서 처리.

### 사용자 결정
> A (Dynamic annotator) 로 진행

## 의사결정

### 1. 별도 함수 `annotate_script_dynamic`
**채택**: 기존 `annotate_script` (정적) 는 보존, dynamic 변형을 같은 모듈에 추가.

```python
def annotate_script_dynamic(
    src_path: str, dst_path: str,
    *,
    base_url_override: str | None = None,
    storage_state_in: str | None = None,
    headless: bool = True,
    nav_timeout_ms: int = 15_000,
    visibility_timeout_ms: int = 2_000,
    hover_settle_ms: int = 500,
) -> AnnotateResult:
    ...
```

`AnnotateResult` 는 그대로 재사용 (필드 동일 의미). `triggers` list 에 dynamic 결과의 cssPath + reason 기록.

**기각된 대안**:
- (a) `annotate_script` 에 `dynamic=True` flag 추가: 같은 함수가 두 가지 다른 인프라(AST only vs Playwright session) 를 가진 분기 — 책임 비대.
- (b) recording_service 단계에서 두 번 호출 (static → dynamic): static 결과를 입력으로 dynamic 이 보강하는 흐름은 line index 가 두 번 밀려 복잡. 단일 dynamic 호출이 깔끔.

### 2. 알고리즘 — replay + visibility probe + ancestor hover
**채택**: src `.py` 를 runpy 로 실행하지 않고, **AST 로 액션 시퀀스 추출 후 sandbox Playwright 세션에서 sequential replay**. 각 click 직전에 dynamic probe.

```
for action in actions_extracted_from_ast:
    if action.is_navigate:        page.goto(action.url, timeout=nav_timeout)
    elif action.is_fill_or_press: action.replay(page)
    elif action.is_click:
        loc = action.resolve_locator(page)
        if not _is_visible_quick(loc, visibility_timeout_ms):
            trigger = _find_hover_trigger_dynamic(loc, page)
            if trigger:
                record_hover_for_line(action.lineno, trigger.css_path, trigger.reason)
                page.locator(trigger.css_path).hover()
                page.wait_for_timeout(hover_settle_ms)
        loc.click(timeout=visibility_timeout_ms)
    # 그 외 (assert, route, expect_popup 등) — 무시 (annotate 목적 한정)
```

**`_find_hover_trigger_dynamic`** — 기존 [_VISIBILITY_HEALER_JS](../zero_touch_qa/executor.py#L133) 를 그대로 재활용:
1. element handle 추출 (`loc.first.element_handle()` 또는 `_get_first_attached_handle`)
2. element 가 attached / not visible 면 JS 실행 → ancestor 후보 list (`{path, reason}`)
3. 각 후보 cssPath 에 대해: `page.locator(path).hover()` → target locator visible 여부 검사 → 첫 성공의 path 반환

attached element 자체가 없으면 trigger 식별 불가 → 정적 annotator 동일 결과 (hover 주입 0).

**기각된 대안**:
- (a) `runpy.run_path(src)` 로 src 그대로 실행 + monkey-patch: Playwright Locator.click 후킹 가능하나 wrap 복잡 + click 실행 흐름 끼어들기 어려움. AST 추출 + 자체 replay 가 단순.
- (b) `zero_touch_qa --mode execute` 의 healing 결과를 거꾸로 읽어 hover 주입: zero_touch_qa 흐름은 14-DSL 변환 거침. raw codegen 의 line 위치와 매핑 어려움.

### 3. AST 추출 — 어떤 액션을 replay 하는가
**채택**: 다음 6개 호출만 replay.

| 호출 | replay 형태 |
|---|---|
| `page.goto(URL)` | `page.goto(URL, timeout=nav_timeout_ms)` |
| `<chain>.click()` | (probe 후) chain.click() |
| `<chain>.fill(VAL)` | chain.fill(VAL) |
| `<chain>.press(KEY)` | chain.press(KEY) |
| `<chain>.check()` / `.uncheck()` | 그대로 |
| `<chain>.select_option(VAL)` | 그대로 |

그 외 (`expect`, `assert`, `wait_for_timeout` 등) — 무시. annotate 목적상 click 가용성만 검증.

**popup chain (`with page.expect_popup(): ...`)**: with-body 의 click 은 새 page 발생 트리거. annotator 가 popup 까지 추적할지 여부:
- **이 PR 범위 외**. dynamic annotate 는 main page 의 hover trigger 만 다룸. popup 안 액션의 hover 는 별도 PLAN.
- 이유: replay 자체가 with 안 액션도 실행하지만 popup 의 hover 추정은 popup page object 를 추적해야 함 — 복잡도 ↑↑. 02_popup_chain / 5e1e5a6f1 같은 popup 시나리오는 popup 안 click 이 dropdown 일 가능성 낮음 (popup 자체가 단일 페이지 이동).

### 4. selector 정확도 — chain 재구성
**채택**: AST 의 chain 을 [converter_ast](../zero_touch_qa/converter_ast.py) 의 `_collect_chain` + `_segments_to_target` 으로 14-DSL target 으로 변환 → [LocatorResolver](../zero_touch_qa/locator_resolver.py) 로 Playwright locator 재구성.

**근거**: converter 가 이미 `get_by_role(name, exact=True)` / `.nth` / `.filter` / `frame_locator` / nested `locator` chain 등 모두 처리. annotator 가 같은 변환 인프라 재사용 — 회귀 위험 최소화.

**확장**: 본 PR 의 `, exact=true` 보존이 그대로 활용됨.

### 5. 인증 / storage_state
**채택**: `storage_state_in` 인자 도입. recording_service 가 사용자 옵션으로 노출.

**이유**: portal 같은 인증 필요 사이트의 메뉴 / dropdown 은 비로그인 시 다르게 렌더링 → hover trigger 추정 부정확. 녹화 시점의 storage_state 를 그대로 주입해야 의미 있는 결과.

**fallback**: storage_state 없으면 그대로 진행. dynamic probe 는 비공개 영역 element 못 찾으면 정적 annotator 결과로 회귀 (회귀 0).

### 6. 비용 / failure mode
- **시간**: 시나리오 step 수 × (visibility_timeout_ms + hover_settle_ms) ≈ 9 step 시나리오 기준 30초 내외. acceptable.
- **재진입성**: src `.py` 비변경, dst `.py` 만 새로 작성. 같은 input → 같은 output 보장 (사이트 응답이 안정적이면).
- **실패 시**: dynamic probe 자체가 timeout/exception 발생 → log warning + 정적 annotator 호출로 fallback. 사용자 흐름 끊지 않음.

## 구현 범위

| 파일 | 변경 |
|---|---|
| `recording_service/annotator.py` | `annotate_script_dynamic()` 신규 + `_DynamicAnnotator` class. 기존 `annotate_script` / `AnnotateResult` 보존. |
| `recording_service/annotator.py` (helper 추출) | `_extract_actions_from_ast(tree, source) -> list[_Action]` — replay 용 dataclass. dst chain target 재구성에 converter_ast helper 활용. |
| `recording_service/server.py` | `/annotate` endpoint (또는 동등) 에 `dynamic` flag + `storage_state_in` 옵션. 미존재면 신규 추가. |
| `zero_touch_qa/executor.py` | `_VISIBILITY_HEALER_JS` 를 module-level export 로 노출 (이미 module 변수, import 만 가능). 변경 없을 수도. |
| `test/test_annotator_dynamic.py` (신규) | dropdown 픽스처 HTML + dynamic annotator 가 hover line 을 정확히 prepend 하는지 검증. |
| `test/fixtures/dropdown_menu.html` (신규) | nav/dropdown 패턴 — `사용신청 관리` 가 hover trigger 하위에 hidden. |

### 비변경
- `zero_touch_qa/executor.py` 의 healing 흐름 — 그대로.
- `converter_ast.py` — annotator 가 helper 만 import.
- recording UI — 내부적으로 dynamic 옵션 켤지 default 결정 (별도 노출 vs 항상 dynamic) 은 후속.

## 검증

### 단위
- `pytest test/test_annotator.py` — 기존 정적 케이스 회귀 0
- `pytest test/test_annotator_dynamic.py` — 신규:
  - dropdown 픽스처 (li:hover > ul.submenu) → hover prepend 1개
  - 단일 segment hidden element 가 ancestor `[aria-haspopup]` 아래 → hover prepend 1개
  - element 자체가 보이는 경우 (정적 false-positive 방지) → hover 주입 0
  - storage_state 없이 비공개 페이지 → fallback 처리 (예외 안 던짐)

### 통합
- 7dc61c99e8f9 시나리오의 `original.py` 에 dynamic annotate 실행 → `original_annotated.py` 가 "사용신청 관리" / "서비스 문의" click 직전에 hover line 보유.
- `codegen_trace_wrapper` 가 그 annotated.py 를 실행 → step 2 / step 5 가 raw 로 PASS.

### 회귀
- 5e1e5a6f1 시나리오 — popup 정상 동작 (페이지 식별 메타 보존됨, dynamic annotator 가 popup with-body 침범 안 함).
- 02_popup_chain corpus — converter 단위 테스트 그대로 PASS.

## 미해결 / 후속 작업
- popup chain 안 click 의 dynamic hover — 현재 범위 외.
- 사이트 응답 비결정성 — 동일 input 이지만 timing 으로 결과 달라질 수 있음. 개선책: 후보 path 들을 union 으로 prepend (모두 hover 후 click → 보수적으로 작동).
- recording UI 가 dynamic annotate 의 진행률 표시 — 시간 30초 내외라 progress bar 필요. 별도 PR.
