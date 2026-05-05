"""LocatorResolver 의 순수 helper 단위 테스트 (브라우저 불필요).

5e1e5a6f1 회귀 — ``name="API"`` 가 ``"오픈API"`` 에 잘못 잡히던 substring
매칭 버그를 막는 ``_split_name_exact`` helper 의 동작 검증.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.locator_resolver import _split_name_exact


@pytest.mark.parametrize(
    "raw_name,expected",
    [
        # exact 미존재 — 그대로
        ("API", ("API", False)),
        ("로그인", ("로그인", False)),
        # exact=true 분리
        ("API, exact=true", ("API", True)),
        ("로그인, exact=true", ("로그인", True)),
        # exact=false → exact 키 없는 것과 동치 (substring 매칭)
        ("API, exact=false", ("API", False)),
        # 대소문자 무시
        ("API, exact=TRUE", ("API", True)),
        ("API, EXACT=true", ("API", True)),
        # 공백 관용
        ("  API  , exact=true  ", ("API", True)),
        # name 안에 콤마/등호 가 있어도 trailing 만 분리
        ("Hello, World, exact=true", ("Hello, World", True)),
        ("Hello, exact=World", ("Hello, exact=World", False)),  # exact=World 는 modifier 아님
    ],
)
def test_split_name_exact(raw_name: str, expected: tuple[str, bool]) -> None:
    assert _split_name_exact(raw_name) == expected
