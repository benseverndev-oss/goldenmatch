"""Regression guard for the max_safe_block scale over-merge bug.

The census-Zipfian surname-soundex block grows ~0.013*N and crossed the OLD fixed
`max(1000, N//200)` cap between ~70K-90K rows. When it crossed, the strong-identity
surname blocking pass was rejected as "oversized", which silently promoted surname
into Fellegi-Sunter SCORING (blocking fields are excluded from scoring) where its
weight over-merged same-block distinct people -- person F1 collapsed 0.97 -> 0.34 at
100K. Fixed by the scale-proportional `_compute_max_safe_block` (height//40, 1000
floor, scorer-aware ceiling): the cap now outgrows the surname block, so the pass is
kept, while small datasets stay at exactly 1000 (DQbench/Febrl byte-unchanged).
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from goldenmatch.core.autoconfig import (
    _compute_max_safe_block,
    auto_configure_probabilistic_df,
)


class TestMaxSafeBlockFormula:
    def test_small_data_unchanged_at_1000(self):
        # < 40K rows: cap stays exactly 1000, byte-identical to the pre-fix
        # behavior on the small datasets the accuracy gates (DQbench/Febrl) use.
        assert _compute_max_safe_block(5_000, native_scoring=False) == 1000
        assert _compute_max_safe_block(20_000, native_scoring=False) == 1000
        assert _compute_max_safe_block(39_000, native_scoring=False) == 1000

    def test_grows_to_hold_surname_soundex_block(self):
        # The surname-soundex block is ~0.013*N. The cap must exceed it so the
        # strong-identity surname pass is not dropped at scale.
        for n, surname_block in [(90_000, 1_143), (100_000, 1_270), (200_000, 2_540)]:
            cap = _compute_max_safe_block(n, native_scoring=False)
            assert cap > surname_block, (
                f"cap {cap} at N={n} must exceed the surname-soundex block "
                f"{surname_block} so the pass is kept, not dropped into scoring"
            )

    def test_monotonic_non_decreasing_in_height(self):
        prev = 0
        for n in (1_000, 40_000, 100_000, 500_000, 1_000_000, 5_000_000):
            cur = _compute_max_safe_block(n, native_scoring=False)
            assert cur >= prev
            prev = cur

    def test_numpy_ceiling_bounds_the_nxn_matrix(self):
        # Pure-numpy keeps the conservative 10K ceiling (float32 10K matrix ~400MB).
        assert _compute_max_safe_block(100_000_000, native_scoring=False) == 10_000

    def test_native_ceiling_is_higher_than_numpy(self):
        # The native FS/bucket scorer has no NxN matrix -> memory basis doesn't
        # bind -> the ceiling lifts (50K) so strong-id passes survive past ~1M.
        big = 5_000_000
        assert _compute_max_safe_block(big, native_scoring=True) > _compute_max_safe_block(
            big, native_scoring=False
        )
        assert _compute_max_safe_block(100_000_000, native_scoring=True) == 50_000


def _zipfian_surname_person_df(n: int, seed: int = 42) -> pl.DataFrame:
    """Person-shaped frame where one surname soundex-collides on ~1.5% of rows --
    enough that its block (~0.015*N) exceeds the OLD 1000 cap but stays under the
    NEW cap at 100K, so it exercises the exact drop-vs-keep boundary."""
    rng = np.random.default_rng(seed)
    # ~1.5% share the hot surname (all soundex S530), rest spread across many.
    hot = int(n * 0.015)
    surnames = np.array(
        ["Smith"] * hot
        + [f"Zzz{rng.integers(0, 10**6)}" for _ in range(n - hot)],
        dtype=object,
    )
    rng.shuffle(surnames)
    firsts = np.array([f"F{i % 5000}" for i in range(n)], dtype=object)
    # distinct dob per person (year spread) + a postcode pool
    years = 1940 + rng.integers(0, 70, n)
    dob = np.array([f"{y}-01-{1 + (i % 28):02d}" for i, y in enumerate(years)], dtype=object)
    postcode = np.array([f"P{rng.integers(0, 200_000)}" for _ in range(n)], dtype=object)
    return pl.DataFrame(
        {
            "record_id": np.arange(n, dtype=np.int64),
            "first_name": firsts,
            "surname": surnames,
            "dob": dob,
            "postcode": postcode,
        }
    )


@pytest.mark.slow
def test_surname_pass_survives_at_100k():
    """At 100K with a Zipfian hot surname, auto-config must KEEP a surname blocking
    pass (so surname stays out of FS scoring). Pre-fix this pass was dropped."""
    df = _zipfian_surname_person_df(100_000)
    cfg = auto_configure_probabilistic_df(df)
    passes = getattr(cfg.blocking, "passes", None) or []
    surname_pass = any(
        (getattr(p, "fields", None) or []) == ["surname"] for p in passes
    )
    assert surname_pass, (
        "surname blocking pass was dropped at 100K -- the max_safe_block "
        "regression is back (surname will over-merge in FS scoring)"
    )
