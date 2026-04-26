#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 오프라인 머신 이미지 로드 헬퍼
#
# offline-prefetch.sh 가 산출한 세 tarball (ttc-allinone + gitlab + dify-sandbox)
# 을 일괄 docker load 한다. 폐쇄망 머신에서 실행한다.
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
OLLAMA_TARGET=""   # 비우면 자동 감지 (~/.ollama/models 또는 WSL2 호스트 경로)
SKIP_OLLAMA=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)          ARCH="$2"; shift 2 ;;
        --dir)           DIR="$2";  shift 2 ;;
        --ollama-target) OLLAMA_TARGET="$2"; shift 2 ;;
        --no-ollama)     SKIP_OLLAMA=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -z "$DIR" ] && DIR="$ALLINONE_DIR/offline-assets/${ARCH}"
[ -d "$DIR" ] || { echo "디렉터리 없음: $DIR" >&2; exit 1; }

command -v docker >/dev/null || { echo "docker 명령이 없습니다" >&2; exit 1; }

# ─── 1) Docker 이미지 tarball load ─────────────────────────────────────────
shopt -s nullglob
# ollama-models-*.tar.gz 는 docker load 대상이 아님 — 별도 처리
image_tarballs=("$DIR"/*.tar.gz)
filtered=()
ollama_tarball=""
for tb in "${image_tarballs[@]}"; do
    case "$(basename "$tb")" in
        ollama-models-*) ollama_tarball="$tb" ;;
        *)               filtered+=("$tb") ;;
    esac
done
shopt -u nullglob

[ ${#filtered[@]} -eq 0 ] && { echo "이미지 tarball 이 없습니다: $DIR" >&2; exit 1; }

echo "[offline-load] arch=$ARCH dir=$DIR"
for tb in "${filtered[@]}"; do
    echo "[offline-load] docker load: $(basename "$tb")"
    gunzip -c "$tb" | docker load
done

echo ""
echo "[offline-load] 설치된 이미지:"
docker images --format '  {{.Repository}}:{{.Tag}}\t{{.Size}}' \
    | grep -E 'ttc-allinone|gitlab/gitlab-ce|yrzr/gitlab-ce-arm64v8|dify-sandbox' || true

# ─── 2) Ollama 모델 번들 추출 ──────────────────────────────────────────────
if [ "$SKIP_OLLAMA" -eq 0 ] && [ -n "$ollama_tarball" ]; then
    if [ -z "$OLLAMA_TARGET" ]; then
        # 자동 감지: WSL2 면 Windows 호스트 사용자 홈, 아니면 $HOME/.ollama/models
        if [ -f /proc/version ] && grep -qiE 'microsoft|wsl' /proc/version; then
            # 실재 경로 우선 탐색 (cmd.exe USERNAME 이 WSL 사용자명 반환하는 경우 회피)
            OLLAMA_TARGET=""
            for cand in /mnt/c/Users/*/.ollama; do
                [ -d "$cand" ] && { OLLAMA_TARGET="$cand/models"; break; }
            done
            if [ -z "$OLLAMA_TARGET" ] && command -v powershell.exe >/dev/null 2>&1; then
                WIN_USER="$(powershell.exe -NoProfile -Command '$env:USERNAME' 2>/dev/null | tr -d '\r\n' || true)"
                [ -n "$WIN_USER" ] && OLLAMA_TARGET="/mnt/c/Users/${WIN_USER}/.ollama/models"
            fi
            [ -z "$OLLAMA_TARGET" ] && OLLAMA_TARGET="$HOME/.ollama/models"
        else
            OLLAMA_TARGET="$HOME/.ollama/models"
        fi
    fi

    echo ""
    echo "[offline-load] Ollama 모델 추출"
    echo "  source: $(basename "$ollama_tarball")"
    echo "  target: $OLLAMA_TARGET"
    mkdir -p "$OLLAMA_TARGET"
    # --keep-newer-files: 호스트 측 더 최신 파일은 보존 (기존 모델 보호)
    tar -xzf "$ollama_tarball" -C "$OLLAMA_TARGET" --keep-newer-files 2>/dev/null \
        || tar -xzf "$ollama_tarball" -C "$OLLAMA_TARGET"
    echo "  추출 완료. 호스트 Ollama daemon 재시작 시 모델 자동 인식."
elif [ "$SKIP_OLLAMA" -eq 0 ] && [ -z "$ollama_tarball" ]; then
    echo ""
    echo "[offline-load] (ollama-models tarball 없음 — 호스트 측에서 별도 ollama pull 필요)"
fi

echo ""
echo "[offline-load] 다음: 폐쇄망에서 compose up"
echo "    docker compose -f docker-compose.wsl2.yaml up -d   # (또는 mac)"
