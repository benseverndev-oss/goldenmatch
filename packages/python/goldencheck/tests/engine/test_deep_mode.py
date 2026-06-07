"""Deep mode: profile the full population instead of the 100K sample."""
from __future__ import annotations

import polars as pl
from goldencheck.engine.scanner import scan_dataframe


def _big_df(rows: int) -> pl.DataFrame:
    return pl.DataFrame({
        "id": list(range(rows)),
        "grp": [i % 7 for i in range(rows)],
    })


def test_default_samples_above_cap() -> None:
    df = _big_df(120_000)
    _findings, _profile, sample = scan_dataframe(df, return_sample=True)
    assert sample.height == 100_000  # default 100K cap applied


def test_deep_uses_full_population() -> None:
    df = _big_df(120_000)
    _findings, profile, sample = scan_dataframe(df, return_sample=True, deep=True)
    assert sample.height == 120_000  # no sampling in deep mode
    # Profile row_count always reflects the full file, sampled or not.
    assert profile.row_count == 120_000


def test_deep_noop_below_cap() -> None:
    """Below the cap, deep and default both see every row (sampling is a no-op)."""
    df = _big_df(500)
    _f1, _p1, s_default = scan_dataframe(df, return_sample=True)
    _f2, _p2, s_deep = scan_dataframe(df, return_sample=True, deep=True)
    assert s_default.height == s_deep.height == 500
