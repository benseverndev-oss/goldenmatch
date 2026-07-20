#!/usr/bin/env python
"""Isolated FS-path peak-RSS probe. One (scale) per subprocess so the OS
reclaims memory between runs and ru_maxrss is a clean high-water mark.

Mirrors the bench FS lane env EXACTLY (run_goldenmatch.py --mode probabilistic
+ orchestrate.py FS env): native FS kernel, posterior calibration, SN blocking
bound, EM sample cap. Adds a VmRSS sampler thread to catch the peak and dumps
the bench stage timings so we can see which stage holds the frames.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# --- env BEFORE importing goldenmatch (native loader + planner read these) ---
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
os.environ["GOLDENMATCH_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_CALIBRATED"] = "posterior"
os.environ["GOLDENMATCH_FS_BLOCKING_SN_BOUND"] = "1"
os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
os.environ.setdefault("GOLDENMATCH_FS_EM_SAMPLE_ROWS", "100000")

import resource


def _vmrss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


class Sampler(threading.Thread):
    def __init__(self, interval=0.05):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak = 0.0
        self._stopev = threading.Event()

    def run(self):
        while not self._stopev.is_set():
            self.peak = max(self.peak, _vmrss_mb())
            time.sleep(self.interval)

    def halt(self):
        self._stopev.set()
        self.join(timeout=1)


def main() -> None:
    path = Path(sys.argv[1])
    import pyarrow.parquet as pq

    from goldenmatch.core._native_loader import native_enabled, native_module
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.bench import bench_capture

    try:
        from goldenmatch import dedupe_df
    except ImportError:
        from goldenmatch._api import dedupe_df

    fs_symbol = bool(native_module() and hasattr(native_module(), "score_block_pairs_fs"))

    df = pq.read_table(path)
    n = df.num_rows
    rss_after_load = _vmrss_mb()

    cfg = auto_configure_probabilistic_df(df)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) == "weighted":
            mk.rerank = False

    sampler = Sampler()
    sampler.start()
    t0 = time.perf_counter()
    with bench_capture() as bench:
        ded = dedupe_df(df, config=cfg)
    wall = time.perf_counter() - t0
    sampler.halt()

    ru_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    bd = bench.to_dict() if hasattr(bench, "to_dict") else {}
    timings = bd.get("stage_timings_seconds") or {}
    peaks_kb = bd.get("stage_peak_rss_kb") or {}

    print(f"=== N={n:,} ===")
    print(f"fs_native_symbol_present={fs_symbol} block_scoring_native={native_enabled('block_scoring')}")
    print(f"rss_after_load_mb={rss_after_load:.0f}")
    print(f"dedupe_wall_s={wall:.2f}")
    print(f"peak_rss_sampled_mb={sampler.peak:.0f}  ru_maxrss_mb={ru_peak:.0f}")
    print(f"peak_over_baseload_mb={sampler.peak - rss_after_load:.0f}")
    allk = sorted(set(timings) | set(peaks_kb),
                  key=lambda k: -(peaks_kb.get(k, 0)))
    print(f"{'stage':34s} {'wall_s':>8s} {'peak_rss_mb':>12s}")
    for k in allk:
        t = timings.get(k)
        p = peaks_kb.get(k, 0) / 1024.0
        ts = f"{t:.2f}" if isinstance(t, (int, float)) else "-"
        print(f"  {k:32s} {ts:>8s} {p:>12.0f}")


if __name__ == "__main__":
    main()
