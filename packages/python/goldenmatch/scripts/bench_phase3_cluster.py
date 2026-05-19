"""Phase 3 kill criterion: cluster stage < 60s at 25M pairs.

Loads pre-scored pairs from parquet, runs `build_clusters_distributed`
end-to-end, asserts cluster wall < 60s.

Run:
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 \
    python scripts/bench_phase3_cluster.py \
        --pairs bench-dataset-v1/pairs_25000000.parquet
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import psutil

KILL_WALL_SEC = 60.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pairs",
        type=str,
        required=True,
        help="Parquet path: columns id_a:int64, id_b:int64, score:float64",
    )
    ap.add_argument("--kill-wall-sec", type=float, default=KILL_WALL_SEC)
    args = ap.parse_args()

    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    from goldenmatch.distributed import read_parquet_partitioned
    from goldenmatch.distributed.clustering import build_clusters_distributed

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    t_load = time.perf_counter()
    pairs_ds = read_parquet_partitioned(args.pairs, n_partitions=64)
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
