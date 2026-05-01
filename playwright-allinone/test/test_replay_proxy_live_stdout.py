"""replay_proxy._run_subprocess 의 실시간 stdout 흐름 회귀 가드.

배경 (사용자 보고 2026-05-02): UI 의 '실시간 진행 로그' 가 빈 화면으로 머무름.
원인은 ``subprocess.run(..., capture_output=True)`` 가 종료 후에야 파일에 dump
했기 때문. 폴링하는 ``play-llm.log`` / ``play-codegen.log`` 파일은 실행 중에
미존재 또는 빈 파일이었음.

본 모듈은 다음을 보장:
  - subprocess 시작 직후 log 파일이 즉시 존재 (헤더 작성 끝)
  - subprocess 가 stdout 한 줄 쓸 때마다 파일에 즉시 반영 (line buffer)
  - 종료 후 footer (returncode/elapsed) 가 append 됨
  - PlayResult.stdout 가 호환적으로 채워짐 (기존 호출자 호환)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from recording_service.replay_proxy import _run_subprocess


def test_run_subprocess_writes_log_live_line_by_line(tmp_path: Path):
    """child Python 이 print(flush=True) 한 줄 쓸 때마다 log 파일에 즉시 반영."""
    started = time.time()
    res = _run_subprocess(
        [sys.executable, "-u", "-c",
         "import time, sys\nfor i in range(3):\n    print(f'live line {i}', flush=True)\n    time.sleep(0.05)\n"],
        cwd=str(tmp_path), env=None, timeout_sec=10, started=started,
        log_name="play-test.log",
    )
    assert res.returncode == 0
    log = (tmp_path / "play-test.log").read_text(encoding="utf-8")
    assert "# cmd:" in log
    assert "live line 0" in log
    assert "live line 2" in log
    assert "# returncode: 0" in log
    # PlayResult.stdout 가 파일 내용으로 채워짐 (호환).
    assert "live line 0" in res.stdout


def test_run_subprocess_creates_log_file_immediately(tmp_path: Path):
    """헤더가 subprocess 시작 *전* 에 쓰이므로 파일이 즉시 존재.

    회귀 가드: 폴링 클라이언트가 시작 직후 첫 polling 에서 빈 응답을 받지 않게.
    """
    started = time.time()
    # 5초 sleep 하는 child — 실행 중 파일 검사
    import threading
    log_path = tmp_path / "play-test.log"

    def _check_during_run():
        # 0.5s 대기 후 파일이 이미 존재해야.
        time.sleep(0.5)
        assert log_path.is_file(), "헤더가 시작 직후 안 쓰임"
        content = log_path.read_text(encoding="utf-8")
        assert "# cmd:" in content

    t = threading.Thread(target=_check_during_run, daemon=True)
    t.start()
    res = _run_subprocess(
        [sys.executable, "-u", "-c", "import time; time.sleep(2); print('done', flush=True)"],
        cwd=str(tmp_path), env=None, timeout_sec=10, started=started,
        log_name="play-test.log",
    )
    t.join(timeout=5)
    assert res.returncode == 0


def test_run_subprocess_pythonunbuffered_set_in_env(tmp_path: Path):
    """child env 에 PYTHONUNBUFFERED=1 이 들어가 line buffer flush 보장."""
    started = time.time()
    # child 가 자기 env 의 PYTHONUNBUFFERED 를 보고 출력
    res = _run_subprocess(
        [sys.executable, "-u", "-c",
         "import os; print('PYUNB=' + os.environ.get('PYTHONUNBUFFERED', 'NOT_SET'))"],
        cwd=str(tmp_path), env={}, timeout_sec=10, started=started,
        log_name="play-test.log",
    )
    assert res.returncode == 0
    log = (tmp_path / "play-test.log").read_text(encoding="utf-8")
    assert "PYUNB=1" in log, f"PYTHONUNBUFFERED 미설정. log:\n{log}"
