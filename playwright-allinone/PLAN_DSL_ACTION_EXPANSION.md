# Zero-Touch QA — Action DSL 확장 계획서 (9 ➔ 12+)

> **문서 목적**
> 현재 Zero-Touch QA 파이프라인의 브라우저 제어 자유도를 높이기 위해, 기존 9개로 제한되었던 표준 Action DSL을 복잡한 웹 UI(Drag & Drop, 무한 스크롤, 파일 업로드 등)까지 제어할 수 있도록 확장하는 구체적인 설계와 수행 계획입니다.

---

## 1. 개요 및 확장 원칙

### 1.1 배경
현재 시스템은 환각(Hallucination) 방지와 자가 치유(Self-Healing)의 안정성을 위해 9대 액션(`navigate`, `click`, `fill`, `press`, `select`, `check`, `hover`, `wait`, `verify`)만 허용하고 있습니다. 
그러나 B2B 솔루션 및 현대적인 웹 앱에서 빈번하게 발생하는 **파일 업로드**, **드래그 앤 드롭**, **상태 기반 검증(예: 버튼 비활성화 여부)** 시나리오를 지원하지 못하는 한계에 도달했습니다.

### 1.2 확장 원칙 (To what extent)
- **통제된 확장**: LLM(gemma4:e4b 4B 모델)의 Context 인식 한계를 고려하여, 액션 풀(Pool)을 무한정 열지 않고 **최대 12~15개** 내외로 엄격히 통제합니다.
- **스키마 호환성**: 기존 JSON 스키마 구조(`action`, `selector`, `value`)를 최대한 재사용하여 파서와 Healer 로직의 변경을 최소화합니다.
- **결정론적 실행**: 추가되는 액션들도 Playwright의 Sync API를 통해 100% 결정론적으로 실행 가능해야 합니다.
- **네트워크 엣지 케이스 커버**: 프론트엔드의 예외 처리(Error, Empty state)를 검증하기 위해 네트워크 모킹(Mocking)을 제한적으로 허용합니다.

### 1.3 로컬/에어갭 환경(소형 LLM) 한계 극복 전략 [NEW]
로컬 구동 모델(gemma4:e4b 등 8B~30B)의 인지 과부하(Attention Bandwidth 한계)와 DOM이 없는 상태에서의 맹목적 추측(Blind Guessing)을 보완하기 위해 다음 원칙을 추가합니다.
- **지능 계층(LLM)**: 복잡한 부정 지시어("~하지 마라")보다 다면적 Few-Shot 예시를 통한 패턴 복제를 강하게 유도합니다.
- **파이프라인 방어(Dify)**: LLM의 출력 스키마 붕괴(`<think>` 블록, Markdown 백틱 등)를 방어하기 위해 순수 파이썬 JSON 파서 노드를 추가합니다.
- **실행 계층(Python)**: LLM이 놓치기 쉬운 엣지 케이스(예: 프레임워크에 의해 숨겨진 `input[type="file"]`)는 Healer 호출 전 Python Executor 단계에서 Zero-Cost로 자동 탐색/복구(Fallback)합니다.

---

## 2. 신규 DSL 명세 (What)

이번 확장을 통해 다음 3가지 핵심 상호작용, 2가지 네트워크 제어, 그리고 1가지 검증 확장을 추가합니다.

### 2.1 상호작용(Interaction) 확장 (3종)

| 신규 액션 | 역할 | JSON 명세 예시 | Playwright API 매핑 |
| --- | --- | --- | --- |
| `upload` | `<input type="file">` 에 파일 업로드 | `{"action": "upload", "target": "#file-upload", "value": "fixtures/test_img.png"}` | `locator(target).set_input_files(value)` |
| `drag` | 요소를 마우스로 끌어서 다른 요소에 드롭 | `{"action": "drag", "target": "#item-1", "value": "#drop-zone"}` <br/>*(※ `value` 필드를 목적지 target으로 재활용)* | `locator(target).drag_to(page.locator(value))` |
| `scroll` | 요소가 화면에 보일 때까지 스크롤 | `{"action": "scroll", "target": "#footer", "value": "into_view"}` | `locator(target).scroll_into_view_if_needed()` |

### 2.2 네트워크 제어(Network Mocking) 확장 (2종)

| 신규 액션 | 역할 | JSON 명세 예시 | Playwright API 매핑 |
| --- | --- | --- | --- |
| `mock_status` | 특정 API 응답의 HTTP 상태 코드를 강제 조작 (에러 테스트용) | `{"action": "mock_status", "target": "**/api/users", "value": "500"}` | `page.route(target, lambda route: route.fulfill(status=int(value)))` |
| `mock_data` | 특정 API 응답의 JSON Body를 강제 조작 (빈 데이터, 가짜 데이터 주입용) | `{"action": "mock_data", "target": "**/api/data", "value": "{\"items\":[]}"}` | `page.route(target, lambda route: route.fulfill(status=200, body=value))` |

### 2.3 검증(Verify) 액션의 고도화
기존 `verify`는 단순 텍스트 포함 여부(`to_have_text`) 위주로 동작했습니다. 이를 요소의 **상태(State)**와 **속성(Attribute)**까지 검증할 수 있도록 `condition` 필드를 논리적으로 확장합니다. (기존 스키마에 `condition` 옵셔널 필드 추가)

- **가시성 검증**: `{"action": "verify", "selector": "#modal", "condition": "visible"}` ➔ `expect().to_be_visible()`
- **비활성화 검증**: `{"action": "verify", "selector": "#submit-btn", "condition": "disabled"}` ➔ `expect().to_be_disabled()`
- **속성 검증**: `{"action": "verify", "selector": "input", "value": "test@test.com", "condition": "value"}` ➔ `expect().to_have_value("test@test.com")`

---

## 3. 상세 구현 계획 (How)

확장은 프롬프트(지능), 실행기(행동), 테스트(검증) 3개의 레이어에서 진행됩니다.

### 3.1 Dify Workflow 수정 및 개선 계획 (Planner / Healer)
- **대상**: Dify UI의 `ZeroTouch QA Brain` Chatflow (또는 `dify-chatflow.yaml` 설정 파일)
- **작업 내용**:
  Dify의 프롬프트는 단순 지시를 넘어 파이썬 실행기와의 '엄격한 API 계약(Contract)' 역할을 해야 합니다. 

  #### 3.1.1 Planner 프롬프트 상세 가이드 (Draft)
  기존 9개에서 14개로 액션이 늘어나면 4B 모델(gemma4:e4b)의 환각 확률이 급증합니다. 이를 막기 위해 **"액션의 범주화", "파라미터의 변칙 사용 명시", "Few-shot 예시"** 세 가지를 System Prompt에 강하게 주입합니다.

  **[Planner System Prompt 초안]**
  ```text
  당신은 Playwright 기반의 E2E 테스트 자동화 아키텍트입니다. 
  사용자의 자연어 요구사항(SRS)을 분석하여, 브라우저를 제어할 수 있는 [14대 표준 액션] 기반의 JSON 시나리오 배열을 작성하십시오.
  
  [14대 표준 액션 및 파라미터 매핑 가이드]
  1. 기본 제어
  - navigate: target="", value="이동할 URL"
  - click: target="클릭할 요소", value=""
  - fill: target="텍스트를 입력할 폼 요소", value="입력할 문자열"
  - press: target="키 이벤트를 받을 요소 (또는 빈 문자열)", value="키 이름 (예: Enter, Escape, Tab)"
  - select: target="<select> 드롭다운 요소", value="선택할 옵션의 텍스트 또는 값"
  - check: target="체크박스 또는 라디오 버튼 요소", value="on" 또는 "off"
  - hover: target="마우스를 올릴 요소", value=""
  - wait: target="", value="대기할 밀리초 단위 시간 (예: 2000)"
  
  2. 검증 (Assertion)
  - verify: target="검증 대상 요소". 
    > 텍스트 검증 시: value="포함되어야 할 텍스트"
    > 상태 검증 시: condition="visible", "hidden", "disabled", "enabled", "checked" 중 하나 명시.
  
  3. 고급 UI 상호작용
  - upload: target="<input type='file'> 요소", value="업로드할 파일의 경로명"
  - drag: target="드래그를 시작할 소스 요소", value="마우스를 드롭할 목적지 요소의 로케이터"
  - scroll: target="화면에 나타나게 스크롤할 대상 요소", value="into_view" 고정
  
  4. 네트워크 제어 (Mocking)
  - mock_status: target="가로챌 API URL 패턴 (예: **/api/v1/users)", value="강제 반환할 HTTP 상태 코드 (예: 500)"
  - mock_data: target="가로챌 API URL 패턴", value="강제 반환할 JSON 본문 문자열 (반드시 쌍따옴표를 이스케이프 처리할 것)"
  
  [로케이터(Target) 작성 원칙]
  - Playwright의 시맨틱 로케이터를 최우선으로 사용하십시오. (예: "role=button, name=로그인", "text=검색", "label=이메일")
  - CSS 선택자나 XPath는 시맨틱 로케이터로 특정하기 어려운 경우에만 보조적으로 사용하십시오.
  
  [JSON 스키마 필수 필드]
  출력할 JSON 배열 내의 각 객체는 다음 필드를 모두 포함해야 합니다:
  - "step": 실행 순서 (1부터 시작하는 정수)
  - "action": 위 14대 표준 액션 중 하나 (절대 임의의 액션을 지어내지 말 것)
  - "target": 대상 로케이터 (해당 없는 액션은 빈 문자열 "")
  - "value": 액션의 값 (해당 없는 액션은 빈 문자열 "")
  - "condition": verify 액션 시에만 선택적으로 사용
  - "description": 이 스텝이 무엇을 하는지 설명하는 한국어 문장
  - "fallback_targets": target 탐색 실패 시도할 대체 로케이터 문자열 배열 (최소 2개 작성 필수. 예: ["text=로그인", ".login-btn"])
  
  [✅ 복합 시나리오 예시 (네트워크 모킹 및 검증)]
  입력: "프로필 저장 API에서 500 에러 발생 시 에러 팝업 노출 확인"
  출력:
  [
    {"step": 1, "action": "mock_status", "target": "**/api/profile", "value": "500", "description": "저장 API 500 에러 모킹", "fallback_targets": []},
    {"step": 2, "action": "click", "target": "role=button, name=저장", "value": "", "description": "저장 버튼 클릭", "fallback_targets": ["text=저장"]},
    {"step": 3, "action": "verify", "target": "text=저장에 실패했습니다", "value": "", "condition": "visible", "description": "에러 팝업 노출 검증", "fallback_targets": []}
  ]
  
  [엄수 사항]:
  - 반드시 유효한 JSON 배열([...]) 형태만 출력하십시오. 마크다운 코드블록이나 부연 설명은 절대 금지합니다.
  ```

  #### 3.1.2 Healer 프롬프트 상세 가이드 (Draft)
  Healer는 에러 복구 시 실패한 액션(`failed_step.action`)의 종류에 따라 탐색 및 복구 전략을 다르게 가져가야 합니다.

  **[Healer System Prompt 추가 규칙 초안]**
  ```text
  당신은 자가 치유(Self-Healing) 시스템입니다. 에러 메시지, 실패한 스텝 정보, 그리고 HTML DOM 스냅샷을 분석하십시오.
  
  [작업]:
  - 실패한 스텝(failed_step)의 원래 액션과 타겟을 확인하십시오.
  - 에러 메시지를 바탕으로 기존 요소를 찾지 못한 이유를 파악하십시오.
  - DOM 스냅샷 내에서 의도에 가장 부합하는 대체 셀렉터를 찾으십시오.
  - ⚠️ 무조건 DOM에 실제로 존재하는 요소만 제안하십시오. 가상의 셀렉터는 절대 금지합니다.
  
  [신규 액션별 치유 전략]:
  - action이 "drag"인 경우: 소스(target)와 목적지(value) 중 어느 것을 못 찾았는지 에러에서 파악하십시오. 목적지 오류라면 새 로케이터를 "value" 키에 담아 반환하십시오.
  - action이 "upload"인 경우: 겉모양이 버튼이더라도 실제로는 `<input type="file">` 속성을 가진 숨겨진 요소를 찾아 제안해야 합니다.
  - action이 "mock_status" / "mock_data"인 경우: DOM 탐색 오류가 아닙니다. 에러를 바탕으로 타겟 URL 패턴(target)의 오타나 정규식을 교정하십시오.
  
  [로케이터 작성 원칙]:
  - DOM을 분석할 때 복잡한 CSS/XPath 계층 구조보다, 변하지 않는 텍스트(`text=로그인`)나 시맨틱 속성(`role=button, name=확인`, `[placeholder="검색"]`)을 최우선으로 제안하십시오.
  
  [출력 형식]:
  - 순수 JSON 객체({...}) 형식으로만 출력하십시오. 부연 설명 금지.
  - 필수 키: "target" (새 로케이터 문자열), "fallback_targets" (대체 로케이터 문자열 배열 2~3개)
  - 선택 키: "value" (drag 등에서 목적지 로케이터나 입력값이 변경되어야 할 경우에만 포함)
  
  [✅ 예시 (click 액션 실패 치유)]
  {"target": "role=button, name=Submit", "fallback_targets": ["text=Submit", "#submit-btn"]}
  ```

  #### 3.1.3 Dify 내부 테스트 및 검증 (Prompt Evaluation)
  Python 실행 코드를 짜기 전, Dify 캔버스의 `미리보기(Preview)` 기능에서 아래 엣지 케이스들을 입력하여 환각 없이 스키마를 출력하는지 검증합니다.
  - **Edge Case 1**: "A영역의 카드를 B영역으로 드래그 앤 드롭해줘." (drag의 value 필드를 올바르게 쓰는지 확인)
  - **Edge Case 2**: "결제 API가 응답 지연될 때 버튼이 disabled 처리되는지 확인." (verify의 condition 속성을 사용하는지 확인)

### 3.2 Python Executor 로직 확장
- **파일**: `zero_touch_qa/__main__.py`, `executor.py`
- **작업 내용**:
  1. **스키마 검증기 수정**: 시나리오 유효성 검사 로직(`_validate_scenario`)에서 허용 action 목록에 신규 액션 5종 추가.
  2. **Playwright 래핑**: `executor.py`의 `execute_step` 함수에 신규 액션 분기(if-elif) 추가.
     ```python
     elif action == "upload":
         # 보안을 위해 컨테이너 내부의 허용된 fixtures/ 경로의 파일만 업로드 허용
         file_path = os.path.join(ARTIFACT_DIR, value) 
         locator.set_input_files(file_path)
     elif action == "drag":
         target_locator = page.locator(value)
         locator.drag_to(target_locator)
     elif action == "scroll":
         locator.scroll_into_view_if_needed()
     elif action == "mock_status":
         # Playwright의 네트워크 인터셉트를 사용해 상태 코드만 강제 조작
         page.route(target, lambda route: route.fulfill(status=int(value), times=1))
     elif action == "mock_data":
         # 응답 본문(JSON)을 강제 주입
         page.route(target, lambda route: route.fulfill(status=200, content_type="application/json", body=value, times=1))
     ```
  3. **Verify 고도화**: `verify` 액션 분기에서 `condition` 키워드를 추출하여 `to_be_disabled()`, `to_be_visible()`, `to_have_value()` 등을 호출하도록 분기 처리.

  #### 3.2.1 실행 계층(Executor) 3대 사각지대 방어 [NEW]
  안정적인 Playwright 부작용(Side-effect) 통제를 위해 다음 3가지 예외 처리 로직을 `executor.py`에 필수로 구현합니다.
  - **drag 목적지 방어**: 출발지(`target`)뿐만 아니라 목적지(`value`) 로케이터도 사전 검증하며, 실패 시 Healer 호출 전 뷰포트의 안전 영역으로 강제 드롭을 시도하는 예외 로직 추가.
  - **mock 전역 오염 차단**: `mock_status`, `mock_data` 액션의 `page.route` 설정이 이후의 모든 테스트 스텝을 망가뜨리지 않도록, 반드시 `times=1` 옵션을 적용하여 정확히 1회만 인터셉트하도록 제한.
  - **verify 에러 세분화**: 요소를 못 찾은 것(Locator Error)인지 상태가 다른 것(Assertion Error)인지 Try-Except로 명확히 분리하여, Healer LLM이 불필요한 로케이터 교체 무한루프에 빠지지 않도록 정확한 에러 컨텍스트 제공.

### 3.3 로컬 단위 테스트 구성
- **위치**: `playwright-allinone/test/`
- **작업 내용**:
  - **신규 Fixtures HTML 생성**: `upload.html`, `drag.html`, `scroll.html` 생성.
  - **테스트 케이스 작성**:
    - `test_upload.py`: `<input type="file">` 동작 검증.
    - `test_drag.py`: HTML5 Native Drag & Drop 영역 이동 검증.
    - `test_scroll.py`: overflow 창 및 window 무한 스크롤(lazy-loading 모방) 환경 검증.
    - `test_verify_advanced.py`: disabled, visible, value 상태 검증 테스트.
    - `test_mocking.py`: `mock_status` 및 `mock_data` 동작 검증 (더미 프론트엔드 호출 가로채기).

---

## 4. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 (Mitigation) |
| --- | --- | --- |
| **보안 (upload 경로 조작)** | 컨테이너 내부의 민감 파일(예: `.env`, `agent.jar`) 유출 위험 | `upload` 액션 사용 시, 지정된 샌드박스 폴더(`workspace/.../artifacts/`) 내의 파일만 접근 가능하도록 Path Traversal 방어 로직 적용(`os.path.abspath` 검증). |
| **LLM 혼란 (Hallucination)** | 액션 수가 늘어나 4B 모델(gemma4:e4b)이 지시를 잊거나 매개변수를 헷갈릴 위험 | 프롬프트의 텍스트 길이를 최대한 압축하여 유지하고, `drag` 의 `value`가 target selector로 쓰인다는 것을 [✅ 예시] 에 강하게 못 박음. |
| **Healer(복구) 실패** | `drag` 수행 중 소스 요소는 찾았으나 타겟 요소(value)가 변경되어 실패할 경우 | `LocalHealer` 에 에러 덤프 전송 시, 타겟 셀렉터 오류인지 소스 오류인지 구분하여 DOM 스냅샷 질의 프롬프트 분기. |
| **JSON Escape 오류** | `mock_data` 사용 시 LLM이 JSON 안의 JSON 이스케이프 문자(`\"`)를 잘못 생성하여 JSONDecodeError 발생 | Dify LLM 노드의 출력물을 파싱하는 파이썬 실행기(`executor.py`) 단에서 JSON Escape 예외를 유연하게 잡아주는 보정 로직(Fixup) 추가. |

---

## 5. 단계별 실행 일정 (Milestones)

- **Sprint 1 (완료)**: Dify Workflow(Planner/Healer) 프롬프트 고도화, 논리 모순 해결 및 `dify-chatflow.yaml`, `architecture.md` 업데이트 완료.
- **Sprint 2 (진행 예정)**: Python Executor(`executor.py`)에 신규 5종 DSL(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`) 매핑 로직 구현.
- **Sprint 3 (진행 예정)**: 기능 검증을 위한 로컬 Fixture HTML 준비 및 Pytest 단위 테스트 수행.
- **Sprint 4 (최종)**: End-to-End 파이프라인 통합 테스트 (API 에러 모킹 및 UI 예외처리 검증 포함).