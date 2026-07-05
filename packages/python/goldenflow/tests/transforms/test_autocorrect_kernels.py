"""Pinned-vector parity for the data-dependent category_auto_correct kernel
(Wave D). It builds a correction map from a column's value frequencies, so it
doesn't fit the string->scalar corpus. Asserts both the pure-Python fallback
(``GOLDENFLOW_NATIVE=0``, which uses the rapidfuzz-based ``_build_canonical_map``)
and the native path (goldenflow-core's ``autocorrect::build_canonical_map``)
produce the same corrected column -- the pinned-vector pattern.

The Python fallback IS the byte-exact reference: the Rust ``fuzz_ratio``
replicates rapidfuzz, and the Rust ordering replicates Python's Counter/dict
insertion order, so native == fallback on tie-free inputs (used here).
"""
from __future__ import annotations

import polars as pl
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.transforms.auto_correct import category_auto_correct

# Tie-free scenario: "active" dominant; case variants; a typo (fuzzy match);
# an unrelated low-freq value (no match); nulls preserved.
_COLUMN = (
    ["active"] * 50
    + ["Active"] * 10
    + ["ACTIVE"] * 5
    + ["actve"] * 2
    + ["banana"] * 1
    + [None] * 3
)
_EXPECTED = (
    ["active"] * 67  # active + Active + ACTIVE + actve all -> "active"
    + ["banana"] * 1  # below match_threshold vs "active" -> unchanged
    + [None] * 3
)


def _check() -> None:
    out = category_auto_correct(pl.Series("s", _COLUMN))
    assert out.to_list() == _EXPECTED


def test_fallback_matches_expected(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    _check()


def test_native_matches_expected(monkeypatch):
    if not native_available():
        import pytest

        pytest.skip("goldenflow-native not built/importable")
    if not hasattr(native_module(), "build_canonical_map_arrow"):
        import pytest

        pytest.skip("installed goldenflow-native predates the autocorrect kernel")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    _check()
