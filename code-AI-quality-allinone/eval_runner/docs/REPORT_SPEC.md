# AI 평가 리포트 사양 (REPORT_SPEC)

> **상태**: Phase 0.3 초안. Phase 2 의 acceptance 기준으로 사용된다.
> **버전**: 1.0.0 (2026-04-22)
> **정본 스펙**: [readme.md §5.1](../../../readme.md) (11지표/5단계)

---

## 1. 목적과 소비자

평가 결과의 **가독성·이해도** 를 파이프라인의 핵심 산출물로 승격. Jenkins 빌드를 보는
사람이 외부 문맥 없이 바로 다음을 판단할 수 있어야 한다.

### 1.1 페르소나와 사용 시나리오

| 페르소나 | 사용 시점 | 알고 싶은 것 | 필요한 정보 |
|---|---|---|---|
| **운영자 (1차 대응)** | Jenkins 알람 직후 | 이 빌드를 통과시킬지 차단할지 | 전체 PASS/FAIL, 어느 지표가 깨졌는지, 시스템 에러인지 품질 실패인지 |
| **엔지니어 (디버깅)** | 회귀 의심 시 | 어느 case 가 왜 실패했는지 | turn 단위 input/output, Judge reason, raw_response, latency |
| **의사결정자 (전략)** | 주기 리뷰 | 평가 시스템이 신뢰할 만한지 | Judge/Dataset 버전, 시간 추세, 보정 세트 편차 |

### 1.2 30초 룰

운영자가 Jenkins 탭을 연 뒤 **30초 이내**에 다음 4가지를 판단할 수 있어야 한다.
1. 전체 빌드 결과(PASS/FAIL/WARN)
2. 11지표 중 어느 것이 가장 약한가
3. 시스템 가용성 이슈인가, 모델 품질 이슈인가
4. 다음 행동(이슈 등록 / 재실행 / 모델 점검 / 무시)

이 룰이 R1~R3 의 설계 기준.

---

## 2. 레이아웃 원칙

### 2.1 3-Layer 정보 밀도

```
┌────────────────────────────────────────────────────────┐
│  Layer 1: 임원 요약 헤더 (R1)                          │  ← 3초
│  단일 스크린, 모든 핵심 수치를 한 번에                 │
├────────────────────────────────────────────────────────┤
│  Layer 2: 11지표 카드 대시보드 (R2)                    │  ← 30초
│  지표별 pass rate · 분포 · 임계치 시각화               │
├────────────────────────────────────────────────────────┤
│  Layer 3: Case drill-down (R3)                         │  ← 분~시간
│  Conversation accordion → turn detail (접힘 기본)      │
│  엔지니어가 디버깅할 때만 펼침                         │
└────────────────────────────────────────────────────────┘
```

### 2.2 시각적 톤

- 색상 약속: **green** (pass) · **amber** (warn / 임계 근접) · **red** (fail) · **gray** (skipped/N/A)
- 시스템 에러는 **slate-blue** 으로 별도 색군 → 품질 실패와 구분 (R4)
- 폰트: 시스템 monospace 친화 (Jenkins iframe 환경 고려)
- 단위: 숫자에 항상 단위 표기 (`ms`, `tokens`, `%`)

---

## 3. 섹션별 사양

### 3.1 R1 — 임원 요약 헤더

**위치**: 페이지 최상단. 스크롤 없이 보여야 함.

**ASCII 목업**:
```
╔════════════════════════════════════════════════════════════════════╗
║  AI Eval Summary    Build #42       2026-04-22 10:15  ✅ PASSED   ║
║  ──────────────────────────────────────────────────────────────── ║
║  Conversations: 10    Turns: 12    PASS: 11    FAIL: 1    WARN: 0 ║
║                                                                    ║
║  ┌─ 11지표 한눈에 (pass rate %) ─────────────────────────────────┐ ║
║  │ ① Policy   ██████████ 100%   ⑦ Recall*    █████████░  90%  │ ║
║  │ ② Format   ██████████ 100%   ⑧ Precision* ████████░░  80%  │ ║
║  │ ③ Task     █████████░  90%   ⑨ Multi-turn ██████████ 100%  │ ║
║  │ ④ Relevy   █████████░  90%   ⑩ Latency    P95: 1230ms      │ ║
║  │ ⑤ Toxic    ██████████ 100%   ⑪ Tokens     45.2K total      │ ║
║  │ ⑥ Faithful*█████████░  90%                                  │ ║
║  │ * = RAG 케이스만 적용                                       │ ║
║  └────────────────────────────────────────────────────────────┘ ║
║                                                                    ║
║  Judge: qwen3-coder:30b @ ollama:11434  T=0  digest:sha256:7f3a   ║
║  Dataset: golden.csv  rows=12  sha256:a1b2c3d  modified=04-21      ║
║  Errors: System(0) / Quality(1)                                    ║
╚════════════════════════════════════════════════════════════════════╝
```

**필수 필드**:
- 빌드 메타: build_number, timestamp, overall_status (`passed`/`failed`/`unstable`)
- 집계: conversations / turns / pass / fail / warn 카운트
- 11지표 한 줄씩: pass rate %, RAG-only 표시, 운영지표(latency P95, tokens total)
- Judge 메타: model, base_url, temperature, digest (가능 시)
- Dataset 메타: path 또는 name, rows, sha256, mtime
- 에러 분류: System count / Quality count

**비기능**:
- 단일 viewport (1280×800) 안에 fit
- 모바일/좁은 창 시 상하 적층

---

### 3.1.1 R1.1 — 🤖 LLM 임원 요약 문단 (기본 on)

R1 헤더 최상단에 LLM 이 생성한 2~3 문장 요약 섹션을 추가. 운영자가 **숫자를 읽기 전에 자연어로 먼저 상황을 파악**할 수 있도록.

**위치**: R1 헤더 맨 위, "AI Eval Summary" 타이틀 바로 아래.

**ASCII 목업**:
```
╔════════════════════════════════════════════════════════════════════╗
║  AI Eval Summary    Build #42       2026-04-22 10:15  ✅ PASSED   ║
║                                                                    ║
║  🤖 이번 빌드 한 줄 요약                                          ║
║  ──────────────────────────────────────────────────────────────── ║
║  이번 빌드는 대화 10건 중 9건 통과 (90%). 주원인 지표는 Answer    ║
║  Relevancy (off-topic 1건). 권장 조치: 프롬프트에 "질문 주제에    ║
║  국한" 지시어 보강.                                               ║
║  ──────────────────────────────────────────────────────────────── ║
║  [기존 R1 카드들: 집계, 11지표, latency/tokens, judge 메타...]    ║
╚════════════════════════════════════════════════════════════════════╝
```

**필수 필드**:
- 생성 문장 (한국어, 2~3 문장, 최대 300자)
- Provenance 배지: 🤖 LLM / 📋 기본 메시지 (fallback 이면)
- summary.json 에 영구 기록: `aggregate.exec_summary: {text, source, role, cached_at}`

**환경변수**: `SUMMARY_LLM_EXEC_SUMMARY` (기본 on, "off" 로 비활성).

**LLM 입력 페이로드 (캐시 키)**: `totals` 의 pass/fail 카운트, `metric_averages`, `latency_ms.p95`, 실패 case 상위 3개의 (case_id, failure_message).

**프롬프트 가드레일**:
- 주어진 JSON 필드의 사실만 사용
- 정확히 2~3 문장, 첫 문장=전체 상태, 둘째=주원인, 셋째(선택)=권장 조치
- 추측·새 해석·외부 지식 금지
- score/case_id/숫자/URL 원문 유지

**Fallback (결정론 템플릿)**: LLM 비활성/실패 시 `"총 N건 중 K건 실패 (통과율 X%). 주요 실패 case: c1, c2, c3. 상세 원인은 case drill-down 을 확인하세요."`.

---

### 3.2 R2 — 11지표 카드 대시보드

**위치**: R1 바로 아래. 11개 카드 grid 배열 (2~3열).

**ASCII 목업** (단일 카드):
```
┌──────────────────────────────────┐
│ ④ Answer Relevancy        🟢 PASS│
│ ───────────────────────────────  │
│ pass: 9/10  (90%)   threshold: 0.70│
│ ┌─ 점수 분포 ──────────────────┐│
│ │ 0.0 ░░                      0││
│ │ 0.5 ░░░░                    1││
│ │ 0.7 ━━━━━━━━━━ (threshold)   ││
│ │ 0.9 ▓▓▓▓▓▓▓▓                4││
│ │ 1.0 ▓▓▓▓▓▓▓▓▓▓              5││
│ └──────────────────────────────┘│
│ 경계 케이스 (±0.05): 0개         │
│ 실패 케이스: deep-relevancy-offtopic│
└──────────────────────────────────┘
```

**필수 필드 (카드별)**:
- 지표 ID (①~⑪) + 이름 + 단계 표시
- 결과 배지: 🟢 PASS / 🟡 WARN / 🔴 FAIL / ⚪ N/A (적용 대상 없음)
- pass count / total · pass rate % · threshold
- 점수 분포 히스토그램 (5~10 bin, 임계치 막대 표시)
- 경계 case 수 (임계 ±0.05 이내 — Phase 5 의 Q7 와 연동)
- 실패 case_id 목록 (최대 3개, 전체는 R3 로 링크)

**카드 표시 규칙**:
- ⑥/⑦/⑧ (RAG 전용) — `retrieval_context` 있는 case 가 0이면 카드 전체를 ⚪ N/A
- ⑩ Latency, ⑪ Token Usage — 정보성. PASS/FAIL 없이 P50/P95/P99 또는 합계만
- ② Format Compliance — `TARGET_TYPE=ui_chat` 빌드에서는 ⚪ N/A

---

### 3.2.1 R2.1 — 🤖 지표별 LLM 해석 1줄 (기본 off)

각 지표 카드 하단에 "이 지표가 왜 이런 점수인지" 한 줄 해설을 LLM 이 생성. **빌드당 최대 11 calls** 이므로 기본 비활성, 운영 중 필요 시 opt-in.

**ASCII 목업 (카드 안)**:
```
┌──────────────────────────────────┐
│ ④ Answer Relevancy        🟢 PASS│
│ pass: 9/10  (90%)   threshold: 0.70│
│ [히스토그램]                     │
│ ─────────────────────────────── │
│ 🤖 Answer Relevancy pass 9/10,  │
│    off-topic case 1건이 편차    │
│    원인.                        │
└──────────────────────────────────┘
```

**환경변수**: `SUMMARY_LLM_INDICATOR_NARRATIVE` (기본 **off**).

**LLM 입력**: 지표명 + pass/total + threshold + 실패 case_id 상위 3개.

**Fallback**: `"{지표명} pass {N}/{M} ({rate}%). 실패 case: c1, c2."`.

**비용 메모**: 호출량 × ~5s = 빌드당 최대 ~55s 추가. 옵션 활성화 전 비용 공지 필요.

---

### 3.3 R3 — Case drill-down

**위치**: R2 아래. 기본 접힘.

**ASCII 목업**:
```
▼ Conversations (10)

  ▶ ✅ rag-1 (1 turn)            Faithful · Recall · Precision
  ▶ ✅ rag-2 (1 turn)            Faithful FAIL · Hallucination
  ▼ ✅ mt-1  (2 turns)           Multi-turn Consistency 0.95

      ┌─ Turn 1 ────────────────────────────────────────────┐
      │ case_id: multi-consistent-t1                        │
      │ input:    제 이름은 김철수입니다.                   │
      │ output:   이름을 기억하겠습니다.                    │
      │ latency:  820 ms          tokens: 45/120 (in/out)   │
      │ Policy:✅ Format:✅ Task:N/A                       │
      └─────────────────────────────────────────────────────┘

      ┌─ Turn 2 ────────────────────────────────────────────┐
      │ case_id: multi-consistent-t2                        │
      │ input:    제 이름이 뭔가요?                         │
      │ output:   김철수입니다.                             │
      │ latency:  920 ms          tokens: 50/30 (in/out)    │
      │ Policy:✅ Format:✅ Task:✅ Relevancy:0.92 Toxicity:0.02│
      │ ▼ Judge reasons (5)                                 │
      │   AnswerRelevancy: "응답이 직접 질문에 대답함"      │
      │   ...                                                │
      └─────────────────────────────────────────────────────┘

      Conversation-level: MultiTurnConsistency = 0.95 ✅

  ▶ 🟡 deep-relevancy-offtopic (1 turn)
  ▶ ❌ policy-fail-pii (1 turn)  Fail-Fast: PII 패턴 탐지
```

**필수 필드 (turn 단위)**:
- case_id, turn_id
- input (전문 또는 클릭 시 펼침)
- actual_output (전문 또는 클릭 시 펼침)
- latency_ms, usage (in/out tokens)
- 단계별 결과 배지: Policy / Format / Task / per-metric
- Judge reason: 메트릭 옆 토글 (기본 접힘, 상시 노출 옵션)
- raw_response: 디버깅 모드 토글

**Conversation-level**:
- 멀티턴인 경우 마지막에 MultiTurnConsistency 점수 표시
- conversation_id, 총 turn 수, 전체 conversation pass/fail

**Drill-down 동작**:
- 기본: 모든 conversation accordion 접힘
- 실패한 conversation 은 자동 펼침 (1단계만)
- "모두 펼침" / "실패만 펼침" 토글

---

### 3.3.1 R3.1 — 🤖 LLM 실패 사유 1문장 해설 (기본 on)

R3 turn 카드의 "쉬운 해설" 필드를 LLM 생성으로 교체. 기존 `_easy_explanation` 의 if-else 키워드 매칭은 **fallback** 으로 유지 (LLM 실패·비활성 시).

**위치**: 각 실패 turn 행의 "쉬운 해설" 섹션.

**ASCII 목업**:
```
│ ▼ Turn 2                                              🤖 LLM   │
│   🤖 응답에 질문과 무관한 요리 레시피가 섞여 있어 답변        │
│      관련성 기준(0.7)을 0.45 로 크게 밑돌았습니다.            │
```

**환경변수**: `SUMMARY_LLM_EASY_EXPLANATION` (기본 **on**).

**LLM 입력**: case_id + failure_message(500자 truncate) + task_completion passed + failed metric names.

**프롬프트 가드**: 정확히 1 문장. 기술 용어는 괄호 부연. 운영자가 다음 조치 판단 가능한 정보량.

**Fallback (현 `_easy_explanation` 키워드 매칭 그대로)**:
- "Promptfoo policy" → "민감정보 또는 금칙 패턴…"
- "Format Compliance Failed" → "응답 형식이 규격과 달라…"
- "Adapter Error / Connection" → "대상 시스템 통신 실패…"
- "TaskCompletion failed" → "핵심 정보 누락…"
- 기타 → "평가 기준 미달로 실패"

**비용**: 실패 case 당 1 call. 20 case 중 5 실패 → ~25s 추가. 기본 on 정당.

---

### 3.3.2 R3.2 — 🤖 LLM 조치 권장 1~2줄 (기본 off)

실패 case 마다 "다음에 시도할 만한 조치"를 LLM 이 1~2줄로 제안. **확정적 단정 금지, 권장 형태로만**.

**ASCII 목업**:
```
│   🤖 조치 권장                                                 │
│      • system prompt 끝에 "질문 주제에서 벗어나지 말 것"       │
│        지시어 추가 권장.                                       │
│      • RAG context 에 관련 도메인 문서 추가 검토.              │
```

**환경변수**: `SUMMARY_LLM_REMEDIATION_HINTS` (기본 **off**).

**LLM 입력**: case_id + failure_message + failed metric names + input/actual_output preview(200자).

**프롬프트 가드**: 1~2줄. 구체적 조치 (prompt 보강 / RAG 추가 / 임계치 재검토 등). "개선 가능" 이 아니라 "개선 시도 권장" 처럼 확정 금지.

**Fallback**: 텍스트 비움 → UI 에서 조치 섹션 자체 숨김 (공백 노이즈 방지).

**비용**: 실패 case 당 1 call. 기본 off — 운영 중 필요 시 on.

---

### 3.4 R4 — 시스템 에러 vs 품질 실패 분리

**원칙**: 다음 두 종류의 실패는 **다른 색·다른 섹션**으로 표시.

| 분류 | 정의 | 색 | 처리 권고 |
|---|---|---|---|
| **System Error** | HTTP 5xx / Conn refused / Timeout / wrapper crash | slate-blue | 재실행, 인프라 점검 |
| **Quality Failure** | Policy/Format 위반, Judge 점수 임계치 미달, Task criteria 미충족 | red | 모델 개선, 프롬프트 튜닝 |

**R1 헤더의 표시**: `Errors: System(0) / Quality(1)`
**R3 case row 의 표시**: 실패 사유 앞에 `[SYSTEM]` / `[QUALITY]` 태그

**Phase 3 와 연계**:
- Phase 3 Q1 의 `UniversalEvalOutput.error_type` enum (`"system" | "quality" | None`) 을 직접 사용
- Phase 2 임시 구현은 문자열 패턴 매칭 (`"HTTP 5"`, `"Connection"`, `"Timeout"` → system)

---

### 3.5 R5 — Build-over-build Delta (스트레치)

**조건부 활성**: 이전 성공 빌드의 `summary.json` 을 fetch 가능할 때만.

**위치**: R1 우측 또는 R2 카드 안에 작은 `Δ` 마커.

**표시 예**:
```
④ Answer Relevancy   🟢 PASS  (pass rate 90% Δ +5pp vs build #41)
```

**구현 옵션**:
- Jenkins env: `${BUILD_URL}/../lastSuccessfulBuild/artifact/eval-reports/.../summary.json` fetch
- airgap 환경: Jenkins workspace 내 `previous_summary.json` 캐시 (post-stage 에서 다음 빌드 위해 복사)

**비활성 시**: placeholder 없이 그냥 표시 안 함 (시각 노이즈 방지)

---

### 3.6 R6 — Jenkins `publishHTML` 통합

**Jenkinsfile post stage 요건**:
```groovy
publishHTML(target: [
    reportName: 'AI Eval Summary',
    reportDir:  "${REPORT_DIR}",
    reportFiles: 'summary.html',
    keepAll: true,
    alwaysLinkToLastBuild: true,
    allowMissing: false,        // ← Phase 2 기준 false 로 변경
])
```

**탭 노출 규칙**:
- 빌드 페이지 좌측 메뉴에 `AI Eval Summary` 항상 노출
- summary.html 누락 시 명시적 placeholder HTML (빌드가 어디서 실패했는지 안내)
- iframe 안에서 깨지지 않도록 inline CSS 유지

---

## 4. summary.json 스키마

리포트의 모든 표시는 이 JSON 한 파일에서 파생되어야 한다 (단일 진실 원천).

```json
{
  "schema_version": "1.0.0",
  "build": {
    "number": "42",
    "url": "http://jenkins.local/job/04-AI평가/42/",
    "started_at": "2026-04-22T10:15:00+09:00",
    "ended_at":   "2026-04-22T10:18:30+09:00",
    "overall_status": "passed"
  },
  "judge": {
    "model": "qwen3-coder:30b",
    "base_url": "http://host.docker.internal:11434",
    "temperature": 0,
    "digest": "sha256:7f3a..."
  },
  "dataset": {
    "path": "/var/knowledges/eval/data/golden.csv",
    "rows": 12,
    "sha256": "a1b2c3d...",
    "mtime": "2026-04-21T08:00:00+09:00"
  },
  "aggregate": {
    "conversations": 10,
    "turns": 12,
    "pass": 11,
    "fail": 1,
    "warn": 0,
    "system_errors": 0,
    "quality_failures": 1,
    "latency_ms": {"p50": 850, "p95": 1230, "p99": 1450},
    "tokens": {"total": 45200, "prompt": 30100, "completion": 15100}
  },
  "indicators": [
    {
      "id": 1, "name": "Policy Violation", "stage": "Fail-Fast",
      "applies_to": "common", "threshold": null,
      "pass": 11, "fail": 1, "skipped": 0,
      "score_distribution": null,
      "borderline_count": 0,
      "failed_case_ids": ["policy-fail-pii"]
    },
    { "id": 4, "name": "Answer Relevancy", "stage": "심층",
      "applies_to": "common", "threshold": 0.7,
      "pass": 9, "fail": 1, "skipped": 0,
      "score_distribution": [0,1,0,0,0,0,0,4,0,5],
      "borderline_count": 0,
      "failed_case_ids": ["deep-relevancy-offtopic"]
    }
    // ... ② ③ ⑤~⑪
  ],
  "conversations": [
    {
      "conversation_id": "mt-1",
      "status": "passed",
      "multi_turn_consistency": {"score": 0.95, "threshold": 0.7, "passed": true, "reason": "..."},
      "turns": [
        {
          "case_id": "multi-consistent-t1",
          "turn_id": 1,
          "input": "...",
          "actual_output": "...",
          "raw_response": "...",
          "latency_ms": 820,
          "usage": {"prompt_tokens": 45, "completion_tokens": 120, "total_tokens": 165},
          "error": null,
          "error_type": null,
          "policy_check": {"passed": true},
          "schema_check": {"passed": true},
          "task_completion": {"score": 1.0, "passed": true, "reason": "criteria met"},
          "metrics": [
            {"name": "AnswerRelevancyMetric", "score": 0.92, "threshold": 0.7,
             "passed": true, "reason": "응답이 직접 답함", "error": null}
          ]
        }
      ]
    }
  ]
}
```

### 4.1 필수/선택 필드 표시

| 경로 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `schema_version` | string | ✅ | semver, 본 문서 버전과 일치 |
| `build.overall_status` | enum | ✅ | `passed/failed/unstable` |
| `judge.{model,base_url,temperature}` | — | ✅ | digest 는 best-effort |
| `dataset.{rows,sha256}` | — | ✅ | mtime 은 권장 |
| `aggregate.latency_ms.{p50,p95,p99}` | int | ✅ | 한 case 만 있어도 채움 |
| `aggregate.tokens.total` | int | ✅ | usage 누락 case 는 0 처리 |
| `indicators[].score_distribution` | int[10] | 권장 | LLM Judge 메트릭만, 정량 메트릭은 null |
| `conversations[].turns[].error_type` | enum/null | ✅ Phase 3+ | Phase 2 는 임시로 null 가능 |

---

## 5. Acceptance Criteria

Phase 2 종료 게이트로 사용. **각 항목은 측정 가능해야 함.**

- [ ] **AC1** — Jenkins 의 `AI Eval Summary` 탭을 클릭하면 리포트가 5초 이내 렌더 완료
- [ ] **AC2** — 리뷰어 N=3 명에게 5개 빌드 (PASS/일부 FAIL/완전 FAIL/시스템 에러/RAG only) 의 결과를 30초 내 정확히 분류 요청 → 정답률 ≥ 90%
- [ ] **AC3** — summary.json 스키마가 §4 명세를 준수 (`jsonschema` 로 자동 검증 가능)
- [ ] **AC4** — 모든 11지표 카드가 R2 에 표시되며, RAG 전용 지표는 적용 0 case 시 ⚪ N/A 명시
- [ ] **AC5** — 시스템 에러와 품질 실패가 R1 헤더 카운트에 분리 표시
- [ ] **AC6** — Judge digest, dataset sha256 이 R1 헤더에 노출 (Phase 3 종료 시점에 충족)
- [ ] **AC7** — summary.html 단일 파일, inline CSS, 외부 자원 의존 없음 (`grep "src=\"http"` 결과 0)
- [ ] **AC8** — 빌드 페이지 description 에 summary 링크 자동 부여 (기존 04 Jenkinsfile 동작 유지)
- [ ] **AC9 (R1.1)** — 모든 빌드의 리포트에 🤖 임원 요약이 2~3 문장(≤300자) 생성, JSON 에 없는 숫자·case_id 를 언급하지 않음 (샘플 5건 수동 환각 체크 0건). summary.json 의 `aggregate.exec_summary.{text,source}` 필드 존재.
- [ ] **AC10 (R3.1)** — 모든 실패 case 에 🤖 쉬운 해설 1 문장. `SUMMARY_LLM_EASY_EXPLANATION=off` 로 실행 시 하드코딩 fallback 으로 degrade, 문장 내용은 계속 존재.
- [ ] **AC11 (LLM 결정성)** — 동일 summary.json 을 입력한 두 리포트 생성의 LLM 문장이 **bit-identical** (동일 `(role, canonical JSON sha256)` → 캐시 hit).
- [ ] **AC12 (LLM graceful degrade)** — `OLLAMA_BASE_URL=http://127.0.0.1:0` 로 LLM 접근 불가 상태에서 리포트 생성이 예외 없이 완주, 모든 role 의 narrative 가 `source="fallback"` 으로 출력.
- [ ] **AC13 (Phase 5.1 보정 세트)** — `golden.csv` 에 `calib=true` 가 하나라도 있으면 summary.json 의 `aggregate.calibration` 이 `{enabled, turn_count, case_ids, per_metric, overall:{mean,std,score_count}}` 완전 채워짐. HTML 헤더 "Judge 변동성" 라인에 `보정 σ=<std>` 노출. 보정 세트 빈 경우 `enabled=False` + "보정 세트: 미설정" 배지.
- [ ] **AC14 (Phase 5.2 N-repeat)** — `REPEAT_BORDERLINE_N=1` (기본) 에선 임의 metric 실행이 `measure()` 1 회만 호출. `REPEAT_BORDERLINE_N=3 BORDERLINE_MARGIN=0.05` 에선 점수가 임계치 ±0.05 경계 case 만 추가 2 회 재실행되어 3 샘플의 median 이 채택. summary.json 의 `aggregate.judge_calls_total` 이 전체 measure 호출 수와 일치. HTML 헤더에 `Judge calls=<n>` + `경계 재실행 N=3` 노출.

---

## 6. 비기능 요건

- **단일 HTML 파일**: Jenkins `publishHTML` 호환, iframe 격리 환경에서 동작
- **외부 자원 0**: CDN/CSS/JS 네트워크 fetch 금지 (airgap 호환)
- **언어**: 헤더·카드 라벨은 한국어 우선. 메트릭 명·기술 식별자(case_id, sha256 등) 는 영문 그대로
- **접근성**: 색만으로 PASS/FAIL 구분 금지. 항상 텍스트 배지(✅/❌/🟡) 동반
- **출력 결정성**: 동일 summary.json → 동일 HTML byte (타임스탬프·UUID 외)
- **크기**: summary.html ≤ 500 KB (10 case dataset 기준). drill-down 의 raw_response 는 lazy 표시

---

## 7. 변경 절차

본 spec 변경은 Phase 2 완료 후 다음 순서로:
1. spec 변경 PR (이 파일)
2. 골든 하네스(Phase 0.2) 의 expected_golden.json 동시 갱신 — 새 필드/구조 반영
3. summary.json 스키마 검증 테스트 추가
4. Phase 2 산출물(reporting/*.py, summary.html) 구현 PR
5. AC1~AC8 재검증 후 머지
