#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — macOS 기동 스크립트
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ALLINONE_DIR"
docker compose -f docker-compose.mac.yaml "$@" up -d

# ttc-gitlab 기동 후 Work Items UI "Truncate descriptions" 기본 OFF 패치.
# 백그라운드 — healthy 대기 자체가 수 분 걸려 foreground 로 걸면 `up -d`
# 의 빠른 완료 감각을 해친다. 로그는 nohup.out 대신 직접 stderr 로.
"$SCRIPT_DIR/gitlab-truncate-patch.sh" >&2 &
disown || true
