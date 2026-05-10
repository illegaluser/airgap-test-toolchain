"""``python -m monitor replay-script`` — 단일 .py 한 개 실행 (orchestrator.run_script).

D17 일원화 — 번들 zip 흐름 폐기 후 단일 진입점. stdout 으로 jsonl 이벤트 미러링
(계획 §C1) — Replay UI 의 SSE 도 같은 stdout 을 받음.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from replay_service import orchestrator


def register_script(sub: argparse._SubParsersAction) -> None:
    """D17 — 단일 .py 시나리오 실행 (번들 zip 폐기 후 단일 진입점)."""
    p = sub.add_parser(
        "replay-script",
        help="단일 .py 시나리오 실행 (D17 — 번들 zip 폐기 후 진입점)",
    )
    p.add_argument("script", metavar="script.py", help="실행할 Playwright .py 경로")
    p.add_argument("--out", required=True, help="결과 디렉토리")
    p.add_argument(
        "--profile",
        default="",
        metavar="이름",
        help="적용할 로그인 프로파일 이름 — 빈 값/미지정 시 비로그인 시나리오로 실행",
    )
    p.add_argument(
        "--verify-url",
        default="",
        metavar="URL",
        help="만료 감지 probe URL — 빈 값 시 프로파일 카탈로그의 verify.service_url 사용",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="화면 없이 헤드리스로 실행 (기본 headed — D9)",
    )
    p.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="ms",
        help="각 액션 사이 지연 ms (사람이 눈으로 따라갈 때 유용). 0 이면 비활성.",
    )
    p.set_defaults(func=_run_replay_script)


def _run_replay_script(args: argparse.Namespace) -> int:
    script = Path(args.script).expanduser().resolve()
    if not script.is_file():
        print(f".py 파일을 찾을 수 없습니다: {script}", file=sys.stderr)
        return orchestrator.EXIT_SYS_ERROR
    out = Path(args.out).expanduser().resolve()

    rc = orchestrator.run_script(
        script,
        out,
        alias=(args.profile or None),
        verify_url=(args.verify_url or None),
        headed=(not args.headless),
        slow_mo_ms=(args.slow_mo if args.slow_mo > 0 else None),
    )
    log_file = out / "run_log.jsonl"
    if log_file.is_file():
        try:
            sys.stdout.write(log_file.read_text(encoding="utf-8"))
            sys.stdout.flush()
        except Exception:
            pass
    return rc
