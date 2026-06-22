"""S2b (spec 2026-06-22-autoconfig-smarter-faster-s1-s3): adaptive sparse-match
floor.

`sparse_match_floor(estimated_pairs) = min(50, estimated_pairs // 100)` — the
floor stays 50 for high-yield datasets and scales down for small-yield ones so
`estimate_sparse_match_signal` doesn't over-trigger sparse-match expansion.
"""
from __future__ import annotations

import polars as pl

from goldenmatch.core.indicators import (
    _sparse_match_floor_py,
    estimate_sparse_match_signal,
    sparse_match_floor,
)


def test_floor_caps_at_50():
    assert _sparse_match_floor_py(5_000) == 50
    assert _sparse_match_floor_py(1_000_000) == 50
    assert sparse_match_floor(5_000) == 50


def test_floor_scales_down_for_low_yield():
    assert _sparse_match_floor_py(0) == 0
    assert _sparse_match_floor_py(100) == 1
    assert _sparse_match_floor_py(1_000) == 10
    assert _sparse_match_floor_py(4_900) == 49


def test_floor_boundary():
    assert _sparse_match_floor_py(4_999) == 49
    assert _sparse_match_floor_py(5_000) == 50
    assert _sparse_match_floor_py(5_001) == 50


def test_floor_dispatcher_clamps_negative():
    assert sparse_match_floor(-5) == 0


def _df_with_collisions(n_groups: int, group_size: int) -> pl.DataFrame:
    """Build a frame whose exact key 'k' yields n_groups blocks of group_size."""
    vals = []
    for g in range(n_groups):
        vals.extend([f"g{g}"] * group_size)
    return pl.DataFrame({"k": vals})


def test_estimated_pairs_lowers_the_bar_for_small_yield():
    # 3 groups of size 2 -> sample n_pairs = 3 (each pair counts 1). With the
    # fixed floor of 50 this is "sparse"; with a small estimated_pairs the
    # adaptive floor drops below 3 so it is NOT flagged sparse.
    df = _df_with_collisions(n_groups=3, group_size=2)
    fixed = estimate_sparse_match_signal(df, exact_columns=["k"])
    assert fixed.estimated_n_true_pairs == 3
    assert fixed.is_sparse is True  # 3 < 50

    # estimated_pairs=200 -> floor = min(50, 2) = 2; 3 >= 2 -> NOT sparse.
    adaptive = estimate_sparse_match_signal(
        df, exact_columns=["k"], estimated_pairs=200
    )
    assert adaptive.estimated_n_true_pairs == 3
    assert adaptive.is_sparse is False


def test_estimated_pairs_keeps_floor_50_for_high_yield():
    # estimated_pairs large -> floor stays 50, same verdict as the fixed default.
    df = _df_with_collisions(n_groups=3, group_size=2)
    adaptive = estimate_sparse_match_signal(
        df, exact_columns=["k"], estimated_pairs=10_000_000
    )
    assert adaptive.is_sparse is True  # 3 < 50


def test_none_estimated_pairs_preserves_fixed_default():
    df = _df_with_collisions(n_groups=3, group_size=2)
    a = estimate_sparse_match_signal(df, exact_columns=["k"])
    b = estimate_sparse_match_signal(df, exact_columns=["k"], estimated_pairs=None)
    assert a == b
