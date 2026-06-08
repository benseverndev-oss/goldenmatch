#!/usr/bin/env python3
"""A/B bench for the GoldenAnalysis native aggregation kernels.

Measures the 5-run median wall of ``histogram`` / ``quantile``, pure-Python vs the
native ``analysis-native`` kernel, over a large Float64 array. This is the
measurement that decides the ``_native_loader._GATED_ON`` flip: a primitive joins
the gated set ONLY after the WALL is shown to move on a real shape.

Don't gate on "it's Rust". The pure ``histogram`` / ``quantile`` are tight Python
loops, and the native path pays an Arrow-marshalling cost; the goldencheck
composite-key kernel was *2.5x slower* than the vectorized baseline until it was
rewritten, and the gate caught it. Run under the native ext (built via
``scripts/build_analysis_native.py``) so ``native_module()`` resolves; otherwise it
reports pure-only.

    POLARS_SKIP_CPU_CHECK=1 uv run python \
        packages/python/goldenanalysis/benchmarks/aggregate_benchmark.py --rows 1000000
"""
from __future__ import annotations

import argparse
import random
import statistics
import time
from collections.abc import Callable

from goldenanalysis.core import aggregate
from goldenanalysis.core._native_loader import native_available, native_module


def _median_wall(fn: Callable[[], object], runs: int) -> float:
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    values = [rng.uniform(-1000.0, 1000.0) for _ in range(args.rows)]
    print(f"rows={args.rows:,} bins={args.bins} runs={args.runs}")

    nm = None
    arr = None
    if native_available():
        import pyarrow as pa

        arr = pa.array(values, type=pa.float64())
        nm = native_module()
    else:
        print("native ext NOT built -> pure-only (run scripts/build_analysis_native.py to A/B)")

    def _report(name: str, pure: Callable[[], object], native: Callable[[], object]) -> None:
        pure_ms = _median_wall(pure, args.runs) * 1e3
        line = f"{name:<10} pure={pure_ms:8.2f} ms"
        if nm is not None:
            nat_ms = _median_wall(native, args.runs) * 1e3
            speedup = pure_ms / nat_ms if nat_ms else float("inf")
            line += f"  native={nat_ms:8.2f} ms  speedup={speedup:5.2f}x"
        print(line)

    _report(
        "histogram",
        lambda: aggregate.histogram(values, args.bins),
        lambda: nm.histogram(arr, args.bins),
    )
    _report(
        "quantile",
        lambda: aggregate.quantile(values, 0.95),
        lambda: nm.quantile(arr, 0.95),
    )

    if nm is not None:
        print("\nGATE: add a primitive to _native_loader._GATED_ON only if its speedup is")
        print("comfortably > 1x here AND parity holds (tests/core/test_native_parity.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
