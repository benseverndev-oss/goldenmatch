from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    aba_validate_native,
    cc_format_native,
    cc_mask_native,
    cc_validate_native,
    ean_validate_native,
    ein_format_native,
    iban_format_native,
    iban_validate_native,
    imei_validate_native,
    isbn_normalize_native,
    isbn_validate_native,
    ssn_format_native,
    ssn_mask_native,
    swift_format_native,
    swift_validate_native,
    vat_format_native,
    vat_validate_native,
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


# --- SWIFT/BIC (ISO 9362, structural only) identifiers ----------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::swift`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same normalize (uppercase + strip
# ASCII spaces ONLY, NOT '-'/'.'), same structural length/charset checks. No
# checksum exists for BIC.


def _swift_normalize(val: str) -> str:
    """Uppercase + remove ASCII spaces only -- mirrors Rust ``normalize``.
    Unlike the other identifiers here, '-'/'.' are NOT stripped: a
    well-formed BIC never contains them, and silently stripping them could
    let a malformed value pass structural validation."""
    return val.replace(" ", "").upper()


def _swift_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    t = _swift_normalize(val)
    length = len(t)
    if length not in (8, 11):
        return False
    if not all(c.isascii() and c.isalpha() for c in t[0:4]):
        return False
    if not all(c.isascii() and c.isalpha() for c in t[4:6]):
        return False
    if not all(c.isascii() and c.isalnum() for c in t[6:8]):
        return False
    if length == 11 and not all(c.isascii() and c.isalnum() for c in t[8:11]):
        return False
    return True


def _swift_format_py(val: str | None) -> str | None:
    if val is None:
        return None
    if not _swift_validate_py(val):
        return None
    return _swift_normalize(val)


@register_transform(
    name="swift_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def swift_validate(series: pl.Series) -> pl.Series:
    """Validate a SWIFT/BIC code via structural checks (length 8/11 +
    per-segment charset). No checksum exists for BIC."""
    native = swift_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_swift_validate_py, return_dtype=pl.Boolean)


@register_transform(
    name="swift_format",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def swift_format(series: pl.Series) -> pl.Series:
    """Normalize a valid SWIFT/BIC code to uppercase (spaces stripped);
    ``null`` for invalid input."""
    native = swift_format_native()
    if native is not None:
        return native(series)
    return series.map_elements(_swift_format_py, return_dtype=pl.Utf8)


# --- EU VAT identifiers (bounded scope) -------------------------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::vat`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip + uppercase,
# same per-prefix structural rules, same DE/IT checksums.
#
# CHECKSUM COVERAGE: DE, IT (structural-only: all other supported prefixes).
# This is a deliberate, documented bound (Wave 0b, Task 5): all 27 EU
# member-state VAT prefixes below are validated STRUCTURALLY (country prefix
# + length + per-position charset), but only Germany (DE, ISO 7064 mod 11,10)
# and Italy (IT, partita IVA Luhn) additionally run a checksum. Checksum
# coverage may grow later without changing this contract. Unsupported /
# unknown prefixes (including a bare "GR" -- Greece's VAT prefix is the
# well-known quirk "EL") -> False.

# Per-prefix structural rule: either a tuple of per-position classes ("D"igit,
# "A"lpha, "N"alnum, or a literal char) for one or more fixed-length variants,
# or ("digits", min, max) for the one variable-length EU VAT format (RO).
_VAT_FIXED_RULES: dict[str, tuple[tuple[str, ...], ...]] = {
    "AT": (("U", "D", "D", "D", "D", "D", "D", "D", "D"),),
    "BE": (("D",) * 10,),
    "CY": (("D",) * 8 + ("A",),),
    "DE": (("D",) * 9,),
    "DK": (("D",) * 8,),
    "EE": (("D",) * 9,),
    "EL": (("D",) * 9,),
    "ES": (("N",) + ("D",) * 7 + ("N",),),
    "FI": (("D",) * 8,),
    "FR": (("N", "N") + ("D",) * 9,),
    "HR": (("D",) * 11,),
    "HU": (("D",) * 8,),
    "IE": (
        ("D", "N", "D", "D", "D", "D", "D", "A"),
        ("D", "N", "D", "D", "D", "D", "D", "A", "A"),
    ),
    "IT": (("D",) * 11,),
    "LT": (("D",) * 9, ("D",) * 12),
    "LU": (("D",) * 8,),
    "LV": (("D",) * 11,),
    "MT": (("D",) * 8,),
    "NL": (("D",) * 9 + ("B",) + ("D",) * 2,),
    "PL": (("D",) * 10,),
    "PT": (("D",) * 9,),
    "SE": (("D",) * 12,),
    "SI": (("D",) * 8,),
    "SK": (("D",) * 10,),
}
_VAT_DIGITS_RULES: dict[str, tuple[int, int]] = {
    "BG": (9, 10),
    "CZ": (8, 10),
    "RO": (2, 10),
}


def _vat_pos_ok(pos: str, c: str) -> bool:
    if pos == "D":
        return c.isascii() and c.isdigit()
    if pos == "A":
        return c.isascii() and c.isalpha()
    if pos == "N":
        return c.isascii() and c.isalnum()
    return c == pos  # literal char (e.g. NL's "B", AT's "U")


def _vat_fixed_ok(pattern: tuple[str, ...], suffix: str) -> bool:
    return len(suffix) == len(pattern) and all(
        _vat_pos_ok(p, c) for p, c in zip(pattern, suffix, strict=True)
    )


def _vat_structural_ok(prefix: str, suffix: str) -> bool:
    if prefix in _VAT_FIXED_RULES:
        return any(_vat_fixed_ok(p, suffix) for p in _VAT_FIXED_RULES[prefix])
    if prefix in _VAT_DIGITS_RULES:
        lo, hi = _VAT_DIGITS_RULES[prefix]
        return lo <= len(suffix) <= hi and suffix.isascii() and suffix.isdigit()
    return False


def _vat_de_checksum_ok(digits: str) -> bool:
    if len(digits) != 9:
        return False
    d = [ord(c) - ord("0") for c in digits]
    p = 10
    for i in range(8):
        m = (d[i] + p) % 10
        if m == 0:
            m = 10
        p = (2 * m) % 11
    check = 11 - p
    if check == 10:
        check = 0
    return check == d[8]


def _vat_it_checksum_ok(digits: str) -> bool:
    if len(digits) != 11:
        return False
    d = [ord(c) - ord("0") for c in digits]
    total = 0
    for i in range(10):
        if i % 2 == 0:
            total += d[i]
        else:
            x = d[i] * 2
            total += x - 9 if x > 9 else x
    check = (10 - (total % 10)) % 10
    return check == d[10]


def _vat_split_prefix(val: str) -> tuple[str, str] | None:
    t = _cc_strip_sep(val).upper()
    if len(t) < 3 or not (t[0].isascii() and t[0].isalpha() and t[1].isascii() and t[1].isalpha()):
        return None
    return t[0:2], t[2:]


def _vat_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    split = _vat_split_prefix(val)
    if split is None:
        return False
    prefix, suffix = split
    if not _vat_structural_ok(prefix, suffix):
        return False
    if prefix == "DE":
        return _vat_de_checksum_ok(suffix)
    if prefix == "IT":
        return _vat_it_checksum_ok(suffix)
    return True


def _vat_format_py(val: str | None) -> str | None:
    if val is None:
        return None
    if not _vat_validate_py(val):
        return None
    return _cc_strip_sep(val).upper()


@register_transform(
    name="vat_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def vat_validate(series: pl.Series) -> pl.Series:
    """Validate an EU VAT number: structural check (country prefix + length +
    charset) for all 27 supported member-state prefixes, plus a checksum for
    DE (ISO 7064 mod 11,10) and IT (partita IVA Luhn) -- see the
    CHECKSUM COVERAGE note above this section for the bound."""
    native = vat_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_vat_validate_py, return_dtype=pl.Boolean)


@register_transform(
    name="vat_format",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def vat_format(series: pl.Series) -> pl.Series:
    """Normalize a valid EU VAT number to its compact uppercase form (prefix
    kept, separators stripped); ``null`` for invalid input."""
    native = vat_format_native()
    if native is not None:
        return native(series)
    return series.map_elements(_vat_format_py, return_dtype=pl.Utf8)


def _ssn_format_py(val: str | None) -> str | None:
    if val is None:
        return None
    digits = _extract_digits(val)
    if len(digits) != 9:
        return val  # preserve invalid
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


@register_transform(
    name="ssn_format",
    input_types=["ssn", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ssn_format(series: pl.Series) -> pl.Series:
    """Normalize SSN to XXX-XX-XXXX format. Native-first over goldenflow-core."""
    native = ssn_format_native()
    if native is not None:
        return native(series)
    return series.map_elements(_ssn_format_py, return_dtype=pl.Utf8)


def _ssn_mask_py(val: str | None) -> str | None:
    if val is None:
        return None
    digits = _extract_digits(val)
    if len(digits) != 9:
        return val  # preserve invalid
    return f"***-**-{digits[5:]}"


@register_transform(
    name="ssn_mask",
    input_types=["ssn", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ssn_mask(series: pl.Series) -> pl.Series:
    """Mask SSN to ***-**-XXXX (last 4 visible). Native-first."""
    native = ssn_mask_native()
    if native is not None:
        return native(series)
    return series.map_elements(_ssn_mask_py, return_dtype=pl.Utf8)


def _ein_format_py(val: str | None) -> str | None:
    if val is None:
        return None
    digits = _extract_digits(val)
    if len(digits) != 9:
        return val  # preserve invalid
    return f"{digits[:2]}-{digits[2:]}"


@register_transform(
    name="ein_format",
    input_types=["ein", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def ein_format(series: pl.Series) -> pl.Series:
    """Normalize EIN to XX-XXXXXXX format. Native-first."""
    native = ein_format_native()
    if native is not None:
        return native(series)
    return series.map_elements(_ein_format_py, return_dtype=pl.Utf8)


# --- ABA routing number (US bank routing transit number) --------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::aba`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip, same
# 9-digit length gate, same weighted checksum.


def _aba_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    t = _cc_strip_sep(val)
    if len(t) != 9 or not t.isascii() or not t.isdigit():
        return False
    d = [ord(c) - ord("0") for c in t]
    total = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    return total % 10 == 0


@register_transform(
    name="aba_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def aba_validate(series: pl.Series) -> pl.Series:
    """Validate a US ABA bank routing number: exactly 9 digits plus the
    standard weighted checksum."""
    native = aba_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_aba_validate_py, return_dtype=pl.Boolean)


# --- IMEI (International Mobile Equipment Identity) --------------------------
#
# Pure-Python reference for goldenflow-core's ``identifiers::imei`` kernel.
# MUST reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl) -- same separator strip, same
# 15-digit length gate, same Luhn checksum (reuses ``_luhn_ok``, the same
# helper the ``cc`` family uses -- one Luhn implementation for both).


def _imei_validate_py(val: str | None) -> bool | None:
    if val is None:
        return None
    t = _cc_strip_sep(val)
    if len(t) != 15 or not t.isascii() or not t.isdigit():
        return False
    return _luhn_ok(t)


@register_transform(
    name="imei_validate",
    input_types=["identifier", "string"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def imei_validate(series: pl.Series) -> pl.Series:
    """Validate an IMEI: exactly 15 digits plus the Luhn checksum."""
    native = imei_validate_native()
    if native is not None:
        return native(series)
    return series.map_elements(_imei_validate_py, return_dtype=pl.Boolean)
