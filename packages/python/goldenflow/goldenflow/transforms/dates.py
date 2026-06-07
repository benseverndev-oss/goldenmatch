from __future__ import annotations

from datetime import date, timedelta

import polars as pl
from dateutil import parser as dateutil_parser

from goldenflow.transforms import register_transform
from goldenflow.transforms._fastpath import apply_with_residual, _V


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return dateutil_parser.parse(val).date()
    except (ValueError, OverflowError):
        return None


_YEAR_ONLY_RE = r"^\s*\d{4}\s*$"

# Formats the vectorized fast path resolves entirely in Polars/Rust. Each is
# either unambiguous (4-digit year anchors the field order) or matches
# dateutil's default month-first interpretation, so a row this path resolves
# is byte-identical to what `dateutil.parse(...).date()` would have produced —
# the parity contract `apply_with_residual` relies on (asserted over a random
# corpus in tests/transforms/test_dates.py). Anything not covered here (2-digit
# years, times, exotic spellings) falls through to the per-row dateutil path.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d %b %Y",
    "%d %B %Y",
)


def _parsed_date_expr() -> pl.Expr:
    """Vectorized parse of the :data:`_V` column to a `pl.Date` (null where no
    fast-path format matched).

    Guarded on the presence of a 4-digit run: chrono's ``%Y`` greedily accepts
    2-digit years (``"02/02/93"`` -> year 0093), but dateutil maps a 2-digit
    year to 1993. Requiring a 4-digit year keeps the fast path off any 2-digit
    -year input so those defer to the per-row dateutil reference — preserving
    parity (see tests/transforms/test_fastpath_parity.py)."""
    has_four_digit_year = pl.col(_V).str.contains(r"\d{4}")
    parsed = pl.coalesce(
        [pl.col(_V).str.to_date(fmt, strict=False) for fmt in _DATE_FORMATS]
    )
    return pl.when(has_four_digit_year).then(parsed).otherwise(None)


@register_transform(
    name="date_iso8601", input_types=["date"], auto_apply=True, priority=50, mode="series"
)
def date_iso8601(series: pl.Series) -> pl.Series:
    # Fast path A: numeric column (the inferred "date" type matched a column
    # that's actually integer years -- e.g. birth_year=1995). Skip dateutil
    # entirely; format as "YYYY-01-01" via Polars vectorized string concat.
    # At 10M rows this drops the transform from ~150s (per-row dateutil) to
    # <1s (Rust string concat under the hood).
    if series.dtype.is_numeric():
        return series.cast(pl.Int64, strict=False).cast(pl.Utf8) + "-01-01"

    # Fast path B: Utf8 column whose values are ALL 4-digit year strings
    # (e.g. "1995"). This is the common shape when a year column was read
    # from CSV as text. v15 measured 161s at 10M on this exact case -- the
    # numeric fast path didn't trigger because the QIS fixture generates
    # year_canon = rng.integers(1940, 2005).astype(str).tolist(), so Polars
    # sees Utf8 values like "1995", not Int64. The dateutil slow path parses
    # each "1995" -> date(1995, 1, 1) -> "1995-01-01" at ~16us per row.
    # Vectorized: detect via regex (Rust-backed pl.str.contains), then strip
    # + concat. ~150x speedup; falls through to dateutil for any column with
    # non-year content.
    if series.dtype == pl.Utf8:
        non_null = series.drop_nulls()
        if non_null.len() > 0 and bool(non_null.str.contains(_YEAR_ONLY_RE).all()):
            return series.str.strip_chars() + "-01-01"

    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.isoformat() if d else val

    # Vectorized fast path: parse the well-formed common formats in Rust and
    # strftime to ISO; only rows the fast path can't resolve hit dateutil.
    fast_iso = _parsed_date_expr().dt.strftime("%Y-%m-%d")
    return apply_with_residual(series, fast_iso, _fmt, pl.Utf8)


@register_transform(
    name="date_us", input_types=["date"], auto_apply=False, priority=50, mode="series"
)
def date_us(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.strftime("%m/%d/%Y") if d else val

    fast = _parsed_date_expr().dt.strftime("%m/%d/%Y")
    return apply_with_residual(series, fast, _fmt, pl.Utf8)


@register_transform(
    name="date_eu", input_types=["date"], auto_apply=False, priority=50, mode="series"
)
def date_eu(series: pl.Series) -> pl.Series:
    def _fmt(val: str | None) -> str | None:
        if val is None:
            return None
        d = _parse_date(val)
        return d.strftime("%d/%m/%Y") if d else val

    fast = _parsed_date_expr().dt.strftime("%d/%m/%Y")
    return apply_with_residual(series, fast, _fmt, pl.Utf8)


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
