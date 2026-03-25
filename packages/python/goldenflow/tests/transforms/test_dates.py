import polars as pl

from goldenflow.transforms.dates import (
    age_from_dob,
    date_eu,
    date_iso8601,
    date_parse,
    date_us,
)


def test_date_iso8601():
    s = pl.Series("d", ["03/15/2024", "Jan 5, 2023", "2024-01-20", "invalid"])
    result = date_iso8601(s)
    assert result[0] == "2024-03-15"
    assert result[1] == "2023-01-05"
    assert result[2] == "2024-01-20"
    assert result[3] == "invalid"  # preserved


def test_date_us():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023"])
    result = date_us(s)
    assert result[0] == "03/15/2024"
    assert result[1] == "01/05/2023"


def test_date_eu():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023"])
    result = date_eu(s)
    assert result[0] == "15/03/2024"
    assert result[1] == "05/01/2023"


def test_date_parse():
    s = pl.Series("d", ["March 15, 2024", "15-03-2024", "2024/03/15"])
    result = date_parse(s)
    # All should parse to the same date, returned as ISO
    assert all(r == "2024-03-15" for r in result.to_list())


def test_age_from_dob():
    # Use a fixed reference date for deterministic tests
    s = pl.Series("d", ["1990-01-01", "2000-06-15"])
    result = age_from_dob(s, reference_date="2026-03-25")
    assert result[0] == 36
    assert result[1] == 25
