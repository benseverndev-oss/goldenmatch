from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform


def _extract_digits(val: str) -> str:
    """Extract only digit characters from a string."""
    return re.sub(r"\D", "", val)


@register_transform(
    name="ssn_format",
    input_types=["ssn", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ssn_format(series: pl.Series) -> pl.Series:
    """Normalize SSN to XXX-XX-XXXX format."""

    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        digits = _extract_digits(val)
        if len(digits) != 9:
            return val  # preserve invalid
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"

    return series.map_elements(_format, return_dtype=pl.Utf8)


@register_transform(
    name="ssn_mask",
    input_types=["ssn", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ssn_mask(series: pl.Series) -> pl.Series:
    """Mask SSN to ***-**-XXXX (last 4 visible)."""

    def _mask(val: str | None) -> str | None:
        if val is None:
            return None
        digits = _extract_digits(val)
        if len(digits) != 9:
            return val  # preserve invalid
        return f"***-**-{digits[5:]}"

    return series.map_elements(_mask, return_dtype=pl.Utf8)


@register_transform(
    name="ein_format",
    input_types=["ein", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ein_format(series: pl.Series) -> pl.Series:
    """Normalize EIN to XX-XXXXXXX format."""

    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        digits = _extract_digits(val)
        if len(digits) != 9:
            return val  # preserve invalid
        return f"{digits[:2]}-{digits[2:]}"

    return series.map_elements(_format, return_dtype=pl.Utf8)
