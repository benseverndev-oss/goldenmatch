# tests/test_indicators_dtype_defer.py
"""The dtype-set constant must be a lazy function, not a module-level pl. evaluation."""
from __future__ import annotations


def test_non_identity_dtypes_retired():
    # W3b: the identity-score dtype gate moved to Column.semantic_dtype
    # ("bool"/"date"); the lazy dtype-set helper is dead code and removed.
    import goldenmatch.core.indicators as indicators

    assert not hasattr(indicators, "_non_identity_dtypes")


def test_dead_boolean_dtypes_constant_removed():
    import goldenmatch.core.indicators as indicators

    assert not hasattr(indicators, "_BOOLEAN_DTYPES")
