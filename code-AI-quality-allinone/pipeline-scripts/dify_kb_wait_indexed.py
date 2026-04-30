#!/usr/bin/env python3
"""Poll the Dify Knowledge Base until every document reaches indexed state.

Called from Stage 4 of pipeline 2 (02 code pre-training). Right after
doc_processor.py uploads chunks the document state is one of
`indexing | waiting | parsing | cleaning`, while bge-m3 embedding runs
asynchronously and finally transitions to `completed`. This script
periodically queries /datasets/{id}/documents and waits until every
document reaches a **terminal state** (completed / error / disabled / ...).

Pipeline 3 (04 static-analysis-result-and-issue-registration) runs Dify
workflows against the same KB through the knowledge-retrieval node for
top_k search. If P3 starts before indexing is done, RAG returns empty
results and analysis quality drops sharply.
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
    p.add_argument("--dify-api-base", required=True, help="e.g. http://127.0.0.1:5001/v1")
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--timeout", type=int, default=1800,
                   help="Maximum wait in seconds. Default 30 minutes. **0 or negative = wait forever** "
                        "(use this when KB indexing exceeds 30 minutes on a large repo — the caller "
                        "must explicitly pass 0). Default is a 30-minute safety cap.")
    p.add_argument("--interval", type=int, default=10, help="Polling interval in seconds (default 10s)")
    args = p.parse_args()

    base = args.dify_api_base
    if not base.endswith("/v1") and not base.endswith("/v1/"):
        base = base.rstrip("/") + "/v1"

    # timeout <= 0 → wait forever (deadline check always False).
    # Prevents large repos (hundreds of chunks) from being cut by the timeout.
    deadline = time.time() + args.timeout if args.timeout > 0 else float("inf")
    last_summary = ""
    last_total = -1

    while True:
        try:
            docs = fetch_documents(base, args.dataset_id, args.api_key)
        except (HTTPError, URLError) as e:
            print(f"[KB-Wait] WARN: document list query failed ({e}). retrying in {args.interval}s.", file=sys.stderr)
            time.sleep(args.interval)
            if time.time() > deadline:
                print(f"[KB-Wait] FAIL: timeout {args.timeout}s — repeated document state query failures.", file=sys.stderr)
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
            print("[KB-Wait] WARN: dataset has 0 documents. Upload may have been skipped.", file=sys.stderr)
            return 3

        pending = total - sum(summary.get(k, 0) for k in TERMINAL_OK | TERMINAL_FAIL | TERMINAL_OTHER)
        if pending <= 0:
            ok = summary.get("completed", 0)
            fail = summary.get("error", 0)
            other = sum(summary.get(k, 0) for k in TERMINAL_OTHER)
            print(f"[KB-Wait] DONE completed={ok} error={fail} other={other} total={total}")
            # Keep the pipeline going even with errors — partial RAG top_k is
            # still usable for impact analysis. Just warn on abnormal counts
            # for observability.
            if fail > 0 or other > 0:
                print(f"[KB-Wait] WARN: {fail + other}/{total} abnormal documents — RAG results will be reduced.", file=sys.stderr)
            return 0

        if time.time() > deadline:
            print(f"[KB-Wait] FAIL: timeout {args.timeout}s — pending={pending}/{total}", file=sys.stderr)
            return 1

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
