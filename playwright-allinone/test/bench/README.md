# 외부 SUT 벤치마크 (트랙 2 Phase B2)

공개 안정 사이트에 14-DSL 시나리오를 N회 반복 실행해 *flake rate 시계열* 을
산출. DSL 표현력의 *임의 사이트 일반화* 데이터 확보가 목적.

상세 의사결정: [docs/PLAN_EXTERNAL_TRUST.md §5](../../docs/PLAN_EXTERNAL_TRUST.md)

## 디렉토리 구조

```text
test/bench/
├── flake_runner.py     # N회 반복 실행 + JSONL 누적
├── dashboard.py        # JSONL → 정적 HTML
├── sites/              # 시나리오 (체크인)
│   ├── playwright_dev/
│   │   └── search.json
│   ├── todomvc/
│   │   └── crud.json
│   └── herokuapp/
│       └── form_auth.json
├── results/            # 결과 누적 (.gitignore — 매 실행 append)
│   └── <YYYY-MM-DD>/runs.jsonl
└── dashboards/         # 생성된 HTML (.gitignore)
    └── index.html
```

## 사용

### 로컬

```bash
cd playwright-allinone
PYTHONPATH=shared:recording-ui:replay-ui \
  python -m test.bench.flake_runner --runs 10

PYTHONPATH=shared:recording-ui:replay-ui \
  python -m test.bench.dashboard
# 결과: test/bench/dashboards/index.html 브라우저로 열기.
```

### 특정 사이트만

```bash
python -m test.bench.flake_runner --site playwright_dev --runs 5
```

### 정기 실행

사용자 결정 (2026-05-13): 정기 cron 누적은 *추후 별도 서비스 내부 구현* 으로
대체. 본 디렉토리는 *시나리오 50개 + 실행 인프라* 만 자산화. 외부 서비스에서
`flake_runner` 와 `dashboard` 를 호출해 시계열 누적을 운영한다.

## 시나리오 JSON 포맷

14-DSL JSON 배열. 형식은 [test/fixtures/scenario_14.json](../fixtures/scenario_14.json)
또는 [docs/PLAN_DSL_ACTION_EXPANSION.md §2](../../docs/PLAN_DSL_ACTION_EXPANSION.md) 참조.

각 step 필수 키: `step` / `action` / `target` / `value` / `description`.
`verify` 액션은 `condition` 추가 (visible/hidden/text/contains/url_contains 등).

## 사이트 선정 + 현재 상태

[PLAN_EXTERNAL_TRUST.md §5.2](../../docs/PLAN_EXTERNAL_TRUST.md) 의 9개 사이트
(Naver 메인은 사용자 결정으로 제외):

| 사이트 | 시나리오 유형 | 작성 |
| --- | --- | --- |
| playwright.dev | 검색 / 문서 네비게이션 | 5 |
| TodoMVC (demo.playwright.dev/todomvc) | CRUD | 5 |
| the-internet.herokuapp.com | 폼 / 모달 / dialog (dialog_choose 회귀 포함) | 10 |
| demoqa.com | UI 컴포넌트 광범위 | 8 |
| saucedemo.com | 로그인 → 상품 → 결제 | 8 |
| practicesoftwaretesting.com | 검색 → 카트 | 5 |
| news.ycombinator.com | 읽기 전용 (검색, 페이징) | 3 |
| wikipedia.org | 읽기 전용 (검색) | 3 |
| Salesforce Trailhead | closed shadow 검증용 (의도적 ❌) | 3 |

총 **50 시나리오**.

## flake rate 임계

- **PASS**: 첫 그린 비율 95%+
- **flaky**: 50~95%
- **unsupported**: 7일 누적 N≥10 + 평균 성공률 <70% 자동 마킹 (dashboard 배지)

## 알려진 한계

- **외부 사이트는 마음대로 바뀜** → flake 가 *우리 도구* 가 아닌 *SUT 변동성*
  의 측정. 갱신을 *하지 않는 것* 이 의도. SUT 변경으로 시나리오가 깨지면 그건
  *그대로 데이터로 보존*.
- **네트워크 의존** — 폐쇄망 본체 솔루션의 *외부 검증 트랙* 은 GitHub Actions
  클라우드 러너에서 돌리는 것이 자연스러움.
- **봇 차단** — 정상 트래픽 흉내 위해 `step_interval_min/max_ms=200/600` 적용
  (랜덤 sleep). headless Chromium 의 기본 UA 유지 (D10 fingerprint pin 원칙).
