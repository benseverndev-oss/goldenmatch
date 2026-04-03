"""Tests for goldencheck.baseline.statistical — TDD baseline."""
from __future__ import annotations

import math

import polars as pl
import pytest

from goldencheck.baseline.statistical import profile_statistical


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_normal_df(n: int = 500, seed: int = 42) -> pl.DataFrame:
    """Return a DataFrame with one normally-distributed numeric column."""
    import numpy as np

    rng = np.random.default_rng(seed)
    values = rng.normal(loc=50.0, scale=10.0, size=n).tolist()
    return pl.DataFrame({"value": values})


def _make_lognormal_df(n: int = 500, seed: int = 42) -> pl.DataFrame:
    """Return a DataFrame with one log-normally-distributed numeric column."""
    import numpy as np

    rng = np.random.default_rng(seed)
    values = rng.lognormal(mean=3.0, sigma=0.5, size=n).tolist()
    return pl.DataFrame({"revenue": values})


# ---------------------------------------------------------------------------
# Distribution fitting
# ---------------------------------------------------------------------------


def test_normal_distribution_detected():
    """Normally-distributed column should be identified as 'normal'."""
    df = _make_normal_df(n=500)
    result = profile_statistical(df)
    assert "value" in result
    sp = result["value"]
    assert sp.distribution == "normal", f"Expected normal, got {sp.distribution!r}"
    assert sp.params is not None
    assert "loc" in sp.params or "mean" in sp.params


def test_lognormal_distribution_detected():
    """Log-normally-distributed column should be identified as 'log_normal'."""
    df = _make_lognormal_df(n=500)
    result = profile_statistical(df)
    assert "revenue" in result
    sp = result["revenue"]
    assert sp.distribution == "log_normal", f"Expected log_normal, got {sp.distribution!r}"
    assert sp.params is not None


def test_distribution_params_are_finite():
    """All fitted distribution params must be finite floats."""
    df = _make_normal_df(n=200)
    result = profile_statistical(df)
    sp = result["value"]
    if sp.params:
        for k, v in sp.params.items():
            assert math.isfinite(v), f"Param {k}={v} is not finite"


# ---------------------------------------------------------------------------
# Non-numeric columns are skipped (no distribution / entropy is string-based)
# ---------------------------------------------------------------------------


def test_skips_non_numeric_column_distribution():
    """String columns should have no distribution fit, only entropy + bounds."""
    df = pl.DataFrame({"category": ["a", "b", "c", "a", "b"] * 10})
    result = profile_statistical(df)
    assert "category" in result
    sp = result["category"]
    assert sp.distribution is None
    assert sp.params is None


# ---------------------------------------------------------------------------
# Minimum row count
# ---------------------------------------------------------------------------


def test_skips_column_with_too_few_rows():
    """Columns with fewer than 30 rows should be absent from output."""
    df = pl.DataFrame({"small": [1.0, 2.0, 3.0] * 5})  # 15 rows
    result = profile_statistical(df)
    assert "small" not in result


def test_processes_column_with_exactly_30_rows():
    """Columns with exactly 30 rows should be processed."""
    import numpy as np

    rng = np.random.default_rng(0)
    df = pl.DataFrame({"x": rng.normal(0, 1, 30).tolist()})
    result = profile_statistical(df)
    assert "x" in result


# ---------------------------------------------------------------------------
# Entropy
# ---------------------------------------------------------------------------


def test_high_entropy_many_unique_values():
    """A column with all unique values should have high entropy."""
    # 200 unique categories → maximum entropy
    categories = [f"cat_{i}" for i in range(200)] * 2  # 400 rows, 200 unique
    df = pl.DataFrame({"code": categories})
    result = profile_statistical(df)
    assert "code" in result
    sp = result["code"]
    assert sp.entropy > 4.0, f"Expected high entropy, got {sp.entropy}"


def test_zero_entropy_single_value():
    """A column with only one distinct value should have entropy ≈ 0."""
    df = pl.DataFrame({"constant": ["X"] * 50})
    result = profile_statistical(df)
    assert "constant" in result
    sp = result["constant"]
    assert sp.entropy == pytest.approx(0.0, abs=1e-9)


def test_numeric_entropy_positive():
    """Numeric columns should have a positive approximate entropy."""
    df = _make_normal_df(n=200)
    result = profile_statistical(df)
    sp = result["value"]
    assert sp.entropy > 0.0


# ---------------------------------------------------------------------------
# Percentile bounds
# ---------------------------------------------------------------------------


def test_percentile_bounds_present():
    """Numeric columns should have p01 and p99 in their bounds dict."""
    df = _make_normal_df(n=300)
    result = profile_statistical(df)
    sp = result["value"]
    assert "p01" in sp.bounds
    assert "p99" in sp.bounds


def test_percentile_bounds_ordering():
    """p01 must be less than p99 for non-constant numeric data."""
    df = _make_normal_df(n=300)
    result = profile_statistical(df)
    sp = result["value"]
    assert sp.bounds["p01"] < sp.bounds["p99"]


def test_percentile_bounds_reasonable_range():
    """p01/p99 should capture the approximate spread of normal(50, 10)."""
    df = _make_normal_df(n=1000)
    result = profile_statistical(df)
    sp = result["value"]
    # 1st percentile of N(50,10) is approximately 26.7; 99th ~73.3
    assert sp.bounds["p01"] < 35.0
    assert sp.bounds["p99"] > 65.0


# ---------------------------------------------------------------------------
# Benford's law
# ---------------------------------------------------------------------------


def test_benford_eligible_amount_column():
    """A column named 'amount' with wide-spanning positive values should get benford result."""
    import numpy as np

    rng = np.random.default_rng(1)
    # Values spanning >2 orders of magnitude (1 to ~10000)
    values = rng.lognormal(mean=4.0, sigma=2.0, size=200).tolist()
    df = pl.DataFrame({"amount": values})
    result = profile_statistical(df)
    assert "amount" in result
    sp = result["amount"]
    assert sp.benford is not None, "Expected benford result for 'amount' column"
    # benford dict should have digit keys 1-9
    assert "1" in sp.benford
    assert "9" in sp.benford


def test_benford_skipped_for_id_column():
    """ID-like columns should not get a Benford analysis."""
    # IDs spanning multiple orders of magnitude but semantic type = id
    values = [float(i) for i in range(100, 600)]  # 500 sequential IDs
    df = pl.DataFrame({"customer_id": values})
    result = profile_statistical(df)
    if "customer_id" in result:
        sp = result["customer_id"]
        assert sp.benford is None, "Benford should be skipped for ID columns"


def test_benford_skipped_for_percentage_column():
    """Percentage columns (0-100 range) should not get Benford analysis."""
    import numpy as np

    rng = np.random.default_rng(3)
    values = rng.uniform(0, 100, 200).tolist()
    df = pl.DataFrame({"discount_pct": values})
    result = profile_statistical(df)
    if "discount_pct" in result:
        sp = result["discount_pct"]
        assert sp.benford is None, "Benford should be skipped for percentage columns"


def test_benford_with_semantic_type_hint():
    """Passing semantic_types={'col': ['amount']} should trigger Benford for that column."""
    import numpy as np

    rng = np.random.default_rng(5)
    values = rng.lognormal(mean=4.0, sigma=2.0, size=200).tolist()
    df = pl.DataFrame({"value_col": values})
    # With semantic hint specifying 'amount'
    result_with_hint = profile_statistical(df, semantic_types={"value_col": ["amount"]})
    # The hint version should produce benford (if eligible by span)
    if "value_col" in result_with_hint:
        sp = result_with_hint["value_col"]
        # If spanning 2+ orders of magnitude, benford should be present
        assert sp.benford is not None


# ---------------------------------------------------------------------------
# Bounds for non-numeric columns
# ---------------------------------------------------------------------------


def test_string_column_bounds_contain_count():
    """String column bounds should contain at least 'n_unique'."""
    df = pl.DataFrame({"status": ["open", "closed", "pending"] * 20})
    result = profile_statistical(df)
    sp = result["status"]
    assert "n_unique" in sp.bounds
