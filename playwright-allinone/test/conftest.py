"""pytest fixtures — local HTML fixtures + Sprint 3 shared infrastructure.

References no external sites, so it runs the same way on airgapped /
closed networks. `page` / `browser` / `context` and other Playwright
fixtures are auto-injected by pytest-playwright; this module additionally
provides:

- `fixture_url(name)` — helper returning `file:///abs/path/fixtures/<name>`
- `make_executor(tmp_path)` — factory for an isolated Config + QAExecutor
- `monkeypatch_dify` — intercepts DifyClient.generate_scenario / request_healing
- `run_scenario(executor, scenario)` — runs a scenario, returns results / artifact paths
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
    """Helper that takes a filename and returns `file:///abs/path/fixtures/<name>`."""

    def _url(name: str) -> str:
        path = FIXTURES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"fixture not found: {path}")
        return path.as_uri()

    return _url


def _make_test_config(artifacts_dir: Path) -> Config:
    """Isolated test Config — disables bot-avoidance sleeps / slow_mo for fast runs."""
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
    """Factory that creates a QAExecutor with an isolated artifacts_dir + Config.

    Each test gets its own `artifacts/` under its tmp_path.
    Use `**overrides` to override individual Config fields.
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
    """Records call counts and last kwargs for monkeypatch_dify intercepts."""

    def __init__(self) -> None:
        self.generate_calls = 0
        self.heal_calls = 0
        self.last_generate_kwargs: dict | None = None
        self.last_heal_kwargs: dict | None = None


@pytest.fixture
def monkeypatch_dify(monkeypatch: pytest.MonkeyPatch):
    """Intercept DifyClient LLM calls with deterministic responses.

    Usage::

        def test_x(monkeypatch_dify):
            recorder = monkeypatch_dify(
                generate_response=[{"step":1,"action":"navigate","value":"file:///..."}],
                heal_response={"target":"#new", "value":""},
            )
            # ... QAExecutor's Dify calls never hit the real network
            assert recorder.heal_calls == 1

    Every Sprint 3 integration test uses this fixture to enforce Dify isolation.
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
    """Helper that runs a scenario and returns (results, scenario_after, artifacts_dir).

    Defaults to `headed=False` (headless). Each test builds a 14-action
    DSL scenario over the fixture HTML and runs it through this helper.
    Step dicts are mutated in place, so after the call you can inspect
    `scenario_after` for healed results.
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
    """For execute-mode integration tests — write the scenario list as a temp .json file."""

    def _write(scenario: list[dict], name: str = "scenario.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    return _write
