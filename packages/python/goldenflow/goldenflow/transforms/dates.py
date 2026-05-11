from __future__ import annotations

from datetime import date, timedelta

import polars as pl
from dateutil import parser as dateutil_parser

from goldenflow.transforms import register_transform


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return dateutil_parser.parse(val).date()
    except (ValueError, OverflowError):
        return None


@register_transform(
    name="date_iso8601", input_types=["date"], auto_apply=True, priority=50, mode="series"
)
def date_iso8601(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.isoformat() if d else val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="date_us", input_types=["date"], auto_apply=False, priority=50, mode="series"
)
def date_us(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.strftime("%m/%d/%Y") if d else val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="date_eu", input_types=["date"], auto_apply=False, priority=50, mode="series"
)
def date_eu(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.strftime("%d/%m/%Y") if d else val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="date_parse", input_types=["date"], auto_apply=False, priority=55, mode="series"
)
def date_parse(series: pl.Series) -> pl.Series:
    """Auto-detect format and normalize to ISO 8601."""
    return date_iso8601(series)


@register_transform(
    name="age_from_dob", input_types=["date"], auto_apply=False, priority=40, mode="series"
)
def age_from_dob(series: pl.Series, reference_date: str | None = None) -> pl.Series:
    ref = (
        dateutil_parser.parse(reference_date).date()
        if reference_date
        else date.today()
    )

    def _age(val: str | None) -> int | None:
        if val is None:
            return None
        d = _parse_date(val)
        if d is None:
            return None
        age = ref.year - d.year - ((ref.month, ref.day) < (d.month, d.day))
        return age

    return series.map_elements(_age, return_dtype=pl.Int64)


@register_transform(
    name="datetime_iso8601",
    input_types=["date"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def datetime_iso8601(series: pl.Series) -> pl.Series:
    """Parse to ISO 8601 datetime (with time component)."""

    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        try:
            dt = dateutil_parser.parse(val)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, OverflowError):
            return val

    return series.map_elements(_fmt, return_dtype=pl.Utf8)


@register_transform(
    name="extract_year",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def extract_year(series: pl.Series) -> pl.Series:
    """Extract the year as an integer."""

    def _year(val: str | None) -> int | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.year if d else None

    return series.map_elements(_year, return_dtype=pl.Int64)


@register_transform(
    name="extract_month",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def extract_month(series: pl.Series) -> pl.Series:
    """Extract the month as an integer (1-12)."""

    def _month(val: str | None) -> int | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.month if d else None

    return series.map_elements(_month, return_dtype=pl.Int64)


@register_transform(
    name="date_shift",
    input_types=["date"],
    auto_apply=False,
    priority=30,
    mode="series",
)
def date_shift(series: pl.Series, days: int = 0) -> pl.Series:
    """Shift dates by a number of days (positive = forward, negative = backward)."""

    def _shift(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        if d is None:
            return val
        shifted = d + timedelta(days=days)
        return shifted.isoformat()

    return series.map_elements(_shift, return_dtype=pl.Utf8)


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@register_transform(
    name="extract_day",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def extract_day(series: pl.Series) -> pl.Series:
    """Extract the day of month as an integer (1-31)."""

    def _day(val: str | None) -> int | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.day if d else None

    return series.map_elements(_day, return_dtype=pl.Int64)


@register_transform(
    name="extract_quarter",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def extract_quarter(series: pl.Series) -> pl.Series:
    """Extract the quarter (1-4) from a date."""

    def _quarter(val: str | None) -> int | None:
        if val is None:
            return None
        d = _parse_date(val)
        if d is None:
            return None
        return (d.month - 1) // 3 + 1

    return series.map_elements(_quarter, return_dtype=pl.Int64)


@register_transform(
    name="extract_day_of_week",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def extract_day_of_week(series: pl.Series) -> pl.Series:
    """Extract the day of week name (Monday, Tuesday, etc.)."""

    def _dow(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        if d is None:
            return None
        return _DAY_NAMES[d.weekday()]

    return series.map_elements(_dow, return_dtype=pl.Utf8)


@register_transform(
    name="date_validate",
    input_types=["date", "string"],
    auto_apply=False,
    priority=60,
    mode="series",
)
def date_validate(series: pl.Series) -> pl.Series:
    """Validate if value is a parseable date. Returns True/False/None."""

    def _validate(val: str | None) -> bool | None:
        if val is None:
            return None
        if not val.strip():
            return False
        return _parse_date(val) is not None

    return series.map_elements(_validate, return_dtype=pl.Boolean)
