#!/usr/bin/env bash
# Standalone Recording UI launcher.
#
# This runs only the host-side FastAPI daemon that serves Recording UI and its
# API. It does not start Jenkins, Dify, Ollama, or the Jenkins agent.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="${DSCORE_AGENT_DIR:-$HOME/.dscore.ttc.playwright-agent}"
HOST="${RECORDING_HOST:-127.0.0.1}"
PORT="${RECORDING_PORT:-18092}"

# Python: agent venv 우선, env override 가능. Windows venv (Git Bash 에서 보면
# Scripts/python.exe) 와 POSIX venv (bin/python) 둘 다 자동 감지.
# run-replay-ui.sh 와 동일 패턴 — Git Bash 의 `python3` 가 Windows Store stub
# 으로 잡혀 launcher 가 실패하던 회귀 방지 (2026-05-11).
if [ -x "$AGENT_DIR/venv/bin/python" ]; then
  DEFAULT_PY="$AGENT_DIR/venv/bin/python"
elif [ -x "$AGENT_DIR/venv/Scripts/python.exe" ]; then
  DEFAULT_PY="$AGENT_DIR/venv/Scripts/python.exe"
else
  DEFAULT_PY="python3"
fi
PYTHON_BIN="${RECORDING_PYTHON:-${VENV_PY:-$DEFAULT_PY}}"
PID_FILE="${RECORDING_SERVICE_PID:-$AGENT_DIR/recording-service.pid}"
LOG_FILE="${RECORDING_SERVICE_LOG:-$AGENT_DIR/recording-service.log}"
RECORDINGS_DIR="${RECORDING_HOST_ROOT:-$AGENT_DIR/recordings}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [start|stop|restart|status|logs|foreground|doctor]

Commands:
  start       Start Recording UI in the background (default)
  stop        Stop the background daemon
  restart     Stop then start
  status      Show daemon health and paths
  logs        Follow the daemon log
  foreground  Run uvicorn in the current terminal
  doctor      Check Python modules and external tools

Environment:
  RECORDING_PORT          Port to bind (default: 18092)
  RECORDING_HOST          Host to bind (default: 127.0.0.1)
  RECORDING_PYTHON        Python executable (default: python3)
  RECORDING_HOST_ROOT     Recordings dir (default: ~/.dscore.ttc.playwright-agent/recordings)
  RECORDING_SERVICE_LOG   Log file path
  RECORDING_SERVICE_PID   PID file path
  RECORDING_CONTAINER_NAME Docker container used for Stop & Convert (default in code: dscore.ttc.playwright)
  AUTH_PROFILES_DIR       Login profile catalog dir (default: ~/ttc-allinone-data/auth-profiles).
                          Point Recording UI and Replay UI to the same dir to share logins
                          on a single host. See docs/replay-ui-guide.md §11.
EOF
}

log() {
  printf '[recording-ui] %s\n' "$*"
}

ensure_dirs() {
  mkdir -p "$AGENT_DIR" "$RECORDINGS_DIR"
}

python_bin_dir() {
  "$PYTHON_BIN" - <<'PY'
import os
import sys
print(os.path.dirname(os.path.realpath(sys.executable)))
PY
}

export_runtime_env() {
  ensure_dirs
  # PYTHONPATH 구분자: Windows native python.exe 는 `;` 만 인식. Git Bash 에서
  # 띄울 때도 동일 — `:` 로 합치면 두 경로가 한 경로로 잘못 해석된다.
  local pysep=":"
  [[ "${OS:-}" == "Windows_NT" ]] && pysep=";"
  export PYTHONPATH="$ROOT_DIR${pysep}$ROOT_DIR/shared${pysep}${PYTHONPATH:-}"
  export RECORDING_HOST_ROOT="$RECORDINGS_DIR"
  export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
  # Windows 콘솔 cp949 한글 깨짐 회귀 방지 — 자식 subprocess stdout/stderr UTF-8 강제.
  export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
  # auth-profiles 디렉토리 — Recording UI 기본은 ~/ttc-allinone-data/auth-profiles.
  # 같은 호스트에서 Replay UI 와 로그인 프로파일을 공유하려면 두 launcher 를
  # 같은 AUTH_PROFILES_DIR 로 띄우면 된다 (docs/replay-ui-guide.md §11 참조).
  # 이전엔 export 가 빠져 사용자가 AUTH_PROFILES_DIR=... 로 띄워도 child python
  # 에 전달이 안 되던 회귀 (2026-05-11) — 기본값은 동일, 사용자 사전 설정은 존중.
  export AUTH_PROFILES_DIR="${AUTH_PROFILES_DIR:-$HOME/ttc-allinone-data/auth-profiles}"

  # Playwright installed in a venv exposes the `playwright` console script next
  # to python. recording_service.codegen_runner currently probes PATH.
  local py_dir
  py_dir="$(python_bin_dir)"
  export PATH="$py_dir:$PATH"
}

check_python_modules() {
  "$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

required = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "multipart": "python-multipart",
    "requests": "requests",
}
missing = [pkg for mod, pkg in required.items() if importlib.util.find_spec(mod) is None]
if missing:
    print("Missing Python packages: " + ", ".join(missing), file=sys.stderr)
    print(
        "Install them in the selected Python environment, for example:\n"
        "  python3 -m pip install fastapi uvicorn pydantic python-multipart requests playwright httpx pyotp",
        file=sys.stderr,
    )
    sys.exit(1)
PY
}

health_url() {
  printf 'http://%s:%s/healthz' "$HOST" "$PORT"
}

ui_url() {
  printf 'http://%s:%s/' "$HOST" "$PORT"
}

http_ok() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "$url" >/dev/null 2>&1
    return $?
  fi
  "$PYTHON_BIN" - "$url" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.5) as resp:
        sys.exit(0 if 200 <= resp.status < 300 else 1)
except Exception:
    sys.exit(1)
PY
}

pid_from_file() {
  if [ -f "$PID_FILE" ]; then
    sed -n '1p' "$PID_FILE" 2>/dev/null || true
  fi
}

is_pid_running() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

port_owner_hint() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$PORT" 2>/dev/null || true
  fi
}

doctor() {
  export_runtime_env

  log "root: $ROOT_DIR"
  log "python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
  log "recordings: $RECORDINGS_DIR"
  log "log: $LOG_FILE"
  log "pid: $PID_FILE"

  check_python_modules
  log "python modules: ok"

  if command -v playwright >/dev/null 2>&1; then
    log "playwright CLI: $(command -v playwright)"
  else
    log "playwright CLI: missing (Start Recording will fail until it is installed)"
  fi

  if command -v docker >/dev/null 2>&1; then
    log "docker CLI: $(command -v docker)"
  else
    log "docker CLI: missing (Stop & Convert will fail until Docker is available)"
  fi
}

foreground() {
  export_runtime_env
  check_python_modules

  log "starting foreground server: $(ui_url)"
  log "health: $(health_url)"
  log "recordings: $RECORDINGS_DIR"
  exec "$PYTHON_BIN" -m uvicorn recording_service.server:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers 1 \
    --log-level info
}

start() {
  export_runtime_env
  check_python_modules

  if http_ok "$(health_url)"; then
    log "already running: $(ui_url)"
    log "log: $LOG_FILE"
    return 0
  fi

  local old_pid
  old_pid="$(pid_from_file)"
  if is_pid_running "$old_pid"; then
    log "PID file points to a running process ($old_pid), but health check failed."
    log "Use '$0 restart' if this is a stale or unhealthy Recording UI process."
    return 1
  fi
  rm -f "$PID_FILE"

  if port_owner_hint | grep -q .; then
    log "port $PORT is already in use:"
    port_owner_hint
    return 1
  fi

  {
    printf '\n[%s] recording_ui launcher start\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '[%s] root=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$ROOT_DIR"
    printf '[%s] python=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
    printf '[%s] recordings=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$RECORDINGS_DIR"
  } >> "$LOG_FILE"

  RECORDING_HOST="$HOST" \
  RECORDING_PORT="$PORT" \
  RECORDING_PYTHON="$PYTHON_BIN" \
  RECORDING_HOST_ROOT="$RECORDINGS_DIR" \
  RECORDING_SERVICE_LOG="$LOG_FILE" \
  RECORDING_SERVICE_PID="$PID_FILE" \
    nohup "$0" foreground >> "$LOG_FILE" 2>&1 &
  local child_pid=$!
  echo "$child_pid" > "$PID_FILE"

  local i
  for i in $(seq 1 20); do
    if http_ok "$(health_url)"; then
      log "started: $(ui_url)"
      log "pid: $child_pid"
      log "log: $LOG_FILE"
      return 0
    fi
    if ! is_pid_running "$child_pid"; then
      log "process exited before health check passed. Recent log:"
      tail -40 "$LOG_FILE" 2>/dev/null || true
      return 1
    fi
    sleep 0.5
  done

  log "health check timeout: $(health_url)"
  log "recent log:"
  tail -40 "$LOG_FILE" 2>/dev/null || true
  return 1
}

stop() {
  local pid
  pid="$(pid_from_file)"
  if ! is_pid_running "$pid"; then
    rm -f "$PID_FILE"
    if http_ok "$(health_url)"; then
      log "health check still responds, but PID file is stale. Inspect port owner:"
      port_owner_hint
      return 1
    fi
    log "not running"
    return 0
  fi

  log "stopping pid=$pid"
  kill "$pid" 2>/dev/null || true
  local i
  for i in $(seq 1 20); do
    if ! is_pid_running "$pid"; then
      rm -f "$PID_FILE"
      log "stopped"
      return 0
    fi
    sleep 0.25
  done

  log "pid=$pid did not exit after SIGTERM; sending SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
}

status() {
  local pid
  pid="$(pid_from_file)"
  log "url: $(ui_url)"
  log "health: $(health_url)"
  log "recordings: $RECORDINGS_DIR"
  log "log: $LOG_FILE"
  log "pid_file: $PID_FILE"
  if is_pid_running "$pid"; then
    log "pid: $pid (running)"
  else
    log "pid: ${pid:-none} (not running)"
  fi
  if http_ok "$(health_url)"; then
    log "health: ok"
  else
    log "health: not responding"
  fi
}

follow_logs() {
  ensure_dirs
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

cmd="${1:-start}"
case "$cmd" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) follow_logs ;;
  foreground) foreground ;;
  doctor) doctor ;;
  -h|--help|help) usage ;;
  *)
    usage
    exit 2
    ;;
esac
