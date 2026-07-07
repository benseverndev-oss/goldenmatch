from __future__ import annotations

import math

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    abs_value_native,
    clamp_native,
    comma_decimal_native,
    currency_strip_native,
    fill_zero_native,
    fraction_to_decimal_native,
    ordinal_to_int_native,
    percentage_normalize_native,
    roman_to_int_native,
    round_native,
    scientific_to_decimal_native,
    to_integer_native,
)

# Pure-Python reference for goldenflow-core's ``numeric`` kernel. MUST
# reproduce the Rust kernel VALUE-for-VALUE (asserted by
# tests/transforms/test_identifiers_parity.py / test_numeric_kernels.py over
# tests/parity/identifiers_corpus.jsonl). This family outputs floats/ints, so
# parity is by VALUE, not string repr.


def _currency_strip_py(val: str | None) -> float | None:
    if val is None:
        return None
    filtered = "".join(c for c in str(val) if c.isdigit() or c in ".-")
    try:
        return float(filtered)
    except ValueError:
        return None


def _percentage_normalize_py(val: str | None) -> float | None:
    if val is None:
        return None
    v = str(val).strip()
    v = v.rstrip("%")
    v = v.strip()
    try:
        return float(v) / 100.0
    except ValueError:
        return None


def _to_integer_py(val: str | None) -> int | None:
    if val is None:
        return None
    try:
        return int(float(str(val).strip()))
    except ValueError:
        return None


def _comma_decimal_py(val: str | None) -> float | None:
    if val is None:
        return None
    v = str(val).strip()
    if "," not in v:
        try:
            return float(v)
        except ValueError:
            return None
    v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _scientific_to_decimal_py(val: str | None) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def _round_f64_py(x: float, n: int) -> float:
    """Round-half-away-from-zero at the n-th decimal, via multiply/round/
    divide -- the SAME formula as goldenflow-core's ``round_f64`` kernel.
    Deliberately NOT Python's builtin ``round()`` (round-half-to-even)."""
    factor = 10.0**n
    scaled = x * factor
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return rounded / factor


def _clamp_f64_py(x: float, min_val: float, max_val: float) -> float:
    if x < min_val:
        return min_val
    if x > max_val:
        return max_val
    return x


def _abs_f64_py(x: float) -> float:
    return abs(x)


def _currency_strip_series(series: pl.Series) -> pl.Series:
    native = currency_strip_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _currency_strip_py, return_dtype=pl.Float64
    )


def _percentage_normalize_series(series: pl.Series) -> pl.Series:
    native = percentage_normalize_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _percentage_normalize_py, return_dtype=pl.Float64
    )


def _to_integer_series(series: pl.Series) -> pl.Series:
    native = to_integer_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _to_integer_py, return_dtype=pl.Int64
    )


# --- W5 numeric breadth: roman / fraction / ordinal parsers -------------------

_ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_TABLE = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
    (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
    (5, "V"), (4, "IV"), (1, "I"),
]


def _int_to_roman(n: int) -> str:
    out = []
    for v, sym in _ROMAN_TABLE:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def _roman_to_int_py(val: str | None) -> int | None:
    if val is None:
        return None
    t = str(val).strip().upper()
    if not t:
        return None
    chars = list(t)
    total = 0
    for i, c in enumerate(chars):
        cur = _ROMAN_VAL.get(c)
        if cur is None:
            return None
        nxt = _ROMAN_VAL.get(chars[i + 1], 0) if i + 1 < len(chars) else 0
        total += -cur if cur < nxt else cur
    if not 1 <= total <= 3999:
        return None
    return total if _int_to_roman(total) == t else None


def _ordinal_suffix(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _ordinal_to_int_py(val: str | None) -> int | None:
    if val is None:
        return None
    t = str(val).strip().lower()
    digits = ""
    for c in t:
        if c.isascii() and c.isdigit():
            digits += c
        else:
            break
    if not digits:
        return None
    n = int(digits)
    return n if t[len(digits):] == _ordinal_suffix(n) else None


def _parse_fraction(s: str) -> float | None:
    if "/" not in s:
        return None
    num_s, den_s = s.split("/", 1)
    try:
        num = float(num_s.strip())
        den = float(den_s.strip())
    except ValueError:
        return None
    if den == 0.0:
        return None
    return num / den


def _fraction_to_decimal_py(val: str | None) -> float | None:
    if val is None:
        return None
    t = str(val).strip()
    if not t:
        return None
    parts = t.split(None, 1)
    if len(parts) == 2:
        frac_s = parts[1].strip()
        if "/" in frac_s:
            try:
                whole = int(parts[0].strip())
            except ValueError:
                return None
            frac = _parse_fraction(frac_s)
            if frac is None:
                return None
            return (whole - frac) if whole < 0 else (whole + frac)
        return None
    if "/" in t:
        return _parse_fraction(t)
    try:
        return float(t)
    except ValueError:
        return None


def _roman_to_int_series(series: pl.Series) -> pl.Series:
    native = roman_to_int_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _roman_to_int_py, return_dtype=pl.Int64
    )


def _ordinal_to_int_series(series: pl.Series) -> pl.Series:
    native = ordinal_to_int_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _ordinal_to_int_py, return_dtype=pl.Int64
    )


def _fraction_to_decimal_series(series: pl.Series) -> pl.Series:
    native = fraction_to_decimal_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _fraction_to_decimal_py, return_dtype=pl.Float64
    )


@register_transform(
    name="currency_strip", input_types=["string", "numeric"], auto_apply=False, priority=50, mode="expr"
)
def currency_strip(column: str) -> pl.Expr:
    """Strip currency symbols and thousand separators, return numeric.

    Native-first (goldenflow-core's ``numeric::currency_strip`` kernel),
    dispatched via ``map_batches`` so the transform keeps its original
    ``expr``-mode signature; the pure-Python fallback is the value-exact
    reference this kernel replicates.
    """
    return pl.col(column).map_batches(_currency_strip_series, return_dtype=pl.Float64)


@register_transform(
    name="percentage_normalize",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=50,
    mode="expr",
)
def percentage_normalize(column: str) -> pl.Expr:
    """Strip trailing %, parse to float, divide by 100.

    Native-first (goldenflow-core's ``numeric::percentage_normalize``
    kernel), dispatched via ``map_batches``; the pure-Python fallback is the
    value-exact reference this kernel replicates.
    """
    return pl.col(column).map_batches(_percentage_normalize_series, return_dtype=pl.Float64)


@register_transform(
    name="round", input_types=["numeric"], auto_apply=False, priority=40, mode="series"
)
def round_values(series: pl.Series, n: int = 2) -> pl.Series:
    """Round to n decimal places (round-half-away-from-zero, see the
    goldenflow-core ``round_f64`` kernel doc for why this deliberately isn't
    the language builtin ``round()``).

    Native-first; the pure-Python fallback below is the value-exact
    reference this kernel replicates.
    """
    native = round_native(n)
    if native is not None:
        return native(series)
    return series.cast(pl.Float64, strict=False).map_elements(
        lambda x: None if x is None else _round_f64_py(x, n), return_dtype=pl.Float64
    )


@register_transform(
    name="clamp", input_types=["numeric"], auto_apply=False, priority=40, mode="series"
)
def clamp(series: pl.Series, min_val: float = 0.0, max_val: float = 1.0) -> pl.Series:
    """Clip values into [min_val, max_val].

    Native-first (goldenflow-core's ``numeric::clamp_f64`` kernel); the
    pure-Python fallback below is the value-exact reference this kernel
    replicates.
    """
    native = clamp_native(min_val, max_val)
    if native is not None:
        return native(series)
    return series.cast(pl.Float64, strict=False).map_elements(
        lambda x: None if x is None else _clamp_f64_py(x, min_val, max_val),
        return_dtype=pl.Float64,
    )


@register_transform(
    name="roman_to_int", input_types=["string"], auto_apply=False, priority=40, mode="expr"
)
def roman_to_int(column: str) -> pl.Expr:
    """Parse a Roman numeral to its integer (canonical forms only, 1..=3999).
    Native-first over goldenflow-core."""
    return pl.col(column).map_batches(_roman_to_int_series, return_dtype=pl.Int64)


@register_transform(
    name="ordinal_to_int", input_types=["string"], auto_apply=False, priority=40, mode="expr"
)
def ordinal_to_int(column: str) -> pl.Expr:
    """Parse an English ordinal (1st/2nd/3rd) to its integer. Native-first."""
    return pl.col(column).map_batches(_ordinal_to_int_series, return_dtype=pl.Int64)


@register_transform(
    name="fraction_to_decimal", input_types=["string", "numeric"], auto_apply=False, priority=40, mode="expr"
)
def fraction_to_decimal(column: str) -> pl.Expr:
    """Parse a fraction or mixed number (1/2, 3 3/4) to a float. Native-first."""
    return pl.col(column).map_batches(_fraction_to_decimal_series, return_dtype=pl.Float64)


@register_transform(
    name="to_integer",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=45,
    mode="expr",
)
def to_integer(column: str) -> pl.Expr:
    """Parse string to integer, truncating any decimal part.

    Native-first (goldenflow-core's ``numeric::to_integer`` kernel),
    dispatched via ``map_batches``; the pure-Python fallback is the
    value-exact reference this kernel replicates.
    """
    return pl.col(column).map_batches(_to_integer_series, return_dtype=pl.Int64)


@register_transform(
    name="abs_value",
    input_types=["numeric"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def abs_value(series: pl.Series) -> pl.Series:
    """Return the absolute value.

    Native-first (goldenflow-core's ``numeric::abs_f64`` kernel); the
    pure-Python fallback below is the value-exact reference this kernel
    replicates.
    """
    native = abs_value_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Float64, strict=False).map_elements(
        lambda x: None if x is None else _abs_f64_py(x), return_dtype=pl.Float64
    )


@register_transform(
    name="fill_zero",
    input_types=["numeric"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def fill_zero(series: pl.Series) -> pl.Series:
    """Replace null values with 0.

    Native-first (goldenflow-core's ``numeric::fill_zero`` kernel); the
    pure-Python fallback below is the value-exact reference this kernel
    replicates.
    """
    native = fill_zero_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Float64, strict=False).fill_null(0.0)


@register_transform(
    name="comma_decimal",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=48,
    mode="series",
)
def comma_decimal(series: pl.Series) -> pl.Series:
    """Convert European decimal format (1.234,56) to float (1234.56).

    Native-first (goldenflow-core's ``numeric::comma_decimal`` kernel); the
    pure-Python fallback below is the value-exact reference this kernel
    replicates.
    """
    native = comma_decimal_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _comma_decimal_py, return_dtype=pl.Float64
    )


@register_transform(
    name="scientific_to_decimal",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def scientific_to_decimal(series: pl.Series) -> pl.Series:
    """Convert scientific notation (1.5e3) to decimal (1500.0).

    Native-first (goldenflow-core's ``numeric::scientific_to_decimal``
    kernel); the pure-Python fallback below is the value-exact reference
    this kernel replicates.
    """
    native = scientific_to_decimal_native()
    if native is not None:
        return native(series)
    return series.cast(pl.Utf8, strict=False).map_elements(
        _scientific_to_decimal_py, return_dtype=pl.Float64
    )
