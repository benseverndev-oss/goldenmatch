"""W2 shadow parity: the three distributional profilers (range_distribution,
sequence_detection, freshness) shadow-compute their fused native kernel alongside
the authoritative Polars compute. This test proves the shadow values MATCH the
Polars values the profiler emits -- i.e. the kernels are Flip-ready -- by running
each fixture's kernel on ``col.to_arrow()`` and comparing to the exact Polars ops
the profiler uses.

Skips gracefully when the relevant kernel isn't gated on / the extension isn't
built. The profilers themselves keep emitting the Polars findings unchanged; the
existing profiler tests (unedited) guard that. Here we only assert kernel ==
Polars on the shadow corpus.
"""
from __future__ import annotations

import datetime as _dt
import math

import polars as pl
import pyarrow as pa
import pytest
from goldencheck.core._native_loader import native_enabled, native_module

_REL_EPS = 1e-9

_EPOCH_DATE = _dt.date(1970, 1, 1)
_EPOCH_DT = _dt.datetime(1970, 1, 1)


def _float_close(native: float, polars: float) -> bool:
    return abs(native - polars) <= _REL_EPS * (1.0 + abs(polars))


# ---------------------------------------------------------------------------
# (a) range_distribution -> column_numeric_stats + count_outside
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not native_enabled("numeric_stats"), reason="numeric_stats kernel not gated on")
def test_range_distribution_shadow_matches_polars() -> None:
    # A numeric column with clear outliers so the +/-3 sigma branch fires.
    s = pl.Series("v", [0.0] * 50 + [10_000.5, -9_999.25, 12_345.0], dtype=pl.Float64)

    count, mn, mx, mean, std, _sum, _nu = native_module().column_numeric_stats(s.to_arrow())
    assert count == s.len() - s.null_count()
    assert _float_close(mn, s.min())
    assert _float_close(mx, s.max())
    assert _float_close(mean, s.mean())
    assert _float_close(std, s.std())

    # count_outside receives the POLARS-computed bounds (spec B1).
    lower = s.mean() - 3 * s.std()
    upper = s.mean() + 3 * s.std()
    non_null = s.drop_nulls()
    outliers = non_null.filter((non_null < lower) | (non_null > upper))
    exp_count = len(outliers)
    exp_sample = [str(v) for v in outliers.to_list()[:5]]
    assert exp_count > 0  # fixture must actually exercise the outlier branch

    out_count, out_sample = native_module().count_outside(s.to_arrow(), lower, upper)
    assert out_count == exp_count
    assert out_sample == exp_sample


@pytest.mark.skipif(not native_enabled("numeric_stats"), reason="numeric_stats kernel not gated on")
def test_range_distribution_shadow_int_dtype_sample() -> None:
    # Int column: the outlier sample must format as "10000" not "10000.0".
    s = pl.Series("i", [0] * 50 + [10_000, -10_000, 12_345], dtype=pl.Int64)
    lower = s.mean() - 3 * s.std()
    upper = s.mean() + 3 * s.std()
    non_null = s.drop_nulls()
    outliers = non_null.filter((non_null < lower) | (non_null > upper))
    exp_sample = [str(v) for v in outliers.to_list()[:5]]

    out_count, out_sample = native_module().count_outside(s.to_arrow(), lower, upper)
    assert out_count == len(outliers)
    assert out_sample == exp_sample


# ---------------------------------------------------------------------------
# (b) sequence_detection -> sequence_analysis
# ---------------------------------------------------------------------------
def _polars_seq_reference(s: pl.Series) -> tuple:
    non_null = s.drop_nulls()
    total = len(non_null)
    diffs = non_null.diff().drop_nulls()
    n_diffs = len(diffs)
    unit_diff_count = int((diffs == 1).sum())
    positive_diff_count = int((diffs > 0).sum())
    is_sorted = bool(non_null.is_sorted())
    col_min = int(non_null.min())
    col_max = int(non_null.max())
    present = set(non_null.unique().to_list())
    present_size = len(present)
    expected = col_max - col_min + 1
    if expected <= total:
        gap_count, gap_sample = 0, []
    else:
        gaps = [v for v in range(col_min, col_max + 1) if v not in present]
        gap_count, gap_sample = len(gaps), gaps[:10]
    return (
        n_diffs, unit_diff_count, positive_diff_count, is_sorted,
        col_min, col_max, present_size, gap_count, gap_sample,
    )


@pytest.mark.skipif(
    not native_enabled("sequence_analysis"), reason="sequence_analysis kernel not gated on"
)
def test_sequence_detection_shadow_matches_polars() -> None:
    # A gapped sequential int column (gaps at 4, 7, 8, 9).
    s = pl.Series("id", [1, 2, 3, 5, 6, 10], dtype=pl.Int64)
    got = native_module().sequence_analysis(s.to_arrow())
    assert got is not None
    n_diffs, unit, pos, is_sorted, mn, mx, present_size, gap_count, gap_sample = got
    actual = (n_diffs, unit, pos, is_sorted, mn, mx, present_size, gap_count, list(gap_sample))
    assert actual == _polars_seq_reference(s)
    assert gap_count > 0  # fixture must actually exercise the gap branch


# ---------------------------------------------------------------------------
# (c) freshness -> date_freshness
# ---------------------------------------------------------------------------
def _epoch_for_unit(ref: _dt.date | _dt.datetime, unit: str) -> int:
    if unit == "day":
        d = ref.date() if isinstance(ref, _dt.datetime) else ref
        return (d - _EPOCH_DATE).days
    dt = ref if isinstance(ref, _dt.datetime) else _dt.datetime(ref.year, ref.month, ref.day)
    delta = dt - _EPOCH_DT
    if unit == "us":
        return delta // _dt.timedelta(microseconds=1)
    raise AssertionError(f"unhandled unit {unit}")


@pytest.mark.skipif(
    not native_enabled("date_freshness"), reason="date_freshness kernel not gated on"
)
def test_freshness_shadow_date_matches_polars() -> None:
    # Date column with a future value (2999) relative to the profiler's `now`.
    now = _dt.date.today()
    s = pl.Series(
        "d",
        [_dt.date(1990, 1, 1), _dt.date(2999, 1, 1), _dt.date(2000, 6, 15)],
        dtype=pl.Date,
    )
    arr = s.to_arrow()
    assert pa.types.is_date32(arr.type)
    now_epoch = _epoch_for_unit(now, "day")
    got = native_module().date_freshness(arr, now_epoch)
    assert got is not None
    future_count, max_epoch = got

    non_null = s.drop_nulls()
    exp_future = non_null.filter(non_null > now).len()
    exp_max_epoch = _epoch_for_unit(non_null.max(), "day")
    assert future_count == exp_future
    assert exp_future > 0  # fixture must actually exercise the future branch
    assert max_epoch == exp_max_epoch


@pytest.mark.skipif(
    not native_enabled("date_freshness"), reason="date_freshness kernel not gated on"
)
def test_freshness_shadow_datetime_matches_polars() -> None:
    now = _dt.datetime.now()
    s = pl.Series(
        "t",
        [
            _dt.datetime(1990, 1, 1, 0, 0, 0),
            _dt.datetime(2999, 1, 1, 0, 0, 0),
            _dt.datetime(2000, 6, 15, 12, 30, 0),
        ],
        dtype=pl.Datetime("us"),
    )
    arr = s.to_arrow()
    assert pa.types.is_timestamp(arr.type) and arr.type.unit == "us"
    now_epoch = _epoch_for_unit(now, "us")
    got = native_module().date_freshness(arr, now_epoch)
    assert got is not None
    future_count, max_epoch = got

    non_null = s.drop_nulls()
    exp_future = non_null.filter(non_null > now).len()
    exp_max_epoch = _epoch_for_unit(non_null.max(), "us")
    assert future_count == exp_future
    assert exp_future > 0
    assert max_epoch == exp_max_epoch
    assert not math.isnan(float(max_epoch))
