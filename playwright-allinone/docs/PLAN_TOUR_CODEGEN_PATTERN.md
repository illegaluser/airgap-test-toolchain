# Tour 스크립트의 codegen 산출물 패턴 통합

## 배경

### 문제
- Discover URLs 에서 생성하는 tour 스크립트(`tour_selected.py`)는 pytest 기반 회귀 슈트(`@pytest.mark.parametrize` + fixture + `pytest.main([...])`).
- 우리 시스템 내 다른 인프라(AST 변환기 / annotator / LLM executor / visibility healer / regression 자동 생성 / diff 분석) 는 모두 **codegen 산출물 패턴 (모듈 레벨 직선 호출, `def run(playwright):` 본문)** 을 가정.
- 두 패턴 사이 간극이 산발적 부작용을 유발:
  - 변환기가 pytest fixture/parametrize 를 못 읽어 `scenario.json` 이 비어 있음 → Generate Doc / Play with LLM 항상 실패.
  - annotator 가 fixture 안 click 호출을 못 찾아 hover 자동 주입이 작동하지 않음.
  - codegen tracing wrapper 의 trace.zip → run_log.jsonl 변환 흐름이 tour 에는 적용되지 않음 (별도 `tour_results.jsonl` 산출).
  - 'codegen 산출물 ↔ LLM healed regression' 비교 의미가 살아남지 않음.
- 임시방편으로 `_synthesize_tour_scenario` 같은 fallback 헬퍼를 만들어 빈 시나리오를 합성했으나, 본질적 해결책 아님.

### 사용자 결정
> Discover URLs 를 통해 생성한 투어링 스크립트를 codegen 산출물 패턴으로 생성하는게 기존에 만들어놓은 각 기능과의 연계를 위해서도 보다 효과적일지에 대해 평가해줘.
>
> 1+2단계 병합 수행하자.

## 의사결정

### 1. tour 골격 변경
**채택**: `def run(playwright):` 본문에 모듈 레벨 직선 호출만 두는 codegen 패턴.

```python
def run(playwright):
    browser = playwright.chromium.launch(headless=HEADLESS)
    context = browser.new_context(**ctx_kwargs)
    page = context.new_page()

    page.goto("https://...url1...")
    assert "errorMsg" not in page.url
    assert len(page.inner_text("body")) >= 50

    page.goto("https://...url2...")
    assert "errorMsg" not in page.url
    assert len(page.inner_text("body")) >= 50
    ...
```

**기각된 대안**:
- (a) **현 pytest 골격 유지 + 변환기에 pytest 인식 추가**: 변환기가 fixture / parametrize / fixture 의존성 그래프까지 따라가야 해 복잡도 폭발. 우리 codebase 의 다른 모든 변환 흐름을 흔드는 invasive 변경.
- (b) **두 패턴 양립 (생성기가 모드 선택)**: 코드 경로 두 개를 영구 유지해야 함. 사용자/유지보수자 모두에게 인지부담.

### 2. 검증 의미 회복 — 모듈 assert → 14-DSL verify step 매핑
**채택**: AST 변환기가 다음 3가지 assert 패턴을 자동 인식해 `verify` step 으로 변환.

| AST 패턴 | 14-DSL step |
|---|---|
| `assert "X" not in page.url` | `{action: verify, target: page.url, condition: url_not_contains, value: "X"}` |
| `assert "X" in page.url` | `{action: verify, ..., condition: url_contains, value: "X"}` |
| `assert len(page.inner_text("S")) >= N` | `{action: verify, target: "S", condition: min_text_length, value: N}` |

executor 의 `verify` 액션 처리부에 위 세 condition 키워드를 새로 받도록 확장.

**기각된 대안**:
- **assert 그대로 두고 변환기는 navigate-only 시나리오 생성**: '테스트코드 원본 실행' 은 정상 작동 (Python assert 가 자연스럽게 raise) — 다만 'LLM 적용 코드 실행' 은 검증 누락. 1+2 통합 의도와 어긋남.

### 3. tour 전용 산출물 (tour_results.jsonl, tour_screenshots/) 제거
**채택**: 14-DSL executor 의 `run_log.jsonl` + `step_<N>_*.png` 인프라로 통일.

**근거**:
- 이미 codegen 세션은 trace.zip → run_log.jsonl 변환 흐름이 잡혀 있음.
- tour 가 같은 패턴이면 자동으로 동일 산출물 생성. 별도 jsonl 형식 유지할 동기 없음.
- "URL 별 PASS/FAIL 표" 가 필요하면 14-DSL run_log.jsonl 의 step 별 status 가 동등 정보 제공.

**손실/완화**:
- 첫 assert 실패 시 `python script.py` 실행은 즉시 abort (pytest 의 "전체 URL 통과" 보장 손실). 다만 LLM 모드는 step 별 PASS/HEALED/FAIL 보고하므로 보강. 단순 codegen 실행에서 "전부 도는 것" 을 보고 싶으면 try/except 래핑이 별도 옵션으로 가능 (후속).

### 4. legacy `_synthesize_tour_scenario` 정리
**채택**: 변환기가 새 tour 패턴을 직접 읽으므로 fallback 헬퍼 불필요. 함수 자체는 보존하되(legacy tour 산출물 호환), import 흐름에서 변환기가 비-빈 결과를 내면 fallback 미진입.

**근거**: 이미 사용자가 만들어둔 옛 tour 스크립트(과거 pytest 패턴)를 임포트할 가능성을 위해 안전망 유지.

## 구현 범위

### 파일 변경

| 파일 | 변경 |
|---|---|
| `shared/zero_touch_qa/executor.py` | `verify` 액션에 `url_contains` / `url_not_contains` / `min_text_length` condition 추가 |
| `shared/zero_touch_qa/converter_ast.py` | `_handle_stmt` 에 `ast.Assert` 분기 + `_assert_to_url_membership` / `_assert_to_min_text_length` 헬퍼 |
| `recording-ui/recording_service/server.py` | `_TOUR_SCRIPT_TEMPLATE` 재작성 (codegen-style + assert 보조). pytest / fixture / pytest.main / tour_results / tour_screenshots 제거 |
| `test/test_recording_service.py` | 새 verify condition / converter assert 매핑 단위 테스트 |
| `test/test_discover_api_e2e.py` | tour 골격 검증 마커를 codegen 패턴 키워드로 교체 |

### 테스트 전략
- 양방향 검증 (fix 적용 시 통과 / fix 제거 시 실패) 으로 회귀 가드.
- E2E 풀셋(110+) 모두 통과 확인.

## 검증

| 항목 | 결과 |
|---|---|
| converter_ast 새 단위 (assert 3종 + 무관 assert 무시 + tour 전체 패턴) | 5/5 통과 (`test_converter_ast.py`) |
| recording_service / discover_api 통합 (148건) | 모두 통과 |
| 통합 e2e (`pytest -m e2e`) | 111 passed, 709 deselected (이전 114 → content_settle 의미 사라진 검증 3건 제거) |
| 합성 출력 spot-check | 2 URL → 6 step (navigate ×2 + verify ×4) 확인 |

## 후속 작업

- (선택) `try:` 블록 안의 assert 도 변환기가 인식하도록 확장 → "전체 URL 통과 후 한꺼번에 결과 보고" 모드 회복.
- (선택) tour 스크립트의 preflight (auth 만료 사전 검출) 도 첫 step 의 verify 로 자동 삽입.
