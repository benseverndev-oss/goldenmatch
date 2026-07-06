from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    email_canonical_native,
    email_extract_domain_native,
    email_lowercase_native,
    email_mask_native,
    email_normalize_native,
    email_validate_native,
)

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Pure-Python reference for goldenflow-core's ``email`` kernel. MUST reproduce
# the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl).


def _email_lowercase_py(val: str | None) -> str | None:
    if val is None:
        return None
    return val.strip().lower()


def _email_normalize_py(val: str | None) -> str | None:
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


def _email_canonical_py(val: str | None) -> str | None:
    if val is None:
        return None
    normalized = _email_normalize_py(val)
    if normalized is None:
        return None
    idx = normalized.rfind("@")
    if idx != -1:
        local = normalized[:idx]
        domain = normalized[idx + 1 :]
        if domain == "googlemail.com":
            return f"{local}@gmail.com"
    return normalized


def _email_mask_py(val: str | None) -> str | None:
    if val is None:
        return None
    v = val.strip().lower()
    idx = v.rfind("@")
    if idx == -1:
        return None
    local = v[:idx]
    domain = v[idx + 1 :]
    if not local or not domain:
        return None
    return local[0] + "*" * (len(local) - 1) + "@" + domain


def _email_extract_domain_py(val: str | None) -> str | None:
    if val is None:
        return None
    v = val.strip()
    idx = v.rfind("@")
    if idx == -1:
        return None
    domain = v[idx + 1 :]
    if not domain:
        return None
    return domain.lower()


def _email_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    val = val.strip()
    if not val:
        return False
    return bool(_EMAIL_PATTERN.match(val))


@register_transform(
    name="email_lowercase",
    input_types=["email", "string"],
    auto_apply=False,
    priority=55,
    mode="series",
)
def email_lowercase(series: pl.Series) -> pl.Series:
    """Lowercase the entire email address (trim + lowercase).

    Native-first (goldenflow-core's ``email::email_lowercase`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = email_lowercase_native()
    if native is not None:
        return native(series)
    return series.map_elements(_email_lowercase_py, return_dtype=pl.Utf8)


@register_transform(
    name="email_normalize",
    input_types=["email"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def email_normalize(series: pl.Series) -> pl.Series:
    """Normalize email: lowercase, strip +tags, strip dots from Gmail local part.

    Native-first (goldenflow-core's ``email::email_normalize`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = email_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_email_normalize_py, return_dtype=pl.Utf8)


@register_transform(
    name="email_canonical",
    input_types=["email"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def email_canonical(series: pl.Series) -> pl.Series:
    """Full dedup key: email_normalize + alias googlemail.com -> gmail.com so
    Gmail variants collapse completely.

    Native-first (goldenflow-core's ``email::email_canonical`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = email_canonical_native()
    if native is not None:
        return native(series)
    return series.map_elements(_email_canonical_py, return_dtype=pl.Utf8)


@register_transform(
    name="email_mask",
    input_types=["email"],
    auto_apply=False,
    priority=30,
    mode="series",
)
def email_mask(series: pl.Series) -> pl.Series:
    """PII mask: keep the first local char, star the rest, keep @domain
    (``John@Example.com`` -> ``j***@example.com``). None on unmaskable input.

    Native-first (goldenflow-core's ``email::email_mask`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = email_mask_native()
    if native is not None:
        return native(series)
    return series.map_elements(_email_mask_py, return_dtype=pl.Utf8)


@register_transform(
    name="email_extract_domain",
    input_types=["email"],
    auto_apply=False,
    priority=40,
    mode="series",
)
def email_extract_domain(series: pl.Series) -> pl.Series:
    """Extract the lowercased domain from an email address.

    Native-first (goldenflow-core's ``email::email_extract_domain`` kernel);
    the pure-Python fallback below is the byte-exact reference this kernel
    replicates. Inputs without '@' (or None) yield None.
    """
    native = email_extract_domain_native()
    if native is not None:
        return native(series)
    return series.map_elements(_email_extract_domain_py, return_dtype=pl.Utf8)


@register_transform(
    name="email_validate",
    input_types=["email", "string"],
    auto_apply=False,
    priority=60,
    mode="series",
)
def email_validate(series: pl.Series) -> pl.Series:
    """Validate email format. Returns True/False/None.

    Native-first (goldenflow-core's ``email::email_validate`` kernel); the
    pure-Python fallback below is the byte-exact reference this kernel
    replicates.
    """
    native = email_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_email_validate_py, return_dtype=pl.Boolean)
