"""Direct pinned-vector parity for the multi-output ``split_address`` kernel
(Wave D address-simple): 1 column -> street/city/state/zip.

``split_address`` is ``mode="dataframe"`` (one input column -> four output
columns), so it doesn't fit the shared string->scalar corpus in
``test_identifiers_parity.py`` (which feeds every row through a length-1 Arrow
*string* array and compares a scalar). Instead this file asserts both the
pure-Python fallback (``GOLDENFLOW_NATIVE=0``) and the native path (when
built/importable) against the goldenflow-core Rust kernel's values -- the same
pinned-vector pattern as ``test_name_kernels.py`` / ``test_numeric_kernels.py``.

The 7 scalar address transforms (address_standardize/address_expand/
state_abbreviate/state_expand/zip_normalize/country_standardize/unit_normalize)
DO fit the shared corpus and are covered in ``test_identifiers_parity.py``.
"""
from __future__ import annotations

import polars as pl
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.transforms.address import split_address

# (input column, expected street, city, state, zip)
_SPLIT_ADDRESS = (
    [
        "123 Main St, Springfield, IL 62704",  # basic match
        "1 Park Ave, New York, NY 10001-2345",  # +4 ZIP
        "  9 Elm Rd, Denver, CO 80014  ",  # trimmed before parse
        "123 Main St, Apt 4, Springfield, IL 62704",  # city backtracks past a comma
        "  just a street  ",  # no match -> street = ORIGINAL (unstripped)
        "123 Main St, Springfield, ILL 62704",  # 3-letter state -> no match
        None,  # null -> all null
    ],
    [
        "123 Main St",
        "1 Park Ave",
        "9 Elm Rd",
        "123 Main St",
        "  just a street  ",
        "123 Main St, Springfield, ILL 62704",
        None,
    ],
    ["Springfield", "New York", "Denver", "Apt 4, Springfield", None, None, None],
    ["IL", "NY", "CO", "IL", None, None, None],
    ["62704", "10001-2345", "80014", "62704", None, None, None],
)


def _check_split_address() -> None:
    inp, exp_street, exp_city, exp_state, exp_zip = _SPLIT_ADDRESS
    out = split_address(pl.DataFrame({"addr": inp}), "addr")
    assert out["street"].to_list() == exp_street
    assert out["city"].to_list() == exp_city
    assert out["state"].to_list() == exp_state
    assert out["zip"].to_list() == exp_zip


def test_fallback_matches_expected(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    _check_split_address()


def test_native_matches_expected(monkeypatch):
    if not native_available():
        import pytest

        pytest.skip("goldenflow-native not built/importable")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    if not hasattr(native_module(), "split_address_arrow"):
        import pytest

        pytest.skip("installed goldenflow-native predates the address kernels")
    _check_split_address()
