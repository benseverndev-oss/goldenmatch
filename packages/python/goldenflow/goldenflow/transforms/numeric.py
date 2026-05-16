from __future__ import annotations

import polars as pl

from goldenflow.transforms import register_transform


@register_transform(
    name="currency_strip", input_types=["string", "numeric"], auto_apply=False, priority=50, mode="expr"
)
def currency_strip(column: str) -> pl.Expr:
    """Strip currency symbols and thousand separators, return numeric.

    Native Polars: cast to Utf8, regex-strip non-numeric chars, cast to
    Float64 (strict=False yields null on failure, matching the old
    try/except). Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return (
        pl.col(column)
        .cast(pl.Utf8, strict=False)
        .str.replace_all(r"[^\d.\-]", "")
        .cast(pl.Float64, strict=False)
    )


@register_transform(
    name="percentage_normalize",
    input_types=["string", "numeric"],
    auto_apply=False,
    priority=50,
    mode="expr",
)
def percentage_normalize(column: str) -> pl.Expr:
    """Strip trailing %, parse to float, divide by 100.

    Native Polars: cast to Utf8, strip whitespace + trailing %, cast to
    Float64 (null on failure), then divide. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return (
        pl.col(column)
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .str.replace(r"%+$", "")
        .cast(pl.Float64, strict=False)
        / 100.0
    )


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
    mode="expr",
)
def to_integer(column: str) -> pl.Expr:
    """Parse string to integer, truncating any decimal part.

    Native Polars: cast via Float64 (truncation matches the old int(float(val))
    semantics) then Int64. strict=False yields null on parse failure, matching
    the old try/except. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return (
        pl.col(column)
        .cast(pl.Float64, strict=False)
        .cast(pl.Int64, strict=False)
    )


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
