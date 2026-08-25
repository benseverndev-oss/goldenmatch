"""What does auto-config's wall-clock budget cost in quality? Measure it.

WHY THIS EXISTS. `ControllerBudget.max_seconds` stops the controller's
iteration loop. When it binds, the committed config is whatever iteration 0
produced -- v0 -- however well-aimed the rules are. #2756 reported that as a
row-count-proxy problem: two lanes in the same `< 5_000` tier, one costing 2.2s
per iteration and the other 36.3s, both granted 15s.

The proxy IS a poor one. But the deeper defect is structural and no proxy can
fix it: **iteration 0 is the measurement pass.** It produces the profile every
rule reads, nothing can be proposed until it has run, and its cost is not
knowable in advance. Charging that mandatory cost against the budget that gates
OPTIONAL adaptive iterations means an expensive frame spends its whole
allowance on the one iteration it had no choice about.

This harness measures what that costs, which is the number that decides whether
the fix is worth its wall-clock.

THE PROTOCOL. Same lane, same data, same code -- only the budget varied.

* `full`      -- the shipped budget for this lane.
* `exhausted` -- `max_seconds` forced to ~0, so the cut fires as early as the
                 code allows. This is the floor: what the lane commits when
                 adaptation gets no turn at all.
* `slow0=N`   -- N seconds charged to iteration 0 ONLY, leaving the shipped
                 budget otherwise intact. This is the SHAPE #2756 describes: an
                 expensive measurement pass and cheap adaptive ones. It is the
                 variant that separates "the budget is mis-keyed" from "the
                 budget is being spent on mandatory work", because the proxy is
                 untouched and only the mandatory cost moves.

Measured 2026-08-25 on Amazon-Google (dedupe), the shape #2756 was filed on.
On `main` BEFORE the fix, with a 20s cost charged to iteration 0:

    variant        controller iterations   wall     F1
    slow0=20                           1  29.8s     0.1097   <- v0 by construction
    full (no slow0)                    4  23.1s     0.1490

AFTER the fix, from this harness:

    variant        controller iterations   wall     F1
    exhausted                          2  14.5s     0.1097
    full                               4  28.2s     0.1490
    slow0=20                           4  42.1s     0.1490
    adaptation is worth +0.0393 F1  (+35.8%)

So the adaptive iterations are worth **+36% F1** on this lane, and before the
fix an expensive measurement pass took all of them away.

Read the `exhausted` row carefully -- it is the honest limit of the fix. A
genuinely near-zero budget still grants exactly ONE adaptive iteration, and one
is not enough here. That also settles a loose end: #2748 tried a
minimum-iterations floor on this lane and recorded "+41% wall for zero F1".
That measurement was right and its conclusion was too broad -- the floor
granted exactly one extra iteration, and the gain here needs iterations 2-3.

    python scripts/controller_budget_cost.py --lane amazon_google --kind dedupe
    python scripts/controller_budget_cost.py --lane amazon_google --slow0 20 --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_DATASETS = (
    ROOT / "packages" / "python" / "goldenmatch" / "tests" / "benchmarks" / "datasets"
)

LANES: dict[str, dict] = {
    "abt_buy": dict(
        subdir="Abt-Buy",
        file_a="Abt.csv",
        file_b="Buy.csv",
        gt_file="abt_buy_perfectMapping.csv",
        gt_cols=("idAbt", "idBuy"),
        src_a="abt",
        src_b="buy",
        rename=None,
    ),
    "amazon_google": dict(
        subdir="Amazon-Google",
        file_a="Amazon.csv",
        file_b="GoogleProducts.csv",
        gt_file="Amzon_GoogleProducts_perfectMapping.csv",
        gt_cols=("idAmazon", "idGoogleBase"),
        src_a="amazon",
        src_b="google",
        rename={"name": "title"},
    ),
}


def _run_once(
    datasets_dir: Path, lane: str, kind: str, max_seconds: float | None, slow0: float
) -> dict:
    """One scored run, with the budget and iteration-0 cost under our control.

    Patching is done on the SHIPPED call path (`ControllerBudget.for_dataset`
    and `AutoConfigController._run_pipeline_sample`), and the lane is scored
    through the same helper the benchmark uses, so the only thing that differs
    between variants is the budget.
    """
    from goldenmatch.core import autoconfig_controller as ctrl

    orig_for_dataset = ctrl.ControllerBudget.for_dataset.__func__
    orig_sample = ctrl.AutoConfigController._run_pipeline_sample
    calls = {"n": 0}

    def _budget(cls, n_rows, effort="normal"):
        b = orig_for_dataset(cls, n_rows, effort)
        if max_seconds is None:
            return b
        return dataclasses.replace(b, max_seconds=max_seconds)

    def _sample(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1 and slow0 > 0:
            time.sleep(slow0)
        return orig_sample(self, *a, **kw)

    ctrl.ControllerBudget.for_dataset = classmethod(_budget)
    ctrl.AutoConfigController._run_pipeline_sample = _sample
    try:
        from dqbench_adapters.leipzig_eval import (
            run_two_source_dedupe_zeroconfig,
            run_two_source_link_zeroconfig,
        )
        from goldenmatch import dedupe_df, match_df

        runner = (
            run_two_source_dedupe_zeroconfig if kind == "dedupe" else run_two_source_link_zeroconfig
        )
        fn = dedupe_df if kind == "dedupe" else match_df
        t0 = time.time()
        result = runner(datasets_dir, fn, **LANES[lane])
        wall = time.time() - t0
    finally:
        ctrl.ControllerBudget.for_dataset = classmethod(orig_for_dataset)
        ctrl.AutoConfigController._run_pipeline_sample = orig_sample

    return {
        "controller_iterations": calls["n"],
        "wall_s": round(wall, 2),
        "f1": getattr(result, "f1", None),
        "precision": getattr(result, "precision", None),
        "recall": getattr(result, "recall", None),
    }


def run(datasets_dir: Path, lane: str, kind: str, slow0: float) -> dict:
    variants = {
        "exhausted": _run_once(datasets_dir, lane, kind, 0.001, 0.0),
        "full": _run_once(datasets_dir, lane, kind, None, 0.0),
    }
    if slow0 > 0:
        variants[f"slow0={slow0:g}"] = _run_once(datasets_dir, lane, kind, None, slow0)

    full = variants["full"]["f1"] or 0.0
    floor = variants["exhausted"]["f1"] or 0.0
    return {
        "lane": f"{lane} ({kind})",
        "variants": variants,
        "summary": {
            "f1_floor_no_adaptation": floor,
            "f1_with_full_budget": full,
            "adaptation_worth_f1": round(full - floor, 4),
            "adaptation_worth_pct": (round((full - floor) / floor, 4) if floor else None),
        },
    }


def report(result: dict) -> None:
    print("=" * 70)
    print(f"BUDGET COST: {result['lane']}")
    print("  same lane, same data, only the wall-clock budget varied")
    print("=" * 70)
    print(f"  {'variant':22s} {'iters':>5} {'wall':>8} {'F1':>8}")
    for name, v in result["variants"].items():
        print(
            f"  {name:22s} {v['controller_iterations']:5d} {v['wall_s']:7.1f}s {v['f1'] or 0:8.4f}"
        )
    s = result["summary"]
    print("-" * 70)
    pct = f"{s['adaptation_worth_pct']:+.1%}" if s["adaptation_worth_pct"] is not None else "n/a"
    print(f"  adaptation is worth {s['adaptation_worth_f1']:+.4f} F1  ({pct})")
    print("-" * 70)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lane", default="amazon_google", choices=sorted(LANES))
    ap.add_argument("--kind", default="dedupe", choices=["dedupe", "linkage"])
    ap.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    ap.add_argument(
        "--slow0",
        type=float,
        default=0.0,
        help="seconds charged to iteration 0 only (the #2756 shape)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not (args.datasets_dir / LANES[args.lane]["subdir"]).exists():
        print(f"dataset missing: {args.datasets_dir / LANES[args.lane]['subdir']}", file=sys.stderr)
        return 2

    result = run(args.datasets_dir, args.lane, args.kind, args.slow0)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
