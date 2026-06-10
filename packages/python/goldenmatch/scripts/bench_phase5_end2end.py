"""Phase 5 kill criterion: 100M end-to-end on multi-node Ray cluster.

Requires:
    RAY_ADDRESS=ray://head:10001    # pre-provisioned multi-node Ray cluster
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1
    GOLDENMATCH_DISTRIBUTED_PIPELINE=2

Run:
    python scripts/bench_phase5_end2end.py \
        --input bench-dataset-v1/bench_100000000.parquet \
        --output bench-out/phase5_golden.parquet

Kill criterion: total wall < 30 min.

This is the load-bearing Splink-Spark parity proof point. Single-node
runs at 100M would project to ~230 GB peak RSS (linear extrapolation
from 25M's 57.7 GB) — won't fit on the 64 GB bench runner. The
distributed pipeline is the only viable path at this scale.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psutil

KILL_WALL_SEC = 30 * 60  # 30 minutes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=str, required=True)
    ap.add_argument("--output", type=str, required=True)
    ap.add_argument("--kill-wall-sec", type=float, default=KILL_WALL_SEC)
    ap.add_argument(
        "--block-shuffle",
        choices=["0", "1"],
        default="0",
        help=(
            "1 = recall-complete leg: enables GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE "
            "and routes clustering to randomized_contraction WCC. "
            "Requires GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH to be a gs:// path "
            "(node-local scratch silently breaks cross-node parquet reads)."
        ),
    )
    args = ap.parse_args()

    block_shuffle = bool(int(args.block_shuffle))

    os.environ.setdefault("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")
    os.environ.setdefault("GOLDENMATCH_DISTRIBUTED_PIPELINE", "2")

    if block_shuffle:
        # HARD-set: these two flags define the recall-complete leg.
        os.environ["GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE"] = "1"
        os.environ["GOLDENMATCH_DISTRIBUTED_WCC"] = "randomized_contraction"
        # GCS scratch is REQUIRED on multi-node: a node-local path silently
        # breaks the cross-node parquet reads in the WCC per-round checkpoint.
        scratch = os.environ.get("GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH", "")
        if not scratch or not scratch.startswith("gs://"):
            print(
                "ERROR: --block-shuffle 1 requires "
                "GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH=gs://<bucket>/rc_scratch. "
                "A node-local scratch path silently breaks cross-node parquet reads "
                "in the WCC per-round checkpoint.",
                file=sys.stderr,
            )
            return 2

    from goldenmatch.distributed import read_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    t_load = time.perf_counter()
    ds = read_partitioned(args.input, n_partitions=64)
    load_wall = time.perf_counter() - t_load

    t_pipe = time.perf_counter()
    result = run_dedupe_pipeline_distributed(
        ds,
        confidence_required=False,
        output_path=args.output,
    )
    pipe_wall = time.perf_counter() - t_pipe
    total = time.perf_counter() - t_load

    peak_gb = proc.memory_info().rss / 1024**3

    # Count multi-member clusters from the golden parquet written by the
    # pipeline (one golden record per multi-member cluster). result.clusters
    # is intentionally empty ({}) to avoid the driver-wedge at 100M.
    multi_member_cluster_count: int | None = None
    try:
        import os as _os  # noqa: PLC0415

        import polars as pl  # noqa: PLC0415

        output_dir = args.output
        golden_parts = [
            f for f in _os.listdir(output_dir)
            if f.endswith(".parquet")
        ] if _os.path.isdir(output_dir) else []
        if golden_parts:
            multi_member_cluster_count = (
                pl.scan_parquet(f"{output_dir}/**/*.parquet")
                .select(pl.len())
                .collect()
                .item()
            )
    except Exception as exc:
        print(f"WARNING: could not count multi-member clusters: {exc}", file=sys.stderr)

    print(f"load_wall_sec={load_wall:.1f}")
    print(f"pipeline_wall_sec={pipe_wall:.1f}")
    print(f"total_wall_sec={total:.1f}")
    print(f"client_peak_rss_gb={peak_gb:.2f}")
    print(f"client_baseline_rss_gb={baseline / 1024**3:.2f}")
    print(f"clusters={len(result.clusters) if result else 0}")
    print(f"block_shuffle={block_shuffle}")
    print(f"multi_member_cluster_count={multi_member_cluster_count}")

    if total >= args.kill_wall_sec:
        print(f"KILL: total wall {total:.1f}s >= {args.kill_wall_sec}s")
        return 1
    print(f"PASS: total wall {total:.1f}s under {args.kill_wall_sec}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
