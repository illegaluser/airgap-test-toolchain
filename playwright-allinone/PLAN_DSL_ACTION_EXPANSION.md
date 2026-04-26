# Zero-Touch QA — Action DSL 확장 계획서 (9 ➔ 14)

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
본 스프린트의 기준 하드웨어는 **단일 로컬 머신, 16GB RAM급, 호스트 Ollama, `gemma4:e4b` 기본 모델**입니다. 따라서 문서와 프롬프트는 "최고 성능 모델이 있으면 더 좋다"가 아니라, **4B~7B 로컬 모델에서 timeout 없이 버티는가**를 우선 기준으로 삼아야 합니다.
- **지능 계층(LLM)**: 복잡한 부정 지시어를 늘어놓는 대신, 짧은 규칙 + 1~2개의 대표 예시로 패턴 복제를 유도합니다.
- **파이프라인 방어(Dify)**: Dify 내부에 별도 parser 노드를 늘리기보다, Chatflow는 짧은 계약과 출력 제한에 집중하고 구조 검증/재시도/보정은 Python 후단에서 담당합니다.
- **실행 계층(Python)**: LLM이 놓치기 쉬운 엣지 케이스(예: 프레임워크에 의해 숨겨진 `input[type="file"]`)는 Healer 호출 전 Python Executor 단계에서 Zero-Cost로 자동 탐색/복구(Fallback)합니다.

### 1.4 Sprint 1 하드웨어 기준선

- **기본 타깃 머신**: 16GB RAM, Docker Desktop, 호스트 Ollama, headed Playwright agent 동시 실행 환경
- **기본 모델**: `gemma4:e4b`
- **허용 대안**: 7B급 로컬 모델 (`llama3.1:8b`, `qwen2.5:7b` 등)
- **비기본/비범위**: 30B급 Ollama 모델, 외부 SaaS 모델(`GPT-4o`, `Claude Sonnet`) 의존 설계
- **CPU-only 실행**: 기술적으로는 가능하지만 Sprint 1의 목표 성능 기준은 아니다. CPU-only는 smoke/debug 용도로만 본다.

### 1.5 Sprint 1 프롬프트/입력 예산

- **Planner System Prompt**: 길고 자세한 설명보다 짧은 계약 중심. 장문 규칙 나열보다 우선순위가 높은 규칙만 남긴다.
- **Planner 출력 예산**: `max_tokens` 는 1024 이내를 기준으로 잡는다.
- **Healer 출력 예산**: `max_tokens` 는 768~1024 범위, 기본은 768을 기준으로 잡는다.
- **Healer DOM 입력 예산**: DOM 스냅샷은 **권장 4,000자 이하**, 운영 상한은 **6,000자 이하**로 본다.
- **`api_docs` 입력 예산**: 네트워크 모킹 힌트가 꼭 필요할 때만 짧은 엔드포인트 목록 형태로 넣고, 장문 문서를 그대로 넣지 않는다.
- **성공 기준**: gemma4:e4b 단독 기준에서도 `<think>` 누출, Markdown 포맷 이탈, timeout 빈도가 과도하지 않아야 한다.

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
  기존 9개에서 14개로 액션이 늘어나면 4B 모델(gemma4:e4b)의 환각 확률이 급증합니다. 이를 막기 위해 Sprint 1에서는 **규칙 수를 무한정 늘리지 않고**, **우선순위 높은 계약 + 짧은 예시 + 출력 금지 규칙**만 남기는 방향으로 프롬프트를 압축합니다.

  **[Planner System Prompt 초안]**
  ```text
  당신은 Playwright 기반의 E2E 테스트 자동화 아키텍트입니다. 
  사용자의 자연어 요구사항(SRS)을 분석하여, 브라우저를 제어할 수 있는 [14대 표준 액션] 기반의 JSON 시나리오 배열을 작성하십시오.
  
  [핵심 계약]
  - step 1 은 항상 navigate 입니다.
  - 반드시 유효한 JSON 배열([...])만 출력합니다.
  - 마크다운 코드블록, `<think>` 같은 내부 추론 텍스트는 절대 출력하지 않습니다.

  [14대 표준 액션 및 파라미터 매핑]
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
  
  [로케이터 작성 원칙]
  - Playwright의 시맨틱 로케이터를 최우선으로 사용하십시오. (예: "role=button, name=로그인", "text=검색", "label=이메일")
  - CSS 선택자나 XPath는 시맨틱 로케이터로 특정하기 어려운 경우에만 보조적으로 사용하십시오.
  - name 없는 단독 role (`role=button`, `role=link`) 은 금지합니다.
  
  [JSON 스키마]
  출력할 JSON 배열 내의 각 객체는 다음 필드를 모두 포함해야 합니다:
  - "step": 실행 순서 (1부터 시작하는 정수)
  - "action": 위 14대 표준 액션 중 하나 (절대 임의의 액션을 지어내지 말 것)
  - "target": 대상 로케이터 (해당 없는 액션은 빈 문자열 "")
  - "value": 액션의 값 (해당 없는 액션은 빈 문자열 "")
  - "condition": verify 액션 시에만 선택적으로 사용
  - "description": 이 스텝이 무엇을 하는지 설명하는 한국어 문장
  - "fallback_targets": target 탐색 실패 시도할 대체 로케이터 문자열 배열 (최소 2개 작성 필수. 예: ["text=로그인", ".login-btn"])
  
  [주의]
  - `mock_status`, `mock_data` 는 반드시 API 호출을 유발하는 액션보다 먼저 배치합니다.
  - `wait` 는 최소화하고, 상태 확인은 `verify` 로 표현합니다.
  - `press` 의 키 이름은 target 이 아니라 value 에 들어갑니다.

  [✅ 복합 시나리오 예시]
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
  - DOM 스냅샷은 잘린 일부일 수 있으므로, 긴 구조 대신 짧고 안정적인 시맨틱 셀렉터를 우선 제안하십시오.
  
  [신규 액션별 치유 전략]:
  - action이 "drag"인 경우: 소스(target)와 목적지(value) 중 어느 것을 못 찾았는지 에러에서 파악하십시오. 목적지 오류라면 새 로케이터를 "value" 키에 담아 반환하십시오.
  - action이 "upload"인 경우: 겉모양이 버튼이더라도 실제로는 `<input type="file">` 속성을 가진 숨겨진 요소를 찾아 제안해야 합니다.
  - action이 "mock_status" / "mock_data"인 경우: DOM 탐색 오류가 아닙니다. 에러를 바탕으로 타겟 URL 패턴(target)의 오타나 정규식을 교정하십시오.
  
  [로케이터 작성 원칙]:
  - DOM을 분석할 때 복잡한 CSS/XPath 계층 구조보다, 변하지 않는 텍스트(`text=로그인`)나 시맨틱 속성(`role=button, name=확인`, `[placeholder="검색"]`)을 최우선으로 제안하십시오.
  
  [출력 형식]:
  - 순수 JSON 객체({...}) 형식으로만 출력하십시오. 부연 설명 금지.
  - 필수 키: "target" (새 로케이터 문자열), "value" (기본은 빈 문자열 ""), "fallback_targets" (대체 로케이터 문자열 배열 2~3개)
  - `value` 는 drag 등에서 목적지 로케이터나 입력값이 변경되어야 할 경우 실제 값을 넣고, 그 외에는 빈 문자열("")을 넣습니다.
  
  [✅ 예시 (click 액션 실패 치유)]
  {"target": "role=button, name=Submit", "value": "", "fallback_targets": ["text=Submit", "#submit-btn"]}
  ```

  #### 3.1.3 Dify 내부 테스트 및 검증 (Prompt Evaluation)
  Python 실행 코드를 짜기 전, Dify 캔버스의 `미리보기(Preview)` 기능에서 아래 엣지 케이스들을 입력하여 환각 없이 스키마를 출력하는지 검증합니다.
  - **Edge Case 1**: "A영역의 카드를 B영역으로 드래그 앤 드롭해줘." (drag의 value 필드를 올바르게 쓰는지 확인)
  - **Edge Case 2**: "결제 API가 응답 지연될 때 버튼이 disabled 처리되는지 확인." (verify의 condition 속성을 사용하는지 확인)
  - **Edge Case 3**: 16GB RAM + `gemma4:e4b` 기준에서 Planner 응답이 timeout 없이 JSON 배열만 반환되는지 확인
  - **Edge Case 4**: Heal 입력 DOM 이 길어졌을 때도 `<think>` 누출 없이 60초 안에 JSON 객체를 반환하는지 확인

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
| **프롬프트 과비대화** | 4B 모델이 규칙을 잊거나 timeout 발생 | Planner/Healer 프롬프트를 짧은 계약형으로 유지하고, 출력 토큰 상한을 낮춘다. |
| **저사양 머신 응답 지연** | 16GB 단일 머신에서 Heal 경로가 60초 안에 끝나지 않을 위험 | DOM 입력량을 제한하고, `gemma4:e4b` 기준 budget 을 문서에 먼저 고정한다. |

---

## 5. Sprint 1 상세 작업목록

Sprint 1은 기능 구현 스프린트가 아니라, **LLM 계약과 문서 기준선을 먼저 고정하는 정비 스프린트**로 정의한다. 즉 Python Executor 구현 전에, Planner/Healer/DOC 간의 용어와 스키마가 서로 어긋나지 않도록 먼저 잠그는 단계다.

### 5.1 목표

- Planner가 14대 DSL만 출력하도록 계약을 명시한다.
- Healer가 신규 액션(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`)까지 복구 가능한 JSON 계약을 갖도록 정리한다.
- `architecture.md`, `dify-chatflow.yaml`, 본 계획서 사이의 표기와 용어를 동기화한다.
- Sprint 2 구현팀이 더 이상 "문서에는 14대, 실행기는 9대" 같은 해석 충돌 없이 착수할 수 있도록 기준선을 만든다.
- 16GB 단일 머신 + `gemma4:e4b` 기준에서 과도한 모델 권장과 장문 프롬프트를 제거한다.
- Sprint 1에서 실제로 손댄 Dify Workflow 범위를 끝까지 닫아, "수정은 했지만 미완료" 상태를 남기지 않는다.

### 5.2 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S1-01 | Sprint 1의 범위를 "프롬프트/문서 계약 정비"로 명시 | 본 계획서 | Sprint 1이 실행 코드 구현을 포함하지 않는다고 명확히 적시 | 완료 |
| S1-02 | 14대 DSL 확장 범위 확정 | 본 계획서, `architecture.md` | 9개 기존 액션 + 신규 5개 액션 목록이 동일하게 기재 | 완료 |
| S1-03 | `verify.condition` 확장 스키마 정의 | 본 계획서, `architecture.md`, `dify-chatflow.yaml` | visible/hidden/disabled/enabled/checked/value 등 허용 의미가 문서화 | 완료 |
| S1-04 | Planner 프롬프트에 14대 액션 계약 반영 | `dify-chatflow.yaml` | 액션 목록, `target`/`value` 매핑, 금지 규칙 포함 | 완료 |
| S1-05 | Planner 프롬프트에 step 1 `navigate` 강제 규칙 반영 | `dify-chatflow.yaml` | 모든 시나리오 첫 step 규칙이 명시 | 완료 |
| S1-06 | Planner 프롬프트에 소형 LLM 방어 규칙 반영 | `dify-chatflow.yaml` | `<think>` 금지, wait 남용 금지, blind guessing 억제 규칙 포함 | 완료 |
| S1-07 | Planner few-shot 예시 추가 | `dify-chatflow.yaml` | 최소 1개 이상의 복합 예시가 JSON 형태로 포함 | 완료 |
| S1-08 | Healer 프롬프트에 신규 액션별 복구 전략 반영 | `dify-chatflow.yaml` | `drag`/`upload`/`mock_*` 전략이 분리 명시 | 완료 |
| S1-09 | Healer 출력 JSON 계약 정리 | `dify-chatflow.yaml`, 본 계획서 | 필수 키와 선택 키 규칙이 모순 없이 문서화 | 완료 |
| S1-10 | Healer few-shot 예시를 출력 계약과 일치시킴 | `dify-chatflow.yaml` | 예시 JSON이 실제 요구 키셋과 충돌하지 않음 | 완료 |
| S1-11 | `architecture.md` 상위 요약에 v4.1 확장 반영 | `architecture.md` | Phase 4/최종 산출물 요약이 14대 DSL 기준 | 완료 |
| S1-12 | `architecture.md` 본문 내 9대 DSL 잔존 표현 정리 | `architecture.md` | Flow 설명, 데이터 흐름, 유저 워크플로우가 14대 DSL 기준으로 정리 | 완료 |
| S1-13 | 제거된 컴포넌트 설명 반영 | `architecture.md` | 제거된 Vision Refactor 노드가 더 이상 활성 구성요소처럼 보이지 않음 | 완료 |
| S1-14 | README와 현재 구현 상태의 경계 명시 | `README.md` 또는 본 계획서 | "문서 계약 선행, 실행기 구현은 Sprint 2" 경계가 독자가 이해 가능 | 완료 |
| S1-15 | Dify Preview용 프롬프트 평가 케이스 정의 | 본 계획서 | drag / disabled / mocking 등 대표 입력 사례 문서화 | 완료 |
| S1-16 | Sprint 2 착수 전 잔여 리스크 목록화 | 본 계획서 | executor/test 미구현 항목이 남은 일로 분리됨 | 완료 |
| S1-17 | Sprint 1 하드웨어 기준선 명시 | 본 계획서, `architecture.md`, `README.md` | 기본 타깃이 16GB + `gemma4:e4b` 임이 명시 | 완료 |
| S1-18 | Planner/Healer prompt budget 정의 | 본 계획서, `dify-chatflow.yaml`, `architecture.md` | 토큰 상한과 DOM 입력 상한이 문서화 | 완료 |
| S1-19 | 30B/클라우드 모델 권장 제거 | `architecture.md` | Sprint 1 본문에서 30B/외부 모델이 기본 권장으로 남지 않음 | 완료 |
| S1-20 | CPU-only 경로의 위치 정리 | 본 계획서, `README.md` | CPU-only는 가능하지만 목표 성능 기준은 아님을 명시 | 완료 |
| S1-21 | Dify guard 설계 실체화 | `dify-chatflow.yaml`, 본 계획서 | 계획서에 적은 JSON parser/guard 방어가 실제 워크플로우 반영 또는 명시적 비채택으로 정리 | 완료 |
| S1-22 | `api_docs` 변수 처리 확정 | `dify-chatflow.yaml`, `architecture.md` | `api_docs`를 실제 prompt에 연결하거나, 쓰지 않을 경우 문서/변수에서 제거 | 완료 |
| S1-23 | Dify Preview 실측 검증 수행 | Dify UI 검증 기록, 본 계획서 | `gemma4:e4b` 기준 Planner/Healer preview 결과를 실제 확인 | 진행 중 |
| S1-24 | Dify 재배포/재import 기준 정리 | 본 계획서, `README.md` | 수정된 yaml을 콘솔에 다시 import/publish 하는 절차와 검증 지점 명시 | 완료 |
| S1-25 | Sprint 1 종료 판정 갱신 | 본 계획서 | 위 4개 잔여 항목을 닫은 뒤 Sprint 1 상태를 `완료`로 변경 | 미착수 |

### 5.3 Sprint 1 종료 조건 (Definition of Done)

- Planner/Healer 프롬프트가 모두 14대 DSL 계약 기준으로 정렬되어 있다.
- 본 계획서와 `architecture.md`가 동일한 액션 수와 동일한 개념 모델을 설명한다.
- 문서 안에서 제거된 노드나 구버전 9대 DSL 표현이 독자를 오도하지 않는다.
- "실행 가능"과 "설계 완료"를 구분하는 문장이 존재한다.
- Sprint 2에서 구현해야 할 남은 작업이 별도 항목으로 분리되어 있다.
- 기본 운영 하드웨어와 기본 모델이 `16GB + gemma4:e4b` 로 일관되게 설명된다.
- Planner/Healer prompt budget 과 DOM 입력 budget 이 명시되어 있다.
- Dify Workflow에서 실제로 수정한 범위가 미정 상태로 남아 있지 않다.
- `api_docs` / guard 설계처럼 문서에 적은 Dify 보강안이 실제 워크플로우와 일치한다.
- `gemma4:e4b` 기준 Preview 실측으로 Planner/Healer가 최소 1회 이상 정상 JSON 응답을 반환하는 것이 확인된다.

### 5.4 Sprint 1 비범위 (Out of Scope)

- `zero_touch_qa/__main__.py` 의 허용 액션 목록 변경
- `executor.py` 의 신규 액션 실행 로직 추가
- 로컬 fixture / pytest 추가
- Sprint 2 구현 범위(`executor.py`, `__main__.py`) 선반영

### 5.5 현재 판정

현재 Sprint 1은 **여전히 완료가 아니라 진행 중**이다. 다만 이유는 이제 하나로 좁혀졌다. Sprint 1에서 실제로 `dify-chatflow.yaml`을 수정한 뒤, `guard` 설계와 `api_docs` 배선, 재import 절차 문서화까지는 닫았지만, `gemma4:e4b` 기준 Preview 실측 검증이 아직 끝나지 않았기 때문이다.

- `guard` 설계는 "Dify 내부 parser/guard 노드 비채택, Python 후단 검증 담당"으로 확정했다.
- `api_docs` 변수는 실제 Planner prompt와 Start 변수 양쪽에 연결했다.
- yaml 재배포/재import 절차는 `README.md`에 반영했다.
- 2026-04-27 실측에서 `Variable #start.api_docs# not found` 오류를 한 번 재현했고, 이는 Start 변수 누락으로 확인되어 YAML에 즉시 수정했다.
- 수정 후 재프로비저닝까지는 성공했지만, 재기동 직후 `/console/api/login` 및 `/v1/chat-messages` 호출이 timeout/지연을 보여 Preview 검증을 완전히 닫지는 못했다.

즉 Sprint 1은 "문서와 프롬프트를 상당 부분 정비한 상태"를 넘어서 "설계와 배선은 닫혔지만 실측 검증이 남은 상태"다.

### 5.6 Sprint 1 잔여작업 상세

#### 5.6.1 Dify Workflow 구조 보강

1. **Guard 설계 결정**
   - **채택안:** Dify 내부에 별도 JSON parser / guard 노드를 추가하지 않는다.
   - 이유:
     - Sprint 1의 기본 하드웨어가 16GB + `gemma4:e4b` 인 만큼, Chatflow 노드를 늘려 복잡도를 키우기보다 prompt 계약과 후단 검증으로 단순성을 유지하는 편이 현실적이다.
     - 실제 구조 검증, 재시도, 보정은 Python 후단(`zero_touch_qa`)이 이미 더 정확하게 담당할 수 있다.
   - 후속 조치:
     - 계획서/아키텍처에서 "Dify 내부 parser 노드 추가"처럼 읽히는 문장을 제거하거나 비채택으로 명시한다.
   - 완료 기준: 문서와 실제 `dify-chatflow.yaml`이 같은 말을 한다.
   - 현재 상태: 완료

2. **`api_docs` 변수 처리**
   - Planner user prompt 또는 system prompt에 `api_docs`를 실제 연결해 네트워크 모킹 힌트로 사용한다.
   - 연결하지 않을 경우, Start 변수 정의와 관련 문서 설명에서 제거하거나 "후속 스프린트 후보"로 내린다.
   - 완료 기준: 정의된 변수와 실제 프롬프트 배선이 일치한다.
   - 현재 상태: 완료

#### 5.6.2 Dify 실측 검증

1. **Planner Preview 검증**
   - 입력 1: drag 시나리오
   - 입력 2: disabled verify 시나리오
   - 입력 3: mock_status 시나리오
   - 확인 항목:
     - JSON 배열만 반환하는지
     - step 1 navigate 규칙을 지키는지
     - `drag.value`, `verify.condition`, `mock_*` 순서를 올바르게 쓰는지

2. **Healer Preview 검증**
   - click 실패, drag 목적지 실패, upload 숨김 input 실패를 대표 케이스로 넣는다.
   - 확인 항목:
     - JSON 객체만 반환하는지
     - `target`, `value`, `fallback_targets` 3키를 모두 포함하는지
     - 긴 DOM 입력에서도 `<think>` 누출이나 timeout이 없는지

3. **하드웨어 기준 검증**
   - 기준 환경: 16GB RAM + `gemma4:e4b`
   - 확인 항목:
     - Planner Preview 응답 성공 여부
     - Healer Preview 응답 성공 여부
     - timeout 또는 포맷 이탈 재현 여부
   - 2026-04-27 실측 메모:
     - 첫 호출에서 `Variable #start.api_docs# not found` 를 재현했고, Start 변수 누락을 수정했다.
     - 수정 후 재프로비저닝은 성공했지만, 재기동 직후 host 측 `/console/api/login` 과 container 내부 `/v1/chat-messages` 호출이 timeout 되어 안정성 판정이 보류됐다.
   - 현재 상태: 진행 중

#### 5.6.3 문서/배포 절차 마감

1. **yaml 재import 절차 명시**
   - 수정한 `dify-chatflow.yaml`을 Dify 콘솔에 다시 import/publish 하는 순서를 문서에 적는다.
   - `.app_provisioned` 또는 재프로비저닝 시 덮어쓰기되는 동작과의 관계를 함께 적는다.
   - 현재 상태: 완료

2. **Sprint 1 종료 선언 업데이트**
   - 위 잔여 항목이 닫히면 `Sprint 1 (진행 중)`를 `완료`로 바꾼다.
   - 체크리스트 상태도 함께 갱신한다.

## 6. 단계별 실행 일정 (Milestones)

- **Sprint 1 (진행 중)**: Dify Workflow(Planner/Healer) 프롬프트 고도화, 하드웨어 기준선 정리, prompt budget 축소, `dify-chatflow.yaml` / `architecture.md` / 계획서 기준선 동기화까지 완료. 남은 일은 `gemma4:e4b` 기준 Planner/Healer Preview 실측 검증과 그에 따른 종료 판정 갱신이다.
- **Sprint 2 (진행 예정)**: Python Executor(`executor.py`)에 신규 5종 DSL(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`) 매핑 로직 구현.
- **Sprint 3 (진행 예정)**: 기능 검증을 위한 로컬 Fixture HTML 준비 및 Pytest 단위 테스트 수행.
- **Sprint 4 (최종)**: End-to-End 파이프라인 통합 테스트 (API 에러 모킹 및 UI 예외처리 검증 포함).
