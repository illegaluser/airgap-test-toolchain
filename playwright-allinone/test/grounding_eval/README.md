# Phase 1 T1.7 — DOM Grounding 효과 측정 하니스

## 목적

`ENABLE_DOM_GROUNDING=1` (flag=on) 과 미설정 (flag=off) 페어를 같은 카탈로그에 실행해 신·구 경로의 셀렉터 정확도·healer 빈도를 비교한다. 절대 baseline 미사용.

## 평가 페이지 (T0.1 카탈로그)

`docs/eval-page-catalog.md` 의 P0-FX-01..05 + P0-HS-01..05 = **10종**.
인증 페이지는 Phase 2 진입 시 추가 — Phase 1 단계는 비인증 우선.

## 골든 시나리오

`golden/{P0-FX-01..05,P0-HS-05}.scenario.json` — 사람이 작성. 각 step 의 셀렉터가 ground truth.

## 실행 방법 (개발 호스트)

```bash
# flag=off 페어
unset ENABLE_DOM_GROUNDING
./scripts/run_grounding_eval.sh > artifacts/eval-off.json

# flag=on 페어
ENABLE_DOM_GROUNDING=1 ./scripts/run_grounding_eval.sh > artifacts/eval-on.json

# 비교 리포트
python3 -m test.grounding_eval.compare \
  --off artifacts/eval-off.json \
  --on  artifacts/eval-on.json \
  --out artifacts/grounding-eval-report.html
```

## 메트릭

페이지당 다음을 수집한다.

| 메트릭 | 정의 |
| --- | --- |
| selector_accuracy | 정확/부분/실패 분류 (DoD §"분류 정의") |
| healer_calls | `llm_calls.jsonl` 의 `kind=healer` 카운트 |
| planner_elapsed_ms | `kind=planner` 의 elapsed_ms |
| grounding_inventory_tokens | flag=on 시 `kind=planner` 의 grounding_inventory_tokens 필드 |

## 구현 순서

1. **카탈로그 wiring** — `golden/*.scenario.json` 5+5 작성 (사람 손)
2. **러너** — `run_grounding_eval.sh` 가 페이지별 zero_touch_qa CLI 호출
3. **분석기** — `compare.py` 가 healed.json 과 golden 을 비교, 분류 라벨링
4. **리포트** — HTML 사이드바이사이드 (off vs on), 페이지별 메트릭 표

본 스켈레톤은 디렉토리 구조 + README 까지. 운영자가 골든 시나리오 작성 후 러너·분석기 작성.
