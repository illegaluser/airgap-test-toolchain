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

# ─── B 머신 부트킷 정리 ────────────────────────────────────────────────────
# 본 디렉터리(offline-assets/<arch>/) 가 그대로 USB 로 복사되어 폐쇄망에서
# 자족 실행될 수 있도록 compose 파일 / 자체-실행 load.sh, run.sh / .env / README /
# CHECKSUMS 를 함께 채운다.
echo ""
echo "[prefetch] B 머신 부트킷 정리..."

case "$ARCH" in
    amd64) COMPOSE_SRC="$ALLINONE_DIR/docker-compose.wsl2.yaml" ;;
    arm64) COMPOSE_SRC="$ALLINONE_DIR/docker-compose.mac.yaml"  ;;
esac
[ -f "$COMPOSE_SRC" ] || { echo "compose 원본 없음: $COMPOSE_SRC" >&2; exit 1; }
cp "$COMPOSE_SRC" "$OUT_DIR/docker-compose.yaml"

# gitlab-truncate-patch.sh — run.sh 에서 호출
mkdir -p "$OUT_DIR/scripts"
[ -f "$SCRIPT_DIR/gitlab-truncate-patch.sh" ] && \
    cp "$SCRIPT_DIR/gitlab-truncate-patch.sh" "$OUT_DIR/scripts/gitlab-truncate-patch.sh"

# .env — compose 의 ${IMAGE}, ${GITLAB_IMAGE} 등 정합화
cat > "$OUT_DIR/.env" <<EOF
# 본 .env 는 prefetch 시점에 자동 생성. compose 의 \${IMAGE} 와 tarball 안 image
# tag 를 일치시킨다.
IMAGE=$IMAGE
GITLAB_IMAGE=$GITLAB_IMAGE
EOF

# 자체-실행 load.sh — 본 디렉터리 안 *.tar.gz 를 docker load + ollama 추출
cat > "$OUT_DIR/load.sh" <<'BUNDLE_LOAD'
#!/usr/bin/env bash
# B 머신용 self-contained load 스크립트 — 본 폴더 통째로 복사된 상태에서 동작.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

OLLAMA_TARGET=""
SKIP_OLLAMA=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ollama-target) OLLAMA_TARGET="$2"; shift 2 ;;
        --no-ollama)     SKIP_OLLAMA=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

command -v docker >/dev/null || { echo "docker 명령 필요" >&2; exit 1; }

shopt -s nullglob
tarballs=("$HERE"/*.tar.gz)
shopt -u nullglob

ollama_tarball=""
images=()
for tb in "${tarballs[@]}"; do
    case "$(basename "$tb")" in
        ollama-models-*) ollama_tarball="$tb" ;;
        *)               images+=("$tb") ;;
    esac
done

[ ${#images[@]} -eq 0 ] && { echo "이미지 tarball 없음: $HERE" >&2; exit 1; }

echo "[load] dir=$HERE"
for tb in "${images[@]}"; do
    echo "[load] docker load: $(basename "$tb")"
    gunzip -c "$tb" | docker load
done

echo ""
echo "[load] 설치된 이미지:"
docker images --format '  {{.Repository}}:{{.Tag}}\t{{.Size}}' \
    | grep -E 'ttc-allinone|gitlab/gitlab-ce|dify-sandbox' || true

if [ "$SKIP_OLLAMA" -eq 0 ] && [ -n "$ollama_tarball" ]; then
    if [ -z "$OLLAMA_TARGET" ]; then
        if [ -f /proc/version ] && grep -qiE 'microsoft|wsl' /proc/version; then
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
    echo "[load] Ollama 모델 추출"
    echo "  source: $(basename "$ollama_tarball")"
    echo "  target: $OLLAMA_TARGET"
    mkdir -p "$OLLAMA_TARGET"
    tar -xzf "$ollama_tarball" -C "$OLLAMA_TARGET" --keep-newer-files 2>/dev/null \
        || tar -xzf "$ollama_tarball" -C "$OLLAMA_TARGET"

    # 추출 직후 Ollama daemon 응답 + 모델 등록 확인
    echo ""
    echo "[load] Ollama daemon 응답 확인..."
    OLLAMA_URL=""
    for u in http://localhost:11434/api/tags http://host.docker.internal:11434/api/tags; do
        if curl -sf --max-time 3 "$u" >/dev/null 2>&1; then
            OLLAMA_URL="$u"; break
        fi
    done
    if [ -z "$OLLAMA_URL" ]; then
        echo "  WARN: Ollama daemon 미응답."
        echo "  → 호스트에 Ollama 설치 + 시작 후 'ollama list' 로 검증:"
        echo "       windows : OllamaSetup.exe 실행 후 Ollama GUI 시작"
        echo "       macOS   : open -a Ollama (또는 brew install ollama && open -a Ollama)"
        echo "       linux   : sudo systemctl start ollama (또는 ollama serve &)"
    else
        echo "  daemon=$OLLAMA_URL"
        if command -v python3 >/dev/null 2>&1; then
            registered=$(curl -sf "$OLLAMA_URL" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    names = [m.get('name','?') for m in d.get('models', [])]
    print(' '.join(names) if names else '(없음)')
except Exception as e:
    print('(파싱 실패)')
")
            echo "  registered: $registered"
        else
            echo "  (python3 없음 — 'ollama list' 로 직접 확인)"
        fi
    fi
fi

echo ""
echo "[load] 완료. 다음:"
echo "    bash $HERE/run.sh"
BUNDLE_LOAD
chmod +x "$OUT_DIR/load.sh"

# 자체-실행 run.sh — 본 디렉터리 docker-compose.yaml 으로 compose up
cat > "$OUT_DIR/run.sh" <<'BUNDLE_RUN'
#!/usr/bin/env bash
# B 머신용 self-contained run 스크립트 — docker compose up -d + GitLab UI 패치.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
docker compose -f docker-compose.yaml "$@" up -d
if [ -x "$HERE/scripts/gitlab-truncate-patch.sh" ]; then
    "$HERE/scripts/gitlab-truncate-patch.sh" >&2 &
    disown || true
fi
BUNDLE_RUN
chmod +x "$OUT_DIR/run.sh"

# CHECKSUMS — 무결성 검증 (B 머신 sha256sum -c CHECKSUMS.sha256)
(cd "$OUT_DIR" && sha256sum *.tar.gz > CHECKSUMS.sha256)

# README
TARBALL_LIST=$(cd "$OUT_DIR" && ls -lh *.tar.gz | awk '{printf "- %s (%s)\n", $9, $5}')
cat > "$OUT_DIR/README-B-MACHINE.md" <<EOF
# TTC All-in-One — B 머신 반입 패키지 ($ARCH / tag=$TAG)

생성: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
원본: $ALLINONE_DIR

## 1) 무결성 검증 (선택)
\`\`\`bash
sha256sum -c CHECKSUMS.sha256
\`\`\`

## 2) 이미지 + Ollama 모델 로드
\`\`\`bash
bash load.sh                # 이미지 3개 docker load + ollama 모델 추출
# 옵션: bash load.sh --no-ollama --ollama-target /path/to/.ollama/models
\`\`\`

## 3) 컨테이너 기동
\`\`\`bash
bash run.sh                 # docker compose up -d + GitLab UI 패치
\`\`\`

## 4) 접속 URL (provision.sh 자동 wiring 후 — 보통 5-10분 소요)
- Jenkins    : http://localhost:28080  (admin / password)
- Dify       : http://localhost:28081  (admin@ttc.local / TtcAdmin!2026)
- SonarQube  : http://localhost:29000  (admin / TtcAdmin!2026)
- GitLab     : http://localhost:28090  (root / ChangeMe!Pass)
- Ollama     : http://host.docker.internal:11434  (B 머신 호스트 daemon)

## 포함 산출물
$TARBALL_LIST

## 호스트 요구사항
- Docker Desktop 24.0+ (Windows) 또는 Docker Engine 24+ (Linux)
- 메모리 24GB+ 권장
- 호스트에 Ollama 설치 + 본 패키지의 ollama-models 추출 후 daemon 재시작
EOF

echo "[prefetch] B 머신 부트킷 정리 완료:"
echo "  docker-compose.yaml / .env / load.sh / run.sh / README-B-MACHINE.md / CHECKSUMS.sha256 / scripts/gitlab-truncate-patch.sh"
echo ""
echo "[prefetch] B 머신 절차:"
echo "  1) USB 등에 ${OUT_DIR} 통째 복사"
echo "  2) (B 머신) bash load.sh && bash run.sh"
