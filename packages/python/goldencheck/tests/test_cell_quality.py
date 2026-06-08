"""Tests for the per-cell quality API (the GoldenMatch survivorship bridge)."""
from __future__ import annotations

import datetime as dt

import polars as pl
from goldencheck import cell_quality


def test_penalizes_fuzzy_variants_not_canonical() -> None:
    # "California" appears most often -> canonical; "Californa"/"CALIFORNIA" are
    # penalized variants. Build with a clear frequency majority.
    states = ["California"] * 30 + ["Californa"] * 2 + ["CALIFORNIA"] * 1 + ["Texas"] * 30
    df = pl.DataFrame({"state": states})
    scores = cell_quality(df)

    # The two 'Californa' rows (indices 30, 31) and one 'CALIFORNIA' (index 32)
    # are penalized; the 'California' and 'Texas' rows are not.
    penalized_cols = {col for (_i, col) in scores}
    assert penalized_cols == {"state"}
    penalized_rows = {i for (i, _c) in scores}
    assert penalized_rows == {30, 31, 32}
    assert all(0 < w < 1 for w in scores.values())
    # A canonical 'California' cell (index 0) is clean (absent).
    assert (0, "state") not in scores


def test_penalizes_future_dates() -> None:
    df = pl.DataFrame({
        "event": [dt.date(2020, 1, 1), dt.date(2099, 1, 1), dt.date(2021, 6, 1)],
    }).with_columns(pl.col("event").cast(pl.Date))
    # need >=2 rows; cell_quality runs regardless of column-name (per-cell)
    scores = cell_quality(df)
    assert (1, "event") in scores  # the 2099 row
    assert (0, "event") not in scores


def test_clean_frame_is_empty() -> None:
    df = pl.DataFrame({"name": ["alice", "bob", "carol"] * 40, "n": list(range(120))})
    assert cell_quality(df) == {}


def test_skips_internal_columns() -> None:
    states = ["California"] * 30 + ["Californa"] * 3 + ["Texas"] * 30
    df = pl.DataFrame({"__row_id__": list(range(63)), "__state__": states})
    # both columns are internal-prefixed -> skipped entirely
    assert cell_quality(df) == {}
