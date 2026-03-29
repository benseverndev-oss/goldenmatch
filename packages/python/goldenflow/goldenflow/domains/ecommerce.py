from __future__ import annotations
import re
import polars as pl
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

@register_transform(name="sku_normalize", input_types=["string"], auto_apply=False, priority=50, mode="series")
def sku_normalize(series: pl.Series) -> pl.Series:
    """Normalize SKU identifiers (uppercase, strip whitespace, remove special chars)."""
    def _norm(val):
        if val is None:
            return None
        return re.sub(r"[^A-Z0-9\-]", "", val.strip().upper())
    return series.map_elements(_norm, return_dtype=pl.Utf8)

PACK = DomainPack(
    name="ecommerce",
    description="SKU normalization, price cleaning, category standardization",
    transforms=["sku_normalize", "currency_strip", "category_auto_correct", "strip"],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="sku", ops=["sku_normalize"]),
            TransformSpec(column="price", ops=["currency_strip"]),
            TransformSpec(column="category", ops=["strip", "title_case"]),
        ]
    ),
)
