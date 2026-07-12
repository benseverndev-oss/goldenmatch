"""Reusable scan perf harness: wall (5-run median, native + fallback) + a Polars
reference + a cProfile top-N. Run each perf iteration to track the hotspot.

  python scripts/perf_scan.py            # wall only
  python scripts/perf_scan.py --profile  # + cProfile top-15 by tottime
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import statistics
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

N = 1_000_000
TMP = Path(__file__).parent / "_perf_scan.parquet"


def _make() -> Path:
    r = np.random.default_rng(7)
    tbl = pa.table({
        "id": np.arange(N, dtype=np.int64),
        "score": r.normal(100, 15, N),
        "amount": np.floor(10 ** r.uniform(0, 6, N)).astype(np.int64) + 1,
        "region": np.array(["north", "south", "east", "west"])[r.integers(0, 4, N)],
        "email": np.array([f"u{i % 50000}@x.com" for i in range(N)]),
        "code": np.array([str(v) for v in r.integers(0, 1_000_000, N)]),  # numeric-looking str
        "flag": r.integers(0, 2, N).astype(bool),
    })
    pq.write_table(tbl, TMP)
    return TMP


def _median(fn, runs=5):
    ts = []
    for _ in range(runs):
        t = time.perf_counter(); n = fn(); ts.append(time.perf_counter() - t)
    return statistics.median(ts), min(ts), n


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--profile", action="store_true"); a = ap.parse_args()
    p = _make()
    from goldencheck.engine.scanner import scan_file
    print(f"scan_file wall, {N:,} rows x 7 cols (5-run median):")
    med, mn, n = _median(lambda: len(scan_file(p)[0]))
    print(f"  scan_file            median {med:6.3f}s (min {mn:.3f})  {n} findings")

    # Polars reference: same column profilers over a PolarsFrame
    import polars as pl
    from goldencheck.core.frame import PolarsFrame
    from scripts.flip_differential import _run_seam
    cols = pl.read_parquet(p).columns
    med2, mn2, _ = _median(lambda: len(_run_seam(PolarsFrame(pl.read_parquet(p)), cols)))
    print(f"  [ref] polars seam    median {med2:6.3f}s (min {mn2:.3f})")

    if a.profile:
        pr = cProfile.Profile(); pr.enable(); scan_file(p); pr.disable()
        s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(15)
        print("\n--- cProfile top (tottime) ---")
        for line in s.getvalue().splitlines():
            if "goldencheck" in line or "pyarrow" in line or "{method" in line or "tottime" in line or "seconds" in line:
                print(line[:120])
    TMP.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
