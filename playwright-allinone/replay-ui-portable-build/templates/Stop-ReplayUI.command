#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$ROOT/.replay-ui.pid" ]]; then
  pid="$(cat "$ROOT/.replay-ui.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "[Replay UI] Stopped PID $pid"
  fi
  rm -f "$ROOT/.replay-ui.pid"
fi

# pid 파일 없거나 stale — 포트로 한 번 더 확인.
pids="$(lsof -nP -iTCP:18099 -sTCP:LISTEN -t 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
  echo "$pids" | xargs kill 2>/dev/null || true
  echo "[Replay UI] Stopped port 18099 holders: $pids"
fi

if [[ -z "${pids:-}" && ! -f "$ROOT/.replay-ui.pid" ]]; then
  echo "[Replay UI] No running instance."
fi
