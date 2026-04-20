# Playwright 9 대 DSL 액션 테스트

`dify-chatflow.yaml` Planner prompt 가 명시하는 **9 대 표준 액션** (`navigate`, `click`, `fill`, `press`, `select`, `check`, `hover`, `wait`, `verify`) 를 각각 3~4 케이스씩 **총 30 개** 로 검증. 모든 테스트는 `fixtures/*.html` 로컬 페이지만 사용하므로 **airgap / 폐쇄망에서도 그대로 실행 가능**.

이 폴더는 [§자체 완결 원칙](../README.md#핵심-개념-먼저-읽기) 을 따라 독립적이며, 별도 설치 / 별도 venv 에서 실행해도 된다.

---

## 빠른 실행

```bash
# 최초 1 회 — pytest + pytest-playwright + Chromium 설치
cd playwright-allinone/test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 30 케이스 실행 (기본 headless)
pytest

# 브라우저 창 띄우기
pytest --headed

# 특정 액션만
pytest test_click.py -v
pytest -k "fill" -v
```

> 이미 `~/.dscore.ttc.playwright-agent/venv` 가 있다면 (host agent setup 을 돌린 적이 있다면) 별도 venv 만들지 않고 `source ~/.dscore.ttc.playwright-agent/venv/bin/activate && pip install pytest pytest-playwright` 만 추가해도 된다.

---

## 케이스 배분

| 액션 | 파일 | 케이스 | 검증 포인트 |
|------|------|--------|-------------|
| navigate | `test_navigate.py` | 3 | 기본 goto / meta redirect / invalid URL 에러 |
| click | `test_click.py` | 4 | 일반 버튼 / role+name / 링크 이동 / fallback selector |
| fill | `test_fill.py` | 4 | text input / textarea / readonly 거부 / 특수문자 |
| press | `test_press.py` | 3 | Enter 폼 제출 / Escape 모달 닫기 / Tab 포커스 이동 |
| select | `test_select.py` | 3 | value 지정 / label 지정 / 존재하지 않는 값 에러 |
| check | `test_check.py` | 3 | checkbox / radio / 이미 체크된 상태 idempotent |
| hover | `test_hover.py` | 3 | tooltip 노출 / 서브메뉴 노출 / 비가시 엘리먼트 타임아웃 |
| wait | `test_wait.py` | 3 | 고정 ms / selector 대기 / timeout 실패 |
| verify | `test_verify.py` | 4 | 보임 / 숨김 / 텍스트 일치 / 영역 존재 |
| **합계** | | **30** | |

## 디렉토리 구조

```text
playwright-allinone/test/
├── README.md          # 이 파일
├── conftest.py        # fixture_url 헬퍼 (file:// URL 생성)
├── pytest.ini         # pytest 설정 (-v --tb=short 기본)
├── requirements.txt   # pytest + pytest-playwright + playwright
├── fixtures/          # 9 개 로컬 HTML (action 별 1 개)
│   ├── navigate.html
│   ├── redirect.html
│   ├── click.html
│   ├── fill.html
│   ├── press.html
│   ├── select.html
│   ├── check.html
│   ├── hover.html
│   ├── wait.html
│   └── verify.html
└── test_*.py          # 9 개 테스트 파일 (한 파일당 3~4 개 케이스)
```

## 설계 메모

- **`file://` URL** 사용 — HTTP 서버 fixture 불필요, 외부 네트워크 의존 0.
- **strict mode 기본** — 한 locator 가 여러 element 매치하면 실패. false-positive PASS 방지.
- **headless 기본** — CI 친화적. 디버깅 시 `--headed` + `--slowmo 500`.
- **pytest-playwright 표준 `page` fixture** 사용 — 각 테스트가 독립 BrowserContext 를 얻어 상태 간섭 없음.

## zero_touch_qa 와의 관계

본 테스트들은 `zero_touch_qa` executor 가 수행하는 9 대 액션의 **Playwright API 직접 호출 레이어를 검증**한다. executor 자체의 치유 / LocatorResolver / Healer 로직은 별도이며 여기서 검증하지 않는다 — 이 테스트의 목적은 "Playwright 자체가 우리가 기대하는대로 각 액션을 수행하는가" 의 기준선 확보.
