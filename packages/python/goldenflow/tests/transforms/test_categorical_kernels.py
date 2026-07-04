"""Direct tests for the owned categorical kernels (Wave D5):
boolean_normalize / gender_standardize / null_standardize +
category_normalize_key.

The four string-input kernels are ALSO covered by the shared
``tests/parity/identifiers_corpus.jsonl`` byte-parity harness in
``test_identifiers_parity.py`` (both fallback and native paths, corpus-wide).
This file adds:
  - a small readable pinned-vector sanity check (mirrors
    ``test_numeric_kernels.py``'s pattern) for the three registered
    transforms,
  - coverage that ``category_standardize``/``category_from_file`` -- whose
    runtime-data mapping application can't fit the string-keyed corpus --
    still resolve correctly when their key-normalization step runs through
    the native kernel path (``category_normalize_key_native``).
"""
from __future__ import annotations

import polars as pl
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.transforms.categorical import (
    _category_normalize_key_series,
    boolean_normalize,
    category_from_file,
    category_standardize,
    gender_standardize,
    null_standardize,
)

_VECTORS = {
    "boolean_normalize": (
        boolean_normalize,
        ["Yes", "Y", "1", "True", "No", "N", "0", "False", "maybe", None],
        [True, True, True, True, False, False, False, False, None, None],
    ),
    "gender_standardize": (
        gender_standardize,
        ["Male", "m", "Female", "f", "Nonbinary", None],
        ["M", "M", "F", "F", "Nonbinary", None],
    ),
    "null_standardize": (
        null_standardize,
        ["N/A", "null", "  ", "actual value", None],
        [None, None, None, "actual value", None],
    ),
}


def test_fallback_matches_expected(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    for name, (fn, values, expected) in _VECTORS.items():
        result = fn(pl.Series("v", values)).to_list()
        assert result == expected, f"{name}: fallback mismatch"


_NATIVE_FLOOR_SYMBOL = {
    "boolean_normalize": "boolean_normalize_arrow",
    "gender_standardize": "gender_standardize_arrow",
    "null_standardize": "null_standardize_arrow",
}


def test_native_matches_expected(monkeypatch):
    if not native_available():
        import pytest

        pytest.skip("goldenflow-native not built/importable")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    nm = native_module()
    for name, (fn, values, expected) in _VECTORS.items():
        floor_symbol = _NATIVE_FLOOR_SYMBOL[name]
        if not hasattr(nm, floor_symbol):
            continue  # wheel skew: installed native predates this kernel
        result = fn(pl.Series("v", values)).to_list()
        assert result == expected, f"{name}: native mismatch"


def test_category_normalize_key_fallback(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    result = _category_normalize_key_series(
        pl.Series("v", ["  Yes  ", "USA", "MiXeD Case", "", None])
    ).to_list()
    assert result == ["yes", "usa", "mixed case", "", None]


def test_category_standardize_uses_key_kernel(monkeypatch):
    """category_standardize's dict lookup must still resolve correctly
    whichever path (native/fallback) derives the normalized key."""
    mapping = {"US": ["USA", "United States", "U.S.A."]}
    s = pl.Series("c", ["USA", "  united states  ", "Canada", None])

    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    result_fallback = category_standardize(s, mapping=mapping).to_list()
    assert result_fallback == ["US", "US", "Canada", None]

    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    result_auto = category_standardize(s, mapping=mapping).to_list()
    assert result_auto == result_fallback


def test_category_from_file_uses_key_kernel(monkeypatch, tmp_path):
    lookup = tmp_path / "countries.csv"
    lookup.write_text("variant,canonical\nUSA,US\nUnited States,US\n")
    s = pl.Series("c", ["USA", "  United States  ", "Canada", None])

    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    result_fallback = category_from_file(s, lookup_path=str(lookup)).to_list()
    assert result_fallback == ["US", "US", "Canada", None]

    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    result_auto = category_from_file(s, lookup_path=str(lookup)).to_list()
    assert result_auto == result_fallback


def test_boolean_normalize_null_propagates():
    result = boolean_normalize(pl.Series("v", ["yes", None])).to_list()
    assert result[1] is None


def test_gender_standardize_null_propagates():
    result = gender_standardize(pl.Series("v", ["m", None])).to_list()
    assert result[1] is None


def test_null_standardize_null_propagates():
    result = null_standardize(pl.Series("v", ["x", None])).to_list()
    assert result[1] is None
