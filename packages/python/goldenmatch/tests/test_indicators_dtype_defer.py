# tests/test_indicators_dtype_defer.py
"""The dtype-set constant must be a lazy function, not a module-level pl. evaluation."""
from __future__ import annotations


def test_non_identity_dtypes_is_lazy_function_with_expected_members():
    import polars as pl

    from goldenmatch.core.indicators import _non_identity_dtypes

    assert _non_identity_dtypes() == {pl.Boolean, pl.Date, pl.Datetime, pl.Time}
    # lru_cache: same object back on the second call
    assert _non_identity_dtypes() is _non_identity_dtypes()


def test_dead_boolean_dtypes_constant_removed():
    import goldenmatch.core.indicators as indicators

    assert not hasattr(indicators, "_BOOLEAN_DTYPES")
