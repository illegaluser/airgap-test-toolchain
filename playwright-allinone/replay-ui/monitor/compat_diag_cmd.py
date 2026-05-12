"""``python -m monitor compat-diag <url>`` — SUT 호환성 사전 진단.

도메인 URL 을 입력받아 14-DSL 자동화 가능성을 5종 카테고리로 판정.
결과는 stdout JSON 으로 출력하고, ``--output report.html`` 지정 시
HTML 리포트를 함께 저장한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zero_touch_qa.compat_diag import scan_dom


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "compat-diag",
        help="SUT 호환성 사전 진단 — closed shadow / WebSocket / CAPTCHA / canvas-heavy 감지",
    )
    p.add_argument("url", help="진단 대상 URL")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="HTML 리포트 저장 경로 (지정 시). JSON 은 항상 stdout 출력",
    )
    p.add_argument(
        "--timeout-ms",
        type=int,
        default=30000,
        help="페이지 로드 timeout (기본 30000ms)",
    )
    p.add_argument(
        "--settle-ms",
        type=int,
        default=2000,
        help="페이지 로드 후 hook 수집 대기 시간 (기본 2000ms)",
    )
    p.add_argument(
        "--headed",
        action="store_true",
        help="headed 모드 실행 (디버깅용)",
    )
    p.set_defaults(func=_cmd_diag)


def _cmd_diag(args: argparse.Namespace) -> int:
    report = scan_dom(
        args.url,
        timeout_ms=args.timeout_ms,
        settle_ms=args.settle_ms,
        headed=args.headed,
    )
    sys.stdout.write(report.to_json() + "\n")
    if args.output:
        args.output.write_text(report.to_html(), encoding="utf-8")
        sys.stderr.write(f"HTML report written to {args.output}\n")
    return 0 if report.verdict.startswith(("compatible", "limited")) else 2
