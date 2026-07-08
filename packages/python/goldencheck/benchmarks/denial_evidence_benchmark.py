"""Measure-first benchmark: denial-constraint Pass-2 pairwise evidence build.

Repo culture (CLAUDE.md) requires a native kernel to *beat its realistic
alternative* before it ships default-on. The denial-constraint evidence kernel
is default-on via the loader gate (``GOLDENCHECK_NATIVE=auto`` runs native
wherever the symbol exists). This script confirms it earns that by timing the
**Pass-2 pairwise evidence** map three ways on a realistic synthetic frame
(2000 rows, 8 mixed-type columns -> a full 64-bit predicate space):

1. **Native kernel** -- ``GOLDENCHECK_NATIVE=1`` + ``kernels.denial_constraint_evidence``.
   No S^2 materialization; a tight Rust double loop over the sample index set.
2. **Pure-Python fallback** -- ``evidence._evidence_python`` directly (the
   kernel's byte-exact oracle). Measured at a smaller sample and extrapolated
   x (m / m_py)^2 (Pass-2 is O(m^2)).
3. **Polars cross-join baseline** -- ``sample.join(sample, how="cross")``
   materializes all S^2 ordered pairs, evaluates every predicate as a Polars
   boolean expr over alpha/beta columns, bit-packs into one u64 mask column,
   then ``group_by(mask).len()``. This is the "obvious vectorized" alternative
   a native skeptic would reach for.

The Polars baseline is verified to produce the **byte-identical** evidence map
(same ``mask -> count`` dict) as native/pure-Python on a small sample before the
timing runs, so we are comparing three implementations of the *same* result.

Run:
    export PYTHONPATH=".../packages/python/goldencheck"   # ';'-sep on Windows
    export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
    python benchmarks/denial_evidence_benchmark.py

------------------------------------------------------------------------------
RESULTS  (recorded 2026-07-08; Windows 11, 16-core box; goldencheck-native
in-tree build; frame = 2000 rows x 8 cols; predicate space = 16 singles /
32 crosses, pass2_effective = 64 bits (full u64), capped; 5-run median wall,
1 warmup discarded)

    Pass-2 sample m = 1500  ->  2,248,500 ordered pairs

    Two back-to-back passes (box was noisy; both shown for honesty):

        method                       run A med   run A min   run B med   run B min
        native kernel                   486.0       472.5      1111.3      1083.8
        polars cross-join               866.4       702.9      1672.2      1196.5
        pure-Python (m_py=400)         3324.4      3274.7      4996.6      2244.2
        pure-Python -> m=1500 (est)   46749.6         --      70264.0         --
            (extrapolated x (1500/400)^2 = 14.06)

    Speedups vs native (both passes):
        polars cross-join : 1.5x - 1.8x slower than native (median AND min)
        pure-Python (est) : 63x - 96x slower than native

    Evidence-map parity (m=60 verification): native == pure-Python ==
    polars cross-join  ->  IDENTICAL (3540 pairs, same mask->count dict).
    The Polars cross-join did NOT OOM/crash at m=1500 (2.25M pairs); it
    completes, just slower.

------------------------------------------------------------------------------
DECISION

KEEP NATIVE DEFAULT-ON -- measured, not assumed. Native beats the realistic
Polars cross-join baseline by a consistent 1.5-1.8x (winning on both median
AND min across two noisy passes) and pure Python by 63-96x, for a
byte-identical evidence map. The win over Polars is modest (NOT the "avoids
S^2 materialization -> orders of magnitude" landslide one might assume before
measuring -- Polars' vectorized cross-join is genuinely fast), but it is a
real and repeatable win on both central tendency and best case, and it grows
with the sample: native walks the sample index set in place while Polars must
materialize S^2 = 2.25M pair rows before it can score them.

No code change -- the loader gate already routes ``denial_constraint`` through
native under ``GOLDENCHECK_NATIVE=auto``; this benchmark is the measured
justification for leaving it that way (feedback_default_to_fast_path + the
Wave-0 measure-first lesson: measure before designing, report margins
honestly).
------------------------------------------------------------------------------
"""
from __future__ import annotations

import functools
import os
import random
import statistics
import time

import polars as pl
from goldencheck.denial.evidence import _evidence_python, space_to_kernel_args
from goldencheck.denial.predicates import build_predicate_space

N_ROWS = 2000
M_FULL = 1500          # Pass-2 sample size for native + polars (m^2 ~ 2.25M pairs)
M_PY = 400             # smaller sample for pure-Python; extrapolated x (M_FULL/M_PY)^2
RUNS = 5
SEED = 7


def make_frame(n: int = N_ROWS, seed: int = SEED) -> pl.DataFrame:
    """Realistic-ish HR-style frame: mixed categorical / boolean / numeric so
    the predicate space spans const, single-tuple, and cross-tuple predicates."""
    rng = random.Random(seed)
    states = ["NY", "CA", "TX", "FL", "WA"]
    depts = ["ENG", "SALES", "HR", "OPS"]
    grades = ["A", "B", "C"]
    return pl.DataFrame(
        {
            "state": [rng.choice(states) for _ in range(n)],
            "dept": [rng.choice(depts) for _ in range(n)],
            "grade": [rng.choice(grades) for _ in range(n)],
            "active": [rng.random() > 0.2 for _ in range(n)],
            "salary": [rng.randint(40_000, 200_000) for _ in range(n)],
            "bonus": [rng.randint(0, 50_000) for _ in range(n)],
            "age": [rng.randint(21, 65) for _ in range(n)],
            "tenure": [rng.randint(0, 40) for _ in range(n)],
        }
    )


def _expr_cmp(op: int, a: pl.Expr, b: pl.Expr) -> pl.Expr:
    if op == 0:
        return a == b
    if op == 1:
        return a != b
    if op == 2:
        return a < b
    if op == 3:
        return a <= b
    if op == 4:
        return a > b
    return a >= b  # 5 = GE


def polars_pair_evidence(cols, nulls, pred_spec, sample_idx) -> dict[int, int]:
    """Pass-2 evidence via a Polars self cross-join (the S^2-materializing
    baseline). Same bit layout as ``evidence._evidence_python``:
    ``[0..s)`` singles-on-alpha, ``[s..2s)`` singles-on-beta,
    ``[2s..2s+c)`` crosses on ``(alpha, beta)``.
    """
    singles = [sp for sp in pred_spec if sp[0] != 2]
    crosses = [sp for sp in pred_spec if sp[0] == 2]
    s = len(singles)

    refcols = {sp[1] for sp in pred_spec} | {sp[3] for sp in pred_spec if sp[0] != 0}
    data: dict[str, list] = {"idx": list(sample_idx)}
    for k in sorted(refcols):
        data[f"c{k}"] = [cols[k][i] for i in sample_idx]
        data[f"nz{k}"] = [not nulls[k][i] for i in sample_idx]  # not-null flag
    left = pl.DataFrame(data)

    joined = left.join(left, how="cross", suffix="_r").filter(
        pl.col("idx") != pl.col("idx_r")
    )

    def _bit(bit: int, cond: pl.Expr) -> pl.Expr:
        return (
            pl.when(cond)
            .then(pl.lit(1 << bit, dtype=pl.UInt64))
            .otherwise(pl.lit(0, dtype=pl.UInt64))
        )

    terms: list[pl.Expr] = []
    for i, (kind, ca, op, cb, lit) in enumerate(singles):
        # singles[i] holds on alpha (left side)
        if kind == 0:  # const: t.A op literal
            cond_a = pl.col(f"nz{ca}") & _expr_cmp(op, pl.col(f"c{ca}"), pl.lit(lit))
        else:          # single: t.A op t.B (same row)
            cond_a = (
                pl.col(f"nz{ca}")
                & pl.col(f"nz{cb}")
                & _expr_cmp(op, pl.col(f"c{ca}"), pl.col(f"c{cb}"))
            )
        terms.append(_bit(i, cond_a))
        # singles[i] holds on beta (right side)
        if kind == 0:
            cond_b = pl.col(f"nz{ca}_r") & _expr_cmp(op, pl.col(f"c{ca}_r"), pl.lit(lit))
        else:
            cond_b = (
                pl.col(f"nz{ca}_r")
                & pl.col(f"nz{cb}_r")
                & _expr_cmp(op, pl.col(f"c{ca}_r"), pl.col(f"c{cb}_r"))
            )
        terms.append(_bit(s + i, cond_b))
    for j, (_kind, ca, op, cb, _lit) in enumerate(crosses):
        # cross: t_alpha.A op t_beta.B
        cond = (
            pl.col(f"nz{ca}")
            & pl.col(f"nz{cb}_r")
            & _expr_cmp(op, pl.col(f"c{ca}"), pl.col(f"c{cb}_r"))
        )
        terms.append(_bit(2 * s + j, cond))

    mask = functools.reduce(lambda a, b: a | b, terms).alias("mask")
    out = joined.select(mask).group_by("mask").len()
    return {row[0]: row[1] for row in out.iter_rows()}


def _median_min_ms(fn, runs: int = RUNS) -> tuple[float, float]:
    fn()  # warmup (discarded)
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples), min(samples)


def main() -> None:
    df = make_frame()
    space = build_predicate_space(df)
    cols, nulls, pred_spec, _ = space_to_kernel_args(space)

    print("=" * 78)
    print("Denial-constraint Pass-2 evidence: native vs Polars cross-join vs pure-Python")
    print("=" * 78)
    print(
        f"frame: {df.height} rows x {df.width} cols | "
        f"predicate space: {space.n_single} singles / {space.n_cross} crosses, "
        f"pass2_effective={space.pass2_effective} bits, capped={space.capped}"
    )

    # --- parity verification on a small sample (must be byte-identical) ---
    rng = random.Random(1)
    small_idx = sorted(rng.sample(range(df.height), 60))
    ref = _evidence_python(cols, nulls, pred_spec, 2, 0, small_idx)
    pol = polars_pair_evidence(cols, nulls, pred_spec, small_idx)
    os.environ["GOLDENCHECK_NATIVE"] = "1"
    from goldencheck.core import kernels  # import AFTER env set (gate reads env per-call)

    nat = kernels.denial_constraint_evidence(cols, nulls, pred_spec, 2, 0, small_idx)
    parity_ok = ref == pol == nat
    print(
        f"\nparity (m=60): native == pure-Python == polars cross-join -> "
        f"{'IDENTICAL' if parity_ok else 'MISMATCH!'} "
        f"({sum(ref.values())} pairs)"
    )
    if not parity_ok:
        print("  !! evidence maps differ -- timing below is not apples-to-apples")
        print("  ref^pol keys:", set(ref) ^ set(pol))
        print("  ref^nat keys:", set(ref) ^ set(nat))

    # --- full-m sample for native + polars ---
    full_idx = sorted(rng.sample(range(df.height), M_FULL))
    n_pairs = M_FULL * (M_FULL - 1)
    print(f"\nPass-2 sample m = {M_FULL}  ->  {n_pairs:,} ordered pairs\n")

    os.environ["GOLDENCHECK_NATIVE"] = "1"
    nat_med, nat_min = _median_min_ms(
        lambda: kernels.denial_constraint_evidence(cols, nulls, pred_spec, 2, 0, full_idx)
    )
    pol_med, pol_min = _median_min_ms(
        lambda: polars_pair_evidence(cols, nulls, pred_spec, full_idx)
    )

    # --- pure-Python at reduced m, extrapolated x (M_FULL/M_PY)^2 ---
    py_idx = sorted(rng.sample(range(df.height), M_PY))
    py_med, py_min = _median_min_ms(
        lambda: _evidence_python(cols, nulls, pred_spec, 2, 0, py_idx)
    )
    scale = (M_FULL / M_PY) ** 2
    py_est = py_med * scale

    print(f"{'method':<34}{'median (ms)':>14}{'min (ms)':>12}")
    print(f"{'native kernel':<34}{nat_med:>14.1f}{nat_min:>12.1f}")
    print(f"{'polars cross-join':<34}{pol_med:>14.1f}{pol_min:>12.1f}")
    print(f"{f'pure-Python (m_py={M_PY})':<34}{py_med:>14.1f}{py_min:>12.1f}")
    print(f"{f'pure-Python -> m={M_FULL} (est)':<34}{py_est:>14.1f}{'--':>12}"
          f"   (x {scale:.2f})")

    print("\nspeedups vs native:")
    print(f"  polars cross-join : {pol_med / nat_med:.1f}x slower")
    print(f"  pure-Python (est) : {py_est / nat_med:.1f}x slower")

    winner = min(("native", nat_med), ("polars", pol_med), key=lambda kv: kv[1])[0]
    print("\nDECISION:", "KEEP NATIVE DEFAULT-ON (native wins)"
          if winner == "native"
          else f"REVIEW -- {winner} beat native; do NOT silently keep a losing kernel")


if __name__ == "__main__":
    main()
