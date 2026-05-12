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
    p_top = sub.add_parser("profile", help="로그인 프로파일 관리")
    psub = p_top.add_subparsers(dest="profile_cmd", required=True)

    p_list = psub.add_parser("list", help="등록된 로그인 프로파일 목록 + 상태")
    p_list.set_defaults(func=_cmd_list)

    p_seed = psub.add_parser(
        "seed",
        help="새 로그인 프로파일 등록 (브라우저 열림 → 직접 로그인 → 사람이 창 닫기)",
    )
    p_seed.add_argument("alias", metavar="이름", help="로그인 프로파일 이름")
    p_seed.add_argument(
        "--target",
        required=True,
        help="시작 URL — 테스트 대상 서비스 진입 페이지 (로그인 페이지로 redirect 되어도 OK)",
    )
    p_seed.add_argument(
        "--verify-url",
        default=None,
        help="로그인 상태 확인 URL (기본 --target 과 동일). 등록 후 이 URL 도달 성공 = 등록 OK",
    )
    p_seed.add_argument(
        "--verify-text",
        default="",
        help="검증 텍스트 (선택). 비우면 URL 도달만 확인, 채우면 해당 텍스트가 페이지에 보여야 통과 (강한 검증)",
    )
    p_seed.add_argument(
        "--no-naver-probe",
        action="store_true",
        help="네이버 측 weak probe 비활성 (기본 활성, best-effort)",
    )
    p_seed.add_argument(
        "--ttl-hint-hours",
        type=int,
        default=12,
        help="만료 추정 (UI 표시용). 기본 12시간",
    )
    p_seed.add_argument(
        "--timeout-sec",
        type=int,
        default=600,
        help="사용자 입력 대기 한도 초 (기본 600)",
    )
    p_seed.set_defaults(func=_cmd_seed)

    p_del = psub.add_parser("delete", help="로그인 프로파일 삭제")
    p_del.add_argument("alias", metavar="이름")
    p_del.set_defaults(func=_cmd_delete)


def _cmd_list(args: argparse.Namespace) -> int:
    profiles = auth_profiles.list_profiles()
    if not profiles:
        print("(등록된 로그인 프로파일 없음)")
        return 0
    print(f"{'이름':<20} {'로그인 상태':<12} {'최근 확인':<25} {'사이트'}")
    print("-" * 80)
    for p in profiles:
        storage_state = "등록됨" if p.storage_path.is_file() else "미등록"
        verified = p.last_verified_at or "-"
        print(f"{p.name:<20} {storage_state:<12} {verified:<25} {p.service_domain}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    target = args.target
    verify_url = args.verify_url or target
    naver_probe = None
    if not args.no_naver_probe:
        naver_probe = auth_profiles.NaverProbeSpec()
    verify = auth_profiles.VerifySpec(
        service_url=verify_url,
        service_text=args.verify_text,
        naver_probe=naver_probe,
    )

    def _on_progress(phase: str, message: str) -> None:
        print(f"[{phase}] {message}", file=sys.stderr)

    try:
        profile = auth_profiles.seed_profile(
            name=args.alias,
            seed_url=target,
            verify=verify,
            ttl_hint_hours=args.ttl_hint_hours,
            timeout_sec=args.timeout_sec,
            progress_callback=_on_progress,
        )
    except auth_profiles.AuthProfileError as e:
        print(f"로그인 등록 실패: {e}", file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001 — Playwright 류 broad 캐치.
        print(f"로그인 등록 중 예외: {e}", file=sys.stderr)
        return 4
    print(f"OK - 프로파일 '{profile.name}' 등록 완료 (저장 위치: {profile.storage_path})")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    try:
        auth_profiles.delete_profile(args.alias)
    except auth_profiles.AuthProfileError as e:
        print(f"삭제 실패: {e}", file=sys.stderr)
        return 4
    print(f"OK - 프로파일 '{args.alias}' 삭제 완료")
    return 0
