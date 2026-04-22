#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 컨테이너 엔트리포인트
#
# 동작:
#   - 포트 28080/28081/50002/29000 적용
#   - SonarQube 수동 start (PG 준비 후)
#   - 파이프라인 1~3 용 Jenkins job seed 는 provision.sh 에서 처리
# ============================================================================
set -euo pipefail

DATA=/data
SEED=/opt/seed
LOG_PREFIX="[entrypoint]"

log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
err()  { printf '%s ERROR: %s\n' "$LOG_PREFIX" "$*" >&2; }
warn() { printf '%s WARN:  %s\n' "$LOG_PREFIX" "$*" >&2; }

# 포트 (Dockerfile ENV 에서 기본값 제공. docker run -e 로 override 가능)
JENKINS_PORT="${JENKINS_PORT:-28080}"
JENKINS_AGENT_PORT="${JENKINS_AGENT_PORT:-50002}"
DIFY_GATEWAY_PORT="${DIFY_GATEWAY_PORT:-28081}"
SONARQUBE_PORT="${SONARQUBE_PORT:-29000}"

# SonarQube ES `path.data` 경로. 기본값은 /data 볼륨 (Linux/WSL2 에서 안전).
# macOS Docker Desktop 처럼 호스트 bind 가 grpcfuse 로 매핑되고 호스트 디스크
# 사용량이 ES flood_stage watermark (95%) 에 걸릴 수 있는 환경에서는
# docker-compose.mac.yaml 에서 /var/lib/sonarqube_data (overlay) 로 override.
# overlay 는 컨테이너 lifecycle 에 묶여 휘발 — SonarQube metadata 는 PG 에 있어
# ES 인덱스만 재생성하면 복구. 영속 필요 시 volume mount 로 별도 관리.
SONAR_DATA_HOST="${SONAR_DATA_HOST:-$DATA/sonarqube/data}"

log "container time: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ────────────────────────────────────────────────────────────────────────────
# 1. /data 최초 seed
# ────────────────────────────────────────────────────────────────────────────
if [ ! -f "$DATA/.initialized" ]; then
    log "최초 seed: /opt/seed → /data"
    mkdir -p \
      "$DATA/pg" "$DATA/redis" "$DATA/qdrant" \
      "$DATA/jenkins/plugins" \
      "$DATA/dify/storage" "$DATA/dify/plugins/cwd" \
      "$DATA/sonarqube/logs" "$DATA/sonarqube/extensions" "$DATA/sonarqube/temp" \
      "$SONAR_DATA_HOST" \
      "$DATA/logs"

    [ -d "$SEED/jenkins-plugins" ] && cp -a "$SEED/jenkins-plugins/." "$DATA/jenkins/plugins/"
    [ -d "$SEED/jenkins-home" ]    && cp -an "$SEED/jenkins-home/." "$DATA/jenkins/" || true

    if [ -d "$SEED/pg" ] && [ -z "$(ls -A "$DATA/pg" 2>/dev/null || true)" ]; then
        cp -a "$SEED/pg/." "$DATA/pg/"
    fi

    if [ -d "$SEED/dify-plugins" ]; then
        mkdir -p "$DATA/dify/plugins/packages"
        cp -a "$SEED/dify-plugins/." "$DATA/dify/plugins/packages/"
    fi

    chown -R postgres:postgres "$DATA/pg"            || true
    chown -R jenkins:jenkins   "$DATA/jenkins"       || true
    chown -R redis:redis       "$DATA/redis"         || true
    chown -R sonar:sonar       "$DATA/sonarqube"     || true
    chown -R sonar:sonar       "$SONAR_DATA_HOST"    || true

    touch "$DATA/.initialized"
    log "seed 완료."
else
    log "기존 볼륨 감지 — seed 건너뜀."
    # SONAR_DATA_HOST 가 기본 /data 경로 밖이면 overlay 라 재기동 시 휘발.
    # 없으면 새로 만들고 소유권 부여 (Sonar ES 인덱스 rebuild 됨).
    mkdir -p "$SONAR_DATA_HOST"
    chown -R sonar:sonar "$SONAR_DATA_HOST" 2>/dev/null || true
fi

# 심볼릭은 매 기동마다 재적용 (이미지 내 /opt/sonarqube 는 실제 디렉토리).
# data 는 SONAR_DATA_HOST (env override 가능), 나머지는 /data/sonarqube/ 하위.
rm -rf /opt/sonarqube/data
ln -sfn "$SONAR_DATA_HOST" /opt/sonarqube/data
chown -h sonar:sonar /opt/sonarqube/data || true
for sub in logs extensions temp; do
    rm -rf "/opt/sonarqube/$sub"
    ln -sfn "$DATA/sonarqube/$sub" "/opt/sonarqube/$sub"
    chown -h sonar:sonar "/opt/sonarqube/$sub" || true
done

# ────────────────────────────────────────────────────────────────────────────
# 1-b. Jenkins job 경로 매핑 + 공용 작업 디렉터리 (Phase 0 런타임 버그 수정)
#   - Jenkinsfile 이 /var/jenkins_home/scripts, /var/jenkins_home/tools/node/bin/node,
#     /var/knowledges/** 를 가정하지만 실제 자산은 /opt/pipeline-scripts,
#     /opt/eval_runner, /usr/local/bin/node 에 있음. 심볼릭 + mkdir 로 매핑.
# ────────────────────────────────────────────────────────────────────────────
mkdir -p /var/jenkins_home/scripts /var/jenkins_home/tools/node/bin
for f in /opt/pipeline-scripts/*.py; do
    [ -f "$f" ] && ln -sfn "$f" "/var/jenkins_home/scripts/$(basename "$f")"
done
ln -sfn /opt/eval_runner /var/jenkins_home/scripts/eval_runner
ln -sfn /usr/local/bin/node /var/jenkins_home/tools/node/bin/node

mkdir -p \
    /var/knowledges/codes \
    /var/knowledges/docs/org \
    /var/knowledges/docs/result \
    /var/knowledges/eval/data \
    /var/knowledges/eval/reports \
    /var/knowledges/state
chown -R jenkins:jenkins /var/knowledges /var/jenkins_home/scripts /var/jenkins_home/tools 2>/dev/null || true

# 평가 fixture 를 런타임 기본 위치로 1회 부트스트랩.
# 사용자가 나중에 golden.csv 를 업로드/교체하면 기존 파일을 보존해야 하므로
# 대상 파일이 없을 때만 복사한다.
if [ -f /opt/eval_runner/tests/fixtures/tiny_dataset.csv ] && [ ! -f /var/knowledges/eval/data/golden.csv ]; then
    cp /opt/eval_runner/tests/fixtures/tiny_dataset.csv /var/knowledges/eval/data/golden.csv
    chown jenkins:jenkins /var/knowledges/eval/data/golden.csv 2>/dev/null || true
    log "기본 평가 데이터셋 부트스트랩 완료: /var/knowledges/eval/data/golden.csv"
fi
log "Jenkins scripts 경로 매핑 + /var/knowledges 부트스트랩 완료"

# ────────────────────────────────────────────────────────────────────────────
# 2. supervisord 백그라운드 기동
# ────────────────────────────────────────────────────────────────────────────
log "supervisord 기동 (Jenkins:$JENKINS_PORT, Dify:$DIFY_GATEWAY_PORT, Sonar:$SONARQUBE_PORT)..."
mkdir -p "$DATA/logs"
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf &
SUPERVISOR_PID=$!

_term() {
    log "shutdown signal — supervisord 종료 중..."
    kill -TERM "$SUPERVISOR_PID" 2>/dev/null || true
    wait "$SUPERVISOR_PID" 2>/dev/null || true
    exit 0
}
trap _term SIGTERM SIGINT

# ────────────────────────────────────────────────────────────────────────────
# 3. PG 준비 후 SonarQube 수동 start
# ────────────────────────────────────────────────────────────────────────────
log "PostgreSQL 헬스 대기..."
_w=0
until PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d sonar -c 'SELECT 1' >/dev/null 2>&1; do
    sleep 2; _w=$((_w + 2))
    if [ $_w -ge 120 ]; then
        warn "PG 2분 내 미준비. SonarQube start 시도는 건너뜀."
        break
    fi
done
if [ $_w -lt 120 ]; then
    log "SonarQube start..."
    supervisorctl -c /etc/supervisor/supervisord.conf start sonarqube >/dev/null 2>&1 || \
        warn "supervisorctl start sonarqube 실패 — 수동 확인: supervisorctl status"

    # dify-plugin-daemon 도 PG 준비 후 start (race 방지).
    # supervisord autostart=true 로 두면 PG 기동 0~3초 사이에 3회 재시도를 모두 써서
    # FATAL 상태로 포기 → dify-api 가 plugin-daemon 헬스 대기에서 무한 블록.
    log "dify-plugin-daemon start..."
    supervisorctl -c /etc/supervisor/supervisord.conf start dify-plugin-daemon >/dev/null 2>&1 || \
        warn "supervisorctl start dify-plugin-daemon 실패 — 수동 확인: supervisorctl status"
fi

# ────────────────────────────────────────────────────────────────────────────
# 4. Dify plugin-daemon 헬스 대기 후 dify-api 수동 start (race 방지)
# ────────────────────────────────────────────────────────────────────────────
log "dify-plugin-daemon 헬스 대기..."
_w=0
until curl -sf --max-time 2 -o /dev/null http://127.0.0.1:5002/health/check; do
    sleep 2; _w=$((_w + 2))
    [ $_w -ge 120 ] && { warn "plugin-daemon 2분 내 미준비. dify-api 기동 시도."; break; }
done
log "  dify-plugin-daemon ready (${_w}s) — dify-api start"
sleep 3
supervisorctl -c /etc/supervisor/supervisord.conf start dify-api >/dev/null 2>&1 || \
    warn "supervisorctl start dify-api 실패"

# ────────────────────────────────────────────────────────────────────────────
# 5. 최초 앱 프로비저닝 (볼륨 최초 생성 후 1회만)
# ────────────────────────────────────────────────────────────────────────────
if [ ! -f "$DATA/.app_provisioned" ]; then
    log "서비스 헬스 대기 (dify/jenkins/sonarqube 전부 HTTP 200, 최대 10분)..."
    _waited=0; _limit=600
    until curl -sf --max-time 3 -o /dev/null http://127.0.0.1:5001/console/api/setup \
       && curl -sf --max-time 3 -o /dev/null http://127.0.0.1:${DIFY_GATEWAY_PORT}/install \
       && curl -sf --max-time 3 -o /dev/null -u admin:password http://127.0.0.1:${JENKINS_PORT}/api/json \
       && curl -sf --max-time 3 -o /dev/null http://127.0.0.1:9000/api/system/status; do
        sleep 5; _waited=$((_waited + 5))
        if [ $_waited -ge $_limit ]; then
            err "일부 서비스가 10분 내 준비되지 않음. /data/logs 확인."
            break
        fi
    done
    log "헬스 대기 완료 (${_waited}s)."

    log "앱 프로비저닝 시작 (provision.sh)"
    export DIFY_URL="http://127.0.0.1:${DIFY_GATEWAY_PORT}"
    export JENKINS_URL="http://127.0.0.1:${JENKINS_PORT}"
    export SONAR_URL="http://127.0.0.1:9000"
    export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
    export OFFLINE_DIFY_PLUGIN_DIR="$SEED/dify-plugins"
    export OFFLINE_JENKINSFILE_DIR="/opt/jenkinsfiles"

    if bash /opt/provision.sh; then
        touch "$DATA/.app_provisioned"
        log "앱 프로비저닝 완료."
    else
        warn "앱 프로비저닝 실패. 컨테이너는 계속 실행됩니다."
        warn "재시도: docker exec <container> bash /opt/provision.sh"
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 6. foreground wait
# ────────────────────────────────────────────────────────────────────────────
log "준비 완료. supervisord wait..."
log "  Jenkins   : http://localhost:${JENKINS_PORT}   (admin / password)"
log "  Dify      : http://localhost:${DIFY_GATEWAY_PORT}"
log "  SonarQube : http://localhost:${SONARQUBE_PORT}   (admin / admin)"
log "  Ollama    : ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}  (호스트)"
wait "$SUPERVISOR_PID"
