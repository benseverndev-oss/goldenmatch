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
