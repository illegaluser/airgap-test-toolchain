"""pytest fixtures for e2e-test/ — local fixture HTML + Playwright helpers.

설계 근거: ../docs/PLAN_E2E_REWRITE.md §5 ('새 슈트 그룹 A~E').

외부 SUT / 네트워크 의존 0. 모든 fixture HTML 은 ``e2e-test/fixtures/`` 에서
``file://`` URI 로 self-serve.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

# Windows 콘솔이 CP949 인 환경에서 pytest 플러그인이 skip reason 의 유니코드
# (em-dash 등) 를 print 하다 UnicodeEncodeError 로 죽는 사고 방지.
for _stream in (sys.stdout, sys.stderr):
    reconfig = getattr(_stream, "reconfigure", None)
    if reconfig is not None:
        try:
            reconfig(encoding="utf-8", errors="replace")
        except Exception:
            pass

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_test_config(artifacts_dir: Path):
    """B 그룹 integration 슈트용 테스트 격리 Config — 봇 회피 sleep/slow_mo 0."""
    from zero_touch_qa.config import Config
    return Config(
        dify_base_url="http://test-stub/v1",
        dify_api_key="test-key",
        artifacts_dir=str(artifacts_dir),
        viewport=(1280, 800),
        slow_mo=0,
        headed_step_pause_ms=0,
        step_interval_min_ms=0,
        step_interval_max_ms=0,
        heal_threshold=0.8,
        heal_timeout_sec=10,
        scenario_timeout_sec=60,
        dom_snapshot_limit=4000,
    )


@pytest.fixture(scope="session")
def fixture_url() -> Callable[[str], str]:
    """파일명을 받아 ``file:///abs/path/fixtures/<name>`` 를 돌려준다."""

    def _url(name: str) -> str:
        path = FIXTURES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"fixture 없음: {path}")
        return path.as_uri()

    return _url


@pytest.fixture
def make_executor(tmp_path: Path) -> Callable:
    """B 그룹 integration 슈트용 — 격리된 artifacts_dir + Config 로 QAExecutor 팩토리.

    ``**overrides`` 로 Config 일부 필드를 덮어쓸 수 있다.
    """
    from zero_touch_qa.executor import QAExecutor

    def _make(**overrides):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        cfg = _make_test_config(artifacts)
        if overrides:
            cfg = replace(cfg, **overrides)
        return QAExecutor(cfg)

    return _make


@pytest.fixture
def run_scenario() -> Callable:
    """시나리오를 실행하고 ``(results, scenario, artifacts_dir)`` 를 돌려준다.

    ``headed=False`` (headless) 가 pre-push 슈트의 기본. 픽스처 HTML 위에서
    14대 DSL 시나리오를 직접 만들어 이 헬퍼로 실행.
    """

    def _run(executor, scenario: list[dict], *, headed: bool = False):
        results = executor.execute(scenario, headed=headed)
        artifacts = Path(executor.config.artifacts_dir)
        return results, scenario, artifacts

    return _run


@pytest.fixture
def emit_regression(tmp_path) -> Callable:
    """unit 슈트용 — scenario → emit 된 회귀 .py 의 소스 문자열을 돌려준다.

    모든 step 을 PASS 로 가정 (custom results 가 필요하면 results= 로 주입).
    외부 의존 0 — 파일 시스템 임시 디렉토리만 사용.
    """
    from zero_touch_qa.executor import StepResult  # local import — pre-commit 빠른 collect
    from zero_touch_qa.regression_generator import generate_regression_test

    def _pass(step: int, action: str, target: str = "", value: str = "") -> "StepResult":
        return StepResult(
            step_id=step, action=action, target=target, value=value,
            description="", status="PASS", heal_stage="none",
        )

    def _emit(scenario: list[dict], results: list | None = None) -> str | None:
        if results is None:
            results = [_pass(s["step"], s["action"], s.get("target", ""), s.get("value", ""))
                       for s in scenario]
        output = generate_regression_test(scenario, results, str(tmp_path))
        if output is None:
            return None
        return Path(output).read_text(encoding="utf-8")

    return _emit
