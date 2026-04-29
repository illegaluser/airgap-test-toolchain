import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable dataclass holding the settings needed to run Zero-Touch QA.

    Every value can be loaded from environment variables or set directly.
    frozen=True so values cannot change after construction.

    Attributes:
        dify_base_url: Base URL for the Dify Chatflow API (e.g. ``http://localhost:18081/v1``).
        dify_api_key: Bearer token for Dify API auth.
        artifacts_dir: Directory where artifacts (screenshots, reports, scenario JSON) are written.
        viewport: Browser viewport size ``(width, height)`` in pixels.
        slow_mo: Playwright slow_mo value (ms). Slows actions down for debugging.
        heal_threshold: Local DOM similarity matching threshold (0.0-1.0). Healing succeeds at or above this.
        dom_snapshot_limit: Max chars of DOM HTML sent on a Dify healing request.
    """

    dify_base_url: str
    dify_api_key: str
    artifacts_dir: str
    viewport: tuple[int, int]
    slow_mo: int
    headed_step_pause_ms: int
    step_interval_min_ms: int
    step_interval_max_ms: int
    heal_threshold: float
    heal_timeout_sec: int
    scenario_timeout_sec: int
    dom_snapshot_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        """Read settings from environment variables and build a Config instance.

        Env vars and defaults:
            - ``DIFY_BASE_URL`` → ``http://localhost/v1``
            - ``DIFY_API_KEY`` → ``""`` (empty string)
            - ``ARTIFACTS_DIR`` → ``artifacts``
            - ``VIEWPORT_WIDTH`` / ``VIEWPORT_HEIGHT`` → ``1440`` / ``900``
            - ``SLOW_MO`` → ``800`` (per-Playwright-action delay, evade bot patterns)
            - ``HEADED_STEP_PAUSE_MS`` → ``1500`` (extra pause after each step in headed mode)
            - ``STEP_INTERVAL_MIN_MS`` / ``STEP_INTERVAL_MAX_MS`` → ``800`` / ``1500``
              (random sleep between DSL steps; 0 disables)
            - ``HEAL_THRESHOLD`` → ``0.8``
            - ``HEAL_TIMEOUT_SEC`` → ``60`` (single timeout for the Dify LLM healing call; no retry)
            - ``DOM_SNAPSHOT_LIMIT`` → ``10000``

        Returns:
            A Config instance populated from env vars.
        """
        return cls(
            dify_base_url=os.getenv("DIFY_BASE_URL", "http://localhost/v1"),
            dify_api_key=os.getenv("DIFY_API_KEY", ""),
            artifacts_dir=os.getenv("ARTIFACTS_DIR", "artifacts"),
            viewport=(
                int(os.getenv("VIEWPORT_WIDTH", "1440")),
                int(os.getenv("VIEWPORT_HEIGHT", "900")),
            ),
            slow_mo=int(os.getenv("SLOW_MO", "800")),
            headed_step_pause_ms=int(os.getenv("HEADED_STEP_PAUSE_MS", "1500")),
            step_interval_min_ms=int(os.getenv("STEP_INTERVAL_MIN_MS", "800")),
            step_interval_max_ms=int(os.getenv("STEP_INTERVAL_MAX_MS", "1500")),
            heal_threshold=float(os.getenv("HEAL_THRESHOLD", "0.8")),
            heal_timeout_sec=int(os.getenv("HEAL_TIMEOUT_SEC", "60")),
            scenario_timeout_sec=int(os.getenv("SCENARIO_TIMEOUT_SEC", "300")),
            dom_snapshot_limit=int(os.getenv("DOM_SNAPSHOT_LIMIT", "10000")),
        )
