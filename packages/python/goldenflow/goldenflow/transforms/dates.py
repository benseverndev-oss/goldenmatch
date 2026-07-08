from __future__ import annotations

from datetime import date, datetime, timedelta

from dateutil import parser as dateutil_parser

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform
from goldenflow.transforms._fastpath import _V, apply_with_residual

# DETERMINISTIC fill for date fields absent from the input (Phase 4d / "own the
# source of truth"). dateutil's default fills missing fields from `datetime.now()`,
# so `parse("March 1995")` returned a DIFFERENT day on every run -- a latent
# non-determinism bug, and inconsistent with GoldenFlow's own year-string fast path
# (which already fills month/day with 1: "1995" -> "1995-01-01"). We pin the fill to
# **month/day = 1, time = 00:00:00** so partial dates are deterministic AND agree
# with the fast path. Only partial-date inputs (which were non-deterministic anyway)
# change; fully-specified dates are unaffected. This is what makes the date family
# byte-reproducible and therefore portable to the native/columnar path.
_DEFAULT_DATE = datetime(2000, 1, 1, 0, 0, 0)


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return dateutil_parser.parse(val, default=_DEFAULT_DATE).date()
    except (ValueError, OverflowError):
        return None


# --------------------------------------------------------------------------- #
# Module-level per-element references (Phase 4d). Deterministic (see _parse_date /
# _DEFAULT_DATE), so they are the SAME fn the Polars `series` path applies AND the
# owned reference the native/columnar path runs -- byte-identical, Polars-free.
# Registered via `scalar=` so the in-memory columnar engine can run the date family
# over a list. The str-returning date transforms are wired this wave; the
# int/bool-returning ones (extract_year/…/date_validate) await dtype-aware egress.
# --------------------------------------------------------------------------- #
def _date_iso8601_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _parse_date(val)
    return d.isoformat() if d else val


def _date_us_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _parse_date(val)
    return d.strftime("%m/%d/%Y") if d else val


def _date_eu_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _parse_date(val)
    return d.strftime("%d/%m/%Y") if d else val


def _datetime_iso8601_py(val: str | None) -> str | None:
    if val is None:
        return None
    try:
        dt = dateutil_parser.parse(val, default=_DEFAULT_DATE)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OverflowError):
        return val


def _extract_day_of_week_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _parse_date(val)
    return _DAY_NAMES[d.weekday()] if d is not None else None


# int/bool-returning date references (Phase 4d dtype-egress). Deterministic via
# _parse_date; the SAME per-element fn the Polars `series` path applies -> the
# columnar engine egresses a real Int64/Boolean column, byte-identical.
def _extract_year_py(val: str | None) -> int | None:
    d = _parse_date(val)
    return d.year if d else None


def _extract_month_py(val: str | None) -> int | None:
    d = _parse_date(val)
    return d.month if d else None


def _extract_day_py(val: str | None) -> int | None:
    d = _parse_date(val)
    return d.day if d else None


def _extract_quarter_py(val: str | None) -> int | None:
    d = _parse_date(val)
    return (d.month - 1) // 3 + 1 if d else None


def _date_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    if not val.strip():
        return False
    return _parse_date(val) is not None


# Parameterized references (Phase 4d): a per-element fn bound to the op's params. The
# same logic the Polars `series` path runs, single-sourced so engine == columnar.
def _date_shift_scalar(val: str | None, days: int) -> str | None:
    if val is None:
        return None
    d = _parse_date(val)
    if d is None:
        return val
    return (d + timedelta(days=days)).isoformat()


def _date_shift_factory(params: list[str]):
    days = int(params[0]) if params else 0
    return lambda v: _date_shift_scalar(v, days)


def _age_scalar(val: str | None, ref: date) -> int | None:
    if val is None:
        return None
    d = _parse_date(val)
    if d is None:
        return None
    return ref.year - d.year - ((ref.month, ref.day) < (d.month, d.day))


def _age_from_dob_factory(params: list[str]):
    ref = (
        dateutil_parser.parse(params[0], default=_DEFAULT_DATE).date()
        if params and params[0]
        else date.today()
    )
    return lambda v: _age_scalar(v, ref)


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
    name="date_iso8601", input_types=["date"], auto_apply=True, priority=50, mode="series",
    scalar=_date_iso8601_py,
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

    # Vectorized fast path: parse the well-formed common formats in Rust and
    # strftime to ISO; only rows the fast path can't resolve hit the deterministic
    # per-row reference (_date_iso8601_py).
    fast_iso = _parsed_date_expr().dt.strftime("%Y-%m-%d")
    return apply_with_residual(series, fast_iso, _date_iso8601_py, pl.Utf8)


@register_transform(
    name="date_us", input_types=["date"], auto_apply=False, priority=50, mode="series",
    scalar=_date_us_py,
)
def date_us(series: pl.Series) -> pl.Series:
    fast = _parsed_date_expr().dt.strftime("%m/%d/%Y")
    return apply_with_residual(series, fast, _date_us_py, pl.Utf8)


@register_transform(
    name="date_eu", input_types=["date"], auto_apply=False, priority=50, mode="series",
    scalar=_date_eu_py,
)
def date_eu(series: pl.Series) -> pl.Series:
    fast = _parsed_date_expr().dt.strftime("%d/%m/%Y")
    return apply_with_residual(series, fast, _date_eu_py, pl.Utf8)


@register_transform(
    name="date_parse", input_types=["date"], auto_apply=False, priority=55, mode="series",
    scalar=_date_iso8601_py,
)
def date_parse(series: pl.Series) -> pl.Series:
    """Auto-detect format and normalize to ISO 8601."""
    return date_iso8601(series)


@register_transform(
    name="age_from_dob", input_types=["date"], auto_apply=False, priority=40, mode="series",
    scalar_factory=_age_from_dob_factory, scalar_dtype="int",
)
def age_from_dob(series: pl.Series, reference_date: str | None = None) -> pl.Series:
    ref = (
        dateutil_parser.parse(reference_date, default=_DEFAULT_DATE).date()
        if reference_date
        else date.today()
    )
    return series.map_elements(lambda v: _age_scalar(v, ref), return_dtype=pl.Int64)


@register_transform(
    name="datetime_iso8601",
    input_types=["date"],
    auto_apply=False,
    priority=50,
    mode="series",
    scalar=_datetime_iso8601_py,
)
def datetime_iso8601(series: pl.Series) -> pl.Series:
    """Parse to ISO 8601 datetime (with time component)."""
    return series.map_elements(_datetime_iso8601_py, return_dtype=pl.Utf8)


@register_transform(
    name="extract_year",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
    scalar=_extract_year_py,
    scalar_dtype="int",
)
def extract_year(series: pl.Series) -> pl.Series:
    """Extract the year as an integer."""
    return series.map_elements(_extract_year_py, return_dtype=pl.Int64)


@register_transform(
    name="extract_month",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
    scalar=_extract_month_py,
    scalar_dtype="int",
)
def extract_month(series: pl.Series) -> pl.Series:
    """Extract the month as an integer (1-12)."""
    return series.map_elements(_extract_month_py, return_dtype=pl.Int64)


@register_transform(
    name="date_shift",
    input_types=["date"],
    auto_apply=False,
    priority=30,
    mode="series",
    scalar_factory=_date_shift_factory,
    scalar_dtype="str",
)
def date_shift(series: pl.Series, days: int = 0) -> pl.Series:
    """Shift dates by a number of days (positive = forward, negative = backward)."""
    return series.map_elements(lambda v: _date_shift_scalar(v, days), return_dtype=pl.Utf8)


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@register_transform(
    name="extract_day",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
    scalar=_extract_day_py,
    scalar_dtype="int",
)
def extract_day(series: pl.Series) -> pl.Series:
    """Extract the day of month as an integer (1-31)."""
    return series.map_elements(_extract_day_py, return_dtype=pl.Int64)


@register_transform(
    name="extract_quarter",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
    scalar=_extract_quarter_py,
    scalar_dtype="int",
)
def extract_quarter(series: pl.Series) -> pl.Series:
    """Extract the quarter (1-4) from a date."""
    return series.map_elements(_extract_quarter_py, return_dtype=pl.Int64)


@register_transform(
    name="extract_day_of_week",
    input_types=["date"],
    auto_apply=False,
    priority=35,
    mode="series",
    scalar=_extract_day_of_week_py,
)
def extract_day_of_week(series: pl.Series) -> pl.Series:
    """Extract the day of week name (Monday, Tuesday, etc.)."""
    return series.map_elements(_extract_day_of_week_py, return_dtype=pl.Utf8)


@register_transform(
    name="date_validate",
    input_types=["date", "string"],
    auto_apply=False,
    priority=60,
    mode="series",
    scalar=_date_validate_py,
    scalar_dtype="bool",
)
def date_validate(series: pl.Series) -> pl.Series:
    """Validate if value is a parseable date. Returns True/False/None."""
    return series.map_elements(_date_validate_py, return_dtype=pl.Boolean)
