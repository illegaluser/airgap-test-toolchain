#!/usr/bin/env bash
# install-monitor.sh — 모니터링 PC 자동 프로비저닝 (Mac · Linux).
#
# monitor-runtime-<ts>.zip 을 풀어둔 디렉토리 안에서 실행.
# 사람 손은 (옵션 prompt 외) 안 들어감.
#
# Usage:
#   bash install-monitor.sh [--register-startup] [--register-task] [--python <python3>]

set -euo pipefail

INSTALL_ROOT="${MONITOR_HOME:-$HOME/.dscore.ttc.monitor}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="python3"
REGISTER_STARTUP=0
REGISTER_TASK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --register-startup) REGISTER_STARTUP=1; shift ;;
    --register-task)    REGISTER_TASK=1; shift ;;
    --python)           PYTHON="$2"; shift 2 ;;
    -h|--help)
      cat <<USAGE
Usage: $0 [--register-startup] [--register-task] [--python <python3>]
  --register-startup   Replay UI 를 사용자 startup task 로 등록 (Mac=launchd / Linux=systemd --user)
  --register-task      30분 주기 모니터 스케줄러 등록 (cron). bundle 별로 행을 사용자가 마무리.
USAGE
      exit 0
      ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

OS="$(uname -s)"
case "$OS" in
  Darwin) OS_TAG=macos-arm64 ;;
  Linux)  OS_TAG=linux-x86_64 ;;
  *) echo "지원 OS 아님: $OS" >&2; exit 1 ;;
esac

echo "[install-monitor] OS=$OS  OS_TAG=$OS_TAG  INSTALL_ROOT=$INSTALL_ROOT"

# 1. 디렉토리.
mkdir -p \
  "$INSTALL_ROOT/venv" \
  "$INSTALL_ROOT/chromium" \
  "$INSTALL_ROOT/auth-profiles" \
  "$INSTALL_ROOT/scenarios" \
  "$INSTALL_ROOT/runs"
echo "[install-monitor] 디렉토리 생성 완료"

# 2. venv (이미 있으면 재사용).
if [[ ! -x "$INSTALL_ROOT/venv/bin/python" ]]; then
  "$PYTHON" -m venv "$INSTALL_ROOT/venv"
  echo "[install-monitor] venv 생성"
else
  echo "[install-monitor] 기존 venv 재사용"
fi

VENV_PIP="$INSTALL_ROOT/venv/bin/pip"
VENV_PY="$INSTALL_ROOT/venv/bin/python"

# 3. 의존성 wheels 설치 (offline).
WHEELS_DIR="$SCRIPT_DIR/wheels/$OS_TAG"
if [[ -d "$WHEELS_DIR" ]]; then
  "$VENV_PIP" install --no-index --find-links "$WHEELS_DIR" --upgrade pip wheel \
    || echo "[install-monitor] pip 자체 업그레이드 skip"
  "$VENV_PIP" install --no-index --find-links "$WHEELS_DIR" \
    fastapi uvicorn pydantic playwright python-multipart
  echo "[install-monitor] wheels 설치 완료"
else
  echo "[install-monitor] WARN — wheels/$OS_TAG 디렉토리 없음. 온라인 fallback 시도."
  "$VENV_PIP" install fastapi uvicorn pydantic playwright python-multipart
fi

# 4. Chromium 배치.
CHROMIUM_SRC="$SCRIPT_DIR/chromium/$OS_TAG"
if [[ -d "$CHROMIUM_SRC" ]]; then
  cp -R "$CHROMIUM_SRC"/* "$INSTALL_ROOT/chromium/" 2>/dev/null || true
  echo "[install-monitor] Chromium 배치 완료"
fi

# 5. 프로젝트 모듈 (소스 그대로).
PY_VER="$("$VENV_PY" -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}")')"
SP="$INSTALL_ROOT/venv/lib/$PY_VER/site-packages"
if [[ ! -d "$SP" ]]; then
  # 일부 distro 는 lib64 경로.
  SP="$("$VENV_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
fi
for mod in replay_service monitor zero_touch_qa recording_service; do
  src="$SCRIPT_DIR/src/$mod"
  if [[ -d "$src" ]]; then
    rm -rf "$SP/$mod"
    cp -R "$src" "$SP/"
  fi
done
echo "[install-monitor] 프로젝트 모듈 site-packages 에 배치"

# 6. startup task.
if [[ "$REGISTER_STARTUP" = "1" ]]; then
  if [[ "$OS" = "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/dscore.replay-ui.plist"
    mkdir -p "$(dirname "$PLIST")"
    sed "s|__HOME__|$HOME|g" "$SCRIPT_DIR/dscore.replay-ui.plist.template" > "$PLIST"
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "[install-monitor] launchd LaunchAgent 등록: $PLIST"
  elif [[ "$OS" = "Linux" ]]; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/dscore-replay-ui.service" <<EOS
[Unit]
Description=DSCORE Replay UI
After=default.target

[Service]
Environment=PLAYWRIGHT_BROWSERS_PATH=$INSTALL_ROOT/chromium
Environment=AUTH_PROFILES_DIR=$INSTALL_ROOT/auth-profiles
Environment=MONITOR_HOME=$INSTALL_ROOT
ExecStart=$INSTALL_ROOT/venv/bin/python -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094
Restart=on-failure

[Install]
WantedBy=default.target
EOS
    systemctl --user daemon-reload
    systemctl --user enable --now dscore-replay-ui.service || true
    echo "[install-monitor] systemd --user unit 등록 완료"
  fi
fi

# 7. 30분 스케줄러 안내 (사용자가 crontab -e 로 bundle 별 행을 직접 추가).
if [[ "$REGISTER_TASK" = "1" ]]; then
  cat <<HINT
[install-monitor] 스케줄러 등록 안내 — crontab -e 에 다음 패턴으로 bundle 별 행을 추가하세요:
*/30 * * * * PLAYWRIGHT_BROWSERS_PATH=$INSTALL_ROOT/chromium AUTH_PROFILES_DIR=$INSTALL_ROOT/auth-profiles MONITOR_HOME=$INSTALL_ROOT $INSTALL_ROOT/venv/bin/python -m monitor replay $INSTALL_ROOT/scenarios/<bundle.zip> --out $INSTALL_ROOT/runs/auto-\$(date +\%Y\%m\%dT\%H\%M\%S)
HINT
fi

cat <<DONE

[install-monitor] 셋업 완료.
  Replay UI:   http://127.0.0.1:18094  (--register-startup 안 했으면 수동 기동)
  수동 기동:   PLAYWRIGHT_BROWSERS_PATH=$INSTALL_ROOT/chromium AUTH_PROFILES_DIR=$INSTALL_ROOT/auth-profiles MONITOR_HOME=$INSTALL_ROOT $INSTALL_ROOT/venv/bin/python -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094

  CLI replay:  $INSTALL_ROOT/venv/bin/python -m monitor replay <bundle.zip> --out <dir>
  alias 시드:  $INSTALL_ROOT/venv/bin/python -m monitor profile seed <alias> --target <url>
DONE
