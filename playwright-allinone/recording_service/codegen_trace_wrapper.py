"""codegen 원본 ``original.py`` 를 *수정 없이* 실행하면서 Playwright tracing
을 자동 주입하는 래퍼.

사용 규약 (subprocess 진입점):
    CODEGEN_SESSION_DIR=<host_session_dir>
    CODEGEN_SCRIPT=<original.py | original_annotated.py>   # optional, 기본
                                                             original.py
    python -m recording_service.codegen_trace_wrapper

동작:
    1. ``BrowserContext.__init__`` (sync) 를 monkey-patch — context 생성 직후
       ``tracing.start(screenshots, snapshots)`` 호출
    2. ``BrowserContext.close`` 를 monkey-patch — close 직전 ``tracing.stop(
       path=<session_dir>/trace.zip)`` 호출
    3. ``runpy.run_path(<script>, run_name="__main__")`` 로 사용자 스크립트 실행

설계 메모:
- async API 는 1차 범위 밖 — sync API 만 patch
- script 안에 여러 context 가 있어도 각각 독립 trace 가 되지 않음. 마지막
  context.close() 직전에 단일 trace.zip 으로 합쳐 저장 (마지막 context 의
  trace 만 보존). 일반 codegen 출력은 context 1개라 무관.
- 사용자 스크립트가 예외로 종료되어도 trace.zip 을 best-effort 로 저장하기
  위해 atexit 핸들러도 설정.
"""

from __future__ import annotations

import atexit
import json
import os
import runpy
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Browser, BrowserContext, BrowserType, Page


# URL 쿼리에 인증 필요 메시지가 박혀 redirect 되는 한국형 엔터프라이즈 패턴.
# `errorMsg` / `error_msg` / `msg` 가 있으면 decode 해 사용자에게 보여줄 텍스트.
_REDIRECT_MSG_KEYS = ("errorMsg", "error_msg", "msg")


def _extract_redirect_msg(url: str) -> "str | None":
    if not url:
        return None
    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=False)
    except Exception:  # noqa: BLE001
        return None
    for key in _REDIRECT_MSG_KEYS:
        vals = qs.get(key)
        if vals and vals[0]:
            return vals[0]
    return None


# 세션 디렉터리 경로 — main() 에서 셋. patched_page_goto 가 종료 시 sidecar 작성에 사용.
_session_dir_global: "Path | None" = None
# Page.goto() 호출이 redirect 끝에 errorMsg URL 로 도달했을 때 모은 기록.
# 각 항목: {"requested": str, "final": str, "msg": str}
_redirect_log: list[dict] = []


def _install_page_goto_redirect_capture() -> None:
    """``Page.goto()`` 를 monkey-patch — 사용자 스크립트의 각 goto 가 redirect
    끝에 errorMsg URL 로 도달했는지 검사. 도달했다면 (requested, final, msg) 를
    ``_redirect_log`` 에 누적. 종료 시 ``codegen_redirects.jsonl`` 로 저장.

    Native alert 가 발화하지 않는 — 서버 redirect 만 하는 — 사이트에서 "이 step
    은 로그인 필요" 흔적을 회수하는 유일한 신호. trace_parser 가 사후에 run_log
    의 매칭 goto step 에 ``dialog_text`` 로 병합한다.
    """
    real_goto = Page.goto

    def patched_goto(self, url, **kwargs):
        response = real_goto(self, url, **kwargs)
        try:
            final_url = self.url or ""
        except Exception:  # noqa: BLE001
            final_url = ""
        msg = _extract_redirect_msg(final_url)
        if msg:
            _redirect_log.append({
                "requested": url,
                "final": final_url,
                "msg": msg,
            })
        return response

    Page.goto = patched_goto  # type: ignore[assignment]


def _flush_redirects() -> None:
    if _session_dir_global is None or not _redirect_log:
        return
    try:
        out = _session_dir_global / "codegen_redirects.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for r in _redirect_log:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[codegen-trace] redirects sidecar 저장 실패: {e}", file=sys.stderr)


def _resolve_paths() -> tuple[Path, Path]:
    sess_raw = os.environ.get("CODEGEN_SESSION_DIR")
    if not sess_raw:
        print("[codegen-trace] CODEGEN_SESSION_DIR 미설정", file=sys.stderr)
        sys.exit(2)
    sess = Path(sess_raw).expanduser()
    if not sess.is_dir():
        print(f"[codegen-trace] 세션 디렉토리 없음: {sess}", file=sys.stderr)
        sys.exit(2)

    script_name = os.environ.get("CODEGEN_SCRIPT", "original.py")
    script = sess / script_name
    if not script.is_file():
        # fallback: original.py (annotated 가 미존재할 때)
        alt = sess / "original.py"
        if alt.is_file():
            script = alt
        else:
            print(f"[codegen-trace] 스크립트 없음: {script}", file=sys.stderr)
            sys.exit(2)
    return sess, script


def _attach_progress_logging(ctx: BrowserContext) -> None:
    """``BrowserContext`` 의 모든 page 에 ``framenavigated`` 훅을 달아
    main frame URL 변경마다 한 줄을 stderr 로 흘려보낸다.

    play-codegen.log 가 즉시 갱신되어 UI 의 "실시간 진행 로그" 박스에
    단계별 진행이 보인다. 사용자 스크립트가 stdout 을 안 쓸 때도 운영자가
    실행이 살아있음을 확인 가능.
    """
    def _attach(page):
        def _on_navigated(frame):
            if frame.parent_frame is None:
                print(f"[codegen-trace] → {frame.url}",
                      file=sys.stderr, flush=True)
        page.on("framenavigated", _on_navigated)
    try:
        ctx.on("page", _attach)
        for p in ctx.pages:
            _attach(p)
    except Exception as e:  # noqa: BLE001
        print(f"[codegen-trace] 진행 훅 부착 실패 — 계속 진행: {e}",
              file=sys.stderr)


def _install_tracing_patches(session_dir: Path) -> None:
    """Browser.new_context / BrowserContext.close 에 tracing hook 주입."""
    trace_path = session_dir / "trace.zip"
    # 이전 실행의 trace 가 있으면 삭제 (덮어쓰기 의미)
    try:
        if trace_path.exists():
            trace_path.unlink()
    except OSError:
        pass

    real_new_context = Browser.new_context
    real_close = BrowserContext.close
    started_contexts: list[BrowserContext] = []
    stopped_paths: set[str] = set()  # 중복 stop 방지

    def patched_new_context(self, **kwargs):
        # 로그인 프로파일 / fingerprint 주입 — 사용자 스크립트는 보통 빈 컨텍스트로
        # 시작하므로(`browser.new_context()`), 이 시점에 storage_state / viewport
        # / locale / timezone / color_scheme 을 끼워 넣어야 인증 상태가 적용된다.
        # 사용자가 명시한 값은 절대 덮어쓰지 않음 (의도 보존).
        _inject_auth_and_fingerprint(kwargs)
        ctx = real_new_context(self, **kwargs)
        try:
            ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
            started_contexts.append(ctx)
        except Exception as e:  # noqa: BLE001 — best-effort
            print(f"[codegen-trace] tracing.start 실패 — 계속 진행: {e}",
                  file=sys.stderr)
        _attach_progress_logging(ctx)
        return ctx

    def patched_close(self, *args, **kwargs):
        # 가장 마지막에 close 되는 context 만 trace.zip 으로 저장
        # (script 안에 context 가 여러 개여도 1개의 통합 trace 는 어려우므로
        # 마지막 context 의 것을 최종 산출로 둔다.)
        if self in started_contexts and str(trace_path) not in stopped_paths:
            try:
                self.tracing.stop(path=str(trace_path))
                stopped_paths.add(str(trace_path))
            except Exception as e:  # noqa: BLE001
                print(f"[codegen-trace] tracing.stop 실패: {e}", file=sys.stderr)
        return real_close(self, *args, **kwargs)

    Browser.new_context = patched_new_context  # type: ignore[assignment]
    BrowserContext.close = patched_close  # type: ignore[assignment]

    # 사용자 스크립트가 예외로 종료되어 close() 미호출일 수 있으므로 atexit 도 등록
    def _flush_remaining():
        for ctx in started_contexts:
            try:
                if str(trace_path) in stopped_paths:
                    return
                ctx.tracing.stop(path=str(trace_path))
                stopped_paths.add(str(trace_path))
            except Exception:
                pass

    atexit.register(_flush_remaining)


def _inject_auth_and_fingerprint(kwargs: dict) -> None:
    """``Browser.new_context()`` kwargs 에 로그인 storageState 와 fingerprint 환경값을 주입.

    호출자(``replay_proxy.run_codegen_replay``) 가 다음 env 를 설정한다:
      - ``AUTH_STORAGE_STATE_IN``: storage_state 파일 경로 (있으면)
      - ``PLAYWRIGHT_VIEWPORT``: ``"<W>x<H>"`` (예: ``1280x800``)
      - ``PLAYWRIGHT_LOCALE``: locale 문자열
      - ``PLAYWRIGHT_TIMEZONE``: IANA timezone
      - ``PLAYWRIGHT_COLOR_SCHEME``: ``light`` / ``dark`` / ``no-preference``

    사용자가 ``new_context(storage_state=..., viewport=...)`` 처럼 직접 명시한
    값이 있으면 그대로 둔다. UA 는 의도적으로 spoof 하지 않음 — sec-ch-ua
    Client Hints 와의 어긋남 방지.
    """
    storage_path = os.environ.get("AUTH_STORAGE_STATE_IN")
    if storage_path and "storage_state" not in kwargs:
        kwargs["storage_state"] = storage_path
        print(f"[codegen-trace] storage_state 주입 — {storage_path}", file=sys.stderr)

    viewport_env = os.environ.get("PLAYWRIGHT_VIEWPORT", "")
    if viewport_env and "x" in viewport_env and "viewport" not in kwargs:
        try:
            w_str, h_str = viewport_env.split("x", 1)
            kwargs["viewport"] = {"width": int(w_str), "height": int(h_str)}
        except (ValueError, IndexError):
            print(
                f"[codegen-trace] PLAYWRIGHT_VIEWPORT 형식 오류 (무시) — {viewport_env!r}",
                file=sys.stderr,
            )

    locale_env = os.environ.get("PLAYWRIGHT_LOCALE")
    if locale_env and "locale" not in kwargs:
        kwargs["locale"] = locale_env

    timezone_env = os.environ.get("PLAYWRIGHT_TIMEZONE")
    if timezone_env and "timezone_id" not in kwargs:
        kwargs["timezone_id"] = timezone_env

    color_env = os.environ.get("PLAYWRIGHT_COLOR_SCHEME")
    if color_env and "color_scheme" not in kwargs:
        kwargs["color_scheme"] = color_env


def _install_launch_overrides() -> None:
    """``BrowserType.launch()`` kwargs 를 env 기반으로 덮어쓴다.

    - ``CODEGEN_HEADLESS=1`` → ``headless=True`` 강제 (사용자 스크립트가 보통
      ``headless=False`` 를 하드코딩).
    - ``CODEGEN_SLOW_MO_MS=<n>`` → ``slow_mo=<n>`` 주입 (사람이 눈으로 따라가며
      디버깅하기 위한 액션 사이 지연, ms). 사용자가 명시한 값이 있으면 보존.

    둘 중 어느 env 도 없으면 patch 자체를 설치하지 않음 (정상 케이스 비용 0).
    """
    force_headless = os.environ.get("CODEGEN_HEADLESS") == "1"
    force_headed = os.environ.get("CODEGEN_HEADED") == "1"
    slow_mo_raw = os.environ.get("CODEGEN_SLOW_MO_MS", "")
    slow_mo_ms = 0
    if slow_mo_raw:
        try:
            slow_mo_ms = max(0, int(slow_mo_raw))
        except ValueError:
            print(
                f"[codegen-trace] CODEGEN_SLOW_MO_MS 형식 오류 (무시) — {slow_mo_raw!r}",
                file=sys.stderr,
            )
    if not force_headless and not force_headed and slow_mo_ms <= 0:
        return

    real_launch = BrowserType.launch

    def patched_launch(self, **kwargs):
        if force_headless:
            kwargs["headless"] = True
        elif force_headed:
            # Playwright 기본 headless=True 라, 사용자 스크립트가 launch() 인자를
            # 안 주면 화면이 안 뜬다. headed 체크박스 ON 시 명시적 False 주입.
            kwargs["headless"] = False
        if slow_mo_ms > 0 and "slow_mo" not in kwargs:
            kwargs["slow_mo"] = slow_mo_ms
        return real_launch(self, **kwargs)

    BrowserType.launch = patched_launch  # type: ignore[assignment]


def main() -> None:
    sess, script = _resolve_paths()
    global _session_dir_global
    _session_dir_global = sess
    _install_launch_overrides()
    _install_tracing_patches(sess)
    _install_page_goto_redirect_capture()
    atexit.register(_flush_redirects)
    # 사용자 스크립트 실행 — 자체 종료 코드를 그대로 전파.
    # runpy.run_path 는 module __main__ 으로 실행하므로 `if __name__ == "__main__"`
    # 가드 안의 코드도 정상 실행됨.
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
