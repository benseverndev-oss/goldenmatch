import polars as pl
from goldenflow.transforms.dates import (
    age_from_dob,
    date_eu,
    date_iso8601,
    date_parse,
    date_shift,
    date_us,
    date_validate,
    datetime_iso8601,
    extract_day,
    extract_day_of_week,
    extract_month,
    extract_quarter,
    extract_year,
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


def test_datetime_iso8601():
    s = pl.Series("d", ["March 15, 2024 3:30 PM", "2024-01-20 14:05:00", None])
    result = datetime_iso8601(s)
    assert result[0] == "2024-03-15T15:30:00"
    assert result[1] == "2024-01-20T14:05:00"
    assert result[2] is None


def test_datetime_iso8601_date_only_gets_midnight():
    s = pl.Series("d", ["2024-03-15"])
    result = datetime_iso8601(s)
    assert result[0] == "2024-03-15T00:00:00"


def test_extract_year():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023", "invalid", None])
    result = extract_year(s)
    assert result[0] == 2024
    assert result[1] == 2023
    assert result[2] is None
    assert result[3] is None


def test_extract_month():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023", "invalid", None])
    result = extract_month(s)
    assert result[0] == 3
    assert result[1] == 1
    assert result[2] is None
    assert result[3] is None


def test_date_shift_forward():
    s = pl.Series("d", ["2024-03-15", "2024-12-31"])
    result = date_shift(s, days=5)
    assert result[0] == "2024-03-20"
    assert result[1] == "2025-01-05"


def test_date_shift_backward():
    s = pl.Series("d", ["2024-03-15", None])
    result = date_shift(s, days=-10)
    assert result[0] == "2024-03-05"
    assert result[1] is None


def test_extract_day():
    s = pl.Series("d", ["2024-03-15", "Jan 1, 2023", "invalid", None])
    result = extract_day(s)
    assert result[0] == 15
    assert result[1] == 1
    assert result[2] is None
    assert result[3] is None


def test_extract_quarter():
    s = pl.Series("d", ["2024-01-15", "2024-04-01", "2024-07-31", "2024-10-05", None])
    result = extract_quarter(s)
    assert result[0] == 1
    assert result[1] == 2
    assert result[2] == 3
    assert result[3] == 4
    assert result[4] is None


def test_extract_day_of_week():
    # 2024-03-15 is a Friday
    s = pl.Series("d", ["2024-03-15", "2024-03-11", None])
    result = extract_day_of_week(s)
    assert result[0] == "Friday"
    assert result[1] == "Monday"
    assert result[2] is None


def test_date_validate():
    s = pl.Series("d", ["2024-03-15", "Jan 5, 2023", "not a date", "", None])
    result = date_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is False
    assert result[3] is False
    assert result[4] is None
