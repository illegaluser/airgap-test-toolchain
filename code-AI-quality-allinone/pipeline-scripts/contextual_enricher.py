#!/usr/bin/env python3
# Contextual Retrieval (Anthropic 2024-09) — 각 코드 청크 앞에 LLM 생성 1~2줄 요약 prepend.
#
# 효과: retrieval recall +35% (Anthropic 공식 실측). 에어갭 호환: 호스트 Ollama gemma4:e4b 사용.
#
# 입력: --in <디렉터리>   — repo_context_builder 가 생성한 *.jsonl 여러 개
# 출력: 동일 JSONL 덮어쓰기 — 각 청크의 "code" 필드 앞에 다음 헤더 prepend:
#     [path:lines] role: {llm_summary_1~2_sentences}
#     ...original code...
#
# 각 청크는 독립 Ollama 호출 1회. 레포 ~500 청크 기준 ~5분 (1회성, 이미지 변경 시 재실행).
# 호출 실패 시: 해당 청크는 요약 없이 원본 유지 (graceful fallback). 파이프라인 비차단.
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
ENRICH_MODEL = os.getenv("ENRICH_MODEL") or os.getenv("OLLAMA_MODEL", "gemma4:e4b")
TIMEOUT = float(os.getenv("ENRICH_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("ENRICH_RETRIES", "2"))


SYSTEM_PROMPT = (
    "You are a code summarizer. Given a source code chunk, write EXACTLY one or two short "
    "Korean sentences describing its role in the project. Focus on WHAT it does and WHY it "
    "exists. No code, no bullets, no preamble — just the sentences."
)


def ollama_summarize(path: str, lines: str, symbol: str, code: str) -> str:
    """Ollama gemma4 에게 코드 청크 요약 1~2문장 요청. 실패 시 빈 문자열."""
    user_prompt = (
        f"File: {path} (lines {lines})\n"
        f"Symbol: {symbol}\n\n"
        f"```\n{code[:4000]}\n```\n\n"
        "Write 1-2 Korean sentences describing this code's role."
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
            # 잡음 정리: 양 끝 따옴표·코드블록 마커 제거
            for marker in ("```", "역할:", "Role:"):
                if msg.startswith(marker):
                    msg = msg[len(marker):].strip()
            return " ".join(msg.split())  # 줄바꿈 평탄화
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(1 + attempt)
    print(f"[enricher:WARN] {path}:{lines} 요약 실패: {last_err}", file=sys.stderr)
    return ""


def enrich_chunk(chunk: dict) -> dict:
    """chunk['code'] 앞에 역할 요약 헤더 prepend. 원본 필드는 유지."""
    summary = ollama_summarize(
        chunk.get("path", ""),
        chunk.get("lines", ""),
        chunk.get("symbol", ""),
        chunk.get("code", ""),
    )
    header_parts = [f"[{chunk.get('path','')}:{chunk.get('lines','')}]"]
    if summary:
        header_parts.append(f"역할: {summary}")
    header = " ".join(header_parts)
    chunk["code"] = header + "\n\n" + chunk.get("code", "")
    chunk["_context_summary"] = summary  # metadata 로도 보관 (업로드 시 optional)
    return chunk


def process_file(jsonl_path: Path) -> tuple[int, int]:
    """단일 JSONL 파일을 읽어 각 청크 enrich 후 덮어쓰기. (total, enriched_ok) 반환."""
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
                print(f"[enricher:WARN] {jsonl_path.name} 파싱 실패: {e}", file=sys.stderr)
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
    ap.add_argument("--in", dest="in_dir", required=True, help="JSONL 파일들이 있는 디렉터리")
    args = ap.parse_args()

    in_dir = Path(args.in_dir).resolve()
    if not in_dir.is_dir():
        raise SystemExit(f"[enricher] 입력 디렉터리 없음: {in_dir}")

    files = sorted(in_dir.glob("*.jsonl"))
    if not files:
        print(f"[enricher] JSONL 없음 → skip: {in_dir}")
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
