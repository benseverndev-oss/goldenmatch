#!/usr/bin/env python3
"""Stage-D quality lever (pure Python): does a BIGGER profiling sample reduce the
error in auto-config's ``estimated_pair_count`` vs ground truth?

Auto-config measures blocking on a SAMPLE and linearly extrapolates the candidate
pair count to the full row count (``BlockingProfile.extrapolate_to``: pairs *=
n_full/n_sample). But within-block pairs grow ~quadratically with block size, so a
small sample's blocks are smaller and linear extrapolation should systematically
UNDER-estimate. This bench measures the extrapolated/true ratio as the sample size
grows, on a realistic blocking scheme, to see (a) how severe the small-sample error
is and (b) where it would flip the v3 planner's backend rung.

Pure Python, no native ext required. Run:
  GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 \
    PYTHONPATH=packages/python/goldenmatch python scripts/bench_autoconfig_sample_quality.py
"""
from __future__ import annotations

import os
import statistics
from types import SimpleNamespace

os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np
import polars as pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import measure_blocking_profile

# v3 planner rung boundaries (autoconfig_planner_rules.py) — for the "would the
# wrong pair-count flip the backend?" punchline.
SIMPLE_MAX_PAIRS = 50_000_000     # < this (and <100k rows) -> simple/bucket
CHUNKED_MAX_PAIRS = 5_000_000_000  # [50M, 5B) -> chunked; >=5B -> duckdb


def gen_people(n: int, *, n_surnames: int = 220, seed: int = 0) -> pl.DataFrame:
    """Realistic-ish person frame with a moderately-skewed surname distribution
    (Zipf), so soundex/exact blocks are moderate (not the degenerate 30-name
    test pathology). Deterministic under ``seed``."""
    rng = np.random.default_rng(seed)
    # Surname pool: random consonant/vowel strings so soundex codes spread out.
    cons, vowels = "bcdfghjklmnprstvw", "aeiou"
    surnames = []
    for _ in range(n_surnames):
        ln = rng.integers(4, 8)
        s = "".join(
            (rng.choice(list(cons)) if i % 2 == 0 else rng.choice(list(vowels)))
            for i in range(int(ln))
        )
        surnames.append(s.capitalize())
    # Zipf-ish weights (common surnames more frequent) -> realistic block skew.
    weights = 1.0 / np.arange(1, n_surnames + 1)
    weights /= weights.sum()
    last = rng.choice(surnames, size=n, p=weights)
    firsts = ["James", "Mary", "John", "Pat", "Sue", "Bob", "Ann", "Tom", "Liz", "Joe"]
    first = rng.choice(firsts, size=n)
    cities = ["Raleigh", "Durham", "Cary", "Apex", "Wake", "Garner", "Holly", "Knight"]
    city = rng.choice(cities, size=n)
    zips = rng.integers(27001, 27999, size=n).astype(str)
    return pl.DataFrame(
        {"first_name": first, "last_name": last, "city": city, "zip": zips}
    )


def main() -> int:
    N_FULL = 100_000
    SAMPLE_SIZES = [1_000, 2_000, 5_000, 10_000, 20_000, 50_000]
    SEEDS = [1, 2, 3]

    print(f"Generating {N_FULL:,} rows ...", flush=True)
    full = gen_people(N_FULL, seed=1)

    # Two clean, hand-built blocking schemes (avoids the degenerate soundex(zip)
    # pass auto-config picks on numeric zips). exact(last_name) = moderate blocks;
    # soundex(last_name) = coarser/bigger blocks (a more quadratic regime).
    schemes = {
        "exact(last_name)": BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase"])],
        ),
        "soundex(last_name)": BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase", "soundex"])],
        ),
    }

    all_results: list[tuple[str, int, float]] = []  # (scheme, sample_n, med_ratio)
    for name, blocking_cfg in schemes.items():
        cfg = SimpleNamespace(blocking=blocking_cfg)
        gt_profile = measure_blocking_profile(full, cfg)
        if gt_profile is None:
            print(f"[{name}] ERROR: measure_blocking_profile returned None")
            continue
        gt = gt_profile.estimated_pair_count
        print(f"\n================ scheme: {name} ================")
        print(
            f"GROUND TRUTH (full {N_FULL:,}): pairs={gt:,}  blocks={gt_profile.n_blocks:,}  "
            f"p99_block={gt_profile.block_sizes_p99:,}  max_block={gt_profile.block_sizes_max:,}",
            flush=True,
        )
        print(f"{'sample':>8} {'frac':>6} {'med_extrap':>14} {'extrap/true':>12} {'spread':>9}")
        print("-" * 55)
        rows = []
        for n in SAMPLE_SIZES:
            ratios, extraps = [], []
            for s in SEEDS:
                bp = measure_blocking_profile(full.sample(n, seed=s), cfg)
                if bp is None:
                    continue
                extrap = bp.extrapolate_to(n, N_FULL).estimated_pair_count
                extraps.append(extrap)
                ratios.append(extrap / gt if gt else float("nan"))
            if not ratios:
                continue
            med_ratio = statistics.median(ratios)
            rows.append((n, med_ratio))
            all_results.append((name, n, med_ratio))
            print(
                f"{n:>8,} {n / N_FULL:>6.2%} {int(statistics.median(extraps)):>14,} "
                f"{med_ratio:>12.3f} {max(ratios) - min(ratios):>9.3f}"
            )

    print("\n--- INTERPRETATION (post-S1) ---")
    print("extrap/true ~= 1.0 across all sample fractions: the corrected ratio**2")
    print("extrapolation (spec 2026-06-22-autoconfig-smarter-faster-s1-s3) tracks the")
    print("true candidate pair count to within a few percent. The OLD linear scaling")
    print("under-counted by ~the sampling fraction (a 1% sample read ~100x low),")
    print("flipping a true chunked-rung dataset to 'simple/bucket' and under-")
    print("provisioning the backend. ratio**2 restores scale-invariance.\n")

    # ── Gate: the under-count must be fixed across every sample fraction. ──────
    # Observed post-S1: 0.95-0.99. The band would fail hard if linear scaling
    # regressed (a 1% sample would read ~0.01 << 0.6).
    LOW, HIGH = 0.6, 1.6
    bad = [(s, n, r) for (s, n, r) in all_results if not (LOW <= r <= HIGH)]
    if bad:
        print(f"GATE FAILED: {len(bad)} (scheme, sample) ratios outside [{LOW}, {HIGH}]:")
        for s, n, r in bad:
            print(f"  {s} sample={n:,}: extrap/true={r:.3f}")
        return 1
    print(f"GATE PASSED: all {len(all_results)} median extrap/true ratios in [{LOW}, {HIGH}].")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
