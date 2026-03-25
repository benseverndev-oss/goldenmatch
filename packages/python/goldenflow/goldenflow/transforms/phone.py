from __future__ import annotations

import phonenumbers
import polars as pl

from goldenflow.transforms import register_transform

_DEFAULT_REGION = "US"


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

    return series.map_elements(_format, return_dtype=pl.Utf8)


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

    return series.map_elements(_format, return_dtype=pl.Utf8)


@register_transform(
    name="phone_digits", input_types=["phone"], auto_apply=False, priority=50, mode="series"
)
def phone_digits(series: pl.Series) -> pl.Series:
    def _to_digits(val: str | None) -> str | None:
        if val is None:
            return None
        return "".join(c for c in val if c.isdigit())

    return series.map_elements(_to_digits, return_dtype=pl.Utf8)


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
