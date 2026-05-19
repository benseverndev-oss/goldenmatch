"""Phase 2 kill criterion: controller iteration on 25M completes in < 30s.

Run:
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 \
    python scripts/bench_phase2_controller.py \
        --input bench-dataset-v1/bench_25000000.parquet \
        --rows 25000000
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import psutil

KILL_WALL_SEC = 30.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=str, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--partitions", type=int, default=64)
    ap.add_argument("--kill-wall-sec", type=float, default=KILL_WALL_SEC)
    args = ap.parse_args()

    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    from goldenmatch import auto_configure_df
    from goldenmatch.distributed import read_partitioned

    proc = psutil.Process()
    peak_rss = [proc.memory_info().rss]
    stop = threading.Event()

    def sample_rss() -> None:
        while not stop.wait(0.5):
            cur = proc.memory_info().rss
            if cur > peak_rss[0]:
                peak_rss[0] = cur

    t = threading.Thread(target=sample_rss, name="rss-peak", daemon=True)
    t.start()

    t_load = time.perf_counter()
    ds = read_partitioned(args.input, n_partitions=args.partitions)
    load_wall = time.perf_counter() - t_load

    t_ctrl = time.perf_counter()
    config = auto_configure_df(ds, confidence_required=False)
    controller_wall = time.perf_counter() - t_ctrl

    stop.set()
    t.join(timeout=2)
    peak_gb = peak_rss[0] / 1024**3

    print(f"rows={args.rows} partitions={args.partitions}")
    print(f"load_wall_sec={load_wall:.1f}")
    print(f"controller_wall_sec={controller_wall:.1f}")
    print(f"driver_peak_rss_gb={peak_gb:.2f}")
    print(f"config_committed={config is not None}")

    if controller_wall >= args.kill_wall_sec:
        print(f"KILL: controller wall {controller_wall:.1f}s >= {args.kill_wall_sec}s threshold")
        return 1
    print(f"PASS: controller wall {controller_wall:.1f}s under {args.kill_wall_sec}s threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
