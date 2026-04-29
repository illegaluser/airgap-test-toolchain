#!/usr/bin/env bash
# 한 번 실행으로 .githooks/ 를 git hooks 디렉토리로 등록.
#
# 동작: `core.hooksPath` 를 `.githooks` 로 설정 → 매 commit 마다
# `.githooks/pre-commit` 자동 실행 (Recording UI E2E).
#
# 우회 / 해제: `git config --unset core.hooksPath` 또는 hook 내부에서
# 사용한 `git commit --no-verify` (커밋 단위).

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.githooks"
PRE_COMMIT="$HOOKS_DIR/pre-commit"

if [ ! -f "$PRE_COMMIT" ]; then
  echo "ERROR: $PRE_COMMIT 미존재 — repo 가 손상됐거나 다른 브랜치" >&2
  exit 1
fi

chmod +x "$PRE_COMMIT"

CURRENT=$(git -C "$REPO_ROOT" config --get core.hooksPath || true)
if [ -n "$CURRENT" ] && [ "$CURRENT" != ".githooks" ]; then
  echo "⚠ core.hooksPath 가 이미 설정됨: $CURRENT"
  echo "  본 스크립트는 .githooks 로 덮어씁니다. 기존 hook 이 필요하면 백업하세요."
fi

git -C "$REPO_ROOT" config core.hooksPath .githooks

echo "✓ git hook 설치 완료"
echo "  - core.hooksPath = .githooks"
echo "  - pre-commit hook → $PRE_COMMIT"
echo ""
echo "테스트: 다음 commit 시 자동 실행 (~18s, recording_service 변경 시만)."
echo "우회 : git commit --no-verify"
