#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 오프라인 이미지 export 헬퍼
#
# 폐쇄망 빌드 전략: 온라인 머신에서 이미지를 빌드한 뒤 docker save 로 tarball
# 을 만들고, 오프라인 머신에서 docker load 로 복원한다.
#
# 빌드 컨텍스트: 이 폴더 자체 (자체 완결).
#
# Usage:
#   bash scripts/offline-prefetch.sh --arch amd64  (WSL2/Linux)
#   bash scripts/offline-prefetch.sh --arch arm64  (macOS Apple Silicon)
#   bash scripts/offline-prefetch.sh --arch amd64 --gitlab-image gitlab/gitlab-ce:17.4.2-ce.0
#
# 선행:
#   bash scripts/download-plugins.sh   # 플러그인 바이너리 준비 (온라인)
#
# 산출물:
#   offline-assets/<arch>/ttc-allinone-<arch>-<tag>.tar.gz
#   offline-assets/<arch>/gitlab-gitlab-ce-<version>-<arch>.tar.gz
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ALLINONE_DIR"

ARCH="amd64"
TAG="${TAG:-dev}"
GITLAB_IMAGE="${GITLAB_IMAGE:-gitlab/gitlab-ce:17.4.2-ce.0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)         ARCH="$2"; shift 2 ;;
        --tag)          TAG="$2";  shift 2 ;;
        --gitlab-image) GITLAB_IMAGE="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

case "$ARCH" in
    amd64) PLATFORM="linux/amd64" ;;
    arm64) PLATFORM="linux/arm64" ;;
    *) echo "unsupported arch: $ARCH (amd64|arm64)" >&2; exit 2 ;;
esac

# arch 기본 GitLab 이미지 매핑. 사용자가 --gitlab-image 로 명시 override 한
# 경우(= 기본값과 다른 값) 는 그대로 보존. 공식 gitlab-ce 는 arm64 manifest 가
# 없어 Apple Silicon 에서는 Rosetta 경유가 되므로 커뮤니티 arm64 포트로 전환.
if [ "${GITLAB_IMAGE}" = "gitlab/gitlab-ce:17.4.2-ce.0" ]; then
    case "$ARCH" in
        amd64) GITLAB_IMAGE="gitlab/gitlab-ce:17.4.2-ce.0" ;;
        arm64) GITLAB_IMAGE="yrzr/gitlab-ce-arm64v8:17.4.2-ce.0" ;;
    esac
fi

IMAGE="ttc-allinone:${ARCH}-${TAG}"
OUT_DIR="$ALLINONE_DIR/offline-assets/${ARCH}"
OUT_FILE="${OUT_DIR}/ttc-allinone-${ARCH}-${TAG}.tar.gz"

mkdir -p "$OUT_DIR"

echo "[prefetch] arch=$ARCH tag=$TAG platform=$PLATFORM image=$IMAGE"
echo "[prefetch] context=$ALLINONE_DIR"

if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[prefetch] 플러그인이 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi

docker buildx inspect ttc-allinone-builder >/dev/null 2>&1 || \
    docker buildx create --name ttc-allinone-builder --use

docker buildx build \
    --builder ttc-allinone-builder \
    --platform "$PLATFORM" \
    -f "$ALLINONE_DIR/Dockerfile" \
    -t "$IMAGE" \
    --load \
    "$ALLINONE_DIR"

echo "[prefetch] saving to $OUT_FILE"
docker save "$IMAGE" | gzip > "$OUT_FILE"

SIZE=$(du -h "$OUT_FILE" | cut -f1)
SHA=$(sha256sum "$OUT_FILE" | cut -d' ' -f1)
cat > "${OUT_DIR}/ttc-allinone-${ARCH}-${TAG}.meta" <<META
image: $IMAGE
arch: $ARCH
platform: $PLATFORM
tag: $TAG
tarball: $(basename "$OUT_FILE")
size: $SIZE
sha256: $SHA
built_at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
META

# GitLab 런타임 이미지도 반드시 함께 반출해야 폐쇄망에서 compose up 가능
# (docker-compose.{wsl2|mac}.yaml 은 gitlab/gitlab-ce:17.4.2-ce.0 을 참조)
GITLAB_TAG_SAFE=$(echo "$GITLAB_IMAGE" | sed 's#[/:]#-#g')
GITLAB_OUT_FILE="${OUT_DIR}/${GITLAB_TAG_SAFE}-${ARCH}.tar.gz"

echo "[prefetch] GitLab 이미지 pull + save: $GITLAB_IMAGE"
docker pull --platform "$PLATFORM" "$GITLAB_IMAGE"
docker save "$GITLAB_IMAGE" | gzip > "$GITLAB_OUT_FILE"

GITLAB_SIZE=$(du -h "$GITLAB_OUT_FILE" | cut -f1)
GITLAB_SHA=$(sha256sum "$GITLAB_OUT_FILE" | cut -d' ' -f1)
cat > "${OUT_DIR}/${GITLAB_TAG_SAFE}-${ARCH}.meta" <<META
image: $GITLAB_IMAGE
arch: $ARCH
platform: $PLATFORM
tarball: $(basename "$GITLAB_OUT_FILE")
size: $GITLAB_SIZE
sha256: $GITLAB_SHA
built_at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
META

echo "[prefetch] 완료:"
echo "  ttc-allinone : $OUT_FILE ($SIZE, sha256=$SHA)"
echo "  gitlab       : $GITLAB_OUT_FILE ($GITLAB_SIZE, sha256=$GITLAB_SHA)"
echo ""
echo "[prefetch] 오프라인 머신 복원 (두 tarball 모두 load 필요):"
echo "    docker load -i $OUT_FILE"
echo "    docker load -i $GITLAB_OUT_FILE"
echo "  또는 일괄: bash scripts/offline-load.sh --arch $ARCH"
