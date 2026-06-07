#!/usr/bin/env python3
"""Wall-clock benchmark for GoldenCheck's native deep-profiling kernels.

This is both the gate and the verification for the native path: a kernel only
earns a place in ``_native_loader._GATED_ON`` once a parity test proves it is
byte-identical AND this harness shows the wall actually moved on a realistic
workload (the repo's ``feedback_verify_perf_not_just_ship`` lesson -- don't
trust "it shipped", measure the wall on the workload of interest).

Run:
    python benchmarks/deep_profile_benchmark.py
    python benchmarks/deep_profile_benchmark.py --rows 10000000

Reports the 5-run median wall for the pure-Python path vs the native kernel,
side by side, for each kernel. Skips native rows cleanly if the extension isn't
built (``pip install goldencheck[native]`` or
``python scripts/build_goldencheck_native.py``).
"""
from __future__ import annotations

import argparse
import random
import statistics
import time
from collections import Counter

import numpy as np
from goldencheck.baseline import statistical as st
from goldencheck.core._native_loader import native_available, native_module


def _median_wall(fn, runs: int = 5) -> float:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


def _benford_dataset(rows: int, seed: int = 7) -> np.ndarray:
    """A Benford-ish positive column spanning several orders of magnitude."""
    rng = random.Random(seed)
    return np.array(
        [rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]) * 10.0 ** rng.randint(0, 6)
         + rng.random() for _ in range(rows)],
        dtype=np.float64,
    )


def bench_benford(rows: int) -> None:
    print(f"\n## Benford leading-digit histogram  (rows={rows:,})")
    values = _benford_dataset(rows)

    def py() -> None:
        digits = st._extract_leading_digits(values)
        Counter(digits)

    py_wall = _median_wall(py)
    print(f"  pure-Python loop : {py_wall * 1000:9.2f} ms")

    if native_available():
        import pyarrow as pa

        arr = pa.array(values)

        def nat() -> None:
            native_module().benford_leading_digits(arr)

        nat_wall = _median_wall(nat)
        speedup = py_wall / nat_wall if nat_wall > 0 else float("inf")
        print(f"  native kernel    : {nat_wall * 1000:9.2f} ms   ({speedup:.1f}x faster)")
    else:
        print("  native kernel    : (not built -- skipping)")


def bench_composite_key(rows: int) -> None:
    import polars as pl
    from goldencheck.relations import composite_key as ck

    print(f"\n## Composite-key search  (rows={rows:,})")
    rng = random.Random(11)
    # A keyless frame whose natural key is a 3-column combination.
    df = pl.DataFrame({
        "region": [rng.randint(0, 8) for _ in range(rows)],
        "store": [rng.randint(0, 50) for _ in range(rows)],
        "sku": [rng.randint(0, 400) for _ in range(rows)],
        "day": [rng.randint(0, 90) for _ in range(rows)],
        "qty": [rng.randint(1, 9) for _ in range(rows)],
    })
    candidates = ck._select_candidates(df, df.height)
    single_unique = [False] * len(candidates)

    def py() -> None:
        ck._python_search(df, candidates, df.height, ck.MAX_KEY_SIZE)

    py_wall = _median_wall(py, runs=3)
    print(f"  pure-Python (Polars) : {py_wall * 1000:9.2f} ms")

    if native_available():
        arrays = [df[c].to_arrow() for c in candidates]

        def nat() -> None:
            native_module().composite_key_search(arrays, ck.MAX_KEY_SIZE, single_unique)

        nat_wall = _median_wall(nat, runs=3)
        speedup = py_wall / nat_wall if nat_wall > 0 else float("inf")
        print(f"  native kernel        : {nat_wall * 1000:9.2f} ms   ({speedup:.1f}x faster)")
    else:
        print("  native kernel        : (not built -- skipping)")


def bench_functional_dependency(rows: int) -> None:
    import polars as pl
    from goldencheck.relations import functional_dependency as fd

    print(f"\n## Functional-dependency discovery  (rows={rows:,})")
    rng = random.Random(13)
    z2c: dict[int, int] = {}
    zips = [rng.randint(0, 5000) for _ in range(rows)]
    df = pl.DataFrame({
        "zip": zips,
        "city": [z2c.setdefault(z, rng.randint(0, 800)) for z in zips],  # zip->city strict
        "dept": [rng.randint(0, 40) for _ in range(rows)],
        "noise1": [rng.randint(0, 9) for _ in range(rows)],
        "noise2": [rng.randint(0, 100) for _ in range(rows)],
        "noise3": [rng.choice(["a", "b", "c", "d"]) for _ in range(rows)],
    })
    cols = fd._select_candidates(df, df.height)

    def py() -> None:
        fd._discover_polars(df, cols, df.height)

    py_wall = _median_wall(py, runs=3)
    print(f"  pure-Python (Polars) : {py_wall * 1000:9.2f} ms")

    if native_available():
        arrays = [df[c].to_arrow() for c in cols]

        def nat() -> None:
            native_module().discover_functional_dependencies(arrays)

        nat_wall = _median_wall(nat, runs=3)
        speedup = py_wall / nat_wall if nat_wall > 0 else float("inf")
        print(f"  native kernel        : {nat_wall * 1000:9.2f} ms   ({speedup:.1f}x faster)")
    else:
        print("  native kernel        : (not built -- skipping)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1_000_000, help="row count per kernel")
    args = ap.parse_args()

    print("GoldenCheck native deep-profiling benchmark")
    print(f"native extension available: {native_available()}")
    bench_benford(args.rows)
    bench_composite_key(min(args.rows, 200_000))  # combinatorial; keep it sane
    bench_functional_dependency(min(args.rows, 200_000))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
