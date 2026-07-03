from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    cc_format_native,
    cc_mask_native,
    cc_validate_native,
    ean_validate_native,
    iban_format_native,
    iban_validate_native,
    isbn_normalize_native,
    isbn_validate_native,
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


# --- IBAN (ISO 7064 mod-97) identifiers -------------------------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::iban`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip + uppercase,
# same structural checks, same mod-97 fold, same 4-char grouping.


def _iban_normalize(val: str) -> str:
    """Strip separators + uppercase -- mirrors Rust ``strip_sep`` + upper."""
    return _cc_strip_sep(val).upper()


def _iban_mod97_ok(t: str) -> bool:
    """ISO 7064 mod-97 check: move the first 4 chars to the end, fold the
    resulting decimal string mod 97 digit-by-digit (letters -> two-digit
    A=10..Z=35 value folded in one step), require remainder 1."""
    rearranged = t[4:] + t[:4]
    acc = 0
    for c in rearranged:
        if c.isdigit():
            acc = (acc * 10 + (ord(c) - ord("0"))) % 97
        else:
            v = (ord(c) - ord("A")) + 10
            acc = (acc * 100 + v) % 97
    return acc == 1


def _iban_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    t = _iban_normalize(val)
    if not (15 <= len(t) <= 34):
        return False
    if not (t[0].isascii() and t[0].isalpha() and t[1].isascii() and t[1].isalpha()):
        return False
    if not (t[2].isascii() and t[2].isdigit() and t[3].isascii() and t[3].isdigit()):
        return False
    if not all(c.isascii() and c.isalnum() for c in t[4:]):
        return False
    return _iban_mod97_ok(t)


def _iban_group4(t: str) -> str:
    return " ".join(t[i : i + 4] for i in range(0, len(t), 4))


def _iban_format_py(val: str | None) -> str | None:
    if val is None:
        return None
    if not _iban_validate_py(val):
        return None
    return _iban_group4(_iban_normalize(val))


@register_transform(
    name="iban_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def iban_validate(series: pl.Series) -> pl.Series:
    """Validate an IBAN via structural checks + the ISO 7064 mod-97 check."""
    native = iban_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_iban_validate_py, return_dtype=pl.Boolean)


@register_transform(
    name="iban_format",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def iban_format(series: pl.Series) -> pl.Series:
    """Group a valid IBAN into 4-char blocks; ``null`` for invalid input."""
    native = iban_format_native()
    if native is not None:
        return native(series)
    return series.map_elements(_iban_format_py, return_dtype=pl.Utf8)


# --- ISBN (10/13 checksum) identifiers --------------------------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::isbn`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip + trailing-X
# uppercase, same ISBN-10/13 checksums, same ISBN-10 -> ISBN-13 conversion.


def _isbn_normalize_case(val: str) -> str:
    """Strip separators; uppercase a trailing 'x' -- mirrors Rust
    ``normalize_case``."""
    t = _cc_strip_sep(val)
    if t and t[-1] == "x":
        t = t[:-1] + "X"
    return t


def _isbn10_checksum_ok(t: str) -> bool:
    if len(t) != 10:
        return False
    if not t[0:9].isascii() or not t[0:9].isdigit():
        return False
    last = t[9]
    if not (last.isascii() and last.isdigit()) and last != "X":
        return False
    total = 0
    for i, c in enumerate(t):
        d = 10 if c == "X" else ord(c) - ord("0")
        total += d * (10 - i)
    return total % 11 == 0


def _isbn13_checksum_ok(t: str) -> bool:
    if len(t) != 13 or not t.isascii() or not t.isdigit():
        return False
    total = 0
    for i, c in enumerate(t):
        d = ord(c) - ord("0")
        weight = 1 if i % 2 == 0 else 3
        total += d * weight
    return total % 10 == 0


def _isbn13_check_digit(twelve: str) -> str:
    total = 0
    for i, c in enumerate(twelve):
        d = ord(c) - ord("0")
        weight = 1 if i % 2 == 0 else 3
        total += d * weight
    return str((10 - (total % 10)) % 10)


def _isbn_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    t = _isbn_normalize_case(val)
    if len(t) == 10:
        return _isbn10_checksum_ok(t)
    if len(t) == 13:
        return _isbn13_checksum_ok(t)
    return False


def _isbn_normalize_py(val: str | None) -> str | None:
    if val is None:
        return None
    t = _isbn_normalize_case(val)
    if len(t) == 10:
        if not _isbn10_checksum_ok(t):
            return None
        twelve = "978" + t[0:9]
        return twelve + _isbn13_check_digit(twelve)
    if len(t) == 13:
        if not _isbn13_checksum_ok(t):
            return None
        return t
    return None


@register_transform(
    name="isbn_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def isbn_validate(series: pl.Series) -> pl.Series:
    """Validate an ISBN-10 or ISBN-13 via its checksum."""
    native = isbn_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_isbn_validate_py, return_dtype=pl.Boolean)


@register_transform(
    name="isbn_normalize",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def isbn_normalize(series: pl.Series) -> pl.Series:
    """Canonicalize a valid ISBN-10/13 to its 13-digit form; ``null`` for
    invalid input."""
    native = isbn_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_isbn_normalize_py, return_dtype=pl.Utf8)


# --- EAN/UPC (GTIN mod-10) identifiers --------------------------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::ean`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip, same length
# band (8/12/13), same GTIN mod-10 check. Validate-only -- no format/normalize.


def _ean_gtin_checksum_ok(t: str) -> bool:
    if not t.isascii() or not t.isdigit():
        return False
    data, check = t[:-1], t[-1]
    check_digit = ord(check) - ord("0")
    total = 0
    for i, c in enumerate(reversed(data)):
        d = ord(c) - ord("0")
        weight = 3 if i % 2 == 0 else 1
        total += d * weight
    computed = (10 - (total % 10)) % 10
    return computed == check_digit


def _ean_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    t = _cc_strip_sep(val)
    if len(t) not in (8, 12, 13):
        return False
    return _ean_gtin_checksum_ok(t)


@register_transform(
    name="ean_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ean_validate(series: pl.Series) -> pl.Series:
    """Validate an EAN-8, UPC-A, or EAN-13 via its GTIN mod-10 checksum."""
    native = ean_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_ean_validate_py, return_dtype=pl.Boolean)


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
