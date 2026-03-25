from __future__ import annotations
import re
import polars as pl
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

@register_transform(name="account_mask", input_types=["string"], auto_apply=False, priority=50, mode="series")
def account_mask(series: pl.Series) -> pl.Series:
    """Mask account numbers showing only last 4 digits."""
    def _mask(val):
        if val is None: return None
        digits = re.sub(r"\D", "", str(val))
        if len(digits) < 4: return val
        return "*" * (len(digits) - 4) + digits[-4:]
    return series.map_elements(_mask, return_dtype=pl.Utf8)

@register_transform(name="cusip_format", input_types=["string"], auto_apply=False, priority=50, mode="series")
def cusip_format(series: pl.Series) -> pl.Series:
    """Standardize CUSIP identifiers (9 chars, uppercase)."""
    def _fmt(val):
        if val is None: return None
        return val.strip().upper()[:9]
    return series.map_elements(_fmt, return_dtype=pl.Utf8)

PACK = DomainPack(
    name="finance",
    description="Account masking, currency standardization, CUSIP/ISIN formatting",
    transforms=["account_mask", "cusip_format", "currency_strip", "date_iso8601"],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="account_number", ops=["account_mask"]),
            TransformSpec(column="amount", ops=["currency_strip"]),
            TransformSpec(column="transaction_date", ops=["date_iso8601"]),
        ]
    ),
)
