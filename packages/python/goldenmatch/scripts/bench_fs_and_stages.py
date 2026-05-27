"""Measure-first profiler: dedupe pipeline stage timings + the Fellegi-Sunter
comparison-vector loop, to decide whether either is worth a native kernel.

Run on a clean box (locally or the bench CI lane); the goldenmatch import +
dedupe are heavy. Prints a per-stage wall breakdown (where the time actually
goes) and the cost of `_build_comparison_matrix` (the per-pair Python loop that
is the FS native-kernel candidate). 3-run medians per the decision-matrix gate
("trust 3-run medians, not single runs").

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_fs_and_stages.py \
        [--rows N] [--fs-pairs N1,N2] [--runs K]
"""
from __future__ import annotations

import argparse
import random
import statistics
import time

import polars as pl

_SURN = [
    "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore",
    "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin",
    "Thompson", "Garcia", "Martinez", "Robinson", "Clark", "Rodriguez", "Lewis",
    "Lee", "Walker", "Hall", "Allen", "Young", "King", "Wright", "Lopez",
]
_FIRST = [
    "Alex", "Blair", "Casey", "Dana", "Eli", "Finley", "Gray", "Harper",
    "Indigo", "Jamie", "Kendall", "Logan", "Morgan", "Noel", "Oakley", "Parker",
    "Quinn", "Riley", "Sage", "Taylor", "Umi", "Val", "Wren", "Xena", "Yael",
    "Zane", "Avery", "Brook", "Cleo", "Drew",
]


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 3:
        return s
    i = rng.randrange(len(s) - 1)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]  # adjacent-char swap


def gen(n: int, dup_frac: float = 0.2, seed: int = 7) -> pl.DataFrame:
    """Synthetic person data with distributed surnames (avoids the soundex
    collapse) + injected near-duplicates (typo'd name, shared block key) so
    blocking + scoring + clustering all do real work. Block key `zip` has
    ~n/40 distinct values -> ~40 rows/block."""
    rng = random.Random(seed)
    n_zip = max(1, n // 40)
    rows = []
    for i in range(n):
        f, l = rng.choice(_FIRST), rng.choice(_SURN)
        z = f"{rng.randrange(n_zip):05d}"
        rows.append({"first_name": f, "last_name": l,
                     "email": f"{f}.{l}.{i}@x.com".lower(), "zip": z})
    for _ in range(int(n * dup_frac)):
        src = rng.choice(rows)
        rows.append({"first_name": _typo(src["first_name"], rng),
                     "last_name": src["last_name"], "email": src["email"],
                     "zip": src["zip"]})
    rng.shuffle(rows)
    return pl.DataFrame(rows)


def _median(fn, runs: int):
    ts = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts), ts


def profile_pipeline(df: pl.DataFrame, runs: int) -> None:
    from goldenmatch import dedupe_df
    from goldenmatch.core.bench import bench_capture

    print(f"\n=== END-TO-END dedupe_df  (rows={df.height}) ===", flush=True)
    last: dict = {}

    def run():
        nonlocal last
        with bench_capture() as b:
            dedupe_df(df, fuzzy={"first_name": 0.85, "last_name": 0.85},
                      blocking=["zip"])
        last = dict(b.timings)

    med, ts = _median(run, runs)
    print(f"total wall ({runs}-run median): {med:.2f}s   runs={[round(x, 2) for x in ts]}",
          flush=True)
    total = sum(last.values()) or 1.0
    print("stage timings (last run), descending  [share of summed stages]:", flush=True)
    for k, v in sorted(last.items(), key=lambda kv: -kv[1]):
        print(f"   {v:8.3f}s  {100 * v / total:5.1f}%  {k}", flush=True)


def profile_fs(df: pl.DataFrame, fs_pairs: list[int], runs: int) -> None:
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.probabilistic import _build_comparison_matrix, _sample_pairs

    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
        ],
    )
    dfid = df.with_row_index("__row_id__")
    row_lookup = {r["__row_id__"]: r for r in dfid.to_dicts()}
    print("\n=== FS comparison-vector loop (_build_comparison_matrix, 2 fields) ===",
          flush=True)
    print("    (per-pair Python loop over native rapidfuzz scorers -- the kernel candidate)",
          flush=True)
    for npairs in fs_pairs:
        pairs = _sample_pairs(dfid, n_pairs=npairs)
        if not pairs:
            print(f"   n_pairs={npairs}: no pairs sampled (dataset too small)", flush=True)
            continue
        med, _ = _median(lambda: _build_comparison_matrix(pairs, row_lookup, mk), runs)
        per_us = med / len(pairs) * 1e6
        print(f"   n_pairs={len(pairs):7d}: {med:7.3f}s  ({per_us:6.2f} us/pair)"
              f"  -> 1M ~ {per_us:.1f}s, 10M ~ {per_us * 10:.0f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=50_000)
    ap.add_argument("--fs-pairs", default="50000,200000")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    fs_pairs = [int(x) for x in args.fs_pairs.split(",") if x.strip()]
    print(f"rows={args.rows}  fs_pairs={fs_pairs}  runs={args.runs}", flush=True)
    df = gen(args.rows)
    profile_pipeline(df, args.runs)
    profile_fs(df, fs_pairs, args.runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
