#!/usr/bin/env bash
# ============================================================================
# Volume restore — backup-volume.sh 가 만든 tarball 을 폐쇄망 머신의 dscore-data
# volume 으로 복구.
#
# 사용 시점:
#   - 폐쇄망 머신에 image tar.gz 를 docker load 한 직후
#   - docker run 으로 컨테이너 기동하기 전
#
# 사용법:
#   ./restore-volume.sh <tarball_경로>
#   ./restore-volume.sh --fresh <tarball_경로>     # 기존 dscore-data 가 있어도 wipe 후 복구
#
# 사전 조건:
#   - dscore.ttc.playwright 컨테이너가 실행 중이지 않아야 함
#   - 기존 dscore-data 볼륨이 비어 있거나, --fresh 플래그로 명시 wipe
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONTAINER_NAME="${CONTAINER_NAME:-dscore.ttc.playwright}"
DATA_VOLUME="${DATA_VOLUME:-dscore-data}"

FRESH=false
TARBALL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh) FRESH=true; shift ;;
    -h|--help)
      cat <<'USAGE'
사용법: ./restore-volume.sh [--fresh] <tarball_경로>

  --fresh   기존 dscore-data 볼륨이 있어도 wipe 후 복구 (기존 데이터 폐기)
            없을 때: 기존 볼륨이 비어 있으면 그대로 복구, 데이터가 있으면 거절

예시:
  ./restore-volume.sh dscore-data-20260101-120000.tar.gz
  ./restore-volume.sh --fresh dscore-data-20260101-120000.tar.gz
USAGE
      exit 0
      ;;
    *) TARBALL="$1"; shift ;;
  esac
done

log()  { printf '[restore-volume] %s\n' "$*"; }
err()  { printf '[restore-volume] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 사전 검증 ────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."
[ -z "$TARBALL" ] && err "tarball 경로 인자 필수. 사용법: ./restore-volume.sh [--fresh] <tarball>"
[ -f "$TARBALL" ] || err "tarball 파일 없음: $TARBALL"

# 컨테이너가 실행 중이면 거절 (volume 사용 중일 때 덮어쓰기 위험)
if docker ps --format '{{.Names}}' | grep -qxF "$CONTAINER_NAME"; then
  err "'$CONTAINER_NAME' 이 실행 중입니다. 먼저 docker stop / rm -f 후 재시도."
fi

# 볼륨 상태 확인
VOL_EXISTS=false
docker volume ls --format '{{.Name}}' | grep -qxF "$DATA_VOLUME" && VOL_EXISTS=true

if [ "$VOL_EXISTS" = "true" ]; then
  # 비어 있는지 확인
  VOL_FILE_COUNT=$(docker run --rm -v "$DATA_VOLUME:/src:ro" busybox sh -c 'find /src -mindepth 1 -maxdepth 1 | wc -l')
  if [ "$VOL_FILE_COUNT" -gt 0 ] && [ "$FRESH" != "true" ]; then
    err "볼륨 '$DATA_VOLUME' 이 비어 있지 않습니다 ($VOL_FILE_COUNT 개 항목). --fresh 플래그로 덮어쓰거나 docker volume rm 으로 먼저 제거."
  fi
  if [ "$FRESH" = "true" ] && [ "$VOL_FILE_COUNT" -gt 0 ]; then
    log "[--fresh] 기존 볼륨 wipe — $VOL_FILE_COUNT 항목 삭제"
    docker run --rm -v "$DATA_VOLUME:/dst" busybox sh -c 'rm -rf /dst/*  /dst/..?* /dst/.[!.]* 2>/dev/null || true'
  fi
else
  log "볼륨 '$DATA_VOLUME' 이 존재하지 않음 — 신규 생성"
  docker volume create "$DATA_VOLUME" >/dev/null
fi

# ── tarball 복구 ─────────────────────────────────────────────────────────
TAR_SIZE=$(du -h "$TARBALL" | cut -f1)
log "복구 중: $TARBALL ($TAR_SIZE) → 볼륨 '$DATA_VOLUME'"
log "  (대용량 tarball 은 수 분~수십 분 소요)"

# 절대 경로 준비
TAR_ABS=$(cd "$(dirname "$TARBALL")" && pwd)/$(basename "$TARBALL")

docker run --rm \
  -v "$DATA_VOLUME:/dst" \
  -v "$(dirname "$TAR_ABS"):/src:ro" \
  busybox \
  sh -c "cd /dst && tar xzf /src/$(basename "$TAR_ABS")"

# 복구 검증
RESTORED_COUNT=$(docker run --rm -v "$DATA_VOLUME:/src:ro" busybox sh -c 'find /src -mindepth 1 -maxdepth 1 | wc -l')
log "  복구 완료 — 볼륨 최상위 항목: $RESTORED_COUNT"

# 주요 디렉토리 sanity check — entrypoint.sh 가 만드는 실제 구조 기준
# (pg=postgres data dir, jenkins=JENKINS_HOME, dify=storage/plugins, logs=supervisor/service log)
for d in pg qdrant redis jenkins dify; do
  if docker run --rm -v "$DATA_VOLUME:/src:ro" busybox test -d "/src/$d"; then
    log "  ✓ /data/$d 존재"
  else
    log "  ⚠ /data/$d 없음 — 백업 시점에 해당 서비스가 활성화 안 됐을 수 있음"
  fi
done

# .app_provisioned 마커 — provision skip 여부 결정
if docker run --rm -v "$DATA_VOLUME:/src:ro" busybox test -f "/src/.app_provisioned"; then
  log "  ✓ /data/.app_provisioned 존재 — entrypoint 가 provision 단계를 skip"
else
  log "  ⚠ /data/.app_provisioned 없음 — 다음 부팅 시 provision 이 다시 실행됨"
fi

log ""
log "============================================================"
log "복구 완료. 다음 단계:"
log ""
log "  docker run -d --name $CONTAINER_NAME \\"
log "    -p 18080:18080 -p 18081:18081 -p 50001:50001 \\"
log "    -v $DATA_VOLUME:/data \\"
log "    --add-host host.docker.internal:host-gateway \\"
log "    -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \\"
log "    -e OLLAMA_MODEL=qwen3.5:9b \\"
log "    -e AGENT_NAME=mac-ui-tester   # WSL2 면 wsl-ui-tester \\"
log "    --restart unless-stopped \\"
log "    dscore.ttc.playwright:latest"
log ""
log "  ※ entrypoint 가 /data/.app_provisioned 마커 발견 → provision 단계 skip,"
log "     기존 KB / Jenkins job / 챗봇 상태 그대로 복구 시작."
log "============================================================"
