"""Direct value-parity tests for the owned numeric-ARRAY-op kernels (Wave D4):
round / clamp / abs_value / fill_zero.

These four transforms take a NUMERIC column (not a string column) as input,
so they don't fit the shared string-keyed
``tests/parity/identifiers_corpus.jsonl`` harness in
``test_identifiers_parity.py`` (which feeds every row through a length-1
Arrow *string* array). Instead this file directly asserts both the
pure-Python fallback (``GOLDENFLOW_NATIVE=0``) and the native path (when
built/importable) against the goldenflow-core Rust kernel's VALUES,
mirroring ``test_url_kernels.py``'s "small readable pinned-vector" pattern.

The 5 string->number PARSER transforms (currency_strip, percentage_normalize,
to_integer, comma_decimal, scientific_to_decimal) DO fit the shared corpus
(their input is a string) and are covered there instead, with numeric-value
comparison (see ``_assert_value_parity`` in ``test_identifiers_parity.py``).
"""
from __future__ import annotations

import polars as pl
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.transforms.numeric import abs_value, clamp, fill_zero, round_values

_VECTORS = {
    "round_default_n2": (round_values, [2.345, -2.345, 1234.0, 0.005], {}, [2.35, -2.35, 1234.0, 0.01]),
    "round_n0": (
        round_values,
        [2.4, 2.5, -2.5],
        {"n": 0},
        [2.0, 3.0, -3.0],
    ),
    "round_negative_n": (
        round_values,
        [1234.0, -1250.0],
        {"n": -2},
        [1200.0, -1300.0],
    ),
    "clamp_default": (
        clamp,
        [-5.0, 0.0, 0.5, 1.5],
        {},
        [0.0, 0.0, 0.5, 1.0],
    ),
    "clamp_custom_bounds": (
        clamp,
        [-5.0, 0.0, 50.0, 150.0],
        {"min_val": 0.0, "max_val": 100.0},
        [0.0, 0.0, 50.0, 100.0],
    ),
    "abs_value": (
        abs_value,
        [-5.0, 3.0, -0.5, 0.0],
        {},
        [5.0, 3.0, 0.5, 0.0],
    ),
    "fill_zero": (
        fill_zero,
        [1.0, None, 3.0, None],
        {},
        [1.0, 0.0, 3.0, 0.0],
    ),
}


def test_fallback_matches_expected(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    for name, (fn, values, kwargs, expected) in _VECTORS.items():
        result = fn(pl.Series("v", values), **kwargs).to_list()
        assert result == expected, f"{name}: fallback mismatch"


_NATIVE_FLOOR_SYMBOL = {
    "round_default_n2": "round_arrow",
    "round_n0": "round_arrow",
    "round_negative_n": "round_arrow",
    "clamp_default": "clamp_arrow",
    "clamp_custom_bounds": "clamp_arrow",
    "abs_value": "abs_value_arrow",
    "fill_zero": "fill_zero_arrow",
}


def test_native_matches_expected(monkeypatch):
    if not native_available():
        import pytest

        pytest.skip("goldenflow-native not built/importable")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    nm = native_module()
    for name, (fn, values, kwargs, expected) in _VECTORS.items():
        floor_symbol = _NATIVE_FLOOR_SYMBOL[name]
        if not hasattr(nm, floor_symbol):
            continue  # wheel skew: installed native predates this kernel
        result = fn(pl.Series("v", values), **kwargs).to_list()
        assert result == expected, f"{name}: native mismatch"


def test_round_null_propagates():
    result = round_values(pl.Series("v", [1.0, None, 2.5])).to_list()
    assert result[1] is None


def test_clamp_null_propagates():
    result = clamp(pl.Series("v", [0.5, None])).to_list()
    assert result[1] is None


def test_abs_value_null_propagates():
    result = abs_value(pl.Series("v", [-1.0, None])).to_list()
    assert result[1] is None
