# Test Planning RAG — 샘플 KB 문서 세트

본 디렉토리의 샘플 문서를 Dify console GUI 로 두 KB 에 업로드하면 Test Planning Brain
챗봇을 즉시 검증할 수 있다. 자세한 설계는 `playwright-allinone/docs/PLAN_TEST_PLANNING_RAG.md`.

## 파일 목록

| 파일 | KB 대상 | 형식 | 내용 |
| --- | --- | --- | --- |
| `spec.md` | `kb_project_info` | Markdown | 결제 모듈 v1.2 기능 명세 (가짜 spec) |
| `feature_login.md` | `kb_project_info` | Markdown | 사용자 로그인 사용자 시나리오 |
| `api.csv` | `kb_project_info` | CSV | 5 endpoint API 문서 |
| `test_theory_boundary_value.md` | `kb_test_theory` | Markdown | 경계값 분석 (BVA) 테스트 설계 기법 |

## 형식 변환 가이드

Dify 1.13.3 의 기본 ETL (`ETL_TYPE=dify`) 은 다음 형식만 지원:

- `pdf`, `docx`, `csv`, `txt`, `md`, `mdx`, `html`, `htm`, `xlsx`, `xls`

→ 본 샘플은 모두 `md` (Markdown) 또는 `csv` 로 작성됐다. **PPTX 는 미지원** —
PDF / Markdown 변환 후 업로드 (PLAN §3.1 참조).

PDF / DOCX 로 변환해 업로드하려면:

```bash
# Markdown → PDF (pandoc)
pandoc spec.md -o spec.pdf

# Markdown → DOCX (pandoc)
pandoc feature_login.md -o feature_login.docx
```

## 업로드 절차 (Dify console GUI)

1. 브라우저 → `http://localhost:18081` → admin 로그인 (`admin@example.com` /
   `Admin1234!`).
2. 좌측 메뉴 → **Knowledge** → 두 KB 가 자동 생성돼 있어야 함:
   - `kb_project_info`
   - `kb_test_theory`
3. 각 KB 클릭 → 우측 상단 **Add documents** → 파일 드래그 또는 선택.
4. 청킹 모드 — **Automatic** (PLAN 의 기본 chunk_size=500, overlap=50 자동 적용).
5. 인덱싱 시작 → 완료까지 대기 (5 청크 미만이면 1-2 분).
6. 챗봇 검증 — `Test Planning Brain` 앱 → "결제 모듈 테스트 계획서 만들어줘" 입력.

## 챗봇 호출 예시

### plan 모드 (테스트 계획서)

```text
요청: "결제 모듈 테스트 계획서 만들어줘"
output_mode: plan
target_module: 결제 모듈
```

기대 출력: IEEE 829-lite 8 섹션 Markdown + traceability 표.

### scenario_dsl 모드 (14-DSL JSON)

```text
요청: "결제 정상 케이스를 자동 실행 가능한 JSON 으로"
output_mode: scenario_dsl
target_module: 결제
```

기대 출력: `[{...}, ...]` 순수 JSON list + `# traceability:` 주석.
파서: `out.split("\n#",1)[0]` → `_validate_scenario` 통과.

### both 모드 (계획서 + 시나리오 + JSON)

```text
요청: "로그인 기능 검증을 위한 계획서와 시나리오 + DSL"
output_mode: both
target_module: 로그인
```

기대 출력: 계획서 → 자연어 시나리오 → fenced 14-DSL JSON.

## 트러블슈팅

### KB 가 자동 생성되지 않음

- `provision.sh` 실행 시 `KB IDs: project=...` 로그 확인.
- 미실행 시: 컨테이너 재기동 (volume wipe 후 fresh provision).
- 수동 생성: `POST /console/api/datasets` 직접 호출 (PLAN §5.5 T-02 참조).

### 인덱싱 실패

- 호스트 Ollama 의 `bona/bge-m3-korean:latest` 가 실행 중이어야 함.
  `ollama list | grep bge-m3-korean` 로 확인.
- Dify console → 모델 → Ollama 공급자 → bge-m3-korean 등록 상태 점검.

### 챗봇이 "KB 미확인" 만 답변

- KB 에 문서가 아직 없거나, retrieval score 가 임계값 (0.5 / 0.6) 미만.
- query 에 target_module 명시 (예: "결제 모듈" 단독 — 더 구체적으로 "결제 모듈의 카드 결제 시나리오").
