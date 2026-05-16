#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# 첫 실행 시 macOS Gatekeeper quarantine 비트 제거 — zip 내부 python3 / chromium 이
# 시그니처 미검증으로 차단되는 회귀 차단. xattr 미설치 / 권한 없음은 silent skip.
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true

export PYTHONHOME=""
export PYTHONPATH="$ROOT:$ROOT/site-packages"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/chromium"
export MONITOR_HOME="$ROOT/data"
export AUTH_PROFILES_DIR="$ROOT/data/auth-profiles"
export PYTHONIOENCODING="utf-8"

# 데이터 디렉토리 사전 생성.
mkdir -p "$ROOT/data/auth-profiles" "$ROOT/data/scenarios" "$ROOT/data/scripts" "$ROOT/data/runs"

# E 그룹 receiving-PC selftest — 첫 실행 1회 (../docs/PLAN_E2E_REWRITE.md §5 E).
# 통과 시 .selftest_done 마커 → 다음 실행에선 skip. 실패해도 launcher 는 계속.
if [ ! -f "$ROOT/.selftest_done" ] && [ -f "$ROOT/selftest-receive.py" ]; then
  echo "[Replay UI] First-run selftest..."
  if "$ROOT/python/bin/python3" "$ROOT/selftest-receive.py"; then
    touch "$ROOT/.selftest_done"
  else
    echo "[Replay UI] Selftest reported issues — see output above. Continuing."
  fi
fi

# 포트 충돌 — 기존 인스턴스 우선.
if lsof -nP -iTCP:18099 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[Replay UI] Port 18099 already in use — opening existing instance."
  open "http://127.0.0.1:18099/"
  exit 0
fi

# Replay UI 백그라운드 기동.
nohup "$ROOT/python/bin/python3" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18099 \
  > "$ROOT/data/runs/replay-ui.stdout.log" 2> "$ROOT/data/runs/replay-ui.stderr.log" &
echo $! > "$ROOT/.replay-ui.pid"

# 준비 폴링.
for i in $(seq 1 15); do
  if curl -sSf --max-time 1 "http://127.0.0.1:18099/" >/dev/null 2>&1; then
    open "http://127.0.0.1:18099/"
    exit 0
  fi
  sleep 1
done

echo "[Replay UI] Service did not come up within 15s. See $ROOT/data/runs/replay-ui.stderr.log"
exit 1
