from __future__ import annotations

import phonenumbers
import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._fastpath import _V, apply_with_residual
from goldenflow.transforms._native import (
    phone_country_code_native,
    phone_e164_native,
    phone_national_native,
)

_DEFAULT_REGION = "US"

# Vectorized E.164 fast path for the dominant NANP shape. A digit-only string
# of 10 chars whose first digit is 2-9 (valid NANP area code) maps to +1<10>;
# 11 chars starting "1" + a 2-9 digit maps to +<11>. Both reproduce exactly
# what phonenumbers.format_number(parse(val, "US"), E164) returns, so a row this
# path resolves is identical to the per-row path (parity asserted over a random
# corpus in tests/transforms/test_phone.py). The 2-9 guard avoids the
# leading-"1" ambiguity (phonenumbers reads a leading 1 on a 10-digit string as
# the country code); rows containing letters defer to phonenumbers' alpha
# handling. Everything else falls through to the per-row path unchanged.
_NANP_10 = r"^[2-9]\d{9}$"
_NANP_11 = r"^1[2-9]\d{9}$"
_HAS_ALPHA = r"[A-Za-z]"


def _e164_fast_expr() -> pl.Expr:
    digits = pl.col(_V).str.replace_all(r"\D", "")
    no_alpha = ~pl.col(_V).str.contains(_HAS_ALPHA)
    return (
        pl.when(no_alpha & digits.str.contains(_NANP_10))
        .then(pl.lit("+1") + digits)
        .when(no_alpha & digits.str.contains(_NANP_11))
        .then(pl.lit("+") + digits)
        .otherwise(None)
    )


def _parse_phone(val: str | None) -> phonenumbers.PhoneNumber | None:
    if not val:
        return None
    try:
        return phonenumbers.parse(val, _DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return None


@register_transform(
    name="phone_e164", input_types=["phone"], auto_apply=True, priority=50, mode="series"
)
def phone_e164(series: pl.Series) -> pl.Series:
    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return val  # preserve original on failure
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    return apply_with_residual(
        series, _e164_fast_expr(), _format, pl.Utf8, native_fn=phone_e164_native()
    )


@register_transform(
    name="phone_national", input_types=["phone"], auto_apply=False, priority=50, mode="series"
)
def phone_national(series: pl.Series) -> pl.Series:
    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return val
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)

    native = phone_national_native()
    if native is None:
        return series.map_elements(_format, return_dtype=pl.Utf8)
    return apply_with_residual(
        series, pl.lit(None, dtype=pl.Utf8), _format, pl.Utf8, native_fn=native
    )


@register_transform(
    name="phone_digits", input_types=["phone"], auto_apply=False, priority=50, mode="series"
)
def phone_digits(series: pl.Series) -> pl.Series:
    # Pure-Polars regex: strip every non-digit. Equivalent to the per-row
    # "".join(c for c in val if c.isdigit()) but stays in Rust (~5x). Note:
    # str.isdigit() also accepts some Unicode digit code points; the column
    # transform targets ASCII phone data, and the parity test pins ASCII rows.
    return series.str.replace_all(r"\D", "")


@register_transform(
    name="phone_validate", input_types=["phone"], auto_apply=False, priority=60, mode="series"
)
def phone_validate(series: pl.Series) -> pl.Series:
    def _validate(val: str | None) -> bool | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return False
        return phonenumbers.is_possible_number(parsed)

    return series.map_elements(_validate, return_dtype=pl.Boolean)


@register_transform(
    name="phone_country_code",
    input_types=["phone"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def phone_country_code(series: pl.Series) -> pl.Series:
    """Extract the country calling code as an integer."""

    def _code(val: str | None) -> int | None:
        if val is None:
            return None
        parsed = _parse_phone(val)
        if parsed is None:
            return None
        return parsed.country_code

    native = phone_country_code_native()
    if native is None:
        return series.map_elements(_code, return_dtype=pl.Int64)
    return apply_with_residual(
        series, pl.lit(None, dtype=pl.Int64), _code, pl.Int64, native_fn=native
    )
