# e2e-test/

전면 재작성된 e2e 슈트. 설계 근거 + 슬롯 매핑 + 폐기된 슈트 목록은
[`../docs/PLAN_E2E_REWRITE.md`](../docs/PLAN_E2E_REWRITE.md) 참조.

## 디렉토리 구조

| 디렉토리 | 슬롯 | 의도 |
|---|---|---|
| `unit/` | pre-commit | AST/문자열 변환 결정론. 외부 의존 0. |
| `integration/` | pre-push | Playwright + `fixtures/` self-served HTML. 1 fixture = 1 회귀. |
| `flow/` | pre-push | Recording UI + Replay UI daemon round-trip. fixture 페이지 한정. |
| `fixtures/` | — | `integration/` 과 `flow/` 가 공유하는 self-served HTML. 1 fixture = 1 회귀 패턴. |
| `selftest_build/` | `./build.sh` 끝 | 빌드 PC 환경 한정 P0 가드. |
| `selftest_receive/` | `Launch-ReplayUI` 최초 1회 | 받는 PC 환경 P0 가드. |

## 슈트 작성 규칙

1. **1 슈트 = 1 회귀 commit** — 각 슈트 docstring 첫 줄에 표적 commit hash 명시.

   ```python
   """Regression guard for f1761f2 — executor 식별자 줄바꿈 정규화."""
   ```

2. **외부 SUT/네트워크 의존 0** — pre-commit/pre-push 슬롯의 슈트는 항상 결정론. LLM 호출 0.
3. **fixture 1개 = 1 회귀 패턴** — `fixtures/` 안의 HTML 한 파일은 한 가지 패턴만 표현. 재사용 시 docstring 으로 표적 commit 1차/2차 분리 표기.
4. **테스트 이름** — `test_emit_*` (unit), `test_exec_*` (integration), `test_flow_*` (flow).

## 폐기 이력

본 디렉토리 도입 commit 에서 `test/test_*_e2e.py` 10개 + `test/test_*_headed.py` 4개 + `test/E2E_COVERAGE.md` 일괄 삭제됨. 폐기 사유는 PLAN §2 진단 참조.
