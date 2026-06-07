"""Measure-first profiler for the bucket+native vs polars-direct decision.

The native Arrow block-scorer (~5x scoring) only runs on the `bucket` backend,
which the controller's v3 planner picks at ~100K rows. Below that, dedupes use
polars-direct and get no native scoring. This sweeps row counts comparing
bucket+native vs polars-direct head-to-head, reporting the speedup AND whether
the two backends produce identical clusters (parity gate) at each N -- the data
needed to safely lower the bucket cutover.

Also times the Fellegi-Sunter comparison-vector loop (a separate opt-in
native-kernel candidate). 3-run medians per the decision-matrix gate.

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_fs_and_stages.py \
        [--ns 1000,5000,10000,30000,60000] [--fs-pairs 50000,200000] [--runs 3]
"""
from __future__ import annotations

import argparse
import os
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
    """Synthetic person data: distributed surnames (no soundex collapse) +
    injected near-duplicates (typo'd name, shared block key) so blocking +
    scoring + clustering all do real work. Block key `zip` ~ n/40 distinct."""
    rng = random.Random(seed)
    n_zip = max(1, n // 40)
    rows = []
    for i in range(n):
        f, l = rng.choice(_FIRST), rng.choice(_SURN)
        rows.append({"first_name": f, "last_name": l,
                     "email": f"{f}.{l}.{i}@x.com".lower(),
                     "zip": f"{rng.randrange(n_zip):05d}"})
    for _ in range(int(n * dup_frac)):
        src = rng.choice(rows)
        rows.append({"first_name": _typo(src["first_name"], rng),
                     "last_name": src["last_name"], "email": src["email"],
                     "zip": src["zip"]})
    rng.shuffle(rows)
    return pl.DataFrame(rows)


def _mk_cfg(backend):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="name", type="weighted", threshold=0.85,
            fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.5),
                MatchkeyField(field="last_name", scorer="jaro_winkler", weight=0.5),
            ],
        )],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend=backend,
    )


def _partition(result) -> set:
    """Non-singleton cluster membership as a set of frozensets (cluster ids +
    order don't matter) -- the backend-agnostic identity of the clustering."""
    return {
        frozenset(c["members"]) for c in result.clusters.values()
        if len(c.get("members", [])) > 1
    }


def sweep(ns: list[int], runs: int) -> None:
    from goldenmatch import dedupe_df
    from goldenmatch.core._native_loader import native_available

    print("\n=== bucket+native vs polars-direct: speedup + cluster parity ===", flush=True)
    print(f"native ext importable: {native_available()}", flush=True)
    print(f"{'N':>7}  {'polars':>8}  {'bucket+nat':>10}  {'speedup':>7}  clusters_identical",
          flush=True)

    def run(df, backend):
        os.environ["GOLDENMATCH_NATIVE"] = "auto"
        walls, last = [], None
        for _ in range(runs):
            t0 = time.perf_counter()
            last = dedupe_df(df, config=_mk_cfg(backend))
            walls.append(time.perf_counter() - t0)
        return statistics.median(walls), last

    for n in ns:
        df = gen(n)
        try:
            pd_t, pd_r = run(df, None)            # polars-direct (default)
            bn_t, bn_r = run(df, "bucket")        # bucket + native
        except Exception as exc:  # noqa: BLE001
            print(f"  {n:>7}  ERROR {type(exc).__name__}: {exc}", flush=True)
            continue
        identical = _partition(pd_r) == _partition(bn_r)
        speed = pd_t / bn_t if bn_t else float("nan")
        print(f"  {n:>7}  {pd_t:7.2f}s  {bn_t:9.2f}s  {speed:6.2f}x  {identical}", flush=True)
        if not identical:
            a, b = _partition(pd_r), _partition(bn_r)
            print(f"           PARITY MISMATCH: polars-only={len(a - b)} bucket-only={len(b - a)}",
                  flush=True)


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
    print("\n=== FS comparison-vector loop (_build_comparison_matrix, 2 fields) ===", flush=True)
    for npairs in fs_pairs:
        pairs = _sample_pairs(dfid, n_pairs=npairs)
        if not pairs:
            continue
        ts = []
        for _ in range(runs):
            t0 = time.perf_counter()
            _build_comparison_matrix(pairs, row_lookup, mk)
            ts.append(time.perf_counter() - t0)
        med = statistics.median(ts)
        per_us = med / len(pairs) * 1e6
        print(f"   n_pairs={len(pairs):7d}: {med:7.3f}s  ({per_us:6.2f} us/pair)"
              f"  -> 1M ~ {per_us:.1f}s, 10M ~ {per_us * 10:.0f}s", flush=True)


def _fs_cfg(backend):
    """Probabilistic (Fellegi-Sunter) config over the synthetic person shape."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend=backend,
    )


def fs_bucket_sweep(ns: list[int], runs: int) -> None:
    """FS on bucket vs polars-direct: speedup + cluster parity (Phase 3a gate).

    Probabilistic matchkeys now ride the bucket orchestration (same em_result,
    scorer-agnostic). This sweep proves the clusters are identical at each N and
    reports the wall ratio.
    """
    from goldenmatch import dedupe_df

    print("\n=== FS: bucket vs polars-direct — speedup + cluster parity ===", flush=True)
    print(f"{'N':>7}  {'polars':>8}  {'bucket':>8}  {'ratio':>6}  clusters_identical",
          flush=True)
    for n in ns:
        df = gen(n)
        def run(backend):
            walls, last = [], None
            for _ in range(runs):
                t0 = time.perf_counter()
                last = dedupe_df(df, config=_fs_cfg(backend))
                walls.append(time.perf_counter() - t0)
            return statistics.median(walls), last
        try:
            pd_t, pd_r = run(None)
            bk_t, bk_r = run("bucket")
        except Exception as exc:  # noqa: BLE001
            print(f"  {n:>7}  ERROR {type(exc).__name__}: {exc}", flush=True)
            continue
        identical = _partition(pd_r) == _partition(bk_r)
        ratio = pd_t / bk_t if bk_t else float("nan")
        print(f"  {n:>7}  {pd_t:7.2f}s  {bk_t:7.2f}s  {ratio:5.2f}x  {identical}", flush=True)
        if not identical:
            a, b = _partition(pd_r), _partition(bk_r)
            print(f"           PARITY MISMATCH: polars-only={len(a - b)} bucket-only={len(b - a)}",
                  flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="1000,5000,10000,30000,60000")
    ap.add_argument("--fs-pairs", default="50000,200000")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    ns = [int(x) for x in args.ns.split(",") if x.strip()]
    fs_pairs = [int(x) for x in args.fs_pairs.split(",") if x.strip()]
    print(f"ns={ns}  fs_pairs={fs_pairs}  runs={args.runs}", flush=True)
    sweep(ns, args.runs)
    fs_bucket_sweep(ns, args.runs)
    profile_fs(gen(max(ns)), fs_pairs, args.runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
