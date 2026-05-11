#!/usr/bin/env bash
# install-monitor.sh — 모니터링 PC 자동 프로비저닝 (Mac · Linux 네이티브).
#
# 이 PC 에 Replay UI 가 동작할 모든 것을 한 번에 설치한다. 이미 설치된 항목은 SKIP.
#
# 두 가지 레이아웃 자동 감지:
#   (A) monitor-runtime-<ts>.zip 을 푼 폴더 — wheels/<OS>/, chromium/<OS>/, src/<모듈>/
#   (B) 소스 트리 (playwright-allinone/monitor-build/) — wheels 없음, ../<모듈>/ 직접 사용 + 온라인 PyPI fallback
#
# Usage:
#   bash install-monitor.sh [--register-startup] [--register-task] [--python <python3>]

set -euo pipefail

INSTALL_ROOT="${MONITOR_HOME:-$HOME/.dscore.ttc.monitor}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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
  --register-task      30분 주기 모니터 스케줄러 등록 (cron) 안내. 시나리오 .py 별 행은 사용자가 마무리.
USAGE
      exit 0
      ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

# 소스 트리 레이아웃 감지: SCRIPT_DIR 가 .../playwright-allinone/monitor-build/ 인 경우
# 부모 디렉토리에서 모듈을 직접 가져온다 (zip 빌드 없이도 동작).
IS_SOURCE_TREE=0
if [[ -d "$PARENT_DIR/replay_service" && -d "$PARENT_DIR/monitor" && -d "$PARENT_DIR/zero_touch_qa" ]]; then
  IS_SOURCE_TREE=1
fi

OS="$(uname -s)"
case "$OS" in
  Darwin) OS_TAG=macos-arm64 ;;
  Linux)  OS_TAG=linux-x86_64 ;;
  *) echo "지원 OS 아님: $OS" >&2; exit 1 ;;
esac

echo "[install-monitor] OS=$OS  OS_TAG=$OS_TAG  INSTALL_ROOT=$INSTALL_ROOT"
if [[ "$IS_SOURCE_TREE" = "1" ]]; then
  echo "[install-monitor] 레이아웃: 소스 트리 (playwright-allinone 안)"
else
  echo "[install-monitor] 레이아웃: monitor-runtime zip"
fi

# Python 검증. monitor-runtime wheels are built with --python-version 3.11,
# so the target interpreter must be Python 3.11.x, not just 3.11+.
PY_VER="$("$PYTHON" -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>&1)" || {
  echo "Python 확인 실패: $PY_VER  (--python <python3 path> 옵션으로 명시 가능)" >&2
  exit 1
}
PY_MAJOR="${PY_VER%.*}"
PY_MINOR="${PY_VER#*.}"
if [[ "$PY_MAJOR" -ne 3 || "$PY_MINOR" -ne 11 ]]; then
  echo "Python 3.11.x required; current $PY_VER (offline wheels are cp311-only)" >&2
  exit 1
fi
echo "[install-monitor] Python $PY_VER OK"

# 1. 디렉토리.
mkdir -p \
  "$INSTALL_ROOT/venv" \
  "$INSTALL_ROOT/chromium" \
  "$INSTALL_ROOT/auth-profiles" \
  "$INSTALL_ROOT/scenarios" \
  "$INSTALL_ROOT/runs"
echo "[install-monitor] 디렉토리 생성 완료"

# 2. venv (이미 있으면 재사용).
if [[ -x "$INSTALL_ROOT/venv/bin/python" ]]; then
  VENV_VER="$("$INSTALL_ROOT/venv/bin/python" -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>&1 || true)"
  if [[ "$VENV_VER" != "3.11" ]]; then
    echo "[install-monitor] existing venv Python $VENV_VER does not match cp311 wheels; recreating venv"
    rm -rf "$INSTALL_ROOT/venv"
  fi
fi
if [[ ! -x "$INSTALL_ROOT/venv/bin/python" ]]; then
  "$PYTHON" -m venv "$INSTALL_ROOT/venv"
  echo "[install-monitor] venv 생성"
else
  echo "[install-monitor] 기존 venv 재사용 (SKIP)"
fi

VENV_PY="$INSTALL_ROOT/venv/bin/python"

# 3. Python 패키지 설치 (이미 설치된 건 pip 가 자동 SKIP).
PACKAGES=(fastapi uvicorn pydantic playwright python-multipart portalocker)
WHEELS_DIR="$SCRIPT_DIR/wheels/$OS_TAG"
if [[ -d "$WHEELS_DIR" ]]; then
  echo "[install-monitor] 오프라인 wheels 사용: $WHEELS_DIR"
  "$VENV_PY" -m pip install --no-index --find-links "$WHEELS_DIR" --upgrade pip wheel \
    || echo "[install-monitor] pip 자체 업그레이드 skip"
  "$VENV_PY" -m pip install --no-index --find-links "$WHEELS_DIR" "${PACKAGES[@]}"
else
  echo "[install-monitor] 온라인 PyPI 사용 (오프라인 wheels 없음)"
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PY" -m pip install "${PACKAGES[@]}"
fi
echo "[install-monitor] 패키지 설치 OK"

# 4. Chromium — 오프라인 카피 우선, 없으면 playwright install (이미 받은 게 있으면 자동 SKIP).
CHROMIUM_SRC="$SCRIPT_DIR/chromium/$OS_TAG"
CHROMIUM_DST="$INSTALL_ROOT/chromium"
if [[ -d "$CHROMIUM_SRC" ]]; then
  cp -R "$CHROMIUM_SRC"/* "$CHROMIUM_DST/" 2>/dev/null || true
  echo "[install-monitor] Chromium 오프라인 배치 완료"
else
  echo "[install-monitor] Chromium 다운로드 (playwright install — 이미 받은 게 있으면 SKIP)"
  PLAYWRIGHT_BROWSERS_PATH="$CHROMIUM_DST" "$VENV_PY" -m playwright install chromium
fi

# 5. 프로젝트 모듈 — 항상 최신본으로 덮어쓴다.
SP="$("$VENV_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
if [[ "$IS_SOURCE_TREE" = "1" ]]; then
  MODULES_ROOT="$PARENT_DIR"
else
  MODULES_ROOT="$SCRIPT_DIR/src"
fi
for mod in replay_service monitor zero_touch_qa recording_service; do
  src="$MODULES_ROOT/$mod"
  if [[ -d "$src" ]]; then
    rm -rf "$SP/$mod"
    cp -R "$src" "$SP/"
    echo "[install-monitor] 모듈 배치: $mod"
  else
    echo "[install-monitor] WARN — 모듈 소스 없음: $src"
  fi
done

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

# 7. 30분 스케줄러 안내 (D17 — 단일 .py 흐름).
if [[ "$REGISTER_TASK" = "1" ]]; then
  cat <<HINT
[install-monitor] 스케줄러 등록 안내 — crontab -e 에 다음 패턴으로 시나리오 .py 별 행을 추가하세요:
*/30 * * * * PLAYWRIGHT_BROWSERS_PATH=$INSTALL_ROOT/chromium AUTH_PROFILES_DIR=$INSTALL_ROOT/auth-profiles MONITOR_HOME=$INSTALL_ROOT $INSTALL_ROOT/venv/bin/python -m monitor replay-script $INSTALL_ROOT/scripts/<시나리오.py> --out $INSTALL_ROOT/runs/auto-\$(date +\%Y\%m\%dT\%H\%M\%S) --profile <프로파일이름>
HINT
fi

cat <<DONE

[install-monitor] 셋업 완료 (D17 — .py 일원화).
  Replay UI:   http://127.0.0.1:18094  (--register-startup 안 했으면 수동 기동)
  단일 진입점 launcher (Recording UI 와 동등 패턴, 권장):
    bash <repo>/playwright-allinone/run-replay-ui.sh restart
  CLI 실행:        $INSTALL_ROOT/venv/bin/python -m monitor replay-script <시나리오.py> --out <결과폴더> [--profile <alias>] [--verify-url <URL>]
  로그인 프로파일: $INSTALL_ROOT/venv/bin/python -m monitor profile seed <이름> --target <사이트URL>
DONE
