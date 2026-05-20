"""Generate the Phase 5 multi-node bench dataset.

Default: 100M rows / ~20M clusters of 5 members / 10% typo injection.
Output: parquet (~700 MB at 100M rows).

Run:
    python scripts/generate_phase5_dataset.py \
        --rows 100000000 \
        --output bench-dataset-v1/bench_100000000.parquet

Sized to break single-node bucket pipeline: at 100M, the bucket backend
would need ~230 GB RAM (linear extrapolation from 25M's 57.7 GB peak).
"""

from __future__ import annotations

import argparse
import random
import sys
import time

import polars as pl


def generate_rows(n: int, seed: int = 42) -> pl.DataFrame:
    """Synthetic ER fixture: ~5 members per cluster, 10% typo rate.

    Each cluster gets a unique (first_name, last_name) pair. Some rows
    have an `@` substituted for `a` to simulate transcription typos —
    the same kind of variation real ER workloads see.
    """
    rng = random.Random(seed)
    rows_per_cluster = 5
    n_clusters = n // rows_per_cluster

    first_pool = [f"name_{i}" for i in range(n_clusters)]
    last_pool = [f"sur_{i}" for i in range(n_clusters)]

    out_first: list[str] = []
    out_last: list[str] = []
    out_email: list[str] = []
    out_zip: list[str] = []

    for cid in range(n_clusters):
        fn_canon = first_pool[cid]
        ln_canon = last_pool[cid]
        for _ in range(rows_per_cluster):
            fn = fn_canon.replace("a", "@") if rng.random() < 0.1 else fn_canon
            ln = ln_canon
            out_first.append(fn)
            out_last.append(ln)
            out_email.append(f"{fn}.{ln}@example.com")
            out_zip.append(f"{cid % 100000:05d}")

    return pl.DataFrame({
        "first_name": out_first,
        "last_name": out_last,
        "email": out_email,
        "zip": out_zip,
    })


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=100_000_000)
    ap.add_argument("--output", type=str, required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    t0 = time.perf_counter()
    df = generate_rows(args.rows, seed=args.seed)
    gen_wall = time.perf_counter() - t0
    print(f"generated {df.height} rows in {gen_wall:.1f}s")

    t1 = time.perf_counter()
    df.write_parquet(args.output)
    write_wall = time.perf_counter() - t1
    print(f"wrote {args.output} in {write_wall:.1f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
