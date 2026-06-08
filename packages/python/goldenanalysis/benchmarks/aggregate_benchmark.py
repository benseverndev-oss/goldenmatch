#!/usr/bin/env python3
"""A/B bench for the GoldenAnalysis native aggregation kernels.

Measures the 5-run median wall of ``histogram`` / ``quantile`` over a large array,
three ways, to decide the ``_native_loader._GATED_ON`` flip:

- ``pure``         -- the pure-Python reference (``core/aggregate``), a Python list in.
- ``native_raw``   -- the native kernel with the Arrow array ALREADY materialized.
                      This is the *frames-out ceiling*: what the kernel is worth when
                      a caller hands it Arrow directly (the #663 columnar world).
- ``native+conv``  -- the REALISTIC dispatch for the current call convention: a Python
                      list in, converted to Arrow, then the native kernel. This is what
                      ``aggregate.histogram`` would pay today (it receives a list).

GATE: flip ``_GATED_ON`` for a primitive ONLY if ``native+conv`` comfortably beats
``pure``. Don't gate on ``native_raw`` -- the current call sites pass Python lists, so
the conversion is real. And don't gate on "it's Rust": the pure ``histogram`` is a
tight loop and ``quantile`` leans on C ``sorted``; the goldencheck composite-key kernel
was 2.5x SLOWER than its baseline until the gate caught it. Build the ext first
(``scripts/build_analysis_native.py``); otherwise this reports pure-only.

    POLARS_SKIP_CPU_CHECK=1 uv run python \
        packages/python/goldenanalysis/benchmarks/aggregate_benchmark.py --rows 1000000
"""
from __future__ import annotations

import argparse
import platform
import random
import statistics
import sys
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
    print(f"# {platform.system()} {platform.machine()} | python {sys.version.split()[0]}")
    print(f"rows={args.rows:,} bins={args.bins} runs={args.runs}")

    nm = None
    arr = None
    pa = None
    if native_available():
        import pyarrow as pa  # noqa: F811

        arr = pa.array(values, type=pa.float64())
        nm = native_module()
    else:
        print("native ext NOT built -> pure-only (run scripts/build_analysis_native.py to A/B)")

    def bench(name: str, pure: Callable[[], object], native_on_arr: Callable[[object], object]) -> None:
        pure_ms = _median_wall(pure, args.runs) * 1e3
        line = f"{name:<10} pure={pure_ms:9.2f} ms"
        if nm is not None and pa is not None:
            raw_ms = _median_wall(lambda: native_on_arr(arr), args.runs) * 1e3
            conv_ms = _median_wall(
                lambda: native_on_arr(pa.array(values, type=pa.float64())), args.runs
            ) * 1e3
            line += (
                f"  native_raw={raw_ms:9.2f} ms ({pure_ms / raw_ms:5.2f}x)"
                f"  native+conv={conv_ms:9.2f} ms ({pure_ms / conv_ms:5.2f}x)"
            )
        print(line)

    bench("histogram", lambda: aggregate.histogram(values, args.bins), lambda a: nm.histogram(a, args.bins))
    bench("quantile", lambda: aggregate.quantile(values, 0.95), lambda a: nm.quantile(a, 0.95))

    if nm is not None:
        print("\nGATE: flip _GATED_ON only if native+conv (Python list in -> the current")
        print("aggregate.py call convention) comfortably beats pure. native_raw is the")
        print("frames-out ceiling (Arrow already materialized), NOT the current dispatch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
