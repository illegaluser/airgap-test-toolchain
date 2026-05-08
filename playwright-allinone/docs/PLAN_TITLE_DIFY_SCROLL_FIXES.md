# PLAN — `get_by_title` 변환 / Dify 연결 진단 / 스크롤 step 위치 지정 (단기 hotfix)

브랜치: 본 변경 묶음용 신규 작업.

## 진행 상태 — 2026-05-05 시점

- (A) `get_by_title` + `get_by_alt_text` 변환 — **완료**.
  - `converter_ast.py` `_segments_to_target` 에 dict 디스패치로 통합 (text /
    label / placeholder / testid / title / alt 6종). 복잡도 113 → 73 으로 감소.
  - `converter.py` line fallback 에 `get_by_title` / `get_by_alt_text` regex
    추가.
  - `locator_resolver.py` 의 3 개 prefix dict 를 모듈 상수
    `_SEMANTIC_PREFIX_TO_METHOD` 로 통합 + `_ROLE_NAME_RE` 상수 추출 (S1192
    중복 제거).
  - 회귀: corpus 9 패턴 (신규 `09_title_alt` 포함) + 14-DSL E2E 모두 통과.
  - 본 사례 `original.py` 직접 변환 결과 — step 5 → 6 으로 복원, step 4 가
    `title=개인정보처리방침` 으로 정확히 캡처됨.
- (B) Dify ConnectionError 진단 메시지 — **완료**.
  - `dify_client.py` 에 `_format_dify_error` 헬퍼 추가. ConnectionError 시
    "dscore.ttc.playwright 컨테이너가 떠 있는지, 또는 DIFY_BASE_URL 가 맞는지
    확인하세요" 가이드 prepend + 원본 메시지 보존. 다른 RequestException 은
    기존 메시지 그대로.
  - 회귀: `test_dify_metrics.py` 6 PASS.
- (C0b) 스크롤 step 위치 지정 — **완료**.
  - 백엔드: `AssertionAddReq.position` (Optional[int]) 추가, `add_assertion`
    이 None ⇒ 끝에 append, 정수 ⇒ 1-base 위치에 insert + 후속 step 번호
    재할당. `_resolve_insert_index` 헬퍼로 분리해 복잡도 회귀 방지.
  - 프론트: `index.html` 의 assertion form 에 `position` number input 추가,
    `app.js` submit handler 가 빈 입력은 미전송, 정수면 검증 후 payload 에
    포함.
  - 회귀: `test_recording_service.py` 140 PASS (기존 135 + 신규 5
    position 케이스). UI e2e 의 step-add-form 케이스 2 PASS.
- 운영 매뉴얼: `operations.md` §10 트러블슈팅 표에 두
  항목 추가 (Dify 연결 실패 / codegen 이 wheel 안 잡힘).

> **본 문서는 단기 hotfix 묶음입니다.** 근본 해결 (codegen 의존 제거 — 자체
> recorder) 은 별도 PLAN [`PLAN_CUSTOM_RECORDER.md`](PLAN_CUSTOM_RECORDER.md)
> 로 분리. 이 hotfix 는 본 사례 즉시 해소를 목적으로 하며, R-Phase 가 완료
> 되면 일부 변경 (특히 (A) `get_by_title` 분기, (C0b) manual scroll API) 은
> 의존성이 사라져 deprecation 후보가 됩니다.

## 배경 — 실 시나리오에서 드러난 3 갈래 결함

녹화 세션 `~/.dscore.ttc.playwright-agent/recordings/919657543d0a/` 에서 step
4 가 FAIL 했고, 치유도 동작하지 않았다. 결함은 서로 독립적이며 각각 다른
모듈에 위치한다.

### 사실 (검증된 것만)

1. `original.py` 는 `page1.get_by_title("개인정보처리방침").click()` 을 포함.
   `codegen_run_log.jsonl` 에도 `internal:attr=[title="개인정보처리방침"i]`
   클릭 step 이 기록됨 (codegen 자체는 제대로 캡처).
2. `scenario.json` 의 step 수 = 5. `original.py` 의 click 수 = 6 (개인정보
   처리방침 1개 누락). [converter_ast.py:604-728](../zero_touch_qa/converter_ast.py#L604-L728)
   `_segments_to_target` 가 처리하는 locator 메서드 set 에 `get_by_title` 없음.
   매칭 실패 시 segment 가 무시되고 target 이 빈 문자열 → 함수가 None 반환 →
   `_handle_expr` 가 step 자체를 silent drop. 라인 fallback
   ([converter.py:248-281](../zero_touch_qa/converter.py#L248-L281)) 도 동일하게 미지원.
3. `scenario.json` step 4 (`role=link, name=~ 2026.3.2 적용지침(클릭)`) 는 사이트
   상에서 "개인정보처리방침" 메뉴를 펼쳐야 보이는 링크. 선행 step 누락으로
   실행 시 닫힌 메뉴 안의 링크를 못 찾아 실패.
4. 치유 시도 실패 — `llm_calls.jsonl` 에 `HTTPConnectionPool(host='localhost',
   port=80): ... Connection refused`. 호스트의 LISTEN 포트 점검 결과 80 번
   미점유. [config.py:56](../zero_touch_qa/config.py#L56) 기본값 `DIFY_BASE_URL=
   http://localhost/v1`. 컨테이너 nginx 가 `/v1` → `127.0.0.1:5001` 로 프록시
   ([nginx.conf:31-32](../nginx.conf#L31-L32)) 하므로 dscore 컨테이너가 호스트의 80
   에 매핑돼야 동작. 현재 호스트에는 컨테이너가 안 떠 있음.
5. trace.zip 의 이벤트 종류: `[after, click, close, console, context-options,
   frame-snapshot, goto, input, log, newPage, page, pageClosed,
   screencast-frame, waitForEventInfo]`. wheel/scroll 이벤트 없음. trace.zip 은
   `original.py` 재실행을 기록하는 것이라 user 의 codegen 중 wheel 은 처음부터
   포착 대상이 아님. codegen .py output 에도 wheel 이 emit 되지 않음
   ([recording_service/server.py:1197](../recording_service/server.py#L1197) 주석
   에 명시).

## A — `get_by_title` 변환 지원

### 의사결정

scenario.json 의 selector DSL 에 신규 prefix `title=<value>` 추가. converter
2 경로 (AST 우선 + line fallback) + locator_resolver 의 기본/raw/chain 3 경로
모두 동일 prefix 처리.

### 대안 검토

| 대안 | 채택 | 사유 |
| --- | --- | --- |
| **A1**: `title=` prefix 신설 (Playwright `get_by_title` 매핑) | ✅ | 다른 semantic prefix (`text=`, `label=`, `placeholder=`, `testid=`) 와 동형 — 추가 비용 최소, executor·resolver 의 dispatch 분기를 자연 확장 |
| A2: `[title="X"]` 같은 raw CSS 로 변환 | ✗ | Playwright `get_by_title` 은 i18n / aria 호환을 자체 처리. CSS 셀렉터로 강등하면 강등 의미 손실 + healing 분기에서도 의미 정보가 사라짐 |
| A3: title 대신 `role=` + `name=` 으로 추정 | ✗ | 사이트의 anchor 가 title 만 있고 role / accessible name 미설정인 케이스가 많음 (실 사례). 정보 손실 |

### 구현 범위

- [converter_ast.py](../zero_touch_qa/converter_ast.py)
  `_segments_to_target` 에 `get_by_title` 분기 추가 → `title=<value>`.
- [converter.py](../zero_touch_qa/converter.py)
  line fallback 의 regex 표 (248-281 line 영역) 에 `get_by_title` 분기 추가.
- [locator_resolver.py](../zero_touch_qa/locator_resolver.py)
  `_resolve_semantic_prefix` / `_raw_semantic_prefix` / `_apply_chain_segment`
  세 dict 에 `"title=": "get_by_title"` 추가.
- [`__main__.py`](../zero_touch_qa/__main__.py) 의 valid action / target schema
  검증이 prefix 화이트리스트를 갖는지 확인 — 갖지 않으면 변경 없음.

### 검증

1. **단위**: 새 시나리오 fixture (`page.get_by_title("X").click()`) → AST 변환
   결과 step.target == `title=X`. 기존 `get_by_title` 미지원 케이스의 silent
   drop 회귀 방지.
2. **통합**: 본 사례 `original.py` 를 변환 → scenario step 수가 6 으로 증가
   하고 step 4 가 `title=개인정보처리방침` 인지 확인.
3. **executor**: locator_resolver 에 fixture 페이지 (`<a title="hi">x</a>`)
   띄우고 `title=hi` resolve → element 매칭 확인.

### 성공 기준

- AST 변환에서 `get_by_title` 호출이 step 으로 보존됨 (drop 0 건).
- locator_resolver 가 `title=` 단일 / `... >> title=` chain / `, nth=N`
  modifier 와 결합된 형태 모두 매칭.
- 기존 다른 prefix 회귀 0 건.

## B — Dify 연결 진단 개선

### 의사결정

코드 변경은 *진단 메시지 한정*. endpoint 자동 fallback / health-check 를 코드
에 박아넣지 않는다 (운영 환경마다 Dify 위치가 다르고, host:port 는 외부
설정의 영역).

호스트 → 컨테이너 매핑이 끊겼을 때 리포트의 `llm_calls.jsonl` 만 보고는 운영자가
원인을 즉시 못 잡는다. 따라서 다음 두 가지 *낮은 위험* 변경:

### 대안 검토

| 대안 | 채택 | 사유 |
| --- | --- | --- |
| **B1**: dify_client 에 connection-refused / host-unreachable 분기 → 한국어 가이드 메시지 ("Dify 컨테이너 미가동 또는 `DIFY_BASE_URL` 점검") + run_log/리포트 표면 | ✅ | 코드 수정 작음, 운영자 자가 진단 가능 |
| **B2**: zero_touch_qa 시작 시 preflight (`GET /v1`/`/health`) → 실패 시 healer 호출 단계에서 즉시 graceful skip | ✗ (보류) | preflight 한 번에 5xx/4xx 응답 양상이 환경별로 달라 false-negative 위험. B1 로 충분 |
| B3: `DIFY_BASE_URL` 기본값 변경 / multi-fallback (`localhost:80`, `localhost:5001`) | ✗ | 정책 결정 — 명시적 env 가 더 안전. 운영 매뉴얼에서 안내 |
| B4: README / 운영 매뉴얼 한 줄 추가 — Dify endpoint 의 의미와 컨테이너 의존성 | ✅ | 문서로 충분 |

### 구현 범위

- [dify_client.py](../zero_touch_qa/dify_client.py)
  POST 실패 분기에서 `requests.exceptions.ConnectionError` 의
  `Connection refused` / `Name or service not known` 만 잡아 운영자용 한국어
  가이드를 error 메시지에 prepend. 기존 raw 메시지는 보존 (디버깅용).
- README 또는 `operations.md` 에 Dify endpoint 항목 한 줄
  추가 — 본 PLAN 작업과는 별개로 link 만 추가.

### 검증

1. dify_client `_call_chat_messages` 를 `localhost:1` (확실히 닫힌 포트) 로
   호출 → error 메시지에 안내 문구 포함, 기존 stack trace 도 보존.
2. 정상 endpoint (mock HTTP) 로 호출 → 행동 변화 없음.

### 성공 기준

- ConnectionError 발생 시 운영자가 메시지만 보고 (a) 컨테이너 기동 (b) env
  교정 중 어느 쪽인지 1차 판정 가능.
- 기존 healer 정상 동작 회귀 0 건.

## C0b — 스크롤 step 위치 지정 추가

### 의사결정 — C0b 만 본 작업에 포함

자동 wheel 캡처 (이전 분류의 C1) 는 [`PLAN_CUSTOM_RECORDER.md`](PLAN_CUSTOM_RECORDER.md)
로 분리. 본 라운드는 운영자 수동 보정 경로 한정.

### 사실 — 왜 자동 캡처가 본 PLAN 밖인가

- `playwright codegen` 은 user 의 wheel/scroll 을 .py 로 emit 하지 않음.
- `--save-trace` 같은 codegen 옵션 부재 (CLI help 1.57.0 검증).
- Playwright Python API 의 `BrowserContext` 에 recorder 제어 hook 미노출
  (`_har_recorders` 만 존재).
- 따라서 자동 캡처는 codegen 자체 대체 (자체 chromium 런처 + DOM 이벤트
  리스너 init_script + 셀렉터 추론 + 시나리오 빌더) 가 필요. 이는 별도 PLAN.

### 대안 검토 (C0a / C0b / C0c)

| 대안 | 채택 | 사유 |
| --- | --- | --- |
| C0a: UI 에 "끝에 추가" 버튼만 — 백엔드 변경 없음 | ✗ | 본 사례처럼 *중간 삽입* 이 필요한 흔한 케이스에 무력. 끝에 추가 후 사용자가 JSON 수동 편집해야 함 |
| **C0b**: `position: int` 필드 추가 + 백엔드에서 step 번호 재할당 + UI 입력 | ✅ | 추가 코드 ~30 라인. 본 사례 직접 해결 |
| C0c: 전체 reorder UI (drag-drop) | ✗ (보류) | 본 라운드의 즉시 필요 없음 (YAGNI). 자체 recorder 구현 후 재검토. 그때는 reorder 자체가 의미 줄어듦 (recorder 가 처음부터 정확히 캡처) |

### 구현 범위 (C0b)

**백엔드** ([server.py:1209-1301](../recording_service/server.py#L1209-L1301)):

- `AssertionAddReq` 에 optional `position: int | None` 필드 추가 (1-base step
  번호. None = 기존대로 끝에 append).
- `add_assertion` 에 분기: position 지정 시 해당 위치에 insert + 후속 step 들의
  `step` 번호 +1 재할당.

**프론트** ([index.html](../recording_service/web/index.html) /
[app.js](../recording_service/web/app.js)):

- Scenario 카드에 "스크롤 step 추가" 버튼.
- 다이얼로그: target (셀렉터), position (정수 입력 — 기본 = 마지막 step + 1).
  value 는 `into_view` 로 고정.
- 추가 후 scenario 카드 자동 갱신.

**문서**:

- 운영 매뉴얼에 "codegen 은 wheel 을 기록하지 않음 — 필요 시 본 버튼으로
  수동 추가. 자동 캡처는 R-Phase 진행 중" 한 줄.

### 검증

1. UI 에서 position=2 로 scroll 추가 → scenario.json 의 새 scroll step 이 step
   2 자리에 들어가고, 기존 step 2~N 들의 `step` 번호가 +1 으로 재할당.
2. position 미지정 (UI 가 빈 값 전송) 시 기존 동작 (끝에 append) 그대로.
3. position 이 음수 / 0 / `len+2` 이상 → 400 에러.
4. 추가된 scroll step 이 executor 에서 `scroll_into_view_if_needed` 호출.

### 성공 기준 (C0b)

- 운영자가 UI 만으로 scroll step 의 위치를 지정해 추가 가능.
- 추가된 scroll 은 동일 세션의 scenario.json / scenario.healed.json /
  regression .py 에 모두 보존.
- 기존 "끝에 append" 호출자 (다른 코드 / 외부 호출) 회귀 0 건.

## 작업 순서

1. **A** (가장 임팩트 큼 — 본 사례 step 4 통과의 핵심).
2. **B** — A 가 끝나도 healing 안 가는 환경 그대로면 다른 사례에서 재발.
3. **C0b** — A·B 가 안정된 후 UI 보강.

각 단계 끝에 회귀 테스트.

## 비범위 (명시)

- 자동 wheel 캡처 — [`PLAN_CUSTOM_RECORDER.md`](PLAN_CUSTOM_RECORDER.md).
- C0c (전체 reorder UI) — 자체 recorder 가 도입되면 의미 줄어듦. 그때 재검토.
- Dify endpoint 자동 fallback / health-check.
- title 외 다른 codegen 미지원 locator (`get_by_alt_text` 등) 추가 — 별도 항목.
