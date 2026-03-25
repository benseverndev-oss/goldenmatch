from __future__ import annotations

from datetime import date, datetime

from dateutil import parser as dateutil_parser
import polars as pl

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
