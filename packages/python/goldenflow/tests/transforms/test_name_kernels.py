"""Direct pinned-vector parity for the multi-output / dataframe-mode names
kernels (Wave D names-remainder): ``split_name`` / ``split_name_reverse``
(1 column -> first+last) and ``merge_name`` (first+last -> full), plus
``initial_expand``'s flagged-rows list.

These transforms are ``mode="dataframe"`` (or return a flag list rather than a
transformed column), so they don't fit the shared string->scalar corpus in
``test_identifiers_parity.py`` (which feeds every row through a length-1 Arrow
*string* array and compares a scalar). Instead this file asserts both the
pure-Python fallback (``GOLDENFLOW_NATIVE=0``) and the native path (when
built/importable) against the goldenflow-core Rust kernel's values -- the same
pinned-vector pattern as ``test_numeric_kernels.py``.

The scalar names transforms (strip_titles/strip_suffixes/name_proper/
nickname_standardize/has_initial) DO fit the shared corpus and are covered in
``test_identifiers_parity.py`` instead.
"""
from __future__ import annotations

import polars as pl
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.transforms.names import (
    initial_expand,
    merge_name,
    split_name,
    split_name_reverse,
)

# (input column, expected first_name, expected last_name)
_SPLIT_NAME = (
    ["John Smith", "John Michael Smith", "Madonna", "  Jane  Doe  ", None],
    ["John", "John Michael", "Madonna", "Jane ", None],
    ["Smith", "Smith", "", "Doe", None],
)
_SPLIT_NAME_REVERSE = (
    ["Smith, John", "Smith,John", "Smith, John, Jr", "Madonna", None],
    ["John", "John", "John, Jr", "Madonna", None],
    ["Smith", "Smith", "Smith", "", None],
)
# (first_name column, last_name column, expected full_name)
_MERGE_NAME = (
    ["John", "John", None, "  John  ", None],
    ["Smith", None, None, "Smith", None],
    ["John Smith", "John", None, "  John   Smith", None],
)
# (input column, expected flagged row indices)
_INITIAL_EXPAND = (
    ["John Q. Public", "John Smith", "J. Smith", "J.Smith", None],
    [0, 2],
)


def _check_split(fn, vectors) -> None:
    inp, exp_first, exp_last = vectors
    out = fn(pl.DataFrame({"x": inp}), "x")
    assert out["first_name"].to_list() == exp_first
    assert out["last_name"].to_list() == exp_last


def _check_merge(vectors) -> None:
    first, last, exp_full = vectors
    out = merge_name(pl.DataFrame({"first_name": first, "last_name": last}), "first_name")
    assert out["full_name"].to_list() == exp_full


def _check_initial(vectors) -> None:
    inp, exp_flagged = vectors
    series, flagged = initial_expand(pl.Series("x", inp))
    assert flagged == exp_flagged
    assert series.to_list() == inp  # value output is the input unchanged


def _check_all() -> None:
    _check_split(split_name, _SPLIT_NAME)
    _check_split(split_name_reverse, _SPLIT_NAME_REVERSE)
    _check_merge(_MERGE_NAME)
    _check_initial(_INITIAL_EXPAND)


def test_fallback_matches_expected(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    _check_all()


def test_native_matches_expected(monkeypatch):
    if not native_available():
        import pytest

        pytest.skip("goldenflow-native not built/importable")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    if not hasattr(native_module(), "split_name_arrow"):
        import pytest

        pytest.skip("installed goldenflow-native predates the names_ext kernels")
    _check_all()
