from __future__ import annotations

import re

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.domains.base import DomainPack
from goldenflow.transforms import register_transform

_SSN_PATTERN = re.compile(r"^(\d{3})-?(\d{2})-?(\d{4})$")


@register_transform(
    name="ssn_mask", input_types=["ssn", "string"], auto_apply=False, priority=50, mode="series"
)
def ssn_mask(series: pl.Series) -> pl.Series:
    def _mask(val: str | None) -> str | None:
        if val is None:
            return None
        m = _SSN_PATTERN.match(val.strip())
        if m:
            return f"***-**-{m.group(3)}"
        return val

    return series.map_elements(_mask, return_dtype=pl.Utf8)


@register_transform(
    name="ssn_validate", input_types=["ssn", "string"], auto_apply=False, priority=55, mode="series"
)
def ssn_validate(series: pl.Series) -> pl.Series:
    def _validate(val: str | None) -> bool | None:
        if val is None:
            return None
        m = _SSN_PATTERN.match(val.strip())
        if not m:
            return False
        area, group, serial = m.group(1), m.group(2), m.group(3)
        if area == "000" or group == "00" or serial == "0000":
            return False
        return True

    return series.map_elements(_validate, return_dtype=pl.Boolean)


PACK = DomainPack(
    name="people_hr",
    description="Name parsing, SSN formatting, employment dates, gender/boolean standardization",
    transforms=[
        "split_name", "split_name_reverse", "strip_titles", "strip_suffixes",
        "name_proper", "ssn_mask", "ssn_validate",
        "date_iso8601", "gender_standardize", "boolean_normalize",
    ],
    default_config=GoldenFlowConfig(
        transforms=[
            TransformSpec(column="name", ops=["strip", "strip_titles", "title_case"]),
            TransformSpec(column="ssn", ops=["ssn_validate"]),
            TransformSpec(column="gender", ops=["gender_standardize"]),
            TransformSpec(column="hire_date", ops=["date_iso8601"]),
            TransformSpec(column="active", ops=["boolean_normalize"]),
        ]
    ),
)
