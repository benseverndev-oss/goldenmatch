"""Tests for `core/blocking_candidates.py` (#408).

Foundation tests for the blocking-candidate classifier, composite-key
search, and avg-block-size estimator. Integration with auto-config
+ the fail-loud guard is covered in
`tests/test_blocking_candidates_e2e.py`.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.blocking_candidates import (
    ColumnRole,
    classify_column_role,
    degenerate_guard_threshold,
    estimate_avg_block_size,
    find_composite_blocking_keys,
)
from goldenmatch.core.quality_exclusions import ColumnProfile


def _profile(
    *,
    cardinality_ratio: float = 0.1,
    null_rate: float = 0.0,
    distinct_count: int = 100,
    dtype: str = "Utf8",
    mean_string_length: float | None = None,
) -> ColumnProfile:
    return ColumnProfile(
        cardinality_ratio=cardinality_ratio,
        null_rate=null_rate,
        distinct_count=distinct_count,
        dtype=dtype,
        mean_string_length=mean_string_length,
    )


# ---------------------------------------------------------------------------
# classify_column_role
# ---------------------------------------------------------------------------


def test_classify_rejects_near_unique_column():
    """NPI-like column (cardinality 1.0) excluded from blocking with
    a 'singleton blocks' reason."""
    role = classify_column_role(_profile(cardinality_ratio=1.0, distinct_count=1000))
    assert role.is_matchkey_candidate is True
    assert role.is_blocking_candidate is False
    assert role.blocking_excluded_reason is not None
    assert "singleton" in role.blocking_excluded_reason


def test_classify_rejects_above_max_ratio():
    """Above the max ratio gate (default 0.5) but below the 0.95 hard
    floor still gets rejected with a 'too unique' reason."""
    role = classify_column_role(_profile(cardinality_ratio=0.7, distinct_count=700))
    assert role.is_blocking_candidate is False
    assert role.blocking_excluded_reason is not None
    assert "too unique" in role.blocking_excluded_reason


def test_classify_accepts_mid_cardinality_column():
    """Zip-like column (cardinality 0.05) is a blocking candidate."""
    role = classify_column_role(_profile(cardinality_ratio=0.05, distinct_count=50))
    assert role.is_blocking_candidate is True
    assert role.blocking_excluded_reason is None


def test_classify_rejects_mega_block_risk_column():
    """Country-like column (cardinality 0.0001, distinct=5) -- low
    cardinality + few distinct values is the lifecycle/flag shape,
    which rejects before the mega-block branch runs. Either way
    blocking is False."""
    role = classify_column_role(
        _profile(cardinality_ratio=0.0001, distinct_count=5)
    )
    assert role.is_blocking_candidate is False
    assert role.blocking_excluded_reason is not None


def test_classify_rejects_mega_block_when_many_distinct_low_ratio():
    """A column with cardinality 0.0001 AND distinct_count=1000 means
    huge dataset with low-ratio key -- mega-block risk."""
    role = classify_column_role(
        _profile(cardinality_ratio=0.0001, distinct_count=1000)
    )
    assert role.is_blocking_candidate is False
    assert role.blocking_excluded_reason is not None
    assert "mega-block" in role.blocking_excluded_reason


def test_classify_rejects_lifecycle_distinct_count_le_10():
    """A boolean/lifecycle column with <=10 distinct values is rejected
    even if cardinality math doesn't trip the unique/mega gates."""
    role = classify_column_role(_profile(cardinality_ratio=0.01, distinct_count=3))
    assert role.is_blocking_candidate is False
    assert "distinct_count=3" in (role.blocking_excluded_reason or "")


def test_classify_env_var_overrides_bounds(monkeypatch):
    """User can override the default 0.5 cap via env var."""
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_MAX_RATIO", "0.9")
    role = classify_column_role(_profile(cardinality_ratio=0.7, distinct_count=700))
    # 0.7 < 0.9 now, so blocking-eligible (still below 0.95 hard floor).
    assert role.is_blocking_candidate is True


# ---------------------------------------------------------------------------
# find_composite_blocking_keys
# ---------------------------------------------------------------------------


def _make_role(name: str, *, is_blocking_candidate: bool = True) -> ColumnRole:
    return ColumnRole(
        name=name,
        is_matchkey_candidate=True,
        is_blocking_candidate=is_blocking_candidate,
        blocking_excluded_reason=None,
    )


def test_composite_finds_pair_in_target_band():
    """Synthetic fixture where neither zip nor last_name lands in the
    target band alone, but the composite hits it."""
    n = 1000
    # 50 unique zips, 50 unique last names: composite ~700-1000 distinct
    # depending on collision rate. n_rows=1000, target_avg=20 → target
    # cardinality = 50.
    df = pl.DataFrame({
        "zip": [f"{i % 50:05d}" for i in range(n)],
        "last_name": [f"name_{i % 50}" for i in range(n)],
        "irrelevant": [str(i) for i in range(n)],
    })
    roles = [_make_role("zip"), _make_role("last_name")]
    result = find_composite_blocking_keys(
        df, roles, target_avg_block_size=20,
    )
    assert result is not None
    assert set(result) == {"zip", "last_name"}


def test_composite_returns_none_when_no_pair_fits():
    """Per-record-unique fixture: every pair has joint cardinality = n,
    so no pair lands in [n/100, n/2]. Returns None."""
    n = 100
    df = pl.DataFrame({
        "id_a": [f"a_{i}" for i in range(n)],
        "id_b": [f"b_{i}" for i in range(n)],
    })
    roles = [_make_role("id_a"), _make_role("id_b")]
    result = find_composite_blocking_keys(df, roles)
    assert result is None


def test_composite_skips_columns_not_in_df():
    """Robust to roles referencing columns that don't exist in the
    passed DataFrame (defensive against stale role lists)."""
    df = pl.DataFrame({"zip": ["a"] * 100, "name": ["x"] * 100})
    roles = [
        _make_role("zip"),
        _make_role("phantom_col"),  # not in df
    ]
    # Two roles but only one in df -> can't form a pair.
    result = find_composite_blocking_keys(df, roles)
    assert result is None


def test_composite_only_considers_blocking_candidates():
    """Roles flagged as non-blocking-candidates are skipped from the
    search even if they're in the df."""
    n = 1000
    df = pl.DataFrame({
        "zip": [f"{i % 50:05d}" for i in range(n)],
        "last_name": [f"name_{i % 50}" for i in range(n)],
        "npi": [str(i) for i in range(n)],  # near-unique, excluded
    })
    roles = [
        _make_role("zip"),
        _make_role("last_name"),
        _make_role("npi", is_blocking_candidate=False),
    ]
    result = find_composite_blocking_keys(df, roles)
    assert result is not None
    assert "npi" not in result


# ---------------------------------------------------------------------------
# estimate_avg_block_size
# ---------------------------------------------------------------------------


def test_estimate_avg_block_size_on_zip_plus_lastname():
    """Synthetic sample where joint cardinality ≈ 50 over 1000 rows ->
    ~20 rows/block in the sample; scales linearly to full population."""
    n = 1000
    df = pl.DataFrame({
        "zip": [f"{i % 50:05d}" for i in range(n)],
        "last_name": [f"name_{i % 50}" for i in range(n)],
    })
    # Sample is the whole df here; scaled estimate should be ~20.
    estimate = estimate_avg_block_size(df, ["zip", "last_name"], n)
    assert estimate > 5.0
    assert estimate < 100.0


def test_estimate_avg_block_size_on_per_record_unique():
    """A unique-per-record key returns ~1."""
    n = 100
    df = pl.DataFrame({"id": [f"x_{i}" for i in range(n)]})
    estimate = estimate_avg_block_size(df, ["id"], n)
    assert estimate == pytest.approx(1.0, abs=0.1)


def test_estimate_avg_block_size_scales_to_full_population():
    """Sample is 100 rows; full population is 1M. Estimate scales the
    sample-distinct linearly to the projected full-pop cardinality."""
    n_sample = 100
    n_full = 1_000_000
    df = pl.DataFrame({
        "zip": [f"{i % 10:05d}" for i in range(n_sample)],  # 10 zips
    })
    # Sample: 10 distinct → ~10 rows/block in sample.
    # Scaled: 10 * (1M/100) = 100K distinct in full pop → 10 rows/block.
    estimate = estimate_avg_block_size(df, ["zip"], n_full)
    assert estimate == pytest.approx(10.0, abs=1.0)


def test_estimate_returns_1_when_no_fields():
    """Empty blocking config -> degenerate, estimate = 1.0."""
    df = pl.DataFrame({"zip": ["a", "b", "c"]})
    assert estimate_avg_block_size(df, [], 1000) == 1.0


def test_estimate_returns_1_when_fields_missing_from_df():
    """All requested fields absent -> degenerate."""
    df = pl.DataFrame({"zip": ["a", "b", "c"]})
    assert estimate_avg_block_size(df, ["missing"], 1000) == 1.0


# ---------------------------------------------------------------------------
# degenerate_guard_threshold env-var
# ---------------------------------------------------------------------------


def test_degenerate_guard_default():
    assert degenerate_guard_threshold() == 2.0


def test_degenerate_guard_env_override(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_DEGENERATE_THRESHOLD", "3.5")
    assert degenerate_guard_threshold() == 3.5


def test_degenerate_guard_env_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_DEGENERATE_THRESHOLD", "not-a-float")
    assert degenerate_guard_threshold() == 2.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
