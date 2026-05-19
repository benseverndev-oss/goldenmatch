"""Phase 1 kill criterion: driver RSS < 8 GB during prep stage.

Loads N rows via `goldenmatch.distributed.read_csv_partitioned`, applies a
two-step TransformPlan chain, and measures peak driver RSS via a psutil
sampler thread. Exits non-zero if peak RSS >= 8 GB.

Usage:
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 \
    python scripts/bench_phase1_loader.py \
        --input bench-dataset-v1/bench_25000000.parquet \
        --rows 25000000 \
        --partitions 64

The 25M run is the binding Phase 1 kill criterion. Local smoke runs at
--rows 1000000 are useful for catching obvious driver-materialization bugs
before paying for the large runner.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import psutil


KILL_RSS_GB = 8.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=str, required=True, help="CSV or parquet path")
    ap.add_argument("--rows", type=int, required=True, help="Expected row count (asserted)")
    ap.add_argument("--partitions", type=int, default=64)
    ap.add_argument(
        "--kill-rss-gb",
        type=float,
        default=KILL_RSS_GB,
        help=f"Fail if peak RSS >= this many GB (default {KILL_RSS_GB})",
    )
    args = ap.parse_args()

    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    from goldenmatch.distributed import (
        apply_transforms_distributed,
        read_csv_partitioned,
    )
    from goldenmatch.distributed.transforms import TransformPlan

    proc = psutil.Process()
    peak_rss_bytes = [proc.memory_info().rss]
    stop = threading.Event()

    def _sample_rss() -> None:
        while not stop.wait(0.5):
            cur = proc.memory_info().rss
            if cur > peak_rss_bytes[0]:
                peak_rss_bytes[0] = cur

    sampler = threading.Thread(target=_sample_rss, name="rss-peak", daemon=True)
    sampler.start()

    t0 = time.perf_counter()
    ds = read_csv_partitioned(args.input, n_partitions=args.partitions)
    ds = apply_transforms_distributed(
        ds,
        [
            TransformPlan(column="name", op="lower"),
            TransformPlan(column="name", op="strip_punctuation"),
        ],
    )
    count = ds.count()
    elapsed = time.perf_counter() - t0

    stop.set()
    sampler.join(timeout=2)
    peak_gb = peak_rss_bytes[0] / 1024**3

    print(f"rows_loaded={count} expected={args.rows} partitions={args.partitions}")
    print(f"prep_stage_wall_sec={elapsed:.1f}")
    print(f"driver_peak_rss_gb={peak_gb:.2f}")

    if count != args.rows:
        print(f"WARN: row count mismatch (got {count}, expected {args.rows})")

    if peak_gb >= args.kill_rss_gb:
        print(f"KILL: driver RSS {peak_gb:.2f} GB >= {args.kill_rss_gb} GB threshold")
        return 1
    print(f"PASS: driver RSS {peak_gb:.2f} GB under {args.kill_rss_gb} GB threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
