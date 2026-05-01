from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


@register_transform(
    name="url_normalize",
    input_types=["url", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def url_normalize(series: pl.Series) -> pl.Series:
    """Normalize URLs: ensure scheme, lowercase domain, strip trailing slash."""

    def _norm(val: str | None) -> str | None:
        if val is None:
            return None
        val = val.strip()
        if not val:
            return None
        # Add scheme if missing
        if not _SCHEME_RE.match(val):
            val = "https://" + val
        # Split scheme from rest
        scheme_end = val.index("://") + 3
        scheme = val[:scheme_end].lower()
        rest = val[scheme_end:]
        # Lowercase the domain (everything before first /)
        slash_idx = rest.find("/")
        if slash_idx == -1:
            domain = rest.lower()
            path = ""
        else:
            domain = rest[:slash_idx].lower()
            path = rest[slash_idx:]
        # Strip trailing slash (but not if path is just "/")
        result = scheme + domain + path
        if result.endswith("/") and len(result) > scheme_end + len(domain) + 1:
            result = result.rstrip("/")
        elif result.endswith("/") and path == "/":
            result = result[:-1]
        return result

    return series.map_elements(_norm, return_dtype=pl.Utf8)


@register_transform(
    name="url_extract_domain",
    input_types=["url", "string"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def url_extract_domain(series: pl.Series) -> pl.Series:
    """Extract domain from a URL."""

    def _domain(val: str | None) -> str | None:
        if val is None:
            return None
        val = val.strip()
        if not val:
            return None
        # Strip scheme
        if "://" in val:
            val = val.split("://", 1)[1]
        # Take everything before the first /
        domain = val.split("/", 1)[0]
        return domain.lower() if domain else None

    return series.map_elements(_domain, return_dtype=pl.Utf8)
