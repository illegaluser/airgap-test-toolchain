---
name: e2e tests run headless by default
description: e2e 테스트는 별도 지시 없으면 headless 로 실행. 임의로 headed 로 바꾸지 말 것.
type: feedback
originSessionId: b6f0b6e5-9e17-41ee-a9cc-32a2b7969842
---
e2e 테스트는 **headless 가 기본**이다. 사용자가 명시적으로 headed 를 요청하지 않는 한, 설정/명령에서 임의로 headed 로 바꾸지 않는다.

**Why:** 2026-05-02, 사용자가 pre-commit 훅 출력을 보고 "headed 로 바꾼 거냐?" 추궁. 실제로는 변경 없었지만, 기본값에 대한 명시적 합의가 필요함을 강조.

**How to apply:**
- pytest/playwright 옵션, conftest, settings 등에서 `headless=False` 또는 `--headed` 를 끼워넣지 말 것.
- 사용자가 디버깅 목적으로 headed 를 부탁한 경우에도, 그 작업이 끝나면 headless 로 되돌려둘 것.
