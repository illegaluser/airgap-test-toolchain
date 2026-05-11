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
PYTHON_WINDOWS_VERSION="${PYTHON_WINDOWS_VERSION:-3.11.9}"
PYTHON_WINDOWS_INSTALLER="python-$PYTHON_WINDOWS_VERSION-amd64.exe"
PYTHON_WINDOWS_URL="${PYTHON_WINDOWS_URL:-https://www.python.org/ftp/python/$PYTHON_WINDOWS_VERSION/$PYTHON_WINDOWS_INSTALLER}"

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

# uvicorn 만 — [standard] extra 는 uvloop 를 끌어오는데 uvloop 는 Windows wheel
# 미존재. pip download --platform win_amd64 가 *현재 호스트* marker (Linux/WSL2)
# 로 dep 를 평가해서 uvloop 가 필요 dep 로 분류, win wheel 없음으로 ResolutionImpossible.
# Replay UI 는 local dev tool 이라 [standard] 의 성능 향상 extra (httptools/
# uvloop/...) 가 필요 없음. install-monitor 의 PACKAGES 도 `uvicorn` 만 사용 중.
# 2026-05-11 WSL2 빌드 실패 회귀 차단.
REQS_COMMON=(fastapi uvicorn pydantic playwright python-multipart wheel portalocker)
REQS_WIN64=(pywin32)
for t in "${TARGETS[@]}"; do
  out="$BUILD_DIR/wheels/$t"
  if [[ "$REUSE_CACHE" = "1" && -d "$out" && -n "$(ls -A "$out" 2>/dev/null)" ]]; then
    echo "[build] wheels 캐시 재사용 — $out"
    continue
  fi
  mkdir -p "$out"
  reqs=("${REQS_COMMON[@]}")
  if [[ "$t" = "win64" ]]; then
    # portalocker declares pywin32 behind a Windows environment marker. When
    # pip download runs on WSL/Linux with --platform win_amd64, that marker is
    # still evaluated from the build host, so include it explicitly.
    reqs+=("${REQS_WIN64[@]}")
  fi
  echo "[build] pip download → $out (target=$t)"
  "$PYTHON" -m pip download \
    --platform "${PIP_PLATFORM[$t]}" \
    --python-version 3.11 \
    --only-binary :all: \
    --dest "$out" \
    "${reqs[@]}"
done

# Windows target carries the official Python 3.11 installer so the monitoring PC
# can be provisioned offline without a preinstalled interpreter.
for t in "${TARGETS[@]}"; do
  if [[ "$t" != "win64" ]]; then
    continue
  fi
  out="$BUILD_DIR/python/$t"
  installer="$out/$PYTHON_WINDOWS_INSTALLER"
  if [[ "$REUSE_CACHE" = "1" && -s "$installer" ]]; then
    echo "[build] Python installer 캐시 재사용 — $installer"
    continue
  fi
  mkdir -p "$out"
  echo "[build] Python installer download → $installer"
  if command -v curl >/dev/null 2>&1; then
    curl -fL "$PYTHON_WINDOWS_URL" -o "$installer"
  else
    "$PYTHON" - "$PYTHON_WINDOWS_URL" "$installer" <<'PYPY'
import sys
import urllib.request

url, out = sys.argv[1], sys.argv[2]
urllib.request.urlretrieve(url, out)
PYPY
  fi
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

# 소스 복사. rsync 우선, 없으면 cp -r fallback — Git Bash (MSYS2) 환경에서는
# rsync 가 기본 미설치라 빌드가 깨지던 회귀 방지 (2026-05-11). 양쪽 결과 동등 —
# __pycache__ / *.pyc 제외.
mkdir -p "$BUILD_DIR/src"
for src in "$SRC_REPLAY" "$SRC_MONITOR" "$SRC_ZTQ" "$SRC_RECORDING"; do
  base="$(basename "$src")"
  dst="$BUILD_DIR/src/$base"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '__pycache__' --exclude '*.pyc' \
      "$src/" "$dst/"
  else
    rm -rf "$dst"
    mkdir -p "$dst"
    # find + cp 로 __pycache__ / *.pyc 제외하며 복사.
    (cd "$src" && find . \
        -type d -name __pycache__ -prune -o \
        -type f -name '*.pyc' -prune -o \
        -type f -print | while read -r f; do
      mkdir -p "$dst/$(dirname "$f")"
      cp "$f" "$dst/$f"
    done)
  fi
done
echo "[build] 소스 복사 완료"

# 설치 스크립트 + plist 템플릿.
cp "$ROOT/playwright-allinone/monitor-build/install-monitor.sh" "$BUILD_DIR/"
cp "$ROOT/playwright-allinone/monitor-build/install-monitor.ps1" "$BUILD_DIR/"
cp "$ROOT/playwright-allinone/monitor-build/install-monitor.cmd" "$BUILD_DIR/"
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
  install-monitor.cmd

설치 후 Replay UI: http://127.0.0.1:18094
EOF

# zip. CLI `zip` 우선, 없으면 Python zipfile fallback — Git Bash (MSYS2) 환경
# 에서는 zip 이 기본 미설치라 빌드가 깨지던 회귀 방지 (2026-05-11).
ZIP_NAME="monitor-runtime-$TS.zip"
ZIP_OUT="$ROOT/$ZIP_NAME"
[[ "$NO_CHROMIUM" = "1" ]] && ZIP_OUT="$ROOT/monitor-runtime-no-chromium-$TS.zip"
if command -v zip >/dev/null 2>&1; then
  (cd "$BUILD_DIR_ROOT" && zip -qr "$ZIP_OUT" "$(basename "$BUILD_DIR")")
else
  "$PYTHON" - "$BUILD_DIR_ROOT" "$(basename "$BUILD_DIR")" "$ZIP_OUT" <<'PYZIP'
import os, sys, zipfile
root, top, out = sys.argv[1], sys.argv[2], sys.argv[3]
base = os.path.join(root, top)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(base):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)  # top/ 부터 시작하는 경로로 압축.
            z.write(full, rel)
print(f"[build] python zipfile 로 zip 생성 완료: {out}")
PYZIP
fi
echo "[build] zip 산출 — $ZIP_OUT ($(du -h "$ZIP_OUT" | cut -f1))"

# sanity. unzip CLI 우선, 없으면 python zipfile.
if command -v unzip >/dev/null 2>&1; then
  LIST_CMD="unzip -l"
  list_output="$(unzip -l "$ZIP_OUT")"
else
  list_output="$("$PYTHON" -c "import sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    print('\n'.join(z.namelist()))" "$ZIP_OUT")"
fi
if ! grep -q "src/zero_touch_qa" <<<"$list_output"; then
  echo "[build] ERROR — sanity 실패: zero_touch_qa 미포함"
  exit 1
fi
if ! grep -q "install-monitor" <<<"$list_output"; then
  echo "[build] ERROR — sanity 실패: install-monitor 미포함"
  exit 1
fi
echo "[build] sanity OK"

# 캐시 모드 아니면 임시 빌드 디렉토리 정리.
if [[ "$REUSE_CACHE" != "1" ]]; then
  rm -rf "$BUILD_DIR"
fi

echo "[build] 완료 — $ZIP_OUT"
