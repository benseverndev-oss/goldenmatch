"""Tests for stratified autoconfig sampling (#131).

Spec: docs/superpowers/specs/2026-05-21-stratified-sampling-design.md
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.autoconfig_controller import (
    _pick_stratification_key,
    _stratified_sample,
)


def test_pick_stratification_key_picks_mid_cardinality():
    """50-distinct column wins over 5000-distinct + 3-distinct."""
    df = pl.DataFrame({
        "high_card": [f"id_{i}" for i in range(5000)],
        "mid_card": [f"zip_{i % 50}" for i in range(5000)],  # 50 distinct
        "low_card": ["A", "B", "C"] * 1666 + ["A", "B"],  # 3 distinct
    })
    assert _pick_stratification_key(df) == "mid_card"


def test_pick_stratification_key_prefers_blocking_named_columns():
    """Among mid-cardinality candidates, prefer blocking-shaped names."""
    df = pl.DataFrame({
        "random_attr": [f"x_{i % 30}" for i in range(1000)],  # 30 distinct
        "zip_code": [f"{i % 50:05d}" for i in range(1000)],   # 50 distinct, named
    })
    # zip_code has more distinct, BUT name preference is the tiebreak.
    # Algorithm: sort by (preference_rank ASC, -distinct ASC). zip_code
    # is rank=-1 (preferred), random_attr is rank=0. zip_code wins.
    assert _pick_stratification_key(df) == "zip_code"


def test_pick_stratification_key_returns_none_when_no_mid_card_columns():
    """Per-record-unique + binary-only columns → None."""
    df = pl.DataFrame({
        "id": [f"u_{i}" for i in range(1000)],          # 1000 distinct
        "is_active": [True, False] * 500,                 # 2 distinct
    })
    assert _pick_stratification_key(df) is None


def test_pick_stratification_key_skips_internal_columns():
    """__row_id__ / __source__ etc. never picked as strat keys."""
    df = pl.DataFrame({
        "__row_id__": list(range(1000)),
        "__source__": [f"src_{i % 50}" for i in range(1000)],  # mid-card but internal
        "real_zip": [f"{i % 100:05d}" for i in range(1000)],
    })
    result = _pick_stratification_key(df)
    assert result == "real_zip"


# ---------------------------------------------------------------------------
# _stratified_sample
# ---------------------------------------------------------------------------


def test_stratified_sample_represents_rare_strata():
    """Rare stratum (1% of data) gets the min_per_stratum floor."""
    # 990 rows in stratum 'A', 10 rows in stratum 'B' (1% rare).
    df = pl.DataFrame({
        "zip": (["A"] * 990) + (["B"] * 10),
        "id": list(range(1000)),
    })
    # Target 100 rows; with min_per_stratum=10, rare stratum gets at least 10.
    sample = _stratified_sample(df, "zip", target_n=100, min_per_stratum=10, seed=42)
    b_count = sample.filter(pl.col("zip") == "B").height
    assert b_count >= 10, (
        f"rare stratum 'B' got {b_count} rows; min_per_stratum=10 violated"
    )


def test_stratified_sample_random_only_under_represents_rare():
    """Sanity check: random sampling under-represents rare 1% stratum."""
    df = pl.DataFrame({
        "zip": (["A"] * 990) + (["B"] * 10),
        "id": list(range(1000)),
    })
    # Random sample of 100: B gets ~1 row in expectation (vs 10 with strat).
    random_sample = df.sample(n=100, seed=42, shuffle=True)
    strat_sample = _stratified_sample(df, "zip", 100, min_per_stratum=10, seed=42)
    b_random = random_sample.filter(pl.col("zip") == "B").height
    b_strat = strat_sample.filter(pl.col("zip") == "B").height
    assert b_strat >= b_random, (
        f"stratified ({b_strat}) should match-or-beat random ({b_random})"
    )


def test_stratified_sample_respects_stratum_size_when_smaller_than_floor():
    """If a stratum has 5 rows and min_per_stratum=10, sample takes all 5."""
    df = pl.DataFrame({
        "zip": (["A"] * 100) + (["B"] * 5),
        "id": list(range(105)),
    })
    sample = _stratified_sample(df, "zip", target_n=50, min_per_stratum=10, seed=42)
    b_count = sample.filter(pl.col("zip") == "B").height
    # Can't sample more than the stratum has.
    assert b_count == 5


def test_stratified_sample_returns_concat_of_per_stratum():
    """Output is a Polars DataFrame with same columns as input."""
    df = pl.DataFrame({
        "zip": [f"z{i % 5}" for i in range(100)],
        "name": [f"n_{i}" for i in range(100)],
    })
    sample = _stratified_sample(df, "zip", target_n=30, seed=42)
    assert set(sample.columns) == {"zip", "name"}
    assert sample.height > 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
