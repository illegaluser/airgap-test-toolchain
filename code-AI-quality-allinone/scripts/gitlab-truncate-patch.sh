#!/usr/bin/env bash
# ============================================================================
# GitLab Work Items UI: "Truncate descriptions" 기본 OFF 패치
# ============================================================================
#
# [배경]
# GitLab 18.x Work Items (이슈) 상세 UI 는 description 길이가 길면 본문을
# 접어 "Read more" 클릭 후에야 전체가 보이는 UX 를 기본값으로 쓴다.
# 본 프로젝트가 생성하는 이슈는 LLM 영향 분석 + 수정 제안 + 코드 스니펫 +
# Rule 설명 + SonarQube 링크가 합쳐져 3000~5000자 규모라, 기본 truncate 가
# 걸리면 사용자가 이슈 본문의 핵심(영향 분석·수정 제안·SonarQube 링크)을
# 바로 보지 못하고 "아무것도 안 보인다" 라는 인상을 받는다.
#
# [저장 위치]
# localStorage 키 `work_item_truncate_descriptions` — 서버 API / Admin 설정
# 으로 기본값 변경 불가. webpack 번들에 minified 로 박힌 초기값
# `truncationEnabled:!0` (true) 를 `!1` (false) 로 sed 치환하는 수밖에 없다.
#
# [적용 범위]
# `work_item_truncate_descriptions` 스토리지 키가 포함된 chunk 만 대상으로
# 제한해 다른 컴포넌트의 동명 상태를 건드리지 않는다. 14 chunk 각 1회씩만
# 출현하는 것을 확인했고, 멱등 (이미 `!1` 이면 no-op) 이라 재실행 안전.
#
# [실행 시점]
# run-mac.sh / run-wsl2.sh 가 `docker compose up -d` 직후 백그라운드로 호출.
# ttc-gitlab healthcheck 가 healthy 가 될 때까지 폴링 후 패치.
#
# [제약]
# - GitLab 업그레이드로 minified 변수명이 바뀌면 grep 실패 → no-op. 그럴
#   경우 docker 로그에 `patched=0` 이 남으므로 업그레이드 후 재확인 필요.
# - 사용자 브라우저 localStorage 에 기존 값이 있으면 그 값이 우선. 신규
#   브라우저 / 새 사용자에만 새 기본값 적용.
# ============================================================================
set -euo pipefail

CONTAINER="${GITLAB_CONTAINER:-ttc-gitlab}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"
LOG_PREFIX="[gitlab-truncate-patch]"

log() { printf '%s %s\n' "$LOG_PREFIX" "$*"; }

log "ttc-gitlab healthy 대기 (최대 $((TIMEOUT_SECONDS / 60)) 분)"
waited=0
while [ "$waited" -lt "$TIMEOUT_SECONDS" ]; do
    if docker ps --filter "name=^${CONTAINER}$" --filter "health=healthy" --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
        log "$CONTAINER healthy (${waited}s)"
        break
    fi
    sleep 10
    waited=$((waited + 10))
    if [ $((waited % 60)) -eq 0 ]; then
        log "  대기 중... (${waited}s)"
    fi
done
if [ "$waited" -ge "$TIMEOUT_SECONDS" ]; then
    log "타임아웃 — ttc-gitlab healthy 되지 않음. 패치 스킵."
    exit 0
fi

log "webpack chunk 패치 적용"
# heredoc 대신 bash -c 로 실행 — docker exec 의 stdin heredoc 은 일부 환경에서
# stdout 이 파이프 버퍼에 묶여 로그에 안 찍히는 현상이 있음. -c 는 안정적.
result=$(docker exec "$CONTAINER" bash -c '
DIR=/opt/gitlab/embedded/service/gitlab-rails/public/assets/webpack
patched=0; already=0; skipped=0
for f in $(grep -l "work_item_truncate_descriptions" "$DIR"/*.chunk.js 2>/dev/null); do
    if grep -q "truncationEnabled:!0" "$f"; then
        cp "$f" "${f}.pre-truncate-patch.bak" 2>/dev/null || true
        sed -i "s/truncationEnabled:!0/truncationEnabled:!1/g" "$f"
        patched=$((patched + 1))
    elif grep -q "truncationEnabled:!1" "$f"; then
        already=$((already + 1))
    else
        skipped=$((skipped + 1))
    fi
done
echo "patched=$patched already_patched=$already minified_variant_mismatch=$skipped"
' 2>&1)
log "  $result"
log "완료"
