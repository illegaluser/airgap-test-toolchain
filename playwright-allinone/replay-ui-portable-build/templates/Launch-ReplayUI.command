#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export PYTHONHOME=""
export PYTHONPATH="$ROOT:$ROOT/site-packages"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/chromium"
export MONITOR_HOME="$ROOT/data"
export AUTH_PROFILES_DIR="$ROOT/data/auth-profiles"
export PYTHONIOENCODING="utf-8"

# 데이터 디렉토리 사전 생성.
mkdir -p "$ROOT/data/auth-profiles" "$ROOT/data/scenarios" "$ROOT/data/scripts" "$ROOT/data/runs"

# 포트 충돌 — 기존 인스턴스 우선.
if lsof -nP -iTCP:18094 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[Replay UI] Port 18094 already in use — opening existing instance."
  open "http://127.0.0.1:18094/"
  exit 0
fi

# Replay UI 백그라운드 기동.
nohup "$ROOT/python/bin/python3" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094 \
  > "$ROOT/data/runs/replay-ui.stdout.log" 2> "$ROOT/data/runs/replay-ui.stderr.log" &
echo $! > "$ROOT/.replay-ui.pid"

# 준비 폴링.
for i in $(seq 1 15); do
  if curl -sSf --max-time 1 "http://127.0.0.1:18094/" >/dev/null 2>&1; then
    open "http://127.0.0.1:18094/"
    exit 0
  fi
  sleep 1
done

echo "[Replay UI] Service did not come up within 15s. See $ROOT/data/runs/replay-ui.stderr.log"
exit 1
