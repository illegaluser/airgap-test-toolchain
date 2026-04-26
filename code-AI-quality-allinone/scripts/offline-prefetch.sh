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
        --no-ollama)     OLLAMA_BUNDLE=0; shift ;;
        --ollama-models) OLLAMA_MODELS="$2"; shift 2 ;;
        --ollama-dir)    OLLAMA_MODELS_DIR="$2"; shift 2 ;;
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

# ─── Ollama 모델 번들링 ──────────────────────────────────────────────────────
# 호스트의 ~/.ollama/models/ 에서 *whitelist* 에 해당하는 모델만 추출 (manifest +
# 그 manifest 가 참조하는 blobs). 폐쇄망에서 ~/.ollama/models/ 로 풀어 그대로 사용.
#
# whitelist override : OLLAMA_MODELS="gemma4:e4b,qwen3-embedding:0.6b,bge-m3"
# models dir override: OLLAMA_MODELS_DIR=/path/to/models
# 비활성화           : --no-ollama 또는 OLLAMA_BUNDLE=0
OLLAMA_BUNDLE="${OLLAMA_BUNDLE:-1}"
OLLAMA_MODELS="${OLLAMA_MODELS:-gemma4:e4b,qwen3-embedding:0.6b,bge-m3}"

if [[ "$OLLAMA_BUNDLE" == "1" ]]; then
    # 모델 디렉터리 자동 감지
    if [[ -z "${OLLAMA_MODELS_DIR:-}" ]]; then
        if [[ -f /proc/version ]] && grep -qiE 'microsoft|wsl' /proc/version; then
            # WSL2 — Windows 호스트의 사용자 홈 검색.
            # cmd.exe %USERNAME% 가 WSL 사용자명을 반환하는 경우가 있어 신뢰 불가 →
            # /mnt/c/Users/*/.ollama/models 탐색으로 실재 경로 찾기.
            OLLAMA_MODELS_DIR=""
            for cand in /mnt/c/Users/*/.ollama/models; do
                [[ -d "$cand" ]] && { OLLAMA_MODELS_DIR="$cand"; break; }
            done
            # fallback: powershell 로 Windows USERPROFILE 직접 조회
            if [[ -z "$OLLAMA_MODELS_DIR" ]] && command -v powershell.exe >/dev/null 2>&1; then
                WIN_USER="$(powershell.exe -NoProfile -Command '$env:USERNAME' 2>/dev/null | tr -d '\r\n' || true)"
                [[ -n "$WIN_USER" ]] && OLLAMA_MODELS_DIR="/mnt/c/Users/${WIN_USER}/.ollama/models"
            fi
        else
            OLLAMA_MODELS_DIR="$HOME/.ollama/models"
        fi
    fi

    OLLAMA_OUT_FILE="${OUT_DIR}/ollama-models-${ARCH}.tar.gz"

    if [[ ! -d "$OLLAMA_MODELS_DIR" ]]; then
        echo "[prefetch] WARN: Ollama 모델 디렉터리 없음 — skip ($OLLAMA_MODELS_DIR)"
    elif ! command -v python3 >/dev/null 2>&1; then
        echo "[prefetch] WARN: python3 필요 (manifest 파싱) — Ollama 번들 skip" >&2
    else
        echo ""
        echo "[prefetch] Ollama 모델 번들링: $OLLAMA_MODELS_DIR"
        echo "  whitelist: $OLLAMA_MODELS"

        STAGE="$(mktemp -d)"
        mkdir -p "$STAGE/manifests" "$STAGE/blobs"
        BUNDLED_OK=()
        BUNDLED_MISS=()

        IFS=',' read -ra _MODELS <<< "$OLLAMA_MODELS"
        for spec in "${_MODELS[@]}"; do
            # 'gemma4:e4b' → name=gemma4 tag=e4b. ':' 없으면 tag=latest
            name="${spec%%:*}"
            tag="${spec#*:}"
            [[ "$name" == "$tag" ]] && tag="latest"
            manifest_path="$OLLAMA_MODELS_DIR/manifests/registry.ollama.ai/library/$name/$tag"
            if [[ ! -f "$manifest_path" ]]; then
                BUNDLED_MISS+=("$spec")
                continue
            fi
            # manifest 복사
            mkdir -p "$STAGE/manifests/registry.ollama.ai/library/$name"
            cp "$manifest_path" "$STAGE/manifests/registry.ollama.ai/library/$name/$tag"
            # 종속 blobs 추출 (config + layers) — python3 로 파싱 (jq 불필요)
            digests=$(python3 -c "
import json, sys
m = json.load(open(sys.argv[1]))
out = []
cfg = (m.get('config') or {}).get('digest')
if cfg: out.append(cfg)
for L in (m.get('layers') or []):
    d = L.get('digest')
    if d: out.append(d)
print('\n'.join(out))
" "$manifest_path" | sed 's/^sha256://')
            for d in $digests; do
                src="$OLLAMA_MODELS_DIR/blobs/sha256-$d"
                if [[ -f "$src" ]]; then
                    cp -n "$src" "$STAGE/blobs/" 2>/dev/null || true
                fi
            done
            BUNDLED_OK+=("$spec")
        done

        if [[ ${#BUNDLED_OK[@]} -eq 0 ]]; then
            echo "[prefetch] WARN: whitelist 어느 것도 호스트에 없음 — skip"
            rm -rf "$STAGE"
        else
            echo "  포함: ${BUNDLED_OK[*]}"
            [[ ${#BUNDLED_MISS[@]} -gt 0 ]] && echo "  누락: ${BUNDLED_MISS[*]} (호스트에서 ollama pull 후 재시도)"

            (cd "$STAGE" && tar czf "$OLLAMA_OUT_FILE" manifests blobs)
            rm -rf "$STAGE"

            OLLAMA_SIZE=$(du -h "$OLLAMA_OUT_FILE" | cut -f1)
            OLLAMA_SHA=$(sha256sum "$OLLAMA_OUT_FILE" | cut -d' ' -f1)
            cat > "${OUT_DIR}/ollama-models-${ARCH}.meta" <<META
type: ollama-models-bundle
arch: $ARCH
models: ${BUNDLED_OK[*]}
source_dir: $OLLAMA_MODELS_DIR
tarball: $(basename "$OLLAMA_OUT_FILE")
size: $OLLAMA_SIZE
sha256: $OLLAMA_SHA
built_at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
META
            echo "  ollama-models: $OLLAMA_OUT_FILE ($OLLAMA_SIZE, sha256=$OLLAMA_SHA)"
        fi
    fi
fi

echo ""
echo "[prefetch] 오프라인 머신 복원:"
echo "    bash scripts/offline-load.sh --arch $ARCH"
echo "  (이미지 3개 + 있으면 ollama-models 자동 처리)"
