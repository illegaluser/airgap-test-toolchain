"""Bench dashboard 생성기 — results/ 의 JSONL 들 → 정적 HTML 시계열.

사용::

    python -m test.bench.dashboard --results test/bench/results/ --out test/bench/dashboards/

설계:
- ``results/<YYYY-MM-DD>/runs.jsonl`` 다수 읽기
- 집계: (site, scenario, date) → success_rate (PASS / total)
- 출력: 단일 HTML 파일 (dashboards/index.html)
  - 행: 사이트 × 시나리오
  - 열: 일자 (최근 7일)
  - 셀: 색상 코딩 success rate
- ``unsupported: true`` 마킹: 7일 평균 flake rate > 30% 면 시나리오 metadata 갱신
  *권장* — 본 generator 는 *읽기만*, 마킹은 별 도구.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


log = logging.getLogger("bench.dashboard")


BENCH_DIR = Path(__file__).parent


@dataclass(frozen=True)
class CellStats:
    total: int
    passed: int

    @property
    def success_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    @property
    def flake_rate(self) -> float:
        return 1.0 - self.success_rate


def _load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("skip malformed line in %s: %s", path, e)
    return out


def _discover_jsonl(results_root: Path) -> list[tuple[str, Path]]:
    """``results/<date>/runs.jsonl`` 모두 발견 — 정렬된 (date, path) 목록."""
    out: list[tuple[str, Path]] = []
    if not results_root.is_dir():
        return out
    for day_dir in sorted(results_root.iterdir()):
        if not day_dir.is_dir():
            continue
        date_str = day_dir.name
        for jsonl in day_dir.glob("runs.jsonl"):
            out.append((date_str, jsonl))
    return out


def aggregate(
    results_root: Path,
    days_window: int = 7,
) -> tuple[dict, list[str]]:
    """JSONL 들 → {(site, scenario): {date: CellStats}} + 정렬된 dates.

    days_window: 최근 N일만 dashboard 에 표시 (기본 7).
    """
    today_utc = datetime.now(timezone.utc).date()
    window_start = today_utc - timedelta(days=days_window - 1)
    cutoff_str = window_start.isoformat()

    # {(site, scenario): {date: (total, passed)}}
    counts: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )

    for date_str, jsonl_path in _discover_jsonl(results_root):
        if date_str < cutoff_str:
            continue
        for row in _load_jsonl(jsonl_path):
            site = row.get("site", "?")
            scenario = row.get("scenario", "?")
            status = row.get("status", "ERROR")
            counts[(site, scenario)][date_str][0] += 1
            if status == "PASS":
                counts[(site, scenario)][date_str][1] += 1

    # ({(site,scenario): {date: CellStats}}, sorted dates list)
    out: dict[tuple[str, str], dict[str, CellStats]] = {}
    all_dates: set[str] = set()
    for key, per_date in counts.items():
        out[key] = {}
        for date_str, (total, passed) in per_date.items():
            out[key][date_str] = CellStats(total=total, passed=passed)
            all_dates.add(date_str)
    return out, sorted(all_dates)


def _cell_color(stats: Optional[CellStats]) -> str:
    """success_rate → CSS 배경색."""
    if stats is None or stats.total == 0:
        return "#eee"
    sr = stats.success_rate
    if sr >= 0.95:
        return "#cce7c9"  # 진한 초록
    if sr >= 0.80:
        return "#e8f3d6"  # 연한 초록
    if sr >= 0.50:
        return "#fde8b6"  # 노랑
    return "#f7c5c5"      # 빨강


def render_html(
    aggregated: dict[tuple[str, str], dict[str, CellStats]],
    dates: list[str],
) -> str:
    """집계 결과 → 정적 HTML 한 페이지."""
    rows = sorted(aggregated.keys())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not rows:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Bench Dashboard</title></head><body>"
            f"<h1>External SUT Bench Dashboard</h1>"
            f"<p><em>(no data — run flake_runner first)</em></p>"
            f"<p>generated at {now}</p></body></html>"
        )

    head_cells = "".join(
        f"<th>{d[5:]}</th>" for d in dates  # MM-DD 만 표시
    )
    body_rows: list[str] = []
    for site, scenario in rows:
        per_date = aggregated[(site, scenario)]
        # 7일 합산 — unsupported 마킹 후보 표시
        total_all = sum(c.total for c in per_date.values())
        passed_all = sum(c.passed for c in per_date.values())
        avg_sr = passed_all / total_all if total_all > 0 else 0.0
        avg_label = f"{avg_sr * 100:.0f}% ({passed_all}/{total_all})"
        unsupported_badge = (
            " <span style='color:#a00;font-weight:bold'>UNSUPPORTED</span>"
            if total_all >= 10 and avg_sr < 0.70
            else ""
        )
        cells = []
        for d in dates:
            stats = per_date.get(d)
            color = _cell_color(stats)
            text = (
                f"{stats.passed}/{stats.total}" if stats and stats.total > 0
                else "-"
            )
            cells.append(
                f"<td style='background:{color};text-align:center'>{text}</td>"
            )
        body_rows.append(
            f"<tr><th>{site}</th><th>{scenario}{unsupported_badge}</th>"
            f"{''.join(cells)}<td><strong>{avg_label}</strong></td></tr>"
        )

    body_html = "\n".join(body_rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>External SUT Bench Dashboard</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 1100px; margin: 2em auto; padding: 0 1em; }}
    h1 {{ margin-bottom: 0.2em; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1em; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 10px; font-size: 13px; }}
    thead th {{ background: #f5f5f5; }}
    tbody th {{ background: #fafafa; text-align: left; font-weight: normal; }}
    .meta {{ color: #666; font-size: 12px; }}
    .legend {{ margin: 0.5em 0; font-size: 12px; }}
    .legend span {{ display: inline-block; padding: 2px 8px; margin-right: 6px; border: 1px solid #aaa; }}
  </style>
</head>
<body>
  <h1>External SUT Bench Dashboard</h1>
  <p class="meta">generated at {now} · window: 최근 {len(dates)}일</p>
  <div class="legend">
    <span style="background:#cce7c9">≥95%</span>
    <span style="background:#e8f3d6">≥80%</span>
    <span style="background:#fde8b6">≥50%</span>
    <span style="background:#f7c5c5">&lt;50%</span>
    <span style="background:#eee">no data</span>
  </div>
  <table>
    <thead>
      <tr><th>site</th><th>scenario</th>{head_cells}<th>7일 평균</th></tr>
    </thead>
    <tbody>
{body_html}
    </tbody>
  </table>
  <p class="meta">
    7일 누적 N≥10 + 평균 성공률 &lt;70% 인 시나리오는 <strong>UNSUPPORTED</strong>
    배지가 표시된다.
  </p>
</body>
</html>
"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.dashboard",
        description="JSONL results → 정적 HTML dashboard.",
    )
    parser.add_argument(
        "--results", type=Path, default=BENCH_DIR / "results",
        help="JSONL 결과 루트 (default: test/bench/results/)",
    )
    parser.add_argument(
        "--out", type=Path, default=BENCH_DIR / "dashboards" / "index.html",
        help="출력 HTML 경로",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="dashboard 표시 일자 윈도우 (default: 7)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    aggregated, dates = aggregate(args.results, days_window=args.days)
    html = render_html(aggregated, dates)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    log.info("dashboard written → %s (rows=%d, dates=%d)",
             args.out, len(aggregated), len(dates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
