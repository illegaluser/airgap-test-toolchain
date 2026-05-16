#!/usr/bin/env bash
# D 그룹 build-time selftest dispatcher.
#
# 호출 시점: ./build.sh 끝 부분 (성공 메시지 직전).
# 동작:
#   - 환경 자동 감지 (Mac native / WSL2 / Linux container 가용성).
#   - 적용 가능한 selftest 만 실행.
#   - 모든 결과를 stdout + selftest.log 로 출력.
#   - 어떤 selftest 가 실패해도 build 자체는 통과 (warn 만). build 가 *생성*
#     한 산출물은 이미 디스크에 있고, selftest 실패는 사후 가드 신호일 뿐.
#
# 설계 근거: ../docs/PLAN_E2E_REWRITE.md §5 그룹 D.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLAYWRIGHT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
LOG="$PLAYWRIGHT_DIR/selftest.log"

VENV_PY="${E2E_PYTHON:-$HOME/.dscore.ttc.playwright-agent/venv/bin/python3}"

if [ ! -x "$VENV_PY" ]; then
  echo "[selftest] ⚠ venv python 미존재 — skip ($VENV_PY)" | tee "$LOG"
  echo "[selftest]   설정: mac-agent-setup.sh / wsl-agent-setup.sh 로 venv 생성" | tee -a "$LOG"
  exit 0
fi

PYTHONPATH="$PLAYWRIGHT_DIR/shared:$PLAYWRIGHT_DIR/replay-ui:$PLAYWRIGHT_DIR/recording-ui:${PYTHONPATH:-}"
export PYTHONPATH

echo "[selftest] D 그룹 build-time 시작 — $(date '+%Y-%m-%d %H:%M:%S')" | tee "$LOG"
echo "[selftest] cwd=$PLAYWRIGHT_DIR" | tee -a "$LOG"
echo "[selftest] python=$VENV_PY" | tee -a "$LOG"

# 모든 환경에 적용되는 핵심 selftest (Mac / WSL2 / Linux).
SUITES=(
  "selftest_convert.py            converter_ast + regression_generator import smoke"
  "selftest_replay_regression.py  emit + subprocess run exit 0"
)

PASS=0
FAIL=0
for entry in "${SUITES[@]}"; do
  script="$(echo "$entry" | awk '{print $1}')"
  desc="$(echo "$entry" | cut -d' ' -f2-)"
  echo "[selftest] >> $script — $desc" | tee -a "$LOG"
  START=$(date +%s)
  if "$VENV_PY" "$SCRIPT_DIR/$script" 2>&1 | tee -a "$LOG"; then
    ELAPSED=$(( $(date +%s) - START ))
    echo "[selftest]    PASS (${ELAPSED}s)" | tee -a "$LOG"
    PASS=$((PASS + 1))
  else
    ELAPSED=$(( $(date +%s) - START ))
    echo "[selftest]    FAIL (${ELAPSED}s)" | tee -a "$LOG"
    FAIL=$((FAIL + 1))
  fi
done

echo "[selftest] 완료 — PASS=$PASS FAIL=$FAIL" | tee -a "$LOG"

# build 차단 안 함 — warn 만. 사용자 결정: D 그룹 selftest 는 *사후 가드 신호*.
if [ "$FAIL" -gt 0 ]; then
  echo "[selftest] ⚠ $FAIL 개 selftest 실패 — 자세한 내용: $LOG" >&2
fi
exit 0
