#!/usr/bin/env python3
"""Issue #688 reproducer, parameterized for the A/B bench.

Runs the exact dedupe shape from the #688 report (NCVR-like synthetic person
data, explicit config, backend="bucket") and prints the wall. The native block
kernel path is selected by GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS (read in Rust):

    0              -> always rayon  (reproduces the LockLatch futex park)
    very large     -> always sequential (the #688 fix; scores in the calling
                      thread, no rayon, no latch)
    unset          -> kernel default (20M pairs/call; the 100K repro is small
                      per bucket, so it takes the sequential path)

Emits a machine-greppable `DEDUPE_WALL_SECONDS=<x>` line; the library's own
`[score_buckets] ... bucket_score done in Xs` prints show the per-pass wall.
"""
from __future__ import annotations

import argparse
import os
import random
import string
import time

os.environ.setdefault("POLARS_MAX_THREADS", "4")
os.environ.setdefault("RAYON_NUM_THREADS", "4")

import polars as pl  # noqa: E402
from goldenmatch._api import dedupe_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _str(rng: random.Random, n: int = 8) -> str:
    return "".join(rng.choices(string.ascii_lowercase, k=n))


def build_df(n: int, seed: int = 0) -> pl.DataFrame:
    rng = random.Random(seed)
    last_names = [_str(rng, 6) for _ in range(2_000)]
    first_names = [_str(rng, 5) for _ in range(1_500)]
    emails = [f"{_str(rng, 8)}@{_str(rng, 5)}.com" for _ in range(max(1, n // 3))]
    npis = [str(rng.randint(1_000_000_000, 9_999_999_999)) for _ in range(max(1, n // 5))]
    phones = [str(rng.randint(2_000_000_000, 9_999_999_999)) for _ in range(max(1, n // 4))]
    zips = [str(rng.randint(10000, 99999)) for _ in range(2_000)]

    rows = []
    for i in range(n):
        rows.append({
            "matching_id": i,
            "source": rng.choice(["A", "B", "C"]),
            "npi": rng.choice(npis) if rng.random() < 0.61 else None,
            "email": rng.choice(emails) if rng.random() < 0.73 else None,
            "first_name": rng.choice(first_names) if rng.random() < 0.56 else None,
            "last_name": rng.choice(last_names) if rng.random() < 0.56 else None,
            "phone_number": rng.choice(phones) if rng.random() < 0.30 else None,
            "zip5": rng.choice(zips) if rng.random() < 0.32 else None,
        })
    return pl.from_dicts(rows)


def build_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="exact_npi", comparison="exact",
                           fields=[MatchkeyField(column="npi")]),
            MatchkeyConfig(name="exact_email", comparison="exact",
                           fields=[MatchkeyField(column="email",
                                                 transforms=["lowercase", "strip"])]),
            MatchkeyConfig(name="exact_phone", comparison="exact",
                           fields=[MatchkeyField(column="phone_number",
                                                 transforms=["digits_only"])]),
            MatchkeyConfig(name="fuzzy_email", comparison="weighted", threshold=0.85,
                           fields=[MatchkeyField(column="email", scorer="levenshtein", weight=1.0,
                                                 transforms=["lowercase", "strip"])]),
            MatchkeyConfig(name="weighted_name_id", comparison="weighted", threshold=0.99, fields=[
                MatchkeyField(column="first_name", scorer="jaro_winkler", weight=0.25,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(column="last_name", scorer="jaro_winkler", weight=0.25,
                              transforms=["lowercase", "strip"]),
                MatchkeyField(column="zip5", scorer="exact", weight=0.20),
                MatchkeyField(column="phone_number", scorer="exact", weight=0.15,
                              transforms=["digits_only"]),
                MatchkeyField(column="npi", scorer="exact", weight=0.15),
            ]),
        ],
        blocking=BlockingConfig(keys=[
            BlockingKeyConfig(fields=["npi"]),
            BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
            BlockingKeyConfig(fields=["phone_number"], transforms=["digits_only"]),
            BlockingKeyConfig(fields=["last_name"], transforms=["lowercase", "substring:0:4"]),
        ], max_block_size=10_000, skip_oversized=True),
        backend="bucket",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from goldenmatch.core._native_loader import native_available, native_module
    print(f"native_available={native_available()} "
          f"has_arrow_kernel={native_available() and hasattr(native_module(), 'score_block_pairs_arrow')}",
          flush=True)
    print(f"GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS={os.environ.get('GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS', '<unset>')}",
          flush=True)

    df = build_df(args.rows, args.seed)
    cfg = build_config()
    t = time.time()
    dedupe_df(df, config=cfg, confidence_required=False)
    wall = time.time() - t
    print(f"DEDUPE_WALL_SECONDS={wall:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
