#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — WSL2 기동 스크립트
# docker compose 래퍼. 데이터 볼륨은 WSL2 HOME 하위에 둔다.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ 의 상위가 code-AI-quality-allinone/ 이고 거기에 compose 파일이 있다.
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ALLINONE_DIR"
docker compose -f docker-compose.wsl2.yaml "$@" up -d

# ttc-gitlab 기동 후 Work Items UI "Truncate descriptions" 기본 OFF 패치.
# 백그라운드 실행 — healthy 대기 자체가 수 분 걸려 foreground 면 UX 를 해친다.
"$SCRIPT_DIR/gitlab-truncate-patch.sh" >&2 &
disown || true
