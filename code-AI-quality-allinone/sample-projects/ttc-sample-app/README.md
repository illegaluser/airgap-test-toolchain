# ttc-sample-app

**Dual-track 샘플 레포지토리** — airgap-test-toolchain 의 두 축 파이프라인을
한 repo 로 동시에 검증하도록 설계된 최소 Python + JS 애플리케이션입니다.
실제 프로덕션 앱이 아니라 **교육·검증 용** 이므로 의도적 결함이 포함되어 있습니다.

## 두 축

| 축  | 대상 파이프라인                    | 이 레포의 역할                                                                |
| -- | ---------------------------------- | ---------------------------------------------------------------------------- |
| 1  | 코드품질 측정 + LLM 해결책 제시 (01~04) | `src/` + `frontend/` 에 의도된 Sonar 이슈 10+ 심어 P1/P2/P3 체인 검증        |
| 2  | **요구사항 대비 구현율 측정 (미래)**   | `requirements/REQ-*.md` 에 front-matter + acceptance_criteria 로 traceability 시연 |

## 디렉토리 구조

```
ttc-sample-app/
├── requirements/              # 축 2 — YAML front-matter 가 박힌 7개 REQ + index
│   ├── README.md              # 현재 상태 요약 (done 3 / partial 2 / in-progress 1 / not-started 1)
│   ├── REQ-001-user-registration.md
│   ├── REQ-002-login-with-mfa.md
│   ├── REQ-003-password-reset.md       # 의도적 not-started
│   ├── REQ-004-session-timeout.md
│   ├── REQ-005-rbac.md                  # in-progress
│   ├── REQ-006-audit-log.md
│   └── REQ-007-rate-limit.md
├── src/                       # 축 1 — Python Flask-style
│   ├── auth/                  # login, mfa, reset(stub), session
│   ├── models/                # user (bcrypt), audit
│   ├── rbac/roles.py
│   ├── routes/                # users, api
│   └── utils/rate_limiter.py
├── frontend/                  # 다언어 커버리지 — JS
│   ├── login.js
│   └── session.js
├── tests/                     # pytest, test_REQ_* 명명 규약
│   ├── test_REQ_001_user_registration.py
│   ├── test_REQ_002_login_mfa.py        # AC-002-2 는 @pytest.mark.skip
│   ├── test_REQ_004_session_timeout.py
│   └── test_auth_common.py              # REQ-006 + REQ-007 cross-cutting
├── docs/
│   ├── architecture.md        # 레이어 + REQ↔구현↔테스트 매핑 표
│   └── security-policy.md     # RBAC 매트릭스 + **Known Defects** 목록 (정답지)
├── sonar-project.properties   # Sonar 설정 — requirements/ docs/ 제외
└── README.md                  # 이 파일
```

## 축 1 — 코드품질 파이프라인 사용법

### 준비

provision.sh 가 GitLab 에 `root/ttc-sample-app` 프로젝트를 자동 생성 + 초기
push 합니다 (§ 메인 README §7 참고). 생성 후 Jenkins `01-코드-분석-체인`
Job 의 파라미터를 다음으로 설정:

| 파라미터      | 값                                                           |
| ------------ | ----------------------------------------------------------- |
| `REPO_URL`   | `http://gitlab:80/root/ttc-sample-app.git`                   |
| `BRANCH`     | `main`                                                      |
| `ANALYSIS_MODE` | `full`                                                   |
| `GITLAB_PROJECT` | `root/ttc-sample-app`                                   |

### 기대 결과

- **SonarQube**: 10~15개 이슈 (BLOCKER 2, CRITICAL 3, MAJOR 8, MINOR ~2). 정확한
  수는 profile 버전에 따라 변동. 기대 이슈는 `docs/security-policy.md` 의
  **Known Defects** 표 참고.
- **RAG Diagnostic Report** (04 빌드 탭): `callers bucket filled > 50%`,
  `avg citation rate > 25%` 가 목표 구간. 이보다 낮으면 KB 빌드/프롬프트를
  점검.
- **GitLab Issues**: 이슈당 1개씩 자동 생성. 본문에 Sonar rule 원문 + LLM 의
  impact analysis + suggested fix.

### Known Defects 활용

`docs/security-policy.md` 마지막의 "Known Defects" 표가 **LLM 정답지 역할**
을 합니다. `eval_rag_quality.py` 의 golden CSV 에 이 표의 `expected_*` 열을
옮겨 넣고 LLM 분석 결과와 비교하면 이 샘플 한정 자동 평가가 가능합니다.

### 샘플 golden CSV 예시

```csv
sonar_issue_key,expected_classification,expected_confidence_min,expected_keywords,expected_cited_paths
<LOGIN_SQL_INJ_KEY>,true_positive,high,SQL injection;parameterized query,src/auth/login.py
<MFA_HARDCODED_KEY>,true_positive,high,hardcoded;TOTP seed,src/auth/mfa.py
<SESSION_COOKIE_KEY>,true_positive,medium,secure flag;cookie,src/auth/session.py
```

`<..._KEY>` 는 실제 Sonar 이슈 key (실행 후 `sonar_issues.json` 에서 확인).

## 축 2 — 요구사항 구현율 파이프라인 (미래)

### Front-matter 포맷

각 `REQ-NNN-<slug>.md` 는 아래 스키마를 준수:

```yaml
---
id: REQ-NNN
status: done | partial | in-progress | not-started
implementation_refs: [src/path/to/file.py, ...]
acceptance_criteria:
  - id: AC-NNN-N
    desc: <사람이 읽는 한 줄>
    evidence_test: tests/test_REQ_NNN_*.py::test_name   # 없으면 null
    verified: true | false
---
```

미래 `06-요구사항-구현율` Jenkins Job 이 이 front-matter 를 파싱해:

- **구현 완료율**: `status == done` 인 REQ 비율
- **인수기준 통과율**: `verified: true` 인 AC 비율
- **커버리지 매트릭스** HTML: REQ × file × test 3 축 표

### 커밋 메시지 규약

```
REQ-002: Add TOTP validation flow for login
REQ-006: Wire failed login audit to login.py
REQ-002/REQ-006: Lockout after 3 fails (also audited)
```

미래 파이프라인이 `git log --grep='REQ-\d{3}'` 로 각 REQ 에 연결된 커밋을
집계해 `implementation_refs` 가 누락된 케이스 자동 보완.

### 테스트 명명 규약

`test_REQ_NNN_<slug>.py` + 테스트 함수 이름에도 AC-NNN-N 참조. `evidence_test`
필드가 이 경로/함수를 가리키면 traceability 매핑 성립.

## 의도된 결함 목록

`docs/security-policy.md` 의 **Known Defects** 표를 참조. 각 결함은 Sonar 가
탐지 가능한 패턴이며, 수정하지 말고 **그대로 유지**해야 파이프라인 검증
용도로 재사용 가능합니다.

## 의도된 요구사항 상태 분포

| status        | 수  | REQ                     |
| ------------- | --- | ----------------------- |
| done          | 3   | REQ-001, REQ-004, REQ-006 |
| partial       | 2   | REQ-002, REQ-007         |
| in-progress   | 1   | REQ-005                  |
| not-started   | 1   | REQ-003                  |

→ 구현 완료율 3/7 = **42.9%**, 인수기준 통과율 10/17 = **58.8%**.
이 숫자들이 미래 traceability 리포트의 주 지표가 됩니다.

## 이 샘플로 하지 말아야 할 것

- **프로덕션 배포**: 의도적 보안 결함이 다수 있음.
- **결함 "수정" PR**: Known Defects 는 파이프라인 검증용 자산이므로 유지.
  새 결함을 추가하거나 structure 를 바꾸고 싶으면 별도 레포로 포크.
- **실제 user 정보 입력**: 로컬 dev/test 전용 DB (SQLite) 로만 작동.
