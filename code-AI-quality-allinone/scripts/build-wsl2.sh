#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — WSL2 (Windows) 빌드 스크립트
#
# 실행 위치: WSL2 Ubuntu 셸.
# 빌드 컨텍스트: 이 폴더 자체 (code-AI-quality-allinone/). 폴더만 압축해서
#   다른 머신으로 옮겨도 바로 빌드 가능.
# Dockerfile 위치: 이 폴더의 Dockerfile
# 결과 이미지: ttc-allinone:wsl2-<tag>
#
# WSL2 의 native arch (linux/amd64) 로 BuildKit 이 빌드한다.
# 단, buildx (멀티플랫폼 manifest 플러그인) 는 사용하지 않는다 — Mac/WSL2 각각
# native 빌드만 하면 되므로 buildx 의 export→load 오버헤드가 낭비였다.
# `docker build` (BuildKit 백엔드) 만 사용 — 단일 native 이미지를 그대로 로컬
# daemon 에 적재. Docker legacy builder 는 제거 예고된 상태라 BuildKit 사용이
# 어차피 강제되므로 처음부터 BuildKit 으로 정렬.
#
# 빌드 전 선행 조건:
#   1) 온라인 연결로 플러그인 다운로드: bash scripts/download-plugins.sh
#      → jenkins-plugins/, dify-plugins/, jenkins-plugin-manager.jar 생성
#   2) 이후 이 스크립트 실행
#
# 산출물:
#   1) 로컬 daemon 의 ttc-allinone:wsl2-<tag> 이미지
#   2) (기본) offline-assets/amd64/ 의 반출 tarball 3개 (all-in-one + GitLab + Dify Sandbox)
#      — 외부망 작업의 자연스러운 종착지가 *반출 패키지* 이므로 빌드 직후 자동 생성.
#      로컬에서 먼저 시험만 하고 싶으면 --no-tarball 플래그로 비활성화.
#
# 사용법:
#   bash scripts/build-wsl2.sh                # build + tarball 추출
#   bash scripts/build-wsl2.sh --no-tarball   # build 만 (로컬 시험용)
#   bash scripts/build-wsl2.sh --no-cache     # 추가 인자는 docker build 로 전달
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ → code-AI-quality-allinone/
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 인자 파싱: --no-tarball 만 가로채고 나머지는 docker build 로 통과
WITH_TARBALL=true
DOCKER_BUILD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-tarball) WITH_TARBALL=false; shift ;;
        *)            DOCKER_BUILD_ARGS+=("$1"); shift ;;
    esac
done

TAG="${TAG:-dev}"
IMAGE="${IMAGE:-ttc-allinone:wsl2-${TAG}}"
JENKINS_BASE_IMAGE="${JENKINS_BASE_IMAGE:-jenkins/jenkins:2.555.1-lts-jdk21}"
SONARQUBE_BASE_IMAGE="${SONARQUBE_BASE_IMAGE:-sonarqube:26.4.0.121862-community}"
DIFY_API_BASE_IMAGE="${DIFY_API_BASE_IMAGE:-langgenius/dify-api:1.13.3}"
DIFY_WEB_BASE_IMAGE="${DIFY_WEB_BASE_IMAGE:-langgenius/dify-web:1.13.3}"
DIFY_PLUGIN_BASE_IMAGE="${DIFY_PLUGIN_BASE_IMAGE:-langgenius/dify-plugin-daemon:0.5.3-local}"
GITLAB_RUNTIME_IMAGE="${GITLAB_RUNTIME_IMAGE:-gitlab/gitlab-ce:18.11.0-ce.0}"

cd "$ALLINONE_DIR"

# 경고: WSL2 는 /mnt/c 가 아닌 native FS 에서 빌드해야 빠르다
if [[ "$(pwd)" == /mnt/* ]]; then
    echo "[build-wsl2] WARN: WSL2 마운트 경로 ($(pwd)) 에서 빌드하면 I/O 가 느립니다." >&2
    echo "[build-wsl2]       WSL2 네이티브 경로로 clone 하는 것을 권장합니다." >&2
fi

# 자체 완결 폴더 전제 검증
[ -f "$ALLINONE_DIR/Dockerfile" ]                          || { echo "Dockerfile 없음" >&2; exit 1; }
[ -f "$ALLINONE_DIR/requirements.txt" ]                    || { echo "requirements.txt 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/pipeline-scripts" ]                    || { echo "pipeline-scripts/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/eval_runner" ]                         || { echo "eval_runner/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkinsfiles" ]                        || { echo "jenkinsfiles/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkins-init" ]                        || { echo "jenkins-init/ 없음" >&2; exit 1; }

# 플러그인 선행 단계 가드
if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[build-wsl2] jenkins-plugins/ 가 비어 있습니다. 먼저 다음을 실행하세요:" >&2
    echo "               bash scripts/download-plugins.sh" >&2
    exit 1
fi
if [ ! -d "$ALLINONE_DIR/dify-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/dify-plugins" 2>/dev/null)" ]; then
    echo "[build-wsl2] dify-plugins/ 가 비어 있습니다. 먼저 다음을 실행하세요:" >&2
    echo "               bash scripts/download-plugins.sh" >&2
    exit 1
fi

echo "[build-wsl2] image:      $IMAGE"
echo "[build-wsl2] context:    $ALLINONE_DIR"
echo "[build-wsl2] Dockerfile: $ALLINONE_DIR/Dockerfile"
echo "[build-wsl2] pinned bases:"
echo "  Jenkins   = $JENKINS_BASE_IMAGE"
echo "  SonarQube = $SONARQUBE_BASE_IMAGE"
echo "  Dify API  = $DIFY_API_BASE_IMAGE"
echo "  Dify Web  = $DIFY_WEB_BASE_IMAGE"
echo "  Dify Plug = $DIFY_PLUGIN_BASE_IMAGE"
echo "  GitLab    = $GITLAB_RUNTIME_IMAGE"

DOCKER_BUILDKIT=1 docker build \
    -f "$ALLINONE_DIR/Dockerfile" \
    -t "$IMAGE" \
    "${DOCKER_BUILD_ARGS[@]}" \
    "$ALLINONE_DIR"

echo "[build-wsl2] 빌드 완료: $IMAGE"

if [[ "$WITH_TARBALL" == true ]]; then
    echo "[build-wsl2] 반출 tarball 생성 단계로 진입 — bash scripts/offline-prefetch.sh --arch amd64"
    bash "$SCRIPT_DIR/offline-prefetch.sh" --arch amd64 --tag "$TAG"
    echo "[build-wsl2] 빌드 + 반출 패키지 생성 완료"
else
    echo "[build-wsl2] --no-tarball 지정 — 반출 단계 건너뜀"
    echo "[build-wsl2] 기동: bash scripts/run-wsl2.sh"
    echo "[build-wsl2] 추후 반출: bash scripts/offline-prefetch.sh --arch amd64 --tag $TAG"
fi
