#!/usr/bin/env bash
# ============================================================================
# download-rag-bundle.sh — A 머신 (외부망 빌드 머신) 자산 일괄 다운로드
#
# 무엇을 하는가:
#   1) Jenkins / Dify 플러그인 번들 (download-plugins.sh 호출)
#   2) Meilisearch v1.42 binary (호스트 native arch 한 개)
#   3) bge-reranker-v2-m3 weight (BAAI, ~1.1 GB, huggingface-cli)
#   4) FalkorDB Docker 이미지 (multi-stage 차용용, docker pull)
#   5) Ollama 모델 — gemma4:e4b (~9.6 GB) + qwen3-embedding:0.6b (~0.6 GB) + bge-m3 (~1 GB)
#
# 멱등성: 재실행 안전. 이미 다운로드된 항목은 skip.
# 예상 시간: 30분 ~ 3시간 (회선 속도 의존)
# 예상 디스크: ~12 GB (offline-assets/ + Ollama models)
#
# 선결: scripts/setup-host.sh 가 먼저 실행되어 docker / ollama / huggingface-cli /
#       curl / git / bash 가 모두 PATH 에 있어야 한다.
#
# 사용법:
#   bash scripts/download-rag-bundle.sh
#   bash scripts/download-rag-bundle.sh --skip-models      # Ollama 모델 다운로드 생략
#   bash scripts/download-rag-bundle.sh --skip-plugins     # download-plugins.sh 생략
# ============================================================================

set -euo pipefail

# setup-host.sh 가 ~/.local/bin/huggingface-cli 에 symlink 를 깔고 .bashrc 에 PATH 추가.
# .bashrc 는 non-interactive 셸에서 early-return 하므로, 이 스크립트가 비대화식으로
# 실행될 경우(예: 다른 스크립트에서 호출, 새 세션 직후) PATH 에 포함되지 않을 수 있어
# 명시적으로 prepend.
[[ -d "$HOME/.local/bin" ]] && export PATH="$HOME/.local/bin:$PATH"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { printf "${BLUE}[bundle]${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}[bundle] ✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[bundle] ⚠${NC} %s\n" "$*" >&2; }
err()  { printf "${RED}[bundle] ✗${NC} %s\n" "$*" >&2; }

SKIP_MODELS=false
SKIP_PLUGINS=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-models)  SKIP_MODELS=true ;;
        --skip-plugins) SKIP_PLUGINS=true ;;
        --help|-h) sed -n '3,25p' "$0"; exit 0 ;;
        *) err "unknown flag: $1"; exit 2 ;;
    esac
    shift
done

# ─── 작업 디렉터리 (레포 루트 — code-AI-quality-allinone/) ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ALLINONE_DIR"

# ─── 호스트 native arch 감지 ───────────────────────────────────────────────
case "$(uname -m)" in
    arm64|aarch64) ARCH=arm64; MEILI_TAG=aarch64 ;;
    x86_64|amd64)  ARCH=amd64; MEILI_TAG=amd64   ;;
    *) err "지원하지 않는 arch: $(uname -m)"; exit 2 ;;
esac
log "ARCH=$ARCH (Meili tag: $MEILI_TAG)"

# ─── 선결 도구 확인 ──────────────────────────────────────────────────────────
require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "$1 명령이 PATH 에 없습니다 — bash scripts/setup-host.sh 먼저 실행"
        exit 1
    fi
}
require docker
require curl
require huggingface-cli

# Docker daemon 응답
if ! docker version >/dev/null 2>&1; then
    err "docker daemon 미응답 — Docker Desktop 을 켜고 재시도"
    exit 1
fi

# ─── Ollama 검증 — daemon 우선, CLI 보조 ───────────────────────────────────
# 시나리오:
#   A. CLI + daemon → 정상 (모델 pull 가능)
#   B. CLI 없음 + daemon 응답 → 호스트 GUI 설치 (Windows + WSL2 흔한 케이스).
#                                Ollama 모델 다운로드 단계는 자동 skip — 호스트에서 직접 pull.
#   C. CLI 있음 + daemon 미응답 → 백그라운드 기동
#   D. 둘 다 없음 → 에러
OLLAMA_CLI_AVAILABLE=false
OLLAMA_DAEMON_REACHABLE=false

if command -v ollama >/dev/null 2>&1; then
    OLLAMA_CLI_AVAILABLE=true
fi
if curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_DAEMON_REACHABLE=true
fi

# Case C: CLI 있음 + daemon 미응답 → 기동 시도
if [[ "$OLLAMA_CLI_AVAILABLE" == true ]] && [[ "$OLLAMA_DAEMON_REACHABLE" == false ]]; then
    log "Ollama daemon 백그라운드 기동..."
    ollama serve >/dev/null 2>&1 &
    sleep 3
    if curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
        OLLAMA_DAEMON_REACHABLE=true
    fi
fi

# Case B: CLI 없지만 daemon 응답 → 모델 다운로드 단계 자동 skip
if [[ "$OLLAMA_CLI_AVAILABLE" == false ]] && [[ "$OLLAMA_DAEMON_REACHABLE" == true ]]; then
    if [[ "$SKIP_MODELS" != true ]]; then
        warn "ollama CLI 가 본 셸에 없지만 daemon 은 응답합니다 (호스트 GUI 설치로 추정 — 예: Windows + WSL2)."
        warn "Ollama 모델 다운로드 단계를 *자동으로 skip* 합니다."
        warn ""
        warn "📌 Ollama 모델은 호스트에서 직접 pull 하세요:"
        warn "  Windows PowerShell / macOS Terminal:"
        warn "    ollama pull gemma4:e4b"
        warn "    ollama pull qwen3-embedding:0.6b"
        warn "    ollama pull bge-m3"
        warn ""
        warn "📦 Windows GUI 설치 시 모델 위치: %USERPROFILE%\\.ollama\\models\\"
        warn "    WSL2 에서 접근:  /mnt/c/Users/<Windows유저명>/.ollama/models/"
        warn "    macOS 위치     :  ~/.ollama/models/"
        warn ""
        warn "이후 §3.4 (offline-prefetch.sh) 가 모델 export 시 위 경로를 참조하면 됩니다."
        warn ""
        SKIP_MODELS=true
    fi
fi

# Case D: CLI + daemon 모두 미응답
if [[ "$OLLAMA_CLI_AVAILABLE" == false ]] && [[ "$OLLAMA_DAEMON_REACHABLE" == false ]]; then
    err "Ollama 가 설치되어 있지 않습니다 (CLI 와 daemon 모두 응답 X)."
    err "  → bash scripts/setup-host.sh 먼저 실행 (또는 Ollama 호스트 설치 후 재시도)"
    exit 1
fi

# ─── 1) Jenkins / Dify 플러그인 번들 ────────────────────────────────────────
download_plugins() {
    log "[1/5] Jenkins / Dify 플러그인 번들..."
    if [[ -d "$ALLINONE_DIR/jenkins-plugins" ]] && \
       [[ -n "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]] && \
       [[ -d "$ALLINONE_DIR/dify-plugins" ]] && \
       [[ -n "$(ls -A "$ALLINONE_DIR/dify-plugins" 2>/dev/null)" ]]; then
        ok "이미 존재 — 재다운로드 건너뜀 (강제 재실행: rm -rf jenkins-plugins/ dify-plugins/)"
        return
    fi
    bash "$SCRIPT_DIR/download-plugins.sh"
    local jenkins_count
    jenkins_count="$(ls "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null | wc -l | tr -d ' ')"
    local dify_count
    dify_count="$(ls "$ALLINONE_DIR/dify-plugins" 2>/dev/null | wc -l | tr -d ' ')"
    ok "Jenkins 플러그인 ${jenkins_count} 개 / Dify 플러그인 ${dify_count} 개 다운로드 완료"
}

# ─── 2) Meilisearch v1.42 binary ────────────────────────────────────────────
download_meilisearch() {
    log "[2/5] Meilisearch v1.42 binary (linux-${MEILI_TAG})..."
    mkdir -p offline-assets/meilisearch
    local target="offline-assets/meilisearch/meilisearch-linux-${MEILI_TAG}"
    if [[ -s "$target" ]]; then
        ok "이미 존재: $(du -h "$target" | cut -f1) — 건너뜀"
        return
    fi
    curl -fL -o "$target" \
        "https://github.com/meilisearch/meilisearch/releases/download/v1.42.1/meilisearch-linux-${MEILI_TAG}"
    chmod +x "$target"
    ok "Meilisearch binary: $(du -h "$target" | cut -f1)"
}

# ─── 3) bge-reranker-v2-m3 weight (BAAI, Apache 2.0) ────────────────────────
download_bge_reranker() {
    log "[3/5] bge-reranker-v2-m3 weight (~1.1 GB)..."
    mkdir -p offline-assets/rerank-models
    local dir="offline-assets/rerank-models/bge-reranker-v2-m3"
    if [[ -s "$dir/model.safetensors" ]] && [[ -s "$dir/config.json" ]]; then
        ok "이미 존재: $(du -sh "$dir" | cut -f1) — 건너뜀"
        return
    fi
    if huggingface-cli download BAAI/bge-reranker-v2-m3 \
        --local-dir "$dir" \
        --local-dir-use-symlinks=False; then
        ok "bge-reranker-v2-m3: $(du -sh "$dir" | cut -f1)"
    else
        err "bge-reranker 다운로드 실패."
        err "  → HF rate limit 의심 — 'huggingface-cli login' 후 재시도"
        err "  → 토큰 발급: https://huggingface.co/settings/tokens (Read 권한)"
        exit 1
    fi
}

# ─── 4) FalkorDB Docker 이미지 (multi-stage 차용) ────────────────────────────
pull_falkordb() {
    log "[4/5] FalkorDB Docker 이미지 (multi-stage source)..."
    if docker image inspect falkordb/falkordb:latest >/dev/null 2>&1; then
        ok "이미 존재 — 건너뜀"
        return
    fi
    docker pull falkordb/falkordb:latest
    local img_arch
    img_arch="$(docker inspect falkordb/falkordb:latest --format '{{.Architecture}}')"
    if [[ "$img_arch" != "$ARCH" ]]; then
        warn "FalkorDB 이미지 arch ($img_arch) 가 호스트 arch ($ARCH) 와 다릅니다 — 빌드 시 문제 가능"
    fi
    ok "FalkorDB Docker 이미지 (arch=$img_arch)"
}

# ─── 5) Ollama 모델 ─────────────────────────────────────────────────────────
pull_ollama_models() {
    log "[5/5] Ollama 모델 (gemma4:e4b + qwen3-embedding:0.6b + bge-m3)..."

    pull_one() {
        local model="$1"
        local size_hint="$2"
        if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qE "^${model}$"; then
            ok "$model — 이미 존재, 건너뜀"
        else
            log "$model 다운로드 (${size_hint}, 회선 속도 따라 5분~3시간)..."
            ollama pull "$model"
            ok "$model 다운로드 완료"
        fi
    }

    pull_one "gemma4:e4b"             "9.6 GB on-disk"
    pull_one "qwen3-embedding:0.6b"   "0.6 GB"
    pull_one "bge-m3"                 "1 GB"

    log ""
    log "Ollama 모델 디렉터리 위치:"
    log "  macOS / Linux : ~/.ollama/models/"
    log "  본 스크립트는 *호스트 ~/.ollama 에만* 적재했습니다."
    log "  반출 시 'cp -r ~/.ollama/models offline-assets/ollama-models/' 또는 §3.4 참조."
}

# ─── 메인 흐름 ──────────────────────────────────────────────────────────────
log "TTC All-in-One 자산 일괄 다운로드 시작"
log "  ARCH         = $ARCH"
log "  SKIP_MODELS  = $SKIP_MODELS"
log "  SKIP_PLUGINS = $SKIP_PLUGINS"
log ""

if [[ "$SKIP_PLUGINS" != true ]]; then
    download_plugins
else
    log "[1/5] 플러그인 다운로드 SKIP (--skip-plugins)"
fi

download_meilisearch
download_bge_reranker
pull_falkordb

if [[ "$SKIP_MODELS" != true ]]; then
    pull_ollama_models
else
    log "[5/5] Ollama 모델 다운로드 SKIP (--skip-models)"
fi

# ─── 요약 ──────────────────────────────────────────────────────────────────
log ""
log "─────────── 다운로드 결과 ───────────"
if [[ -d "$ALLINONE_DIR/jenkins-plugins" ]]; then
    log "jenkins-plugins/        : $(ls "$ALLINONE_DIR/jenkins-plugins" | wc -l | tr -d ' ') 개"
fi
if [[ -d "$ALLINONE_DIR/dify-plugins" ]]; then
    log "dify-plugins/           : $(ls "$ALLINONE_DIR/dify-plugins" | wc -l | tr -d ' ') 개"
fi
if [[ -d offline-assets/meilisearch ]]; then
    log "offline-assets/meilisearch/  : $(du -sh offline-assets/meilisearch 2>/dev/null | cut -f1)"
fi
if [[ -d offline-assets/rerank-models ]]; then
    log "offline-assets/rerank-models/: $(du -sh offline-assets/rerank-models 2>/dev/null | cut -f1)"
fi
if docker image inspect falkordb/falkordb:latest >/dev/null 2>&1; then
    log "falkordb/falkordb:latest     : Docker daemon 안 (multi-stage 차용 준비됨)"
fi
if [[ "$SKIP_MODELS" != true ]]; then
    log "Ollama 모델 (~/.ollama/models/):"
    ollama list 2>/dev/null | tail -n +2 | awk '{printf "  %s\n", $0}'
fi
log "─────────────────────────────────"

ok "자산 일괄 다운로드 완료"
log ""
log "다음 단계:"
log "  이미지 빌드 :  bash scripts/build-wsl2.sh   (또는 build-mac.sh)"
log "  반입 패키지 :  bash scripts/offline-prefetch.sh --arch $ARCH"
log ""
log "Ollama 모델 반출 (B 머신 으로 옮기기 위해):"
log "  mkdir -p offline-assets/ollama-models"
log "  cp -r ~/.ollama/models/{blobs,manifests} offline-assets/ollama-models/"
