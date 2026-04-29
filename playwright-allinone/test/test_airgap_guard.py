"""S3-12 — automatic guard blocking external-domain references in fixture HTML.

Every Sprint 3 fixture HTML must run over file://. Any new line that
references an external http(s) domain is caught here as a regression.

Whitelist:
- `api.example.test` — virtual host on the RFC 2606 reserved `.test` TLD.
  It can never be registered in public DNS, so fetches don't leak to the
  real network. Used solely for mock_* routes to intercept.
- `https://www.w3.org/` — XML/HTML namespace literals. No resource loads.
"""

from __future__ import annotations

import re
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Allowed hosts — only patterns that never reach an external network.
_WHITELIST = (
    "api.example.test",
    "www.w3.org",
)

_URL_RE = re.compile(r"https?://([^/\s\"'<>)]+)")


def _scan_fixture(path: Path) -> list[tuple[int, str]]:
    """Find lines in a fixture that reference an external host outside the whitelist."""
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
    """All .html files under test/fixtures/ have zero external URLs outside the whitelist."""
    fixtures = sorted(FIXTURES_DIR.glob("*.html"))
    assert fixtures, "fixtures dir is empty — can't run regression check"

    violations: dict[str, list[tuple[int, str]]] = {}
    for f in fixtures:
        bad = _scan_fixture(f)
        if bad:
            violations[f.name] = bad

    assert not violations, (
        "fixture references an external host (airgap violation):\n"
        + "\n".join(
            f"  {name}:\n    "
            + "\n    ".join(f"L{ln}: {snippet}" for ln, snippet in entries)
            for name, entries in violations.items()
        )
    )
