"""Generate the Phase 5 multi-node bench dataset.

Default: 100M rows / ~20M clusters of 5 members / 10% typo injection.
Output: parquet (~700 MB at 100M rows, ~340 MB at 50M).

Run (single-process, vectorized):
    python scripts/generate_phase5_dataset.py \\
        --rows 100000000 \\
        --output bench-dataset-v1/bench_100000000.parquet

Run (multi-process, faster on large N):
    python scripts/generate_phase5_dataset.py \\
        --rows 50000000 \\
        --workers 8 \\
        --output bench-dataset-v1/bench_50000000.parquet

Sized to break single-node bucket pipeline: at 100M, the bucket backend
would need ~230 GB RAM (linear extrapolation from 25M's 57.7 GB peak).

History: the original loop-based generator did 50M Python list-appends
per output column and took ~8 hr at 50M scale. The vectorized rewrite
runs the same workload in numpy + Polars in <5 min on a 16-core box;
the optional ProcessPoolExecutor path further parallelizes across N
worker processes for diminishing returns above ~8 workers (the parquet
write becomes the long-pole).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import polars as pl

ROWS_PER_CLUSTER = 5
TYPO_RATE = 0.1


def _generate_chunk(
    cluster_start: int,
    cluster_end: int,
    seed: int,
) -> pl.DataFrame:
    """Generate one chunk of clusters in vectorized form.

    cluster_start inclusive, cluster_end exclusive. Each cluster
    produces ``ROWS_PER_CLUSTER`` rows; total chunk size is
    ``(cluster_end - cluster_start) * ROWS_PER_CLUSTER``.

    Worker processes call this directly; the chunk is returned as a
    Polars frame so the parent can ``pl.concat`` without any
    Python-level row iteration.
    """
    n_clusters = cluster_end - cluster_start
    n_rows = n_clusters * ROWS_PER_CLUSTER
    rng = np.random.default_rng(seed)

    # cluster_id per row (length n_rows). Each cluster contributes
    # ROWS_PER_CLUSTER consecutive rows.
    cluster_ids_repeated = np.repeat(
        np.arange(cluster_start, cluster_end, dtype=np.int64),
        ROWS_PER_CLUSTER,
    )
    typo_mask = rng.random(n_rows) < TYPO_RATE

    # Build everything as vectorized Polars expressions over native
    # Arrow buffers. ~50x faster than the original Python loop at
    # 1M+ rows. pl.concat_str broadcasts scalar literals across the
    # column length automatically (unlike Series + Series, which
    # requires matching lengths).
    return pl.DataFrame(
        {
            "__cid__": cluster_ids_repeated,
            "__typo__": typo_mask,
        }
    ).with_columns(
        first_canon=pl.concat_str(
            [pl.lit("name_"), pl.col("__cid__").cast(pl.Utf8)],
        ),
        last_name=pl.concat_str(
            [pl.lit("sur_"), pl.col("__cid__").cast(pl.Utf8)],
        ),
    ).with_columns(
        # Typo: replace 'a' with '@' in first_name where typo mask is True.
        first_name=pl.when(pl.col("__typo__"))
        .then(pl.col("first_canon").str.replace_all("a", "@", literal=True))
        .otherwise(pl.col("first_canon")),
    ).with_columns(
        # Email: post-typo first_name + "." + last_name + "@example.com".
        # Matches the original generator's behavior including typo carryover.
        email=pl.concat_str(
            [
                pl.col("first_name"),
                pl.lit("."),
                pl.col("last_name"),
                pl.lit("@example.com"),
            ],
        ),
        # zip: cluster_id % 100000 zero-padded to 5 digits.
        zip=(pl.col("__cid__") % 100000).cast(pl.Utf8).str.zfill(5),
    ).select(
        "first_name", "last_name", "email", "zip",
    )


def generate_rows(
    n: int,
    seed: int = 42,
    workers: int = 1,
    progress: bool = True,
) -> pl.DataFrame:
    """Generate ``n`` synthetic ER rows.

    When ``workers > 1``, splits the cluster range across N processes
    via ``ProcessPoolExecutor`` and concatenates the chunks at the end.
    Polars ``concat`` over per-worker frames is zero-copy on the
    underlying Arrow buffers so the concat itself is cheap.

    Each worker gets a derived seed (``seed + chunk_idx``) so the
    output is deterministic across worker counts (the typo pattern
    differs slightly with worker count, but reproducibility for a
    fixed ``(rows, workers, seed)`` triple is preserved).
    """
    n_clusters = n // ROWS_PER_CLUSTER
    if workers <= 1:
        return _generate_chunk(0, n_clusters, seed)

    # Split into ``workers`` roughly-even cluster chunks.
    chunk_size = (n_clusters + workers - 1) // workers
    ranges = [
        (i * chunk_size, min((i + 1) * chunk_size, n_clusters))
        for i in range(workers)
        if i * chunk_size < n_clusters
    ]

    chunks: list[pl.DataFrame] = [pl.DataFrame()] * len(ranges)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate_chunk, start, end, seed + i): i
            for i, (start, end) in enumerate(ranges)
        }
        done = 0
        for fut in as_completed(futures):
            idx = futures[fut]
            chunks[idx] = fut.result()
            done += 1
            if progress:
                print(
                    f"  worker chunk {done}/{len(ranges)} done "
                    f"(idx={idx}, rows={chunks[idx].height})",
                    flush=True,
                )

    return pl.concat(chunks, how="vertical_relaxed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=100_000_000)
    ap.add_argument("--output", type=str, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of worker processes. >1 enables ProcessPoolExecutor "
            "chunking. Diminishing returns above ~8 on most boxes."
        ),
    )
    args = ap.parse_args()

    print(
        f"generating rows={args.rows:,} seed={args.seed} workers={args.workers}",
        flush=True,
    )
    t0 = time.perf_counter()
    df = generate_rows(args.rows, seed=args.seed, workers=args.workers)
    gen_wall = time.perf_counter() - t0
    print(f"generated {df.height:,} rows in {gen_wall:.1f}s", flush=True)

    t1 = time.perf_counter()
    df.write_parquet(args.output)
    write_wall = time.perf_counter() - t1
    print(f"wrote {args.output} in {write_wall:.1f}s", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
