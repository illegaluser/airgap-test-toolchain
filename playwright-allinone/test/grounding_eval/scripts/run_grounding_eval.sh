#!/usr/bin/env bash
# Phase 1 T1.7 — DOM Grounding off/on 페어 실행 러너.
#
# 사용:
#   FLAG=off ./test/grounding_eval/scripts/run_grounding_eval.sh <out_root>
#   FLAG=on  ./test/grounding_eval/scripts/run_grounding_eval.sh <out_root>
#
# 환경:
#   FLAG       — off | on (기본 off)
#   SRS_TEXT   — Planner 입력 SRS. 골든 시나리오의 description 을 합쳐 자동 생성도 가능
#   PLAYWRIGHT_PYTHON — python interpreter (기본 python3)
#   DIFY_BASE_URL / DIFY_API_KEY 등은 컨테이너 env 또는 호스트 export
#
# 입력: test/grounding_eval/golden/<id>.scenario.json (catalog_id + target_url)
# 출력: <out_root>/<catalog_id>/{scenario.json, llm_calls.jsonl, run_log.jsonl}
#
# Dify 가 실행 중이어야 동작. 본 스크립트는 페어 실행 자동화만 담당하고
# 결과 비교는 test/grounding_eval/compare.py 가 처리한다.
set -euo pipefail

OUT_ROOT="${1:-artifacts/grounding-eval}"
FLAG="${FLAG:-off}"
PY="${PLAYWRIGHT_PYTHON:-python3}"
GOLDEN_DIR="${GOLDEN_DIR:-test/grounding_eval/golden}"

if [[ "$FLAG" == "on" ]]; then
  export ENABLE_DOM_GROUNDING=1
else
  unset ENABLE_DOM_GROUNDING || true
fi

mkdir -p "$OUT_ROOT"

shopt -s nullglob
GOLDEN_FILES=( "$GOLDEN_DIR"/*.scenario.json )
if [[ ${#GOLDEN_FILES[@]} -eq 0 ]]; then
  echo "[grounding-eval] $GOLDEN_DIR 골든 파일 없음" >&2
  exit 1
fi

for golden in "${GOLDEN_FILES[@]}"; do
  cid=$(${PY} -c "import json,sys; print(json.load(open('$golden'))['catalog_id'])")
  url=$(${PY} -c "import json,sys; print(json.load(open('$golden'))['target_url'])")
  desc=$(${PY} -c "import json,sys; print(json.load(open('$golden'))['description'])")

  page_dir="$OUT_ROOT/$cid"
  mkdir -p "$page_dir"

  echo "[grounding-eval][$FLAG] $cid → $url"

  # zero_touch_qa CLI 호출. chat 모드 + flag 가 grounding 결정.
  ARTIFACTS_DIR="$page_dir" \
    ${PY} -m zero_touch_qa \
      --mode chat \
      --headless \
      --srs-text "$desc" \
      --target-url "$url" \
    || echo "[grounding-eval][$FLAG] $cid 실패 (계속 진행)" >&2
done

echo "[grounding-eval][$FLAG] 완료 — $OUT_ROOT"
