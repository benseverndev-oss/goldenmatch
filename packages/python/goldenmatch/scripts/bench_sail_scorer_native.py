#!/usr/bin/env python3
"""R1 throughput gate -- the native `score_field_pairwise` Arrow UDF backend
must beat the pure-Python rapidfuzz floor on a single-process pairwise bench.

R1 of ``docs/superpowers/specs/2026-06-13-sail-tier-past-one-box-roadmap.md``:
the Sail scorer's S1 floor is pure-Python rapidfuzz in a `pandas_udf`; the
native target is the score-core kernel rebound as a vectorized Arrow UDF (one
FFI crossing per batch). This script proves the win (and parity) WITHOUT a
Spark cluster -- it exercises the same `score_batch` backends the UDF calls.

Run (native must be built/importable):
    GOLDENMATCH_NATIVE=1 python packages/python/goldenmatch/scripts/bench_sail_scorer_native.py

Notes:
- The bench builds the input as a Python list then `pa.array(...)`; in a real
  `pandas_udf` the input is already a pandas Series, so `pa.array(series)` is a
  faster near-zero-copy conversion -- the native advantage shown here is a
  LOWER bound.
- Native returns f32 (repo convention); parity vs the f64 floor holds to f32
  epsilon. The bench prints max|native-pure| so a regression is visible.
"""
from __future__ import annotations

import argparse
import random
import statistics
import string
import time

import numpy as np
from goldenmatch.core._native_loader import native_module
from goldenmatch.sail import scorers


def _rand_strings(n: int, lo: int, hi: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(lo, hi)))
        for _ in range(n)
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--min-len", type=int, default=6)
    ap.add_argument("--max-len", type=int, default=18)
    args = ap.parse_args()

    native = native_module()
    if native is None or not hasattr(native, "score_field_pairwise"):
        print(
            "ERROR: native score_field_pairwise not available -- build the wheel "
            "(python scripts/build_native.py) and set GOLDENMATCH_NATIVE=1."
        )
        return 2

    a = _rand_strings(args.rows, args.min_len, args.max_len, seed=1)
    # ~50% exact-match pairs, ~50% random -- a realistic dedupe candidate mix.
    b_rand = _rand_strings(args.rows, args.min_len, args.max_len, seed=2)
    rng = random.Random(3)
    b = [a[i] if rng.random() < 0.5 else b_rand[i] for i in range(args.rows)]

    def pure(name):
        return scorers._pure_scores(name, a, b)

    def nat(name):
        return scorers._native_scores(name, a, b)

    print(f"rows={args.rows}  runs={args.runs}  len=[{args.min_len},{args.max_len}]")
    print(f"{'scorer':14s} {'maxdiff':>10s} {'pure(ms)':>10s} {'native(ms)':>11s} {'speedup':>8s}")
    all_pass = True
    for name in scorers._SUPPORTED:
        # parity on a 3K slice (cheap, exact)
        p0 = np.asarray(scorers._pure_scores(name, a[:3000], b[:3000]), dtype=np.float64)
        n0 = np.asarray(scorers._native_scores(name, a[:3000], b[:3000]), dtype=np.float64)
        maxdiff = float(np.max(np.abs(p0 - n0)))

        tp, tn = [], []
        for _ in range(args.runs):
            t = time.perf_counter(); pure(name); tp.append(time.perf_counter() - t)
            t = time.perf_counter(); nat(name); tn.append(time.perf_counter() - t)
        mp, mn = statistics.median(tp), statistics.median(tn)
        speedup = mp / mn if mn else float("inf")
        flag = "" if (speedup >= 1.0 and maxdiff < 1e-6) else "  <-- FAIL"
        if flag:
            all_pass = False
        print(f"{name:14s} {maxdiff:10.2e} {mp*1000:10.1f} {mn*1000:11.1f} {speedup:7.2f}x{flag}")

    print("\nR1 gate:", "PASS (parity + native faster)" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
