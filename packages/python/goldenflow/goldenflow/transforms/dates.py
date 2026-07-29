"""Date/datetime transforms — backed by an OWNED deterministic parser.

The date family used to lean on two things: ``python-dateutil`` (per-row fuzzy
parse) and a Polars ``str.to_date`` vectorized fast path. Both are gone. The
owned parser (``goldenflow-core::dates`` in Rust; the byte-identical pure-Python
fallback below) is now the single source of truth — no dateutil at runtime, no
Polars dependence. See ``docs/design/2026-07-06-goldenflow-owned-kernel-
boundary.md`` and the module docs in ``dates.rs`` for the full contract.

Supported formats (byte-identical to
``dateutil.parser.parse(v, default=datetime(2000,1,1)).date()`` on the covered
set): numeric ``%Y-%m-%d`` / ``%Y/%m/%d`` / ``%m/%d/%Y`` / ``%m-%d-%Y``
(ambiguous numeric is MONTH-FIRST, flipping to day-first when the month field
> 12); English month names (abbrev + full, case-insensitive); ``Month Year``
partials (day -> 1); year-only ``YYYYY`` -> Jan 1; and, for datetimes, a trailing
24-hour ``HH:MM`` / ``HH:MM:SS`` after a space or ``T``.

Deterministic rules (some INTENTIONALLY diverge from dateutil, documented):
- Missing month/day -> 1; missing time -> 00:00:00 (the ``_DEFAULT_DATE`` policy).
- 2-digit years use a deterministic POSIX pivot (00-68 -> 2000-2068,
  69-99 -> 1969-1999) instead of dateutil's non-deterministic ``now()``-relative
  mapping.
- A datetime-bearing string on a date-only transform TRUNCATES to the date
  (``"2024-01-20 14:05:00" -> 2024-01-20``), matching dateutil -- datetime values
  in a date column are common and standardizing them is the point.
- Anything outside the supported set -> ``None`` (the value passes through
  unchanged). dateutil fuzz-parses the exotic tail (bare month names, AM/PM
  times, weekday/ordinal words); we don't.
- Real calendar validation (month 1-12, day-in-month, leap-aware).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform

# DETERMINISTIC fill policy anchor (kept as documentation + a stable import for
# tests): date fields absent from the input fill to month/day = 1, time =
# 00:00:00. dateutil's default filled missing fields from ``datetime.now()``, so
# ``parse("March 1995")`` returned a DIFFERENT day on every run. Pinning the fill
# is what makes the date family byte-reproducible and portable to the owned
# kernel / columnar / native path.
_DEFAULT_DATE = datetime(2000, 1, 1, 0, 0, 0)


# --------------------------------------------------------------------------- #
# Owned pure-Python parser — a byte-identical fallback for the goldenflow-core
# Rust kernel (``dates.rs``). Mirrors that contract EXACTLY. Native/wasm arrow
# wiring is a follow-up; today this pure path is the runtime, dateutil-free and
# Polars-free. Keep the two in lockstep (the cargo tests + the Python parity
# test are the guard).
# --------------------------------------------------------------------------- #
_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _ascii_digits(s: str) -> bool:
    return len(s) > 0 and all("0" <= c <= "9" for c in s)


def _month_from_name(tok: str) -> int | None:
    return _MONTHS.get(tok.rstrip(".").lower())


def _parse_day_token(tok: str) -> int | None:
    if not (0 < len(tok) <= 2) or not _ascii_digits(tok):
        return None
    return int(tok)


def _parse_year_token(tok: str) -> int | None:
    if not (0 < len(tok) <= 4) or not _ascii_digits(tok):
        return None
    v = int(tok)
    if len(tok) == 2:  # POSIX pivot
        return 2000 + v if v <= 68 else 1900 + v
    return v


def _is_leap(y: int) -> bool:
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


_DIM = {1: 31, 3: 31, 5: 31, 7: 31, 8: 31, 10: 31, 12: 31, 4: 30, 6: 30, 9: 30, 11: 30}


def _days_in_month(y: int, m: int) -> int:
    if m == 2:
        return 29 if _is_leap(y) else 28
    return _DIM.get(m, 0)


def _valid_ymd(y: int, m: int, d: int) -> bool:
    return 1 <= m <= 12 and 1 <= d <= _days_in_month(y, m)


def _finish(y: int, m: int, d: int) -> tuple[int, int, int] | None:
    return (y, m, d) if _valid_ymd(y, m, d) else None


def _interpret_numeric(g1: str, g2: str, g3: str) -> tuple[int, int, int] | None:
    if len(g1) == 4:
        # ISO: a 4-digit first group anchors year-first (Y, M, D).
        y, m, d = int(g1), int(g2), int(g3)
    else:
        # US month-first: year is the last group; flip to day-first when the
        # month field > 12 and the day field <= 12 (dateutil disambiguation).
        y_opt = _parse_year_token(g3)
        if y_opt is None:
            return None
        y = y_opt
        a, b = int(g1), int(g2)
        (m, d) = (b, a) if (a > 12 and b <= 12) else (a, b)
    return _finish(y, m, d)


def _parse_numeric(s: str) -> tuple[int, int, int] | None:
    for sep in ("-", "/"):
        parts = s.split(sep)
        if len(parts) == 3 and all(0 < len(p) <= 4 and _ascii_digits(p) for p in parts):
            r = _interpret_numeric(parts[0], parts[1], parts[2])
            if r is not None:
                return r
    return None


def _parse_month_name(s: str) -> tuple[int, int, int] | None:
    toks = [t for t in (tok.rstrip(",") for tok in s.split()) if t]
    n = len(toks)
    if n == 3:
        m = _month_from_name(toks[0])
        if m is not None:  # "Mon Day Year"
            d = _parse_day_token(toks[1])
            y = _parse_year_token(toks[2])
            return _finish(y, m, d) if (d is not None and y is not None) else None
        m = _month_from_name(toks[1])
        if m is not None:  # "Day Mon Year"
            d = _parse_day_token(toks[0])
            y = _parse_year_token(toks[2])
            return _finish(y, m, d) if (d is not None and y is not None) else None
        return None
    if n == 2:
        m = _month_from_name(toks[0])
        if m is not None:  # "Mon Year" -> day 1
            y = _parse_year_token(toks[1])
            return _finish(y, m, 1) if y is not None else None
        return None
    return None


def _parse_date_ymd(s: str) -> tuple[int, int, int] | None:
    s = s.strip()
    if not s:
        return None
    if len(s) == 4 and _ascii_digits(s):  # year-only
        return (int(s), 1, 1)
    r = _parse_numeric(s)
    if r is not None:
        return r
    return _parse_month_name(s)


def _looks_like_time(tok: str) -> bool:
    parts = tok.split(":")
    return len(parts) in (2, 3) and all(
        0 < len(p) <= 2 and _ascii_digits(p) for p in parts
    )


def _parse_time(t: str) -> tuple[int, int, int] | None:
    parts = t.split(":")
    if len(parts) not in (2, 3):
        return None
    if any(not (0 < len(p) <= 2) or not _ascii_digits(p) for p in parts):
        return None
    hh, mm = int(parts[0]), int(parts[1])
    ss = int(parts[2]) if len(parts) == 3 else 0
    return (hh, mm, ss) if hh < 24 and mm < 60 and ss < 60 else None


def _peel_time(s: str) -> tuple[str, str | None]:
    n = len(s)
    # ISO 'T' flanked by ASCII digits separates date and time.
    for i in range(n):
        if s[i] == "T" and 0 < i < n - 1 and s[i - 1] in "0123456789" and s[i + 1] in "0123456789":
            return (s[:i], s[i + 1:])
    # Trailing whitespace-delimited time token.
    last = -1
    for i, ch in enumerate(s):
        if ch.isspace():
            last = i
    if last >= 0:
        head, tail = s[:last], s[last + 1:]
        if _looks_like_time(tail):
            return (head.rstrip(), tail)
    return (s, None)


def _parse_datetime_ymdhms(s: str) -> tuple[int, int, int, int, int, int] | None:
    s = s.strip()
    if not s:
        return None
    date_part, time_part = _peel_time(s)
    dr = _parse_date_ymd(date_part)
    if dr is None:
        return None
    if time_part is None:
        hms: tuple[int, int, int] | None = (0, 0, 0)
    else:
        hms = _parse_time(time_part)
        if hms is None:
            return None
    return (*dr, *hms)


def _parse_date(val: str | None) -> date | None:
    """Owned deterministic parse to a :class:`datetime.date` (``None`` on the
    exotic tail / junk). The single primitive every date transform derives from.

    Accepts a datetime-bearing string and TRUNCATES to the date (e.g.
    ``"2024-01-20 14:05:00" -> 2024-01-20``), matching the prior dateutil
    behavior -- datetime values in a date column are a common real shape, and
    standardizing them is the whole point of these transforms. A trailing token
    that is NOT a valid time still fails (whole-string parse, like dateutil)."""
    if not val:
        return None
    r = _parse_datetime_ymdhms(val)  # date + optional VALID trailing time
    if r is None:
        return None
    try:
        return date(r[0], r[1], r[2])
    except ValueError:  # year out of the datetime range (e.g. 0000)
        return None


# --------------------------------------------------------------------------- #
# Per-element references (the owned scalars). Deterministic, so they are BOTH the
# fn the Polars ``series`` path applies (via ``map_elements``) AND the owned
# reference the Polars-free columnar path runs (via ``scalar=``) — byte-identical
# by construction, no fast-path-vs-scalar divergence.
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
    r = _parse_datetime_ymdhms(val)
    if r is None:
        return val
    y, m, d, hh, mm, ss = r
    try:
        return datetime(y, m, d, hh, mm, ss).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return val


def _extract_day_of_week_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _parse_date(val)
    return _DAY_NAMES[d.weekday()] if d is not None else None


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
    ref = (_parse_date(params[0]) or date.today()) if params and params[0] else date.today()
    return lambda v: _age_scalar(v, ref)


# Utf8 columns of pure 4-digit years take a cheap vectorized shortcut (no
# per-row parse); it produces the SAME "YYYY-01-01" the owned scalar does.
_YEAR_ONLY_RE = r"^\s*\d{4}\s*$"


@register_transform(
    name="date_iso8601", input_types=["date"], auto_apply=True, priority=50, mode="series",
    scalar=_date_iso8601_py,
)
def date_iso8601(series: pl.Series) -> pl.Series:
    # Fast path A: numeric column (the inferred "date" type matched a column
    # that's actually integer years -- e.g. birth_year=1995). Format as
    # "YYYY-01-01" via Polars vectorized string concat (no per-row parse).
    if series.dtype.is_numeric():
        return series.cast(pl.Int64, strict=False).cast(pl.Utf8) + "-01-01"

    # Fast path B: Utf8 column whose values are ALL 4-digit year strings
    # (e.g. "1995"). Vectorized strip + concat; identical to the owned scalar.
    if series.dtype == pl.Utf8:
        non_null = series.drop_nulls()
        if non_null.len() > 0 and bool(non_null.str.contains(_YEAR_ONLY_RE).all()):
            return series.str.strip_chars() + "-01-01"

    # Owned parse over the column: the deterministic scalar per element. No
    # Polars fast path, no dateutil.
    return series.map_elements(_date_iso8601_py, return_dtype=pl.Utf8)


@register_transform(
    name="date_us", input_types=["date"], auto_apply=False, priority=50, mode="series",
    scalar=_date_us_py,
)
def date_us(series: pl.Series) -> pl.Series:
    return series.map_elements(_date_us_py, return_dtype=pl.Utf8)


@register_transform(
    name="date_eu", input_types=["date"], auto_apply=False, priority=50, mode="series",
    scalar=_date_eu_py,
)
def date_eu(series: pl.Series) -> pl.Series:
    return series.map_elements(_date_eu_py, return_dtype=pl.Utf8)


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
    ref = (_parse_date(reference_date) or date.today()) if reference_date else date.today()
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
