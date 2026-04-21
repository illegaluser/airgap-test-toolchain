#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 오프라인 머신 이미지 로드 헬퍼
#
# offline-prefetch.sh 가 산출한 두 tarball (ttc-allinone + gitlab) 을 일괄
# docker load 한다. 폐쇄망 머신에서 실행한다.
#
# Usage:
#   bash scripts/offline-load.sh --arch amd64
#   bash scripts/offline-load.sh --arch arm64 --dir /media/usb/offline-assets
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ARCH="amd64"
DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch) ARCH="$2"; shift 2 ;;
        --dir)  DIR="$2";  shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -z "$DIR" ] && DIR="$ALLINONE_DIR/offline-assets/${ARCH}"
[ -d "$DIR" ] || { echo "디렉터리 없음: $DIR" >&2; exit 1; }

command -v docker >/dev/null || { echo "docker 명령이 없습니다" >&2; exit 1; }

shopt -s nullglob
tarballs=("$DIR"/*.tar.gz)
shopt -u nullglob
[ ${#tarballs[@]} -eq 0 ] && { echo "tarball 이 없습니다: $DIR" >&2; exit 1; }

echo "[offline-load] arch=$ARCH dir=$DIR"
for tb in "${tarballs[@]}"; do
    echo "[offline-load] load: $(basename "$tb")"
    gunzip -c "$tb" | docker load
done

echo ""
echo "[offline-load] 완료. 설치된 이미지:"
docker images --format '  {{.Repository}}:{{.Tag}}\t{{.Size}}' \
    | grep -E 'ttc-allinone|gitlab/gitlab-ce|yrzr/gitlab-ce-arm64v8' || true

echo ""
echo "[offline-load] 다음: 폐쇄망에서 compose up"
echo "    docker compose -f docker-compose.wsl2.yaml up -d   # (또는 mac)"
