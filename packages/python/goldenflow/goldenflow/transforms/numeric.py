from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform


@register_transform(
    name="currency_strip", input_types=["string", "numeric"], auto_apply=False, priority=50, mode="series"
)
def currency_strip(series: pl.Series) -> pl.Series:
    def _strip(val: str | None) -> float | None:
        if val is None:
            return None
        cleaned = re.sub(r"[^\d.\-]", "", str(val))
        try:
            return float(cleaned)
        except ValueError:
            return None

    return series.map_elements(_strip, return_dtype=pl.Float64)


@register_transform(
    name="percentage_normalize",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def percentage_normalize(series: pl.Series) -> pl.Series:
    def _norm(val: str | None) -> float | None:
        if val is None:
            return None
        v = str(val).strip().rstrip("%")
        try:
            return float(v) / 100.0
        except ValueError:
            return None

    return series.map_elements(_norm, return_dtype=pl.Float64)


@register_transform(
    name="round", input_types=["numeric"], auto_apply=False, priority=40, mode="series"
)
def round_values(series: pl.Series, n: int = 2) -> pl.Series:
    return series.round(n)


@register_transform(
    name="clamp", input_types=["numeric"], auto_apply=False, priority=40, mode="series"
)
def clamp(series: pl.Series, min_val: float = 0.0, max_val: float = 1.0) -> pl.Series:
    return series.clip(min_val, max_val)


@register_transform(
    name="to_integer",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def to_integer(series: pl.Series) -> pl.Series:
    """Parse string to integer, truncating any decimal part."""

    def _to_int(val: str | None) -> int | None:
        if val is None:
            return None
        try:
            return int(float(val))
        except (ValueError, OverflowError):
            return None

    return series.map_elements(_to_int, return_dtype=pl.Int64)


@register_transform(
    name="abs_value",
    input_types=["numeric"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def abs_value(series: pl.Series) -> pl.Series:
    """Return the absolute value."""
    return series.abs()


@register_transform(
    name="fill_zero",
    input_types=["numeric"],
    auto_apply=False,
    priority=35,
    mode="series",
)
def fill_zero(series: pl.Series) -> pl.Series:
    """Replace null values with 0."""
    return series.fill_null(0)


@register_transform(
    name="comma_decimal",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=48,
    mode="series",
)
def comma_decimal(series: pl.Series) -> pl.Series:
    """Convert European decimal format (1.234,56) to float (1234.56)."""

    def _convert(val: str | None) -> float | None:
        if val is None:
            return None
        v = str(val).strip()
        if "," not in v:
            # No comma present — parse as-is (US format or plain number)
            try:
                return float(v)
            except ValueError:
                return None
        # European format: dots are thousands, comma is decimal
        v = v.replace(".", "").replace(",", ".")
        try:
            return float(v)
        except ValueError:
            return None

    return series.map_elements(_convert, return_dtype=pl.Float64)


@register_transform(
    name="scientific_to_decimal",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def scientific_to_decimal(series: pl.Series) -> pl.Series:
    """Convert scientific notation (1.5e3) to decimal (1500.0)."""

    def _convert(val: str | None) -> float | None:
        if val is None:
            return None
        try:
            return float(str(val).strip())
        except ValueError:
            return None

    return series.map_elements(_convert, return_dtype=pl.Float64)
