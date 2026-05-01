from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@register_transform(
    name="email_lowercase",
    input_types=["email", "string"],
    auto_apply=False,
    priority=55,
    mode="series",
)
def email_lowercase(series: pl.Series) -> pl.Series:
    """Lowercase the entire email address."""

    def _lower(val: str | None) -> str | None:
        if val is None:
            return None
        return val.strip().lower()

    return series.map_elements(_lower, return_dtype=pl.Utf8)


@register_transform(
    name="email_normalize",
    input_types=["email"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def email_normalize(series: pl.Series) -> pl.Series:
    """Normalize email: lowercase, strip +tags, strip dots from Gmail local part."""

    def _normalize(val: str | None) -> str | None:
        if val is None:
            return None
        original = val
        val = val.strip().lower()
        if not val or "@" not in val:
            return original  # preserve invalid values
        local, domain = val.rsplit("@", 1)
        # Strip +tag
        local = local.split("+")[0]
        # Strip dots from Gmail local part
        if domain in ("gmail.com", "googlemail.com"):
            local = local.replace(".", "")
        return f"{local}@{domain}"

    return series.map_elements(_normalize, return_dtype=pl.Utf8)


@register_transform(
    name="email_extract_domain",
    input_types=["email"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def email_extract_domain(series: pl.Series) -> pl.Series:
    """Extract the domain from an email address."""

    def _domain(val: str | None) -> str | None:
        if val is None:
            return None
        if "@" not in val:
            return None
        return val.strip().rsplit("@", 1)[1].lower()

    return series.map_elements(_domain, return_dtype=pl.Utf8)


@register_transform(
    name="email_validate",
    input_types=["email", "string"],
    auto_apply=False,
    priority=60,
    mode="series",
)
def email_validate(series: pl.Series) -> pl.Series:
    """Validate email format. Returns True/False/None."""

    def _validate(val: str | None) -> bool | None:
        if val is None:
            return None
        val = val.strip()
        if not val:
            return False
        return bool(_EMAIL_PATTERN.match(val))

    return series.map_elements(_validate, return_dtype=pl.Boolean)
