"""Bench flake runner — 외부 사이트 시나리오 N회 반복 실행 + JSONL 누적.

사용::

    python -m test.bench.flake_runner --runs 3 --out test/bench/results/
    python -m test.bench.flake_runner --site playwright_dev --runs 10

설계:
- sites/<site>/<scenario>.json (14-DSL JSON 배열) 순회
- 각 시나리오를 ``zero_touch_qa.executor.QAExecutor`` 로 N회 실행
- 결과 1행/run JSONL 누적: {ts, site, scenario, run_idx, status, duration_s, error}
- 출력: ``<out>/<YYYY-MM-DD>/runs.jsonl`` (append)
- bot detection 회피 — D10 fingerprint pin 원칙 따라 *임의 UA spoof 금지*,
  Playwright 기본 viewport / locale 유지.

CI vs local:
- CI (GitHub Actions): ``--runs 3`` 권장 (실행 시간 ~수분)
- local: ``--runs 10`` 권장 (안정성 데이터 확보)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from zero_touch_qa.config import Config
from zero_touch_qa.executor import QAExecutor


log = logging.getLogger("bench.flake_runner")


BENCH_DIR = Path(__file__).parent
SITES_DIR = BENCH_DIR / "sites"


@dataclass(frozen=True)
class RunResult:
    site: str
    scenario: str
    run_idx: int
    status: str           # "PASS" / "FAIL" / "ERROR"
    duration_s: float
    error: Optional[str]
    ts: str               # UTC ISO 8601 (Z)

    def to_jsonl(self) -> str:
        import dataclasses
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_dir(out_root: Path) -> Path:
    """``<out_root>/<YYYY-MM-DD>/`` — UTC 날짜 기준."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = out_root / day
    p.mkdir(parents=True, exist_ok=True)
    return p


def _bench_config(artifacts_dir: Path) -> Config:
    """벤치 실행 전용 Config — 봇 회피 sleep 적용 (외부 사이트라 정상 트래픽 흉내)."""
    return Config(
        dify_base_url="http://test-stub/v1",  # bench 는 Dify healing 안 씀
        dify_api_key="",
        artifacts_dir=str(artifacts_dir),
        viewport=(1280, 800),
        slow_mo=0,
        # 봇 회피 — 액션 간 0.2~0.6초 무작위. 외부 사이트 트래픽 자연스럽게.
        step_interval_min_ms=200,
        step_interval_max_ms=600,
        headed_step_pause_ms=0,
        heal_threshold=0.8,
        heal_timeout_sec=10,
        scenario_timeout_sec=120,  # 외부 사이트는 네트워크 지연 가능
        dom_snapshot_limit=4000,
    )


def _load_scenario(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path}: scenario JSON 은 list 여야 함 (got {type(data).__name__})")
    return data


def _run_scenario_once(
    scenario: list[dict],
    site: str,
    scenario_name: str,
    run_idx: int,
    artifacts_root: Path,
) -> RunResult:
    """한 시나리오를 한 번 실행 — 격리된 artifacts 디렉토리."""
    artifacts = artifacts_root / site / scenario_name / f"run_{run_idx}"
    artifacts.mkdir(parents=True, exist_ok=True)
    config = _bench_config(artifacts)
    executor = QAExecutor(config)

    started = time.perf_counter()
    error: Optional[str] = None
    try:
        results = executor.execute(scenario, headed=False)
        # 전체 step PASS 일 때만 시나리오 PASS.
        all_pass = all(r.status in ("PASS", "HEALED") for r in results)
        status = "PASS" if all_pass else "FAIL"
        if not all_pass:
            fails = [
                f"step={r.step_id} action={r.action} status={r.status}"
                for r in results if r.status not in ("PASS", "HEALED")
            ]
            error = " | ".join(fails[:3])  # 최대 3개만 기록
    except Exception as exc:  # noqa: BLE001
        status = "ERROR"
        error = f"{type(exc).__name__}: {exc}"
        log.warning("[%s/%s/run_%d] ERROR: %s\n%s",
                    site, scenario_name, run_idx, error, traceback.format_exc())
    duration = time.perf_counter() - started

    return RunResult(
        site=site,
        scenario=scenario_name,
        run_idx=run_idx,
        status=status,
        duration_s=round(duration, 2),
        error=error,
        ts=_utc_iso(),
    )


def discover_scenarios(sites_dir: Path, only_site: Optional[str] = None) -> list[tuple[str, str, Path]]:
    """``sites/<site>/<scenario>.json`` 을 모두 탐색."""
    out: list[tuple[str, str, Path]] = []
    if not sites_dir.is_dir():
        return out
    for site_dir in sorted(sites_dir.iterdir()):
        if not site_dir.is_dir():
            continue
        if only_site and site_dir.name != only_site:
            continue
        for json_path in sorted(site_dir.glob("*.json")):
            scenario_name = json_path.stem
            out.append((site_dir.name, scenario_name, json_path))
    return out


def run(
    runs: int,
    out_root: Path,
    artifacts_root: Path,
    only_site: Optional[str] = None,
) -> tuple[list[RunResult], Path]:
    """전체 실행 — 모든 (site, scenario) 조합을 runs 회 반복."""
    scenarios = discover_scenarios(SITES_DIR, only_site=only_site)
    if not scenarios:
        log.warning("no scenarios found under %s (site filter=%r)", SITES_DIR, only_site)
        return [], _today_dir(out_root) / "runs.jsonl"

    day_dir = _today_dir(out_root)
    jsonl_path = day_dir / "runs.jsonl"
    results: list[RunResult] = []

    with jsonl_path.open("a", encoding="utf-8") as fp:
        for site, scenario_name, json_path in scenarios:
            scenario = _load_scenario(json_path)
            for run_idx in range(1, runs + 1):
                log.info("[%s/%s] run %d/%d", site, scenario_name, run_idx, runs)
                result = _run_scenario_once(
                    scenario, site, scenario_name, run_idx, artifacts_root,
                )
                fp.write(result.to_jsonl() + "\n")
                fp.flush()
                results.append(result)

    log.info("=== summary ===")
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    error_count = sum(1 for r in results if r.status == "ERROR")
    log.info("total=%d PASS=%d FAIL=%d ERROR=%d", len(results), pass_count, fail_count, error_count)
    log.info("JSONL appended → %s", jsonl_path)

    return results, jsonl_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.flake_runner",
        description="외부 SUT 시나리오 N회 반복 실행 + JSONL 누적.",
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="시나리오당 반복 횟수 (default: 3, CI 친화)",
    )
    parser.add_argument(
        "--out", type=Path, default=BENCH_DIR / "results",
        help="결과 디렉토리 (default: test/bench/results/)",
    )
    parser.add_argument(
        "--artifacts", type=Path, default=BENCH_DIR / "_artifacts",
        help="실행 부산물 (스크린샷 등) 디렉토리",
    )
    parser.add_argument(
        "--site", default=None,
        help="특정 사이트만 실행 (sites/ 하위 디렉토리 이름)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="DEBUG 로그",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    results, _ = run(
        runs=args.runs,
        out_root=args.out,
        artifacts_root=args.artifacts,
        only_site=args.site,
    )
    fail_or_error = sum(1 for r in results if r.status != "PASS")
    # exit code = fail/error 시 1 (CI 가 dashboard 는 여전히 생성하게 0 도 옵션이지만,
    # 본 러너는 의도적으로 fail 알림). dashboard 생성기는 별도 실행.
    return 1 if fail_or_error > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
