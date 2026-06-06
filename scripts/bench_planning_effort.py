"""A/B demonstration: planning-effort `thinking` routes skewed data correctly.

The win that the `thinking`/`einstein` tiers buy (spec 2026-06-06 §Phase 1):
on skewed blocking, a uniform sample's pair count is a *quadratic* under-estimate
of the real load, so the old linear extrapolation routes the run to an in-memory
plan that the true workload would blow past. Measuring real blocking on the full
frame sees the true load and routes to the RAM-safe out-of-core plan instead.

This script makes that concrete: it builds a dataset with one dominant block,
then shows the planner pick a DIFFERENT (heavier, safe) backend once it measures
instead of extrapolates.

    python scripts/bench_planning_effort.py            # defaults: 40k rows, 12k block
    python scripts/bench_planning_effort.py --rows 60000 --dominant 16000
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import polars as pl

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def build_skewed(n_rows: int, dominant: int, seed: int = 0) -> pl.DataFrame:
    """One dominant blocking-key value + a long tail of small ones."""
    tail = n_rows - dominant
    last = ["SMITH"] * dominant + [f"name{i % max(1, tail // 6)}" for i in range(tail)]
    return pl.DataFrame({
        "last": last,
        "email": [f"r{i}@example.com" for i in range(n_rows)],
    })


def plan_backend(blocking_profile, n_rows: int):
    """Run the real v3 planner over a profile carrying the given blocking signal."""
    from goldenmatch.core.autoconfig_planner import apply_planner_rules
    from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
    from goldenmatch.core.complexity_profile import ComplexityProfile
    from goldenmatch.core.runtime_profile import RuntimeProfile

    # Fix the runtime so the demo is deterministic across boxes: a fat box with
    # plenty of RAM (so the heavy rung is `chunked`, not the <16GB `duckdb`).
    runtime = RuntimeProfile(available_ram_gb=64.0, cpu_count=8, disk_free_gb=500.0)
    profile = dataclasses.replace(ComplexityProfile(), blocking=blocking_profile)
    return apply_planner_rules(
        profile=profile, runtime=runtime, n_rows_full=n_rows,
        rules=DEFAULT_RULES, context={"user_backend": None},
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=40_000)
    ap.add_argument("--dominant", type=int, default=12_000)
    ap.add_argument("--sample", type=int, default=2_000, help="controller sample size (normal tier)")
    args = ap.parse_args()

    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.blocker import measure_blocking_profile

    df = build_skewed(args.rows, args.dominant)
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="exact", fields=[MatchkeyField(field="email")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["last"])]),
    )

    # normal: linear extrapolation from a uniform sample (the prior default).
    sample_prof = measure_blocking_profile(df.sample(args.sample, seed=0), cfg)
    extrapolated = sample_prof.extrapolate_to(n_rows_sample=args.sample, n_rows_full=args.rows)
    # thinking/einstein: measure real blocking on the full frame (Phase 1).
    measured = measure_blocking_profile(df, cfg)

    plan_normal = plan_backend(extrapolated, args.rows)
    plan_thinking = plan_backend(measured, args.rows)

    print("=" * 74)
    print(f"Skewed dataset: {args.rows:,} rows, one dominant block of {args.dominant:,}")
    print("=" * 74)
    print(f"{'tier':10} {'pair-count basis':18} {'candidate pairs':>16}  {'backend':9} rule")
    print(f"{'normal':10} {'extrapolate(2k)':18} {extrapolated.estimated_pair_count:>16,}  "
          f"{plan_normal.backend:9} {plan_normal.rule_name}")
    print(f"{'thinking':10} {'measure(full)':18} {measured.estimated_pair_count:>16,}  "
          f"{plan_thinking.backend:9} {plan_thinking.rule_name}")
    print("-" * 74)
    ratio = measured.estimated_pair_count / max(1, extrapolated.estimated_pair_count)
    flipped = plan_normal.rule_name != plan_thinking.rule_name
    print(f"extrapolation under-counts the true load by {ratio:.1f}x")
    if flipped:
        print(f"ROUTING FLIP: normal -> '{plan_normal.backend}' (would choke on the real load); "
              f"thinking -> '{plan_thinking.backend}' (RAM-safe out-of-core). The brain works.")
    else:
        print("No routing flip at this size — increase --dominant to cross the 50M-pair line.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
