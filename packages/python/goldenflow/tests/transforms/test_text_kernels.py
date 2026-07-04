"""Direct pinned-vector parity for the PARAMETERIZED text kernels (Wave D
text-1): ``truncate(n)`` / ``pad_left(width, char)`` / ``pad_right(width,
char)``.

These carry per-column-constant params (like numeric ``round(n)`` /
``clamp(min,max)``), so a single ``(transform, input) -> expected`` corpus row
can't express the non-default-param cases. This file asserts both the
pure-Python fallback (``GOLDENFLOW_NATIVE=0``) and the native path (when
built/importable) against the goldenflow-core Rust kernel's values -- the same
pinned-vector pattern as ``test_numeric_kernels.py`` / ``test_name_kernels.py``.

The 10 non-parameterized text transforms (strip/collapse_whitespace/... /
extract_numbers) fit the shared corpus and are covered in
``test_identifiers_parity.py``.
"""
from __future__ import annotations

import polars as pl
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.transforms.text import pad_left, pad_right, truncate


def _apply(expr_fn, col_values: list[str | None], *args) -> list[str | None]:
    df = pl.DataFrame({"x": col_values})
    return df.select(expr_fn("x", *args)).to_series().to_list()


# (transform-fn, args, input column, expected output)
_CASES = [
    (truncate, (5,), ["hello world", "hi", "", None], ["hello", "hi", "", None]),
    (truncate, (0,), ["abc", None], ["", None]),
    # char-based, not byte (accented char counts as 1)
    (truncate, (4,), ["cafés", None], ["café", None]),
    (pad_left, (5, "0"), ["42", "already?", None], ["00042", "already?", None]),
    (pad_left, (3, "0"), ["already", None], ["already", None]),  # len >= width
    (pad_right, (5, " "), ["42", None], ["42   ", None]),
    (pad_right, (4, "."), ["ab", None], ["ab..", None]),
]


def _check_all() -> None:
    for fn, args, inp, expected in _CASES:
        assert _apply(fn, inp, *args) == expected


def test_fallback_matches_expected(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    _check_all()


def test_native_matches_expected(monkeypatch):
    if not native_available():
        import pytest

        pytest.skip("goldenflow-native not built/importable")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    if not hasattr(native_module(), "truncate_arrow"):
        import pytest

        pytest.skip("installed goldenflow-native predates the text kernels")
    _check_all()
