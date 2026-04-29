"""Phase 1 T1.7 — flag=off vs flag=on 비교 리포트 생성기.

입력:
  --golden <dir>           골든 시나리오 디렉토리 (test/grounding_eval/golden/)
  --off <dir>              flag=off 실행 산출물 루트 (per-page subdir)
  --on  <dir>              flag=on  실행 산출물 루트 (per-page subdir)
  --out <path>             출력 HTML 리포트 경로
  --json <path>            (옵션) 메트릭 JSON 출력 경로

각 page subdir 형식:
  <root>/<catalog_id>/scenario.json
  <root>/<catalog_id>/llm_calls.jsonl

산출:
  - HTML 리포트 (off vs on 페이지별 표 + 합계 요약)
  - JSON 메트릭 (CI 관찰용)

DoD 자동 판정:
  - "8종 이상에서 selector_accuracy ≥ 0.75" — 평가에 포함된 페이지 수 기준
  - "healer 호출 on/off 비율 ≤ 1/3"
  - "planner 응답시간 증가 ≤ +10초"

본 모듈은 결정론적 (LLM 호출 없음). 실제 실행은 run_grounding_eval.sh 가 담당.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .classifier import PageEval, evaluate_page


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_scenario(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Phase 1 산출물은 list[dict] 또는 {"steps": [...]} 두 형식 허용
    if isinstance(raw, dict) and "steps" in raw:
        return list(raw.get("steps") or [])
    if isinstance(raw, list):
        return raw
    return []


def evaluate_run(
    *, golden_dir: Path, run_root: Path,
) -> dict[str, PageEval]:
    """run_root 의 per-page 산출물을 골든 디렉토리와 비교.

    반환: {catalog_id: PageEval}
    """
    results: dict[str, PageEval] = {}
    for golden_path in sorted(golden_dir.glob("*.scenario.json")):
        spec = json.loads(golden_path.read_text(encoding="utf-8"))
        cid = spec.get("catalog_id") or golden_path.stem.split(".")[0]
        target_url = spec.get("target_url", "")
        golden_steps = list(spec.get("steps") or [])

        page_dir = run_root / cid
        observed_steps = load_scenario(page_dir / "scenario.json")
        calls = load_jsonl(page_dir / "llm_calls.jsonl")

        planner_calls = [c for c in calls if c.get("kind") == "planner"]
        healer_calls = [c for c in calls if c.get("kind") == "healer"]
        planner_elapsed = (
            sum(float(c.get("elapsed_ms") or 0) for c in planner_calls) / len(planner_calls)
        ) if planner_calls else 0.0
        # grounding 메타는 planner 레코드의 첫 항목에서
        gtokens: Optional[int] = None
        gtrunc: Optional[bool] = None
        gused: Optional[bool] = None
        if planner_calls:
            first = planner_calls[0]
            gtokens = first.get("grounding_inventory_tokens")
            gtrunc = first.get("grounding_truncated")
            gused = first.get("used") if "used" in first else first.get("grounding_used")
            if gused is None and gtokens is not None:
                gused = True

        results[cid] = evaluate_page(
            catalog_id=cid, target_url=target_url,
            golden_steps=golden_steps, observed_steps=observed_steps,
            healer_calls=len(healer_calls),
            planner_elapsed_ms=planner_elapsed,
            grounding_inventory_tokens=gtokens,
            grounding_truncated=gtrunc,
            grounding_used=gused,
        )
    return results


def compute_dod(off: dict[str, PageEval], on: dict[str, PageEval]) -> dict:
    """DoD 자동 판정 — off/on 페어가 모두 있는 페이지에 한해 계산."""
    pages = sorted(set(off) & set(on))
    accuracy_pass = 0
    rows = []
    for cid in pages:
        on_acc = on[cid].selector_accuracy()
        off_acc = off[cid].selector_accuracy()
        rows.append({
            "catalog_id": cid,
            "off_accuracy": off_acc,
            "on_accuracy": on_acc,
            "delta": on_acc - off_acc,
        })
        if on_acc >= 0.75:
            accuracy_pass += 1

    total_off_healer = sum(off[cid].healer_calls for cid in pages)
    total_on_healer = sum(on[cid].healer_calls for cid in pages)
    healer_ratio = (total_on_healer / total_off_healer) if total_off_healer else None

    avg_off_elapsed = (
        sum(off[cid].planner_elapsed_ms for cid in pages) / len(pages)
    ) if pages else 0.0
    avg_on_elapsed = (
        sum(on[cid].planner_elapsed_ms for cid in pages) / len(pages)
    ) if pages else 0.0
    elapsed_delta_ms = avg_on_elapsed - avg_off_elapsed

    over_budget_pages = sum(
        1 for cid in pages
        if (on[cid].grounding_inventory_tokens or 0) > 0
        and bool(on[cid].grounding_truncated)
    )
    over_budget_ratio = (over_budget_pages / len(pages)) if pages else 0.0

    return {
        "pages_evaluated": len(pages),
        "rows": rows,
        "accuracy_75_pct_pages": accuracy_pass,
        "healer_off_total": total_off_healer,
        "healer_on_total": total_on_healer,
        "healer_ratio_on_over_off": healer_ratio,
        "avg_planner_elapsed_off_ms": round(avg_off_elapsed, 1),
        "avg_planner_elapsed_on_ms": round(avg_on_elapsed, 1),
        "planner_elapsed_delta_ms": round(elapsed_delta_ms, 1),
        "over_budget_pages": over_budget_pages,
        "over_budget_ratio": round(over_budget_ratio, 3),
        "dod_checks": {
            "accuracy_8_or_more_pages_at_75pct": accuracy_pass >= 8,
            "healer_ratio_one_third_or_less": (
                healer_ratio is not None and healer_ratio <= (1.0 / 3.0)
            ),
            "elapsed_delta_within_10s": elapsed_delta_ms <= 10_000.0,
            "over_budget_ratio_within_10pct": over_budget_ratio <= 0.10,
        },
    }


# ── HTML 리포트 ───────────────────────────────────────────────────────────────


def _h(s) -> str:
    return html.escape(str(s) if s is not None else "")


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def render_html(
    *, off: dict[str, PageEval], on: dict[str, PageEval], dod: dict,
) -> str:
    pages = sorted(set(off) | set(on))
    rows_html = []
    for cid in pages:
        off_p = off.get(cid)
        on_p = on.get(cid)
        off_acc = _fmt_pct(off_p.selector_accuracy()) if off_p else "—"
        on_acc = _fmt_pct(on_p.selector_accuracy()) if on_p else "—"
        off_healer = off_p.healer_calls if off_p else "—"
        on_healer = on_p.healer_calls if on_p else "—"
        gtok = (on_p.grounding_inventory_tokens if on_p else None) or 0
        gtrunc = "Y" if (on_p and on_p.grounding_truncated) else "—"
        rows_html.append(
            f"<tr>"
            f"<td>{_h(cid)}</td>"
            f"<td>{off_acc}</td>"
            f"<td>{on_acc}</td>"
            f"<td>{_h(off_healer)}</td>"
            f"<td>{_h(on_healer)}</td>"
            f"<td>{_h(gtok)}</td>"
            f"<td>{_h(gtrunc)}</td>"
            f"</tr>"
        )

    dod_rows = []
    for k, v in dod.get("dod_checks", {}).items():
        mark = "✅" if v else "❌"
        dod_rows.append(f"<li>{mark} <code>{_h(k)}</code></li>")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>DOM Grounding 효과 측정 리포트</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 20px; }}
  h1 {{ color: #06c; }}
  table {{ border-collapse: collapse; margin: 16px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 12px; text-align: left; }}
  th {{ background: #f0f0f0; }}
  .summary {{ background: #fafaff; border-left: 4px solid #06c; padding: 12px 16px; margin: 16px 0; }}
  code {{ background: #eef; padding: 1px 4px; border-radius: 3px; }}
</style></head>
<body>
<h1>DOM Grounding 효과 측정 리포트 (Phase 1 T1.7)</h1>
<div class="summary">
  <p><b>평가 페이지 수</b>: {dod.get('pages_evaluated', 0)}</p>
  <p><b>selector_accuracy ≥ 75% 페이지 수</b>: {dod.get('accuracy_75_pct_pages', 0)}</p>
  <p><b>healer 호출 합계</b>: off={dod.get('healer_off_total', 0)} / on={dod.get('healer_on_total', 0)} (ratio = {dod.get('healer_ratio_on_over_off')})</p>
  <p><b>avg planner elapsed (ms)</b>: off={dod.get('avg_planner_elapsed_off_ms', 0)} / on={dod.get('avg_planner_elapsed_on_ms', 0)} (Δ = {dod.get('planner_elapsed_delta_ms', 0)} ms)</p>
  <p><b>토큰 예산 초과 페이지</b>: {dod.get('over_budget_pages', 0)} ({_fmt_pct(dod.get('over_budget_ratio', 0))})</p>
</div>
<h2>DoD 자동 판정</h2>
<ul>{''.join(dod_rows)}</ul>
<h2>페이지별 페어 비교</h2>
<table>
  <thead><tr>
    <th>catalog_id</th>
    <th>off accuracy</th>
    <th>on accuracy</th>
    <th>off healer</th>
    <th>on healer</th>
    <th>on inv tokens</th>
    <th>truncated?</th>
  </tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
</body></html>
"""


def _serialize_eval(p: PageEval) -> dict:
    d = asdict(p)
    d["selector_accuracy"] = p.selector_accuracy()
    d["partial_rate"] = p.partial_rate()
    return d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DOM Grounding off/on 페어 비교 리포트")
    parser.add_argument("--golden", required=True, help="골든 디렉토리 (test/grounding_eval/golden/)")
    parser.add_argument("--off",    required=True, help="flag=off 산출물 루트")
    parser.add_argument("--on",     required=True, help="flag=on  산출물 루트")
    parser.add_argument("--out",    required=True, help="HTML 리포트 출력 경로")
    parser.add_argument("--json",   default=None,  help="(옵션) JSON 메트릭 출력 경로")
    args = parser.parse_args(argv)

    gdir = Path(args.golden)
    off = evaluate_run(golden_dir=gdir, run_root=Path(args.off))
    on = evaluate_run(golden_dir=gdir, run_root=Path(args.on))
    dod = compute_dod(off, on)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(off=off, on=on, dod=dod), encoding="utf-8")
    print(f"[grounding-eval] HTML 리포트 → {out}")

    if args.json:
        jpath = Path(args.json)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dod": dod,
            "off": {k: _serialize_eval(v) for k, v in off.items()},
            "on": {k: _serialize_eval(v) for k, v in on.items()},
        }
        jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[grounding-eval] JSON 메트릭 → {jpath}")

    # 페어 페이지가 0 이면 비정상 종료 (CI 가시성)
    if dod.get("pages_evaluated", 0) == 0:
        print("[grounding-eval] off/on 페어 페이지 0 — 산출물 디렉토리 확인 필요", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
