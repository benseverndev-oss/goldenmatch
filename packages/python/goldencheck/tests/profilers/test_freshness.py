"""Tests for the freshness / staleness profiler."""
from __future__ import annotations

import datetime as dt

import polars as pl
from goldencheck.profilers.freshness import FreshnessProfiler


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def test_future_dated_datetime_flagged() -> None:
    now = dt.datetime.now()
    df = pl.DataFrame({"order_ts": [now - dt.timedelta(days=1), dt.datetime(2099, 1, 1)]})
    findings = FreshnessProfiler().profile(df, "order_ts")
    assert "future_dated" in _checks(findings)
    f = next(f for f in findings if f.check == "future_dated")
    assert f.affected_rows == 1


def test_future_dated_date_flagged() -> None:
    df = pl.DataFrame({"d": [dt.date.today(), dt.date(2099, 1, 1)]}).with_columns(pl.col("d").cast(pl.Date))
    assert "future_dated" in _checks(FreshnessProfiler().profile(df, "d"))


def test_no_future_no_finding() -> None:
    df = pl.DataFrame({"d": [dt.date(2020, 1, 1), dt.date(2021, 6, 1)]}).with_columns(pl.col("d").cast(pl.Date))
    # 'd' is not an update/event name, and not future -> silent.
    assert FreshnessProfiler().profile(df, "d") == []


def test_staleness_on_update_column() -> None:
    old = dt.date.today() - dt.timedelta(days=800)
    df = pl.DataFrame({"updated_at": [old, old - dt.timedelta(days=5)]}).with_columns(
        pl.col("updated_at").cast(pl.Date)
    )
    findings = FreshnessProfiler().profile(df, "updated_at")
    assert "stale_data" in _checks(findings)


def test_old_non_update_column_not_stale() -> None:
    # An old 'birth_date' is normal, not stale -> no stale_data finding.
    old = dt.date.today() - dt.timedelta(days=800)
    df = pl.DataFrame({"birth_date": [old]}).with_columns(pl.col("birth_date").cast(pl.Date))
    assert "stale_data" not in _checks(FreshnessProfiler().profile(df, "birth_date"))


def test_non_temporal_column_skipped() -> None:
    df = pl.DataFrame({"n": [1, 2, 3], "s": ["a", "b", "c"]})
    assert FreshnessProfiler().profile(df, "n") == []
    assert FreshnessProfiler().profile(df, "s") == []
