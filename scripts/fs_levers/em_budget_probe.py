"""Would an `em_iterations` lever help, on the one cell that motivated it?

WHY THIS EXISTS. #2637 argues that `em_iterations` / `convergence_threshold`
are a "detects and cannot act" defect: the engine emits `EM did not converge
after 20 iterations (delta=0.001560)`, that warning fired on exactly one cell
of six -- `historical_50k` at `levels=5`, which collapsed to F1 0.4803 -- and
nothing in the closed `ConfigEdit` vocabulary can raise the budget in response.
The issue's own words: *"there is a clean, rare, directional distress signal
attached to them, which is exactly the trigger such a lever would need."*

That argument has a load-bearing assumption nobody had tested: that raising the
budget MOVES the cell. If non-convergence is a symptom of a misspecified model
rather than a solver that ran out of road, the lever is noise-chasing -- which
is the position #2637 says it previously held and abandoned on the strength of
the trigger's existence.

THE PROTOCOL. One dataset, one cell, only `em_iterations` varied. Each run
captures the convergence warning through a log handler so "did it converge" is
observed rather than assumed. Includes `em_iterations=1` as a KNOWN-POSITIVE:
a budget of one cannot converge, so if that run does not report the warning the
handler is broken and every "converged" reading in the sweep is worthless.

MEASURED 2026-08-25 (`historical_50k`, `--row-cap 20000`, `levels=5`):

    em_iterations   converged   F1        precision   recall
                1   NO          0.5109    0.3555      0.9075
               20   yes         0.4744    0.3282      0.8557
              100   yes         0.4744    0.3282      0.8557

Three findings, and all three point the same way.

1. **Raising the budget changes nothing.** 20 -> 100 is identical to four
   decimal places, on every metric.
2. **The trigger does not fire on this cell at the default budget.** It reports
   `converged=True` at 20, so the distress signal #2637 built its case on does
   not reproduce here. (The issue measured it on 2026-08-17; something between
   then and now changed it. Recorded rather than explained.)
3. **Less EM is BETTER.** `em_iterations=1` -- maximally unconverged, the state
   the proposed lever exists to escape -- scores the highest F1 of the three.

So the direction is inverted. The cell is not a solver that needs more road; it
is a model misspecified at `levels=5`, and EM converges confidently onto a bad
solution. An "increase the budget when EM does not converge" lever would move
F1 the WRONG WAY on the only cell that motivated it.

That is the same shape as the argument #2637 itself makes for NOT adding a
`LevelsEdit`: *"the signal that looks like its trigger points the wrong way."*
It applies here too.

Note the failure mode this cell actually has: precision 0.33 at recall 0.86 is
heavy over-merging, which is a threshold and level-specification problem, not an
EM budget problem.

    python scripts/fs_levers/em_budget_probe.py --levels 5 --row-cap 20000
    python scripts/fs_levers/em_budget_probe.py --grid 1 20 100 --json out.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _ConvergenceCatcher(logging.Handler):
    """Records the engine's own non-convergence warning.

    Observed rather than inferred: the whole question is whether a runtime
    signal predicts anything, so re-deriving "did it converge" from the config
    would beg it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "did not converge" in message:
            self.messages.append(message)


def run_cell(df, gt, base_cfg, levels: int, iterations: int) -> dict:
    import goldenmatch
    from goldenmatch.core.evaluate import evaluate_clusters

    cfg = base_cfg.model_copy(deep=True)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) != "probabilistic":
            continue
        mk.em_iterations = iterations
        for field in mk.fields or []:
            field.levels = levels

    catcher = _ConvergenceCatcher()
    logger = logging.getLogger("goldenmatch")
    logger.addHandler(catcher)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    started = time.perf_counter()
    try:
        result = goldenmatch.dedupe_df(df, config=cfg)
        summary = evaluate_clusters(result.clusters, gt).summary()
        return {
            "em_iterations": iterations,
            "levels": levels,
            "converged": not catcher.messages,
            "warning": catcher.messages[-1] if catcher.messages else None,
            "f1": summary["f1"],
            "precision": summary["precision"],
            "recall": summary["recall"],
            "seconds": round(time.perf_counter() - started, 1),
        }
    finally:
        logger.removeHandler(catcher)
        logger.setLevel(previous)


def run(dataset: str, levels: int, row_cap: int, grid: list[int]) -> dict:
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    from scripts.suggest_quality.datasets import REGISTRY

    spec = {d.name: d for d in REGISTRY}.get(dataset)
    if spec is None:
        raise SystemExit(f"unknown dataset {dataset!r}")
    loaded = spec.loader()
    if loaded is None:
        raise SystemExit(f"{dataset}: data not present locally")
    df, gt = loaded
    if row_cap and df.height > row_cap:
        df = df.head(row_cap)
        gt = {(a, b) for a, b in gt if a < row_cap and b < row_cap}

    print(f"{dataset} rows={df.height} gt_pairs={len(gt)} levels={levels}", flush=True)
    base_cfg = auto_configure_probabilistic_df(df)
    rows = [run_cell(df, gt, base_cfg, levels, n) for n in grid]
    for r in rows:
        print(
            f"  em_iterations={r['em_iterations']:5d}  converged={str(r['converged']):5s}"
            f"  f1={r['f1']:.4f}  P={r['precision']:.4f}  R={r['recall']:.4f}"
            f"  {r['seconds']:6.1f}s",
            flush=True,
        )

    known_positive = next((r for r in rows if r["em_iterations"] == 1), None)
    if known_positive is not None and known_positive["converged"]:
        print(
            "  !! KNOWN-POSITIVE FAILED: em_iterations=1 reported convergence, so "
            "the log handler is not catching the warning and every 'converged' "
            "reading above is meaningless.",
            flush=True,
        )

    f1s = {r["em_iterations"]: r["f1"] for r in rows}
    return {
        "dataset": dataset,
        "levels": levels,
        "rows": df.height,
        "cells": rows,
        "summary": {
            "f1_spread": round(max(f1s.values()) - min(f1s.values()), 4),
            "best_iterations": max(f1s, key=lambda k: f1s[k]),
            "known_positive_fired": (not known_positive["converged"] if known_positive else None),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="historical_50k")
    ap.add_argument("--levels", type=int, default=5)
    ap.add_argument("--row-cap", type=int, default=20000)
    ap.add_argument(
        "--grid",
        nargs="+",
        type=int,
        default=[1, 20, 100],
        help="em_iterations values; keep 1 as the known-positive",
    )
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    result = run(args.dataset, args.levels, args.row_cap, args.grid)
    s = result["summary"]
    print(
        f"  F1 spread across the grid: {s['f1_spread']:.4f}  "
        f"best at em_iterations={s['best_iterations']}",
        flush=True,
    )
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
