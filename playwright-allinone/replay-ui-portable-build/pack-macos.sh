#!/usr/bin/env bash
# pack-macos.sh — Replay UI 휴대용 자산을 `playwright-allinone/replay-ui/` 안에
# 직접 채워넣는다 (macOS arm64).
#
# 반출 모델 — replay-ui/ 폴더 자체가 portable. 본 스크립트는 그 폴더 안에
# relocatable CPython · 외부 의존 패키지 · Chromium · 공용 코드 · 실행파일을
# 직접 채워넣는다. 채워넣은 후 사용자는 `zip -r replay-ui.zip replay-ui/`
# 한 줄로 반출하거나, 본 스크립트의 --make-zip 옵션으로 자동 zip 생성.
#
# 사전 조건 — `.replay-ui-cache/cache/` 안에
#   wheels/macos-arm64/*.whl, chromium/macos-arm64/... 채워져 있어야 함.
#   비어있으면 먼저:
#     bash playwright-allinone/replay-ui-portable-build/build-cache.sh \
#       --target macos-arm64
#
# Usage:
#   bash pack-macos.sh [--reuse-cache] [--make-zip]
#
# 환경변수:
#   PBS_URL    python-build-standalone tarball URL override

set -euo pipefail

REUSE_CACHE=0
MAKE_ZIP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reuse-cache) REUSE_CACHE=1; shift ;;
    --make-zip)    MAKE_ZIP=1; shift ;;
    -h|--help)
      head -22 "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$BUILD_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ALLINONE_DIR/.." && pwd)"
REPLAYUI_DIR="$ALLINONE_DIR/replay-ui"
SHARED_DIR="$ALLINONE_DIR/shared"

CACHE_ROOT="$REPO_ROOT/.replay-ui-cache"
CACHE_BUILD_DIR="$CACHE_ROOT/cache"
WHEELS_DIR="$CACHE_BUILD_DIR/wheels/macos-arm64"
CHROMIUM_SRC="$CACHE_BUILD_DIR/chromium/macos-arm64"

TEMPLATES_DIR="$BUILD_DIR/templates"
BUILD_OUT_DIR="$BUILD_DIR/build-out"

echo "[pack-macos] ReplayUiDir = $REPLAYUI_DIR"
echo "[pack-macos] CacheBuildDir = $CACHE_BUILD_DIR"

# --- 사전 조건 ---------------------------------------------------------
if [[ ! -d "$WHEELS_DIR" ]] || ! ls "$WHEELS_DIR"/*.whl >/dev/null 2>&1; then
  cat >&2 <<EOF
wheels 캐시가 비어있습니다 — $WHEELS_DIR
먼저:
  bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target macos-arm64
EOF
  exit 1
fi
WHEEL_COUNT="$(ls "$WHEELS_DIR"/*.whl 2>/dev/null | wc -l | tr -d ' ')"
echo "[pack-macos] wheels 캐시 OK ($WHEEL_COUNT files)"

if [[ ! -d "$CHROMIUM_SRC" ]]; then
  echo "[pack-macos] WARN — chromium 캐시 없음: $CHROMIUM_SRC"
  echo "[pack-macos] Chromium 미동봉으로 진행."
fi

# --- python-build-standalone tarball URL 결정 -------------------------
# PBS_URL env 가 있으면 그대로 사용. 없으면 GitHub API 로 latest release 에서
# cpython-3.11 aarch64-apple-darwin install_only tarball 자동 탐색.
if [[ -z "${PBS_URL:-}" ]]; then
  echo "[pack-macos] Resolving latest python-build-standalone release ..."
  py_bin="$(command -v python3 || command -v python || true)"
  if [[ -z "$py_bin" ]]; then
    echo "[pack-macos] ERROR — python3 미존재 (PBS_URL 자동 조회 불가)" >&2
    exit 1
  fi
  PBS_URL="$("$py_bin" - <<'PYRESOLVE'
import json, sys, urllib.request
try:
    r = json.loads(urllib.request.urlopen(
        "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest",
        timeout=15,
    ).read())
except Exception as e:
    sys.stderr.write(f"GitHub API 조회 실패: {e}\n")
    sys.exit(2)
for a in r.get("assets", []):
    name = a.get("name", "")
    if name.startswith("cpython-3.11.") and name.endswith("aarch64-apple-darwin-install_only.tar.gz"):
        print(a["browser_download_url"])
        sys.exit(0)
sys.stderr.write("latest release 에 macOS arm64 install_only cp311 tarball 없음\n")
sys.exit(3)
PYRESOLVE
)"
  echo "[pack-macos] PBS_URL=$PBS_URL"
fi
PBS_TARBALL="$CACHE_ROOT/$(basename "$PBS_URL")"

if [[ "$REUSE_CACHE" != "1" ]] || [[ ! -s "$PBS_TARBALL" ]]; then
  mkdir -p "$CACHE_ROOT"
  echo "[pack-macos] Downloading python-build-standalone -> $PBS_TARBALL"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 "$PBS_URL" -o "$PBS_TARBALL"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$PBS_TARBALL" "$PBS_URL"
  else
    echo "[pack-macos] ERROR — curl 또는 wget 필요" >&2
    exit 1
  fi
fi
if [[ ! -s "$PBS_TARBALL" ]] || [[ "$(stat -f%z "$PBS_TARBALL" 2>/dev/null || stat -c%s "$PBS_TARBALL")" -lt 10000000 ]]; then
  echo "[pack-macos] ERROR — tarball 손상 또는 너무 작음: $PBS_TARBALL" >&2
  exit 1
fi

# --- replay-ui/ 폴더의 이전 자산 비우기 (재실행 안전) ----------------
for name in python site-packages chromium recording_shared zero_touch_qa; do
  p="$REPLAYUI_DIR/$name"
  if [[ -e "$p" ]]; then
    echo "[pack-macos] Clean previous -> $p"
    rm -rf "$p"
  fi
done
for f in Launch-ReplayUI.command Stop-ReplayUI.command README.txt; do
  p="$REPLAYUI_DIR/$f"
  [[ -e "$p" ]] && rm -f "$p"
done

# 1. python/ — relocatable CPython 풀기.
echo "[pack-macos] Extracting python-build-standalone -> python/"
tar -xzf "$PBS_TARBALL" -C "$REPLAYUI_DIR"
if [[ ! -x "$REPLAYUI_DIR/python/bin/python3" ]]; then
  echo "[pack-macos] ERROR — python-build-standalone 의 python/bin/python3 미발견" >&2
  ls -la "$REPLAYUI_DIR" >&2
  exit 1
fi

# 2. site-packages/ 채우기 (외부 의존성만).
SITE_PKG="$REPLAYUI_DIR/site-packages"
mkdir -p "$SITE_PKG"
PACKAGES=(fastapi uvicorn pydantic playwright python-multipart portalocker requests pyotp pillow)
echo "[pack-macos] pip install --target site-packages (offline wheels)"
"$REPLAYUI_DIR/python/bin/python3" -m pip install \
  --target "$SITE_PKG" \
  --no-index --find-links "$WHEELS_DIR" \
  --platform macosx_11_0_arm64 \
  --python-version 3.11 \
  --only-binary :all: \
  --implementation cp --abi cp311 \
  "${PACKAGES[@]}"

# 3. Chromium 복사.
CHROMIUM_DST="$REPLAYUI_DIR/chromium"
if [[ -d "$CHROMIUM_SRC" ]]; then
  mkdir -p "$CHROMIUM_DST"
  echo "[pack-macos] Copying Chromium -> chromium/"
  cp -R "$CHROMIUM_SRC"/* "$CHROMIUM_DST/"
fi

# 3b. chromium revision 보정 — cache 의 chromium 과 site-packages 의 playwright 가
# 기대하는 revision 이 일치하지 않을 수 있음 (build-cache.sh 의 helper
# venv 가 다른 playwright 버전을 갖고 받은 경우). relocatable python 으로 한 번 더
# install 호출해서 누락 revision 만 보충 (E2E 회귀 검출).
mkdir -p "$CHROMIUM_DST"
PLAYWRIGHT_BROWSERS_PATH="$CHROMIUM_DST" \
  "$REPLAYUI_DIR/python/bin/python3" -m playwright install chromium \
  || echo "[pack-macos] WARN — playwright install chromium 실패. 받는 사람 PC 가 첫 실행 시 받아야 할 수 있음."

# 4. 공용 코드 패키지 카피 (shared/ → replay-ui/ 루트로).
copy_module() {
  local src="$1"
  local name="$2"
  [[ -d "$src" ]] || { echo "공용 패키지 소스 없음: $src" >&2; exit 1; }
  local dst="$REPLAYUI_DIR/$name"
  rm -rf "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude '__pycache__' --exclude '*.pyc' "$src/" "$dst/"
  else
    cp -R "$src" "$dst"
    find "$dst" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
    find "$dst" -type f -name '*.pyc' -delete 2>/dev/null || true
  fi
  echo "[pack-macos] $name <- $src"
}
copy_module "$SHARED_DIR/recording_shared" "recording_shared"
copy_module "$SHARED_DIR/zero_touch_qa"    "zero_touch_qa"

# 5. 실행파일 / 안내 카피 + 실행 권한.
cp "$TEMPLATES_DIR/Launch-ReplayUI.command" "$REPLAYUI_DIR/"
cp "$TEMPLATES_DIR/Stop-ReplayUI.command"   "$REPLAYUI_DIR/"
cp "$TEMPLATES_DIR/README-macos.txt"         "$REPLAYUI_DIR/README.txt"
chmod +x "$REPLAYUI_DIR/Launch-ReplayUI.command" "$REPLAYUI_DIR/Stop-ReplayUI.command"

# 6. data/ 빈 디렉토리.
mkdir -p "$REPLAYUI_DIR/data/auth-profiles" \
         "$REPLAYUI_DIR/data/scenarios" \
         "$REPLAYUI_DIR/data/scripts" \
         "$REPLAYUI_DIR/data/runs"

# 7. Smoke.
echo "[pack-macos] Smoke: import 핵심 모듈"
PYTHONPATH="$REPLAYUI_DIR:$SITE_PKG" \
  "$REPLAYUI_DIR/python/bin/python3" -c \
  "import replay_service.server, recording_shared.trace_parser, recording_shared.report_export, zero_touch_qa.auth_profiles, monitor.replay_cmd, playwright"
echo "[pack-macos] Smoke OK"

echo
echo "[pack-macos] 자산 채우기 완료 — $REPLAYUI_DIR"
echo "  반출 zip 은 'zip -r replay-ui.zip replay-ui' 한 줄로 만들거나 --make-zip 옵션을 쓰세요."

# 자산 source SHA 를 stamp 로 기록 — pre-push hook 의 stale 검출 기준.
# OS 무관 일관성 위해 git rev-parse 의 tree object id 들을 LF 없이 concat 후 SHA256.
STAMP_SHA="$(cd "$REPO_ROOT" && git rev-parse \
  HEAD:playwright-allinone/shared \
  HEAD:playwright-allinone/replay-ui/replay_service \
  HEAD:playwright-allinone/replay-ui/monitor \
  HEAD:playwright-allinone/replay-ui-portable-build/templates \
  | tr -d '\n' \
  | { command -v sha256sum >/dev/null 2>&1 && sha256sum || shasum -a 256; } \
  | awk '{print $1}')"
echo -n "$STAMP_SHA" > "$REPLAYUI_DIR/.pack-stamp"
echo "[pack-macos] .pack-stamp = ${STAMP_SHA:0:12}..."

# 8. (옵션) zip 압축. POSIX 실행 권한 보존.
if [[ "$MAKE_ZIP" = "1" ]]; then
  mkdir -p "$BUILD_OUT_DIR"
  TS="$(date +%Y%m%d-%H%M%S)"
  ZIP_PATH="$BUILD_OUT_DIR/DSCORE-ReplayUI-portable-macos-arm64-$TS.zip"
  rm -f "$ZIP_PATH"
  echo "[pack-macos] Compressing replay-ui/ -> $ZIP_PATH"
  if command -v zip >/dev/null 2>&1; then
    (cd "$ALLINONE_DIR" && zip -qr "$ZIP_PATH" "replay-ui")
  elif command -v ditto >/dev/null 2>&1; then
    (cd "$ALLINONE_DIR" && ditto -ck --keepParent "replay-ui" "$ZIP_PATH")
  else
    echo "[pack-macos] ERROR — zip 또는 ditto 필요" >&2
    exit 1
  fi
  SIZE="$(du -h "$ZIP_PATH" | cut -f1)"
  if command -v shasum >/dev/null 2>&1; then
    HASH="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
  else
    HASH="$(sha256sum "$ZIP_PATH" | awk '{print $1}')"
  fi
  echo
  echo "[pack-macos] zip 산출: $ZIP_PATH"
  echo "  size : $SIZE"
  echo "  sha256: $HASH"
fi
