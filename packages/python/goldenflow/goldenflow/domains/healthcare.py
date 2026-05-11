from __future__ import annotations

import re

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform


@register_transform(name="npi_validate", input_types=["string"], auto_apply=False, priority=50, mode="series")
def npi_validate(series: pl.Series) -> pl.Series:
    """Validate NPI numbers (10-digit, Luhn check)."""
    def _validate(val):
        if val is None:
            return None
        digits = re.sub(r"\D", "", str(val))
        if len(digits) != 10:
            return False
        # Luhn check with prefix 80840
        full = "80840" + digits
        total = 0
        for i, d in enumerate(reversed(full)):
            n = int(d)
            if i % 2 == 1:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        return total % 10 == 0
    return series.map_elements(_validate, return_dtype=pl.Boolean)

@register_transform(name="icd10_format", input_types=["string"], auto_apply=False, priority=50, mode="series")
def icd10_format(series: pl.Series) -> pl.Series:
    """Standardize ICD-10 codes (uppercase, insert dot after 3rd char)."""
    def _fmt(val):
        if val is None:
            return None
        code = val.strip().upper().replace(".", "")
        if len(code) > 3:
            return code[:3] + "." + code[3:]
        return code
    return series.map_elements(_fmt, return_dtype=pl.Utf8)

PACK = DomainPack(
    name="healthcare",
    description="MRN normalization, ICD-10 formatting, NPI validation, date standardization",
    transforms=["npi_validate", "icd10_format", "date_iso8601", "null_standardize", "strip"],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="npi", ops=["npi_validate"]),
            TransformSpec(column="icd10_code", ops=["icd10_format"]),
            TransformSpec(column="service_date", ops=["date_iso8601"]),
            TransformSpec(column="patient_name", ops=["strip", "title_case"]),
        ]
    ),
)
