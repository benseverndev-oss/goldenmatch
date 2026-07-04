#!/usr/bin/env python
"""Generate/check the byte-parity corpus for goldenflow identifier transforms.

Recomputes ``expected`` for every corpus row by calling the reference
kernels -- the native ``goldenflow._native`` (or ``goldenflow_native._native``)
module when importable, else the pure-Python fallback in
``goldenflow.transforms.identifiers``. Both are asserted to agree wherever
native is available, so either source is a valid oracle; native is preferred
because it is the canonical reference (docs/design/2026-07-01-rust-is-the-
reference-roadmap.md).

Usage:
    python scripts/gen_identifiers_corpus.py            # rewrite the corpus
    python scripts/gen_identifiers_corpus.py --check     # diff only, exit 1 on drift

The corpus format is JSON Lines, one row per case:
    {"transform": "cc_validate", "input": "4242 4242 4242 4242", "expected": true}
    {"transform": "cc_format",   "input": "378282246310005",     "expected": "3782 822463 10005"}
    {"transform": "cc_mask",     "input": "4242424242424242",    "expected": "************4242"}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goldenflow.core._native_loader import native_available, native_module  # noqa: E402
from goldenflow.transforms.identifiers import (  # noqa: E402
    _aba_validate_py,
    _cc_format_py,
    _cc_mask_py,
    _cc_validate_py,
    _ean_validate_py,
    _iban_format_py,
    _iban_validate_py,
    _imei_validate_py,
    _isbn_normalize_py,
    _isbn_validate_py,
    _swift_format_py,
    _swift_validate_py,
    _vat_format_py,
    _vat_validate_py,
)
from goldenflow.transforms.names import _name_transliterate_py  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent.parent / "tests" / "parity" / "identifiers_corpus.jsonl"

# (transform, input) pairs. `expected` is recomputed, never hand-maintained.
_CASES: list[tuple[str, str | None]] = [
    # --- cc_validate: valid ---
    ("cc_validate", "4242 4242 4242 4242"),  # Visa test, spaced
    ("cc_validate", "4242424242424242"),  # Visa test, bare
    ("cc_validate", "5555555555554444"),  # Mastercard
    ("cc_validate", "378282246310005"),  # Amex (15)
    ("cc_validate", "4000-0000-0000-0002"),  # Visa, dashed
    ("cc_validate", "6011111111111117"),  # Discover
    ("cc_validate", "30569309025904"),  # Diners Club (14)
    ("cc_validate", "4111111111111111111"),  # 19-digit, length-boundary, bad checksum
    # --- cc_validate: invalid ---
    ("cc_validate", "4242424242424241"),  # bad checksum
    ("cc_validate", "1234"),  # too short
    ("cc_validate", "4242abcd42424242"),  # non-digit
    ("cc_validate", "42424242424242424242"),  # 20 digits, too long
    ("cc_validate", "123456789012"),  # 12 digits, too short
    ("cc_validate", ""),  # empty
    ("cc_validate", None),  # null
    # --- cc_format: valid ---
    ("cc_format", "4242424242424242"),  # 16-digit -> 4-4-4-4
    ("cc_format", "4242 4242 4242 4242"),  # already spaced
    ("cc_format", "378282246310005"),  # Amex -> 4-6-5
    ("cc_format", "340000000000009"),  # Amex (34 prefix) -> 4-6-5
    ("cc_format", "6011111111111117"),  # Discover -> 4-4-4-4
    ("cc_format", "30569309025904"),  # Diners (14, not Amex) -> 4-4-4-2
    ("cc_format", "4111111111111111111"),  # 19-digit -> trailing groups of 4
    # --- cc_format: invalid -> null ---
    ("cc_format", "4242424242424241"),  # bad checksum
    ("cc_format", "1234"),  # too short
    ("cc_format", ""),
    ("cc_format", None),
    # --- cc_mask: valid (length-only, no Luhn requirement) ---
    ("cc_mask", "4242424242424242"),
    ("cc_mask", "4242 4242 4242 4242"),
    ("cc_mask", "378282246310005"),
    ("cc_mask", "4242424242424241"),  # bad checksum but still maskable (len OK)
    ("cc_mask", "4111111111111111111"),  # 19-digit
    # --- cc_mask: invalid -> null ---
    ("cc_mask", "bogus"),
    ("cc_mask", "1234"),
    ("cc_mask", ""),
    ("cc_mask", None),
    # --- iban_validate: valid ---
    ("iban_validate", "GB82 WEST 1234 5698 7654 32"),  # UK, spaced
    ("iban_validate", "GB82WEST12345698765432"),  # UK, bare
    ("iban_validate", "DE89370400440532013000"),  # Germany
    ("iban_validate", "FR1420041010050500013M02606"),  # France, alnum BBAN
    ("iban_validate", "de89 370400440532013000"),  # lowercase + spaced
    # --- iban_validate: invalid ---
    ("iban_validate", "GB82WEST12345698765433"),  # bad check digits
    ("iban_validate", "XX00"),  # too short
    ("iban_validate", "GB82WEST1234569876543212345678901234"),  # too long (>34)
    ("iban_validate", "1B82WEST12345698765432"),  # non-alpha country code
    ("iban_validate", "GBXXWEST12345698765432"),  # non-digit check digits
    ("iban_validate", "GB82WEST1234569876543!"),  # non-alnum BBAN char
    ("iban_validate", ""),  # empty
    ("iban_validate", None),  # null
    # --- iban_format: valid ---
    ("iban_format", "DE89370400440532013000"),
    ("iban_format", "GB82 WEST 1234 5698 7654 32"),
    ("iban_format", "FR1420041010050500013M02606"),
    # --- iban_format: invalid -> null ---
    ("iban_format", "GB82WEST12345698765433"),
    ("iban_format", "XX00"),
    ("iban_format", ""),
    ("iban_format", None),
    # --- isbn_validate: valid ---
    ("isbn_validate", "0-306-40615-2"),  # ISBN-10, dashed
    ("isbn_validate", "0306406152"),  # ISBN-10, bare
    ("isbn_validate", "0-19-852663-6"),  # ISBN-10, digit check
    ("isbn_validate", "0-8044-2957-X"),  # ISBN-10, X check digit
    ("isbn_validate", "0-8044-2957-x"),  # ISBN-10, lowercase x check digit
    ("isbn_validate", "978-0-306-40615-7"),  # ISBN-13, dashed
    ("isbn_validate", "9780306406157"),  # ISBN-13, bare
    # --- isbn_validate: invalid ---
    ("isbn_validate", "0306406153"),  # ISBN-10, bad check digit
    ("isbn_validate", "9780306406158"),  # ISBN-13, bad check digit
    ("isbn_validate", "12345"),  # wrong length
    ("isbn_validate", ""),  # empty
    ("isbn_validate", None),  # null
    # --- isbn_normalize: valid ---
    ("isbn_normalize", "0306406152"),  # ISBN-10 -> ISBN-13
    ("isbn_normalize", "0-306-40615-2"),  # ISBN-10, dashed -> ISBN-13
    ("isbn_normalize", "0-8044-2957-X"),  # ISBN-10, X check -> ISBN-13
    ("isbn_normalize", "978-0-306-40615-7"),  # ISBN-13 -> canonical digits
    ("isbn_normalize", "9780306406157"),  # ISBN-13, already canonical
    # --- isbn_normalize: invalid -> null ---
    ("isbn_normalize", "0306406153"),  # bad check digit
    ("isbn_normalize", "12345"),  # wrong length
    ("isbn_normalize", ""),
    ("isbn_normalize", None),
    # --- ean_validate: valid ---
    ("ean_validate", "4006381333931"),  # EAN-13
    ("ean_validate", "73513537"),  # EAN-8
    ("ean_validate", "036000291452"),  # UPC-A
    ("ean_validate", "400 6381 3339 31"),  # EAN-13, spaced
    ("ean_validate", "0-36000-29145-2"),  # UPC-A, dashed
    # --- ean_validate: invalid ---
    ("ean_validate", "4006381333930"),  # bad check digit
    ("ean_validate", "12345"),  # wrong length
    ("ean_validate", "40063813339a1"),  # non-digit
    ("ean_validate", ""),  # empty
    ("ean_validate", None),  # null
    # --- swift_validate: valid ---
    ("swift_validate", "DEUTDEFF"),  # 8-char, valid
    ("swift_validate", "DEUTDEFF500"),  # 11-char, valid
    ("swift_validate", "NEDSZAJJXXX"),  # 11-char, valid, all-alpha branch
    ("swift_validate", "deutdeff"),  # lowercase -> valid, normalized
    ("swift_validate", "deu tdeff"),  # embedded space stripped -> valid
    # --- swift_validate: invalid ---
    ("swift_validate", "DEUTDEFF5"),  # 9 chars, bad length
    ("swift_validate", "DEUT1EFF"),  # digit in institution code
    ("swift_validate", "1234DEFF"),  # digits in institution code
    ("swift_validate", "DEUTDE-FF"),  # hyphen not stripped -> bad length/charset
    ("swift_validate", ""),  # empty
    ("swift_validate", None),  # null
    # --- swift_format: valid ---
    ("swift_format", "deutdeff"),  # -> DEUTDEFF
    ("swift_format", "DEUTDEFF500"),
    ("swift_format", "ne dsz ajj xxx"),  # spaced -> NEDSZAJJXXX
    # --- swift_format: invalid -> null ---
    ("swift_format", "DEUTDEFF5"),
    ("swift_format", "1234DEFF"),
    ("swift_format", ""),
    ("swift_format", None),
    # --- vat_validate: valid, checksummed (DE, IT) ---
    ("vat_validate", "DE136695976"),  # DE, checksum ok
    ("vat_validate", "de 136 695 976"),  # DE, lowercase + spaced
    ("vat_validate", "IT00743110157"),  # IT, checksum ok
    # --- vat_validate: valid, structural-only prefixes ---
    ("vat_validate", "NL004495445B01"),  # NL, digits + B + 2 digits
    ("vat_validate", "ATU13585627"),  # AT, U + 8 digits
    ("vat_validate", "FR12345678901"),  # FR, 2 alnum + 9 digits
    ("vat_validate", "RO12"),  # RO, variable-length digits (2)
    # --- vat_validate: invalid ---
    ("vat_validate", "DE136695970"),  # bad DE checksum
    ("vat_validate", "IT00743110150"),  # bad IT checksum
    ("vat_validate", "ZZ123"),  # unknown prefix
    ("vat_validate", "DE12345"),  # bad length for DE
    ("vat_validate", "GR123456789"),  # GR is not a valid VAT prefix (EL is)
    ("vat_validate", ""),  # empty
    ("vat_validate", None),  # null
    # --- vat_format: valid ---
    ("vat_format", "de 136 695 976"),  # -> DE136695976
    ("vat_format", "NL004495445B01"),
    # --- vat_format: invalid -> null ---
    ("vat_format", "DE136695970"),  # bad checksum
    ("vat_format", "ZZ123"),  # unknown prefix
    ("vat_format", ""),
    ("vat_format", None),
    # --- aba_validate: valid ---
    ("aba_validate", "011000015"),  # valid checksum
    ("aba_validate", "021000021"),  # valid checksum
    ("aba_validate", "122105155"),  # valid checksum
    ("aba_validate", "011-000-015"),  # dashed, still valid
    # --- aba_validate: invalid ---
    ("aba_validate", "011000016"),  # bad checksum
    ("aba_validate", "12345"),  # wrong length
    ("aba_validate", "01100001a"),  # non-digit
    ("aba_validate", ""),  # empty
    ("aba_validate", None),  # null
    # --- imei_validate: valid ---
    ("imei_validate", "490154203237518"),  # valid Luhn
    ("imei_validate", "356938035643809"),  # valid Luhn
    ("imei_validate", "49-0154-203237518"),  # dashed, still valid
    # --- imei_validate: invalid ---
    ("imei_validate", "490154203237519"),  # bad Luhn
    ("imei_validate", "12345"),  # wrong length
    ("imei_validate", "49015420323751a"),  # non-digit
    ("imei_validate", ""),  # empty
    ("imei_validate", None),  # null
    # --- astral-plane / non-ASCII char edge cases (UTF-16-vs-codepoint length
    # gate insurance -- Python `len()` counts codepoints, JS `.length` counts
    # UTF-16 code units, so an astral-plane char (surrogate pair) counts as 1
    # in Python but 2 in JS; both sides must still reject identically because
    # every char-class gate rejects non-ASCII regardless of how the length is
    # counted). All expected to fail structurally -> false/null on both sides.
    ("cc_validate", "424242424242\U0001f6002"),  # astral emoji inside a card number
    ("iban_validate", "DE89370400440532\U0001f60013000"),  # astral emoji inside IBAN BBAN
    ("vat_validate", "DE13669597\U0001f600"),  # astral emoji inside VAT suffix
    # --- name_transliterate ---
    ("name_transliterate", "José"),  # single acute vowel
    ("name_transliterate", "Müller"),  # diaeresis
    ("name_transliterate", "Straße"),  # eszett -> ss
    ("name_transliterate", "Łódź"),  # l-stroke, o-acute, z-acute
    ("name_transliterate", "Renée"),  # trailing double acute-e
    ("name_transliterate", "Æsir"),  # ligature -> two-char AE
    ("name_transliterate", "Smith"),  # pure ASCII passthrough
    ("name_transliterate", ""),  # empty
    ("name_transliterate", None),  # null
    ("name_transliterate", "张\U0001f600"),  # CJK + emoji, both unmapped -> dropped
    ("name_transliterate", "Nguyễn"),  # Vietnamese combining diacritic, unmapped -> dropped
]

_PY_FN = {
    "cc_validate": _cc_validate_py,
    "cc_format": _cc_format_py,
    "cc_mask": _cc_mask_py,
    "iban_validate": _iban_validate_py,
    "iban_format": _iban_format_py,
    "isbn_validate": _isbn_validate_py,
    "isbn_normalize": _isbn_normalize_py,
    "ean_validate": _ean_validate_py,
    "swift_validate": _swift_validate_py,
    "swift_format": _swift_format_py,
    "vat_validate": _vat_validate_py,
    "vat_format": _vat_format_py,
    "aba_validate": _aba_validate_py,
    "imei_validate": _imei_validate_py,
    "name_transliterate": _name_transliterate_py,
}

_NATIVE_ARROW_FN = {
    "cc_validate": "cc_validate_arrow",
    "cc_format": "cc_format_arrow",
    "cc_mask": "cc_mask_arrow",
    "iban_validate": "iban_validate_arrow",
    "iban_format": "iban_format_arrow",
    "isbn_validate": "isbn_validate_arrow",
    "isbn_normalize": "isbn_normalize_arrow",
    "ean_validate": "ean_validate_arrow",
    "swift_validate": "swift_validate_arrow",
    "swift_format": "swift_format_arrow",
    "vat_validate": "vat_validate_arrow",
    "vat_format": "vat_format_arrow",
    "aba_validate": "aba_validate_arrow",
    "imei_validate": "imei_validate_arrow",
    "name_transliterate": "name_transliterate_arrow",
}


def _native_one(transform: str, value: str | None) -> object:
    """Call the native kernel on a single value via a length-1 Arrow array.
    Returns ``_NO_NATIVE_SYMBOL`` if the installed/built module predates the
    ``cc`` kernel (wheel-skew: a stale ``goldenflow-native`` wheel without the
    new symbols) -- in that case pure-Python is the only oracle for this row."""
    import pyarrow as pa

    nm = native_module()
    attr = _NATIVE_ARROW_FN[transform]
    if not hasattr(nm, attr):
        return _NO_NATIVE_SYMBOL
    func = getattr(nm, attr)
    out = func(pa.array([value], type=pa.string()))
    return out.to_pylist()[0]


_NO_NATIVE_SYMBOL = object()


def compute_expected(transform: str, value: str | None) -> object:
    py_result = _PY_FN[transform](value)
    if native_available():
        try:
            nat_result = _native_one(transform, value)
        except ImportError:
            nat_result = _NO_NATIVE_SYMBOL  # pyarrow not installed
        if nat_result is not _NO_NATIVE_SYMBOL and nat_result != py_result:
            raise AssertionError(
                f"native/python disagree on {transform}({value!r}): "
                f"native={nat_result!r} python={py_result!r}"
            )
    return py_result


def build_corpus() -> list[dict[str, object]]:
    rows = []
    for transform, value in _CASES:
        expected = compute_expected(transform, value)
        rows.append({"transform": transform, "input": value, "expected": expected})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in-memory and diff against the committed corpus; "
        "exit nonzero on drift (used by CI)",
    )
    args = parser.parse_args()

    rows = build_corpus()
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    new_content = "\n".join(lines) + "\n"

    oracle = "native" if native_available() else "pure-Python fallback"

    if args.check:
        if not CORPUS_PATH.exists():
            print(f"MISSING: {CORPUS_PATH}", file=sys.stderr)
            return 1
        current = CORPUS_PATH.read_text(encoding="utf-8")
        if current != new_content:
            print(
                f"DRIFT: {CORPUS_PATH} does not match the regenerated corpus "
                f"(oracle: {oracle}). Run `python scripts/gen_identifiers_corpus.py` "
                "to refresh it.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: corpus matches regenerated output (oracle: {oracle})")
        return 0

    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(new_content, encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {CORPUS_PATH} (oracle: {oracle})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
