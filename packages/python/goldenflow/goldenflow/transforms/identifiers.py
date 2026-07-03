from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    cc_format_native,
    cc_mask_native,
    cc_validate_native,
)


def _extract_digits(val: str) -> str:
    """Extract only digit characters from a string."""
    return re.sub(r"\D", "", val)


# --- Payment-card (Luhn) identifiers ----------------------------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::luhn`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip, same Luhn,
# same 13-19 length band, same Amex 4-6-5 vs 4-4-4-4... grouping, same mask.


def _cc_strip_sep(val: str) -> str:
    """Remove ASCII spaces, '-' and '.' -- mirrors Rust ``strip_sep``."""
    return val.translate(str.maketrans("", "", " -."))


def _cc_normalized_digits(val: str) -> str | None:
    d = _cc_strip_sep(val)
    if not d or not d.isascii() or not d.isdigit():
        return None
    return d


def _luhn_ok(digits: str) -> bool:
    total = 0
    dbl = False
    for c in reversed(digits):
        d = ord(c) - ord("0")
        if dbl:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        dbl = not dbl
    return total % 10 == 0


def _cc_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    d = _cc_normalized_digits(val)
    if d is None:
        return False
    return 13 <= len(d) <= 19 and _luhn_ok(d)


def _cc_group(d: str, sizes: list[int]) -> str:
    out: list[str] = []
    i = 0
    for n in sizes:
        if i >= len(d):
            break
        end = min(i + n, len(d))
        out.append(d[i:end])
        i = end
    while i < len(d):
        end = min(i + 4, len(d))
        out.append(d[i:end])
        i = end
    return " ".join(out)


def _cc_format_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _cc_normalized_digits(val)
    if d is None:
        return None
    if not (13 <= len(d) <= 19 and _luhn_ok(d)):
        return None
    if len(d) == 15 and (d.startswith("34") or d.startswith("37")):
        sizes = [4, 6, 5]
    else:
        sizes = [4, 4, 4, 4, 4]
    return _cc_group(d, sizes)


def _cc_mask_py(val: str | None) -> str | None:
    if val is None:
        return None
    d = _cc_normalized_digits(val)
    if d is None:
        return None
    if not (13 <= len(d) <= 19):
        return None
    return "*" * (len(d) - 4) + d[-4:]


@register_transform(
    name="cc_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def cc_validate(series: pl.Series) -> pl.Series:
    """Validate a payment-card number via the Luhn checksum."""
    native = cc_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_cc_validate_py, return_dtype=pl.Boolean)


@register_transform(
    name="cc_format",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def cc_format(series: pl.Series) -> pl.Series:
    """Group a valid payment-card number (Amex 4-6-5, else 4-4-4-4...);
    ``null`` for invalid input."""
    native = cc_format_native()
    if native is not None:
        return native(series)
    return series.map_elements(_cc_format_py, return_dtype=pl.Utf8)


@register_transform(
    name="cc_mask",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def cc_mask(series: pl.Series) -> pl.Series:
    """Mask a payment-card number to stars + last 4 digits."""
    native = cc_mask_native()
    if native is not None:
        return native(series)
    return series.map_elements(_cc_mask_py, return_dtype=pl.Utf8)


@register_transform(
    name="ssn_format",
    input_types=["ssn", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ssn_format(series: pl.Series) -> pl.Series:
    """Normalize SSN to XXX-XX-XXXX format."""

    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        digits = _extract_digits(val)
        if len(digits) != 9:
            return val  # preserve invalid
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"

    return series.map_elements(_format, return_dtype=pl.Utf8)


@register_transform(
    name="ssn_mask",
    input_types=["ssn", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ssn_mask(series: pl.Series) -> pl.Series:
    """Mask SSN to ***-**-XXXX (last 4 visible)."""

    def _mask(val: str | None) -> str | None:
        if val is None:
            return None
        digits = _extract_digits(val)
        if len(digits) != 9:
            return val  # preserve invalid
        return f"***-**-{digits[5:]}"

    return series.map_elements(_mask, return_dtype=pl.Utf8)


@register_transform(
    name="ein_format",
    input_types=["ein", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ein_format(series: pl.Series) -> pl.Series:
    """Normalize EIN to XX-XXXXXXX format."""

    def _format(val: str | None) -> str | None:
        if val is None:
            return None
        digits = _extract_digits(val)
        if len(digits) != 9:
            return val  # preserve invalid
        return f"{digits[:2]}-{digits[2:]}"

    return series.map_elements(_format, return_dtype=pl.Utf8)
