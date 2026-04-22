# eval_runner — AI 평가 파이프라인 구현

`04 AI평가.jenkinsPipeline` (Phase 5: AI 에이전트 평가) 의 실제 평가 엔진.

## 정본 스펙

본 디렉터리의 **평가 스펙 정본** 은 상위 레포의 [readme.md](../../readme.md) **§5.1 DSCORE-TTC AI평가** 이다.
해당 문서가 11지표 × 5단계를 정의하며, 이 코드는 그 규정을 구현한다.

과거 `eval_runner/외부 AI 에이전트 평가 시스템 구축 프로젝트 계획서.md` 가 제안한 7지표/3단계 설계안은 **폐기됨** (2026-04-22).
해당 초기 제안서와 현 정본 간 차이는 [docs/PLAN_AI_EVAL_PIPELINE.md](../docs/PLAN_AI_EVAL_PIPELINE.md) §1 에 기록.

## 개선 로드맵

진행 중인 체계적 개선 작업은 [docs/PLAN_AI_EVAL_PIPELINE.md](../docs/PLAN_AI_EVAL_PIPELINE.md) 참조. Phase 0~5 의 6단계 로드맵.

## 모듈 구조

```
eval_runner/
├── adapters/          # UniversalEvalOutput + BaseAdapter + HTTP/Browser 구현
├── configs/           # schema.json (Format Compliance), security.yaml/security_assert.py (Policy)
├── tests/             # test_runner.py (pytest 기반 파이프라인 실행)
├── ollama_wrapper_api.py  # 로컬 Ollama → OpenAI-compatible HTTP 래퍼
├── Jenkinsfile        # 서브 파이프라인 정의
└── SUCCESS_CRITERIA_GUIDE.md  # success_criteria DSL 사용법
```

## 현 phase 동작 범위

- **Wrapper 모드**: `local_ollama_wrapper` 단일. OpenAI/Gemini 는 향후 phase 로 이연.
- **Langfuse**: 조건부. 크리덴셜 미설정 시 자동 비활성. 11지표는 summary.json 만으로 완전 기록.
- **Judge**: 기본 `qwen3-coder:30b`, temperature=0.

## 관련 문서

- [../../readme.md](../../readme.md) §5.1 — 정본 스펙
- [../docs/PLAN_AI_EVAL_PIPELINE.md](../docs/PLAN_AI_EVAL_PIPELINE.md) — 개선 계획서
- [SUCCESS_CRITERIA_GUIDE.md](./SUCCESS_CRITERIA_GUIDE.md) — Golden dataset 작성 가이드
- [../CONVERSATION_LOG.md](../CONVERSATION_LOG.md) — 세션별 설계 결정 이력
