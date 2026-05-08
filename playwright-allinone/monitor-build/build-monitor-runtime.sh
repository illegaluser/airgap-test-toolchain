#!/usr/bin/env bash
# build-monitor-runtime.sh — monitor-runtime-<ts>.zip 빌더 (Mac · Linux 빌드 머신).
#
# 산출: 프로젝트 루트에 monitor-runtime-<timestamp>.zip
#
# 옵션:
#   --target <win64|macos-arm64|all>  기본 all (양쪽 OS 다 포함)
#   --no-chromium                     Chromium 제외 (이미 설치된 모니터링 PC 용 작은 패키지)
#   --reuse-cache                     wheels/chromium 캐시 재사용
#   --python <path>                   pip download 에 쓸 호스트 python (기본 python3)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ALLINONE="$ROOT/playwright-allinone"
SRC_RECORDING="$ALLINONE/recording_service"
SRC_REPLAY="$ALLINONE/replay_service"
SRC_MONITOR="$ALLINONE/monitor"
SRC_ZTQ="$ALLINONE/zero_touch_qa"
BUILD_DIR_ROOT="${BUILD_DIR_ROOT:-$ROOT/.monitor-runtime-cache}"

TARGET="all"
NO_CHROMIUM=0
REUSE_CACHE=0
PYTHON="python3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)       TARGET="$2"; shift 2 ;;
    --no-chromium)  NO_CHROMIUM=1; shift ;;
    --reuse-cache)  REUSE_CACHE=1; shift ;;
    --python)       PYTHON="$2"; shift 2 ;;
    -h|--help)
      head -25 "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

case "$TARGET" in
  win64)        TARGETS=(win64) ;;
  macos-arm64)  TARGETS=(macos-arm64) ;;
  all)          TARGETS=(win64 macos-arm64) ;;
  *) echo "잘못된 --target: $TARGET" >&2; exit 1 ;;
esac

TS="$(date +%Y%m%d-%H%M%S)"
BUILD_DIR="$BUILD_DIR_ROOT/monitor-runtime-build-$TS"
[[ "$REUSE_CACHE" = "1" ]] && BUILD_DIR="$BUILD_DIR_ROOT/monitor-runtime-build-cache"
mkdir -p "$BUILD_DIR"
echo "[build] BUILD_DIR=$BUILD_DIR"

# wheels/<os>/ 채우기.
declare -A PIP_PLATFORM=(
  [win64]="win_amd64"
  [macos-arm64]="macosx_11_0_arm64"
)

REQS=(fastapi "uvicorn[standard]" pydantic playwright python-multipart)
for t in "${TARGETS[@]}"; do
  out="$BUILD_DIR/wheels/$t"
  if [[ "$REUSE_CACHE" = "1" && -d "$out" && -n "$(ls -A "$out" 2>/dev/null)" ]]; then
    echo "[build] wheels 캐시 재사용 — $out"
    continue
  fi
  mkdir -p "$out"
  echo "[build] pip download → $out (target=$t)"
  "$PYTHON" -m pip download \
    --platform "${PIP_PLATFORM[$t]}" \
    --python-version 3.11 \
    --only-binary :all: \
    --dest "$out" \
    "${REQS[@]}"
done

# Chromium.
if [[ "$NO_CHROMIUM" != "1" ]]; then
  for t in "${TARGETS[@]}"; do
    out="$BUILD_DIR/chromium/$t"
    if [[ "$REUSE_CACHE" = "1" && -d "$out" && -n "$(ls -A "$out" 2>/dev/null)" ]]; then
      echo "[build] chromium 캐시 재사용 — $out"
      continue
    fi
    mkdir -p "$out"
    echo "[build] playwright install chromium → $out (target=$t)"
    PLAYWRIGHT_BROWSERS_PATH="$out" "$PYTHON" -m playwright install chromium \
      || echo "[build] WARN — chromium 다운로드 실패 (target=$t). 빌드 계속."
  done
fi

# 소스 복사.
mkdir -p "$BUILD_DIR/src"
for src in "$SRC_REPLAY" "$SRC_MONITOR" "$SRC_ZTQ" "$SRC_RECORDING"; do
  base="$(basename "$src")"
  rsync -a --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$src/" "$BUILD_DIR/src/$base/"
done
echo "[build] 소스 복사 완료"

# 설치 스크립트 + plist 템플릿.
cp "$ROOT/playwright-allinone/monitor-build/install-monitor.sh" "$BUILD_DIR/"
cp "$ROOT/playwright-allinone/monitor-build/install-monitor.ps1" "$BUILD_DIR/"
cp "$ROOT/playwright-allinone/monitor-build/dscore.replay-ui.plist.template" "$BUILD_DIR/"
chmod +x "$BUILD_DIR/install-monitor.sh"

# README.
cat > "$BUILD_DIR/README.txt" <<EOF
DSCORE 모니터링 PC 셋업 패키지 — monitor-runtime
=================================================
빌드 시각: $TS
포함 OS:   ${TARGETS[*]}

설치 (Mac/Linux):
  bash install-monitor.sh --register-startup --register-task

설치 (Windows):
  powershell -ExecutionPolicy Bypass -File install-monitor.ps1 -RegisterStartup -RegisterTask

설치 후 Replay UI: http://127.0.0.1:18094
EOF

# zip.
ZIP_NAME="monitor-runtime-$TS.zip"
ZIP_OUT="$ROOT/$ZIP_NAME"
[[ "$NO_CHROMIUM" = "1" ]] && ZIP_OUT="$ROOT/monitor-runtime-no-chromium-$TS.zip"
(cd "$BUILD_DIR_ROOT" && zip -qr "$ZIP_OUT" "$(basename "$BUILD_DIR")")
echo "[build] zip 산출 — $ZIP_OUT ($(du -h "$ZIP_OUT" | cut -f1))"

# sanity.
if ! unzip -l "$ZIP_OUT" | grep -q "src/zero_touch_qa"; then
  echo "[build] ERROR — sanity 실패: zero_touch_qa 미포함"
  exit 1
fi
if ! unzip -l "$ZIP_OUT" | grep -q "install-monitor"; then
  echo "[build] ERROR — sanity 실패: install-monitor 미포함"
  exit 1
fi
echo "[build] sanity OK"

# 캐시 모드 아니면 임시 빌드 디렉토리 정리.
if [[ "$REUSE_CACHE" != "1" ]]; then
  rm -rf "$BUILD_DIR"
fi

echo "[build] 완료 — $ZIP_OUT"
