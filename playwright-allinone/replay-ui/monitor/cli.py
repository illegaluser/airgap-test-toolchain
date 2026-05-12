"""monitor — 모니터링 PC 의 CLI 진입점.

Usage::

    python -m monitor replay-script <script.py> --out <dir> [--profile <alias>] [--verify-url <url>]
    python -m monitor profile list
    python -m monitor profile seed <alias> --target <url>
    python -m monitor profile delete <alias>
    python -m monitor compat-diag <url> [--output report.html]
"""

from __future__ import annotations

import argparse
import sys

from . import compat_diag_cmd, profile_cmd, replay_cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="monitor",
        description="모니터링 PC CLI — 단일 .py 시나리오 실행 / 로그인 프로파일 관리 (D17 일원화)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    replay_cmd.register_script(sub)
    profile_cmd.register(sub)
    compat_diag_cmd.register(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
