"""``python -m monitor profile`` — alias 카탈로그 관리.

기능: list / seed / delete.

내부적으로 ``zero_touch_qa.auth_profiles`` 를 호출. 카탈로그 경로는 D11 에 따라
환경변수 ``AUTH_PROFILES_DIR=~/.dscore.ttc.monitor/auth-profiles/`` 로 모니터링
PC 전용 경로로 분리됨 (install-monitor 가 설정).
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from zero_touch_qa import auth_profiles


def register(sub: argparse._SubParsersAction) -> None:
    p_top = sub.add_parser("profile", help="alias 카탈로그 관리")
    psub = p_top.add_subparsers(dest="profile_cmd", required=True)

    p_list = psub.add_parser("list", help="카탈로그 alias 목록 + 상태")
    p_list.set_defaults(func=_cmd_list)

    p_seed = psub.add_parser("seed", help="수동 시드 (브라우저 열림)")
    p_seed.add_argument("alias", help="alias 이름")
    p_seed.add_argument(
        "--target",
        required=True,
        help="시드 시작 URL (보통 사이트 진입 URL — 로그인 페이지로 redirect 되더라도 OK)",
    )
    p_seed.add_argument(
        "--verify-url",
        default=None,
        help="검증 URL (기본 --target). 시드 후 이 URL 접근 성공 = 시드 OK",
    )
    p_seed.set_defaults(func=_cmd_seed)

    p_del = psub.add_parser("delete", help="alias 카탈로그 엔트리 제거")
    p_del.add_argument("alias")
    p_del.set_defaults(func=_cmd_delete)


def _cmd_list(args: argparse.Namespace) -> int:
    profiles = auth_profiles.list_profiles()
    if not profiles:
        print("(카탈로그 비어있음)")
        return 0
    print(f"{'alias':<20} {'storage':<10} {'verified':<25} {'service_domain'}")
    print("-" * 80)
    for p in profiles:
        storage_state = "ok" if p.storage_path.is_file() else "missing"
        verified = p.last_verified_at or "-"
        print(f"{p.name:<20} {storage_state:<10} {verified:<25} {p.service_domain}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    target = args.target
    verify_url = args.verify_url or target
    verify = auth_profiles.VerifySpec(service_url=verify_url)
    try:
        profile = auth_profiles.seed_profile(
            name=args.alias,
            seed_url=target,
            verify=verify,
        )
    except auth_profiles.AuthProfileError as e:
        print(f"시드 실패: {e}", file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001 — Playwright 류 broad 캐치.
        print(f"시드 중 예외: {e}", file=sys.stderr)
        return 4
    print(f"OK - alias={profile.name} storage={profile.storage_path}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    try:
        auth_profiles.delete_profile(args.alias)
    except auth_profiles.AuthProfileError as e:
        print(f"삭제 실패: {e}", file=sys.stderr)
        return 4
    print(f"OK - {args.alias} 삭제")
    return 0
