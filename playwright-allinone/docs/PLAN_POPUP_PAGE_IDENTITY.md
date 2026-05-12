# Popup 시 active page 식별 정합화 (codegen → scenario → executor)

## 배경

### 사례
2026-05-05 5e1e5a6f141a 시나리오 실행 결과:

```
[Step 2] click(role=link, name=ChatBot) → window.open 으로 새 탭 발생 (login.koreaconnect.kr)
[Step 2] 새 탭 감지 → 활성 페이지 전환 (portal → login)
[Step 3] click(role=textbox, name=키워드 입력) → FAIL (login 페이지에 textbox 없음)
```

원본 codegen (`recording-actions.py`) 은 다음과 같다:

```python
with page.expect_popup() as page1_info:
    page.get_by_role("link", name="...").click()   # ← popup 트리거
page1 = page1_info.value
page.get_by_role("textbox", name="키워드 입력").click()  # ← 후속은 page (원본)
...
page1.close()
page.close()
```

popup(`page1` = 신규 chatbot 탭) 은 **열기만** 하고, 후속 인터랙션 12스텝은 모두 **원본 page (portal)** 에서 진행됨. `page1` 은 마지막에 close 만.

### 문제의 위치

| 컴포넌트 | 현황 |
|---|---|
| `converter_ast.py` | `page_vars`, `popup_info_vars` 를 내부적으로 추적하나 **step dict 에 page identity 를 기록하지 않음** ([:91-93](../zero_touch_qa/converter_ast.py#L91-L93)) |
| `scenario.json` (출력) | step 에 `action / target / value / description / step / fallback_targets` 만 — **어떤 page 변수의 액션이었는지 정보 없음** |
| `executor.py` | `len(context.pages) > 1` 만 보고 **무조건 새 탭으로 active page 전환** ([:425-453](../zero_touch_qa/executor.py#L425-L453)) |

즉 codegen 시점엔 `page` vs `page1` 가 명확하나, scenario 직렬화에서 정보가 소실되고, executor 는 휴리스틱으로 추측한다. window.open 으로 popup 이 떠도 후속 액션이 원본 page 에 있는 패턴(=이번 케이스)에서 잘못 작동.

### 사용자 결정
> 옵션1 (정공법: converter→executor 메타 propagate)

## 의사결정

### 1. scenario step 스키마 확장
**채택**: 각 액션 step 에 `page` 키 추가. popup 트리거 step 엔 `popup_to` 키 추가.

```json
{
  "action": "click",
  "target": "role=link, name=ChatBot",
  "value": "",
  "description": "클릭",
  "step": 2,
  "fallback_targets": [],
  "page": "page",
  "popup_to": "page1"      // ← 이 click 이 popup 을 트리거 → page1 alias 로 등록
},
{
  "action": "click",
  "target": "role=textbox, name=키워드 입력",
  "step": 3,
  "page": "page",          // ← 원본 page 에서 실행 (popup 으로 전환 안 함)
  ...
}
```

**하위호환**: 두 키 모두 optional. `page` 미존재 → `"page"` 로 default. `popup_to` 미존재 → 자동 전환 안 함. 기존 scenario.json 은 그대로 동작 (단, 기존 자동전환 휴리스틱은 제거되므로 popup-기반 시나리오는 재변환 필요 — §검증 참조).

**기각된 대안**:
- (a) **휴리스틱 (다음 step target 이 popup 에 있는지 probe)**: 비결정적. 같은 selector 가 양쪽에 있으면 오작동. 디버깅 어려움.
- (b) **자동전환 완전 제거 (popup 케이스는 명시적 step 으로만)**: 5e1e5a6f141a 만 해결되고 02_popup_chain 같은 "popup 안에서 후속 액션" 케이스가 깨짐.

### 2. converter — page identity emit
**채택**: `_convert_call_to_step` 의 `receiver_root` 를 step 에 그대로 기록.

popup-트리거 click 식별:
- `_handle_with` 진입 시 `popup_info_vars` 에 등록된 var 가 있으면, with-body 내 마지막 액션 step 에 `_pending_popup_info=<var name>` 임시 마커.
- `_handle_assign` 에서 `pageX = pX_info.value` 발견 시 마커를 가진 step 의 `popup_to` 를 해당 promoted page var 로 resolve.

**근거**:
- AST 변환기가 이미 `receiver_root` 를 추출 ([:332](../zero_touch_qa/converter_ast.py#L332), [:396](../zero_touch_qa/converter_ast.py#L396)) — 추가 분석 비용 없음.
- with-body 의 trigger click 식별은 codegen 패턴 상 마지막 stmt 가 액션이라는 관례 사용. 02_popup_chain 픽스처와 부합.

### 3. executor — pages_map dispatch
**채택**: `pages: dict[str, Page]` 로 page registry 보유. step 별 `pages[step.get("page", "page")]` 로 active page 결정. `popup_to` 가 있으면 click 직전 `with page.expect_popup()` 으로 신규 page 를 잡아 `pages[popup_to] = new_page` 등록.

**기존 자동전환 블록 ([:425-453](../zero_touch_qa/executor.py#L425-L453)) 제거** — 메타 기반 dispatch 로 대체. 봇차단 페이지 검사([:432-446](../zero_touch_qa/executor.py#L432-L446)) 는 popup 등록 직후로 이전 보존.

**resolver/healer rebind**: 액션마다 `resolver.page = pages[...]` / `healer.page = pages[...]`.

**final_state 캡처**: 현행 "마지막 active page" 캡처 의도를 유지하려면 마지막 step 의 page 를 final 로. 별도로 모든 page 의 최종 스냅샷도 취합할지는 후속 (P-2 화면 표시 만 가지고 갈 것 → 기존 파일명 유지).

### 4. dify_client — default URL 정정 (B 작업)
**채택**: `DIFY_BASE_URL` default 를 `http://localhost/v1` → `http://localhost:18081/v1` 로 변경.

**근거**:
- `dscore.ttc.playwright` 컨테이너 포트 매핑 (`docker port`):
  - `18080 → Jenkins` (login redirect)
  - `18081 → nginx (Dify gateway)` — `/v1/parameters` 401 + `{"code":"unauthorized"}` Dify 응답 확인
  - `50001 → agent`
- 현행 default 는 80 포트 (호스트 nginx) 가정인데 모든 docker 환경에서 실패. 18081 이 실제 진입.

**기각된 대안**: env-only (default 유지, 사용자가 매번 export). 새 사용자가 `DIFY_API_KEY` 누락만 알아도 즉시 동작하도록 default 가 맞아야.

## 구현 범위

### 파일 변경

| 파일 | 변경 |
|---|---|
| `shared/zero_touch_qa/converter_ast.py` | step build 시 `step["page"] = receiver_root`. `_handle_with` + `_handle_assign` 으로 `popup_to` resolve. |
| `shared/zero_touch_qa/executor.py` | `pages: dict[str, Page]` 도입. `_execute_step` 호출부에서 step 메타로 page 선택. 기존 자동전환 블록 제거. popup_to 처리 (`expect_popup`). |
| `shared/zero_touch_qa/dify_client.py` (또는 `config.py`) | default `DIFY_BASE_URL` → `http://localhost:18081/v1`. |
| `test/test_converter_ast.py` | 02_popup_chain 의 step 들에 `page` 메타 + popup_to 검증. |
| `test/fixtures/codegen_corpus/05_popup_then_back_to_main.py` (신규) | 5e1e5a6f141a 패턴 — popup 열고 원본 page 에서 후속. |
| `test/test_converter_ast.py` (신규 케이스) | 새 픽스처가 후속 step 들에 `page="page"` 부여 검증. |

### 비변경 (의도적)
- `scenario.json` 의 기존 키 (`step / action / target / value / description / fallback_targets`) — 변경 없음. 신규 키만 추가.
- recording-UI / recording_service — 그대로 (변환기 입력만 바뀜).
- regression_generator — `step["page"]` 가 있으면 codegen 출력 시 해당 var 를 receiver 로 사용하도록 후속 (이번 PLAN 범위 밖. 회귀 코드 생성은 실패 step 있으면 어차피 skip).

## 검증

### 단위
- `pytest playwright-allinone/test/test_converter_ast.py -v`
  - 02_popup_chain: 6개 step 의 `page` 가 (`page`, `page`, `page`, `page1`, `page1`, `page1`) — popup chain 의 변천 정확.
  - popup 트리거 click step 의 `popup_to` 정확.
  - 신규 05 픽스처: popup 후 원본 page 에서 12 액션 → `page` 모두 "page".

### 통합 (재실행)
- 5e1e5a6f141a 시나리오 재변환 (recording → scenario.json 새로 생성).
- `DIFY_BASE_URL=http://localhost:18081/v1 DIFY_API_KEY=... python -m zero_touch_qa --mode execute --scenario .../scenario.json --slow-mo 1000`
- 기대: step 3 부터 원본 portal page 에서 진행. PASS / HEALED 다수.

### 수동 회귀
- 02_popup_chain (naver) 시나리오 재변환 + 실행 → page→page1→page2 chain 정상.
- 단일 page 시나리오 (popup 없음) → 회귀 없음.

## 추가 변경 (2026-05-05) — 5e1e5a6f1 step 7 진단 중 발견된 공통 회귀

popup fix 검증 재실행에서 step 6 가 PASS 였으나 실은 **`get_by_role(name="API", exact=True)` 의 exact 가 converter 에서 손실**되어 substring 매칭으로 ``"오픈API"`` (검색결과 카테고리 탭) 가 잘못 클릭된 false-PASS. 결과적으로 portal 이 검색결과 페이지로 자동전환 → step 7 의 ``#btn_done`` 이 더 이상 페이지에 없어 FAIL.

### 변경

| 파일 | 변경 |
|---|---|
| `shared/zero_touch_qa/converter_ast.py` | `_segments_to_target` 의 `get_by_role` 분기에서 `exact=True` kwarg 발견 시 target 끝에 `, exact=true` modifier emit. `exact=False` 는 default 라 emit 안 함. |
| `shared/zero_touch_qa/locator_resolver.py` | `_split_name_exact` helper 추가 — name= 끝의 `, exact=true|false` 분리. `_resolve_role` / `_raw_role` / `_apply_chain_segment` 3곳에서 `get_by_role(role, name=name, exact=exact)` 로 native 호출. |
| `test/fixtures/codegen_corpus/11_exact_match.py` (신규) | exact=True 보존 회귀 픽스처. |
| `test/test_converter_ast.py` | `test_exact_kwarg_preserved_in_target` 추가. |
| `test/test_locator_resolver_unit.py` (신규) | `_split_name_exact` parametrize 단위 테스트 (10 케이스). |

### 효과
재실행 시 step 6 가 정직하게 FAIL — false-PASS 회귀 차단. exact=True 가 명시된 element 만 정확 매칭. 시나리오/사이트 UI 가 실제로 다르면 healing 폴백 (DIFY_API_KEY 필요) 또는 시나리오 재녹화로 해결.

## 미해결 / 후속 작업
- popup 의 close (`page1.close()`) 를 step 으로 보존할지 — 현 변환기에서 close 는 무시. executor 가 final cleanup 시 모든 page close 하므로 기능적 영향 없음. 별도 PLAN 불필요.
- 다중 popup chain (page → page1 → page2) 의 "page1 에서 page2 발생" 케이스 → `popup_to` resolve 로직이 nested with 도 처리해야 함. 02_popup_chain 픽스처가 이 케이스. 단위 테스트로 보장.
- regression_generator 가 `step["page"]` 인지 후 출력 코드에서 `page1.click(...)` / `page2.click(...)` 식으로 receiver 분기 — 별도 PLAN 으로.
