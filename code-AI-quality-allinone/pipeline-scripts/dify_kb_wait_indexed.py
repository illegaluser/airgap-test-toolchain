#!/usr/bin/env python3
"""Dify Knowledge Base 의 모든 문서가 indexing 완료 상태가 될 때까지 polling.

파이프라인 2 (02 코드 사전학습) 의 Stage 4 에서 호출. doc_processor.py 가
청크를 업로드한 직후 문서 상태는 `indexing | waiting | parsing | cleaning`
이고, bge-m3 임베딩이 async 로 진행되며 최종 `completed` 로 전이한다. 본
스크립트는 /datasets/{id}/documents 를 주기적으로 조회해 전체 문서가
completed / error / disabled 등 **terminal 상태**가 될 때까지 대기한다.

파이프라인 3 (04 정적분석-결과분석-이슈등록) 의 Dify workflow 는 동일 KB
에 대해 knowledge-retrieval 노드로 top_k 검색을 수행하므로, indexing 이
끝나지 않은 상태로 P3 를 시작하면 RAG 결과가 텅 빈 채 LLM 에 전달되어
분석 질이 급격히 저하된다.
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


TERMINAL_OK = {"completed"}
TERMINAL_FAIL = {"error"}
TERMINAL_OTHER = {"disabled", "archived", "paused"}


def fetch_documents(base: str, dataset_id: str, api_key: str) -> list[dict]:
    # Dify Knowledge API: GET /v1/datasets/{id}/documents?page=1&limit=100
    url = urljoin(base.rstrip("/") + "/", f"datasets/{dataset_id}/documents?page=1&limit=100")
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("data", [])


def summarize(docs: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for d in docs:
        st = d.get("indexing_status") or "unknown"
        counts[st] = counts.get(st, 0) + 1
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dify-api-base", required=True, help="예: http://127.0.0.1:5001/v1")
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--timeout", type=int, default=1800, help="최대 대기 초 (기본 30분)")
    p.add_argument("--interval", type=int, default=10, help="polling 간격 초 (기본 10s)")
    args = p.parse_args()

    base = args.dify_api_base
    if not base.endswith("/v1") and not base.endswith("/v1/"):
        base = base.rstrip("/") + "/v1"

    deadline = time.time() + args.timeout
    last_summary = ""
    last_total = -1

    while True:
        try:
            docs = fetch_documents(base, args.dataset_id, args.api_key)
        except (HTTPError, URLError) as e:
            print(f"[KB-Wait] WARN: document list 조회 실패 ({e}). {args.interval}s 후 재시도.", file=sys.stderr)
            time.sleep(args.interval)
            if time.time() > deadline:
                print(f"[KB-Wait] FAIL: timeout {args.timeout}s — 문서 상태 조회 연속 실패.", file=sys.stderr)
                return 2
            continue

        summary = summarize(docs)
        total = sum(summary.values())
        summary_str = ", ".join(f"{k}={v}" for k, v in sorted(summary.items()))
        if summary_str != last_summary or total != last_total:
            print(f"[KB-Wait] total={total} status={{{summary_str}}}", flush=True)
            last_summary = summary_str
            last_total = total

        if total == 0:
            print("[KB-Wait] WARN: dataset 에 문서 0 건. 업로드가 누락됐을 가능성.", file=sys.stderr)
            return 3

        pending = total - sum(summary.get(k, 0) for k in TERMINAL_OK | TERMINAL_FAIL | TERMINAL_OTHER)
        if pending <= 0:
            ok = summary.get("completed", 0)
            fail = summary.get("error", 0)
            other = sum(summary.get(k, 0) for k in TERMINAL_OTHER)
            print(f"[KB-Wait] DONE completed={ok} error={fail} other={other} total={total}")
            # error 가 있어도 파이프라인은 계속 — RAG top_k 가 부분적이라도
            # 영향 분석이 가능하므로. 관측성을 위해 비정상 건수만 경고.
            if fail > 0 or other > 0:
                print(f"[KB-Wait] WARN: 비정상 문서 {fail + other}/{total} 건 — RAG 결과가 축소됩니다.", file=sys.stderr)
            return 0

        if time.time() > deadline:
            print(f"[KB-Wait] FAIL: timeout {args.timeout}s — pending={pending}/{total}", file=sys.stderr)
            return 1

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
