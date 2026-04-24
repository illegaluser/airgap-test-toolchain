#!/usr/bin/env python3
"""
Dify 1.13.3 — dataset.hit_count SQLAlchemy cache_key bug workaround.

증상
----
dataset 을 flask_restx 가 serialize 할 때 `hit_count` property 가
`db.session.scalar(...)` 의 cache_key 생성 중
`TypeError: 'async for' requires an iterator with __anext__ method, got method`
를 일으켜 document upload / dataset GET 이 일괄 HTTP 500 으로 깨진다.
`doc_processor.py` 의 첫 몇 청크 SUCCESS 후 나머지 모두 FAIL 하는 fingerprint.

우회
----
hit_count 는 dashboard 표시용 누적 hit 통계 (도큐먼트별 읽힘 횟수) 일 뿐이고
RAG retrieval / document upload 기능은 영향 없음 → 항상 0 반환으로 안전하게
우회. Dify 1.13.4+ 에서 공식 fix 배포되면 이 patch 제거.

멱등성
------
이미 patch 적용된 소스에 대해 재실행해도 안전 (skip).
"""

from __future__ import annotations

import pathlib
import re
import sys


PATCH_MARKER = "TTC patch: hit_count bypass"
TARGET = pathlib.Path("/opt/dify-api/api/models/dataset.py")


def main() -> int:
    if not TARGET.exists():
        print(f"[ttc-patch] target not found: {TARGET}", file=sys.stderr)
        return 1
    s = TARGET.read_text(encoding="utf-8")
    if "def hit_count" not in s:
        print("[ttc-patch] hit_count property missing — Dify version drift?", file=sys.stderr)
        return 1
    if PATCH_MARKER in s:
        print("[ttc-patch] hit_count already patched, skip")
        return 0

    pat = re.compile(
        r"    @property\n"
        r"    def hit_count\(self\):\n"
        r"        return db\.session\.scalar\(\n"
        r"            [^\n]+\n"
        r"        \)\n",
        re.MULTILINE,
    )
    m = pat.search(s)
    if not m:
        print(
            "[ttc-patch] hit_count block pattern not found — check Dify 1.13.3 layout",
            file=sys.stderr,
        )
        return 1

    replacement = (
        "    @property\n"
        "    def hit_count(self):\n"
        f"        return 0  # {PATCH_MARKER} for SQLAlchemy cache_key bug in 1.13.3\n"
    )
    s2 = s[: m.start()] + replacement + s[m.end() :]
    TARGET.write_text(s2, encoding="utf-8")
    print("[ttc-patch] hit_count property patched to return 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
