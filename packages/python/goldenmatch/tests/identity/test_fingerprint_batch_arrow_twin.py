# tests/identity/test_fingerprint_batch_arrow_twin.py
"""W4b-2: the pa.Table fingerprint twin is cross-pinned against the polars
path -- byte-identical fingerprints per row, identical routing masks."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from goldenmatch.identity.fingerprint_batch import (
    batch_fingerprints,
    batch_fingerprints_table,
    canonicalize_records_df,
    canonicalize_records_table,
)


def _cross_pin(df: pl.DataFrame):
    tbl = df.to_arrow()
    assert batch_fingerprints_table(tbl) == batch_fingerprints(df)


def test_clean_primitives_parity():
    _cross_pin(
        pl.DataFrame(
            {
                "name": ["alice", "bob", None],
                "age": [30, None, 41],
                "score": [1.5, 2.0, None],
                "active": [True, False, None],
            }
        )
    )


def test_temporal_parity():
    _cross_pin(
        pl.DataFrame(
            {
                "d": [dt.date(2024, 1, 5), None, dt.date(1999, 12, 31)],
                "t": [
                    dt.datetime(2024, 1, 5, 12, 30, 45, 123456),
                    dt.datetime(2024, 1, 5, 12, 30, 45),
                    None,
                ],
            },
            schema={"d": pl.Date, "t": pl.Datetime},
        )
    )


def test_int_upcasts_and_uint64_overflow_parity():
    df = pl.DataFrame(
        {
            "i8": pl.Series([1, -2, None], dtype=pl.Int8),
            "u32": pl.Series([7, 8, 9], dtype=pl.UInt32),
            "u64": pl.Series([1, 2**63, None], dtype=pl.UInt64),  # row 1 overflows
        }
    )
    _, mask_pl = canonicalize_records_df(df)
    _, mask_pa = canonicalize_records_table(df.to_arrow())
    assert mask_pl == mask_pa == [False, True, False]
    _cross_pin(df)


def test_nonfinite_floats_route_per_row_parity():
    df = pl.DataFrame({"x": [1.0, float("nan"), float("inf"), None]})
    _, mask_pl = canonicalize_records_df(df)
    _, mask_pa = canonicalize_records_table(df.to_arrow())
    assert mask_pl == mask_pa == [False, True, True, False]
    _cross_pin(df)


def test_unbatchable_column_routes_whole_frame_parity():
    df = pl.DataFrame({"b": [b"x", b"y"], "n": ["a", "b"]})
    batch_pl, mask_pl = canonicalize_records_df(df)
    batch_pa, mask_pa = canonicalize_records_table(df.to_arrow())
    assert batch_pl is None and batch_pa is None
    assert mask_pl == mask_pa == [True, True]
    _cross_pin(df)


def test_all_null_untyped_column_parity():
    df = pl.DataFrame({"z": [None, None], "n": ["a", "b"]})
    _cross_pin(df)


def test_dunder_columns_dropped_parity():
    df = pl.DataFrame({"name": ["a", "b"], "__row_id__": [1, 2]})
    plain = pl.DataFrame({"name": ["a", "b"]})
    assert batch_fingerprints_table(df.to_arrow()) == batch_fingerprints(plain)


def test_categorical_parity():
    df = pl.DataFrame({"c": pl.Series(["x", "y", "x"], dtype=pl.Categorical)})
    _cross_pin(df)


@pytest.mark.parametrize("unit,tz", [("ms", None), ("us", "UTC")])
def test_nonus_or_tz_datetime_unbatchable_both(unit, tz):
    df = pl.DataFrame(
        {"t": [dt.datetime(2024, 1, 5, 12, 0, 0)]},
        schema={"t": pl.Datetime(time_unit=unit, time_zone=tz)},
    )
    batch_pl, mask_pl = canonicalize_records_df(df)
    batch_pa, mask_pa = canonicalize_records_table(df.to_arrow())
    assert batch_pl is None and batch_pa is None
    assert mask_pl == mask_pa == [True]
    _cross_pin(df)
