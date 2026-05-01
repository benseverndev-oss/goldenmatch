from __future__ import annotations
import polars as pl
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

@register_transform(name="mls_normalize", input_types=["string"], auto_apply=False, priority=50, mode="series")
def mls_normalize(series: pl.Series) -> pl.Series:
    """Normalize MLS listing IDs (uppercase, strip whitespace)."""
    def _norm(val):
        if val is None:
            return None
        return val.strip().upper()
    return series.map_elements(_norm, return_dtype=pl.Utf8)

PACK = DomainPack(
    name="real_estate",
    description="Address parsing (USPS), MLS ID normalization, price cleaning",
    transforms=["mls_normalize", "address_standardize", "zip_normalize", "currency_strip"],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="mls_id", ops=["mls_normalize"]),
            TransformSpec(column="address", ops=["strip", "address_standardize"]),
            TransformSpec(column="price", ops=["currency_strip"]),
            TransformSpec(column="zip", ops=["zip_normalize"]),
        ]
    ),
)
