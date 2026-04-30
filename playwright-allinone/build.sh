#!/usr/bin/env bash
# ============================================================================
# Zero-Touch QA All-in-One — 호스트 하이브리드 이미지 빌드 스크립트 (온라인 머신)
#
# 본 스크립트는 호스트 Ollama + 호스트 agent 하이브리드 이미지를 만든다.
# 설계: Mac (Metal GPU passthrough 부재) 과 Windows (headed Chromium 이 Windows
# 네이티브 화면에서만 뜸) 공통 요구 해결. 컨테이너는 Jenkins controller + Dify 만.
#
# 원본 All-in-One 대비 차이:
#   - 컨테이너 내부 Ollama 제거 (바이너리·모델 seed·supervisord 프로그램 없음)
#   - 컨테이너 내부 Jenkins agent 제거 (호스트에서 JNLP 연결)
#   - 사전 `ollama pull` 단계 없음 → 빌드 빠름 (4GB 모델 다운로드 스킵)
#   - 결과 이미지 5-7GB (원본 All-in-One 10-12GB 대비 4-5GB 절감)
#
# 동작:
#   1) Jenkins 플러그인 hpi 다운로드 (jenkins-plugin-manager.jar 로 의존성 포함)
#   2) Dify 플러그인 .difypkg 다운로드 (langgenius/ollama)
#   3) docker buildx build 로 단일 이미지 제작
#   4) docker save | gzip 으로 배포용 tar.gz 산출
#
# 출력: dscore.ttc.playwright-<timestamp>.tar.gz (이 폴더 내부)
#
# 요구:
#   - Docker 26+ (buildx 활성), 디스크 20GB+ 여유
#   - 온라인 (Docker Hub + PyPI + github.com + marketplace.dify.ai + updates.jenkins.io 접근)
#   - 빌드 소요: 10-30 분 (ollama pull 단계 없어 원본보다 크게 단축)
#
# 런타임 전제:
#   - 호스트 (Mac 또는 Windows) 에 Ollama 가 설치·기동되어 있어야 함
#   - 호스트에 JDK 21 + Python 3.11+ 설치 필요 (agent 용)
#   - docker run 에 `--add-host host.docker.internal:host-gateway` 필수
#   - 자세한 가이드: README §4 (Mac), §5 (Windows)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 빌드 컨텍스트 = 이 폴더 자체 (자체 완결). 폴더만 압축해서 다른 머신으로
# 옮겨도 바로 빌드 가능하도록 설계.
BUILD_CTX="$SCRIPT_DIR"
cd "$SCRIPT_DIR"

# ── 플래그 파싱 ────────────────────────────────────────────────────────────
# --redeploy    : 빌드 직후 같은 호스트에서 컨테이너 재기동 + agent 재연결까지 수행
# --fresh       : redeploy 시 dscore-data 볼륨까지 삭제 (프로비저닝 재수행, 데이터 폐기)
# --reprovision : redeploy 시 .app_provisioned 마커만 wipe — provision 재실행하되 데이터(KB·Jenkins 이력) 보존
# --no-agent    : redeploy 시 agent 재연결 스킵 (컨테이너만 기동)
REDEPLOY=false
FRESH_VOLUME=false
REPROVISION=false
SKIP_AGENT=false
while [ $# -gt 0 ]; do
  case "$1" in
    --redeploy)    REDEPLOY=true; shift ;;
    --fresh)       FRESH_VOLUME=true; shift ;;
    --reprovision) REPROVISION=true; shift ;;
    --no-agent)    SKIP_AGENT=true; shift ;;
    -h|--help)
      cat <<'USAGE'
사용법: ./build.sh [옵션]

  --redeploy     빌드 후 같은 호스트에서 컨테이너 재기동 + agent 재연결까지 수행
                 (기존 dscore.ttc.playwright 컨테이너가 있으면 rm -f, 기존 agent.jar
                  프로세스는 agent-setup 이 정리. dscore-data 볼륨은 유지)
  --fresh        --redeploy 와 함께 — dscore-data 볼륨도 삭제해 제로베이스 기동 (데이터 폐기)
  --reprovision  --redeploy 와 함께 — .app_provisioned 마커만 wipe → provision 재실행.
                 데이터(KB 임베딩·Jenkins 이력·챗봇 conversation)는 보존하면서 chatflow YAML /
                 Jenkins job 정의 / Dify provider 등록 같은 이미지 baked-in 정의를 새 이미지 기준으로 재생성.
                 --fresh 와 함께 쓰면 --fresh 가 우선 (전체 wipe).
  --no-agent     --redeploy 와 함께 — 컨테이너만 기동, agent 재연결은 스킵
  -h, --help     이 도움말

주요 env:
  IMAGE_TAG                dscore.ttc.playwright:latest (기본)
  TARGET_PLATFORM          uname -m 자동 감지 (Mac arm64 → linux/arm64, 그 외 → linux/amd64)
  OLLAMA_MODEL             gemma4:26b (Dify provider 에 등록될 모델 id; Sprint 5 §10.2 기본 모델)
  OUTPUT_TAR               dscore.ttc.playwright-<ts>.tar.gz
  FORCE_PLUGIN_DOWNLOAD    false (기본). true 면 jenkins-plugins/ dify-plugins/ 에
                           파일이 있어도 재다운로드 (플러그인 버전 갱신 시만 사용).
                           기본은 기존 파일 재사용 — airgap 환경에서 네트워크 없이 빌드 가능.

예시:
  ./build.sh                              # 빌드만 (tar.gz 산출)
  ./build.sh --redeploy                   # 빌드 + 기존 볼륨 재사용 재기동 + agent
  ./build.sh --redeploy --fresh           # 빌드 + 볼륨 초기화 + agent (데이터 폐기)
  ./build.sh --redeploy --reprovision     # 빌드 + 데이터 보존 + chatflow/Jenkins job 재생성 + agent
  ./build.sh --redeploy --no-agent        # 빌드 + 컨테이너만 재기동
  FORCE_PLUGIN_DOWNLOAD=true ./build.sh   # 플러그인 강제 재다운로드
USAGE
      exit 0
      ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

# ── 설정값 ────────────────────────────────────────────────────────────────
IMAGE_TAG="${IMAGE_TAG:-dscore.ttc.playwright:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-dscore.ttc.playwright}"
DATA_VOLUME="${DATA_VOLUME:-dscore-data}"
JENKINS_BASE_IMAGE="${JENKINS_BASE_IMAGE:-jenkins/jenkins:2.555.1-lts-jdk21}"
DIFY_API_BASE_IMAGE="${DIFY_API_BASE_IMAGE:-langgenius/dify-api:1.13.3}"
DIFY_WEB_BASE_IMAGE="${DIFY_WEB_BASE_IMAGE:-langgenius/dify-web:1.13.3}"
DIFY_PLUGIN_BASE_IMAGE="${DIFY_PLUGIN_BASE_IMAGE:-langgenius/dify-plugin-daemon:0.5.3-local}"

# TARGET_PLATFORM: 호스트 아키텍처를 자동 감지하되, env 로 override 가능.
# "빌드 머신 OS/아키텍처 = 런타임 머신 OS/아키텍처" 가 기본 가정 (qemu 에뮬레이션 회피).
#   - Apple Silicon Mac  → linux/arm64  (네이티브)
#   - Intel Mac / Windows (WSL2/Docker Desktop) / Linux x86 → linux/amd64
#   - qemu 크로스 빌드는 playwright chromium 설치가 silent-fail 하는 등 결함이 잦음
#   - 다른 아키의 서버로 배포하려면 TARGET_PLATFORM=linux/<amd64|arm64> 로 명시 override
if [ -z "${TARGET_PLATFORM:-}" ]; then
  case "$(uname -m)" in
    arm64|aarch64) TARGET_PLATFORM="linux/arm64" ;;
    x86_64|amd64)  TARGET_PLATFORM="linux/amd64" ;;
    *)             TARGET_PLATFORM="linux/amd64" ;;  # fallback
  esac
fi

# OLLAMA_MODEL: 이미지에 사전 pull 되지 않음 (이 이미지는 호스트 Ollama 사용).
# 이 값은 docker buildx 가 Dockerfile ARG 로 받아두긴 하지만 실질적 효과는 없음.
# 실제 런타임 모델 지정은 docker run 의 `-e OLLAMA_MODEL=...` 로 Dify provider 에 등록됨.
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:26b}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-bona/bge-m3-korean:latest}"
OUTPUT_TAR="${OUTPUT_TAR:-dscore.ttc.playwright-$(date +%Y%m%d-%H%M%S).tar.gz}"

JENKINS_PLUGINS=(
  workflow-aggregator
  file-parameters
  htmlpublisher
  junit
  plain-credentials
  sidebar-link
)
# Jenkins base 는 최신 LTS exact tag 로 고정한다.
# 플러그인 매니저의 --jenkins-version 도 같은 이미지에서 직접 추출해 맞춘다.
JENKINS_VERSION_OVERRIDE="${JENKINS_VERSION:-}"
JENKINS_PLUGIN_MANAGER_URL="https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.13.2/jenkins-plugin-manager-2.13.2.jar"

DIFY_PLUGIN_MARKETPLACE="https://marketplace.dify.ai"
DIFY_PLUGIN_ID="langgenius/ollama"
DIFY_VERSION="1.13.3"

# ── 헬퍼 ──────────────────────────────────────────────────────────────────
log()  { printf '[build-allinone] %s\n' "$*"; }
err()  { printf '[build-allinone] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 사전 검증 ────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."
command -v curl   >/dev/null || err "curl 명령을 찾을 수 없습니다."
command -v java   >/dev/null || err "java 명령을 찾을 수 없습니다 (JDK 11+ 필요)."
docker buildx version >/dev/null 2>&1 || err "docker buildx 가 필요합니다 (Docker 26+)."

# 자체 완결 폴더 전제: 의존 파일이 이 폴더에 복사되어 있어야 한다.
[ -f "$SCRIPT_DIR/Dockerfile" ]         || err "Dockerfile 이 없습니다."
[ -f "$SCRIPT_DIR/dify-chatflow.yaml" ] || err "dify-chatflow.yaml 이 없습니다."
[ -f "$SCRIPT_DIR/test-planning-chatflow.yaml" ] || err "test-planning-chatflow.yaml 이 없습니다 (Test Planning RAG 트랙)."
[ -d "$SCRIPT_DIR/zero_touch_qa" ]      || err "zero_touch_qa/ 디렉토리가 없습니다."
[ -f "$SCRIPT_DIR/ZeroTouch-QA.jenkinsPipeline" ] || err "Pipeline 정의 파일 없음."
[ -d "$SCRIPT_DIR/jenkins-init" ]       || err "jenkins-init/ 디렉토리가 없습니다."

log "빌드 대상: $IMAGE_TAG (platform=$TARGET_PLATFORM)"
log "출력 파일: $OUTPUT_TAR"
log "최신 런타임 핀:"
log "  Jenkins       = $JENKINS_BASE_IMAGE"
log "  Dify API      = $DIFY_API_BASE_IMAGE"
log "  Dify Web      = $DIFY_WEB_BASE_IMAGE"
log "  Dify Plugin   = $DIFY_PLUGIN_BASE_IMAGE"

# ── 1. Jenkins 플러그인 hpi 다운로드 (의존성 재귀 해결) ──────────────────
# 기본 정책: jenkins-plugins/ 에 파일이 이미 있으면 **네트워크 접근 없이 재사용**.
# 저장소에 사전 수집된 플러그인이 git 으로 동반되므로, airgap / 폐쇄망 환경에서
# 그대로 빌드 가능. 플러그인 버전을 갱신해야 할 때만 FORCE_PLUGIN_DOWNLOAD=true.
log "[1/4] Jenkins 플러그인 준비"
mkdir -p "$SCRIPT_DIR/jenkins-plugins"
EXISTING_PLUGIN_COUNT=$(find "$SCRIPT_DIR/jenkins-plugins" \( -name '*.hpi' -o -name '*.jpi' \) 2>/dev/null | wc -l | tr -d ' ')

MISSING_REQUIRED_PLUGINS=()
for p in "${JENKINS_PLUGINS[@]}"; do
  if ! find "$SCRIPT_DIR/jenkins-plugins" -maxdepth 1 -type f \( -name "$p.hpi" -o -name "$p.jpi" \) 2>/dev/null | grep -q .; then
    MISSING_REQUIRED_PLUGINS+=("$p")
  fi
done

if [ "${FORCE_PLUGIN_DOWNLOAD:-false}" != "true" ] && [ "$EXISTING_PLUGIN_COUNT" -gt 0 ] && [ "${#MISSING_REQUIRED_PLUGINS[@]}" -eq 0 ]; then
  log "  기존 플러그인 $EXISTING_PLUGIN_COUNT 개 재사용 (다운로드 스킵)."
  log "  강제 재다운로드: FORCE_PLUGIN_DOWNLOAD=true ./build.sh"
else
  if [ "$EXISTING_PLUGIN_COUNT" -eq 0 ]; then
    log "  jenkins-plugins/ 비어 있음 — 다운로드 시작 (인터넷 필요)"
  elif [ "${#MISSING_REQUIRED_PLUGINS[@]}" -gt 0 ]; then
    log "  필수 플러그인 누락: ${MISSING_REQUIRED_PLUGINS[*]} — 추가 다운로드"
  else
    log "  FORCE_PLUGIN_DOWNLOAD=true — 기존 $EXISTING_PLUGIN_COUNT 개 무시하고 재다운로드"
  fi

  if [ ! -f "$SCRIPT_DIR/jenkins-plugin-manager.jar" ]; then
    log "  jenkins-plugin-manager.jar 다운로드"
    curl -fL -o "$SCRIPT_DIR/jenkins-plugin-manager.jar" "$JENKINS_PLUGIN_MANAGER_URL"
  fi

  # 플러그인 매니저 설정: 각 플러그인을 :latest 로 요청. 의존성은 재귀 resolve.
  PLUGIN_LIST_TXT="$SCRIPT_DIR/.plugins.txt"
  : > "$PLUGIN_LIST_TXT"
  for p in "${JENKINS_PLUGINS[@]}"; do
    echo "$p:latest" >> "$PLUGIN_LIST_TXT"
  done

  log "  플러그인 목록: ${JENKINS_PLUGINS[*]}"

  # 빌드 머신에서 실제 사용할 Jenkins 버전 추출 (고정된 최신 LTS tag 기준)
  if [ -n "$JENKINS_VERSION_OVERRIDE" ]; then
    JENKINS_VERSION_DETECTED="$JENKINS_VERSION_OVERRIDE"
  else
    log "  $JENKINS_BASE_IMAGE 버전 동적 추출 중..."
    JENKINS_VERSION_DETECTED=$(docker run --rm --entrypoint java "$JENKINS_BASE_IMAGE" \
      -jar /usr/share/jenkins/jenkins.war --version 2>/dev/null | head -n1 | tr -d '\r')
  fi
  [ -z "$JENKINS_VERSION_DETECTED" ] && err "Jenkins 버전 추출 실패 — JENKINS_VERSION 환경변수로 명시"
  log "  대상 Jenkins 버전: $JENKINS_VERSION_DETECTED"

  java -jar "$SCRIPT_DIR/jenkins-plugin-manager.jar" \
    --war "" \
    --plugin-download-directory "$SCRIPT_DIR/jenkins-plugins" \
    --plugin-file "$PLUGIN_LIST_TXT" \
    --jenkins-version "$JENKINS_VERSION_DETECTED" \
    --verbose || err "Jenkins 플러그인 다운로드 실패"
fi

# PoC 2026-04-19: plugin-manager 는 .jpi 로 저장한다 (.hpi 와 동등, Jenkins 가 양쪽 모두 로드)
PLUGIN_FILE_COUNT=$(find "$SCRIPT_DIR/jenkins-plugins" \( -name '*.hpi' -o -name '*.jpi' \) | wc -l | tr -d ' ')
log "  최종 Jenkins 플러그인 파일 개수: $PLUGIN_FILE_COUNT (hpi + jpi)"

# ── 2. Dify 플러그인 .difypkg 다운로드 ────────────────────────────────────
# Jenkins 플러그인과 동일 정책: 이미 있으면 재사용, FORCE_PLUGIN_DOWNLOAD=true 로 갱신.
log "[2/4] Dify 플러그인 준비 ($DIFY_PLUGIN_ID)"
mkdir -p "$SCRIPT_DIR/dify-plugins"
EXISTING_DIFYPKG_COUNT=$(find "$SCRIPT_DIR/dify-plugins" -name '*.difypkg' 2>/dev/null | wc -l | tr -d ' ')

if [ "${FORCE_PLUGIN_DOWNLOAD:-false}" != "true" ] && [ "$EXISTING_DIFYPKG_COUNT" -gt 0 ]; then
  log "  기존 Dify 플러그인 $EXISTING_DIFYPKG_COUNT 개 재사용 (다운로드 스킵)."
  ls -lh "$SCRIPT_DIR/dify-plugins/" 2>/dev/null
else
  if [ "$EXISTING_DIFYPKG_COUNT" -eq 0 ]; then
    log "  dify-plugins/ 비어 있음 — marketplace 조회 (인터넷 필요)"
  else
    log "  FORCE_PLUGIN_DOWNLOAD=true — 기존 $EXISTING_DIFYPKG_COUNT 개 무시하고 재다운로드"
  fi

  PLUGIN_BATCH_RESP=$(curl -sS -X POST "$DIFY_PLUGIN_MARKETPLACE/api/v1/plugins/batch" \
    -H "Content-Type: application/json" \
    -H "X-Dify-Version: $DIFY_VERSION" \
    -d "{\"plugin_ids\":[\"$DIFY_PLUGIN_ID\"]}")

  PLUGIN_UID=$(echo "$PLUGIN_BATCH_RESP" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    plugins = d.get('data',{}).get('plugins',[])
    print(plugins[0].get('latest_package_identifier','') if plugins else '')
except Exception:
    print('')
")
  [ -z "$PLUGIN_UID" ] && err "marketplace 조회 실패. 응답: $PLUGIN_BATCH_RESP"

  # unique_identifier 에서 버전 분리: "org/name:ver@sha"
  PLUGIN_VERSION=$(echo "$PLUGIN_UID" | sed -n 's#.*:\([^@]*\)@.*#\1#p')
  log "  최신 버전: $PLUGIN_VERSION (uid: ${PLUGIN_UID:0:60}...)"

  # marketplace 에서 패키지 다운로드
  # endpoint: /api/v1/plugins/download?unique_identifier=...
  curl -fL -o "$SCRIPT_DIR/dify-plugins/${DIFY_PLUGIN_ID//\//-}-${PLUGIN_VERSION}.difypkg" \
    "$DIFY_PLUGIN_MARKETPLACE/api/v1/plugins/download?unique_identifier=$PLUGIN_UID" \
    || err "Dify 플러그인 다운로드 실패"

  log "  저장: $SCRIPT_DIR/dify-plugins/"
  ls -lh "$SCRIPT_DIR/dify-plugins/"
fi

# ── 3. Docker 이미지 빌드 ─────────────────────────────────────────────────
log "[3/4] Docker 이미지 빌드 ($TARGET_PLATFORM)"
log "  (이 단계는 30-90분 소요될 수 있습니다)"

docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --file "$SCRIPT_DIR/Dockerfile" \
  --tag "$IMAGE_TAG" \
  --build-arg "OLLAMA_MODEL=$OLLAMA_MODEL" \
  --load \
  "$BUILD_CTX" \
  || err "docker buildx build 실패"

log "  이미지 크기:"
docker images "$IMAGE_TAG" --format '  {{.Repository}}:{{.Tag}}  {{.Size}}'

# ── 4. docker save + gzip 압축 ────────────────────────────────────────────
log "[4/4] 이미지 save + 압축 → $OUTPUT_TAR"
docker save "$IMAGE_TAG" | gzip -1 > "$SCRIPT_DIR/$OUTPUT_TAR"
log "  최종 파일: $SCRIPT_DIR/$OUTPUT_TAR ($(du -h "$SCRIPT_DIR/$OUTPUT_TAR" | cut -f1))"

# ── 5. --redeploy: 같은 호스트에서 바로 컨테이너 재기동 + agent 재연결 ──────
if [ "$REDEPLOY" = "true" ]; then
  log ""
  log "=========================================================================="
  log "[--redeploy] 빌드 후 같은 호스트에서 컨테이너 재기동 + agent 재연결 시작"
  log "=========================================================================="

  # 5-1. 기존 컨테이너 정리 + 볼륨 처리 분기
  if docker ps -a --format '{{.Names}}' | grep -qxF "$CONTAINER_NAME"; then
    log "  [5-1] 기존 컨테이너 '$CONTAINER_NAME' 제거 (docker rm -f)"
    docker rm -f "$CONTAINER_NAME" >/dev/null
  fi
  if [ "$FRESH_VOLUME" = "true" ]; then
    if docker volume ls --format '{{.Name}}' | grep -qxF "$DATA_VOLUME"; then
      log "  [5-1] --fresh — 볼륨 '$DATA_VOLUME' 제거 (provision 재수행됨, 데이터 폐기)"
      docker volume rm "$DATA_VOLUME" >/dev/null
    fi
  elif [ "$REPROVISION" = "true" ]; then
    if docker volume ls --format '{{.Name}}' | grep -qxF "$DATA_VOLUME"; then
      log "  [5-1] --reprovision — 볼륨 '$DATA_VOLUME' 의 .app_provisioned 마커만 wipe (provision 재실행, 데이터 보존)"
      docker run --rm -v "$DATA_VOLUME":/data busybox rm -f /data/.app_provisioned
    fi
  else
    log "  [5-1] 볼륨 '$DATA_VOLUME' 유지 — 기존 provision 결과 재사용"
    log "        (이미지의 chatflow YAML / Jenkins job 정의 변경은 자동 반영 안 됨. 반영하려면 --reprovision)"
  fi

  # 5-2. 컨테이너 기동 — 호스트 플랫폼 감지해 provision.sh 가 올바른 Jenkins Node 를
  # 등록하도록 AGENT_NAME 을 env 로 주입 (Mac: mac-ui-tester / WSL2/Linux: wsl-ui-tester).
  case "$(uname -s)" in
    Darwin) HOST_AGENT_NAME="mac-ui-tester" ;;
    Linux)  HOST_AGENT_NAME="wsl-ui-tester" ;;
    *)      HOST_AGENT_NAME="wsl-ui-tester" ;;   # fallback
  esac
  # 사용자가 명시적으로 env 로 덮어쓴 경우 그대로 존중
  HOST_AGENT_NAME="${AGENT_NAME:-$HOST_AGENT_NAME}"
  # Phase R-MVP TR.8 — 호스트 recordings 디렉토리를 컨테이너에 bind mount.
  # /data 는 named volume 이라 그 위에 nested bind 는 docker 보장이 약함 →
  # 별도 마운트 포인트 /recordings 채택.
  HOST_RECORDINGS_DIR="${RECORDING_HOST_ROOT:-$HOME/.dscore.ttc.playwright-agent/recordings}"
  mkdir -p "$HOST_RECORDINGS_DIR"
  log "  [5-2] docker run (Ollama 는 호스트 → host.docker.internal 로 경유, Jenkins Node = $HOST_AGENT_NAME)"
  log "        recordings bind: $HOST_RECORDINGS_DIR → /recordings"
  docker run -d --name "$CONTAINER_NAME" \
    -p 18080:18080 -p 18081:18081 -p 50001:50001 \
    -v "$DATA_VOLUME":/data \
    -v "$HOST_RECORDINGS_DIR":/recordings:rw \
    --add-host host.docker.internal:host-gateway \
    -e OLLAMA_BASE_URL="http://host.docker.internal:11434" \
    -e OLLAMA_MODEL="$OLLAMA_MODEL" \
    -e EMBEDDING_MODEL="$EMBEDDING_MODEL" \
    -e AGENT_NAME="$HOST_AGENT_NAME" \
    --restart unless-stopped \
    "$IMAGE_TAG" >/dev/null
  log "  [5-2] 컨테이너 기동: docker ps --filter name=$CONTAINER_NAME"

  # 5-3. NODE_SECRET 대기 (프로비저닝 완료까지 최대 15분)
  if [ "$SKIP_AGENT" != "true" ]; then
    log "  [5-3] 프로비저닝 완료 / NODE_SECRET 출력 대기 (최대 15분 — amd64 는 3-5분 통상)"
    _w=0
    NODE_SECRET=""
    while [ $_w -lt 900 ]; do
      if docker exec "$CONTAINER_NAME" test -f /data/.app_provisioned 2>/dev/null; then
        NODE_SECRET=$(docker logs "$CONTAINER_NAME" 2>&1 \
          | grep -oE 'NODE_SECRET: [a-f0-9]{64}' \
          | tail -n1 \
          | awk '{print $2}' || true)
        [ -n "$NODE_SECRET" ] && break
      fi
      sleep 5; _w=$((_w + 5))
      if [ $((_w % 60)) -eq 0 ]; then
        log "    ... ${_w}s 경과"
      fi
    done
    if [ -z "$NODE_SECRET" ]; then
      log "  [5-3] ⚠ NODE_SECRET 15분 내 확보 실패. 수동 확인: docker logs $CONTAINER_NAME | grep NODE_SECRET"
      log "      이후 agent 연결: NODE_SECRET=<값> ./<mac|wsl>-agent-setup.sh"
    else
      log "  [5-3] NODE_SECRET 확보: ${NODE_SECRET:0:16}..."

      # 5-4. 플랫폼 감지 → 해당 agent-setup 호출 (setsid 로 분리 기동 — 셸 반환 후에도 생존)
      case "$(uname -s)" in
        Darwin) AGENT_SCRIPT="$SCRIPT_DIR/mac-agent-setup.sh"; AGENT_LABEL="mac-agent-setup" ;;
        Linux)  AGENT_SCRIPT="$SCRIPT_DIR/wsl-agent-setup.sh"; AGENT_LABEL="wsl-agent-setup" ;;
        *)      log "  [5-4] ⚠ 알 수 없는 OS ($(uname -s)) — agent 수동 연결 필요"; AGENT_SCRIPT="" ;;
      esac
      if [ -n "$AGENT_SCRIPT" ]; then
        log "  [5-4] $AGENT_LABEL 기동 (분리 실행 + /tmp/dscore-agent.log 로 리다이렉트)"
        # agent-setup 스크립트 자신이 기존 프로세스 정리 + 새 연결 수행
        if command -v setsid >/dev/null 2>&1; then
          setsid env NODE_SECRET="$NODE_SECRET" bash "$AGENT_SCRIPT" \
            </dev/null >/tmp/dscore-agent.log 2>&1 &
          disown
        elif command -v screen >/dev/null 2>&1; then
          # macOS 에는 setsid 가 없고 nohup 백그라운드 실행이 부모 셸 종료와 함께
          # 내려가는 경우가 있어 detached screen 세션을 우선 사용한다.
          screen -S dscore-agent-setup -X quit >/dev/null 2>&1 || true
          screen -dmS dscore-agent-setup bash -lc \
            'NODE_SECRET="$1" exec bash "$2" >/tmp/dscore-agent.log 2>&1' \
            _ "$NODE_SECRET" "$AGENT_SCRIPT"
        else
          # Mac 에는 setsid 가 없을 수 있음 — nohup 으로 대체
          nohup env NODE_SECRET="$NODE_SECRET" bash "$AGENT_SCRIPT" \
            </dev/null >/tmp/dscore-agent.log 2>&1 &
          disown
        fi
        sleep 3
        log "  [5-4] 로그 위치: /tmp/dscore-agent.log  (tail -f 로 진행 관찰 가능)"
        log "  [5-4] Jenkins Node online 확인: "
        log "        curl -sf -u admin:password \$JENKINS_URL/computer/mac-ui-tester/api/json | grep offline   # Mac"
        log "        curl -sf -u admin:password \$JENKINS_URL/computer/wsl-ui-tester/api/json | grep offline   # Windows(WSL2)"
      fi
    fi
  else
    log "  [5-3] --no-agent — agent 재연결 스킵"
    log "        수동 연결:"
    log "          docker logs $CONTAINER_NAME | grep NODE_SECRET"
    log "          NODE_SECRET=<값> ./<mac|wsl>-agent-setup.sh"
  fi

  log ""
  log "=========================================================================="
  log "[--redeploy] 완료"
  log "=========================================================================="
fi

log ""
log "=========================================================================="
log "빌드 완료 (호스트 하이브리드 이미지 — 내부 Ollama·agent 없음)"
log ""
log "아키텍처:"
log "  Jenkins master + Dify + DB  → 컨테이너 (이 이미지)"
log "  Ollama (LLM 추론)          → 호스트 (Mac: Metal / WSL2: CUDA)"
log "  Jenkins agent + Playwright → 호스트 (headed Chromium)"
log "                              — Mac: macOS 네이티브 창"
log "                              — WSL2: WSLg 경유 Windows 데스크탑 창"
log ""
log "[사전 준비 — 호스트 Mac]"
log "  A. Ollama:  brew install ollama && brew services start ollama && ollama pull ${OLLAMA_MODEL}"
log "  B. JDK 21:  brew install --cask temurin@21   (또는 openjdk@21)"
log "  C. Python:  brew install python@3.12   (3.11+)"
log ""
log "[사전 준비 — Windows 11 하이브리드 (Ollama = Windows 네이티브, agent = WSL2 Ubuntu)]"
log "  0. Windows NVIDIA 드라이버 + WSL2 + Ubuntu 22.04 설치 (PowerShell 관리자: wsl --install -d Ubuntu-22.04)"
log "  A. Ollama (Windows 네이티브 — PowerShell): winget install Ollama.Ollama && ollama pull ${OLLAMA_MODEL}"
log "     (Windows Ollama 가 host.docker.internal 로 Docker Desktop 포워딩되어 컨테이너에서 사용됨)"
log "  B. WSL2 Ubuntu 안 — JDK 21:  sudo apt install -y openjdk-21-jdk-headless"
log "  C. WSL2 Ubuntu 안 — Python:  sudo apt install -y python3.12 python3.12-venv"
log "  D. Docker:  Docker Desktop (WSL2 백엔드) 또는 WSL native Docker Engine"
log ""
log "[이미지 로드 및 컨테이너 기동 — 호스트 플랫폼에 맞는 AGENT_NAME 주입]"
log "  docker load -i $OUTPUT_TAR"
log "  case \"\$(uname -s)\" in"
log "    Darwin) AGENT_NAME=mac-ui-tester ;;"
log "    *)      AGENT_NAME=wsl-ui-tester ;;"
log "  esac"
log "  HOST_RECORDINGS_DIR=\"\${RECORDING_HOST_ROOT:-\$HOME/.dscore.ttc.playwright-agent/recordings}\""
log "  mkdir -p \"\$HOST_RECORDINGS_DIR\""
log "  docker run -d --name dscore.ttc.playwright \\"
log "    -p 18080:18080 -p 18081:18081 -p 50001:50001 \\"
log "    -v dscore-data:/data \\"
log "    -v \"\$HOST_RECORDINGS_DIR\":/recordings:rw \\"
log "    --add-host host.docker.internal:host-gateway \\"
log "    -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \\"
log "    -e OLLAMA_MODEL=${OLLAMA_MODEL} \\"
log "    -e EMBEDDING_MODEL=${EMBEDDING_MODEL} \\"
log "    -e AGENT_NAME=\"\$AGENT_NAME\" \\"
log "    --restart unless-stopped \\"
log "    $IMAGE_TAG"
log ""
log "[호스트 agent 연결 — 컨테이너 기동 완료 후]"
log "  1) docker logs dscore.ttc.playwright | grep NODE_SECRET        # 64자 hex 값 확인"
log "  2) Mac:  NODE_SECRET=<위 값> ./mac-agent-setup.sh"
log "     WSL2: NODE_SECRET=<위 값> ./wsl-agent-setup.sh"
log "     → JDK/venv/Chromium 설치 + agent 연결. 성공 시 호스트 화면에 headed Chromium"
log ""
log "접속:"
log "  - Jenkins:  http://localhost:18080  (admin / password)"
log "  - Dify:     http://localhost:18081  (admin@example.com / Admin1234!)"
log "  - Recording UI: http://localhost:18092"
log ""
log "⚠️  호스트 Ollama 미기동 / 호스트 agent 미연결 시 Pipeline Stage 3 가 실패한다."
log "    자세한 가이드: playwright-allinone_QUICKSTART.md / playwright-allinone_OPERATIONS.md"
log "=========================================================================="
