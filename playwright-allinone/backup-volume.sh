#!/usr/bin/env bash
# ============================================================================
# Volume backup — 운영 중인 dscore-data volume 을 폐쇄망 반입용 tarball 로 export.
#
# 보존 대상:
#   - PostgreSQL 데이터 (Dify 메타데이터, 앱 설정, 챗봇 conversation)
#   - Qdrant 벡터 (KB 임베딩 인덱스 — 누적된 프로젝트 지식)
#   - Redis 캐시
#   - Jenkins data (job 설정, build 이력, credentials)
#   - Dify plugin 데이터
#
# 절차:
#   1. supervisorctl 로 모든 서비스 quiesce (DB 일관성 보장)
#   2. busybox 컨테이너로 volume tar gzip
#   3. supervisorctl 로 서비스 재기동
#
# 사용법:
#   ./backup-volume.sh [출력_파일명]
#     기본 출력: dscore-data-YYYYMMDD-HHMMSS.tar.gz (이 폴더 내부)
#
# 폐쇄망 반입 시:
#   - image tar.gz (build.sh 산출물) 와 함께 두 파일을 모두 옮긴다.
#   - 폐쇄망에서 image load → restore-volume.sh → docker run.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONTAINER_NAME="${CONTAINER_NAME:-dscore.ttc.playwright}"
DATA_VOLUME="${DATA_VOLUME:-dscore-data}"
OUTPUT_TAR="${1:-dscore-data-$(date +%Y%m%d-%H%M%S).tar.gz}"

log()  { printf '[backup-volume] %s\n' "$*"; }
err()  { printf '[backup-volume] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 사전 검증 ────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."

if ! docker ps --format '{{.Names}}' | grep -qxF "$CONTAINER_NAME"; then
  err "컨테이너 '$CONTAINER_NAME' 이 실행 중이지 않습니다. 먼저 docker run 으로 기동."
fi

if ! docker volume ls --format '{{.Name}}' | grep -qxF "$DATA_VOLUME"; then
  err "볼륨 '$DATA_VOLUME' 이 존재하지 않습니다."
fi

# ── 1. 서비스 quiesce (supervisorctl stop all) ──────────────────────────
log "[1/3] 서비스 quiesce (supervisorctl stop all) — DB 일관성 보장"
docker exec "$CONTAINER_NAME" supervisorctl -c /etc/supervisor/supervisord.conf stop all >/dev/null 2>&1 || true

# PG 가 완전히 stop 됐는지 약간 대기 (transaction log flush)
sleep 3
log "  서비스 stop 완료"

# ── 2. tar gzip export ───────────────────────────────────────────────────
log "[2/3] 볼륨 tar gzip export → $OUTPUT_TAR"
log "  (대용량 볼륨은 수 분~수십 분 소요)"

# busybox 로 짧게 — volume mount + 호스트 PWD mount → tar
docker run --rm \
  -v "$DATA_VOLUME:/src:ro" \
  -v "$SCRIPT_DIR:/dst" \
  busybox \
  sh -c "cd /src && tar czf /dst/$OUTPUT_TAR ."

if [ ! -f "$OUTPUT_TAR" ]; then
  # 서비스 복구 시도 후 에러
  docker exec "$CONTAINER_NAME" supervisorctl -c /etc/supervisor/supervisord.conf start all >/dev/null 2>&1 || true
  err "tarball 생성 실패: $OUTPUT_TAR"
fi

TAR_SIZE=$(du -h "$OUTPUT_TAR" | cut -f1)
log "  완료: $OUTPUT_TAR ($TAR_SIZE)"

# ── 3. 서비스 재기동 ─────────────────────────────────────────────────────
log "[3/3] 서비스 재기동 (supervisorctl start all)"
docker exec "$CONTAINER_NAME" supervisorctl -c /etc/supervisor/supervisord.conf start all >/dev/null 2>&1 || \
  log "  ⚠ supervisorctl start 실패 — 컨테이너 재기동 권장 (docker restart $CONTAINER_NAME)"

# 재기동 후 짧은 health check (Dify api readiness)
log "  Dify api readiness 확인 (최대 60s)"
_w=0
until curl -sf --max-time 3 -o /dev/null "http://localhost:18081/install"; do
  sleep 3; _w=$((_w + 3))
  [ "$_w" -ge 60 ] && { log "  ⚠ 60s 내 응답 없음 — docker logs $CONTAINER_NAME 로 점검"; break; }
done
log "  서비스 재기동 완료 (${_w}s)"

log ""
log "============================================================"
log "백업 완료: $SCRIPT_DIR/$OUTPUT_TAR ($TAR_SIZE)"
log ""
log "폐쇄망 반입 절차:"
log "  1. (이 머신에서) image tar.gz + 본 backup tarball 두 파일을 폐쇄망 머신으로 옮김"
log "  2. (폐쇄망에서) docker load -i dscore.ttc.playwright-*.tar.gz"
log "  3. (폐쇄망에서) ./restore-volume.sh $OUTPUT_TAR"
log "  4. (폐쇄망에서) docker run -d ... dscore.ttc.playwright:latest"
log "============================================================"
