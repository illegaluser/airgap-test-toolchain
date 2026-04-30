#!/usr/bin/env python3
# Contextual Retrieval (Anthropic 2024-09) — prepend a 1-2 line LLM-generated
# summary in front of each code chunk.
#
# Effect: retrieval recall +35% (Anthropic published measurement). Air-gap
# compatible: uses the host's Ollama gemma4:e4b.
#
# Input: --in <directory>  — the *.jsonl files produced by repo_context_builder
# Output: overwrites the same JSONL files — each chunk's "code" field is
# prefixed with the following header:
#     [path:lines] role: {llm_summary_1_or_2_sentences}
#     ...original code...
#
# Each chunk makes one independent Ollama call. ~5 minutes for a ~500-chunk
# repo (one-shot, rerun only when the image changes). On call failure the
# chunk keeps its original body (graceful fallback), so the pipeline does
# not block.
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
ENRICH_MODEL = os.getenv("ENRICH_MODEL", "gemma4:e4b")
TIMEOUT = float(os.getenv("ENRICH_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("ENRICH_RETRIES", "2"))


SYSTEM_PROMPT = (
    "You are a code summarizer. Given a source code chunk, write EXACTLY one or two short "
    "English sentences describing its role in the project. Focus on WHAT it does and WHY it "
    "exists. No code, no bullets, no preamble — just the sentences."
)


def ollama_summarize(path: str, lines: str, symbol: str, code: str) -> str:
    """Ask Ollama gemma4 for a 1-2 sentence summary of a code chunk. Returns
    an empty string on failure."""
    user_prompt = (
        f"File: {path} (lines {lines})\n"
        f"Symbol: {symbol}\n\n"
        f"```\n{code[:4000]}\n```\n\n"
        "Write 1-2 English sentences describing this code's role."
    )
    body = {
        "model": ENRICH_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 120,
        },
    }
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.post(url, json=body, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            msg = data.get("message", {}).get("content", "").strip()
            # Cleanup noise: strip leading quote markers / code-block fences.
            for marker in ("```", "Role:", "role:"):
                if msg.startswith(marker):
                    msg = msg[len(marker):].strip()
            return " ".join(msg.split())  # flatten newlines
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(1 + attempt)
    print(f"[enricher:WARN] {path}:{lines} summarize failed: {last_err}", file=sys.stderr)
    return ""


def enrich_chunk(chunk: dict) -> dict:
    """Prepend a role-summary header to chunk['code']. Original fields kept."""
    summary = ollama_summarize(
        chunk.get("path", ""),
        chunk.get("lines", ""),
        chunk.get("symbol", ""),
        chunk.get("code", ""),
    )
    header_parts = [f"[{chunk.get('path','')}:{chunk.get('lines','')}]"]
    if summary:
        header_parts.append(f"role: {summary}")
    header = " ".join(header_parts)
    chunk["code"] = header + "\n\n" + chunk.get("code", "")
    chunk["_context_summary"] = summary  # also kept as metadata (optional on upload)
    return chunk


def process_file(jsonl_path: Path) -> tuple[int, int]:
    """Read a single JSONL file, enrich each chunk, then overwrite. Returns
    (total, enriched_ok)."""
    total = 0
    ok = 0
    enriched_lines = []
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                chunk = json.loads(line)
            except Exception as e:
                print(f"[enricher:WARN] {jsonl_path.name} parse failed: {e}", file=sys.stderr)
                enriched_lines.append(line)
                continue
            enriched = enrich_chunk(chunk)
            if enriched.get("_context_summary"):
                ok += 1
            enriched_lines.append(json.dumps(enriched, ensure_ascii=False))
    jsonl_path.write_text("\n".join(enriched_lines) + "\n", encoding="utf-8")
    return total, ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Contextual Retrieval enricher for JSONL chunks")
    ap.add_argument("--in", dest="in_dir", required=True, help="Directory containing JSONL files")
    args = ap.parse_args()

    in_dir = Path(args.in_dir).resolve()
    if not in_dir.is_dir():
        raise SystemExit(f"[enricher] input directory not found: {in_dir}")

    files = sorted(in_dir.glob("*.jsonl"))
    if not files:
        print(f"[enricher] no JSONL files → skip: {in_dir}")
        return 0

    total_chunks = 0
    total_ok = 0
    t0 = time.time()
    for jsonl_path in files:
        t, k = process_file(jsonl_path)
        total_chunks += t
        total_ok += k
        print(f"[enricher] {jsonl_path.name}: {k}/{t} enriched")
    elapsed = time.time() - t0
    print(f"[enricher] done — files={len(files)} chunks={total_chunks} enriched_ok={total_ok} ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
