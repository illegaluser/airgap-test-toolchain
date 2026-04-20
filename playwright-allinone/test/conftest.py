"""pytest fixtures — 로컬 HTML fixture 를 file:// URL 로 제공.

외부 사이트를 전혀 참조하지 않으므로 airgap / 폐쇄망에서도 그대로 실행된다.
`page` / `browser` / `context` 등의 Playwright fixture 는 pytest-playwright 가
자동 주입하며, 여기서는 fixture 폴더 경로만 추가로 제공한다.
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_url():
    """파일명을 받아 `file:///abs/path/fixtures/<name>` 를 돌려주는 헬퍼.

    예: `fixture_url("click.html")` → `file:///.../test/fixtures/click.html`
    """

    def _url(name: str) -> str:
        path = FIXTURES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"fixture 없음: {path}")
        return path.as_uri()

    return _url
