"""Auto-config quality harness CLI.

    python -m scripts.autoconfig_quality report   # diff vs baseline (iterate loop)
    python -m scripts.autoconfig_quality gate     # exit nonzero on regression (CI)
    python -m scripts.autoconfig_quality bless     # accept current as the baseline

Flags: --fast-only (skip the F1 tier), --datasets a,b (filter), --row-cap N
(F1 tractability), --native {0,1,auto} (run pure-Python / native / default),
--tolerance F (real-dataset F1 floor band, default 0.01).
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
from pathlib import Path

# Polars CPU probe can hang on Windows; set before anything imports polars.
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_BASELINE = Path(__file__).resolve().parent / "baselines" / "scorecard.json"


def run(dataset_names: set[str] | None, fast_only: bool, row_cap: int | None):
    """Run the corpus -> (results, skipped). Heavy imports are deferred so
    --native can set GOLDENMATCH_NATIVE before goldenmatch loads."""
    from scripts.autoconfig_quality.datasets import REGISTRY, effective_row_cap
    from scripts.autoconfig_quality.f1 import evaluate_f1
    from scripts.autoconfig_quality.signals import extract_signals

    results: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for d in REGISTRY:
        if dataset_names and d.name not in dataset_names:
            continue
        try:
            loaded = d.loader()
        except Exception as e:  # loader failure -> skip with reason, never crash
            skipped[d.name] = f"loader_error: {e}"
            continue
        if loaded is None:
            skipped[d.name] = "absent"
            continue
        df, gt = loaded
        rec: dict = {"kind": d.kind}
        try:
            rec["signals"] = extract_signals(df)
        except Exception as e:
            rec["signals"] = {"error": str(e)}  # anchor->FAIL, real->neutral (per diff)
        if not fast_only and gt:
            cap = effective_row_cap(d, row_cap)
            try:
                rec["f1"] = evaluate_f1(df, gt, row_cap=cap)
            except Exception as e:
                rec["error"] = str(e)  # real F1 error -> neutral (per diff)
            try:  # second strategy: forced Fellegi-Sunter (the routing-lever evidence)
                rec["f1_probabilistic"] = evaluate_f1(df, gt, row_cap=cap, strategy="probabilistic")
            except Exception as e:
                rec["error_probabilistic"] = str(e)  # informational; floored only when present
        results[d.name] = rec
        # Release each dataset's frame + dedupe intermediates before the next one.
        # The corpus runs in ONE process; without this, a big dataset's dedupe
        # (e.g. historical_50k at 50k rows) can fail to allocate on a memory-tight
        # runner because earlier datasets' frames are still held.
        del loaded, df, gt
        gc.collect()
    return results, skipped


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autoconfig_quality")
    p.add_argument("mode", nargs="?", default="report", choices=["report", "gate", "bless"])
    p.add_argument("--fast-only", action="store_true", help="skip the F1 tier")
    p.add_argument("--datasets", default="", help="comma-separated dataset filter")
    p.add_argument("--row-cap", type=int, default=20_000, help="F1-tier row cap")
    p.add_argument("--native", choices=["0", "1", "auto"], default=None,
                   help="GOLDENMATCH_NATIVE for this run")
    p.add_argument("--tolerance", type=float, default=0.01, help="real-dataset F1 floor band")
    args = p.parse_args(argv)

    if args.native is not None:
        os.environ["GOLDENMATCH_NATIVE"] = args.native  # before goldenmatch import

    from scripts.autoconfig_quality.diff import diff_scorecards, render_table
    from scripts.autoconfig_quality.scorecard import (
        build_scorecard,
        dumps,
        gather_meta,
        loads,
    )

    names = {s for s in args.datasets.split(",") if s} or None
    results, skipped = run(names, args.fast_only, args.row_cap)
    native_version, git_sha = gather_meta()
    current = build_scorecard(results, native_version=native_version,
                              git_sha=git_sha, skipped=skipped)

    if args.mode == "bless":
        _BASELINE.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE.write_text(dumps(current), encoding="utf-8")
        print(f"Blessed baseline -> {_BASELINE} ({len(results)} datasets, "
              f"{len(skipped)} skipped)")
        return 0

    baseline = loads(_BASELINE.read_text(encoding="utf-8")) if _BASELINE.exists() else {"datasets": {}}
    rows, verdict = diff_scorecards(current, baseline, tolerance=args.tolerance)
    print(render_table(rows))
    print(f"\nverdict: {verdict}  ({len(results)} run, {len(skipped)} skipped)")
    if skipped:
        print("skipped: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()))

    if args.mode == "gate":
        return 0 if verdict == "PASS" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
