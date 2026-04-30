#!/usr/bin/env bash
# Run once to register .githooks/ as the git hooks directory.
#
# Behavior: sets `core.hooksPath` to `.githooks` so every commit
# automatically runs `.githooks/pre-commit` (Recording UI E2E).
#
# Bypass / disable: `git config --unset core.hooksPath`, or use
# `git commit --no-verify` per-commit (also used inside the hook).

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.githooks"
PRE_COMMIT="$HOOKS_DIR/pre-commit"

if [ ! -f "$PRE_COMMIT" ]; then
  echo "ERROR: $PRE_COMMIT missing — repo is corrupted or you are on a different branch" >&2
  exit 1
fi

chmod +x "$PRE_COMMIT"

CURRENT=$(git -C "$REPO_ROOT" config --get core.hooksPath || true)
if [ -n "$CURRENT" ] && [ "$CURRENT" != ".githooks" ]; then
  echo "⚠ core.hooksPath is already set to: $CURRENT"
  echo "  This script will overwrite it with .githooks. Back up the existing hook if you need it."
fi

git -C "$REPO_ROOT" config core.hooksPath .githooks

echo "✓ git hook installed"
echo "  - core.hooksPath = .githooks"
echo "  - pre-commit hook → $PRE_COMMIT"
echo ""
echo "Test: runs automatically on the next commit (~18s, only when recording_service changes)."
echo "Bypass: git commit --no-verify"
