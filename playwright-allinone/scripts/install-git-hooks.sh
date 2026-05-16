#!/usr/bin/env bash
# 한 번 실행으로 .githooks/ 를 git hooks 디렉토리로 등록.
#
# 동작: `core.hooksPath` 를 `.githooks` 로 설정 → 매 commit·push 시
#   - .githooks/pre-commit  자동 실행 (Phase 3 도입 예정 — 현재 placeholder)
#   - .githooks/pre-push    자동 실행 (e2e-test/ 전체 + Replay UI 자산 stale 갱신)
#
# 설계 근거: ../docs/PLAN_E2E_REWRITE.md
# 우회 / 해제: `git config --unset core.hooksPath` 또는 `--no-verify` (커밋/푸시 단위).

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.githooks"
PRE_COMMIT="$HOOKS_DIR/pre-commit"
PRE_PUSH="$HOOKS_DIR/pre-push"

if [ ! -f "$PRE_COMMIT" ]; then
  echo "ERROR: $PRE_COMMIT 미존재 — repo 가 손상됐거나 다른 브랜치" >&2
  exit 1
fi
if [ ! -f "$PRE_PUSH" ]; then
  echo "ERROR: $PRE_PUSH 미존재 — repo 가 손상됐거나 다른 브랜치" >&2
  exit 1
fi

chmod +x "$PRE_COMMIT" "$PRE_PUSH"

CURRENT=$(git -C "$REPO_ROOT" config --get core.hooksPath || true)
if [ -n "$CURRENT" ] && [ "$CURRENT" != ".githooks" ]; then
  echo "⚠ core.hooksPath 가 이미 설정됨: $CURRENT"
  echo "  본 스크립트는 .githooks 로 덮어씁니다. 기존 hook 이 필요하면 백업하세요."
fi

git -C "$REPO_ROOT" config core.hooksPath .githooks

echo "✓ git hook 설치 완료"
echo "  - core.hooksPath = .githooks"
echo "  - pre-commit hook → $PRE_COMMIT"
echo "  - pre-push   hook → $PRE_PUSH"
echo ""
echo "동작: commit 마다 e2e-test/unit/ 발사 (A 그룹 emit/generator, < 30s)."
echo "      push 마다 e2e-test/ 전체 + Replay UI 휴대용 자산 stale 자동 갱신."
echo "우회: git commit --no-verify  /  git push --no-verify"
