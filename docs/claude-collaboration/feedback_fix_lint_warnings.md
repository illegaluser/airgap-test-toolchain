---
name: Fix lint warnings — never dismiss
description: IDE 진단/lint 경고를 "기존 거라 무관" 으로 무시하지 않는다. 모두 수정 대상.
type: feedback
originSessionId: e6f62888-2cea-4b43-a1d6-7d4782cd0ba4
---
IDE 가 보고하는 lint/진단 경고를 **무시하거나 "이번 변경과 무관" 으로 dismiss 하지 않는다**. 전부 수정 대상으로 다룬다.

**Why:** 2026-05-02 사용자 명시 지시 — "이런거 전부 수정해. 무시하지마." CLAUDE.md 의 "Surgical Changes" 규칙은 이 사용자에 한해 lint 경고에 대해선 적용 안 됨.

**How to apply:**
- 편집한 파일에서 lint 경고가 PostToolUse hook 으로 들어오면, 그 파일의 **모든** 경고를 정리한다 (이번 변경 라인뿐 아니라 기존 backlog 도).
- 큰 backlog (HTTPException OpenAPI 문서화 / Cognitive Complexity 등) 는 한 번에 끝낼 수 없으면 같은 작업 묶음의 부수 변경으로 여러 커밋에 나눠 진행. 단 "무시" 또는 "무관" 보고는 절대 금지.
- 경고를 그냥 무시하고 진행해야 하는 경우(예: 기존 함수 시그니처 호환성 때문에 못 고침) 라도 *왜* 못 고치는지 사용자에게 명확히 보고. 이유 없는 dismiss 는 신뢰 손상.
- **새로 추가하는 코드** 가 lint 경고를 낳지 않도록 작성 시점에 점검 (cognitive complexity 폭증, frozen field 직접 assign, dict.get 결과 float 비교, 빈 except 등 빈번한 패턴들).
- markdown lint 도 동일 — heading/list 주변 공백, table column style 등 docs/*.md 작성 시 처음부터 맞춰 작성.

**우선순위:**
- 이번 작업 범위 내 파일 → 즉시 수정
- 그 외 파일 → 별도 sweep 커밋 (지시 받으면 진행)
