"""pytest fixtures — 로컬 HTML fixture + Sprint 3 공통 인프라.

외부 사이트를 전혀 참조하지 않으므로 airgap / 폐쇄망에서도 그대로 실행된다.
`page` / `browser` / `context` 등의 Playwright fixture 는 pytest-playwright 가
자동 주입하며, 여기서는 다음을 추가로 제공한다:

- `fixture_url(name)` — `file:///abs/path/fixtures/<name>` URL 헬퍼
- `make_executor(tmp_path)` — 격리된 Config + QAExecutor 팩토리
- `monkeypatch_dify` — DifyClient.generate_scenario / request_healing 가로채기
- `run_scenario(executor, scenario)` — 시나리오 실행 + 결과/산출물 경로 반환
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

from zero_touch_qa.config import Config
from zero_touch_qa.dify_client import DifyClient
from zero_touch_qa.executor import QAExecutor, StepResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_url() -> Callable[[str], str]:
    """파일명을 받아 `file:///abs/path/fixtures/<name>` 를 돌려주는 헬퍼."""

    def _url(name: str) -> str:
        path = FIXTURES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"fixture 없음: {path}")
        return path.as_uri()

    return _url


def _make_test_config(artifacts_dir: Path) -> Config:
    """테스트 격리 Config — 봇 회피 sleep / slow_mo 모두 끄고 빠르게 돌게 한다."""
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


@pytest.fixture
def make_executor(tmp_path: Path) -> Callable[..., QAExecutor]:
    """격리된 artifacts_dir + Config 로 QAExecutor 인스턴스를 만들어 주는 팩토리.

    각 테스트는 자신의 tmp_path 아래 `artifacts/` 를 갖는다.
    `**overrides` 로 Config 일부 필드만 덮어쓸 수 있다.
    """

    def _make(**overrides) -> QAExecutor:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        cfg = _make_test_config(artifacts)
        if overrides:
            cfg = replace(cfg, **overrides)
        return QAExecutor(cfg)

    return _make


class _DifyCallRecorder:
    """monkeypatch_dify 가 가로챈 호출 횟수와 마지막 인자를 기록한다."""

    def __init__(self) -> None:
        self.generate_calls = 0
        self.heal_calls = 0
        self.last_generate_kwargs: dict | None = None
        self.last_heal_kwargs: dict | None = None


@pytest.fixture
def monkeypatch_dify(monkeypatch: pytest.MonkeyPatch):
    """DifyClient 의 LLM 호출을 결정론적 응답으로 가로챈다.

    사용법::

        def test_x(monkeypatch_dify):
            recorder = monkeypatch_dify(
                generate_response=[{"step":1,"action":"navigate","value":"file:///..."}],
                heal_response={"target":"#new", "value":""},
            )
            # ... QAExecutor 가 Dify 호출을 시도해도 실 네트워크 안 나감
            assert recorder.heal_calls == 1

    Sprint 3 의 모든 통합 테스트는 이 fixture 로 실 Dify 분리를 강제한다.
    """

    def _install(
        *,
        generate_response: list | Exception | None = None,
        heal_response: dict | Exception | None = None,
    ) -> _DifyCallRecorder:
        rec = _DifyCallRecorder()

        def _fake_generate(self, **kwargs):
            rec.generate_calls += 1
            rec.last_generate_kwargs = kwargs
            if isinstance(generate_response, Exception):
                raise generate_response
            return generate_response or []

        def _fake_heal(self, **kwargs):
            rec.heal_calls += 1
            rec.last_heal_kwargs = kwargs
            if isinstance(heal_response, Exception):
                raise heal_response
            return heal_response

        monkeypatch.setattr(DifyClient, "generate_scenario", _fake_generate)
        monkeypatch.setattr(DifyClient, "request_healing", _fake_heal)
        return rec

    return _install


@pytest.fixture
def run_scenario():
    """시나리오를 실행하고 (results, scenario_after, artifacts_dir) 를 돌려주는 헬퍼.

    `headed=False` (headless) 가 기본. 각 테스트는 fixture HTML 위에서 14대
    DSL 시나리오를 직접 만들어 이 헬퍼로 실행한다. step dict 은 in-place
    mutation 되므로 호출 후 `scenario_after` 로 healed 결과를 검증할 수 있다.
    """

    def _run(
        executor: QAExecutor,
        scenario: list[dict],
        *,
        headed: bool = False,
    ) -> tuple[list[StepResult], list[dict], Path]:
        results = executor.execute(scenario, headed=headed)
        artifacts = Path(executor.config.artifacts_dir)
        return results, scenario, artifacts

    return _run


@pytest.fixture
def write_scenario_json(tmp_path: Path):
    """execute 모드 통합 테스트용 — scenario list 를 임시 .json 파일로 기록."""

    def _write(scenario: list[dict], name: str = "scenario.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    return _write
