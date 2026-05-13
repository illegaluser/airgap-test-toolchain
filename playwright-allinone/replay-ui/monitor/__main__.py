"""``python -m monitor`` 진입점."""

import sys
from pathlib import Path

# Windows cp949 콘솔에서 em-dash 등 다바이트 문자 출력 시 UnicodeEncodeError 가
# 난다. Launch-ReplayUI.{bat,command} 는 데몬에 PYTHONIOENCODING=utf-8 을 주입하지만
# CLI 직접 호출에는 빠지므로 여기서 보강.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# zero_touch_qa / recording_shared 는 ../shared/ (소스 트리) 또는 ../ (휴대용
# zip 안 — 같은 폴더 루트로 카피됨) 둘 중 한 곳에 위치. Launch-ReplayUI.{bat,command}
# 는 데몬 기동 시 PYTHONPATH 로 주입하지만, ``python -m monitor`` 직접 호출에는
# 해당 경로 보강이 없어 ModuleNotFoundError 가 난다 — 여기서 보강.
_SHARED = Path(__file__).resolve().parent.parent.parent / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from .cli import main

raise SystemExit(main())
