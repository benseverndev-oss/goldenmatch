"""Parity: the native `date_freshness` kernel must reproduce what the
`freshness` profiler gets from Polars via `col.count_gt(now)` + `col.max()`.

Both signals are exact integers (a count + a raw epoch), so the harness
registers `date_freshness` with an EMPTY divergence class.

CRITICAL (spec review B2): `now_epoch` is computed **offset-free** in the
array's native Arrow unit. Polars stores tz-naive `Date`/`Datetime` as
wall-clock values with NO tz shift (`Date -> date32[day]`,
`Datetime -> timestamp[us, None]`), so the epoch of a reference `date`/`datetime`
is a pure subtraction from the naive epoch. We NEVER use
`datetime.timestamp()` -- that applies the local UTC offset and would corrupt
both `now_epoch` and the `max_epoch` oracle (off by the machine's tz).

Both native + fallback lanes; skips cleanly when the extension isn't built."""
from __future__ import annotations

import datetime as _dt

import polars as pl
import pyarrow as pa
import pytest
from goldencheck.core._native_loader import native_available, native_module

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)

_EPOCH_DATE = _dt.date(1970, 1, 1)
_EPOCH_DT = _dt.datetime(1970, 1, 1)


def _arrow_unit(arr: pa.Array) -> str:
    """The freshness-relevant unit token for a temporal pyarrow array, mirroring
    the mapping the caller/profiler uses to pick `now_epoch`'s unit."""
    t = arr.type
    if pa.types.is_date32(t):
        return "day"
    if pa.types.is_date64(t):
        return "ms"
    if pa.types.is_timestamp(t):
        return t.unit  # "s" | "ms" | "us" | "ns"
    raise AssertionError(f"non-temporal array type: {t}")


def _epoch_offset_free(ref: _dt.date | _dt.datetime, unit: str) -> int:
    """Offset-free epoch of `ref` in `unit`. Pure subtraction from the naive
    1970-01-01 epoch -- NO `.timestamp()`, NO tz shift."""
    if unit == "day":
        assert not isinstance(ref, _dt.datetime)
        return (ref - _EPOCH_DATE).days
    dt = ref if isinstance(ref, _dt.datetime) else _dt.datetime(ref.year, ref.month, ref.day)
    delta = dt - _EPOCH_DT
    if unit == "s":
        return delta // _dt.timedelta(seconds=1)
    if unit == "ms":
        return delta // _dt.timedelta(milliseconds=1)
    if unit == "us":
        return delta // _dt.timedelta(microseconds=1)
    if unit == "ns":
        return (delta // _dt.timedelta(microseconds=1)) * 1000
    raise AssertionError(f"unknown unit: {unit}")


def _check(s: pl.Series, ref: _dt.date | _dt.datetime) -> None:
    arr = s.to_arrow()
    unit = _arrow_unit(arr)
    now_epoch = _epoch_offset_free(ref, unit)
    got = native_module().date_freshness(arr, now_epoch)

    non_null = s.drop_nulls()
    if len(non_null) == 0:
        assert got is None, f"kernel returned {got!r} for empty/all-null {s.dtype}"
        return

    assert got is not None, f"kernel declined a temporal column: {s.to_list()!r}"
    future_count, max_epoch = got

    # Oracle: exactly the profiler's Polars ops.
    exp_future = non_null.filter(non_null > ref).len()
    exp_max_epoch = _epoch_offset_free(non_null.max(), unit)

    assert future_count == exp_future, (
        f"future_count mismatch for {s.dtype}: native={future_count} polars={exp_future}"
    )
    assert max_epoch == exp_max_epoch, (
        f"max_epoch mismatch for {s.dtype}: native={max_epoch} polars={exp_max_epoch}"
    )


# ---------------------------------------------------------------------------
# Date columns (date32[day]).
# ---------------------------------------------------------------------------
_REF_DATE = _dt.date(2000, 1, 1)


def _date_series(days: list[int | None]) -> pl.Series:
    vals = [None if d is None else _EPOCH_DATE + _dt.timedelta(days=d) for d in days]
    return pl.Series("d", vals, dtype=pl.Date)


@native_only
def test_date_all_past() -> None:
    _check(_date_series([100, 5000, 10000]), _REF_DATE)  # all before 2000


@native_only
def test_date_some_future() -> None:
    # 2000-01-01 is day 10957; include values past it.
    _check(_date_series([100, 20000, 30000, 10957]), _REF_DATE)


@native_only
def test_date_boundary_equal_not_future() -> None:
    ref_day = (_REF_DATE - _EPOCH_DATE).days
    _check(_date_series([ref_day, ref_day + 1]), _REF_DATE)


@native_only
def test_date_with_nulls() -> None:
    _check(_date_series([100, None, 30000, None, 5000]), _REF_DATE)


@native_only
def test_date_pre_epoch() -> None:
    _check(_date_series([-500, -1, 100, 30000]), _REF_DATE)


@native_only
def test_date_all_null_declines() -> None:
    _check(pl.Series("d", [None, None], dtype=pl.Date), _REF_DATE)


@native_only
def test_date_empty_declines() -> None:
    _check(pl.Series("d", [], dtype=pl.Date), _REF_DATE)


# ---------------------------------------------------------------------------
# Datetime columns (timestamp[us, None]).
# ---------------------------------------------------------------------------
_REF_DT = _dt.datetime(2000, 1, 1, 12, 0, 0)


def _dt_series(micros_from_ref: list[int | None]) -> pl.Series:
    vals = [
        None if m is None else _REF_DT + _dt.timedelta(microseconds=m)
        for m in micros_from_ref
    ]
    return pl.Series("t", vals, dtype=pl.Datetime("us"))


@native_only
def test_datetime_all_past() -> None:
    _check(_dt_series([-1, -1_000_000, -999]), _REF_DT)


@native_only
def test_datetime_some_future() -> None:
    _check(_dt_series([-1_000_000, 1, 1_000_000, 0]), _REF_DT)  # 0 == ref, not future


@native_only
def test_datetime_boundary_equal_not_future() -> None:
    _check(_dt_series([0, 1]), _REF_DT)


@native_only
def test_datetime_with_nulls() -> None:
    _check(_dt_series([-5_000, None, 7_000, None]), _REF_DT)


@native_only
def test_datetime_all_null_declines() -> None:
    _check(pl.Series("t", [None, None], dtype=pl.Datetime("us")), _REF_DT)


@native_only
def test_datetime_empty_declines() -> None:
    _check(pl.Series("t", [], dtype=pl.Datetime("us")), _REF_DT)


# ---------------------------------------------------------------------------
# Non-temporal -> the kernel declines (None).
# ---------------------------------------------------------------------------
@native_only
def test_non_temporal_declined() -> None:
    assert native_module().date_freshness(pl.Series("i", [1, 2, 3]).to_arrow(), 0) is None
