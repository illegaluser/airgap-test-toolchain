#!/usr/bin/env bash
# export-airgap.sh — Playwright Zero-Touch QA 폐쇄망 반출본 한 번에 생성.
#
# 두 산출:
#   1) playwright-allinone/dscore.ttc.playwright-<ts>.tar.gz
#        → Recording PC (녹화 PC) 용 Docker 이미지 tarball
#   2) monitor-runtime-<ts>.zip (저장소 루트)
#        → Monitoring PC (모니터링 PC) 용 Replay UI 설치 패키지
#
# 두 PC 는 설계상 역할·OS 가 분리되어 있다. 본 스크립트는 두 빌드를 한 명령으로
# 실행해 양쪽 산출을 한 자리에 모아 USB 등에 같이 담아두기 편하게 한다.
#
# 사용:
#   bash export-airgap.sh                    # 둘 다 생성 (기본)
#   bash export-airgap.sh --monitor-only     # monitor-runtime zip 만
#   bash export-airgap.sh --recording-only   # Recording PC tarball 만
#   bash export-airgap.sh --no-chromium      # monitor zip 에서 Chromium 제외 (작은 zip)
#   bash export-airgap.sh --reuse-cache      # monitor 빌드 wheels/chromium 캐시 재사용
#   bash export-airgap.sh --target win64     # monitor zip 을 Windows 용으로만
#   bash export-airgap.sh --target macos-arm64
#
# 요구:
#   - Recording PC tarball 부분: Docker 26+, 인터넷
#   - Monitoring PC zip 부분:    Python 3.11+, 인터넷 (pip download / playwright install)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DO_RECORDING=1
DO_MONITOR=1
MONITOR_ARGS=()

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recording-only) DO_MONITOR=0; shift ;;
    --monitor-only)   DO_RECORDING=0; shift ;;
    --no-chromium)    MONITOR_ARGS+=(--no-chromium); shift ;;
    --reuse-cache)    MONITOR_ARGS+=(--reuse-cache); shift ;;
    --target)         MONITOR_ARGS+=(--target "$2"); shift 2 ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 2 ;;
  esac
done

echo "[export-airgap] 시작 — recording=${DO_RECORDING}, monitor=${DO_MONITOR}"

if [[ "$DO_RECORDING" = "1" ]]; then
  echo
  echo "═══ [1/2] Recording PC — Docker 이미지 빌드 + tarball 산출 ═══"
  bash "$ROOT/playwright-allinone/build.sh"
fi

if [[ "$DO_MONITOR" = "1" ]]; then
  echo
  echo "═══ [2/2] Monitoring PC — monitor-runtime zip 산출 ═══"
  bash "$ROOT/playwright-allinone/monitor-build/build-monitor-runtime.sh" "${MONITOR_ARGS[@]}"
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
if [[ "$DO_MONITOR" = "1" ]]; then
  mon_zip="$(ls -1t "$ROOT/"monitor-runtime-*.zip 2>/dev/null | head -1 || true)"
  if [[ -n "$mon_zip" ]]; then
    echo "  Monitoring PC 용: $mon_zip"
    du -h "$mon_zip" | awk '{print "                    크기 " $1}'
  fi
fi
cat <<'HINT'

  USB / 외장 디스크 / 사내 공유 폴더 등으로 위 산출물을 옮긴 뒤 대상 PC 에서:

  Recording PC (Docker 호스트):
    docker load < dscore.ttc.playwright-<ts>.tar.gz
    # 이후 build.sh --redeploy 또는 동등 docker run

  Monitoring PC (Mac/Linux):
    unzip monitor-runtime-<ts>.zip && cd monitor-runtime-*
    bash install-monitor.sh --register-startup --register-task

  Monitoring PC (Windows):
    Expand-Archive monitor-runtime-<ts>.zip ; cd monitor-runtime-*
    powershell -ExecutionPolicy Bypass -File install-monitor.ps1

  설치 끝나면 http://127.0.0.1:18094 — 대상 PC 내부에서만 접속 가능.
HINT
