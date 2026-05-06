#!/usr/bin/env bash
# ============================================================================
# wsl-agent-setup.sh — WSL2 Ubuntu 에서 Jenkins agent + Playwright 환경 구성
#
# mac-agent-setup.sh 의 WSL2 버전. Windows 11 의 하이브리드 토폴로지는:
#
#   Windows 네이티브: Ollama (GPU CUDA 직접) — 본 스크립트와 별개로 사용자가 설치
#   WSL2 Ubuntu     : 본 스크립트가 구성하는 Jenkins agent + Playwright
#   컨테이너         : Jenkins master + Dify → host.docker.internal:11434 로
#                     Docker Desktop 을 거쳐 Windows Ollama 에 도달
#   Chromium 창      : WSLg 를 경유해 Windows 데스크탑에 headed 표시
#
# 즉 agent 는 Ollama 를 직접 호출하지 않는다 (추론은 컨테이너 몫). 본 스크립트
# 1단계의 Ollama 체크는 **사용자 편의용 사전 확인**이며 실패해도 진행한다.
#
# 일반 Linux 서버에서도 동일하게 동작한다 — 이 경우 Ollama 가 같은 호스트에
# 설치됐다면 127.0.0.1:11434 로 잡히고, headed 창은 X server (:0) 필요.
#
# 역할 (7 단계, idempotent):
#   1) 호스트 Ollama 도달성 확인 (정보성, 실패 시 warn 만)
#   2) JDK 21 확인/설치
#   3) Python 3.11+ 확인/설치
#   4) venv 생성 + Playwright Chromium 호스트 설치 (WSLg/X 로 창 표시)
#   5) Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크
#   6) Jenkins controller 에서 agent.jar 다운로드
#   7) 기동 스크립트 생성 + 포그라운드 agent 연결
#
# 기본 실행 (이미 JDK 21 / python 3.11+ 설치된 환경):
#     NODE_SECRET=<64자> ./offline/wsl-agent-setup.sh
#
# 의존성 자동 설치 (sudo 필요; apt install openjdk-21 / python3.12):
#     NODE_SECRET=<64자> AUTO_INSTALL_DEPS=true ./offline/wsl-agent-setup.sh
#
# 환경변수:
#   OLLAMA_PING_URL  - 호스트 Ollama 확인 URL 강제 지정 (기본: 자동 탐색)
#   OLLAMA_MODEL     - 존재 확인 대상 모델 (정보성, WSL2 기본 qwen3.5:9b)
#
# 재실행은 idempotent — 이미 설치된 것은 스킵.
# ============================================================================
set -euo pipefail

AGENT_DIR="${WSL_AGENT_WORKDIR:-$HOME/.dscore.ttc.playwright-agent}"
JENKINS_URL="${JENKINS_URL:-http://localhost:18080}"
AGENT_NAME="${AGENT_NAME:-wsl-ui-tester}"
PY_VERSION_MIN="${PY_VERSION_MIN:-3.11}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"
AUTO_INSTALL_DEPS="${AUTO_INSTALL_DEPS:-false}"
# NODE_SECRET 자동 추출 시 읽을 컨테이너 (docker logs) — 이름 override 가능
CONTAINER_NAME="${CONTAINER_NAME:-dscore.ttc.playwright}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"                        # playwright-allinone/ (zero_touch_qa 의 부모)

log()  { printf '[wsl-agent-setup] %s\n' "$*"; }
err()  { printf '[wsl-agent-setup] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[wsl-agent-setup] WARN:  %s\n' "$*" >&2; }

# ── 0. 사전 검증 + 호스트 종류 판별 ─────────────────────────────────────────
# 본 스크립트는 다음 세 환경을 cover:
#   - Linux 베어 호스트  : python3 / java / Linux Chromium / X11 디스플레이
#   - WSL2 (정보성)      : 사용 중단 — 사용자 원칙에 따라 Windows 호스트 직접 권장 (Git Bash)
#   - Git Bash on Windows: python.exe / java.exe / Windows Chromium 네이티브 (host-native 원칙)
# Mac 은 mac-agent-setup.sh 별도.
UNAME_S="$(uname -s)"
case "$UNAME_S" in
  MINGW*|MSYS*|CYGWIN*)
    IS_WIN_HOST=true; IS_WSL=false
    log "Git Bash on Windows 감지 — Windows 호스트 네이티브 (python.exe / java.exe / Windows Chromium) 사용"
    ;;
  Linux)
    IS_WIN_HOST=false
    if grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
      IS_WSL=true
      err "WSL2 안에서 본 스크립트 실행 금지. 사용자 원칙: Windows 호스트 네이티브 — Git Bash 에서 ./wsl-agent-setup.sh 를 실행하세요."
    else
      IS_WSL=false
      log "일반 Linux — headed 창은 X server (DISPLAY=${DISPLAY:-unset}) 가 있어야 표시됩니다"
    fi
    ;;
  *)
    err "지원 OS 아님: $UNAME_S (Mac 은 mac-agent-setup.sh, Windows 는 Git Bash 에서 실행)"
    ;;
esac

# OS 별 바이너리 / 캐시 경로 정규화 — 이후 단계는 이 변수만 참조한다.
if [ "$IS_WIN_HOST" = "true" ]; then
  VENV_BIN_REL="Scripts"               # venv 안 실행파일 디렉토리 (Win)
  VENV_PY_NAME="python.exe"
  PW_CACHE="${LOCALAPPDATA:-$HOME/AppData/Local}/ms-playwright"
  # Java/Python 절대 경로를 JVM/Windows 가 이해하는 'C:\...' 형식으로 바꿔주는 헬퍼.
  to_win_path() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
  # mklink junction — 인자는 따로 전달해야 cmd 가 공백 포함 path 를 올바르게 파싱.
  # rc=0 정상, rc!=0 실패. set -e 트리거 안 되도록 호출자가 ||/&& 로 감싸 사용.
  make_junction() {
    local link_p="$1" target="$2"
    MSYS_NO_PATHCONV=1 cmd /c mklink /J "$(to_win_path "$link_p")" "$(to_win_path "$target")" >/dev/null 2>&1
  }
  # 기존 junction/디렉토리 안전 제거.
  remove_path() {
    local p="$1"
    if [ -L "$p" ]; then
      rm -f "$p" 2>/dev/null || true
    elif [ -d "$p" ]; then
      MSYS_NO_PATHCONV=1 cmd /c rmdir "$(to_win_path "$p")" 2>/dev/null \
        || rm -rf "$p" 2>/dev/null || true
    fi
  }
else
  VENV_BIN_REL="bin"
  VENV_PY_NAME="python3"
  PW_CACHE="$HOME/.cache/ms-playwright"
  to_win_path() { echo "$1"; }
  make_junction() { ln -sfn "$2" "$1"; }
  remove_path() { rm -rf "$1"; }
fi

# ── 0-A. 기존 세션 정리 — "빌드/재배포 시마다 깨끗하게" 보장 ──────────────────
# 이전 run 에서 남은 agent.jar 프로세스가 살아있으면 새 NODE_SECRET 으로 연결할 때
# Jenkins master 가 "already connected" 로 거부한다. 또 여러 번 실행하면 중복 인스턴스가
# 쌓여 리소스 낭비. 시작 시점에 먼저 전부 정리한다.
#
# 자기 자신 필터링: bash `$(...)` 는 subshell 을 띄우는데 그 subshell 은 부모
# 스크립트의 cmdline 을 그대로 상속 → pgrep -f "wsl-agent-setup.sh" 에 같이 잡힌다.
# MY_PID/PPID 만으론 이 subshell 을 걸러낼 수 없으므로 **session id (SID) 기준**으로
# 같은 세션의 프로세스는 전부 제외한다. setsid 로 분리 기동된 이전 인스턴스는
# 다른 SID 를 가지므로 정확히 타겟팅됨.
MY_PID=$$
MY_SID=$({ ps -o sid= -p "$MY_PID" 2>/dev/null || true; } | tr -d ' ')
[ -z "$MY_SID" ] && MY_SID="$MY_PID"
log "[0-A] 기존 agent / setup 프로세스 정리 (my_sid=$MY_SID)"

# agent.jar 프로세스 (현재 $AGENT_NAME 기준 — 다른 세션 포함 모두)
# Linux: pgrep -f / Windows(Git Bash): wmic + taskkill (pgrep 은 MSYS 프로세스만 보임)
if [ "$IS_WIN_HOST" = "true" ]; then
  # wmic 출력은 \r 포함 — strip 하고 PID 컬럼만 추출
  EXISTING_AGENT_PIDS=$(wmic process where "name='java.exe' and CommandLine like '%%agent.jar%%-name $AGENT_NAME%%'" get processid 2>/dev/null \
    | tr -d '\r' | awk 'NR>1 && /^[0-9]+/ {print $1}' | head -10 || true)
else
  EXISTING_AGENT_PIDS=$(pgrep -f "agent.jar.*-name $AGENT_NAME" 2>/dev/null || true)
fi
if [ -n "$EXISTING_AGENT_PIDS" ]; then
  log "  기존 agent.jar 감지 (pid: $(echo "$EXISTING_AGENT_PIDS" | tr '\n' ' ')) — 종료"
  if [ "$IS_WIN_HOST" = "true" ]; then
    for _p in $EXISTING_AGENT_PIDS; do taskkill //F //T //PID "$_p" 2>/dev/null || true; done
  else
    kill $EXISTING_AGENT_PIDS 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      pgrep -f "agent.jar.*-name $AGENT_NAME" >/dev/null 2>&1 || break
      sleep 1
    done
    STILL=$(pgrep -f "agent.jar.*-name $AGENT_NAME" 2>/dev/null || true)
    [ -n "$STILL" ] && kill -9 $STILL 2>/dev/null || true
  fi
fi

# 다른 wsl-agent-setup.sh 인스턴스 (자기 세션 제외). ps/pgrep 실패를 set -e 가
# 스크립트 전체 종료로 연결하지 않도록 각 단계마다 `|| true` 로 묶는다.
OTHER_SETUP_PIDS=""
_setup_pids=$(pgrep -f "wsl-agent-setup.sh" 2>/dev/null || true)
for _pid in $_setup_pids; do
  _sid=$({ ps -o sid= -p "$_pid" 2>/dev/null || true; } | tr -d ' ')
  if [ -n "$_sid" ] && [ "$_sid" != "$MY_SID" ]; then
    OTHER_SETUP_PIDS="$OTHER_SETUP_PIDS $_pid"
  fi
done
# 양쪽 공백 trim (xargs 없이 bash 내장으로)
OTHER_SETUP_PIDS="${OTHER_SETUP_PIDS# }"
OTHER_SETUP_PIDS="${OTHER_SETUP_PIDS% }"
if [ -n "$OTHER_SETUP_PIDS" ]; then
  log "  이전 wsl-agent-setup.sh 인스턴스 감지 (pid: $OTHER_SETUP_PIDS) — 종료"
  kill $OTHER_SETUP_PIDS 2>/dev/null || true
  sleep 1
  # 여전히 살아있는 것만 SIGKILL (PID 지정 — pkill -f 로 자기 자신까지 죽이는 사고 방지)
  for _pid in $OTHER_SETUP_PIDS; do
    kill -0 "$_pid" 2>/dev/null && kill -9 "$_pid" 2>/dev/null || true
  done
fi

# Jenkins 가 기존 연결을 disconnect 로 인지할 때까지 대기 — **실제로 kill 한 게
# 있을 때만**. 처음부터 깨끗한 상태 (kill 대상 0개) 면 즉시 통과.
if [ -n "$EXISTING_AGENT_PIDS" ] || [ -n "$OTHER_SETUP_PIDS" ]; then
  log "  Jenkins 가 disconnect 를 인지할 때까지 대기 (최대 15s)"
  for _ in 1 2 3 4 5; do
    if curl -fsS --max-time 2 -u admin:password "$JENKINS_URL/computer/$AGENT_NAME/api/json" 2>/dev/null \
        | grep -q '"offline":true'; then
      break
    fi
    sleep 1
  done
fi
log "  정리 완료"

# ── 0-B. NODE_SECRET 자동 추출 (env 미지정 시 docker logs 에서) ──────────────
if [ -z "${NODE_SECRET:-}" ]; then
  log "[0-B] NODE_SECRET 미지정 — docker logs '$CONTAINER_NAME' 에서 자동 추출 시도"
  if command -v docker >/dev/null 2>&1 && \
     docker ps --filter "name=^${CONTAINER_NAME}$" --format '{{.Names}}' | grep -q .; then
    # 두 Node 가 공존하므로 AGENT_NAME 태그가 붙은 라인만 추출
    NODE_SECRET=$(docker logs "$CONTAINER_NAME" 2>&1 \
      | grep -oE "NODE_SECRET: [a-f0-9]{64}   \($AGENT_NAME\)" \
      | tail -n1 \
      | awk '{print $2}' || true)
    # 구 이미지 호환: 태그 없는 `NODE_SECRET: <hex>` 형태 fallback
    if [ -z "$NODE_SECRET" ]; then
      NODE_SECRET=$(docker logs "$CONTAINER_NAME" 2>&1 \
        | grep -oE 'NODE_SECRET: [a-f0-9]{64}' \
        | tail -n1 \
        | awk '{print $2}' || true)
    fi
  fi
  if [ -z "${NODE_SECRET:-}" ]; then
    cat >&2 <<EOT
[wsl-agent-setup] ERROR: NODE_SECRET 을 찾을 수 없습니다.

  자동 추출 실패 원인 (아래 중 하나):
    - 컨테이너 '$CONTAINER_NAME' 이 기동되어 있지 않음
    - 프로비저닝이 아직 완료되지 않음 (NODE_SECRET 로그 라인이 아직 안 찍힘)
    - 컨테이너 이름이 다름 — CONTAINER_NAME env 로 override

  수동 지정:
    NODE_SECRET=<64자 hex> ./offline/wsl-agent-setup.sh

  또는 Jenkins REST 로 직접 추출:
    NODE_SECRET=\$(curl -sS -u admin:password \\
      "$JENKINS_URL/computer/$AGENT_NAME/slave-agent.jnlp" \\
      | sed -n 's/.*<argument>\\([a-f0-9]\\{64\\}\\)<\\/argument>.*/\\1/p' | head -n1)
    NODE_SECRET=\$NODE_SECRET ./offline/wsl-agent-setup.sh
EOT
    exit 1
  fi
  export NODE_SECRET
  log "  NODE_SECRET 자동 추출 완료: ${NODE_SECRET:0:16}..."
fi

command -v curl >/dev/null || err "curl 필요 (apt install curl)"

# AUTO_INSTALL_DEPS=true 일 때는 sudo 가 필요 (apt 설치).
if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    err "AUTO_INSTALL_DEPS=true 지만 sudo 미설치 — 의존성 자동 설치 불가. 수동 설치 후 AUTO_INSTALL_DEPS 없이 재실행."
  fi
  if ! command -v apt >/dev/null 2>&1; then
    warn "apt 가 없습니다 (Debian/Ubuntu 계열이 아님). 의존성은 수동으로 설치해주세요."
    AUTO_INSTALL_DEPS=false
  fi
fi

log "작업 디렉토리: $AGENT_DIR"
log "AUTO_INSTALL_DEPS=$AUTO_INSTALL_DEPS  OLLAMA_MODEL=$OLLAMA_MODEL"
mkdir -p "$AGENT_DIR"

# ── 1. 호스트 Ollama 도달성 확인 (정보성) ──────────────────────────────────
# 이 설계에서 Ollama 는 **Windows 네이티브** 에 설치되어 있고 (GPU 직접 CUDA),
# Docker 컨테이너는 `host.docker.internal:11434` 로 Docker Desktop 을 거쳐
# Windows Ollama 에 접근한다. WSL agent 자체는 Ollama 를 호출하지 않는다
# (추론은 컨테이너 내 Dify 의 책임). 이 단계는 **호스트 Ollama 기동 여부
# 사전 확인**만 수행하고, 실패해도 치명적 에러가 아니다 — 컨테이너 쪽 경로가
# 독립적으로 열려있다면 Pipeline 은 동작한다.
log "[1/7] 호스트 Ollama 도달성 확인 (정보성 — 실제 호출은 컨테이너 → host.docker.internal)"

# Windows Ollama 는 기본적으로 127.0.0.1 에만 바인드되어 WSL 에서 직접
# 보이지 않는다. 사용자가 `OLLAMA_HOST=0.0.0.0` 으로 바인드를 열어둔 경우에만
# 여기서 성공. 열지 않았어도 Docker Desktop 은 여전히 host.docker.internal 로
# 컨테이너 → Windows localhost:11434 를 포워딩하므로 전체 파이프라인은 동작.
OLLAMA_PING_URL="${OLLAMA_PING_URL:-}"
if [ -z "$OLLAMA_PING_URL" ]; then
  # 자동 탐색: 기본 경로 + (Linux 만) WSL default gateway. ip 명령은 Git Bash 에 없음.
  WIN_HOST=""
  if [ "$IS_WIN_HOST" = "false" ] && command -v ip >/dev/null 2>&1; then
    # set -e + pipefail 환경에서 ip 가 실패해도 트리거 안 되게 || true
    WIN_HOST=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}' || true)
  fi
  CANDIDATES="http://127.0.0.1:11434 http://${WIN_HOST:-127.0.0.1}:11434 http://host.docker.internal:11434"
else
  CANDIDATES="$OLLAMA_PING_URL"
fi

OLLAMA_REACHABLE=""
OLLAMA_MODELS_JSON=""
for url in $CANDIDATES; do
  if OLLAMA_MODELS_JSON=$(curl -fsS --max-time 2 "$url/api/tags" 2>/dev/null); then
    OLLAMA_REACHABLE="$url"
    break
  fi
done

if [ -n "$OLLAMA_REACHABLE" ]; then
  log "  호스트 Ollama 도달 가능: $OLLAMA_REACHABLE"
  if command -v python3 >/dev/null 2>&1; then
    HAS_MODEL=$(printf '%s' "$OLLAMA_MODELS_JSON" \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if any(m.get('name','')==sys.argv[1] or m.get('name','').split(':')[0]==sys.argv[1].split(':')[0] for m in d.get('models',[])) else 'no')" \
        "$OLLAMA_MODEL" 2>/dev/null || echo "unknown")
    case "$HAS_MODEL" in
      yes) log "  모델 $OLLAMA_MODEL (또는 동일 family) 존재 확인" ;;
      no)  warn "  모델 $OLLAMA_MODEL 이 Ollama 에 없습니다 — Windows 쪽에서 'ollama pull $OLLAMA_MODEL' 실행 필요"
           warn "  (Pipeline 실행 시 Dify Stage 3 에서 ConnectionError 가 납니다)" ;;
      *)   : ;;
    esac
  fi
else
  warn "  호스트 Ollama 에 WSL 에서 도달 불가 ($CANDIDATES 모두 실패)"
  warn "  → Windows Ollama 가 기본 127.0.0.1 바인드면 WSL 에서 안 보이는 게 정상입니다."
  warn "  → 컨테이너 쪽은 Docker Desktop 이 host.docker.internal → Windows localhost:11434 로 포워딩하므로 Pipeline 은 동작할 수 있습니다."
  warn "  → Windows 쪽에서 'ollama list' 로 모델 존재 여부를 수동 확인해주세요."
  warn "  → WSL 에서도 보고 싶다면 Windows User env OLLAMA_HOST=0.0.0.0 설정 후 Ollama 재기동."
fi

# GPU 가시성 점검 (정보성) — Windows 네이티브 Ollama 는 Windows 드라이버의
# CUDA 를 직접 쓰므로 WSL 안 nvidia-smi 여부와는 무관. 그냥 참고용.
if command -v nvidia-smi >/dev/null 2>&1; then
  if GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1); then
    [ -n "$GPU_NAME" ] && log "  WSL2 nvidia-smi 감지: $GPU_NAME (Windows 드라이버 WSL2 노출 OK)"
  fi
fi

# ── 2. JDK 21 확인/설치 ────────────────────────────────────────────────────
# Jenkins 2.479+ 및 remoting 3355.v 는 Java 21 로 컴파일된 bytecode 를 에이전트에
# 다운로드하므로, agent 쪽 JDK 가 21 미만이면 연결 직후 UnsupportedClassVersionError
# 로 offline 상태에 머문다.
log "[2/7] JDK 21 확인"

detect_java21() {
  local cand
  if [ "$IS_WIN_HOST" = "true" ]; then
    # Windows 호스트 — PATH 의 java.exe / java 와 일반 설치 경로 (공백 포함) 모두 시도.
    # 공백 path 는 array 로 보존해야 word-splitting 에 의해 깨지지 않는다.
    # 1) PATH 우선
    local p
    for cand in java.exe java; do
      p="$(command -v "$cand" 2>/dev/null || true)"
      if [ -n "$p" ] && [ -x "$p" ] && "$p" -version 2>&1 | head -1 | grep -qE 'version "21'; then
        echo "$p"; return 0
      fi
    done
    # 2) 표준 설치 위치 — bash 가 array 정의 시 글로브 확장. 매치 없으면 literal 유지 → -x 에서 걸러짐.
    local paths=(
      "/c/Program Files/Common Files/Oracle/Java/javapath/java.exe"
      /c/Program\ Files/Microsoft/jdk-21*/bin/java.exe
      /c/Program\ Files/Eclipse\ Adoptium/jdk-21*/bin/java.exe
      /c/Program\ Files/Java/jdk-21*/bin/java.exe
    )
    for p in "${paths[@]}"; do
      [ -x "$p" ] || continue
      if "$p" -version 2>&1 | head -1 | grep -qE 'version "21'; then
        echo "$p"; return 0
      fi
    done
    return 1
  fi
  # Linux
  for cand in \
      "/usr/lib/jvm/temurin-21-jdk-amd64/bin/java" \
      "/usr/lib/jvm/temurin-21-jdk-arm64/bin/java" \
      "/usr/lib/jvm/java-21-openjdk-amd64/bin/java" \
      "/usr/lib/jvm/java-21-openjdk-arm64/bin/java" \
      "/usr/lib/jvm/openjdk-21/bin/java" \
    ; do
    if [ -x "$cand" ]; then echo "$cand"; return 0; fi
  done
  # update-alternatives 로 선택된 java 가 21 이면 수용
  if command -v java >/dev/null 2>&1 && java -version 2>&1 | head -1 | grep -qE 'version "21'; then
    command -v java
    return 0
  fi
  return 1
}

JAVA_BIN="$(detect_java21 || true)"
if [ -z "$JAVA_BIN" ]; then
  if [ "$IS_WIN_HOST" = "true" ]; then
    err "JDK 21 미설치. winget install Microsoft.OpenJDK.21 (또는 Temurin 21) 실행 후 재시도."
  elif [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  JDK 21 미설치 — apt install openjdk-21-jdk-headless"
    sudo apt-get update
    sudo apt-get install -y openjdk-21-jdk-headless
    JAVA_BIN="$(detect_java21 || true)"
    [ -z "$JAVA_BIN" ] && err "openjdk-21 설치 후에도 JDK 21 바이너리 탐색 실패"
  else
    cat >&2 <<'EOT'
[wsl-agent-setup] ERROR: JDK 21 이 설치되지 않았습니다.

Jenkins 2.479+ (현재 이미지) 는 Java 21 bytecode 를 에이전트에 전송하므로
JDK 21 미만은 UnsupportedClassVersionError 로 즉시 연결 실패합니다.

설치 (Ubuntu 22.04+ 는 apt 에 openjdk-21 포함):
  sudo apt update && sudo apt install -y openjdk-21-jdk-headless

자동 설치 (이 스크립트가 apt install 수행):
  AUTO_INSTALL_DEPS=true NODE_SECRET=... ./wsl-agent-setup.sh

Windows (Git Bash):
  winget install Microsoft.OpenJDK.21
EOT
    exit 2
  fi
fi
export JAVA_BIN
log "  OK: $JAVA_BIN"
log "  $("$JAVA_BIN" -version 2>&1 | head -1)"

# ── 3. Python 3.11+ 확인/설치 ──────────────────────────────────────────────
log "[3/7] Python $PY_VERSION_MIN+ 확인"

detect_python() {
  local min_major min_minor cand
  min_major="${PY_VERSION_MIN%%.*}"
  min_minor="${PY_VERSION_MIN##*.}"
  local cands=()
  if [ "$IS_WIN_HOST" = "true" ]; then
    # py 런처가 있으면 가장 신뢰. 없으면 PATH 의 python.exe.
    if command -v py >/dev/null 2>&1; then
      for v in 3.12 3.11; do
        local p; p="$(py -$v -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
        [ -n "$p" ] && cands+=("$p")
      done
    fi
    cands+=(
      "$(command -v python.exe 2>/dev/null || true)"
      "$(command -v python 2>/dev/null || true)"
      "$LOCALAPPDATA/Programs/Python/Python312/python.exe"
      "$LOCALAPPDATA/Programs/Python/Python311/python.exe"
    )
  else
    cands=(
      "/usr/bin/python3.12"
      "/usr/bin/python3.11"
      "$(command -v python3.12 2>/dev/null || true)"
      "$(command -v python3.11 2>/dev/null || true)"
      "$(command -v python3 2>/dev/null || true)"
    )
  fi
  for cand in "${cands[@]}"; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info >= ($min_major,$min_minor) else 1)" 2>/dev/null; then
      echo "$cand"; return 0
    fi
  done
  return 1
}

PY_BIN="$(detect_python || true)"
if [ -z "$PY_BIN" ]; then
  if [ "$IS_WIN_HOST" = "true" ]; then
    err "Python $PY_VERSION_MIN+ 미설치. winget install Python.Python.3.11 (또는 3.12) 실행 후 재시도."
  elif [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  python3 $PY_VERSION_MIN+ 미존재 — apt install python3.12-venv (또는 python3.11-venv)"
    sudo apt-get update
    # Ubuntu 22.04 는 python3.10 이 기본 — 3.12 는 deadsnakes PPA 필요할 수 있음.
    # 22.04 native 로는 python3.11-venv 가 가용한 경우 많음. 기본 3.10 이면 아래가 실패할 수 있어
    # deadsnakes fallback 을 순차 시도.
    if ! sudo apt-get install -y python3.12 python3.12-venv 2>/dev/null; then
      if ! sudo apt-get install -y python3.11 python3.11-venv 2>/dev/null; then
        warn "  apt 기본 repo 에 python3.11+ 가 없습니다. deadsnakes PPA 추가 시도:"
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt-get update
        sudo apt-get install -y python3.12 python3.12-venv
      fi
    fi
    PY_BIN="$(detect_python || true)"
    [ -z "$PY_BIN" ] && err "python 설치 후에도 python3 탐색 실패"
  else
    err "python3 $PY_VERSION_MIN+ 필요. 'sudo apt install python3.12 python3.12-venv' 실행 (또는 AUTO_INSTALL_DEPS=true)"
  fi
fi
PY_VER=$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
log "  OK: $PY_BIN (python $PY_VER)"

# ── 4. venv + Playwright Chromium (호스트 설치 — headed 모드 핵심) ─────────
log "[4/7] venv 준비 + Playwright Chromium 호스트 설치"

VENV_DIR="$AGENT_DIR/venv"
VENV_PY="$VENV_DIR/$VENV_BIN_REL/$VENV_PY_NAME"
VENV_ACTIVATE="$VENV_DIR/$VENV_BIN_REL/activate"
venv_ok() {
  [ -x "$VENV_PY" ] || return 1
  VENV_DIR_NATIVE=$(to_win_path "$VENV_DIR" 2>/dev/null || printf "%s" "$VENV_DIR"); "$VENV_PY" -c "import sys, os; assert os.path.normcase(os.path.realpath(sys.prefix)) == os.path.normcase(os.path.realpath(r\"$VENV_DIR_NATIVE\"))" 2>/dev/null || return 1
  "$VENV_PY" -m pip --version >/dev/null 2>&1 || return 1
}
if [ ! -f "$VENV_ACTIVATE" ]; then
  log "  venv 생성: $VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
elif ! venv_ok; then
  log "  venv 무결성 손상 감지 — 재생성"
  rm -rf "$VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
else
  log "  venv 이미 존재 — 스킵"
fi

"$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1
REQ_PKGS=(requests playwright pillow pymupdf pytest pytest-xdist pytest-playwright fastapi uvicorn httpx pyotp python-multipart portalocker)
log "  pip install: ${REQ_PKGS[*]}"
"$VENV_PY" -m pip install --quiet "${REQ_PKGS[@]}"

# Windows venv 는 Scripts/ 인데 Jenkinsfile 은 venv/bin/activate 를 호출 — bin junction 으로 호환.
if [ "$IS_WIN_HOST" = "true" ] && [ ! -e "$VENV_DIR/bin" ]; then
  if make_junction "$VENV_DIR/bin" "$VENV_DIR/Scripts"; then
    log "  venv bin -> Scripts junction (Jenkinsfile 호환)"
  else
    warn "  venv bin junction 생성 실패 — Jenkinsfile 의 bin/activate 참조가 깨질 수 있음"
  fi
fi

# Playwright Chromium 캐시 — Linux: ~/.cache/ms-playwright/, Windows: %LOCALAPPDATA%\ms-playwright\
if ls -d "$PW_CACHE/chromium-"* >/dev/null 2>&1; then
  log "  Chromium 이미 설치됨 — 스킵 ($PW_CACHE)"
else
  if [ "$IS_WIN_HOST" = "true" ]; then
    log "  playwright install chromium (Windows 네이티브 — 100MB+ 다운로드)"
  elif command -v sudo >/dev/null 2>&1; then
    # 베어 Linux — Playwright 가 요구하는 X libs 사전 설치
    log "  Playwright Chromium 의존 apt 패키지 자동 설치 (sudo 필요)"
    sudo "$VENV_PY" -m playwright install-deps chromium 2>&1 \
      || warn "  install-deps 실패 — 이미 설치돼 있거나 수동 apt 필요. 계속 진행."
  fi
  "$VENV_PY" -m playwright install chromium
fi

# Linux 베어 호스트 — DISPLAY 가 있어야 headed Chromium 가 보인다.
if [ "$IS_WIN_HOST" = "false" ]; then
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    warn "  DISPLAY / WAYLAND_DISPLAY 가 비어있음 — headed 창이 안 뜰 수 있습니다."
  fi
fi

# ── 5. Jenkins Node remoteFS 절대경로 + workspace venv 심볼릭 링크 사전 준비 ──
#
# provision-apps.sh 는 Node 생성 시 remoteFS 를 "~/.dscore.ttc.playwright-agent" 로 설정했지만
# Jenkins remoting 이 `~` 를 expansion 하지 않아 workspace 가
# /absolute/.dscore.ttc.playwright-agent/~/.dscore.ttc.playwright-agent/workspace 로 꼬인다. 여기서
# 호스트 쪽 실제 홈 경로를 알고 있으므로 Groovy 로 Node config.xml 을 절대경로로 갱신.
#
# Pipeline Stage 1 이 ${WORKSPACE}/.qa_home/venv/bin/activate 를 요구하므로,
# 우리가 만든 AGENT_DIR/venv 를 해당 워크스페이스에 미리 심볼릭 링크.
ABS_WORKSPACE="$AGENT_DIR/workspace/ZeroTouch-QA"
log "[5/7] Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크"

# Jenkins 2.555 의 /crumbIssuer 는 404 일 수 있고, 이 이미지의 Jenkins 는 200 + JSON.
# python3 이 없는 Git Bash 환경 호환을 위해 sed 로 직접 파싱 (의존성 없음).
CRUMB_JSON=$(curl -sS -u admin:password "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null || true)
CRUMB=""
if [ -n "$CRUMB_JSON" ] && echo "$CRUMB_JSON" | grep -q '"crumb"'; then
  CRUMB_FIELD=$(echo "$CRUMB_JSON" | sed -n 's/.*"crumbRequestField":"\([^"]*\)".*/\1/p')
  CRUMB_VAL=$(echo "$CRUMB_JSON"   | sed -n 's/.*"crumb":"\([^"]*\)".*/\1/p')
  if [ -n "$CRUMB_FIELD" ] && [ -n "$CRUMB_VAL" ]; then
    CRUMB="${CRUMB_FIELD}:${CRUMB_VAL}"
  fi
fi
if [ -z "$CRUMB" ]; then
  warn "Jenkins crumb 획득 실패 — basic auth 만으로 진행 (POST 가 403 이면 본 Jenkins 인스턴스가 crumb 강제)"
fi
# Windows 호스트 — JVM 이 인식하는 Windows-style 경로 (C:\...) 로 remoteFS 갱신.
# Linux 호스트 — POSIX 절대경로 그대로.
AGENT_DIR_FOR_JVM=$(to_win_path "$AGENT_DIR")
# Groovy 문자열 안 백슬래시 escape (Windows path 의 \ 를 \\ 로)
AGENT_DIR_FOR_JVM_ESC=${AGENT_DIR_FOR_JVM//\\/\\\\}
GROOVY_UPDATE=$(cat <<GROOVY
import jenkins.model.Jenkins
def n = Jenkins.get().getNode("$AGENT_NAME")
if (n == null) { println "ERR: Node '$AGENT_NAME' 없음"; return }
def f = n.getClass().getSuperclass().getDeclaredField("remoteFS")
f.setAccessible(true)
f.set(n, "$AGENT_DIR_FOR_JVM_ESC")
Jenkins.get().updateNode(n)
println "OK remoteFS=" + n.getRemoteFS()
GROOVY
)
UPDATE_RESP=$(curl -sS -u admin:password ${CRUMB:+-H "$CRUMB"} \
    --data-urlencode "script=$GROOVY_UPDATE" \
    "$JENKINS_URL/scriptText" 2>&1)
log "  $UPDATE_RESP"

# workspace 스켈레톤 + venv 링크 (Linux=symlink, Windows=directory junction).
# make_junction 헬퍼가 OS 별로 분기.
mkdir -p "$ABS_WORKSPACE/.qa_home/artifacts"
if [ -d "$VENV_DIR/$VENV_BIN_REL" ]; then
  WS_VENV_LINK="$ABS_WORKSPACE/.qa_home/venv"
  remove_path "$WS_VENV_LINK"
  if make_junction "$WS_VENV_LINK" "$VENV_DIR"; then
    log "  workspace venv link/junction: $WS_VENV_LINK → $VENV_DIR"
  else
    warn "  workspace venv 링크 생성 실패 — Pipeline 이 .qa_home/venv 를 못 찾을 수 있음"
  fi
fi

# ── 6. agent.jar 다운로드 ──────────────────────────────────────────────────
log "[6/7] Jenkins agent.jar 다운로드: $JENKINS_URL/jnlpJars/agent.jar"
AGENT_JAR="$AGENT_DIR/agent.jar"
if [ -f "$AGENT_JAR" ] && [ "${FORCE_AGENT_DOWNLOAD:-false}" != "true" ]; then
  log "  agent.jar 이미 존재 — 스킵 (강제 재다운로드: FORCE_AGENT_DOWNLOAD=true)"
else
  curl -fL -o "$AGENT_JAR" "$JENKINS_URL/jnlpJars/agent.jar" \
    || err "agent.jar 다운로드 실패. Jenkins 가 $JENKINS_URL 에서 응답하는지 확인"
  log "  다운로드 완료: $(du -h "$AGENT_JAR" | cut -f1)"
fi

# ── 6.5 Recording 서비스 (Phase R-MVP TR.9) ────────────────────────────────
# WSL2 환경에서도 mac 과 동일 패턴 — nohup 백그라운드 기동. systemd 사용자
# 단위는 WSL distro 별 가용성이 들쭉날쭉이라 R-MVP 단계는 nohup 으로 통일.
# 운영자가 WSL 재시작 시 본 setup 스크립트 재실행으로 복구.

REC_PORT="${RECORDING_PORT:-18092}"
REC_PID_FILE="$AGENT_DIR/recording-service.pid"
REC_LOG_FILE="$AGENT_DIR/recording-service.log"
REC_RUN_SCRIPT="$AGENT_DIR/run-recording-service.sh"

if [ -f "$REC_PID_FILE" ]; then
  OLD_PID=$(cat "$REC_PID_FILE" 2>/dev/null || true)
  if [ -n "$OLD_PID" ]; then
    log "  [6.5] 기존 recording_service PID=$OLD_PID 정리 시도"
    if [ "$IS_WIN_HOST" = "true" ]; then
      taskkill //F //T //PID "$OLD_PID" 2>/dev/null || true
    else
      kill -0 "$OLD_PID" 2>/dev/null && kill "$OLD_PID" 2>/dev/null || true
    fi
    sleep 1
  fi
  rm -f "$REC_PID_FILE"
fi
# 포트 점유 프로세스 정리 (안전망)
if [ "$IS_WIN_HOST" = "true" ]; then
  # netstat -ano + taskkill (Windows). LISTENING 상태의 PID 만 잡는다.
  PORT_PIDS=$(netstat -ano 2>/dev/null | tr -d '\r' | awk -v p=":$REC_PORT" '$2 ~ p"$" && $4=="LISTENING" {print $5}' | sort -u)
  for _p in $PORT_PIDS; do taskkill //F //T //PID "$_p" 2>/dev/null || true; done
elif command -v fuser >/dev/null 2>&1; then
  fuser -k "$REC_PORT/tcp" 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:"$REC_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 | xargs -r -I{} kill {} 2>/dev/null || true
fi

cat > "$REC_RUN_SCRIPT" <<REC_EOF
#!/usr/bin/env bash
# Phase R-MVP TR.9 — recording_service 기동 (WSL nohup 백그라운드)
set -e
export PYTHONPATH="$ROOT_DIR:\${PYTHONPATH:-}"
export RECORDING_HOST_ROOT="\${RECORDING_HOST_ROOT:-\$HOME/.dscore.ttc.playwright-agent/recordings}"
export PYTHONUNBUFFERED="\${PYTHONUNBUFFERED:-1}"
# venv bin/ 을 PATH 앞에 둬야 codegen_runner.is_codegen_available() 가 'playwright'
# CLI 를 찾는다. 이 줄이 없으면 UI badge 가 ⚠ codegen 미설치 로 떨어진다.
export PATH="$(dirname "$VENV_PY"):\${PATH:-}"
LOG_FILE="\${RECORDING_SERVICE_LOG:-$REC_LOG_FILE}"
PID_FILE="\${RECORDING_SERVICE_PID:-$REC_PID_FILE}"
mkdir -p "$AGENT_DIR"
mkdir -p "\$RECORDING_HOST_ROOT"
echo "\$\$" > "\$PID_FILE"
{
  printf '\\n[%s] recording_service starting\\n' "\$(date '+%Y-%m-%d %H:%M:%S %z')"
  printf '[%s] cwd=%s\\n' "\$(date '+%Y-%m-%d %H:%M:%S %z')" "\$(pwd)"
  printf '[%s] log=%s\\n' "\$(date '+%Y-%m-%d %H:%M:%S %z')" "\$LOG_FILE"
  printf '[%s] pid_file=%s pid=%s\\n' "\$(date '+%Y-%m-%d %H:%M:%S %z')" "\$PID_FILE" "\$\$"
} >> "\$LOG_FILE"
exec >> "\$LOG_FILE" 2>&1
exec "$VENV_PY" -m uvicorn recording_service.server:app \\
  --host 127.0.0.1 --port $REC_PORT --workers 1 --log-level info
REC_EOF
chmod +x "$REC_RUN_SCRIPT"

log "[6.5] Recording 서비스 백그라운드 기동: port=$REC_PORT"
nohup "$REC_RUN_SCRIPT" > "$REC_LOG_FILE" 2>&1 &
echo $! > "$REC_PID_FILE"

_w=0
while [ $_w -lt 10 ]; do
  if curl -sS "http://127.0.0.1:$REC_PORT/healthz" >/dev/null 2>&1; then
    log "  [6.5] ✓ /healthz 응답 — http://127.0.0.1:$REC_PORT/"
    break
  fi
  _w=$((_w + 1))
  sleep 1
done
if [ $_w -ge 10 ]; then
  log "  [6.5] ⚠ recording_service 헬스체크 실패 — 로그 확인: $REC_LOG_FILE"
fi


# ── 7. 기동 스크립트 생성 + agent 연결 ─────────────────────────────────────
# 기존 agent.jar 프로세스 정리는 이미 step 0-A 에서 수행됨 (스크립트 시작 시점).
# 여기선 run-agent.sh 스크립트를 써 두고 foreground 로 agent 를 기동한다.

RUN_SCRIPT="$AGENT_DIR/run-agent.sh"
cat > "$RUN_SCRIPT" <<RUN_EOF
#!/usr/bin/env bash
# 이 파일은 wsl-agent-setup.sh 가 생성한 agent 기동 스크립트입니다.
# SCRIPTS_HOME 은 e2e-pipeline 저장소 위치 (이 파일의 부모 디렉토리 기준).
# JAVA_BIN 은 JDK 21 의 절대 경로 — 시스템 PATH 에 JDK 17 이 있더라도
# agent 는 반드시 JDK 21 로 기동해야 remoting 호환성이 맞는다.
set -e
export SCRIPTS_HOME="$ROOT_DIR"
# venv PATH 주입 — Pipeline 이 python3 을 호출할 때 host Chromium 이 깔린 venv 사용
export PATH="$VENV_DIR/bin:\$PATH"
cd "$AGENT_DIR"
exec "$JAVA_BIN" -jar agent.jar \\
  -url "$JENKINS_URL" \\
  -secret "$NODE_SECRET" \\
  -name "$AGENT_NAME" \\
  -workDir "$AGENT_DIR"
RUN_EOF
chmod +x "$RUN_SCRIPT"
log "[7/7] 기동 스크립트: $RUN_SCRIPT"
log "  SCRIPTS_HOME=$ROOT_DIR"
log "  venv=$VENV_DIR"
log "  JENKINS_URL=$JENKINS_URL"
log "  AGENT_NAME=$AGENT_NAME"

log "=========================================================================="
log "설정 완료. agent 를 연결합니다 (Ctrl+C 로 종료, 재연결 시 이 스크립트 재실행)."
if [ "$IS_WSL" = "true" ]; then
  log "Pipeline 이 headed 모드로 실행되면 Windows 데스크탑에 Chromium 창이 뜹니다 (WSLg)."
fi
log "=========================================================================="
log ""
exec "$RUN_SCRIPT"
