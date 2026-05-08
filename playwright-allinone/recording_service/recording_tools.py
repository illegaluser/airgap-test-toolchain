"""recording_tools — 녹화 PC 의 sess_dir 에서 bundle.zip 을 만드는 CLI.

UI 진입점 (Recording UI 의 [📦 모니터링 번들 다운로드] 버튼) 과 동등한 결과.
스크립트 / CI 환경 / 헤드리스에서 사용. 내부적으로 ``auth_flow.pack_bundle`` 호출.

Usage::

    python -m recording_service.recording_tools pack-bundle <sid> \\
        --alias packaged \\
        --verify-url https://example.com/dashboard \\
        --script-source regression_test.py \\
        --out ./mybundle.zip

Exit codes:
    0  성공
    1  argparse 오류 (argparse 가 자동 처리)
    2  sess_dir 없음
    3  평문 자격증명 의심 (--consent-plain-pw 필요)
    4  기타 BundleError
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import auth_flow, storage


def _cmd_pack_bundle(args: argparse.Namespace) -> int:
    sess_dir = storage.session_dir(args.sid)
    if not sess_dir.is_dir():
        print(f"세션 디렉토리 없음: {sess_dir}", file=sys.stderr)
        return 2
    try:
        zip_bytes = auth_flow.pack_bundle(
            sess_dir,
            alias=args.alias,
            verify_url=args.verify_url,
            script_source=args.script_source,
            consent_plain_pw=args.consent_plain_pw,
        )
    except auth_flow.PlainCredentialDetectedError as e:
        print(f"평문 자격증명 의심: {e}", file=sys.stderr)
        for line in e.diff_lines:
            print(line, file=sys.stderr)
        print("--consent-plain-pw 명시 동의 후 재시도", file=sys.stderr)
        return 3
    except auth_flow.BundleError as e:
        print(f"bundle 생성 실패: {e}", file=sys.stderr)
        return 4

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(zip_bytes)
    print(f"OK - {out} ({len(zip_bytes)} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recording_tools",
        description="녹화 sess_dir 에서 모니터링용 bundle.zip 생성",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser(
        "pack-bundle",
        help="sess_dir 에서 portable bundle.zip 생성",
    )
    p_pack.add_argument("sid", help="녹화 세션 ID (Recording UI 의 sid)")
    p_pack.add_argument(
        "--alias",
        required=True,
        help="모니터링 PC 카탈로그 alias 이름 (예: packaged)",
    )
    p_pack.add_argument(
        "--verify-url",
        required=True,
        help="만료 감지용 URL (이미 로그인된 페이지)",
    )
    p_pack.add_argument(
        "--script-source",
        default=None,
        help="sess_dir 안 .py 파일명 (생략 시 자동 — .py 가 1개일 때만)",
    )
    p_pack.add_argument(
        "--consent-plain-pw",
        action="store_true",
        help="Login Profile 미적용 + sanitize 후 평문 잔존 시 명시 동의",
    )
    p_pack.add_argument("--out", required=True, help="출력 zip 경로")
    p_pack.set_defaults(func=_cmd_pack_bundle)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
