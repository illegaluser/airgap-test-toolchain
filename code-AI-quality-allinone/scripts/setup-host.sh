#!/usr/bin/env bash
# ============================================================================
# setup-host.sh — A 머신 (외부망 빌드 머신) 호스트 사전 설치 자동화
#
# 무엇을 하는가:
#   1) OS 자동 감지 (macOS / WSL2 / Linux)
#   2) Docker 설치 여부 검증 (UI 설치 필요 — 이 스크립트는 *설치 안 함*)
#   3) Ollama 설치 여부 검증 (UI 권장 — macOS 는 brew 도 가능, Linux 는 자동 설치 옵션)
#   4) Python 3.11+ / pip / venv 설치
#   5) Git / curl / bash / unzip / build-essential (Linux/WSL2) 설치
#   6) huggingface_hub[cli] 설치 + PATH 추가
#   7) (선택) --with-assets : download-rag-bundle.sh 자동 호출
#
# 제외 (UI 설치 필요):
#   - Docker Desktop 본체 — https://www.docker.com/products/docker-desktop/
#   - Ollama 본체 (macOS) — https://ollama.com/download/mac
#       (단 Linux/WSL2 는 --install-ollama 플래그로 자동 설치 가능)
#
# 사용법:
#   bash scripts/setup-host.sh                  # 호스트 패키지만 설치
#   bash scripts/setup-host.sh --check          # 검증만 (변경 없음)
#   bash scripts/setup-host.sh --install-ollama # Linux/WSL2 한정 — Ollama 도 자동 설치
#   bash scripts/setup-host.sh --with-assets    # 호스트 패키지 + 자산 일괄 다운로드 (~1~3시간)
#   bash scripts/setup-host.sh --all            # 호스트 패키지 + Ollama 자동 + 자산 다운로드
#
# 멱등성: 재실행 안전. 이미 설치된 항목은 skip.
# ============================================================================

set -euo pipefail

# ─── 색상 + 헬퍼 ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { printf "${BLUE}[setup-host]${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}[setup-host] ✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[setup-host] ⚠${NC} %s\n" "$*" >&2; }
err()  { printf "${RED}[setup-host] ✗${NC} %s\n" "$*" >&2; }

# ─── 인자 파싱 ──────────────────────────────────────────────────────────────
CHECK_ONLY=false
INSTALL_OLLAMA=false
WITH_ASSETS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)            CHECK_ONLY=true ;;
        --install-ollama)   INSTALL_OLLAMA=true ;;
        --with-assets)      WITH_ASSETS=true ;;
        --all)              INSTALL_OLLAMA=true; WITH_ASSETS=true ;;
        --help|-h)
            sed -n '3,30p' "$0"
            exit 0
            ;;
        *) err "unknown flag: $1"; exit 2 ;;
    esac
    shift
done

# ─── OS 자동 감지 ───────────────────────────────────────────────────────────
detect_os() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "macos"
    elif [[ -f /proc/version ]] && grep -qiE 'microsoft|wsl' /proc/version; then
        echo "wsl2"
    elif [[ "$(uname -s)" == "Linux" ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

OS="$(detect_os)"
ARCH="$(uname -m)"

log "감지된 환경: OS=$OS, ARCH=$ARCH"
case "$ARCH" in
    arm64|aarch64) ;;
    x86_64|amd64)  ;;
    *) err "지원하지 않는 CPU 아키텍처: $ARCH"; exit 2 ;;
esac

if [[ "$OS" == "unknown" ]]; then
    err "지원하지 않는 OS — 본 스크립트는 macOS / WSL2 / Linux 전용"
    exit 2
fi

# ─── 1) Docker 검증 (설치는 사용자 책임) ─────────────────────────────────────
verify_docker() {
    log "Docker 검증..."
    if ! command -v docker >/dev/null 2>&1; then
        err "docker 명령을 찾을 수 없습니다."
        err ""
        err "Docker Desktop 을 먼저 설치하세요 (UI 설치 필요):"
        case "$OS" in
            macos) err "  → https://www.docker.com/products/docker-desktop/" ;;
            wsl2)  err "  → Windows host 에 Docker Desktop 설치 + WSL2 backend 활성화" ;;
            linux) err "  → https://docs.docker.com/engine/install/ (배포판별 가이드)" ;;
        esac
        err ""
        err "설치 후 본 스크립트 재실행: bash scripts/setup-host.sh"
        exit 1
    fi
    if ! docker version >/dev/null 2>&1; then
        err "docker daemon 에 연결할 수 없습니다."
        err "  → Docker Desktop GUI 가 실행 중인지 확인 (메뉴바/트레이의 🐳 아이콘)"
        err "  → Linux: 'sudo systemctl start docker' 또는 'sudo usermod -aG docker \$USER' 후 재로그인"
        exit 1
    fi
    local ver
    ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
    ok "Docker daemon ready (version: $ver)"
}

# ─── 2) Ollama 검증 (daemon 우선, CLI 보조) ─────────────────────────────────
# 시나리오 매트릭스:
#   A. CLI 있음 + daemon 응답         → 정상 (Linux/WSL2 native 설치 또는 macOS .dmg)
#   B. CLI 없음 + daemon 응답         → 호스트 GUI 설치 (예: Windows host 의 Ollama GUI,
#                                       WSL2 셸에서는 CLI PATH 미인식이지만 daemon 은 호스트
#                                       11434 에서 응답). 이 경우 정상 — 모델 pull 은 호스트
#                                       에서 진행하거나 본 셸에 별도 CLI 설치 안내.
#   C. CLI 있음 + daemon 미응답       → daemon 미기동 → 백그라운드 기동 시도
#   D. CLI 없음 + daemon 미응답       → 진짜 미설치 → OS 별 설치 안내 또는 --install-ollama

install_ollama_linux() {
    log "Ollama 자동 설치 (curl https://ollama.com/install.sh | sh)..."
    if curl -fsSL https://ollama.com/install.sh | sh; then
        ok "Ollama 설치 완료"
    else
        err "Ollama 자동 설치 실패 — 수동 설치 필요"
        err "  → curl -fsSL https://ollama.com/install.sh | sh"
        exit 1
    fi
}

verify_ollama() {
    log "Ollama 검증..."

    # 1) daemon 응답성 (가장 신뢰할 수 있는 검사)
    local daemon_reachable=false
    if curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
        daemon_reachable=true
    fi

    # 2) CLI 존재 여부 (보조)
    local cli_present=false
    if command -v ollama >/dev/null 2>&1; then
        cli_present=true
    fi

    # === Case A: CLI + daemon 모두 OK ===
    if [[ "$cli_present" == true ]] && [[ "$daemon_reachable" == true ]]; then
        ok "Ollama CLI + daemon ready ($(ollama --version 2>&1 | head -1))"
        return
    fi

    # === Case B: daemon 응답하지만 CLI 미존재 (Windows host GUI + WSL2 셸 등) ===
    if [[ "$cli_present" == false ]] && [[ "$daemon_reachable" == true ]]; then
        ok "Ollama daemon 응답 (호스트 측에 이미 설치됨 — 예: Windows GUI / macOS .dmg)"
        log ""
        log "  ℹ 이 셸에는 ollama CLI 가 없지만 daemon 이 호스트 11434 에서 응답하므로 *문제 없음*."
        log "    → 컨테이너는 host.docker.internal:11434 로 호스트 daemon 호출 (정상 흐름)"
        log ""
        log "  📌 Ollama 모델 다운로드 (gemma4:e4b 등) 는 다음 중 하나로 진행:"
        log "    (A) 호스트에서 직접 — Windows PowerShell 또는 macOS Terminal 에서 'ollama pull gemma4:e4b'"
        log "    (B) 본 셸에 ollama CLI 도 설치 후 호스트 daemon 공유:"
        log "        curl -fsSL https://ollama.com/install.sh | sh"
        log "        export OLLAMA_HOST=http://localhost:11434     # 본 셸 ollama CLI 가 호스트 daemon 호출"
        log ""
        return
    fi

    # === Case C: CLI 있지만 daemon 미응답 → 백그라운드 기동 ===
    if [[ "$cli_present" == true ]] && [[ "$daemon_reachable" == false ]]; then
        warn "Ollama CLI 발견했으나 daemon 미응답 — 백그라운드 기동 시도"
        ollama serve >/dev/null 2>&1 &
        sleep 3
        if curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
            ok "Ollama daemon 기동 완료"
        else
            err "ollama serve 실행했으나 daemon 응답 없음 — 수동 확인 필요:"
            err "  ollama serve &"
            err "  (또는 'ps aux | grep ollama' 로 기존 프로세스 확인)"
            exit 1
        fi
        return
    fi

    # === Case D: CLI + daemon 모두 없음 → 진짜 미설치 ===
    case "$OS" in
        wsl2|linux)
            if [[ "$INSTALL_OLLAMA" == true ]]; then
                install_ollama_linux
            else
                err "Ollama 가 설치되어 있지 않습니다 (CLI 와 daemon 모두 응답 X)."
                err ""
                err "설치 옵션 (둘 중 하나):"
                err "  (A) 호스트 (Windows) 에 GUI 설치"
                err "      → https://ollama.com/download/windows → OllamaSetup.exe"
                err "      → 설치 후 본 스크립트 재실행 (WSL2 에서 호스트 daemon 자동 감지)"
                err "  (B) 본 셸에 자동 설치"
                err "      → bash scripts/setup-host.sh --install-ollama"
                exit 1
            fi
            ;;
        macos)
            err "Ollama 가 설치되어 있지 않습니다 (CLI 와 daemon 모두 응답 X)."
            err ""
            err "설치 옵션 (둘 중 하나):"
            err "  (A) UI 설치  : https://ollama.com/download/mac → .dmg → Applications 드래그 → 실행"
            err "  (B) 명령행   : brew install ollama && open -a Ollama"
            err ""
            err "설치 후 본 스크립트 재실행"
            exit 1
            ;;
    esac
}

# ─── 3) macOS 패키지 (Homebrew + python@3.12 + git) ──────────────────────────
install_homebrew_if_missing() {
    if command -v brew >/dev/null 2>&1; then
        ok "Homebrew $(brew --version | head -1)"
        return
    fi
    log "Homebrew 설치 (관리자 비밀번호 요구될 수 있음)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # PATH 추가 (Apple Silicon)
    if [ -d /opt/homebrew/bin ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -d /usr/local/Homebrew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "Homebrew 설치 완료"
}

install_packages_macos() {
    install_homebrew_if_missing
    log "brew install python@3.12 git curl ..."
    brew install python@3.12 git curl 2>&1 | grep -E '(==>|Already|Pouring)' || true
    ok "brew 패키지 설치 확인"

    # python3 명령 보장
    if ! command -v python3 >/dev/null 2>&1; then
        err "python3 명령을 PATH 에서 찾을 수 없습니다 — 셸 재시작 후 재시도"
        exit 1
    fi
    PYTHON3="$(command -v python3)"
}

# ─── 4) WSL2 / Linux 패키지 (apt) ───────────────────────────────────────────
install_packages_wsl2_linux() {
    log "sudo apt update + install (관리자 비밀번호 요구될 수 있음)..."
    if ! command -v sudo >/dev/null 2>&1; then
        err "sudo 명령을 찾을 수 없습니다 — 본 스크립트는 sudo 가능한 일반 사용자로 실행"
        exit 1
    fi

    sudo apt update -qq
    sudo apt install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        git curl bash unzip ca-certificates \
        build-essential
    ok "apt 패키지 설치 확인"

    PYTHON3="$(command -v python3)"
}

# ─── 5) huggingface-cli 설치 + PATH ──────────────────────────────────────────
install_hf_cli() {
    log "huggingface_hub[cli] 설치 (--user)..."

    # pip 자체 업그레이드 (선택, 실패 무시)
    "$PYTHON3" -m pip install --user --upgrade pip 2>&1 | tail -3 || true

    # huggingface_hub[cli] 설치
    "$PYTHON3" -m pip install --user "huggingface_hub[cli]" 2>&1 | tail -5

    # PATH 추가
    local user_bin="$HOME/.local/bin"
    local rcfile
    case "$OS" in
        macos) rcfile="$HOME/.zshrc" ;;
        *)     rcfile="$HOME/.bashrc" ;;
    esac

    if [[ ! -f "$rcfile" ]]; then
        touch "$rcfile"
    fi
    if ! grep -q '\.local/bin' "$rcfile" 2>/dev/null; then
        echo '' >> "$rcfile"
        echo '# Added by setup-host.sh — huggingface-cli PATH' >> "$rcfile"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rcfile"
        ok "PATH 추가: $rcfile"
    fi
    export PATH="$user_bin:$PATH"

    if command -v huggingface-cli >/dev/null 2>&1; then
        ok "huggingface-cli $(huggingface-cli --version 2>&1 | head -1)"
    else
        warn "huggingface-cli 명령을 PATH 에서 찾을 수 없습니다 — 새 셸을 열거나 'source $rcfile' 실행 후 재확인"
    fi
}

# ─── 6) 검증 요약 ────────────────────────────────────────────────────────────
verify_all() {
    log "─────────── 최종 검증 ───────────"
    docker --version || warn "docker 누락"
    ollama --version || warn "ollama 누락"
    "$PYTHON3" --version || warn "python3 누락"
    "$PYTHON3" -m pip --version || warn "pip 누락"
    if command -v huggingface-cli >/dev/null 2>&1; then
        huggingface-cli --version | head -1
    else
        warn "huggingface-cli 누락 — 새 셸 진입 후 재확인 필요"
    fi
    git --version || warn "git 누락"
    curl --version | head -1 || warn "curl 누락"
    bash --version | head -1 || warn "bash 누락"
    log "─────────────────────────────────"
    ok "호스트 사전 설치 완료"
}

# ─── 7) 자산 다운로드 (선택) ─────────────────────────────────────────────────
download_assets() {
    log "─────────── 자산 일괄 다운로드 ───────────"
    log "예상 시간: 30분 ~ 3시간 / 디스크 ~12 GB"
    log ""

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local bundle="$script_dir/download-rag-bundle.sh"

    if [[ ! -x "$bundle" ]]; then
        if [[ -f "$bundle" ]]; then
            chmod +x "$bundle"
        else
            err "$bundle 가 존재하지 않습니다 — 레포 손상 의심"
            exit 1
        fi
    fi

    bash "$bundle"
}

# ─── 메인 흐름 ──────────────────────────────────────────────────────────────
log "TTC All-in-One 호스트 사전 설치 시작"
log "  CHECK_ONLY     = $CHECK_ONLY"
log "  INSTALL_OLLAMA = $INSTALL_OLLAMA"
log "  WITH_ASSETS    = $WITH_ASSETS"

if [[ "$CHECK_ONLY" == true ]]; then
    log "─────────── 검증 모드 (변경 없음) ───────────"
    verify_docker
    verify_ollama
    if command -v python3 >/dev/null 2>&1; then
        PYTHON3="$(command -v python3)"
    else
        PYTHON3=""
    fi
    verify_all
    exit 0
fi

# OS 별 패키지 설치
case "$OS" in
    macos)        install_packages_macos ;;
    wsl2|linux)   install_packages_wsl2_linux ;;
esac

# Docker / Ollama 검증 (설치는 사용자 책임 또는 --install-ollama)
verify_docker
verify_ollama

# huggingface-cli
install_hf_cli

# 최종 검증
verify_all

# 자산 다운로드 (선택)
if [[ "$WITH_ASSETS" == true ]]; then
    download_assets
fi

log ""
log "다음 단계:"
if [[ "$WITH_ASSETS" != true ]]; then
    log "  자산 다운로드: bash scripts/download-rag-bundle.sh"
    log "                 (또는 본 스크립트를 --with-assets 로 재실행)"
fi
log "  이미지 빌드 :  bash scripts/build-wsl2.sh   (또는 build-mac.sh)"
log "  반입 패키지 :  bash scripts/offline-prefetch.sh --arch \$(uname -m)"
log ""
log "상세: README §3 (플랫폼별 빌드)"
