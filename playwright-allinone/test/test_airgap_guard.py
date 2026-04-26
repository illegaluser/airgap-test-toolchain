"""S3-12 — fixture HTML 의 외부 도메인 참조를 봉쇄하는 자동 가드.

Sprint 3 의 모든 fixture HTML 은 file:// 위에서 동작해야 한다. 외부 http(s)
도메인을 참조하는 줄이 새로 들어오면 본 테스트가 회귀로 잡는다.

화이트리스트:
- `api.example.test` — RFC 2606 reserved `.test` TLD 의 가상 호스트. 절대로
  공인 DNS 에 등록되지 않으므로 fetch 가 실네트워크에 안 나간다. mock_*
  라우트가 전부 가로채는 용도로만 쓴다.
- `https://www.w3.org/` — XML/HTML namespace 표기. 리소스 로드 안 함.
"""

from __future__ import annotations

import re
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# 허용 호스트 — 외부 네트워크에 절대 안 나가는 패턴만.
_WHITELIST = (
    "api.example.test",
    "www.w3.org",
)

_URL_RE = re.compile(r"https?://([^/\s\"'<>)]+)")


def _scan_fixture(path: Path) -> list[tuple[int, str]]:
    """fixture 안에서 화이트리스트 밖 외부 호스트를 참조하는 줄을 찾는다."""
    bad: list[tuple[int, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _URL_RE.finditer(line):
            host = m.group(1)
            if any(host == w or host.endswith("." + w) for w in _WHITELIST):
                continue
            bad.append((lineno, line.strip()))
            break
    return bad


def test_no_fixture_references_external_host():
    """test/fixtures/ 안 모든 .html 파일에 화이트리스트 밖 외부 URL 0 건."""
    fixtures = sorted(FIXTURES_DIR.glob("*.html"))
    assert fixtures, "fixture 디렉토리가 비어 있다 — 회귀 본능적 검증 불가"

    violations: dict[str, list[tuple[int, str]]] = {}
    for f in fixtures:
        bad = _scan_fixture(f)
        if bad:
            violations[f.name] = bad

    assert not violations, (
        "fixture 가 외부 호스트를 참조한다 (airgap 위반):\n"
        + "\n".join(
            f"  {name}:\n    "
            + "\n    ".join(f"L{ln}: {snippet}" for ln, snippet in entries)
            for name, entries in violations.items()
        )
    )
