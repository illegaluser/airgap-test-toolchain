"""recording_service.server 회귀 — D17 (2026-05-11) .py 일원화 이후 신규 엔드포인트
``/recording/sessions/{sid}/original`` 의 sanitize gate 가 *항상* 동작하는지.

D17 이전엔 bundle 다운로드 시점에만 sanitize 했고, 그 endpoint 는 제거됨.
새 흐름은 *어떤 응답 모드(다운로드/inline) 든* sanitize 통과를 보장 —
회귀 시 사용자가 받는 .py 에 평문 자격증명이 노출되는 사고를 막는 게이트.

다른 endpoint (start/stop/list/scenario 등) 는 e2e 슈트가 커버하므로 본
회귀는 sanitize gate 만 다룬다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from recording_service import server


def _stub_session(monkeypatch: pytest.MonkeyPatch, py_path: Path) -> None:
    """_registry.get / storage.original_py_path 를 격리 격납."""
    monkeypatch.setattr(
        server._registry, "get",
        lambda sid: SimpleNamespace(id=sid, state="done"),
    )
    monkeypatch.setattr(server.storage, "original_py_path", lambda sid: py_path)


def test_original_endpoint_sanitizes_password_in_inline_response(monkeypatch, tmp_path: Path):
    """download=0 (inline) — sanitize 적용된 본문 반환."""
    py = tmp_path / "x.py"
    py.write_text('page.get_by_label("password").fill("leaked-pw")\n', encoding="utf-8")
    _stub_session(monkeypatch, py)

    resp = server.get_session_original("sid-123", download=0)
    body = resp.body.decode("utf-8")

    assert "leaked-pw" not in body, "평문 자격증명이 응답에 누출됨"
    assert "__REPLACED_BY_BUNDLE_SANITIZER__" in body
    assert resp.media_type == "text/plain"


def test_original_endpoint_sanitizes_password_in_download_response(monkeypatch, tmp_path: Path):
    """download=1 (attachment) — sanitize 동일 적용 + Content-Disposition."""
    py = tmp_path / "x.py"
    py.write_text('page.get_by_label("password").fill("leaked-pw")\n', encoding="utf-8")
    _stub_session(monkeypatch, py)

    resp = server.get_session_original("sid-456", download=1)
    body = resp.body.decode("utf-8")

    assert "leaked-pw" not in body
    assert "__REPLACED_BY_BUNDLE_SANITIZER__" in body
    assert resp.media_type == "text/x-python"
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "sid-456-original.py" in cd


def test_original_endpoint_passthrough_when_no_credentials(monkeypatch, tmp_path: Path):
    """비-자격증명 코드는 변형 없이 그대로 반환 — sanitize 가 의도 외 코드 망가뜨리지 않음."""
    src = (
        'page.goto("https://example.com")\n'
        'page.get_by_label("username").fill("alice")\n'
        'page.get_by_role("button", name="검색").click()\n'
    )
    py = tmp_path / "x.py"
    py.write_text(src, encoding="utf-8")
    _stub_session(monkeypatch, py)

    resp = server.get_session_original("sid-789", download=0)
    body = resp.body.decode("utf-8")

    assert body == src
    assert "__REPLACED_BY_BUNDLE_SANITIZER__" not in body


def test_original_endpoint_404_when_session_missing(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(server._registry, "get", lambda sid: None)
    with pytest.raises(HTTPException) as exc_info:
        server.get_session_original("nope", download=0)
    assert exc_info.value.status_code == 404


def test_original_endpoint_404_when_file_missing(monkeypatch, tmp_path: Path):
    from fastapi import HTTPException

    monkeypatch.setattr(
        server._registry, "get",
        lambda sid: SimpleNamespace(id=sid, state="error"),
    )
    monkeypatch.setattr(
        server.storage, "original_py_path",
        lambda sid: tmp_path / "does_not_exist.py",
    )
    with pytest.raises(HTTPException) as exc_info:
        server.get_session_original("nope", download=0)
    assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────
# Session lifecycle — list / get / delete (서브프로세스 spawn 없이 unit)
# ─────────────────────────────────────────────────────────────────────────


def test_list_sessions_empty_when_registry_empty(monkeypatch):
    """빈 레지스트리에서 list_sessions() 는 빈 리스트."""
    from recording_service import session

    monkeypatch.setattr(server, "_registry", session.SessionRegistry())
    assert server.list_sessions() == []


def test_list_sessions_returns_created_sessions_newest_first(monkeypatch):
    """created_at 기준 내림차순 — 최근 세션이 첫 번째."""
    from recording_service import session
    import time

    reg = session.SessionRegistry()
    s1 = reg.create("https://a.com")
    time.sleep(0.005)  # created_at 가 다르도록.
    s2 = reg.create("https://b.com")
    monkeypatch.setattr(server, "_registry", reg)

    result = server.list_sessions()
    assert len(result) == 2
    # newest first
    assert result[0].id == s2.id
    assert result[1].id == s1.id


def test_get_session_returns_summary(monkeypatch):
    """``GET /recording/sessions/{sid}`` — 정상 세션 조회."""
    from recording_service import session

    reg = session.SessionRegistry()
    s = reg.create("https://x.com")
    monkeypatch.setattr(server, "_registry", reg)

    resp = server.get_session(s.id)
    assert resp.id == s.id
    assert resp.target_url == "https://x.com"
    assert resp.state == session.STATE_PENDING


def test_get_session_404_when_missing(monkeypatch):
    """존재하지 않는 sid → 404."""
    from fastapi import HTTPException
    from recording_service import session

    monkeypatch.setattr(server, "_registry", session.SessionRegistry())
    with pytest.raises(HTTPException) as exc_info:
        server.get_session("not-here")
    assert exc_info.value.status_code == 404


def test_estimate_action_count_counts_playwright_idioms(tmp_path: Path):
    """Playwright codegen 의 핵심 idiom 라인 수 — page action 카운트."""
    py = tmp_path / "x.py"
    py.write_text(
        'page.goto("https://x")\n'           # navigate
        'page.click("a")\n'                  # click
        'page.fill("b", "v")\n'              # fill
        'page.locator(".btn").hover()\n'     # hover
        'page.set_input_files("#in", "/p")\n'  # upload
        'print("not an action")\n'           # 비-액션
        ,
        encoding="utf-8",
    )
    result = server._estimate_action_count(py)
    # 5 idioms detected (goto/click/fill/hover/set_input_files)
    assert result == 5
