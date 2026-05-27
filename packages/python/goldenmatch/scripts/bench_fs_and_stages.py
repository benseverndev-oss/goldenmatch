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


def _run_pipeline(df: pl.DataFrame, native_mode: str) -> tuple[float, dict]:
    """One dedupe_df under bench_capture with GOLDENMATCH_NATIVE=native_mode.
    Returns (wall, stage_timings). native_enabled() reads the env each call, so
    toggling it per-run flips the block-scoring + clustering kernels."""
    import os

    from goldenmatch import dedupe_df
    from goldenmatch.core.bench import bench_capture

    os.environ["GOLDENMATCH_NATIVE"] = native_mode
    t0 = time.perf_counter()
    with bench_capture() as b:
        dedupe_df(df, fuzzy={"first_name": 0.85, "last_name": 0.85}, blocking=["zip"])
    return time.perf_counter() - t0, dict(b.timings)


def profile_pipeline(df: pl.DataFrame, runs: int) -> None:
    from goldenmatch.core._native_loader import native_available

    print(f"\n=== END-TO-END dedupe_df  (rows={df.height}) ===", flush=True)
    print(f"native ext importable: {native_available()}", flush=True)
    if not native_available():
        print("   WARNING: native ext not built -> only the pure-Python path is "
              "measurable; build it (scripts/build_native.py) to see the kernel.",
              flush=True)

    score_times: dict[str, float] = {}
    for mode in ("0", "auto"):
        timings_last: dict = {}
        walls = []
        for _ in range(runs):
            w, t = _run_pipeline(df, mode)
            walls.append(w)
            timings_last = t
        med = statistics.median(walls)
        label = "python (NATIVE=0)" if mode == "0" else "native (NATIVE=auto)"
        total = sum(timings_last.values()) or 1.0
        print(f"\n-- {label}: total wall ({runs}-run median) {med:.2f}s "
              f"runs={[round(x, 2) for x in walls]}", flush=True)
        for k, v in sorted(timings_last.items(), key=lambda kv: -kv[1])[:8]:
            print(f"   {v:8.3f}s  {100 * v / total:5.1f}%  {k}", flush=True)
        # Track the scoring stage for the native-vs-python comparison.
        score_times[mode] = timings_last.get("fuzzy_score_blocks") or \
            timings_last.get("fuzzy_scoring") or 0.0

    py, nat = score_times.get("0", 0.0), score_times.get("auto", 0.0)
    if py > 0 and nat > 0:
        ratio = py / nat
        engaged = "ENGAGED" if ratio >= 1.3 else "NOT engaging (routing gap?)"
        print(f"\nscoring stage: python {py:.3f}s vs native {nat:.3f}s "
              f"-> {ratio:.2f}x  [native fast-path {engaged}]", flush=True)


def _mk_weighted_cfg(backend):
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


def _run_cfg(df: pl.DataFrame, cfg, native_mode: str) -> tuple[float, dict]:
    import os

    from goldenmatch import dedupe_df
    from goldenmatch.core.bench import bench_capture

    os.environ["GOLDENMATCH_NATIVE"] = native_mode
    t0 = time.perf_counter()
    with bench_capture() as b:
        dedupe_df(df, config=cfg)
    return time.perf_counter() - t0, dict(b.timings)


def profile_backends(df: pl.DataFrame, runs: int) -> None:
    """Head-to-head: does the bucket backend + native Arrow kernel beat the
    default polars-direct path at mid scale? Explicit config bypasses the
    controller so we pin the backend. Decides whether lowering the bucket
    threshold (or wiring the kernel into the default scorer) would unlock the
    ~5x scoring win below the controller's current ~100K cutover."""
    print(f"\n=== BACKEND HEAD-TO-HEAD  (rows={df.height}, explicit config) ===", flush=True)
    configs = [
        ("polars-direct  native=auto", None, "auto"),
        ("bucket         native=0   ", "bucket", "0"),
        ("bucket         native=auto", "bucket", "auto"),
    ]
    res: dict[str, float] = {}
    for label, backend, native in configs:
        walls, last = [], {}
        try:
            for _ in range(runs):
                w, t = _run_cfg(df, _mk_weighted_cfg(backend), native)
                walls.append(w)
                last = t
        except Exception as exc:  # noqa: BLE001 - report, don't abort the bench
            print(f"-- {label}: ERROR {type(exc).__name__}: {exc}", flush=True)
            continue
        med = statistics.median(walls)
        res[label.strip()] = med
        top = ", ".join(f"{k}={v:.2f}s" for k, v in
                        sorted(last.items(), key=lambda kv: -kv[1])[:3])
        print(f"-- {label}: total {med:.2f}s   [{top}]", flush=True)
    pd_ = res.get("polars-direct  native=auto")
    bn = res.get("bucket         native=auto")
    bp = res.get("bucket         native=0")
    if pd_ and bn:
        print(f"\nbucket+native vs polars-direct: {bn:.2f}s vs {pd_:.2f}s "
              f"-> {pd_ / bn:.2f}x  (>1 = bucket+native wins at this scale)", flush=True)
    if bp and bn:
        print(f"native kernel within bucket: {bp:.2f}s -> {bn:.2f}s = {bp / bn:.2f}x",
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
    profile_backends(df, args.runs)
    profile_fs(df, fs_pairs, args.runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
