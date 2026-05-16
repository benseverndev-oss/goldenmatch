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
    mode="expr",
)
def email_lowercase(column: str) -> pl.Expr:
    """Lowercase the entire email address.

    Native Polars: strip then to_lowercase. Drops the per-row Python UDF
    (previously map_elements). Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return pl.col(column).str.strip_chars().str.to_lowercase()


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
    mode="expr",
)
def email_extract_domain(column: str) -> pl.Expr:
    """Extract the lowercased domain from an email address.

    Native Polars: strip → regex extract after '@' → lowercase. Inputs
    without '@' (or None) yield None via Polars's native null propagation.
    Spec docs/superpowers/specs/2026-05-15-map-elements-attack-design.md
    Tier 1.
    """
    return (
        pl.col(column)
        .str.strip_chars()
        .str.extract(r"@([^@]+)$", 1)
        .str.to_lowercase()
    )


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
