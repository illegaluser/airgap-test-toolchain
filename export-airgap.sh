#!/usr/bin/env bash
# export-airgap.sh — Playwright Zero-Touch QA 폐쇄망 반출본 한 번에 생성.
#
# 두 산출:
#   1) playwright-allinone/dscore.ttc.playwright-<ts>.tar.gz
#        → Recording PC (녹화 PC) 용 Docker 이미지 tarball
#   2) playwright-allinone/replay-ui-portable-build/build-out/
#         DSCORE-ReplayUI-portable-{win64|macos-arm64}-<ts>.zip
#        → Monitoring PC (모니터링 PC) 용 휴대용 Replay UI 패키지
#
# 두 PC 는 설계상 역할·OS 가 분리되어 있다. 본 스크립트는 두 빌드를 한 명령으로
# 실행해 양쪽 산출을 한 자리에 모아 USB 등에 같이 담아두기 편하게 한다.
#
# OS 분기 정책 — 휴대용 zip 산출:
#   - Mac (macOS arm64):  pack-macos.sh 항상 호출. pack-windows.ps1 은 pwsh 7
#                         설치된 경우에만 호출 (Git Bash 의 powershell.exe 와는
#                         다름 — pwsh 미설치면 win64 zip 산출 skip).
#   - Windows (Git Bash / WSL2 Ubuntu): powershell.exe 호출 가능 → pack-windows.ps1
#                         로 win64 zip 산출. pack-macos.sh 는 macOS arm64 native
#                         API (python-build-standalone) 의존이라 호출 불가 →
#                         macos-arm64 zip 산출 skip.
#   - Linux: 휴대용 빌드 머신으로 사용 불가 (Mac/Windows 의 OS-native 도구 필요).
#            에러 메시지 후 exit.
#
# 사용:
#   bash export-airgap.sh                    # 둘 다 생성 (기본)
#   bash export-airgap.sh --recording-only   # Recording PC tarball 만
#   bash export-airgap.sh --replay-only      # 휴대용 Replay UI 만
#   bash export-airgap.sh --target win64
#   bash export-airgap.sh --target macos-arm64
#
# 요구:
#   - Recording PC tarball 부분: Docker 26+, 인터넷
#   - 휴대용 Replay UI 부분:    Python 3.11+, 인터넷 (pip download / playwright install)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DO_RECORDING=1
DO_REPLAY=1
TARGET="all"
HOST_OS="$(uname -s)"

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recording-only) DO_REPLAY=0; shift ;;
    --replay-only)    DO_RECORDING=0; shift ;;
    --target)         TARGET="$2"; shift 2 ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 2 ;;
  esac
done

case "$TARGET" in
  win64|macos-arm64|all) ;;
  *) echo "잘못된 --target: $TARGET (win64|macos-arm64|all)" >&2; exit 2 ;;
esac

echo "[export-airgap] 시작 — recording=${DO_RECORDING}, replay=${DO_REPLAY}, target=${TARGET}, host=${HOST_OS}"

if [[ "$DO_RECORDING" = "1" ]]; then
  echo
  echo "═══ [1/2] Recording PC — Docker 이미지 빌드 + tarball 산출 ═══"
  # --no-redeploy: export 흐름에선 현재 머신 서비스 재시작 의도 없음 (build.sh 의 default 가
  # 재기동인 만큼 명시 OFF).
  bash "$ROOT/playwright-allinone/build.sh" --tarball --no-redeploy
fi

# 휴대용 Replay UI 빌드 — OS 분기 정책 (헤더 코멘트 참조).
build_replay_win64() {
  echo
  echo "═══ Replay UI (win64) — 캐시 채움 + zip 산출 ═══"
  bash "$ROOT/playwright-allinone/replay-ui-portable-build/build-cache.sh" --target win64
  local pwsh_cmd=""
  if command -v powershell.exe >/dev/null 2>&1; then
    pwsh_cmd="powershell.exe"
  elif command -v pwsh >/dev/null 2>&1; then
    pwsh_cmd="pwsh"
  else
    echo "[export-airgap] WARN — pwsh/powershell 미발견. win64 zip 산출 skip." >&2
    return 0
  fi
  "$pwsh_cmd" -NoProfile -ExecutionPolicy Bypass \
    -File "$ROOT/playwright-allinone/replay-ui-portable-build/pack-windows.ps1" \
    -MakeZip
}

build_replay_macos_arm64() {
  echo
  echo "═══ Replay UI (macos-arm64) — 캐시 채움 + zip 산출 ═══"
  bash "$ROOT/playwright-allinone/replay-ui-portable-build/build-cache.sh" --target macos-arm64
  bash "$ROOT/playwright-allinone/replay-ui-portable-build/pack-macos.sh" --make-zip
}

if [[ "$DO_REPLAY" = "1" ]]; then
  case "$HOST_OS" in
    Darwin)
      if [[ "$TARGET" = "all" || "$TARGET" = "macos-arm64" ]]; then
        build_replay_macos_arm64
      fi
      if [[ "$TARGET" = "all" || "$TARGET" = "win64" ]]; then
        build_replay_win64   # pwsh 없으면 함수 내부에서 skip
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*|Linux)
      # Windows Git Bash 또는 WSL2 — pack-macos.sh 호출 불가.
      if [[ "$HOST_OS" = "Linux" ]] && ! grep -qi microsoft /proc/version 2>/dev/null; then
        echo "[export-airgap] ERROR — Linux 네이티브에서는 휴대용 zip 산출 불가." >&2
        echo "  pack-macos.sh 는 macOS arm64 전용, pack-windows.ps1 은 PowerShell 전용입니다." >&2
        echo "  Mac 또는 Windows 빌드 머신 (Git Bash / WSL2) 에서 실행하세요." >&2
        exit 1
      fi
      if [[ "$TARGET" = "macos-arm64" ]]; then
        echo "[export-airgap] ERROR — Windows 빌드 머신에서는 macos-arm64 zip 산출 불가." >&2
        echo "  pack-macos.sh 는 macOS arm64 native (python-build-standalone) 의존입니다." >&2
        exit 1
      fi
      if [[ "$TARGET" = "all" || "$TARGET" = "win64" ]]; then
        build_replay_win64
      fi
      if [[ "$TARGET" = "all" ]]; then
        echo "[export-airgap] INFO — macos-arm64 zip 은 별도 Mac 빌드 머신에서 산출하세요."
      fi
      ;;
    *)
      echo "[export-airgap] ERROR — 알 수 없는 호스트 OS: $HOST_OS" >&2
      exit 1
      ;;
  esac
fi

# 산출물 위치 안내.
echo
echo "═══ 산출 요약 ═══"
if [[ "$DO_RECORDING" = "1" ]]; then
  rec_tar="$(ls -1t "$ROOT/playwright-allinone/"dscore.ttc.playwright-*.tar.gz 2>/dev/null | head -1 || true)"
  if [[ -n "$rec_tar" ]]; then
    echo "  Recording PC 용: $rec_tar"
    du -h "$rec_tar" | awk '{print "                   크기 " $1}'
  fi
fi
if [[ "$DO_REPLAY" = "1" ]]; then
  out_dir="$ROOT/playwright-allinone/replay-ui-portable-build/build-out"
  win_zip="$(ls -1t "$out_dir"/DSCORE-ReplayUI-portable-win64-*.zip 2>/dev/null | head -1 || true)"
  mac_zip="$(ls -1t "$out_dir"/DSCORE-ReplayUI-portable-macos-arm64-*.zip 2>/dev/null | head -1 || true)"
  if [[ -n "$win_zip" ]]; then
    echo "  Monitoring PC (Windows): $win_zip"
    du -h "$win_zip" | awk '{print "                          크기 " $1}'
  fi
  if [[ -n "$mac_zip" ]]; then
    echo "  Monitoring PC (macOS arm64): $mac_zip"
    du -h "$mac_zip" | awk '{print "                              크기 " $1}'
  fi
fi
cat <<'HINT'

  USB / 외장 디스크 / 사내 공유 폴더 등으로 위 산출물을 옮긴 뒤 대상 PC 에서:

  Recording PC (Docker 호스트):
    docker load < dscore.ttc.playwright-<ts>.tar.gz
    # 이후 ./build.sh (default 가 빌드 + 재기동 + reprovision + agent) 또는 동등 docker run

  Monitoring PC (Windows):
    DSCORE-ReplayUI-portable-win64-<ts>.zip 을 풀고 Launch-ReplayUI.bat 더블클릭.

  Monitoring PC (macOS arm64):
    DSCORE-ReplayUI-portable-macos-arm64-<ts>.zip 을 풀고 Launch-ReplayUI.command 더블클릭.
    첫 실행은 Finder 에서 right-click → 열기 (Gatekeeper 1회 우회).

  접속: http://127.0.0.1:18099 — 대상 PC 내부에서만.
HINT
