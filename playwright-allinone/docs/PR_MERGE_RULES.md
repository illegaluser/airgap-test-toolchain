# PR 머지 룰 (개선책 #6)

## 배경

2026-05-02 사용자 사고 두 건 — `FrozenInstanceError` / `headless` dead control — 모두
다음 패턴으로 통과했다:

- 단위·e2e 테스트는 **mock 위에서만** 검증.
- 실 `subprocess` / 실 Playwright 흐름이 **한 번도 안 도는** 코드 path 가 그대로 머지.
- 사용자 환경에서 첫 실행 즉시 회귀 노출.

본 문서는 같은 패턴의 사고를 막기 위해 머지 전 강제로 통과해야 할 체크리스트.
강제력은 **사람** 이지만, 위반 시 PR 보류가 원칙.

## Rule 1 — Mock 추가 시 동행 정책

신규 `monkeypatch.setattr(X, ...)` 또는 동등 stub 을 PR 에 추가하면 다음 중 **하나** 가 같이 들어와야 한다:

| 옵션 | 의미 |
|---|---|
| (a) | `X` 함수의 **비-mock 호출 시나리오 e2e** 가 이미 있고, PR 의 변경이 그것을 깨뜨리지 않음 — PR 설명에 그 e2e 파일·함수 명시 |
| (b) | 새 mock 옆에 **비-mock smoke 1건** 도 같이 추가 (예: `test_smoke_subprocess.py`) |
| (c) | `X` 가 **외부 의존성** (LLM API / docker registry / 외부 SaaS) 라 mock 이 정공 — PR 설명에 명시적 사유 |

위 셋 중 어느 것도 만족 못 하면 PR 보류. 같은 사고 사례:
- `_run_llm_play_impl` mock 만 있고 실 `python -m zero_touch_qa` 실행 e2e 없음 → frozen Config 사고.

## Rule 2 — 사용자 흐름 골드 시나리오 비파괴

`test/test_user_journey_gold_e2e.py` 가 PR 변경 후에도 통과해야 한다. 이 파일은
"시드 → tour 생성 → 변환 → executor 실 실행 → run_log PASS" 의 한 묶음 — 사용자가
가장 자주 도는 동선의 마지막 안전망.

이 테스트가 깨지면 사용자가 보는 화면이 깨졌다는 뜻. 코드 자체가 문제든, 테스트 가
부정확하든, 둘 중 하나는 명시적으로 정리하고 머지.

## Rule 3 — 검증/변환기 어휘 동기화

`zero_touch_qa.executor._perform_action` 의 verify 분기에 새 condition 을 추가하면,
같은 PR 에 다음도 들어와야:

- `zero_touch_qa.__main__._VALID_VERIFY_CONDITIONS` 화이트리스트에 새 키워드 추가
- 변환기 (`converter_ast`) 가 새 condition 을 emit 하면, executor 가 그 정확한 키워드로
  분기 가능해야

위 둘이 어긋나면 `_validate_scenario` 가 condition 을 `""` 로 강등 → executor 가
default 로 떨어짐 → 사용자에게 의미가 다른 결과. 골드 시나리오로 잡힌 사고.

## Rule 4 — 회귀 가드 양방향 검증

새 fix 를 PR 에 넣을 때, 회귀 가드 테스트가 다음 양방향을 만족해야:

1. **fix 적용 상태**: 해당 테스트 PASS
2. **fix 임시 제거 상태**: 해당 테스트가 정확한 사유로 FAIL

(2) 를 PR 작업 중 한 번이라도 직접 확인했어야 한다. 안 했으면 그 회귀 가드는 신뢰
못 함 (다른 이유로 통과 중일 수 있음).

## Rule 5 — Lint / IDE 진단 무시 금지

편집한 파일에서 떠 오르는 IDE 진단(S3776 cognitive complexity, S8415 OpenAPI
docs, S125 commented-out code 등) 을 "이번 변경과 무관" 으로 dismiss 하지 않는다.
관련 메모리 룰: [`feedback_fix_lint_warnings.md`](../../../../.claude/projects/-Users-luuuuunatic-Developer-airgap-test-toolchain/memory/feedback_fix_lint_warnings.md).

큰 backlog (Cognitive Complexity 100+ 함수의 refactor 등) 는 별도 sweep 커밋으로
분리하되, **무시는 안 함**.

## Rule 6 — 진단/원인 보고는 검증된 사실만

코드만 읽고 "원인은 X 입니다" 라고 단정하지 않는다. **명령 실행/스크립트 검증** 후
보고. 메모리 룰: [`feedback_no_speculation.md`](../../../../.claude/projects/-Users-luuuuunatic-Developer-airgap-test-toolchain/memory/feedback_no_speculation.md).

추측은 "가설" 또는 "확인 필요" 로 명시. 가설을 사실처럼 적으면 사용자 시간 낭비 + 신뢰 손상.

## Rule 7 — 새 코드 path 는 사용자 흐름 검증으로 보강

새 기능 path 를 PR 에 넣을 때, 단위 테스트만으로 끝내지 말고 다음 중 하나로 보강:

- 실 subprocess smoke 추가 (`test/test_smoke_subprocess.py` 패턴)
- 인증 fixture 통합 e2e 추가 (`test/test_authn_fixture_integration.py` 패턴)
- 사용자 골드 시나리오에 시나리오 한 줄 추가 (`test/test_user_journey_gold_e2e.py`)

이 셋 중 어느 것도 안 닿는 코드 path 는 사용자 환경에서 처음 시험되는 셈 — 본
사고 패턴 그대로다.

---

**적용 시점**: 본 룰 문서가 머지된 시점부터 모든 PR.
**예외**: 긴급 핫픽스 (사용자 차단 사고) 는 룰 (1)~(7) 중 일부 우회 가능. 다만 후속
PR 로 보강 의무.
