"""Tests for goldencheck.baseline.correlation — TDD baseline."""
from __future__ import annotations

import polars as pl

from goldencheck.baseline.correlation import _cramers_v, analyze_correlations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_perfect_numeric_df(n: int = 100) -> pl.DataFrame:
    """Return a DataFrame where col_b = col_a * 2 (perfect positive correlation)."""
    import numpy as np

    rng = np.random.default_rng(42)
    a = rng.normal(loc=10.0, scale=3.0, size=n).tolist()
    b = [v * 2.0 for v in a]
    return pl.DataFrame({"col_a": a, "col_b": b})


def _make_random_numeric_df(n: int = 200) -> pl.DataFrame:
    """Return a DataFrame with two independent random numeric columns."""
    import numpy as np

    rng = np.random.default_rng(99)
    return pl.DataFrame({
        "rand_x": rng.normal(size=n).tolist(),
        "rand_y": rng.normal(size=n).tolist(),
    })


def _make_perfect_cat_df(n: int = 100) -> pl.DataFrame:
    """Return a DataFrame where cat_b mirrors cat_a exactly (perfect Cramer's V)."""
    labels = ["red", "green", "blue"]
    cats = [labels[i % 3] for i in range(n)]
    return pl.DataFrame({"cat_a": cats, "cat_b": cats})


def _make_wide_df(n_cols: int = 50, n_rows: int = 100) -> pl.DataFrame:
    """Return a wide DataFrame with many numeric columns (stress test)."""
    import numpy as np

    rng = np.random.default_rng(7)
    data = {f"col_{i}": rng.normal(size=n_rows).tolist() for i in range(n_cols)}
    return pl.DataFrame(data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_perfect_numeric_correlation_detected_as_strong():
    """col_b = col_a * 2 should yield a strong Pearson correlation."""
    df = _make_perfect_numeric_df(n=100)
    results = analyze_correlations(df)

    assert results, "Expected at least one correlation entry"
    strong = [r for r in results if r.strength == "strong"]
    assert strong, f"Expected a strong correlation, got: {[r.model_dump() for r in results]}"

    entry = strong[0]
    assert set(entry.columns) == {"col_a", "col_b"}
    assert entry.measure == "pearson"
    assert abs(entry.value) >= 0.7


def test_random_columns_no_strong_correlation():
    """Independently drawn columns should not produce any strong correlations."""
    df = _make_random_numeric_df(n=200)
    results = analyze_correlations(df)

    strong = [r for r in results if r.strength == "strong"]
    assert not strong, f"Unexpected strong correlations on random data: {strong}"


def test_perfect_categorical_correlation_detected_as_strong():
    """cat_b == cat_a should yield a strong Cramer's V."""
    df = _make_perfect_cat_df(n=90)
    results = analyze_correlations(df)

    cramers = [r for r in results if r.measure == "cramers_v"]
    assert cramers, "Expected at least one Cramer's V entry"

    strong = [r for r in cramers if r.strength == "strong"]
    assert strong, f"Expected strong Cramer's V, got: {[r.model_dump() for r in cramers]}"
    entry = strong[0]
    assert set(entry.columns) == {"cat_a", "cat_b"}
    assert entry.value >= 0.7


def test_cramers_v_module_level_function():
    """_cramers_v should be callable directly and return a CorrelationEntry for perfect cats."""
    df = _make_perfect_cat_df(n=90)
    result = _cramers_v(df, "cat_a", "cat_b")

    assert result is not None
    assert result.measure == "cramers_v"
    assert result.strength == "strong"
    assert result.value >= 0.7


def test_cramers_v_returns_none_for_weak():
    """_cramers_v should return None when the association is weak (random categories)."""
    import numpy as np

    rng = np.random.default_rng(55)
    n = 100
    cats_a = [f"a{rng.integers(0, 5)}" for _ in range(n)]
    cats_b = [f"b{rng.integers(0, 5)}" for _ in range(n)]
    df = pl.DataFrame({"x": cats_a, "y": cats_b})
    result = _cramers_v(df, "x", "y")

    # Could be None (weak) or a moderate entry — it must NOT be strong
    if result is not None:
        assert result.strength != "strong", f"Expected weak or moderate, got: {result}"


def test_wide_dataframe_does_not_blowup(benchmark=None):
    """analyze_correlations should handle 50 columns without error (pair cap check)."""
    df = _make_wide_df(n_cols=50, n_rows=100)
    # Should complete without raising; result count is bounded by MAX_PAIRS
    results = analyze_correlations(df)
    # Just assert it runs and returns a list
    assert isinstance(results, list)


def test_only_moderate_and_strong_reported():
    """No weak (< 0.4) correlations should appear in the results."""
    df = _make_random_numeric_df(n=200)
    results = analyze_correlations(df)
    for entry in results:
        assert entry.strength in {"moderate", "strong"}, (
            f"Weak correlation leaked through: {entry.model_dump()}"
        )


def test_minimum_rows_respected():
    """DataFrames with fewer than 30 rows should yield no correlations."""
    import numpy as np

    rng = np.random.default_rng(1)
    df = pl.DataFrame({
        "a": rng.normal(size=20).tolist(),
        "b": rng.normal(size=20).tolist(),
    })
    results = analyze_correlations(df)
    assert results == [], f"Expected empty list for <30 rows, got: {results}"
