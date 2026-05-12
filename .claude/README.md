# `.claude/` — 팀 공통 Claude Code 설정

이 디렉터리는 이 저장소를 작업하는 모든 사람이 동일한 Claude Code 환경을 갖도록 하는
**팀 공유 설정**입니다. 개인 환경은 별도로 `settings.local.json` 에 두며, 이 파일만
[.gitignore](../.gitignore) 로 차단됩니다.

## 구조

```text
.claude/
├── README.md             ← 이 문서
├── settings.json         ← 팀 공통 권한 화이트리스트 (커밋)
├── settings.local.json   ← 개인용 추가 권한 (gitignored, 각자 자유)
├── agents/               ← 팀 공통 서브에이전트 (.md 파일 1개당 1 에이전트)
├── skills/               ← 팀 공통 slash skills (이름 폴더에 SKILL.md)
└── commands/             ← 팀 공통 slash commands (.md)
```

각 디렉터리의 작성 규칙은 Claude Code 공식 문서를 참조하세요.
현재는 골격만 두었고, 팀에서 필요해지면 그때 추가합니다.

## settings.json — 허용된 명령

루트의 문서화된 런처(`build.sh`, `run-recording-ui.sh`, `run-replay-ui.sh`,
`export-airgap.sh`, `scripts/install-git-hooks.sh`, `scripts/run-mac.sh`,
`scripts/run-wsl2.sh`)와 읽기 전용 git/test/lint 명령만 무프롬프트로 허용합니다.
파괴적 명령(`rm -rf`, `git push --force`, `git reset --hard`, `git clean -fd`)은
명시적으로 거부합니다.

개인 환경에서 추가로 허용하고 싶은 명령은 `settings.local.json` 에 적으세요.
저장소에는 올라가지 않습니다.

## 메모리는 공유되지 않습니다

Claude Code 의 "memory" 는 사용자 홈(`~/.claude/projects/.../memory/`)에 저장되어
각자 다릅니다. 팀 전체에 알리고 싶은 사실은 루트 [CLAUDE.md](../CLAUDE.md) 에
직접 적어 주세요. 그래야 새로 합류하는 사람과 새 세션의 Claude 가 동일하게 인지합니다.

## 마켓플레이스 플러그인 (선택)

다음 Anthropic 공식 플러그인이 이 저장소 작업에 유용합니다 (각자 설치):

- `code-review` — PR 리뷰
- `security-guidance` — 보안 가이드
- `skill-creator` — 새 skill 작성 도우미
- `commit-commands`, `pr-review-toolkit`

설치는 Claude Code 안에서 `/plugin` 으로 진행하세요. 이 저장소는 플러그인 의존을
강제하지 않으며, 없어도 모든 작업이 동작합니다.
