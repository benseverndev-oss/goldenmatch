#!/usr/bin/env python3
"""Measure GoldenMatch's real candidate-gather sparsity (block size / N).

The Lance-vs-Parquet bake-off (scripts/bench_lance_vs_parquet.py) found Lance's
random `take` beats Parquet only when the gather is very sparse: ~6x at
K/N=1e-4, parity by 1e-3, a loss by 1e-2. So whether a Lance-backed on-disk
candidate-retrieval path would pay off reduces to ONE empirical question:

    how sparse is a real block relative to the dataset?  (K/N, where K = block
    size, N = total rows)

This script answers it by running the ACTUAL blocker (auto-configured) on a
representative person dataset and reporting the K/N distribution, both
block-count-weighted and MEMBER-weighted (member-weighted is what matters —
scoring cost and rows-gathered concentrate in the big blocks, not the many
tiny ones).

Maps each block onto the bake-off crossover:
    K/N <= 1e-4  -> Lance big win (~6x)
    1e-4..1e-3   -> marginal / parity
    > 1e-3       -> Lance loss

Usage:
    python scripts/measure_ann_gather_sparsity.py --rows 100000 1000000
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from pathlib import Path

# Keep auto-config hermetic/offline for a measurement run.
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

# Make the in-repo fixture generator importable.
_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG / "tests" / "fixtures"))

# Crossover thresholds read straight off the bake-off curve.
WIN = 1e-4   # <= this: Lance ~6x
PAR = 1e-3   # <= this: marginal; above: Lance loses


def _bucket(kn: float) -> str:
    if kn <= WIN:
        return "win (<=1e-4)"
    if kn <= PAR:
        return "marginal (<=1e-3)"
    return "loss (>1e-3)"


def measure(rows: int) -> None:
    import polars as pl  # noqa: F401
    from goldenmatch import auto_configure_df
    from goldenmatch.core.blocker import build_blocks
    from realistic_person import realistic_person_df

    df = realistic_person_df(rows)
    n = df.height
    config = auto_configure_df(df)
    # The pipeline injects __row_id__ before blocking; some strategies
    # (learned/ann) read it. Add it for a standalone build_blocks call.
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__")
    blocking = getattr(config, "blocking", None)
    if blocking is None:
        print(f"rows={rows:,}: auto-config produced no blocking; skipping")
        return

    if blocking.passes:
        keys = [list(k.fields) for k in blocking.passes]
    elif blocking.keys:
        keys = [list(k.fields) for k in blocking.keys]
    else:
        keys = []

    blocks = build_blocks(df.lazy(), blocking)
    sizes = []
    for b in blocks:
        try:
            sizes.append(b.df.select(pl.len()).collect().item())
        except Exception:
            pass
    # Only multi-record blocks generate candidate pairs / gathers worth a take.
    sizes = [s for s in sizes if s >= 2]
    if not sizes:
        print(f"rows={rows:,}: no multi-record blocks")
        return

    sizes.sort()
    total_members = sum(sizes)

    def pct(p: float) -> int:
        i = min(len(sizes) - 1, int(p * len(sizes)))
        return sizes[i]

    p50, p95, p99, mx = pct(0.50), pct(0.95), pct(0.99), sizes[-1]

    # Block-count-weighted and member-weighted bucket shares.
    by_count = {"win (<=1e-4)": 0, "marginal (<=1e-3)": 0, "loss (>1e-3)": 0}
    by_member = {"win (<=1e-4)": 0, "marginal (<=1e-3)": 0, "loss (>1e-3)": 0}
    for s in sizes:
        b = _bucket(s / n)
        by_count[b] += 1
        by_member[b] += s

    # Member-weighted median K/N: the K/N a randomly chosen GATHERED row sees.
    member_kn = []
    for s in sizes:
        member_kn.extend([s / n] * min(s, 1000))  # cap expansion for huge blocks
    member_median_kn = statistics.median(member_kn)

    print("=" * 70)
    print(f"rows N = {n:,}   keys = {keys}")
    print(f"multi-record blocks = {len(sizes):,}   total gathered members = {total_members:,}")
    print(f"block size:  p50={p50}  p95={p95}  p99={p99}  max={mx}")
    print(f"K/N      :  p50={p50/n:.2e}  p95={p95/n:.2e}  p99={p99/n:.2e}  max={mx/n:.2e}")
    print(f"member-weighted median K/N = {member_median_kn:.2e}  -> {_bucket(member_median_kn)}")
    print("-" * 70)
    print(f"{'bucket':<20} {'% of blocks':>14} {'% of gathered rows':>20}")
    for b in ("win (<=1e-4)", "marginal (<=1e-3)", "loss (>1e-3)"):
        print(
            f"{b:<20} {100*by_count[b]/len(sizes):>13.1f}% "
            f"{100*by_member[b]/total_members:>19.1f}%"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, nargs="+", default=[100_000, 1_000_000])
    args = ap.parse_args()
    print("Gather-sparsity vs Lance crossover (win<=1e-4, marginal<=1e-3, loss>1e-3)\n")
    for r in args.rows:
        measure(r)
    print("=" * 70)
    print("Read with the bake-off: a high MEMBER-weighted share in 'loss' means the")
    print("rows that actually get gathered live in dense blocks where Parquet wins;")
    print("a high share in 'win' means a Lance take-path would pay off on real work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
