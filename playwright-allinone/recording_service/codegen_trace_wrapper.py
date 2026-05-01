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
import os
import runpy
import sys
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, BrowserType


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
        ctx = real_new_context(self, **kwargs)
        try:
            ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
            started_contexts.append(ctx)
        except Exception as e:  # noqa: BLE001 — best-effort
            print(f"[codegen-trace] tracing.start 실패 — 계속 진행: {e}",
                  file=sys.stderr)
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


def _install_headless_patch() -> None:
    """CODEGEN_HEADLESS=1 일 때 ``BrowserType.launch()`` 의 ``headless`` 인자를
    강제 True 로 덮어쓴다. codegen 이 만든 스크립트는 보통 ``headless=False`` 가
    하드코딩되어 있어, 호출자가 따로 헤드리스를 지정할 방법이 없다.
    """
    if os.environ.get("CODEGEN_HEADLESS") != "1":
        return
    real_launch = BrowserType.launch

    def patched_launch(self, **kwargs):
        kwargs["headless"] = True
        return real_launch(self, **kwargs)

    BrowserType.launch = patched_launch  # type: ignore[assignment]


def main() -> None:
    sess, script = _resolve_paths()
    _install_headless_patch()
    _install_tracing_patches(sess)
    # 사용자 스크립트 실행 — 자체 종료 코드를 그대로 전파.
    # runpy.run_path 는 module __main__ 으로 실행하므로 `if __name__ == "__main__"`
    # 가드 안의 코드도 정상 실행됨.
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
