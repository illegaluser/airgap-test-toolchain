#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — macOS (Apple Silicon / Intel) 빌드 스크립트
#
# 빌드 컨텍스트: 이 폴더 자체 (자체 완결).
# Dockerfile 위치: 이 폴더의 Dockerfile
# 결과 이미지: ttc-allinone:mac-<tag>
#
# Apple Silicon 의 native arch (linux/arm64) 로 BuildKit 이 빌드한다.
# 단, buildx (멀티플랫폼 manifest 플러그인) 는 사용하지 않는다 — WSL2 는
# build-wsl2.sh 에서 각자 native 로 빌드하므로 buildx 의 export→load 오버헤드
# (14GB tarball 직렬화, 수십 분 소요) 가 순수 낭비였다.
# `docker build` (BuildKit 백엔드) 만 사용 — 단일 native 이미지를 로컬 daemon 에
# 적재. legacy builder 제거 예고로 어차피 BuildKit 강제이므로 처음부터 정렬.
#
# 빌드 전 선행: bash scripts/download-plugins.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TAG="${TAG:-dev}"
IMAGE="${IMAGE:-ttc-allinone:mac-${TAG}}"
JENKINS_BASE_IMAGE="${JENKINS_BASE_IMAGE:-jenkins/jenkins:2.555.1-lts-jdk21}"
SONARQUBE_BASE_IMAGE="${SONARQUBE_BASE_IMAGE:-sonarqube:26.4.0.121862-community}"
DIFY_API_BASE_IMAGE="${DIFY_API_BASE_IMAGE:-langgenius/dify-api:1.13.3}"
DIFY_WEB_BASE_IMAGE="${DIFY_WEB_BASE_IMAGE:-langgenius/dify-web:1.13.3}"
DIFY_PLUGIN_BASE_IMAGE="${DIFY_PLUGIN_BASE_IMAGE:-langgenius/dify-plugin-daemon:0.5.3-local}"
GITLAB_RUNTIME_IMAGE="${GITLAB_RUNTIME_IMAGE:-gitlab/gitlab-ce:18.11.0-ce.0}"

cd "$ALLINONE_DIR"

# 자체 완결 검증
[ -f "$ALLINONE_DIR/Dockerfile" ]       || { echo "Dockerfile 없음" >&2; exit 1; }
[ -f "$ALLINONE_DIR/requirements.txt" ] || { echo "requirements.txt 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/pipeline-scripts" ] || { echo "pipeline-scripts/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/eval_runner" ]      || { echo "eval_runner/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkinsfiles" ]     || { echo "jenkinsfiles/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkins-init" ]     || { echo "jenkins-init/ 없음" >&2; exit 1; }

if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[build-mac] jenkins-plugins/ 가 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi
if [ ! -d "$ALLINONE_DIR/dify-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/dify-plugins" 2>/dev/null)" ]; then
    echo "[build-mac] dify-plugins/ 가 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi

echo "[build-mac] image:      $IMAGE"
echo "[build-mac] context:    $ALLINONE_DIR"
echo "[build-mac] Dockerfile: $ALLINONE_DIR/Dockerfile"
echo "[build-mac] pinned bases:"
echo "  Jenkins   = $JENKINS_BASE_IMAGE"
echo "  SonarQube = $SONARQUBE_BASE_IMAGE"
echo "  Dify API  = $DIFY_API_BASE_IMAGE"
echo "  Dify Web  = $DIFY_WEB_BASE_IMAGE"
echo "  Dify Plug = $DIFY_PLUGIN_BASE_IMAGE"
echo "  GitLab    = $GITLAB_RUNTIME_IMAGE"

DOCKER_BUILDKIT=1 docker build \
    -f "$ALLINONE_DIR/Dockerfile" \
    -t "$IMAGE" \
    "$@" \
    "$ALLINONE_DIR"

echo "[build-mac] 빌드 완료: $IMAGE"
echo "[build-mac] 기동: bash scripts/run-mac.sh"
