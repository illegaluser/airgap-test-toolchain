#!/usr/bin/env bash
# build-cache.sh — Replay UI 휴대용 빌드 캐시 (.replay-ui-cache/cache/) 채움.
# pack-windows.ps1 / pack-macos.sh 가 이 캐시의 wheels / chromium 을 zip 안으로 옮긴다.
#
# 동작 — 캐시만 채움. zip 산출은 pack-*.{ps1,sh} 책임.
#
# 옵션:
#   --target <win64|macos-arm64|all>  기본 all (양쪽 OS 다 포함)
#   --python <path>                   pip download 에 쓸 호스트 python (기본 python3)
#
# 캐시 위치 (재실행 시 그대로 재사용):
#   .replay-ui-cache/cache/wheels/<target>/*.whl
#   .replay-ui-cache/cache/chromium/<target>/...
#   .replay-ui-cache/cache/python/win64/python-3.11.x-amd64.exe   (Windows 용 Python installer)
#   .replay-ui-cache/.build-tools/playwright-cli-venv/            (helper venv — chromium 받기용)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR_ROOT="${BUILD_DIR_ROOT:-$ROOT/.replay-ui-cache}"
BUILD_DIR="$BUILD_DIR_ROOT/cache"
PYTHON_WINDOWS_VERSION="${PYTHON_WINDOWS_VERSION:-3.11.9}"
PYTHON_WINDOWS_INSTALLER="python-$PYTHON_WINDOWS_VERSION-amd64.exe"
PYTHON_WINDOWS_URL="${PYTHON_WINDOWS_URL:-https://www.python.org/ftp/python/$PYTHON_WINDOWS_VERSION/$PYTHON_WINDOWS_INSTALLER}"
HOST_OS="$(uname -s)"
PW_PYTHON=""

# Playwright 버전은 wheels 디렉토리의 playwright-*.whl 과 helper venv 의
# playwright 가 일치해야 한다 (chromium revision 이 playwright 버전에 종속).
# 미래 빌드 머신에서 PyPI 의 최신 playwright 가 잡혀 revision 표류하지 않도록 핀.
PLAYWRIGHT_VERSION="${PLAYWRIGHT_VERSION:-1.59.0}"

TARGET="all"
PYTHON="python3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)       TARGET="$2"; shift 2 ;;
    --python)       PYTHON="$2"; shift 2 ;;
    -h|--help)
      head -16 "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

case "$TARGET" in
  win64)        TARGETS=(win64) ;;
  macos-arm64)  TARGETS=(macos-arm64) ;;
  all)          TARGETS=(win64 macos-arm64) ;;
  *) echo "잘못된 --target: $TARGET" >&2; exit 1 ;;
esac

mkdir -p "$BUILD_DIR"
echo "[build-cache] BUILD_DIR=$BUILD_DIR"
echo "[build-cache] PLAYWRIGHT_VERSION=$PLAYWRIGHT_VERSION"

# wheels/<os>/ 채우기.
# macOS 기본 /bin/bash 는 3.2 라 associative array (`declare -A`) 미지원 → 함수 분기.
pip_platform_for() {
  case "$1" in
    win64)        echo "win_amd64" ;;
    macos-arm64)  echo "macosx_11_0_arm64" ;;
    *) echo "알 수 없는 target: $1" >&2; return 1 ;;
  esac
}

# uvicorn 만 — [standard] extra 는 uvloop 를 끌어오는데 uvloop 는 Windows wheel
# 미존재. pip download --platform win_amd64 가 *현재 호스트* marker (Linux/WSL2)
# 로 dep 를 평가해서 uvloop 가 필요 dep 로 분류, win wheel 없음으로 ResolutionImpossible.
REQS_COMMON=(fastapi uvicorn pydantic "playwright==$PLAYWRIGHT_VERSION" python-multipart wheel portalocker requests pyotp pillow)
REQS_WIN64=(pywin32 colorama)
for t in "${TARGETS[@]}"; do
  out="$BUILD_DIR/wheels/$t"
  mkdir -p "$out"
  reqs=("${REQS_COMMON[@]}")
  if [[ "$t" = "win64" ]]; then
    # portalocker declares pywin32 behind a Windows environment marker. When
    # pip download runs on WSL/Linux with --platform win_amd64, that marker is
    # still evaluated from the build host, so include it explicitly.
    reqs+=("${REQS_WIN64[@]}")
  fi
  if [[ -d "$out" && -n "$(ls -A "$out" 2>/dev/null)" ]]; then
    echo "[build-cache] pip download 증분 확인 → $out (target=$t)"
  else
    echo "[build-cache] pip download → $out (target=$t)"
  fi
  "$PYTHON" -m pip download \
    --platform "$(pip_platform_for "$t")" \
    --python-version 3.11 \
    --only-binary :all: \
    --exists-action i \
    --dest "$out" \
    "${reqs[@]}"
done

# Playwright browser download uses the build host's Python environment. Keep the
# required CLI in an isolated helper venv so a bare /usr/bin/python3 can still
# produce a complete cache.
ensure_playwright_cli() {
  # 호스트 python 의 playwright 가 핀과 *일치* 할 때만 그대로 사용.
  # 일치하지 않으면 helper venv 를 만들어 핀 버전으로 격리 (chromium revision
  # 표류 차단). 호스트 playwright 가 다른 버전이면 그 버전의 chromium 을
  # 받게 되어 wheels 의 playwright 와 mismatch 가 발생한다 (실측 회귀).
  local host_version
  host_version="$("$PYTHON" -m pip show playwright 2>/dev/null | awk '/^Version:/ {print $2}')"
  if [[ "$host_version" = "$PLAYWRIGHT_VERSION" ]]; then
    PW_PYTHON="$PYTHON"
    return
  fi

  local tool_dir="$BUILD_DIR_ROOT/.build-tools/playwright-cli-venv"
  local tool_py="$tool_dir/bin/python"
  if [[ "$HOST_OS" =~ MINGW|MSYS|CYGWIN ]]; then
    tool_py="$tool_dir/Scripts/python.exe"
  fi
  if [[ ! -x "$tool_py" ]]; then
    echo "[build-cache] build helper venv 생성 — playwright CLI 준비"
    "$PYTHON" -m venv "$tool_dir"
    "$tool_py" -m pip install --upgrade pip
    "$tool_py" -m pip install "playwright==$PLAYWRIGHT_VERSION"
  fi
  # helper venv 의 playwright 버전이 핀과 일치하지 않으면 (이전 빌드 잔재 등)
  # 강제로 핀 버전으로 재설치 — chromium revision 표류 차단.
  local installed_version
  installed_version="$("$tool_py" -m pip show playwright 2>/dev/null | awk '/^Version:/ {print $2}')"
  if [[ "$installed_version" != "$PLAYWRIGHT_VERSION" ]]; then
    echo "[build-cache] helper venv 의 playwright 가 $installed_version (핀: $PLAYWRIGHT_VERSION) — 재설치"
    "$tool_py" -m pip install --upgrade "playwright==$PLAYWRIGHT_VERSION"
  fi
  if ! "$tool_py" -c 'import playwright' >/dev/null 2>&1; then
    echo "[build-cache] ERROR — playwright CLI 준비 실패: $tool_py" >&2
    exit 1
  fi
  PW_PYTHON="$tool_py"
}

validate_chromium_payload() {
  local target="$1"
  local out="$2"
  case "$target" in
    win64)
      if find "$out" \( -path '*/chrome-win64/chrome.exe' -o -path '*/chrome-win/chrome.exe' \) -type f -print -quit | grep -q .; then
        return 0
      fi
      ;;
    macos-arm64)
      if find "$out" \( \
          -path '*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' \
          -o -path '*/chrome-mac/Chromium.app/Contents/MacOS/Chromium' \
        \) -type f -print -quit | grep -q .; then
        return 0
      fi
      ;;
  esac
  return 1
}

# Windows Python installer 캐시 — pack-windows.ps1 이 embeddable Python 을 직접
# 받으므로 일상 빌드에서는 사용 안 함. 1회 설치형 시절의 보조 자료로 남겨둠
# (디스크 절약 필요 시 .replay-ui-cache/cache/python/ 폴더 수동 제거 OK).
for t in "${TARGETS[@]}"; do
  if [[ "$t" != "win64" ]]; then
    continue
  fi
  out="$BUILD_DIR/python/$t"
  installer="$out/$PYTHON_WINDOWS_INSTALLER"
  if [[ -s "$installer" ]]; then
    echo "[build-cache] Python installer 캐시 재사용 — $installer"
    continue
  fi
  mkdir -p "$out"
  echo "[build-cache] Python installer download → $installer"
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
# 매번 `playwright install chromium` 을 호출한다 — 이미 받은 revision 은 skip,
# 핀 (1.59.0) 기준 새 revision 이 캐시에 없으면 그 revision 만 추가로 받음 (idempotent).
# validate 분기로 "cache 있으면 skip" 처리하던 이전 동작은, 호스트 playwright 가
# 다른 버전으로 한 번 받아둔 캐시가 있으면 핀 revision 을 영영 못 받는 회귀를
# 일으켜 제거. 캐시에 옛 revision 폴더가 잔존해도 무해 (사용은 핀 기준만 됨).
ensure_playwright_cli
for t in "${TARGETS[@]}"; do
  out="$BUILD_DIR/chromium/$t"
  mkdir -p "$out"
  echo "[build-cache] playwright install chromium → $out (target=$t)"
  if ! PLAYWRIGHT_BROWSERS_PATH="$out" "$PW_PYTHON" -m playwright install chromium; then
    echo "[build-cache] WARN — chromium 다운로드 실패 (target=$t). pack-* 단계에서 보충 시도."
    continue
  fi
  if ! validate_chromium_payload "$t" "$out"; then
    echo "[build-cache] WARN — chromium payload가 target=$t 형식이 아님. pack-* 단계에서 보충 시도."
  fi
done

echo "[build-cache] 완료 — 캐시 위치: $BUILD_DIR"
