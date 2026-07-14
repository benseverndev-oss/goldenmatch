"""autoconfig arrow-port PR-3: arrow->polars dtype-spelling contract + profile_columns
seam-routing parity.

The native ``autoconfig_classify_columns`` kernel (and the downstream string-column
check ``p.dtype.startswith("String"/"Utf8")``) is fed ``str(dtype)`` and expects the
POLARS dtype spelling. On an ``ArrowFrame`` ``str(dtype)`` spells the same types
differently ("double" vs "Float64", "large_string" vs "String", ...). ``profile_columns``
maps arrow->polars-spelling at the classify boundary so both backends feed the kernel
an identical dtype. These tests pin:

1. ``_arrow_to_polars_dtype_spelling`` per-dtype (arrow->polars; identity on polars
   spellings; ``str(raw)`` passthrough for unknowns).
2. ``profile_columns(pl.DataFrame)`` vs ``profile_columns(pa.Table)`` built from the
   SAME data produce byte-identical ``ColumnProfile``s INCLUDING the dtype fed to
   classify (the classify-input dtype string is the polars spelling on both).

Run with ``GOLDENMATCH_NATIVE=0`` so the native kernel is not required on the box;
the dtype-map + profile-dict equality hold on the pure-Python classify path too.
"""

from __future__ import annotations

import dataclasses

import polars as pl
import pytest
from goldenmatch.core.autoconfig import (
    _arrow_to_polars_dtype_spelling,
    profile_columns,
)


def _data() -> dict:
    # float / int / str / bool / null columns (the dtypes autoconfig sees).
    return {
        "amount": [1.0, 2.0, 3.0, 4.0],
        "count": [10, 20, 30, 40],
        "name": ["Alice", "Bob", "Carol", "Dave"],
        "flag": [True, False, True, False],
        "empty": [None, None, None, None],
    }


# ── the dtype-spelling map ────────────────────────────────────────────────────


def test_arrow_spellings_map_to_polars() -> None:
    assert _arrow_to_polars_dtype_spelling("double") == "Float64"
    assert _arrow_to_polars_dtype_spelling("float") == "Float32"
    assert _arrow_to_polars_dtype_spelling("int64") == "Int64"
    assert _arrow_to_polars_dtype_spelling("int32") == "Int32"
    assert _arrow_to_polars_dtype_spelling("bool") == "Boolean"
    assert _arrow_to_polars_dtype_spelling("null") == "Null"
    # string family -> the polars Utf8 spelling ("String" on modern polars)
    for arrow_str in ("string", "large_string", "utf8", "large_utf8"):
        assert _arrow_to_polars_dtype_spelling(arrow_str) in ("String", "Utf8")
    # date / timestamp families (unit-parametrized)
    assert _arrow_to_polars_dtype_spelling("date32[day]") == "Date"
    assert _arrow_to_polars_dtype_spelling("date64[ms]") == "Date"
    assert _arrow_to_polars_dtype_spelling("timestamp[us]").startswith("Datetime")
    assert _arrow_to_polars_dtype_spelling("timestamp[ns]").startswith("Datetime")


def test_map_is_identity_on_polars_spellings() -> None:
    # A polars spelling passed in comes back UNCHANGED -- this is what keeps the
    # polars path byte-for-byte what it was before the port.
    for pol in ("Float64", "Float32", "Int64", "Int32", "String", "Utf8", "Boolean",
                "Date", "Null", "Datetime(time_unit='us', time_zone=None)"):
        assert _arrow_to_polars_dtype_spelling(pol) == pol


def test_map_unknown_falls_back_to_str() -> None:
    assert _arrow_to_polars_dtype_spelling("some_future_dtype") == "some_future_dtype"
    assert _arrow_to_polars_dtype_spelling(123) == "123"


def test_map_string_target_matches_polars_native() -> None:
    # Arrow string maps to EXACTLY what polars renders Utf8 as, so the arrow
    # classify input equals the polars one (the differential-parity contract).
    native = str(pl.Series(["x"]).dtype)
    assert _arrow_to_polars_dtype_spelling("large_string") == native
    assert _arrow_to_polars_dtype_spelling("string") == native


# ── profile_columns cross-backend parity ──────────────────────────────────────


def test_profile_columns_dtype_is_polars_spelling_on_both_backends() -> None:
    pdf = pl.DataFrame(_data())
    tbl = pdf.to_arrow()  # SAME data as a pa.Table, bypassing the auto_configure unwrap

    by_name_pol = {p.name: p for p in profile_columns(pdf)}
    by_name_arr = {p.name: p for p in profile_columns(tbl)}

    assert set(by_name_pol) == set(by_name_arr) == {"amount", "count", "name", "flag", "empty"}

    # The dtype fed to classify is the polars spelling on BOTH backends.
    assert by_name_pol["amount"].dtype == "Float64"
    assert by_name_pol["count"].dtype == "Int64"
    assert by_name_pol["flag"].dtype == "Boolean"
    assert by_name_pol["empty"].dtype == "Null"
    assert by_name_pol["name"].dtype in ("String", "Utf8")

    for name in by_name_pol:
        assert by_name_arr[name].dtype == by_name_pol[name].dtype, name


def test_profile_columns_full_profile_parity_polars_vs_arrow() -> None:
    pdf = pl.DataFrame(_data())
    tbl = pdf.to_arrow()

    prof_pol = {p.name: dataclasses.asdict(p) for p in profile_columns(pdf)}
    prof_arr = {p.name: dataclasses.asdict(p) for p in profile_columns(tbl)}

    # Byte-identical column profiles (dtype, col_type, confidence, null_rate,
    # cardinality_ratio, avg_len, sample_values) regardless of backend.
    assert prof_pol == prof_arr


def test_profile_columns_accepts_pa_table_directly() -> None:
    # The routing (to_frame at the top) lets profile_columns run on a pa.Table
    # without the auto_configure_df unwrap -- the post-PR-6 shape, exercised now.
    tbl = pl.DataFrame(_data()).to_arrow()
    profiles = profile_columns(tbl)
    assert {p.name for p in profiles} == {"amount", "count", "name", "flag", "empty"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
