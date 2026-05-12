#!/usr/bin/env bash
# Standalone Replay UI launcher (D17 일원화 후, Recording UI 의 run-recording-ui.sh 와 동일 패턴).
#
# Recording UI 의 run-recording-ui.sh 와 대칭 — env 자동 셋업 + nohup detach +
# PID 관리. 사용자는 ./run-replay-ui.sh restart 한 줄로 재기동 가능.
#
# 본 스크립트는 host-side FastAPI daemon 만 띄운다 (Jenkins / Dify / Ollama /
# Jenkins agent 와 무관). install-monitor.ps1 / install-monitor.sh 는 1회 셋업
# (venv / chromium / 모듈 복사) 전용 — 일상 재기동은 본 스크립트.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${MONITOR_HOME:-$HOME/.dscore.ttc.monitor}"
HOST="${REPLAY_HOST:-127.0.0.1}"
PORT="${REPLAY_PORT:-18094}"

# Python: monitor venv 우선, env override 가능. Windows venv (Git bash 에서
# 보면 Scripts/python.exe) 와 POSIX venv (bin/python) 둘 다 자동 감지.
if [ -x "$INSTALL_ROOT/venv/bin/python" ]; then
  DEFAULT_PY="$INSTALL_ROOT/venv/bin/python"
elif [ -x "$INSTALL_ROOT/venv/Scripts/python.exe" ]; then
  DEFAULT_PY="$INSTALL_ROOT/venv/Scripts/python.exe"
else
  DEFAULT_PY="python3"
fi
PYTHON_BIN="${REPLAY_PYTHON:-${VENV_PY:-$DEFAULT_PY}}"
PID_FILE="${REPLAY_SERVICE_PID:-$INSTALL_ROOT/replay-ui.pid}"
LOG_FILE="${REPLAY_SERVICE_LOG:-$INSTALL_ROOT/replay-ui.stdout.log}"
STDERR_FILE="${REPLAY_SERVICE_STDERR:-$INSTALL_ROOT/replay-ui.stderr.log}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [start|stop|restart|status|logs|foreground|doctor]

Commands:
  start       Start Replay UI in the background (default)
  stop        Stop the background daemon
  restart     Stop then start
  status      Show daemon health and paths
  logs        Follow the daemon log
  foreground  Run uvicorn in the current terminal
  doctor      Check Python modules and external tools

Environment:
  REPLAY_PORT             Port to bind (default: 18094)
  REPLAY_HOST             Host to bind (default: 127.0.0.1)
  REPLAY_PYTHON           Python executable (default: \$MONITOR_HOME/venv 의 python)
  MONITOR_HOME            Replay UI 데이터 루트 (default: ~/.dscore.ttc.monitor)
  REPLAY_SERVICE_LOG      Log 파일 경로
  REPLAY_SERVICE_PID      PID 파일 경로
EOF
}

log() {
  printf '[replay-ui] %s\n' "$*"
}

ensure_dirs() {
  mkdir -p "$INSTALL_ROOT" \
           "$INSTALL_ROOT/auth-profiles" \
           "$INSTALL_ROOT/scenarios" \
           "$INSTALL_ROOT/scripts" \
           "$INSTALL_ROOT/runs" \
           "$INSTALL_ROOT/chromium"
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
  # 소스 우선 — venv site-packages 미설치 환경에서도 동작.
  # PYTHONPATH 구분자: Windows native python.exe 는 `;` 만 인식. Git Bash 에서
  # 띄울 때도 동일 — `:` 로 합치면 두 경로가 한 경로로 잘못 해석된다.
  local pysep=":"
  [[ "${OS:-}" == "Windows_NT" ]] && pysep=";"
  export PYTHONPATH="$ROOT_DIR${pysep}$ROOT_DIR/shared${pysep}${PYTHONPATH:-}"
  export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
  # Windows 콘솔 default codec (cp949) 가 UTF-8 byte 를 garbage 로 디코딩하는
  # 회귀 방지 — 자식 subprocess (monitor replay-script / codegen_trace_wrapper /
  # zero_touch_qa) 모두 UTF-8 stdout/stderr 강제.
  export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$INSTALL_ROOT/chromium}"
  export AUTH_PROFILES_DIR="${AUTH_PROFILES_DIR:-$INSTALL_ROOT/auth-profiles}"
  export MONITOR_HOME="$INSTALL_ROOT"

  # monitor venv 의 bin/Scripts 를 PATH 첫 자리에 — `playwright open ...`
  # subprocess (시드 단계) 가 monitor venv 의 playwright CLI 를 정확히 잡도록
  # (run-recording-ui.sh 와 동일 패턴). 이 줄 누락 시 시드의 `playwright open`
  # 이 PATH 의 다른 playwright 를 잡아 "Looks like Playwright was just
  # installed or updated. playwright install" 에러로 떨어진다.
  local py_dir
  py_dir="$(python_bin_dir)"
  export PATH="$py_dir:$PATH"
}

check_python_modules() {
  "$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

required = ["fastapi", "uvicorn", "pydantic", "playwright"]
missing = [m for m in required if importlib.util.find_spec(m) is None]
if missing:
    print("Missing Python packages: " + ", ".join(missing), file=sys.stderr)
    print(
        "monitor venv 에 누락된 모듈입니다. install-monitor.{ps1,sh} 가 venv 셋업과 \n"
        "모듈 복사를 해 주는 1회 셋업 도구입니다.",
        file=sys.stderr,
    )
    sys.exit(1)
PY
}

health_url() {
  printf 'http://%s:%s/api/profiles' "$HOST" "$PORT"
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

doctor() {
  export_runtime_env
  log "root: $ROOT_DIR"
  log "install_root: $INSTALL_ROOT"
  log "python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
  log "log: $LOG_FILE"
  log "pid: $PID_FILE"
  check_python_modules
  log "python modules: ok"
}

foreground() {
  export_runtime_env
  check_python_modules
  log "starting foreground server: $(ui_url)"
  log "health: $(health_url)"
  exec "$PYTHON_BIN" -m uvicorn replay_service.server:app \
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
    log "Use '$0 restart' if this is a stale or unhealthy Replay UI process."
    return 1
  fi
  rm -f "$PID_FILE"

  {
    printf '\n[%s] replay_ui launcher start\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '[%s] root=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$ROOT_DIR"
    printf '[%s] install_root=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$INSTALL_ROOT"
    printf '[%s] python=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
  } >> "$LOG_FILE"

  REPLAY_HOST="$HOST" \
  REPLAY_PORT="$PORT" \
  REPLAY_PYTHON="$PYTHON_BIN" \
  MONITOR_HOME="$INSTALL_ROOT" \
  REPLAY_SERVICE_LOG="$LOG_FILE" \
  REPLAY_SERVICE_PID="$PID_FILE" \
    nohup "$0" foreground >> "$LOG_FILE" 2>> "$STDERR_FILE" &
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
      tail -20 "$STDERR_FILE" 2>/dev/null || true
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
      log "health check still responds, but PID file is stale. 다른 프로세스가 포트 점유 중일 수 있습니다."
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
  log "install_root: $INSTALL_ROOT"
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
