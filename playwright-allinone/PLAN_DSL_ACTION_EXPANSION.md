# Zero-Touch QA — Action DSL 확장 계획서 (9 ➔ 14)

> **문서 목적**
> 현재 Zero-Touch QA 파이프라인의 브라우저 제어 자유도를 높이기 위해, 기존 9개로 제한되었던 표준 Action DSL을 복잡한 웹 UI(Drag & Drop, 무한 스크롤, 파일 업로드 등)까지 제어할 수 있도록 확장하는 구체적인 설계와 수행 계획입니다.

## 변경 이력 / 의사결정 로그

| 날짜 | 단계 | 결정 / 작업 | 산출물 |
| --- | --- | --- | --- |
| 2026-04-27 | Sprint 2 | 14대 DSL Python Executor 구현 + `_validate_scenario` 신규 5종 + LLM heal `step.update` 병합 | `executor.py`, `__main__.py` |
| 2026-04-27 | Sprint 2 재검증 | 냉정 재검증으로 4 갭 + 1 누락 식별 → 같은 스프린트 내 마감. execute 모드 검증 일관화, `verify.condition` 화이트리스트, `regression_generator` 14대 emitter, fallback heal 의 `scenario.healed.json` 반영, mock_* 2단계 healing 경로(`_execute_mock_step`), `times` 옵션 노출 + smoke runbook | `__main__.py`, `executor.py`, `regression_generator.py`, `test_sprint2_runtime.py`, §6.4 / §6.6 / §6.8 |
| 2026-04-27 | 검증 결과 | `python3 -m pytest test/test_sprint2_runtime.py` 16/16 PASS — Sprint 2 DoD 8 항목 모두 충족. Sprint 2 "구현 완료" 선언 (§6 헤더) | §6.4.2 |
| 2026-04-27 | Sprint 3 계획 수립 | 14대 DSL fixture 기반 pytest 회귀 / mock_* UI 예외처리 / 3-Flow 통합 / regression_test.py 산출물 실행 검증 / Jenkins pytest 단계 까지 14건 작업 정의. 구현 순서를 Phase 0~8 의 9 단계 + Gate 로 구체화 (공통 conftest 선행, 저위험→고위험 액션, 산출물 회귀, 통합, healing, 3-Flow, 에어갭/CI, 클로저) | §7.1~§7.8 |
| 2026-04-27 | Sprint 4 계획 수립 | 실 Dify + Mac agent + 운영 도메인 위에서 14대 DSL E2E, healing 실효성, Convert 14대 확장(Sprint 2 carve-out closure), 운영 산출물 / 매뉴얼 까지 20 건 작업 정의. Sprint 1 §5.5 잔여 (S1-23 Preview 실측) 를 Phase 1 자동화 회귀로 흡수 (S4-19). 운영 SLA 표준 8 metric / 30 일 retention / 알림 / 책임분담 명문화 | §8.1~§8.8 |
| 2026-04-27 | Sprint 3 수행 | Phase 0~8 9 단계 순차 수행. 공통 conftest fixture 4 종 + helpers/scenarios.py 빌더, fixture HTML 7 종 (file:// + `api.example.test` mock-only 화이트리스트), 통합 pytest 11 파일 (총 31 케이스), regression_test.py subprocess 실행 검증, fallback / mock_* 2단계 healing 통합 회귀, 3-Flow 통합, airgap 가드, Jenkins Stage 2.4 두 sub-step 으로 추가. 9대 native 테스트는 `test/native/` 로 격리해 sync_playwright nesting 충돌 회피 | §7.4 / §7.5 / §7.7 |
| 2026-04-27 | 검증 결과 | `python3 -m pytest test --ignore=test/native -q` 47 PASS + `python3 -m pytest test/native -q` 30 PASS = 총 77/77, flake 0. Sprint 3 DoD 8 항목 모두 충족. Sprint 3 "구현 완료" 선언 (§7 헤더, §9 milestone) | §7.4.2 |

상세 의사결정 근거는 §6.2 / §6.3 (Sprint 2 리뷰), §7.2 / §7.3 (Sprint 3 리뷰), §8.2 / §8.3 (Sprint 4 리뷰) 참조.

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

- **가시성 검증**: `{"action": "verify", "target": "#modal", "condition": "visible"}` ➔ `expect().to_be_visible()`
- **비활성화 검증**: `{"action": "verify", "target": "#submit-btn", "condition": "disabled"}` ➔ `expect().to_be_disabled()`
- **속성 검증**: `{"action": "verify", "target": "input", "value": "test@test.com", "condition": "value"}` ➔ `expect().to_have_value("test@test.com")`

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
  2. **Playwright 래핑**: 실제 코드 기준 `executor.py`의 `_execute_step()` / `_perform_action()` 경로에 신규 액션 분기와 부가 상태 관리 로직을 추가.
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

## 6. Sprint 2 상세 작업목록 (구현 완료 — 2026-04-27)

Sprint 2는 **문서 계약을 실제 Python 실행기로 연결하는 구현 스프린트**다. 범위는 `zero_touch_qa` 런타임과 Jenkins 파이프라인 입력까지이며, Dify 운영/배포 계층 안정화는 포함하지 않는다. 2026-04-27 기준 구현·재검증·갭 마감·단위테스트(16/16 통과)까지 모두 닫혔다.

### 6.1 목표

- `chat`, `doc`, `execute` 경로에서 14대 DSL이 구조적으로 허용된다.
- `executor.py`가 신규 5종 액션(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`)을 실제 Playwright 동작으로 실행한다.
- `verify.condition`이 텍스트 검증 외의 상태/속성 검증까지 수행한다.
- 신규 액션이 기존 Self-Healing 흐름(`fallback_targets` → LocalHealer → Dify healer)과 충돌하지 않는다.
- 파일 업로드 경로, mock 수명주기, assertion/locator 오류 구분 같은 안전장치가 함께 구현된다.

### 6.2 리뷰 분석내역

Sprint 2 계획은 방향성 자체는 맞지만, 실제 코드베이스 기준으로는 다음 공백이 있었다.

- **구현 지점 표기 불일치**: 문서가 `execute_step` 기준으로 설명돼 있었지만, 실제 수정 지점은 `__main__.py`의 `_VALID_ACTIONS` / `_validate_scenario`, 그리고 `executor.py`의 `_execute_step()` / `_perform_action()` 이다.
- **업로드 계약 미정**: `upload.value`를 경로 문자열로만 적어두고, 어떤 디렉터리를 기준으로 해석할지와 path traversal 방어를 계획 수준에서 고정하지 않았다.
- **mock 수명주기 누락**: `page.route()`를 추가하는 것만 적혀 있었고, 언제 설치하고 언제 해제할지, 후속 스텝 오염을 어떻게 막을지 빠져 있었다.
- **verify 실패 분류 미흡**: `verify.condition` 구현 목표는 있었지만 locator 실패와 assertion 실패를 분리한다는 작업이 체크리스트와 종료 조건으로 내려와 있지 않았다.
- **Healer 병합 규칙 누락**: `drag`, `mock_*`는 `target`뿐 아니라 `value` 교정도 의미가 있는데, healer 응답을 어떤 기준으로 병합할지 문서에 없었다.
- **예시 키 불일치**: verify 예시 일부가 현재 DSL 계약의 `target`이 아니라 `selector`를 사용하고 있었다.

### 6.3 리뷰 보강내역

위 분석을 반영해 Sprint 2 계획을 다음과 같이 보강했다.

- **실제 코드 기준으로 수정 지점 명시**: `__main__.py`와 `executor.py`의 어떤 함수들을 수정해야 하는지 문서에 직접 적었다.
- **구현 범위 축소**: Sprint 2 범위를 `zero_touch_qa` 런타임과 Jenkins 입력 계약으로 한정하고, Dify 운영/배포 계층은 명시적으로 제외했다.
- **세부 체크리스트 확장**: 구조 검증, upload 경로 방어, drag 목적지 검증, mock 수명주기, verify 실패 분리, healer 병합, execute 모드 호환성, `scenario.healed.json` 반영까지 작업 단위를 쪼갰다.
- **완료 조건 강화**: 단순 “신규 액션 추가”가 아니라 업로드 방어, route 오염 차단, assertion/locator 실패 분리까지 Sprint 2 Definition of Done에 포함했다.
- **구현 순서 제안**: 저위험 액션(`upload`, `scroll`) → 고위험 액션(`drag`, `mock_*`, `verify.condition`) → healer/산출물 정리 순으로 권장 순서를 적었다.
- **예시 정합성 수정**: verify 예시의 키를 `selector`에서 `target`으로 고쳐 현재 DSL 계약과 맞췄다.

### 6.4 수행 내역 및 검증 결과 (2026-04-27)

Sprint 2 구현 직후 실시한 냉정 재검증에서 표면적으로는 거의 끝나 있었으나 4건의 실 갭과 1건의 누락 항목이 추가로 식별되어 같은 스프린트 내에서 모두 닫았다. 닫힌 갭과 그 검증 방식은 다음과 같다.

#### 6.4.1 식별된 갭과 처리 결과

| 갭 | 영향 | 처리 |
| --- | --- | --- |
| execute 모드에서 `_validate_scenario` 미호출 | 손으로 작성한 scenario.json 의 계약 위반이 런타임 ValueError 로 우회됨 | `_prepare_scenario` 의 execute 분기에 `_validate_scenario` 호출 추가 (S2-11 보강) |
| `verify.condition` 화이트리스트 검증 부재 | `condition: "exists"` 같은 오타가 검증을 통과하고 런타임 ValueError | `_VALID_VERIFY_CONDITIONS` 화이트리스트와 `_check_action_specific` 검증 추가 (S2-02 보강) |
| `regression_generator.py` 신규 5종 침묵 스킵 | 회귀 테스트가 upload/drag/scroll/mock_* 를 검증하지 않은 채 PASS | `_emit_step_code` 디스패치로 14대 모두 매핑 (신규 S2-15) |
| fallback heal / mock fallback 이 healed.json 미반영 | LLM heal 만 healed.json 에 기록되어 fallback 치유 결과가 분실 | fallback heal 시 `step["target"]` 직접 갱신 (S2-12 강화) |
| `mock_*` 실패 시 healing 미연결 | yaml 의 Healer mock_* 가이드가 dead code | `_execute_mock_step` 신설: fallback URL 패턴 → Dify LLM 2단계 치유 (S2-10 강화) |
| `times=1` 의미 미명문화 | 폴링/재시도 시나리오에서 의도와 다른 모킹 | step.times 옵션 노출 + §6.8 smoke 절차에 의미 명시 (S2-07 강화) |
| smoke 절차 부재 | Sprint 3 fixture 작성 전 런타임 검증 수단 없음 | §6.8 단위테스트 + 수동 시나리오 + grep 점검 절차 작성 (S2-14) |

#### 6.4.2 단위테스트 검증 결과

```text
$ python3 -m pytest test/test_sprint2_runtime.py -v
============================== 16 passed in 0.10s ==============================
```

추가된 단위테스트 (총 16건):

- 14대 액션 시나리오 검증 통과 / scroll 위반 거부 / upload artifacts 루트 강제 / mock body 정규화 (Sprint 2 초기 4건)
- `verify.condition` 알 수 없는 값 거부 / 확장 condition 수용
- `mock_*.times` 비정수·0 거부 / 양의 정수 수용
- `regression_generator` 의 upload/drag/scroll/mock_status/mock_data/verify-condition 분기 출력 검증
- `_install_mock_route` 가 `times<=0` 입력을 1 로 끌어올리는지 확인

전 항목 통과. 9대 액션 기존 pytest (30건, Playwright 브라우저 필요) 와 import 정합성도 재확인했다.

### 6.5 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S2-01 | `_VALID_ACTIONS`를 14대 DSL로 확장 | `zero_touch_qa/__main__.py` | `upload`/`drag`/`scroll`/`mock_status`/`mock_data`가 구조 검증에서 거부되지 않음 | 완료 |
| S2-02 | 액션별 필수 필드 검증 추가 | `zero_touch_qa/__main__.py` | `drag.value`, `upload.value`, `scroll.value=into_view`, `mock_status.value` 숫자성, `verify.condition` 화이트리스트, `mock_*.times` 양의 정수 검증 | 완료 |
| S2-03 | `_perform_action()`에 신규 5종 액션 구현 | `zero_touch_qa/executor.py` | 신규 액션이 실제 Playwright API 호출로 실행됨 | 완료 |
| S2-04 | upload 경로 해석/방어 로직 추가 | `zero_touch_qa/executor.py` | 허용 루트 밖 경로(`../`, 절대경로 탈출)가 차단되고 정상 파일만 업로드 가능 | 완료 |
| S2-05 | drag 목적지 사전 검증과 예외 메시지 정리 | `zero_touch_qa/executor.py` | source/target 중 어느 쪽 실패인지 로그와 예외에 남음 | 완료 |
| S2-06 | scroll 계약 고정 | `zero_touch_qa/__main__.py`, `executor.py` | `scroll.value`가 `into_view` 외 값이면 조기 실패 또는 normalize | 완료 |
| S2-07 | mock route 설치/수명주기 관리 | `zero_touch_qa/executor.py` | `mock_*`가 기본 `times=1` 로 후속 스텝 오염을 막고, step.times 로 횟수 제어 가능. 의미를 본 계획서에 명문화 | 완료 |
| S2-08 | `verify.condition` 분기 구현 | `zero_touch_qa/executor.py` | `visible`/`hidden`/`disabled`/`enabled`/`checked`/`value`/`text`(별칭 `contains_text`,`contains`) 가 각각 적절한 `expect()` 호출로 매핑 | 완료 |
| S2-09 | verify 실패 원인 분리 | `zero_touch_qa/executor.py` | `VerificationAssertionError` 가 별도 클래스로 분리되어 locator 실패와 assertion 실패가 구분된다 | 완료 |
| S2-10 | Healer 병합 규칙 점검 | `zero_touch_qa/executor.py` | `drag`/`upload` 는 LLM heal 의 `step.update` 로 target/value 모두 반영. `mock_*` 는 신규 `_execute_mock_step` 가 fallback URL 패턴 → Dify LLM 2단계 치유를 수행 | 완료 |
| S2-11 | `execute` 모드 호환성 확보 | `zero_touch_qa/__main__.py`, `executor.py` | execute 모드에서도 `_validate_scenario` 가 호출되어 외부 scenario.json 에 신규 액션이 포함돼도 동일한 계약으로 실행됨 | 완료 |
| S2-12 | `scenario.healed.json` 반영 범위 점검 | `zero_touch_qa`, 리포트 산출물 | LLM heal 뿐 아니라 fallback_targets / mock_* fallback 치유도 step dict 갱신 → 최종 healed.json 반영 | 완료 |
| S2-13 | Jenkins 입력 계약 정리 | `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline`, README | `API_DOCS` 등 Sprint 1에서 추가한 입력이 Sprint 2 런타임과 일관되게 유지됨 | 완료 |
| S2-14 | 최소 smoke 검증 절차 문서화 | 본 계획서 §6.8 | 신규 5종 액션별 단위테스트 + 수동 시나리오 + 회귀 산출물 grep 절차가 본 계획서에 작성 | 완료 |
| S2-15 | `regression_generator.py` 14대 확장 | `zero_touch_qa/regression_generator.py` | upload/drag/scroll/mock_* 가 회귀 테스트 산출물에 정상 출력 (이전엔 빈 줄로 침묵 스킵) | 완료 |

### 6.5 구현 순서 권장안

1. `__main__.py` 구조 검증 확장부터 수행한다.
2. `executor.py`에 신규 액션 분기를 추가하되, `upload`/`scroll` 같은 저위험 액션을 먼저 붙인다.
3. 그다음 `drag`, `mock_*`, `verify.condition`처럼 부작용과 상태 분기가 큰 액션을 붙인다.
4. 마지막에 Healer 병합, `scenario.healed.json`, `execute` 모드 호환성을 정리한다.

### 6.6 Sprint 2 종료 조건 (Definition of Done) — 모두 충족

- ✅ `chat`, `doc`, `execute` 모두에서 신규 5종 액션을 포함한 시나리오가 `_validate_scenario` 를 통과한다. execute 모드도 동일 검증을 거치도록 [`__main__.py:223`](zero_touch_qa/__main__.py#L223) 가 보강되었다.
- ✅ `executor.py` 가 `upload`/`drag`/`scroll`/`mock_status`/`mock_data` 5종을 실제 Playwright API 호출로 수행한다 ([`executor.py:967-991`](zero_touch_qa/executor.py#L967-L991), `_execute_mock_step`).
- ✅ `verify.condition` 7개 분기(visible/hidden/disabled/enabled/checked/value/text 별칭) 가 구현되고, `VerificationAssertionError` 가 별도 클래스로 분리되어 assertion 실패와 locator 실패가 구분된다.
- ✅ upload 경로 방어 (`commonpath` 기반 artifacts 루트 강제) 와 mock 수명주기 제한 (`times=1` 기본 + step.times 옵션) 이 코드와 단위테스트로 보장된다.
- ✅ Healer 가 바꾼 `target`/`value` 는 LLM heal 의 `step.update` 로 drag/upload 에 반영되고, fallback_targets / mock_* fallback 치유는 `step["target"]` 직접 갱신으로 `scenario.healed.json` 까지 일관되게 반영된다.
- ✅ `regression_generator.py` 가 14대 액션 모두를 출력하므로 회귀 산출물이 신규 액션을 침묵 스킵하지 않는다.
- ✅ Sprint 3 테스트팀이 fixture/pytest 작성에 착수할 수 있도록 `test_sprint2_runtime.py` 가 16건의 단위테스트로 입력 계약·헬퍼·회귀 출력을 잠갔다.

### 6.7 Sprint 2 비범위 (Out of Scope)

- Dify 서버 기동, nginx, gunicorn, `provision.sh` 등 운영 계층 수정
- 대규모 Dify prompt 재설계
- 로컬 fixture HTML 작성과 pytest 본격 구축
- Flow 3(convert) 경로의 14대 DSL 완전 확장

### 6.8 신규 5종 액션 Smoke 점검 절차

Sprint 3 정식 pytest 구축 전, 14대 DSL 확장이 외관상 동작하는지 빠르게 확인하기 위한 수동 점검이다. 모두 `playwright-allinone` 디렉터리에서 실행한다.

#### 6.8.1 사전 준비

```bash
cd playwright-allinone
python3 -m pip install -r test/requirements.txt
python3 -m playwright install chromium
mkdir -p artifacts/fixtures
```

#### 6.8.2 단위 검증 (pytest)

신규 액션의 입력 계약·런타임 헬퍼만 격리해 빠르게 확인한다. Playwright 브라우저를 실제로 띄우지 않으므로 60초 안에 끝난다.

```bash
python3 -m pytest test/test_sprint2_runtime.py -v
```

확인 포인트:

- 14대 액션 시나리오가 `_validate_scenario` 통과
- `scroll.value=bottom` 같은 위반은 즉시 거부
- `verify.condition=foo` 같은 화이트리스트 밖 값은 즉시 거부
- `mock_*.times` 가 정수가 아니면 거부
- `_resolve_upload_path` 가 artifacts 루트 밖 경로(`../secret.txt`) 차단
- `_normalize_mock_body` 가 dict / JSON 문자열 / 평문 모두 처리

#### 6.8.3 액션별 시나리오 smoke (실 브라우저)

각 액션을 단독으로 검증하는 최소 시나리오 4개를 `--mode execute` 로 돌려본다. 별도 Dify 호출 없이 14대 DSL 입력 계약만 검사한다.

##### A) upload — `<input type="file">` 에 artifacts 안 파일 업로드

```json
[
  {"step": 1, "action": "navigate", "value": "https://the-internet.herokuapp.com/upload"},
  {"step": 2, "action": "upload", "target": "#file-upload", "value": "fixtures/sample.txt"},
  {"step": 3, "action": "click", "target": "#file-submit"},
  {"step": 4, "action": "verify", "target": "#uploaded-files", "condition": "visible"}
]
```

```bash
echo "smoke" > artifacts/fixtures/sample.txt
python3 -m zero_touch_qa --mode execute --scenario artifacts/upload_smoke.json --headless
```

기대: 4 스텝 PASS, `regression_test.py` 가 `set_input_files("fixtures/sample.txt")` 라인을 포함.

##### B) drag — source → destination drag-and-drop

```json
[
  {"step": 1, "action": "navigate", "value": "https://the-internet.herokuapp.com/drag_and_drop"},
  {"step": 2, "action": "drag", "target": "#column-a", "value": "#column-b"}
]
```

기대: drag PASS. 실패 시 로그에 "drag 목적지 탐색 실패" 또는 source 실패가 명시되어 분리됨.

##### C) scroll — into_view 강제

```json
[
  {"step": 1, "action": "navigate", "value": "https://the-internet.herokuapp.com/large"},
  {"step": 2, "action": "scroll", "target": "#sibling-50.10", "value": "into_view"}
]
```

기대: PASS. `value: "bottom"` 으로 바꾸면 `_validate_scenario` 가 즉시 거부.

##### D) mock_status / mock_data — 네트워크 모킹

```json
[
  {"step": 1, "action": "mock_status", "target": "**/api/users/*", "value": "500"},
  {"step": 2, "action": "mock_data", "target": "**/api/list", "value": "{\"items\":[]}"},
  {"step": 3, "action": "navigate", "value": "https://example.com"}
]
```

기대: 모든 스텝 PASS. `times` 기본 1 이므로 첫 매칭만 가로채고 후속 스텝은 실네트워크. 폴링이 필요하면 `"times": 5` 등 명시.

#### 6.8.4 회귀 테스트 산출물 확인

성공한 시나리오는 `artifacts/regression_test.py` 가 자동 생성된다. 신규 5종이 빈 줄로 침묵 스킵되지 않는지 확인한다:

```bash
grep -E "set_input_files|drag_to|scroll_into_view_if_needed|page.route" artifacts/regression_test.py
```

위 grep 이 모두 매칭되면 회귀 테스트 생성기까지 14대 매핑이 살아 있다.

## 7. Sprint 3 상세 작업목록 (구현 완료 — 2026-04-27)

Sprint 3 은 **Sprint 2 에서 구현한 14대 DSL 런타임이 진짜로 동작하는지 fixture 기반 pytest 로 잠그는 검증 스프린트**다. 범위는 `playwright-allinone/test/` 의 fixture HTML 과 pytest 케이스, 그리고 Jenkins 파이프라인의 pytest 단계 추가까지다. Dify Brain 의 LLM 응답 품질 검증은 Sprint 1 책임이며, 실제 운영 환경 통합은 Sprint 4 로 분리한다. 2026-04-27 기준 구현·47/47 통합테스트 PASS·30/30 native 회귀 PASS·Jenkins stage 추가까지 모두 닫혔다.

### 7.1 목표

- 14대 DSL 액션이 모두 fixture 기반 pytest 1건 이상으로 잠긴다 — 9대는 회귀, 신규 5종은 신설.
- **에어갭 강제**: 모든 fixture 가 `file://` URL 로 동작하고 외부 도메인 참조 0건.
- v4.1 의 진짜 차별점인 **mock_* 가 단순 라우트 설치를 넘어 UI 예외처리 시나리오** 로 검증된다 (예: `mock_status 500` → `verify` 에러 UI).
- 3-Flow (`chat`/`doc`/`execute`) 모두 fixture 위에서 한 번씩 통합 회귀.
- Healing 루프 (fallback_targets / LocalHealer / Dify mock) 가 실제로 복구하고 `scenario.healed.json` 에 기록되는지 검증.
- Jenkins 파이프라인이 pytest 단계를 가져 매 빌드마다 자동 회귀.

### 7.2 리뷰 분석내역

Sprint 3 을 단순히 "신규 액션마다 pytest 1건씩"으로 잡으면 다음 함정에 빠진다.

- **mock_* 의 의미 누락**: 라우트 설치 자체만 PASS 로 넘기면 v4.1 의 동기 (UI 예외처리 검증) 가 산출물에 안 남는다. UI 가 실제로 에러 메시지를 노출하는지까지 검증해야 의미가 있다.
- **Healing 검증 누락**: Sprint 2 에서 `_execute_mock_step` / fallback heal 갱신을 만들었지만 통합 테스트가 없으면 회귀 차단력이 약하다.
- **외부 사이트 의존**: Sprint 2 smoke runbook 은 `the-internet.herokuapp.com` 을 참조했지만 본 스프린트의 정식 회귀는 file:// 만 허용해야 에어갭 환경 보장.
- **Dify 실호출 위험**: 작은 모델 (gemma4:e4b) 의 응답 비결정성을 Sprint 3 이 흡수하면 회귀 안정성 0. Dify 는 monkeypatch 로 강제 분리.
- **regression_test.py 산출물 회귀 부재**: Sprint 2 에서 14대 emitter 를 추가했지만 생성된 코드가 실제로 Python 으로 실행 가능한지 끝단 검증이 없다.
- **3-Flow 통합 부재**: 단위테스트만으로는 chat/doc/execute 3 모드의 입력 → DSL → 실행 → 산출물 사슬이 살아 있는지 회귀가 안 된다.

### 7.3 리뷰 보강내역

위 분석을 반영해 Sprint 3 을 다음과 같이 잡는다.

- **mock_* 는 UI 예외처리까지 한 시나리오** 로 묶는다 — fixture HTML 에 `fetch()` 하는 페이지를 두고, 500/empty 응답 후 DOM 에 에러 UI 가 노출되는지 `verify` 로 검증.
- **Healing 통합 테스트 별도** — 깨진 selector 를 fixture 에 넣고 fallback_targets 로 복구되는 케이스, mock 패턴이 잘못되어 fallback URL 로 복구되는 케이스를 각각 신설.
- **외부 도메인 참조 0 강제** — CI 에서 `grep -r "https://" test/fixtures/` 같은 가드를 두는 단계까지 포함.
- **Dify 호출은 monkeypatch** — `DifyClient.request_healing` 을 가로채 결정론적 응답 반환. 실 LLM 검증은 Sprint 1 영역.
- **regression_test.py 실행 검증** — 생성된 산출물을 별도 subprocess 로 실행해 PASS 까지 확인.
- **3-Flow 통합** — chat 모드는 Dify monkeypatch, doc 모드는 로컬 구조화 step parser, execute 모드는 손작성 scenario.json 입력으로 각각 한 케이스.

### 7.4 수행 내역 및 검증 결과 (2026-04-27)

#### 7.4.1 신규 산출물

- **conftest 인프라**: `make_executor`, `monkeypatch_dify`, `run_scenario`, `write_scenario_json` fixture 4 종 + `helpers/scenarios.py` 14대 액션 빌더.
- **fixture HTML 7 종**: `upload.html`, `scroll.html`, `verify_conditions.html`, `drag.html`, `mock_status.html`, `mock_data.html`, `full_dsl.html` — 모두 file:// 로 동작하고 외부 도메인 0 (mock-only `api.example.test` 만 허용).
- **테스트 파일 11 종**: `test_upload.py` (3) / `test_scroll.py` (2) / `test_verify_conditions.py` (8) / `test_drag.py` (2) / `test_mock_status.py` (3) / `test_mock_data.py` (3) / `test_regression_emit_runs.py` (2) / `test_executor_full_dsl.py` (1) / `test_healing_fallback.py` (1) / `test_mock_healing.py` (2) / `test_three_flows.py` (3) / `test_airgap_guard.py` (1).
- **9대 native 분리**: `test/native/` 디렉토리로 9 개 기존 pytest-playwright 파일 이동 — sync_playwright nesting 충돌 방지.
- **Jenkins stage 2.4** 신규: integration / native 두 sub-step 으로 호출 + JUnit XML 두 개 보존.

#### 7.4.2 검증 결과

```text
$ python3 -m pytest test --ignore=test/native -q
============================= 47 passed in 15.02s ==============================

$ python3 -m pytest test/native -q
============================== 30 passed in 9.80s ==============================
```

총 **77 케이스 PASS** — Sprint 2 단위 16 + Sprint 3 통합 31 + native 회귀 30. 실패 0, flake 0.

#### 7.4.3 주요 디버깅 결정

- **mock_status 의 fetch URL 절대화**: file:// 페이지에서 상대 fetch URL 은 page.route 글롭 패턴 매칭이 안 됨. fixture 의 fetch 를 `https://api.example.test/api/users/*` 같은 가상 절대 URL 로 변경하고 airgap 가드 화이트리스트에 `.test` TLD 등록.
- **hover→click 순서 보존**: full_dsl 통합 시나리오에서 click 핸들러가 status 를 "clicked" 로 덮어쓰면 이후 hover 의 mouseenter 가 다시 발화되지 않음. hover 를 click 보다 먼저 배치.
- **9대/Sprint 3 분리**: pytest-playwright 의 page fixture 와 executor 의 sync_playwright 가 같은 세션 안에서 asyncio loop 충돌 → 9 개 native 테스트를 `test/native/` 디렉토리로 격리, Jenkins stage 도 두 sub-step 으로.
- **fallback heal 의 healed.json 반영 회귀**: Sprint 2 의 S2-12 강화가 실제로 직렬화에 반영되는지 통합 테스트로 검증 — `step["target"]` 갱신값이 scenario_after dict 에 in-place 적용됨을 확인.

### 7.5 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S3-01 | upload fixture + pytest | `test/fixtures/upload.html`, `test/test_upload.py` | `<input type="file">` 에 artifacts 안 파일 업로드 성공 / artifacts 밖 경로 차단 / 빈 value 거부 — 3 케이스 | 완료 |
| S3-02 | drag fixture + pytest | `test/fixtures/drag.html`, `test/test_drag.py` | source→destination drop 후 DOM 변화 검증 / 목적지 미존재 시 RuntimeError — 2 케이스 | 완료 |
| S3-03 | scroll fixture + pytest | `test/fixtures/scroll.html`, `test/test_scroll.py` | viewport 밖 요소가 scroll 후 visible / `into_view` 외 value 는 `_validate_scenario` 거부 — 2 케이스 | 완료 |
| S3-04 | mock_status + UI 예외처리 시나리오 | `test/fixtures/mock_status.html`, `test/test_mock_status.py` | 500 모킹 → fetch 호출 → DOM 에 에러 UI 노출 → `verify` / `times=1` 기본 / `times=3` 다중 가로채기 — 3 케이스 | 완료 |
| S3-05 | mock_data + 빈 응답 시나리오 | `test/fixtures/mock_data.html`, `test/test_mock_data.py` | dict body / JSON 문자열 / 빈 배열 응답 후 "데이터 없음" UI — 3 케이스 | 완료 |
| S3-06 | verify.condition 분기별 pytest | `test/fixtures/verify_conditions.html`, `test/test_verify_conditions.py` | hidden/disabled/enabled/checked/value/text 분기 + 빈 condition + unknown condition 거부 — 8 케이스 | 완료 |
| S3-07 | regression_generator 산출물 실행 검증 | `test/test_regression_emit_runs.py` | 14대 액션 시나리오 → `generate_regression_test` → subprocess 실행 종료코드 0 + compileall — 2 케이스 | 완료 |
| S3-08 | 14대 액션 통합 시나리오 | `test/test_executor_full_dsl.py` | 14대 22 스텝 한 시나리오 PASS / 14대 모두 등장 검증 — 1 케이스 | 완료 |
| S3-09 | fallback heal 통합 검증 | `test/test_healing_fallback.py` | 1차 target 실패 → fallback_targets 복구 → scenario_after 에 갱신된 target 직렬화 / Dify heal 미호출 검증 — 1 케이스 | 완료 |
| S3-10 | mock_* 치유 경로 통합 검증 | `test/test_mock_healing.py` | 잘못된 URL 패턴 → fallback URL / Dify monkeypatch 응답 → 복구 — 2 케이스 | 완료 |
| S3-11 | 3-Flow 통합 회귀 | `test/test_three_flows.py` | chat (monkeypatch) / doc (로컬 parser, Dify 0 호출) / execute (scenario.json + _validate_scenario) — 3 케이스 | 완료 |
| S3-12 | airgap 가드 | `test/test_airgap_guard.py` | 모든 fixture HTML 이 화이트리스트(`api.example.test`/`w3.org`) 외 외부 호스트 0 — 1 케이스 | 완료 |
| S3-13 | Jenkins 파이프라인 pytest 단계 | `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` Stage 2.4 | integration + native 두 sub-step 호출 / JUnit XML 두 개 보존 / 실패 시 빌드 fail | 완료 |
| S3-14 | Sprint 3 결과 요약 | 본 계획서 §7.4 / §7.6 | 단위테스트 수, 신규 fixture 목록, 회귀 차단 범위 명시 + Sprint 4 인계 가능 | 완료 |

### 7.6 구현 순서 권장안

전체를 7 phase 로 나눈다. 각 phase 의 산출물이 다음 phase 의 입력이 되므로 **순서를 지킨다**. phase 마다 "다음으로 넘어가지 않을 조건" 을 명시한다.

#### Phase 0 — 공통 인프라 준비 (선행 필수)

S3-01 부터 손대지 않고, 모든 후속 테스트가 공유할 헬퍼/fixture 를 conftest 와 작은 헬퍼 모듈로 먼저 만든다.

1. `test/conftest.py` 에 다음 fixture 추가:
   - `monkeypatch_dify(monkeypatch)` — `DifyClient.generate_scenario` / `request_healing` 을 결정론적 응답을 반환하도록 가로채는 fixture. 인자로 응답 dict 를 받는 factory 형태.
   - `make_executor(tmp_path)` — `Config.from_env()` 대체로 격리된 `Config` + `QAExecutor` 인스턴스를 반환. artifacts_dir 는 tmp_path.
   - `run_scenario(executor, scenario, headed=False)` — 시나리오를 실행하고 `(results, scenario_after, run_log_path)` 를 반환하는 헬퍼.
2. `test/fixtures/_common.css` 같은 공통 inline 스타일 파일은 **두지 않는다** — 각 fixture HTML 자기완결.
3. `test/helpers/scenarios.py` 신설 — 자주 쓰는 시나리오 빌더 함수 (`navigate_to(fixture_name)` 등).

**Gate**: Phase 0 산출물만으로 빈 시나리오 실행이 동작해야 다음 단계 진입. `python3 -m pytest test/test_sprint2_runtime.py` 16 건 회귀 무결.

#### Phase 1 — 저위험 단일 액션 (S3-01, S3-03, S3-06)

부작용이 적은 순서로 시작한다. 각 케이스는 fixture HTML 1 개 + test 파일 1 개로 구성.

1. **S3-01 upload** — fixture 는 `<input type="file">` 와 업로드 후 파일명을 표시할 `<div id="result">` 만 가진 HTML. pytest 3 케이스: happy / artifacts 밖 차단 / 빈 value 거부.
2. **S3-03 scroll** — fixture 는 viewport 보다 큰 페이지에 `#footer` 를 둠. pytest 2 케이스: scroll 후 visible / `into_view` 외 value 거부.
3. **S3-06 verify.condition** — fixture 는 disabled/checked/value/hidden/text 를 가진 다양한 element 들의 정적 페이지. pytest 7 케이스 (각 condition + 빈 condition + unknown).

**Gate**: Phase 1 의 12+ 케이스가 모두 통과해야 다음 phase. 이 시점에서 9대 액션 회귀 (`test_*.py`) + Sprint 2 단위테스트도 함께 PASS.

#### Phase 2 — 고위험/부작용 단일 액션 (S3-02, S3-04, S3-05)

drag 와 mock_* 는 단순 액션이 아니라 v4.1 의 차별점이므로 별도 phase 로 분리.

1. **S3-02 drag** — fixture 는 HTML5 drag-and-drop API 로 동작하는 source/target 컬럼. pytest 2 케이스: drop 후 DOM 변화 검증 / 목적지 미존재 시 RuntimeError.
2. **S3-04 mock_status (UI 예외처리 시나리오)** — fixture 는 페이지 로드 시 `fetch('/api/users/1')` 를 호출하고 응답 status 가 5xx 면 `<div class="error">` 를 표시하는 페이지. 시나리오는 `mock_status 500 → navigate → verify error UI visible` 의 4 스텝. pytest 3 케이스: 500 → 에러 UI / `times=1` 후 두 번째 호출은 실네트워크 / `times=3` 다중 가로채기.
3. **S3-05 mock_data (빈 응답 시나리오)** — fixture 는 `fetch('/api/list')` 응답을 list 로 렌더하는 페이지. dict body / JSON 문자열 / 평문 모두 fulfill 되는지 + 빈 배열 응답 시 "데이터 없음" UI 노출까지 확인.

**Gate**: Phase 2 의 8+ 케이스 통과 + mock_* 가 단순 라우트 설치가 아니라 **UI 예외처리까지 검증** 한다는 v4.1 동기가 산출물 (스크린샷 / run_log) 에 남아 있어야 한다.

#### Phase 3 — 산출물 실행 검증 (S3-07)

Sprint 2 에서 가장 큰 침묵 갭이었던 부분. 단순 emit 단위테스트와 별개로 **별도 Python 프로세스에서 실행되는지** 까지 회귀.

1. 14대 액션을 모두 포함한 시나리오 dict 를 만든다 (Phase 2 까지의 fixture 를 그대로 재활용).
2. `generate_regression_test(scenario, results, tmp_path)` 호출.
3. `subprocess.run(["python3", str(tmp_path / "regression_test.py")], check=True, timeout=60)` — 종료코드 0 확인.
4. 산출 파일이 valid Python 인지 추가로 `compile()` 로 syntax check.

**Gate**: subprocess 실행 PASS. Phase 3 통과 시점이 Sprint 2 의 `regression_generator` 14대 확장 (S2-15) 의 **진짜** Definition of Done 충족.

#### Phase 4 — 14대 통합 시나리오 (S3-08)

Phase 1~2 의 격리 fixture 들을 한 번에 조합한 통합 시나리오를 executor 가 메타-회귀.

1. `test/fixtures/full_dsl.html` 신설 — 14대 모든 액션이 한 페이지에서 검증되도록 form/list/error UI 영역을 모두 포함.
2. 14 스텝 시나리오 1 개를 손작성 (각 액션 1 회씩).
3. `run_scenario` 로 실행 → results 14건 모두 PASS / `run_log.jsonl` 14 줄 / `scenario.healed.json` 이 원본과 동일 (heal 미발생).

**Gate**: 14 스텝 모두 PASS. 이 시점이 "14대 DSL 이 한 시나리오에서 같이 돌아간다" 는 메타-회귀 보장.

#### Phase 5 — Healing 루프 통합 (S3-09, S3-10)

self-healing 이 진짜로 step dict 을 갱신하고 healed.json 에 직렬화되는지 검증. **반드시 Phase 0 의 monkeypatch_dify fixture 를 사용** — 실 Dify 호출 0.

1. **S3-09 fallback heal** — fixture 에 selector A 를 일부러 깨놓고, `fallback_targets` 에 selector B 를 넣은 시나리오 작성. 실행 후 `scenario.healed.json` 에 `step.target == B` 가 직렬화되는지 검증.
2. **S3-10 mock fallback** — 잘못된 URL 패턴을 1차로, fallback URL 을 2차로 둔 mock_status 스텝 작성. fallback 으로 복구되어 `scenario.healed.json` 에 갱신된 패턴이 남는지 + monkeypatch 된 Dify 응답이 LLM heal 단계까지 도달하는지 둘 다 검증 (2 케이스).

**Gate**: healed.json 직렬화 정합성 PASS. monkeypatch 가 풀린 채로 테스트 누수되지 않았는지 (`request_healing` 호출 카운트 == 예상치) 추가 확인.

#### Phase 6 — 3-Flow 통합 (S3-11)

`__main__.main()` 진입점부터 산출물 생성까지 chat/doc/execute 3 모드 모두 fixture 위에서 한 번씩.

1. **chat** — `--mode chat --srs-text "..." --target-url file://.../full_dsl.html`. Dify 는 monkeypatch 로 결정론적 14 스텝 시나리오 반환.
2. **doc** — 로컬 step parser (`parse_structured_doc_steps`) 가 인식하는 마크다운 형식의 fixture 파일 (`test/fixtures/spec.md`) 을 `--mode doc --file` 로 입력. Dify 호출 없이 통과.
3. **execute** — Phase 4 의 손작성 scenario.json 을 `--mode execute --scenario` 로 입력. 동일한 결과를 재현하는지 확인.

**Gate**: 3 모드 모두 종료코드 0 + 산출물 (`run_log.jsonl`, `scenario.json`, `scenario.healed.json`, `regression_test.py`, `index.html`) 5종 모두 생성.

#### Phase 7 — 에어갭/CI 봉쇄 (S3-12, S3-13)

가드와 CI 단계는 모든 테스트가 통과한 뒤에 추가한다. 미리 추가하면 임시로 외부 URL 을 쓰던 fixture 가 가드에 걸려 진척이 막힌다.

1. **S3-12 airgap 가드** — `test/test_airgap_guard.py` 1 케이스. `test/fixtures/*.html` 를 모두 읽어 정규식 `r"https?://[^/\s]"` (단 `https://www.w3.org/` 같은 namespace 제외 화이트리스트) 에 매칭되는 줄이 0 줄임을 검증.
2. **S3-13 Jenkins** — `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` 의 build 단계 직전에 integration/native 를 분리한 `python3 -m pytest` stage 추가. JUnit XML 두 개를 보존하고 실패 시 `unstable` 이 아니라 `fail`.

**Gate**: airgap 가드 PASS + Jenkins dry-run 시 pytest stage 가 실행되어 통과.

#### Phase 8 — 결과 요약 및 클로저 (S3-14)

1. 본 계획서 §7.4 표 14 건 모두 "완료" 로 갱신.
2. §7.6 DoD 8 항목을 ✅ 형태로 마감.
3. §6 의 "검증 결과" 섹션과 같은 양식으로 §7 끝에 "Sprint 3 검증 결과" 절 추가 — 단위테스트 총 건수, 신규 fixture 목록, 회귀 차단 범위 명시.
4. §8 milestone 의 Sprint 3 라인을 "구현 완료 — YYYY-MM-DD" 로 변경.
5. Sprint 4 (E2E) 가 인계받을 미해결 항목 (Convert 14대 확장, 실 Dify 검증 등) 을 별도 목록으로 분리.

**Gate**: PLAN 문서가 Sprint 3 종료 상태를 명시 → Sprint 4 착수 가능.

### 7.7 Sprint 3 종료 조건 (Definition of Done) — 모두 충족

- ✅ 14대 액션 모두 fixture 기반 pytest 케이스 ≥1 건 (S3-01~S3-06 + S3-08).
- ✅ Sprint 2 단위 16건 + Sprint 3 신규 31건 + native 30건 = **77건 PASS**, flake 0.
- ✅ 모든 fixture 가 file:// 만 참조 — `api.example.test` 화이트리스트는 mock-only 가상 호스트로 실네트워크 0 (S3-12 가드 자동 보장).
- ✅ `regression_test.py` 산출물이 별도 subprocess 에서 종료코드 0 + compileall 통과 (S3-07).
- ✅ fallback heal / mock fallback 모두 `step["target"]` 갱신을 통해 scenario_after 에 직렬화 (S3-09, S3-10).
- ✅ chat (Dify monkeypatch) / doc (로컬 parser, Dify 0 호출) / execute (손작성 + _validate_scenario) 3 모드 PASS (S3-11).
- ✅ Jenkins Stage 2.4 가 integration + native 두 sub-step 으로 구성, JUnit XML 두 개 보존 (S3-13).
- ✅ §7.4 표 14건 모두 "완료" — Sprint 4 가 E2E 통합에 착수 가능.

### 7.8 Sprint 3 비범위 (Out of Scope)

- 실제 Dify Brain (`gemma4:e4b`) 호출 검증 — Sprint 1 §5.5 책임.
- 실제 Mac agent / headed browser / 실 운영 도메인 — Sprint 4 책임.
- Convert(Flow 3) 경로의 14대 DSL 확장 — 별도 트랙 (현재 9대만 지원).
- LLM 응답 품질 평가 메트릭 — Sprint 1 의 Preview 실측 일부.
- regression_test.py 산출물의 운영 회귀 — Sprint 4 의 E2E 회귀에 포함.

### 7.9 fixture 설계 표준 (에어갭 강제)

신규 fixture 는 모두 다음 표준을 따른다.

- **위치**: `playwright-allinone/test/fixtures/<action>.html`.
- **URL**: 외부 도메인 0. 이미지/스크립트/CSS 도 inline 또는 data URI 만 허용. fixture 안 다른 fixture 참조도 상대경로로만.
- **fetch() 대상**: 실제 네트워크가 아니라 mock 패턴이 가로챌 가짜 endpoint (`/api/...`). 실호출은 mock 라우트가 잡으므로 외부에 안 나간다.
- **의도성**: 한 fixture 는 한 액션의 happy + 1~2 개 negative 만 담는다. 통합은 별도 fixture 또는 통합 케이스에서 조립.
- **헤더**: `<title>` 에 `[fixture] <action>` 명시 — 디버깅 시 어느 fixture 인지 즉시 식별.
- **검증 가드**: S3-12 가 모든 fixture HTML 에 `http://` / `https://` 가 없음을 정규식으로 강제.

## 8. Sprint 4 상세 작업목록

Sprint 4 는 **Sprint 1~3 에서 결정론적으로 잠근 14대 DSL 파이프라인이 실 운영 환경 (실 Dify Brain `gemma4:e4b` + 실 Mac agent + 실/통제 가능한 도메인) 에서 동일하게 동작함을 보장하는 최종 통합 스프린트** 다. 범위는 운영 의존성 회귀, 실 LLM 응답 회귀, 3-Flow E2E, mock_* 운영 검증, healing 실효성, Convert 경로 14대 확장, 운영 산출물/SLA, 매뉴얼 인계까지다. 이 스프린트가 닫히면 v4.1 은 운영 출시 가능 상태로 선언된다.

또한 Sprint 4 의 Phase 1 은 **Sprint 1 의 잔여 항목 (S1-23 `gemma4:e4b` Preview 실측)** 을 자동화된 회귀로 격상해 흡수한다. Sprint 1 은 Phase 1 통과 시 동시에 종료된다.

### 8.1 목표

- 실 Dify Brain (`gemma4:e4b`) 의 Planner/Healer 응답이 회귀 가능한 정확도 metric 으로 추적된다.
- 3-Flow (`chat`/`doc`/`convert`) 모두가 실 Jenkins + 실 Mac agent + 실 Dify 위에서 한 번 이상 PASS 한다.
- v4.1 의 진짜 차별점인 `mock_*` 가 운영 등급 페이지 (실 fetch() 호출) 에서 **UI 예외처리까지 검증** 한다.
- 3 단계 self-healing 이 실 LLM 환경에서 의미 있는 selector 를 반환해 회복함이 입증된다.
- Convert(Flow 3) 경로의 9대 → 14대 확장 (Sprint 2 비범위였던 carve-out) 이 닫힌다.
- 운영 산출물 (HTML report / run_log / scenario / healed / regression_test / screenshots / pytest XML) 이 빌드별 보존되고 회귀 추세가 추적된다.
- 동시 빌드 / 반복 실행 시 안정성 SLA 가 측정되어 운영 규약으로 명문화된다.

### 8.2 리뷰 분석내역

E2E 단계에서 빠지기 쉬운 함정을 사전에 식별한다.

- **실 LLM 회귀 부재**: Sprint 1 의 Preview 실측이 1 회성 수동 검증이라 회귀 차단력이 없다. Sprint 4 가 자동화 metric 으로 격상하지 않으면 운영에서 모델 변경 시 즉시 깨진다.
- **운영 API 오염 위험**: `mock_status`/`mock_data` 가 실수로 운영 API endpoint 패턴을 잡으면 회귀 테스트가 실 트래픽에 영향. 안전장치 없이 출시하면 위험.
- **봇 차단/dynamic content**: 실 운영 도메인 (Yahoo, Naver 등) 은 Playwright 봇 패턴을 captcha 로 차단하거나 SPA 렌더링이 늦어 false positive PASS 가 잦다. fixture 회귀로는 잡히지 않음.
- **healing 비결정성**: `gemma4:e4b` 의 heal 응답이 항상 의미 있는 selector 를 반환하는 건 아니다. heal 성공률이 운영 SLA 에 들어와야 신뢰 가능.
- **Convert 9대 잔존**: Sprint 2 가 carve-out 한 Convert 경로의 9대 DSL 만으로는 14대 시나리오를 녹화→실행 사슬이 깨진다. 사용자가 codegen 으로 upload/drag 를 녹화하면 변환되지 않음.
- **산출물 retention 미정**: Sprint 3 까지는 단발 빌드 산출물만. 운영에서는 회귀 추세 (성공률 / heal rate / SLA) 를 N 일 보관해야 모델 변경 영향 추적 가능.
- **Mac agent 동시성**: 단일 Mac agent 가 동시 빌드 2~3 개를 받으면 artifacts 디렉토리 충돌, 브라우저 자원 부족으로 false fail 발생.
- **운영 매뉴얼 누락**: README / architecture.md 가 v4.1 운영 절차 (DIFY_API_KEY 만료 처리, mock 안전장치, healing 비용 모니터링) 를 다루지 않으면 운영팀 인계 불가.

### 8.3 리뷰 보강내역

위 분석을 반영해 Sprint 4 를 다음과 같이 잡는다.

- **실 LLM 회귀 자동화**: Planner / Healer 입력셋을 N 개 고정해 정확도 metric 을 매 빌드마다 측정 (chat/doc 입력 셋, 깨진 selector 셋). Sprint 1 §5.5 잔여를 흡수.
- **mock 안전장치**: mock 패턴이 운영 API 호스트와 매칭 가능한 형태일 때 빌드 실패 또는 명시 confirmation 요구. allowlist 또는 prefix 검증.
- **운영 도메인은 통제 가능한 staging**: 봇 차단을 회피하기 위해 가능한 한 staging / dev 환경 도메인을 사용. 외부 도메인 사용 시 captcha/봇 차단 우회 정책을 명시.
- **healing SLA**: heal 성공률 / 평균 시간 / LLM 토큰 사용량을 metric 으로 수집. 운영 임계값 (예: heal 성공률 ≥ 60%, 평균 시간 ≤ 30s) 정의.
- **Convert 14대 확장**: `converter.py` 정규식에 `set_input_files`/`drag_to`/`scroll_into_view_if_needed`/`page.route` 매핑 추가, regression_generator 14대 emitter 와 1:1 일관성 회귀.
- **산출물 retention**: Jenkins archiveArtifacts + build trend 플러그인 또는 별도 dashboard 로 최근 N 일 추세. JUnit XML 로 pytest 추세 추적.
- **동시성 격리**: artifacts 디렉토리에 빌드 번호 prefix, 브라우저 동시 실행 수 제한. 동시 빌드 2 개 회귀 케이스를 명시적 검증.
- **운영 매뉴얼**: 트러블슈팅 / 운영 절차 / SLA 표를 README / architecture.md 에 별도 섹션으로 삽입.

### 8.4 세부 체크리스트

| ID | 작업 | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- | --- |
| S4-01 | Mac agent 운영 점검 체크리스트 | 본 계획서 §8.8, `mac-agent-setup.sh` 보강 | Java 17 / 권한 / 디스크 / `devops-net` / 디렉토리 / 환경변수 가 빌드 시작 직전에 자동 검증되는 stage 가 Jenkinsfile 에 존재 | 미착수 |
| S4-02 | 실 Dify endpoint health check 자동화 | Jenkinsfile, 본 계획서 | 빌드 시작 시 `/v1/parameters` 또는 동등 endpoint 로 모델 응답 시간 + API key 유효성 + Credentials 정합 검증 stage 통과해야 진행 | 미착수 |
| S4-03 | Planner 응답 정확도 회귀 | `test/e2e/test_planner_accuracy.py`, 입력셋 fixture | 14대 액션이 골고루 들어간 SRS prompt 셋 ≥10 개에 대해 `_validate_scenario` 통과율 + 액션 타입 정확도 측정. 빌드별 추이 기록 | 미착수 |
| S4-04 | Healer 응답 정확도 회귀 | `test/e2e/test_healer_accuracy.py`, 입력셋 fixture | 의도적으로 깨뜨린 selector + DOM snapshot 셋 ≥10 개에 대해 heal 응답이 valid Playwright selector 를 반환하는 비율 측정. SLA 임계값 정의 | 미착수 |
| S4-05 | LLM SLA 측정 | `test/e2e/test_llm_sla.py`, build trend | Planner/Healer 응답 시간 p50/p95/p99 + 타임아웃 발생률 + 재시도 카운트가 빌드별로 기록. 임계값 (p95 ≤ 30s, 타임아웃 ≤ 5%) 정의 | 미착수 |
| S4-06 | chat E2E (실 Dify + Mac agent) | `test/e2e/test_chat_flow.py` 또는 Jenkins job 산출물 | Jenkins UI 입력 → 실 Dify 호출 → Mac agent 실행 → 산출물 7종 생성 → 종료코드 0. staging 도메인 사용 | 미착수 |
| S4-07 | doc E2E (실 PDF/Docx 첨부) | `test/e2e/test_doc_flow.py`, 샘플 기획서 | 실 PDF 기획서를 `--mode doc --file` 로 입력 → Dify Parser → Planner LLM → 시나리오 추출 → 실행 → PASS | 미착수 |
| S4-08 | convert E2E (실 codegen 녹화) | `test/e2e/test_convert_flow.py`, 녹화 스크립트 | 사용자가 로컬 `playwright codegen` 으로 녹화한 실 `.py` 파일을 `--mode convert --file` 로 입력 → 14대 DSL 변환 → 실행 → PASS | 미착수 |
| S4-09 | mock_status 운영 검증 | `test/e2e/test_mock_status_prod.py`, staging 페이지 | 실 fetch 호출 페이지에서 `mock_status 500` → DOM 에 에러 UI 노출 → `verify` PASS. 운영 API 호스트와 패턴 충돌 없음 | 미착수 |
| S4-10 | mock_data 운영 검증 | `test/e2e/test_mock_data_prod.py`, staging 페이지 | 실 fetch 페이지에서 `mock_data` 빈 배열 → "데이터 없음" UI 노출 → `verify` PASS. dict / JSON 문자열 / 평문 모두 운영에서 정상 fulfill | 미착수 |
| S4-11 | mock route 안전장치 | `zero_touch_qa/__main__.py` 또는 `executor.py` | mock 패턴이 사전 정의된 차단 호스트 목록 (예: 운영 API 도메인) 과 매칭 시 빌드 실패 또는 명시 confirmation 환경변수 요구 | 미착수 |
| S4-12 | 3 단계 healing 실효성 회귀 | `test/e2e/test_healing_real_llm.py` | 깨진 selector → fallback PASS / fallback 미존재 → LocalHealer PASS / 둘 다 실패 → 실 Dify heal PASS 의 3 케이스 ≥1 회 이상 통과. heal 성공률 ≥60% | 미착수 |
| S4-13 | LLM heal 비용/시간 SLA | build trend, `test/e2e/test_heal_sla.py` | heal 트리거 시 평균 시간 / LLM 토큰 사용량 / 성공률이 빌드별 기록. 임계값 미달 시 unstable. | 미착수 |
| S4-14 | Convert 14대 확장 | `zero_touch_qa/converter.py` | `set_input_files`/`drag_to`/`scroll_into_view_if_needed`/`page.route` 정규식 매핑 추가. regression_generator 의 14대 emitter 와 1:1 일관성 단위테스트 통과 | 미착수 |
| S4-15 | Jenkins archiveArtifacts 7종 보존 | `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` | HTML report / run_log / scenario / healed / regression_test / screenshots / pytest XML 7 종이 빌드별로 archive. 최근 30 일 retention | 미착수 |
| S4-16 | 빌드 추세 / 회귀 dashboard | Jenkins build trend, JUnit plugin | pytest 통과율 / healing rate / Planner 정확도가 최근 N 빌드 단위로 시각화. 회귀 시 즉시 식별 가능 | 미착수 |
| S4-17 | 동시 빌드 / 반복 안정성 회귀 | `test/e2e/test_concurrency.py` 또는 Jenkins matrix | 동시 빌드 2 개 시 artifacts 디렉토리 충돌 없음 + 동일 시나리오 10 회 반복 시 flake rate ≤5% | 미착수 |
| S4-18 | 운영 매뉴얼 갱신 | `README.md`, `architecture.md` §5.8, 트러블슈팅 | DIFY_API_KEY 갱신 절차 / mock 안전장치 사용법 / healing 비용 모니터링 / SLA 표 / Mac agent 권한 / 동시성 한계 가 운영 섹션으로 삽입 | 미착수 |
| S4-19 | Sprint 1 §5.5 종료 흡수 | 본 계획서 §5.5, §8 | Phase 1 (S4-03 / S4-04) 통과 시 Sprint 1 의 S1-23 (Preview 실측) 이 자동화 회귀로 격상되어 Sprint 1 종료 선언 가능 | 미착수 |
| S4-20 | v4.1 운영 출시 선언 | 본 계획서 §8 끝, `architecture.md` 헤더 | Sprint 4 모든 항목 닫힌 시점에 v4.1 closure 선언, 다음 트랙 (Convert path 추가 확장 / 모바일 / 다국어 등) 을 별도 backlog 로 분리 | 미착수 |

### 8.5 구현 순서 권장안

전체를 9 phase 로 나눈다. 각 phase 의 산출물이 다음 phase 의 입력이 되므로 **순서를 지킨다**. phase 마다 "다음으로 넘어가지 않을 조건" 을 명시한다.

#### Phase 0 — 운영 환경 사전점검 (S4-01, S4-02)

코드 변경 없이 실 운영 의존성이 살아있는지부터 확인한다. 여기서 막히면 후속 phase 가 모두 false fail.

1. Mac agent 의 Java 17 / 권한 / 디스크 / `devops-net` 연결 / 디렉토리 / 환경변수 6 항목을 자동 검증하는 stage 를 Jenkinsfile 의 build 시작 직전에 추가.
2. 실 Dify endpoint 의 모델 응답 시간 + API key 유효성 + Jenkins Credentials 정합 (`DIFY_API_KEY`, `DIFY_BASE_URL`, `MOCK_BLOCKED_HOSTS` 등) 을 health check stage 에서 검증.
3. 어느 하나라도 실패하면 빌드 즉시 중단 (후속 phase 진입 금지).

**Gate**: Mac agent + Dify endpoint health check 가 100% 통과해야 다음 phase. fixture 변경 0, executor 변경 0.

#### Phase 1 — 실 Dify 회귀 자동화 (S4-03, S4-04, S4-05, S4-19)

Sprint 1 §5.5 잔여를 흡수해 회귀 가능한 metric 으로 격상.

1. **S4-03 Planner 정확도** — 14대 액션이 골고루 들어간 SRS prompt 셋 ≥10 개를 `test/e2e/fixtures/planner_inputs/` 에 고정. 각 입력에 대해 `_validate_scenario` 통과율 + 기대 액션 타입 정확도 측정. 결과를 `artifacts/planner_accuracy.json` 에 기록.
2. **S4-04 Healer 정확도** — 깨진 selector + DOM snapshot 셋 ≥10 개를 `test/e2e/fixtures/healer_inputs/` 에 고정. heal 응답이 valid Playwright selector 형태인지 + DOM 에 실제 매칭 가능한지 검증.
3. **S4-05 SLA 측정** — Planner/Healer 응답 시간 p50/p95/p99 + 타임아웃 발생률 + 재시도 카운트를 빌드별로 JSON 으로 저장. 임계값 정의: Planner p95 ≤ 30s, Healer p95 ≤ 30s, 타임아웃 ≤ 5%.
4. **S4-19 Sprint 1 종료** — Phase 1 통과 시점에 Sprint 1 의 S1-23 / S1-25 를 모두 "완료" 로 갱신, §5.5 에 Sprint 1 종료 선언 추가.

**Gate**: Planner 정확도 ≥80%, Healer valid selector 비율 ≥70%, SLA 임계값 통과. 미달 시 prompt 보강 (Sprint 1 회귀) 또는 입력셋 조정 후 재시도.

#### Phase 2 — 3-Flow E2E (S4-06, S4-07, S4-08)

`__main__.main()` 진입점부터 산출물 생성까지 chat/doc/convert 3 모드 모두 실 운영 의존성으로 한 번씩.

1. **S4-06 chat** — Jenkins UI 에서 `MODE=chat`, `SRS_TEXT="..."`, `TARGET_URL=<staging>` 입력 → 실 Dify 호출 → Mac agent 실행 → 산출물 7종 생성 → 종료코드 0.
2. **S4-07 doc** — 샘플 PDF 기획서 (`test/e2e/fixtures/spec_sample.pdf`) 를 `--mode doc --file` 로 입력 → Dify Parser → Planner → 시나리오 → 실행. 14대 액션이 추출되는지 확인.
3. **S4-08 convert** — 사용자가 로컬에서 `playwright codegen` 으로 녹화한 실 `.py` 파일 (`test/e2e/fixtures/recorded_real.py`) 을 입력 → 9대 DSL 변환 → 실행. **이 시점에서 14대 미지원 (S4-14 가 닫기 전)** — 녹화 시 9대 액션만 사용된 시나리오로 검증.

**Gate**: 3 모드 모두 종료코드 0 + 산출물 7종 (HTML report, run_log, scenario, healed, regression_test, screenshots, pytest XML) 빠짐없이 생성. convert 는 9대 한정.

#### Phase 3 — mock_* 운영 검증 (S4-09, S4-10, S4-11)

v4.1 의 진짜 차별점이 운영에서도 동작하는지 검증. 이 phase 의 PASS 가 v4.1 의 가치 보장.

1. **S4-09 mock_status 500 + UI 에러** — staging 페이지에 `fetch('/api/users/1')` 호출 + 5xx 시 에러 UI 노출 코드를 배포. 시나리오: `mock_status 500 → navigate → verify error UI visible` 4 스텝 → PASS.
2. **S4-10 mock_data 빈 응답 + UI 처리** — staging 페이지에 `fetch('/api/list')` 응답을 list 로 렌더 + 빈 배열 시 "데이터 없음" UI 노출 코드 배포. 시나리오: `mock_data {"items":[]} → navigate → verify "데이터 없음"` PASS.
3. **S4-11 mock route 안전장치** — `__main__.py` 또는 `executor.py` 에 mock 패턴이 사전 정의된 차단 호스트 목록 (`MOCK_BLOCKED_HOSTS` 환경변수 또는 config) 과 매칭 시 빌드 즉시 실패. 명시 confirmation (`MOCK_OVERRIDE=1`) 없이는 우회 불가.

**Gate**: 3 케이스 모두 PASS + mock 안전장치가 운영 API 호스트 패턴을 실제로 차단함을 별도 negative 케이스로 검증.

#### Phase 4 — Healing 실효성 (S4-12, S4-13)

Sprint 3 의 monkeypatch 회귀가 실 LLM 환경에서도 의미 있는 회복을 만드는지 검증.

1. **S4-12 3 단계 트리거** — staging 페이지에 의도적으로 깨진 selector 를 두고 시나리오 작성. 케이스 A: `fallback_targets` 로 복구. 케이스 B: fallback 미존재 → LocalHealer 로 복구. 케이스 C: 둘 다 실패 → 실 Dify heal 로 복구. 3 케이스 모두 1 회 이상 PASS.
2. **S4-13 heal SLA** — heal 트리거 시 평균 시간 / LLM 토큰 사용량 / 성공률을 `artifacts/heal_metrics.json` 에 기록. 임계값: heal 성공률 ≥60%, 평균 시간 ≤30s. 미달 시 빌드 unstable.

**Gate**: 3 단계 healing 모두 ≥1 회 트리거 + 회복 성공 + SLA 임계값 통과.

#### Phase 5 — Convert 14대 확장 (S4-14)

Sprint 2 가 carve-out 한 Convert 경로를 닫는다.

1. `zero_touch_qa/converter.py` 의 정규식 dispatch 에 `set_input_files` (→ `upload`), `drag_to` (→ `drag`), `scroll_into_view_if_needed` (→ `scroll`), `page.route` (→ `mock_status` 또는 `mock_data`) 매핑 추가.
2. 단위테스트 신설: `test/test_converter_14_actions.py` — Playwright codegen 출력 샘플 (`examples/playwright_dev_convert_input.py` 확장) 을 14대 모두 포함하는 형태로 만들고 변환 결과가 14대 DSL 시나리오와 1:1 매칭.
3. regression_generator 의 emitter 와 1:1 일관성 회귀 (입력 = 변환 결과 = 회귀 산출물 코드 동일성 검증).

**Gate**: 14대 모두 변환 + regression_generator 일관성 PASS. Convert E2E 재실행 (Phase 2 의 S4-08 을 14대 녹화로 다시) → 종료코드 0.

#### Phase 6 — 운영 산출물 / 모니터링 (S4-15, S4-16)

산출물 보존과 추세 추적은 운영 출시의 전제조건이다.

1. **S4-15 archiveArtifacts** — Jenkinsfile 의 post stage 에 `archiveArtifacts artifacts: 'artifacts/**/*', fingerprint: true`. retention 30 일.
2. **S4-16 build trend** — JUnit plugin 으로 pytest 추세, build trend 플러그인으로 success rate, custom step 으로 `planner_accuracy.json` / `heal_metrics.json` 시각화. 또는 Grafana 대시보드 1 개 신설.

**Gate**: 30 일 retention 보장 + 최근 N 빌드의 추세 페이지 접근 가능.

#### Phase 7 — 부하 / 안정성 (S4-17)

운영 SLA 보장.

1. Jenkins matrix 또는 동시 빌드 2 개 트리거 → artifacts 디렉토리 충돌 없음 (빌드 번호 prefix 적용 검증).
2. 동일 시나리오 10 회 반복 실행 → flake rate ≤5%. 실패한 1~2 회의 패턴 분석 (네트워크 / 봇 차단 / heal 비결정성) 후 root cause 명문화.

**Gate**: 동시 빌드 충돌 0 + flake rate ≤5%. 미달 시 root cause 별 mitigation (artifacts prefix / retry / 봇 회피) 적용 후 재측정.

#### Phase 8 — 인계 / 운영 매뉴얼 / 클로저 (S4-18, S4-20)

1. **S4-18 운영 매뉴얼** — README / architecture.md 의 §5.8 운영 섹션에 다음 추가: DIFY_API_KEY 갱신 절차, mock 안전장치 사용법, healing 비용 모니터링, SLA 표, Mac agent 권한, 동시성 한계, 트러블슈팅.
2. **S4-20 v4.1 closure** — 본 계획서 §8 끝에 "Sprint 4 검증 결과" 절 추가 (단위테스트 / E2E / SLA / Convert 14대 확장 결과 모두 명시), §9 milestone 의 Sprint 4 라인을 "구현 완료 — YYYY-MM-DD" 로 변경. v4.1 운영 출시 선언. 다음 트랙 (모바일 gesture / 다국어 / 다중 브라우저 등) 은 별도 backlog 로 분리해 본 PLAN 종료.

**Gate**: 운영 매뉴얼이 운영팀 인계 가능한 수준 + PLAN 문서가 v4.1 closure 상태.

### 8.6 Sprint 4 종료 조건 (Definition of Done)

- 실 Dify Brain (`gemma4:e4b`) Planner/Healer 정확도 ≥80% / ≥70% 가 회귀 가능한 metric 으로 측정된다.
- 3-Flow (chat/doc/convert) 모두가 실 Jenkins + Mac agent + Dify 위에서 종료코드 0 + 산출물 7종 보존.
- mock_* 가 staging 운영 페이지에서 UI 예외처리 시나리오까지 PASS + 운영 API 차단 안전장치 작동.
- 3 단계 healing 모두 1 회 이상 트리거 + 회복 성공 + SLA 임계값 통과 (heal 성공률 ≥60%, 평균 시간 ≤30s).
- Convert 경로의 14대 확장 완료 + regression_generator 일관성 회귀 PASS.
- archiveArtifacts 7 종 30 일 retention + 빌드 추세 dashboard 접근 가능.
- 동시 빌드 충돌 0 + 반복 flake rate ≤5%.
- README / architecture.md / 트러블슈팅에 v4.1 운영 매뉴얼 인계 가능 수준으로 갱신.
- Sprint 1 의 S1-23 / S1-25 가 Phase 1 흡수로 닫힘.
- 본 계획서 §8.4 표 20 건 모두 "완료" + §9 milestone Sprint 4 "구현 완료" 로 갱신 + v4.1 출시 선언.

### 8.7 Sprint 4 비범위 (Out of Scope)

- v4.2 / v5.0 신규 기능 — 별도 프로젝트.
- 모바일 gesture (tap/swipe/pinch) DSL 추가 — 별도 backlog.
- 다국어 페이지 회귀 (영어/일본어 fixture) — 별도 backlog.
- 다중 브라우저 지원 (Firefox/WebKit) — 현재 chromium 한정 유지.
- 보안 회귀 / penetration test / 권한 분리 - 별도 트랙.
- 분산 / 멀티 region Jenkins 배포 — 별도 인프라 트랙.
- LLM 모델 교체 (Llama / Qwen 등) 비교 평가 — 별도 R&D 트랙.

### 8.8 운영 회귀 표준 (SLA / Retention / 알림)

Sprint 4 통과 후 운영 출시 시 적용할 표준.

#### 8.8.1 SLA

| 지표 | 임계값 | 측정 위치 | 미달 시 |
| --- | --- | --- | --- |
| Planner 응답 정확도 | ≥80% | `artifacts/planner_accuracy.json` | 빌드 unstable, 입력셋 / prompt 재검토 |
| Healer valid selector 비율 | ≥70% | `artifacts/healer_accuracy.json` | 빌드 unstable, healer prompt 재검토 |
| Planner / Healer 응답 시간 p95 | ≤30s | `artifacts/llm_sla.json` | 빌드 unstable, 모델 / hardware 재검토 |
| LLM 타임아웃 발생률 | ≤5% | `artifacts/llm_sla.json` | 빌드 unstable, 재시도 정책 재검토 |
| heal 성공률 | ≥60% | `artifacts/heal_metrics.json` | 빌드 unstable, healer 입력 재검토 |
| 동일 시나리오 flake rate | ≤5% | 반복 실행 stage | 빌드 unstable, root cause 분석 |
| 동시 빌드 artifacts 충돌 | 0 건 | Jenkins matrix | 빌드 fail, prefix 정책 재검토 |
| pytest 통과율 (Sprint 2/3 회귀) | 100% | JUnit XML | 빌드 fail (즉시 중단) |

#### 8.8.2 Retention

- archiveArtifacts: 30 일 (HTML report / run_log / scenario / healed / regression_test / screenshots / pytest XML).
- build trend: 최근 100 빌드.
- LLM metric JSON: 최근 100 빌드 (회귀 추세 분석용).

#### 8.8.3 알림 (선택)

- 빌드 fail / unstable 시 Slack 채널 알림 (Jenkins notification plugin).
- LLM SLA 임계값 미달이 연속 3 빌드 발생 시 운영팀 escalation.
- mock 안전장치 trigger 시 빌드 fail + 알림.

#### 8.8.4 운영 책임 분담

| 영역 | 담당 |
| --- | --- |
| Dify Brain prompt / 모델 | Sprint 1 책임자 |
| Python Executor / DSL 계약 | Sprint 2 책임자 |
| pytest 회귀 / fixture | Sprint 3 책임자 |
| 운영 SLA / 인프라 / 매뉴얼 | Sprint 4 책임자 |

## 9. 단계별 실행 일정 (Milestones)

- **Sprint 1 (진행 중)**: Dify Workflow(Planner/Healer) 프롬프트 고도화, 하드웨어 기준선 정리, prompt budget 축소, `dify-chatflow.yaml` / `architecture.md` / 계획서 기준선 동기화까지 완료. 남은 일은 `gemma4:e4b` 기준 Planner/Healer Preview 실측 검증인데, Sprint 4 Phase 1 (S4-03 / S4-04) 이 자동화 회귀로 격상해 흡수한다 (S4-19).
- **Sprint 2 (구현 완료)**: Python Executor(`executor.py`)에 신규 5종 DSL(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`) 매핑 로직 구현 + `regression_generator.py` 14대 확장 + execute 모드 구조 검증 일관화 + mock 라우트 healing 경로 신설 완료.
- **Sprint 3 (구현 완료 — 2026-04-27)**: §7 의 14건 작업 모두 닫힘. 14대 DSL fixture 기반 pytest (Sprint 2 16 + Sprint 3 31 + native 30 = 77 PASS), mock_* UI 예외처리, 3-Flow 통합, regression_test.py subprocess 실행 검증, airgap 가드, Jenkins Stage 2.4 추가까지 완료.
- **Sprint 4 (진행 예정)**: §8 의 20건 작업으로 실 Dify + Mac agent + 운영 도메인 위에서 14대 DSL E2E, healing 실효성, Convert 14대 확장, 운영 SLA / archiveArtifacts / 매뉴얼까지 닫고 v4.1 운영 출시 선언.
