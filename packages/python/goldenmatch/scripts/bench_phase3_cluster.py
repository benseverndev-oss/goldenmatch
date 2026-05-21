"""Phase 3 kill criterion: cluster stage < 60s at 25M pairs.

Two modes:

1. ``--pairs <parquet>`` — clusters a pre-scored pairs parquet. Columns:
   ``id_a:int64, id_b:int64, score:float64``. Fast path for repeated bench
   runs once the score artifact is built.

2. ``--input <parquet>`` — raw records parquet (the bench-dataset-v1 shape:
   ``first_name``, ``last_name``, ``email``, ``zip``). The script runs
   ``goldenmatch.dedupe_df`` to score in-memory, then times JUST the
   distributed cluster stage on the resulting pair list. The pre-score
   time is reported separately as ``prescore_wall_sec``; the kill
   criterion is on ``cluster_wall_sec``.

Run:
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 \
    python scripts/bench_phase3_cluster.py \
        --input bench-dataset-v1/bench_25000000.parquet
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psutil

KILL_WALL_SEC = 60.0


def _prescore(input_path: str) -> tuple[list[tuple[int, int, float]], float]:
    """Run dedupe_df to produce scored pairs. Returns (pairs, wall_sec).

    Forces backend="bucket" — the supported 25M-on-64GB single-node path
    (run 26095134836: 6.5 min / 57.7 GB peak RSS). Without this, dedupe_df
    auto-picks the polars backend, which OOMs at 25M (run 26118400787
    SIGTERM'd at the 16 min mark).

    The bench is measuring the DISTRIBUTED cluster stage, not the scorer.
    Pre-score time is reported separately as prescore_wall_sec.
    """
    import goldenmatch as gm
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df

    t0 = time.perf_counter()
    df = pl.read_parquet(input_path)

    # Mirror bench_distributed_stack pattern: auto-configure first with
    # _skip_finalize so the controller commits a config but doesn't run
    # the pipeline yet; override backend to "bucket"; then run dedupe_df.
    cfg = auto_configure_df(df, confidence_required=False, _skip_finalize=True)
    cfg.backend = "bucket"
    result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    elapsed = time.perf_counter() - t0
    return result.scored_pairs, elapsed


def _write_pairs_parquet(
    pairs: list[tuple[int, int, float]], path: str
) -> None:
    import polars as pl

    pl.DataFrame(
        {
            "id_a": [a for a, _, _ in pairs],
            "id_b": [b for _, b, _ in pairs],
            "score": [s for _, _, s in pairs],
        }
    ).write_parquet(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--pairs",
        type=str,
        help="Parquet of pre-scored pairs (id_a, id_b, score).",
    )
    src.add_argument(
        "--input",
        type=str,
        help="Raw records parquet; the bench will pre-score it via dedupe_df.",
    )
    ap.add_argument(
        "--scratch-pairs",
        type=str,
        default="bench-out/phase3_pairs.parquet",
        help="Where to write the intermediate pairs parquet when using --input.",
    )
    ap.add_argument("--kill-wall-sec", type=float, default=KILL_WALL_SEC)
    args = ap.parse_args()

    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    from goldenmatch.distributed import read_parquet_partitioned
    from goldenmatch.distributed.clustering import build_clusters_distributed

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    prescore_wall = 0.0
    if args.input is not None:
        os.makedirs(os.path.dirname(args.scratch_pairs) or ".", exist_ok=True)
        pairs, prescore_wall = _prescore(args.input)
        _write_pairs_parquet(pairs, args.scratch_pairs)
        pairs_path = args.scratch_pairs
        print(f"prescore_wall_sec={prescore_wall:.1f}")
        print(f"pairs_written={len(pairs)} -> {pairs_path}")
    else:
        pairs_path = args.pairs

    t_load = time.perf_counter()
    pairs_ds = read_parquet_partitioned(pairs_path, n_partitions=64)
    ids: set[int] = set()
    for row in pairs_ds.take_all():
        ids.add(row["id_a"])
        ids.add(row["id_b"])
    all_ids = list(ids)
    load_wall = time.perf_counter() - t_load

    t_cluster = time.perf_counter()
    clusters_ds = build_clusters_distributed(pairs_ds, all_ids=all_ids)
    n_rows = clusters_ds.count()
    cluster_wall = time.perf_counter() - t_cluster

    peak_gb = proc.memory_info().rss / 1024**3

    print(f"members={n_rows} ids={len(all_ids)}")
    print(f"load_wall_sec={load_wall:.1f}")
    print(f"cluster_wall_sec={cluster_wall:.1f}")
    print(f"driver_peak_rss_gb={peak_gb:.2f}")
    print(f"baseline_rss_gb={baseline / 1024**3:.2f}")

    if cluster_wall >= args.kill_wall_sec:
        print(f"KILL: cluster wall {cluster_wall:.1f}s >= {args.kill_wall_sec}s")
        return 1
    print(f"PASS: cluster wall {cluster_wall:.1f}s under {args.kill_wall_sec}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
