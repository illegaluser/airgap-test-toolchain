#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 오프라인 이미지 export 헬퍼
#
# 폐쇄망 빌드 전략: 온라인 머신에서 build-{wsl2,mac}.sh 로 이미지를 빌드한 뒤
# docker save 로 tarball 을 만들고, 오프라인 머신에서 docker load 로 복원한다.
#
# 본 스크립트는 *재빌드하지 않음* — 이미 build-{wsl2,mac}.sh 가 만든
# ttc-allinone:wsl2-<tag> (또는 mac-<tag>) 를 retag 후 그대로 docker save 한다.
# 이전 구현은 별도 buildx builder (docker-container) 로 재빌드 + sending tarball
# 로 ~5분 + 165초의 순수 낭비가 있었다.
#
# Usage:
#   bash scripts/offline-prefetch.sh --arch amd64  (WSL2/Linux)
#   bash scripts/offline-prefetch.sh --arch arm64  (macOS Apple Silicon)
#   bash scripts/offline-prefetch.sh --arch amd64 --gitlab-image gitlab/gitlab-ce:18.11.0-ce.0
#
# 선행:
#   bash scripts/build-wsl2.sh --no-tarball  (또는 --no-tarball 없이 자동 호출됨)
#   bash scripts/build-mac.sh  --no-tarball
#
# 산출물:
#   offline-assets/<arch>/ttc-allinone-<arch>-<tag>.tar.gz
#   offline-assets/<arch>/gitlab-gitlab-ce-<version>-<arch>.tar.gz
#   offline-assets/<arch>/langgenius-dify-sandbox-<version>-<arch>.tar.gz
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ALLINONE_DIR"

ARCH="amd64"
TAG="${TAG:-dev}"
GITLAB_IMAGE="${GITLAB_IMAGE:-gitlab/gitlab-ce:18.11.0-ce.0}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-langgenius/dify-sandbox:0.2.10}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)          ARCH="$2"; shift 2 ;;
        --tag)           TAG="$2";  shift 2 ;;
        --gitlab-image)  GITLAB_IMAGE="$2"; shift 2 ;;
        --sandbox-image) SANDBOX_IMAGE="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

case "$ARCH" in
    amd64) PLATFORM="linux/amd64" ;;
    arm64) PLATFORM="linux/arm64" ;;
    *) echo "unsupported arch: $ARCH (amd64|arm64)" >&2; exit 2 ;;
esac

IMAGE="ttc-allinone:${ARCH}-${TAG}"
OUT_DIR="$ALLINONE_DIR/offline-assets/${ARCH}"
OUT_FILE="${OUT_DIR}/ttc-allinone-${ARCH}-${TAG}.tar.gz"

mkdir -p "$OUT_DIR"

echo "[prefetch] arch=$ARCH tag=$TAG platform=$PLATFORM image=$IMAGE"
echo "[prefetch] context=$ALLINONE_DIR"
echo "[prefetch] latest runtime pins:"
echo "  Jenkins   = jenkins/jenkins:2.555.1-lts-jdk21"
echo "  SonarQube = sonarqube:26.4.0.121862-community"
echo "  Dify API  = langgenius/dify-api:1.13.3"
echo "  Dify Web  = langgenius/dify-web:1.13.3"
echo "  Dify Plug = langgenius/dify-plugin-daemon:0.5.3-local"
echo "  GitLab    = $GITLAB_IMAGE"
echo "  Sandbox   = $SANDBOX_IMAGE  (Dify Code 노드 격리 실행 — 별도 service)"

if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[prefetch] 플러그인이 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi

# build-{wsl2,mac}.sh 가 이미 native 이미지를 만들어 둔 상태를 전제로,
# 별도 buildx builder 로 재빌드하지 않고 기존 이미지를 retag + save.
# (이전 구현은 docker-container builder 로 재빌드 + sending tarball — ~5분 + 165초
#  순수 낭비. cache 도 별도 builder 라 build-{wsl2,mac} 와 공유 안 됨.)
case "$ARCH" in
    amd64) SOURCE_TAG="ttc-allinone:wsl2-${TAG}" ;;
    arm64) SOURCE_TAG="ttc-allinone:mac-${TAG}"  ;;
esac

if ! docker image inspect "$SOURCE_TAG" >/dev/null 2>&1; then
    echo "[prefetch] 원본 이미지 $SOURCE_TAG 가 없습니다." >&2
    case "$ARCH" in
        amd64) echo "  → bash scripts/build-wsl2.sh --no-tarball  먼저 실행 후 재시도" >&2 ;;
        arm64) echo "  → bash scripts/build-mac.sh --no-tarball   먼저 실행 후 재시도" >&2 ;;
    esac
    exit 1
fi

# tarball 안 이미지 이름을 arch-tag 정규형으로 통일하기 위해 alias 만 추가 (실 데이터 복사 없음)
docker tag "$SOURCE_TAG" "$IMAGE"
echo "[prefetch] 원본 이미지 재사용: $SOURCE_TAG → $IMAGE (tag alias)"

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
# (docker-compose.{wsl2|mac}.yaml 은 gitlab/gitlab-ce:18.11.0-ce.0 을 참조)
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

# Dify Sandbox 이미지도 반출 — Dify Workflow 의 Code 노드 (context_filter / json_parser
# 등) 가 실 호출 시 의존. 통합 이미지에 sandbox binary 가 포함되지 않음.
SANDBOX_TAG_SAFE=$(echo "$SANDBOX_IMAGE" | sed 's#[/:]#-#g')
SANDBOX_OUT_FILE="${OUT_DIR}/${SANDBOX_TAG_SAFE}-${ARCH}.tar.gz"

echo "[prefetch] Sandbox 이미지 pull + save: $SANDBOX_IMAGE"
docker pull --platform "$PLATFORM" "$SANDBOX_IMAGE"
docker save "$SANDBOX_IMAGE" | gzip > "$SANDBOX_OUT_FILE"

SANDBOX_SIZE=$(du -h "$SANDBOX_OUT_FILE" | cut -f1)
SANDBOX_SHA=$(sha256sum "$SANDBOX_OUT_FILE" | cut -d' ' -f1)
cat > "${OUT_DIR}/${SANDBOX_TAG_SAFE}-${ARCH}.meta" <<META
image: $SANDBOX_IMAGE
arch: $ARCH
platform: $PLATFORM
tarball: $(basename "$SANDBOX_OUT_FILE")
size: $SANDBOX_SIZE
sha256: $SANDBOX_SHA
built_at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
META

echo "  sandbox      : $SANDBOX_OUT_FILE ($SANDBOX_SIZE, sha256=$SANDBOX_SHA)"
echo ""
echo "[prefetch] 오프라인 머신 복원 (세 tarball 모두 load 필요):"
echo "    docker load -i $OUT_FILE"
echo "    docker load -i $GITLAB_OUT_FILE"
echo "    docker load -i $SANDBOX_OUT_FILE"
echo "  또는 일괄: bash scripts/offline-load.sh --arch $ARCH"
