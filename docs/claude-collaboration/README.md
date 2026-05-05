# Claude Code 협업 가이드 / 메모리 공유

본 디렉터리는 본 프로젝트에서 Claude Code 와 협업할 때 사용 중인 **CLAUDE.md
가이드라인** 과 **자동 누적 메모리** 를 팀 전체와 공유하기 위한 곳이다. 새로
합류하는 사람도 동일한 작업 컨텍스트로 Claude 와 협업할 수 있게 한다.

## 구성

### 프로젝트 가이드라인 (CLAUDE.md)

각 작업 디렉터리의 `CLAUDE.md` 가 Claude Code 에 자동 로드된다.

- [`/CLAUDE.md`](../../CLAUDE.md) — 저장소 루트 공통 가이드 (LLM 코딩 실수
  방지 4원칙: Think Before Coding / Simplicity First / Surgical Changes /
  Goal-Driven Execution).
- [`/playwright-allinone/CLAUDE.md`](../../playwright-allinone/CLAUDE.md) —
  playwright-allinone 서브 트리 한정. 루트와 동일 4원칙을 보강.

### 누적 메모리 (`MEMORY.md` + `*.md`)

Claude 가 작업 중 학습한 사용자 선호, 프로젝트 맥락, 피드백 등을 영속화한
파일들. `MEMORY.md` 가 인덱스, 나머지는 항목별 본문.

원본은 각 작업자 로컬 (`~/.claude/projects/<encoded>/memory/`) 에 있고,
Claude 가 자동 로드한다. 본 디렉터리는 그 **스냅샷** — 새 합류자가 자기
로컬 메모리 디렉터리로 복사해 시작점으로 삼을 수 있게 한다.

#### 인덱스 파일

- [`MEMORY.md`](MEMORY.md) — 항목별 한 줄 요약 + 링크.

#### 피드백 (작업 방식 선호)

- [`feedback_commit_message_style.md`](feedback_commit_message_style.md) —
  커밋 메시지: 비개발자도 이해 가능한 한국어 + 항목별 본문.
- [`feedback_commit_messages.md`](feedback_commit_messages.md) — (이전 버전
  잔존, 위 파일과 함께 참고).
- [`feedback_document_decisions.md`](feedback_document_decisions.md) —
  비-사소 변경 시 `docs/PLAN_*.md` 작성 (대안/사유/트레이드오프 명시).
- [`feedback_e2e_headless_default.md`](feedback_e2e_headless_default.md) —
  e2e 는 headless 가 기본.
- [`feedback_fix_lint_warnings.md`](feedback_fix_lint_warnings.md) — 편집
  파일의 lint 경고는 모두 수정 대상.
- [`feedback_no_buildx.md`](feedback_no_buildx.md) — docker buildx 미사용.
- [`feedback_no_speculation.md`](feedback_no_speculation.md) — 진단/분석은
  직접 실행해 검증된 사실만 사용. 추측 금지.

#### 프로젝트 맥락

- [`project_naver_auth_stepping_stone.md`](project_naver_auth_stepping_stone.md)
  — auth-profile 설계는 네이버 직접 테스트가 아니라 외부 서비스 테스트의
  전 단계.

## 새 합류자 사용법

1. Claude Code 를 처음 설치한 직후, 본 저장소를 클론.
2. 자동 로드 위치에 메모리 복사 (한 번만):
   ```sh
   PROJ_ENC=$(pwd | sed 's|/|-|g')
   mkdir -p "$HOME/.claude/projects/${PROJ_ENC}/memory"
   cp docs/claude-collaboration/*.md "$HOME/.claude/projects/${PROJ_ENC}/memory/"
   ```
3. 이후 Claude 가 본인 로컬 작업으로 새 메모리 추가/수정 가능. 팀 전체에
   반영하려면 본 디렉터리에 다시 복사 후 PR.

## 운영 메모

- 본 스냅샷은 시점 동결. 원본 메모리는 누가 사용하느냐에 따라 진화함.
- "사용자 개인" 정보 (이메일·이름) 는 메모리 본문에 안 넣는 것이 원칙. 이미
  들어가 있는 항목은 검토 후 제거 또는 일반화.
- CLAUDE.md 변경은 코드 변경처럼 PR 리뷰 대상.
