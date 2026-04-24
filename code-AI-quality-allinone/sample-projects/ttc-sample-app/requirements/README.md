# Requirements Index — ttc-sample-app

이 디렉토리는 **요구사항 대비 코드 구현율 측정** 파이프라인 (축 2) 의
입력 원본입니다. 각 요구사항은 별도 `REQ-NNN-<slug>.md` 로 분리되어 있고
YAML front-matter 에 결정적 traceability 메타데이터를 담습니다.

## 현재 상태 요약 (의도된 분포)

| ID      | 제목                                  | status         | 구현율 (verified AC / total AC) |
| ------- | ------------------------------------ | -------------- | ------------------------------- |
| REQ-001 | User Registration                    | done           | 3/3                             |
| REQ-002 | Multi-Factor Authentication (MFA)    | partial        | 1/3                             |
| REQ-003 | Password Reset                       | not-started    | 0/2                             |
| REQ-004 | Session Timeout                      | done           | 2/2                             |
| REQ-005 | Role-Based Access Control (RBAC)     | in-progress    | 1/3                             |
| REQ-006 | Audit Log                            | done           | 2/2                             |
| REQ-007 | API Rate Limiting                    | partial        | 1/2                             |
|         |                                      | **totals**     | **10 / 17 = 58.8%**             |

> 이 분포는 의도적입니다 — 추후 구축할 `06-요구사항-구현율` Jenkins Job 이
> 구현 완료율 (done 3/7 = 42.9%) · 인수기준 통과율 (10/17 = 58.8%) 같은
> 지표를 자연스럽게 드러내도록 done / partial / in-progress / not-started
> 네 상태를 고르게 섞었습니다.

## Front-matter 스키마

```yaml
---
id: REQ-XXX
title: 한 줄 제목
priority: low | medium | high | critical
status: done | partial | in-progress | not-started
owner: 팀명
acceptance_criteria:
  - id: AC-XXX-N
    desc: 검증해야 할 행동 기준
    evidence_test: tests/test_REQ_XXX_*.py::test_name  # 없으면 null
    verified: true | false
implementation_refs:
  - src/path/to/module.py
---
```

**필수 필드**: `id`, `status`, `implementation_refs`, `acceptance_criteria[].verified`.
이 4개만 있어도 구현율 자동 집계가 가능하고 나머지 (priority / owner / desc)
는 사람이 읽는 용도.

## 커밋 메시지 규약 (traceability 보조)

구현 커밋은 prefix 로 REQ ID 를 포함:

```
REQ-002: Add TOTP validation flow for login
REQ-002/REQ-006: Lockout after 3 fails (also logs to audit)
```

미래 파이프라인이 `git log --grep='REQ-\d{3}'` 로 각 REQ 에 연결된 커밋을
집계해 `implementation_refs` 가 누락된 케이스를 보완.

## 테스트 명명 규약

pytest 파일명 `test_REQ_XXX_<slug>.py`, 테스트 함수 이름에도 REQ 포함
가능. `evidence_test` 필드가 이 경로·함수를 가리켜야 커버리지 매핑이 된다.
