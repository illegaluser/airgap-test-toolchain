"""인증 흐름 시뮬레이션용 로컬 HTTP fixture (개선 3).

테스트가 "쿠키-기반 인증이 적용된 페이지/안 적용된 페이지" 를 구분하는 시나리오를
검증할 때 사용. 인터넷 의존성 없이 ``http.server`` 로 작은 사이트를 띄움.

엔드포인트:
    GET /              — 로그인 폼 (비로그인 진입점). body 에 "로그인" 단어 포함.
    GET /login?user=X  — Set-Cookie: qa_session=X → /mypage 로 redirect.
    GET /mypage        — qa_session 쿠키 있으면 환영(로그아웃 단어 포함),
                          없으면 / 로 redirect.
    GET /secret        — 동일 동작 (로그인-게이트). 회귀 fixture 다양성용 alias.
    GET /login_short?user=X
                       — Set-Cookie 에 ``Max-Age=2`` 부착 (쿠키 만료 시뮬레이션).

사용:
    from test._authn_fixture_site import start_authn_site
    httpd, base_url = start_authn_site()
    try:
        # ... use base_url
    finally:
        httpd.shutdown()
"""

from __future__ import annotations

import http.server
import socketserver
import threading
from urllib.parse import parse_qs, urlparse


_PAGE_LOGIN_FORM = """<!doctype html>
<html lang="ko"><body>
<h1>Test Service</h1>
<p>로그인이 필요합니다.</p>
<form action="/login" method="get">
  <button name="user" value="qa-tester">로그인 (mock)</button>
</form>
</body></html>"""


_PAGE_LOGGED_IN = """<!doctype html>
<html lang="ko"><body>
<h1>My Page</h1>
<p>안녕하세요, {user}님. 환영합니다.</p>
<a href="/logout">로그아웃</a>
<p>이 페이지는 인증된 사용자만 볼 수 있습니다. 충분한 본문 길이 확보를 위한 추가 텍스트.
플랫폼 / 공지사항 / 자유게시판 같은 메뉴들이 보입니다.</p>
</body></html>"""


class _AuthnHandler(http.server.BaseHTTPRequestHandler):
    """간단한 쿠키-기반 인증 시뮬 핸들러."""

    def log_message(self, *a, **kw):  # noqa: A003 — 핸들러 표준 시그니처
        pass  # 테스트 출력 깔끔하게.

    def _send(self, status: int, body: str, headers: dict | None = None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        body_b = body.encode("utf-8")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_b)))
        self.end_headers()
        self.wfile.write(body_b)

    def _has_session(self) -> bool:
        return "qa_session=" in (self.headers.get("Cookie") or "")

    def _logged_in_user(self) -> str:
        cookie = self.headers.get("Cookie") or ""
        for chunk in cookie.split(";"):
            chunk = chunk.strip()
            if chunk.startswith("qa_session="):
                return chunk.split("=", 1)[1]
        return "unknown"

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler 규약
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, _PAGE_LOGIN_FORM)
            return
        if url.path == "/login":
            qs = parse_qs(url.query)
            user = qs.get("user", ["unknown"])[0]
            self._send(302, "", {
                "Set-Cookie": f"qa_session={user}; Path=/; HttpOnly",
                "Location": "/mypage",
            })
            return
        if url.path == "/login_short":
            # 만료 시뮬레이션 — Max-Age=2 (2초 후 폐기).
            qs = parse_qs(url.query)
            user = qs.get("user", ["unknown"])[0]
            self._send(302, "", {
                "Set-Cookie": f"qa_session={user}; Path=/; HttpOnly; Max-Age=2",
                "Location": "/mypage",
            })
            return
        if url.path in ("/mypage", "/secret"):
            if self._has_session():
                self._send(200, _PAGE_LOGGED_IN.format(user=self._logged_in_user()))
            else:
                self._send(302, "", {"Location": "/"})
            return
        self._send(404, "<p>not found</p>")


def start_authn_site() -> tuple[socketserver.TCPServer, str]:
    """포트 0 으로 사이트 기동 후 ``(httpd, base_url)`` 반환.

    호출자가 ``httpd.shutdown(); httpd.server_close()`` 로 정리해야 함.
    """
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _AuthnHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{port}"
