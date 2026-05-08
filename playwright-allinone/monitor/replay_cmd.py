"""``python -m monitor replay`` — bundle.zip 한 개 실행 (orchestrator 재사용).

stdout 으로 jsonl 이벤트 미러링 (계획 §C1) — Replay UI 의 SSE 도 같은 stdout 을 받음.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from replay_service import orchestrator


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "replay",
        help="시나리오 묶음 zip 한 개 실행 (로그인 상태 확인 → 스크립트 실행 → 결과 파싱)",
    )
    p.add_argument("bundle", metavar="시나리오묶음.zip", help="시나리오 묶음 zip 경로")
    p.add_argument(
        "--out",
        required=True,
        help="결과 디렉토리 (run_log.jsonl / trace.zip / screenshots/ / meta.json 이 누적됨)",
    )
    p.set_defaults(func=_run_replay)


def _run_replay(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).expanduser().resolve()
    if not bundle.is_file():
        print(f"시나리오 묶음 파일을 찾을 수 없습니다: {bundle}", file=sys.stderr)
        return orchestrator.EXIT_SYS_ERROR
    out = Path(args.out).expanduser().resolve()

    rc = orchestrator.run_bundle(bundle, out)

    # run_log 를 stdout 으로 미러링 (간단 tail).
    log_file = out / "run_log.jsonl"
    if log_file.is_file():
        try:
            sys.stdout.write(log_file.read_text(encoding="utf-8"))
            sys.stdout.flush()
        except Exception:
            pass
    return rc
