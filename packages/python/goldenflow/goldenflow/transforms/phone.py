from __future__ import annotations

import phonenumbers
import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._fastpath import _V, apply_with_residual
from goldenflow.transforms._native import (
    phone_country_code_native,
    phone_digits_native,
    phone_e164_native,
    phone_national_native,
)

_DEFAULT_REGION = "US"

# Vectorized E.164 fast path for the dominant NANP shape. A digit-only string
# of 10 chars whose first digit is 2-9 (valid NANP area code) maps to +1<10>;
# 11 chars starting "1" + a 2-9 digit maps to +<11>. Both reproduce exactly
# what phonenumbers.format_number(parse(val, "US"), E164) returns, so a row this
# path resolves is identical to the per-row path (parity asserted over a random
# corpus in tests/transforms/test_fastpath_parity.py).
#
# Three guards keep it parity-safe:
#   - the 2-9 area-code guard avoids the leading-"1" ambiguity (phonenumbers
#     reads a leading 1 on a 10-digit string as the country code);
#   - rows with letters defer to phonenumbers' alpha handling;
#   - rows with a "+" defer too: an explicit "+CC" is international, and a
#     foreign number can strip to exactly 10 digits starting 2-9 (e.g. German
#     "+4930123456" -> "4930123456") which would otherwise be mis-NANP'd.
# Everything excluded falls through to the per-row / native path unchanged.
_NANP_10 = r"^[2-9]\d{9}$"
_NANP_11 = r"^1[2-9]\d{9}$"
_HAS_ALPHA = r"[A-Za-z]"


def _e164_fast_expr() -> pl.Expr:
    digits = pl.col(_V).str.replace_all(r"\D", "")
    eligible = ~pl.col(_V).str.contains(_HAS_ALPHA) & ~pl.col(_V).str.contains(r"\+")
    return (
        pl.when(eligible & digits.str.contains(_NANP_10))
        .then(pl.lit("+1") + digits)
        .when(eligible & digits.str.contains(_NANP_11))
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

    # Native NANP national format, gated to the canonical "(NXX) NXX-XXXX" shape
    # (phone_national_native nulls the ambiguous leading-1 outputs so tier-3
    # Python settles them). No Polars fast expr -- a no-op lit + the Python
    # residual, same shape as phone_country_code.
    return apply_with_residual(
        series,
        pl.lit(None, dtype=pl.Utf8),
        _format,
        pl.Utf8,
        native_fn=phone_national_native(),
    )


@register_transform(
    name="phone_digits", input_types=["phone"], auto_apply=False, priority=50, mode="series"
)
def phone_digits(series: pl.Series) -> pl.Series:
    # Native-first over goldenflow-core (ASCII digit-strip); else the pure-Polars
    # regex fallback (strips every non-digit, stays in Rust ~5x). On ASCII phone
    # data -- the pinned parity contract -- both equal _phone_digits_py.
    native = phone_digits_native()
    if native is not None:
        return native(series)
    # cast(Utf8) is a no-op on a real string column but turns an all-null
    # (Null-dtype) series into Utf8 so `.str` is valid; nulls pass through.
    return series.cast(pl.Utf8).str.replace_all(r"\D", "")


def _phone_digits_py(val: str | None) -> str | None:
    """Byte-exact reference for the corpus oracle: keep only ASCII digits
    (matches the goldenflow-core `phone_digits` kernel; a Unicode digit is
    dropped here, unlike Python `str.isdigit`)."""
    if val is None:
        return None
    return "".join(c for c in val if c in "0123456789")


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
