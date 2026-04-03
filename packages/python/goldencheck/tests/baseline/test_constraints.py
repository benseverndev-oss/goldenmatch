"""Tests for goldencheck.baseline.constraints — TDD."""
from __future__ import annotations

import polars as pl
import pytest

from goldencheck.baseline.constraints import mine_constraints


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zip_city_df(n: int = 200) -> pl.DataFrame:
    """DataFrame where zip_code deterministically determines city."""
    rows = n // 4
    zips = ["10001", "10002", "10003", "10004"] * rows
    cities = ["New York", "Brooklyn", "Queens", "Bronx"] * rows
    return pl.DataFrame({"zip_code": zips, "city": cities, "noise": list(range(n))})


def _approx_fd_df(n: int = 200) -> pl.DataFrame:
    """zip_code -> city holds for ~98 % of rows (2 exceptions)."""
    zips = ["10001", "10002", "10003", "10004"] * (n // 4)
    cities = ["New York", "Brooklyn", "Queens", "Bronx"] * (n // 4)
    # corrupt 2 rows
    cities_list = list(cities)
    cities_list[0] = "WRONG"
    cities_list[n // 2] = "WRONG"
    return pl.DataFrame({"zip_code": zips, "city": cities_list, "noise": list(range(n))})


def _random_df(n: int = 200) -> pl.DataFrame:
    """No functional dependency between columns."""
    import random

    rng = random.Random(42)
    a = [rng.choice(["x", "y", "z"]) for _ in range(n)]
    b = [rng.choice(["p", "q", "r"]) for _ in range(n)]
    return pl.DataFrame({"a": a, "b": b})


# ---------------------------------------------------------------------------
# Functional dependency tests
# ---------------------------------------------------------------------------


def test_exact_fd_discovered():
    """zip_code -> city should be discovered with confidence 1.0."""
    df = _zip_city_df(200)
    fds, _, _ = mine_constraints(df, min_confidence=0.95)
    assert any(
        "zip_code" in fd.determinant and "city" in fd.dependent for fd in fds
    ), f"Expected zip_code->city FD, got: {fds}"


def test_exact_fd_confidence_is_one():
    """Exact FD should have confidence == 1.0."""
    df = _zip_city_df(200)
    fds, _, _ = mine_constraints(df, min_confidence=0.95)
    fd = next(
        (fd for fd in fds if "zip_code" in fd.determinant and "city" in fd.dependent),
        None,
    )
    assert fd is not None
    assert fd.confidence == pytest.approx(1.0)


def test_approximate_fd_discovered():
    """zip_code -> city should be found even with ~2 % violations (conf ~0.98)."""
    df = _approx_fd_df(200)
    fds, _, _ = mine_constraints(df, min_confidence=0.95)
    assert any(
        "zip_code" in fd.determinant and "city" in fd.dependent for fd in fds
    ), f"Expected approx zip_code->city FD, got: {fds}"


def test_approximate_fd_confidence_below_one():
    """Approximate FD should have confidence strictly < 1.0."""
    df = _approx_fd_df(200)
    fds, _, _ = mine_constraints(df, min_confidence=0.95)
    fd = next(
        (fd for fd in fds if "zip_code" in fd.determinant and "city" in fd.dependent),
        None,
    )
    assert fd is not None
    assert fd.confidence < 1.0
    assert fd.confidence >= 0.95


def test_no_fd_on_random_data():
    """Random column pairs should not produce FDs above 0.95."""
    df = _random_df(200)
    fds, _, _ = mine_constraints(df, min_confidence=0.95)
    assert fds == [], f"Expected no FDs on random data, got: {fds}"


def test_fds_merged_same_determinant():
    """A->B and A->C should be merged into a single FD A->[B,C]."""
    # state_code -> city AND state_code -> region both hold
    state = ["CA", "NY", "TX", "FL"] * 50
    city = ["LA", "NYC", "Dallas", "Miami"] * 50
    region = ["West", "East", "South", "South"] * 50
    df = pl.DataFrame({"state_code": state, "city": city, "region": region})
    fds, _, _ = mine_constraints(df, min_confidence=0.95)
    # Find FD with state_code as determinant
    sc_fds = [fd for fd in fds if fd.determinant == ["state_code"]]
    assert len(sc_fds) == 1, f"Expected merged FD, got: {sc_fds}"
    assert set(sc_fds[0].dependent) == {"city", "region"}


# ---------------------------------------------------------------------------
# Column limit test
# ---------------------------------------------------------------------------


def test_column_limit_does_not_blow_up():
    """40 columns should not cause exponential blowup — completes quickly."""
    import random
    import time

    rng = random.Random(0)
    data = {f"col_{i}": [rng.choice(["a", "b", "c"]) for _ in range(50)] for i in range(40)}
    df = pl.DataFrame(data)

    start = time.monotonic()
    fds, keys, temporal = mine_constraints(df)
    elapsed = time.monotonic() - start
    # Should complete in under 10 seconds even on slow CI
    assert elapsed < 10.0, f"mine_constraints took {elapsed:.2f}s with 40 columns"


# ---------------------------------------------------------------------------
# Candidate key tests
# ---------------------------------------------------------------------------


def test_detects_unique_column_as_candidate_key():
    """A column with all unique non-null values should be a candidate key."""
    df = pl.DataFrame({
        "id": list(range(100)),
        "name": [f"user_{i}" for i in range(100)],
        "group": ["A", "B"] * 50,
    })
    _, keys, _ = mine_constraints(df, min_confidence=0.95)
    # Both id and name are unique — at least one should be found
    key_cols = [k[0] for k in keys if len(k) == 1]
    assert "id" in key_cols or "name" in key_cols, f"Expected id or name as key, got: {keys}"


def test_non_unique_column_not_a_key():
    """A column with duplicates should not be reported as a candidate key."""
    df = pl.DataFrame({
        "id": list(range(100)),
        "group": ["A", "B"] * 50,
    })
    _, keys, _ = mine_constraints(df, min_confidence=0.95)
    key_cols = [k[0] for k in keys if len(k) == 1]
    assert "group" not in key_cols


def test_column_with_nulls_not_a_key():
    """A column with null values should not be reported as a candidate key."""
    ids = [i if i % 10 != 0 else None for i in range(100)]
    df = pl.DataFrame({"id": ids, "val": list(range(100))})
    _, keys, _ = mine_constraints(df, min_confidence=0.95)
    key_cols = [k[0] for k in keys if len(k) == 1]
    assert "id" not in key_cols


# ---------------------------------------------------------------------------
# Temporal order tests
# ---------------------------------------------------------------------------


def test_discovers_date_ordering():
    """start_date < end_date should be detected as a temporal order."""
    from datetime import date, timedelta

    base = date(2020, 1, 1)
    starts = [base + timedelta(days=i) for i in range(100)]
    ends = [s + timedelta(days=30) for s in starts]
    df = pl.DataFrame({
        "start_date": [str(s) for s in starts],
        "end_date": [str(e) for e in ends],
    })
    _, _, temporal = mine_constraints(
        df, date_columns=["start_date", "end_date"]
    )
    assert any(
        t.before == "start_date" and t.after == "end_date" for t in temporal
    ), f"Expected start_date->end_date order, got: {temporal}"


def test_temporal_violation_rate_zero_for_perfect_order():
    """Perfect ordering should have violation_rate == 0.0."""
    from datetime import date, timedelta

    base = date(2020, 1, 1)
    starts = [base + timedelta(days=i) for i in range(100)]
    ends = [s + timedelta(days=1) for s in starts]
    df = pl.DataFrame({
        "start_date": [str(s) for s in starts],
        "end_date": [str(e) for e in ends],
    })
    _, _, temporal = mine_constraints(df, date_columns=["start_date", "end_date"])
    t = next(
        (t for t in temporal if t.before == "start_date" and t.after == "end_date"), None
    )
    assert t is not None
    assert t.violation_rate == pytest.approx(0.0)


def test_temporal_violation_rate_recorded():
    """20 % violations should be recorded accurately."""
    from datetime import date, timedelta

    base = date(2020, 1, 1)
    starts = [base + timedelta(days=i) for i in range(100)]
    ends = [s + timedelta(days=1) for s in starts]
    # Flip 20 pairs so end < start
    ends_list = list(ends)
    for i in range(20):
        ends_list[i] = starts[i] - timedelta(days=1)
    df = pl.DataFrame({
        "start_date": [str(s) for s in starts],
        "end_date": [str(e) for e in ends_list],
    })
    _, _, temporal = mine_constraints(df, date_columns=["start_date", "end_date"])
    t = next(
        (t for t in temporal if t.before == "start_date" and t.after == "end_date"), None
    )
    assert t is not None
    assert t.violation_rate == pytest.approx(0.20, abs=0.01)


def test_temporal_reversed_reported_correctly():
    """When majority violates, direction should be reversed."""
    from datetime import date, timedelta

    base = date(2020, 1, 1)
    # end_date is usually BEFORE start_date (i.e. end < start most of the time)
    starts = [base + timedelta(days=i * 2 + 1) for i in range(100)]
    ends = [base + timedelta(days=i * 2) for i in range(100)]
    df = pl.DataFrame({
        "start_date": [str(s) for s in starts],
        "end_date": [str(e) for e in ends],
    })
    _, _, temporal = mine_constraints(df, date_columns=["start_date", "end_date"])
    # The "natural" direction is reversed: end_date -> start_date
    assert any(
        t.before == "end_date" and t.after == "start_date" for t in temporal
    ), f"Expected reversed order, got: {temporal}"


# ---------------------------------------------------------------------------
# Minimum rows guard
# ---------------------------------------------------------------------------


def test_returns_empty_for_small_df():
    """DataFrames with fewer than 30 rows should return empty results."""
    df = pl.DataFrame({"a": list(range(10)), "b": list(range(10))})
    fds, keys, temporal = mine_constraints(df)
    assert fds == []
    assert keys == []
    assert temporal == []
