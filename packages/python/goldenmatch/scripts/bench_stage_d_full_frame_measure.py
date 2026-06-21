"""Stage D measure-first bench: full-frame blocking measurement wall.

The Stage D finding (docs/superpowers/specs/2026-06-21-autoconfig-sample-quality-
finding.md) showed linear sample extrapolation under-counts candidate pairs by
~the sampling fraction (up to ~500x at 10M), so the planner under-provisions the
backend. The only accurate fix is FULL-FRAME measurement. This bench answers the
finding's open question: is full-frame `measure_blocking_profile` affordable
enough to default-on, and where does the wall go (native kernel vs polars)?

Run: python scripts/bench_stage_d_full_frame_measure.py
"""
from __future__ import annotations

import os
import time

import polars as pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig, GoldenMatchConfig
from goldenmatch.core._native_loader import native_module
from goldenmatch.core.blocker import measure_blocking_profile

_SURNAMES = [
    "smith", "jones", "brown", "davis", "wilson", "taylor", "thomas", "moore",
    "jackson", "white", "harris", "martin", "thompson", "garcia", "martinez",
    "robinson", "clark", "rodriguez", "lewis", "lee", "walker", "hall", "allen",
    "young", "king", "wright", "scott", "green", "baker", "adams",
]
_FIRST = ["james", "mary", "john", "patricia", "robert", "jennifer", "michael", "linda"]


def make_person_df(n: int, seed: int = 0) -> pl.DataFrame:
    """Person-shaped frame: surname blocking key with realistic skew (fixed
    cardinality => block size grows with N, the regime where extrapolation breaks)."""
    import random

    rng = random.Random(seed)
    last = [rng.choice(_SURNAMES) for _ in range(n)]
    first = [rng.choice(_FIRST) for _ in range(n)]
    zip_ = [f"{rng.randint(10000, 99999)}" for _ in range(n)]
    return pl.DataFrame({"first_name": first, "last_name": last, "zip": zip_})


def _config() -> GoldenMatchConfig:
    """Single exact pass on last_name -- the canonical fixed-cardinality scheme."""
    cfg = GoldenMatchConfig()
    cfg.blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=[])],
        max_block_size=100_000,
        skip_oversized=False,
    )
    return cfg


def _fast_full_frame_pairs(df: pl.DataFrame, native: bool) -> tuple[int, float]:
    """The 'fast' full-frame path: ONE polars group_by().len() to get block sizes
    in a single query (no per-block collect loop), then the pair-count aggregate
    (native candidate_pair_count when available, else the Python sum)."""
    t = time.perf_counter()
    sizes = (
        df.lazy()
        .group_by("last_name")
        .agg(pl.len().alias("__sz__"))
        .select("__sz__")
        .collect()["__sz__"]
        .to_list()
    )
    if native:
        nm = native_module()
        pairs = nm.candidate_pair_count(sizes)
    else:
        pairs = sum(s * (s - 1) // 2 for s in sizes)
    return pairs, time.perf_counter() - t


def main() -> None:
    sizes = [100_000, 1_000_000]
    # 5M only if there's headroom (string-heavy frame ~ a few hundred MB).
    if os.environ.get("BENCH_5M") == "1":
        sizes.append(5_000_000)

    nm = native_module()
    print(f"native ext: {'present' if nm else 'ABSENT'}  "
          f"candidate_pair_count: {hasattr(nm, 'candidate_pair_count') if nm else False}\n")
    cfg = _config()

    hdr = f"{'N':>10} | {'measure_blocking_profile':>26} | {'fast(polars+native)':>20} | {'fast(pure py)':>14} | {'true_pairs':>14}"
    print(hdr)
    print("-" * len(hdr))
    for n in sizes:
        df = make_person_df(n)

        # (1) the current production full-frame path
        t = time.perf_counter()
        prof = measure_blocking_profile(df, cfg)
        wall_measure = time.perf_counter() - t
        true_pairs = prof.total_comparisons if prof else -1

        # (2) the fast restructured path, native + pure
        p_nat, wall_fast_nat = _fast_full_frame_pairs(df, native=bool(nm))
        p_py, wall_fast_py = _fast_full_frame_pairs(df, native=False)
        assert p_nat == p_py == true_pairs, f"pair-count mismatch: {p_nat}/{p_py}/{true_pairs}"

        print(f"{n:>10,} | {wall_measure*1e3:>23.1f}ms | {wall_fast_nat*1e3:>17.1f}ms | "
              f"{wall_fast_py*1e3:>11.1f}ms | {true_pairs:>14,}")


if __name__ == "__main__":
    main()
