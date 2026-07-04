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
from goldenflow.transforms.address import (  # noqa: E402
    _address_expand_py,
    _address_standardize_py,
    _country_standardize_py,
    _state_abbreviate_py,
    _state_expand_py,
    _unit_normalize_py,
    _zip_normalize_py,
)
from goldenflow.transforms.categorical import (  # noqa: E402
    _boolean_normalize_py,
    _category_normalize_key_py,
    _gender_standardize_py,
    _null_standardize_py,
)
from goldenflow.transforms.email import (  # noqa: E402
    _email_extract_domain_py,
    _email_lowercase_py,
    _email_normalize_py,
    _email_validate_py,
)
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
from goldenflow.transforms.names import (  # noqa: E402
    _has_initial_py,
    _name_proper_py,
    _name_script_py,
    _name_transliterate_py,
    _nickname_standardize_py,
    _strip_suffixes_py,
    _strip_titles_py,
)
from goldenflow.transforms.numeric import (  # noqa: E402
    _comma_decimal_py,
    _currency_strip_py,
    _percentage_normalize_py,
    _scientific_to_decimal_py,
    _to_integer_py,
)
from goldenflow.transforms.url import (  # noqa: E402
    _url_extract_domain_py,
    _url_normalize_py,
)

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
    # --- name_script ---
    ("name_script", "Smith"),  # Latin
    ("name_script", "José"),  # Latin (with diacritic)
    ("name_script", "Иван"),  # Cyrillic
    ("name_script", "Ολγα"),  # Greek
    ("name_script", "张伟"),  # Han
    ("name_script", "田中"),  # Han (Kanji)
    ("name_script", "ひらがな"),  # Hiragana
    ("name_script", "カタカナ"),  # Katakana
    ("name_script", "홍길동"),  # Hangul
    ("name_script", "محمد"),  # Arabic
    ("name_script", "דָּוִד"),  # Hebrew
    ("name_script", "राम"),  # Devanagari
    ("name_script", "123"),  # digits only -> Common
    ("name_script", ""),  # empty -> Unknown
    ("name_script", None),  # null
    # --- strip_titles (leading personal titles; auto_apply) ---
    ("strip_titles", "Dr. Smith"),  # title + dot
    ("strip_titles", "Mr Smith"),  # title, no dot
    ("strip_titles", "Mrs. Jane Doe"),  # keeps the rest
    ("strip_titles", "Prof. Alan Turing"),
    ("strip_titles", "Sra Garcia"),  # Sra (not Sr)
    ("strip_titles", "Miss Ellie"),
    ("strip_titles", "Dr.   Smith"),  # multiple spaces after title
    ("strip_titles", "Missy"),  # NOT the title "Miss"
    ("strip_titles", "  John Smith  "),  # no title -> still trimmed
    ("strip_titles", ""),  # empty
    ("strip_titles", None),  # null
    # --- strip_suffixes (trailing professional suffixes) ---
    ("strip_suffixes", "John Smith Jr"),
    ("strip_suffixes", "John Smith Jr."),  # optional dot
    ("strip_suffixes", "Jane Doe MD"),
    ("strip_suffixes", "Bob III"),  # III beats II via the anchor
    ("strip_suffixes", "Bob II"),
    ("strip_suffixes", "Alice Esq."),
    ("strip_suffixes", "Sam RN"),
    ("strip_suffixes", "Robert"),  # no suffix
    ("strip_suffixes", "John DODO"),  # "DO" not a standalone trailing suffix here
    ("strip_suffixes", None),  # null
    # --- name_proper (title-case + Mc/O' fixups) ---
    ("name_proper", "john smith"),
    ("name_proper", "JOHN SMITH"),
    ("name_proper", "mcdonald"),  # -> McDonald
    ("name_proper", "old mcdonald"),  # -> Old McDonald
    ("name_proper", "o'brien"),  # -> O'Brien
    ("name_proper", "d'angelo"),  # -> D'Angelo
    ("name_proper", "macdonald"),  # Mac != Mc, not fixed up
    ("name_proper", ""),  # empty
    ("name_proper", None),  # null
    # --- nickname_standardize (map lookup; unknown passes through unchanged) ---
    ("nickname_standardize", "Bob"),  # -> Robert
    ("nickname_standardize", "  bob  "),  # trimmed key, unknown-miss returns original
    ("nickname_standardize", "JIM"),  # -> James
    ("nickname_standardize", "patty"),  # -> Patricia
    ("nickname_standardize", "pat"),  # -> Patrick
    ("nickname_standardize", "Xavier"),  # unknown -> unchanged
    ("nickname_standardize", None),  # null
    # --- has_initial (the initial_expand flag predicate) ---
    ("has_initial", "John Q. Public"),  # middle initial -> True
    ("has_initial", "J. Smith"),  # leading initial -> True
    ("has_initial", "John Smith"),  # no initial -> False
    ("has_initial", "J.Smith"),  # no whitespace after dot -> False
    ("has_initial", ""),  # empty -> False
    ("has_initial", None),  # null
    # --- email_lowercase ---
    ("email_lowercase", " John@X.COM "),  # leading/trailing whitespace
    ("email_lowercase", "A@B.com"),  # already lower domain
    ("email_lowercase", "ADMIN@EXAMPLE.COM"),  # all-caps
    ("email_lowercase", ""),  # empty
    ("email_lowercase", None),  # null
    # --- email_normalize ---
    ("email_normalize", "John.Doe+tag@Gmail.com"),  # gmail dot-strip + tag-strip
    ("email_normalize", "a+b@x.com"),  # non-gmail: tag stripped, dots kept (none here)
    ("email_normalize", "notanemail"),  # no '@' -> preserved verbatim
    ("email_normalize", "A@B.com"),  # simple lowercase
    ("email_normalize", "j.o.h.n@googlemail.com"),  # googlemail dot-strip
    ("email_normalize", "user+spam@example.com"),  # non-gmail tag-strip only
    ("email_normalize", ""),  # empty -> preserved
    ("email_normalize", "   "),  # whitespace-only -> preserved verbatim
    ("email_normalize", None),  # null
    # --- email_extract_domain ---
    ("email_extract_domain", "x@Foo.COM"),  # mixed-case domain -> lowercased
    ("email_extract_domain", "noat"),  # no '@' -> None
    ("email_extract_domain", "trailing@"),  # nothing after '@' -> None
    ("email_extract_domain", "admin@sub.domain.org"),  # multi-label domain
    ("email_extract_domain", "a@b@c.com"),  # multiple '@' -> domain after LAST
    ("email_extract_domain", ""),  # empty -> None
    ("email_extract_domain", None),  # null
    # --- email_validate ---
    ("email_validate", "a@b.co"),  # valid
    ("email_validate", "valid@example.com"),  # valid
    ("email_validate", "also.valid+tag@sub.example.co.uk"),  # valid, multi-label
    ("email_validate", "a@b"),  # no dot in domain -> false
    ("email_validate", "a b@c.com"),  # whitespace in local -> false
    ("email_validate", "a@@b.com"),  # two '@' -> false
    ("email_validate", "@no-local.com"),  # empty local -> false
    ("email_validate", "no-domain@"),  # empty domain -> false
    ("email_validate", ""),  # empty -> false
    ("email_validate", "   "),  # whitespace-only -> false
    ("email_validate", None),  # null
    # --- url_normalize ---
    ("url_normalize", "Example.COM/Path/"),  # no scheme -> prepend https, lowercase domain
    ("url_normalize", "http://X.com/"),  # trailing slash, path == "/" -> strip one
    ("url_normalize", "https://a.com"),  # no trailing slash -> unchanged
    ("url_normalize", "https://a.com/x/"),  # path beyond root -> strip all trailing slashes
    ("url_normalize", "https://a.com/x//"),  # multiple trailing slashes -> strip all
    ("url_normalize", "HTTPS://Foo.com"),  # uppercase scheme -> lowercased
    ("url_normalize", "HtTp://Foo.com"),  # mixed-case scheme -> lowercased
    ("url_normalize", "EXAMPLE.com"),  # no scheme, no path -> https prepended
    ("url_normalize", " example.com "),  # leading/trailing whitespace -> trimmed
    ("url_normalize", "http://sub.Domain.ORG/Path/More"),  # multi-label domain, mixed-case path
    ("url_normalize", ""),  # empty -> None
    ("url_normalize", "   "),  # whitespace-only -> None
    ("url_normalize", None),  # null
    # --- url_extract_domain ---
    ("url_extract_domain", "https://Foo.com/x"),  # mixed-case domain -> lowercased
    ("url_extract_domain", "bar.com"),  # no scheme -> domain as-is (lowercased)
    ("url_extract_domain", "http://sub.domain.org/path/more"),  # multi-label domain
    ("url_extract_domain", "HTTPS://EXAMPLE.COM"),  # all-caps, no path
    ("url_extract_domain", ""),  # empty -> None
    ("url_extract_domain", "   "),  # whitespace-only -> None
    ("url_extract_domain", None),  # null
    # --- address_standardize (full street suffix -> abbreviation) ---
    ("address_standardize", "123 Main Street"),  # Street -> St
    ("address_standardize", "1 Park Avenue"),  # Avenue -> Ave
    ("address_standardize", "5 Sunset Boulevard"),  # Boulevard -> Blvd
    ("address_standardize", "10 elm STREET"),  # case-insensitive, canonical repl
    ("address_standardize", "Streetsboro Road"),  # word-boundary: Streets NOT abbrev'd
    ("address_standardize", "42 Nowhere"),  # no suffix -> unchanged
    ("address_standardize", ""),  # empty
    ("address_standardize", None),  # null
    # --- address_expand (abbreviation -> full street suffix) ---
    ("address_expand", "123 Main St"),  # St -> Street
    ("address_expand", "1 Park Ave"),  # Ave -> Avenue
    ("address_expand", "1 Park Ste"),  # St inside Ste is not a word-bound match
    ("address_expand", "5 sunset blvd"),  # case-insensitive
    ("address_expand", ""),  # empty
    ("address_expand", None),  # null
    # --- state_abbreviate (full name / valid abbr / else original) ---
    ("state_abbreviate", "California"),  # full -> CA
    ("state_abbreviate", "new york"),  # case-insensitive full -> NY
    ("state_abbreviate", "North Carolina"),  # multi-word full -> NC
    ("state_abbreviate", "ca"),  # valid 2-letter -> uppercased
    ("state_abbreviate", "Ny"),  # valid 2-letter mixed -> NY
    ("state_abbreviate", "DC"),  # DC is included
    ("state_abbreviate", "  Freedonia  "),  # unmatched -> ORIGINAL (unstripped)
    ("state_abbreviate", "XZ"),  # 2-char non-abbr -> original
    ("state_abbreviate", ""),  # empty -> original ("")
    ("state_abbreviate", None),  # null
    # --- state_expand (abbr -> full name / else original) ---
    ("state_expand", "CA"),  # -> California
    ("state_expand", "ny"),  # case-insensitive -> New York
    ("state_expand", "  il  "),  # stripped lookup -> Illinois
    ("state_expand", "DC"),  # -> District Of Columbia
    ("state_expand", "  ZZ  "),  # unmatched -> ORIGINAL (unstripped)
    ("state_expand", ""),  # empty -> original ("")
    ("state_expand", None),  # null
    # --- zip_normalize (auto_apply=True; strip +4, zero-pad, preserve invalid) ---
    ("zip_normalize", "12345"),  # already 5
    ("zip_normalize", "12345-6789"),  # strip +4
    ("zip_normalize", "  90210  "),  # trimmed
    ("zip_normalize", "210"),  # zero-pad short all-digit
    ("zip_normalize", "123456"),  # >5 all-digit -> as-is
    ("zip_normalize", "SW1A"),  # non-numeric passthrough
    ("zip_normalize", "SW1A-1AA"),  # base segment before '-'
    ("zip_normalize", ""),  # empty -> unchanged
    ("zip_normalize", None),  # null
    # --- country_standardize (name/alias -> ISO alpha-2; else original) ---
    ("country_standardize", "United States"),  # -> US
    ("country_standardize", "usa"),  # alias -> US
    ("country_standardize", "  England  "),  # trimmed alias -> GB
    ("country_standardize", "Deutschland"),  # -> DE
    ("country_standardize", "CA"),  # 2-letter alias -> CA
    ("country_standardize", "  Atlantis  "),  # unknown -> ORIGINAL (unstripped)
    ("country_standardize", ""),  # empty -> original ("")
    ("country_standardize", None),  # null
    # --- unit_normalize (anchored prefix subs, in order) ---
    ("unit_normalize", "Apt 4"),  # Apt -> Unit
    ("unit_normalize", "Apt. 4"),  # optional dot
    ("unit_normalize", "Apartment 12B"),  # Apartment -> Unit
    ("unit_normalize", "Suite 200"),  # Suite -> Ste
    ("unit_normalize", "Ste. 200"),  # Ste + dot -> Ste
    ("unit_normalize", "#5"),  # # -> Unit (no space)
    ("unit_normalize", "# 5"),  # # + space -> Unit
    ("unit_normalize", "APT 9"),  # case-insensitive
    ("unit_normalize", "Apt.5"),  # no whitespace after -> no match
    ("unit_normalize", "Aptos"),  # Apt prefix but no boundary -> unchanged
    ("unit_normalize", "  Building C  "),  # no designator -> trimmed
    ("unit_normalize", ""),  # empty
    ("unit_normalize", None),  # null
    # --- currency_strip (string->float; VALUE parity, not repr) ---
    ("currency_strip", "$1,234.56"),  # strip $ and comma
    ("currency_strip", "-$42.00"),  # negative, dollar sign
    ("currency_strip", "USD 100"),  # strip letters + space
    ("currency_strip", "0.50"),  # plain decimal
    ("currency_strip", "free"),  # no numeric chars -> null
    ("currency_strip", ""),  # empty -> null
    ("currency_strip", None),  # null
    # --- percentage_normalize (string->float) ---
    ("percentage_normalize", "85%"),
    ("percentage_normalize", "100%"),
    ("percentage_normalize", "0.5%"),
    ("percentage_normalize", " 12.5 % "),  # whitespace around number and %
    ("percentage_normalize", "50"),  # no % sign
    ("percentage_normalize", "abc%"),  # non-numeric -> null
    ("percentage_normalize", ""),  # empty -> null
    ("percentage_normalize", None),  # null
    # --- to_integer (string->int, truncating) ---
    ("to_integer", "42"),
    ("to_integer", "3.7"),  # truncates
    ("to_integer", "-3.7"),  # truncates toward zero
    ("to_integer", "100"),
    ("to_integer", "abc"),  # non-numeric -> null
    ("to_integer", ""),  # empty -> null
    ("to_integer", None),  # null
    # --- comma_decimal (string->float, EU format) ---
    ("comma_decimal", "1.234,56"),  # EU format
    ("comma_decimal", "99,99"),  # EU format, no thousands sep
    ("comma_decimal", "1.000,00"),  # EU format with thousands sep
    ("comma_decimal", "1.5"),  # US format (no comma) -> parsed as-is
    ("comma_decimal", "100"),  # plain integer
    ("comma_decimal", "abc"),  # non-numeric -> null
    ("comma_decimal", ""),  # empty -> null
    ("comma_decimal", None),  # null
    # --- scientific_to_decimal (string->float) ---
    ("scientific_to_decimal", "1.5e3"),
    ("scientific_to_decimal", "2.0E-4"),
    ("scientific_to_decimal", "3.14e0"),
    ("scientific_to_decimal", "100"),  # plain number, no exponent
    ("scientific_to_decimal", "abc"),  # non-numeric -> null
    ("scientific_to_decimal", ""),  # empty -> null
    ("scientific_to_decimal", None),  # null
    # --- boolean_normalize (string->bool) ---
    ("boolean_normalize", "Yes"),
    ("boolean_normalize", "Y"),
    ("boolean_normalize", "1"),
    ("boolean_normalize", "True"),
    ("boolean_normalize", " true "),  # whitespace around a recognized token
    ("boolean_normalize", "T"),
    ("boolean_normalize", "No"),
    ("boolean_normalize", "N"),
    ("boolean_normalize", "0"),
    ("boolean_normalize", "False"),
    ("boolean_normalize", "f"),
    ("boolean_normalize", "maybe"),  # unrecognized -> null
    ("boolean_normalize", ""),  # empty -> null
    ("boolean_normalize", None),  # null
    # --- gender_standardize (string->string, passthrough on no match) ---
    ("gender_standardize", "Male"),
    ("gender_standardize", "male"),
    ("gender_standardize", "M"),
    ("gender_standardize", "m"),
    ("gender_standardize", "Female"),
    ("gender_standardize", "female"),
    ("gender_standardize", "F"),
    ("gender_standardize", "f"),
    ("gender_standardize", "Nonbinary"),  # no match -> passthrough UNCHANGED
    ("gender_standardize", ""),  # empty -> passthrough (empty string)
    ("gender_standardize", None),  # null
    # --- null_standardize (string->string|null) ---
    ("null_standardize", "N/A"),
    ("null_standardize", "NULL"),
    ("null_standardize", "none"),
    ("null_standardize", ""),
    ("null_standardize", "  "),  # trims to empty -> null
    ("null_standardize", "null"),
    ("null_standardize", "NA"),
    ("null_standardize", "nil"),
    ("null_standardize", "nan"),
    ("null_standardize", "-"),
    ("null_standardize", "actual value"),  # no match -> passthrough UNCHANGED
    ("null_standardize", None),  # null
    # --- category_normalize_key (string->string, shared key-derivation for
    # the mapping-based transforms category_standardize/category_from_file --
    # always trim+lowercase, never fails) ---
    ("category_normalize_key", "  Yes  "),
    ("category_normalize_key", "USA"),
    ("category_normalize_key", "MiXeD Case"),
    ("category_normalize_key", ""),
    ("category_normalize_key", None),  # null
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
    "name_script": _name_script_py,
    "strip_titles": _strip_titles_py,
    "strip_suffixes": _strip_suffixes_py,
    "name_proper": _name_proper_py,
    "nickname_standardize": _nickname_standardize_py,
    "has_initial": _has_initial_py,
    "email_lowercase": _email_lowercase_py,
    "email_normalize": _email_normalize_py,
    "email_extract_domain": _email_extract_domain_py,
    "email_validate": _email_validate_py,
    "url_normalize": _url_normalize_py,
    "url_extract_domain": _url_extract_domain_py,
    "address_standardize": _address_standardize_py,
    "address_expand": _address_expand_py,
    "state_abbreviate": _state_abbreviate_py,
    "state_expand": _state_expand_py,
    "zip_normalize": _zip_normalize_py,
    "country_standardize": _country_standardize_py,
    "unit_normalize": _unit_normalize_py,
    "currency_strip": _currency_strip_py,
    "percentage_normalize": _percentage_normalize_py,
    "to_integer": _to_integer_py,
    "comma_decimal": _comma_decimal_py,
    "scientific_to_decimal": _scientific_to_decimal_py,
    "boolean_normalize": _boolean_normalize_py,
    "gender_standardize": _gender_standardize_py,
    "null_standardize": _null_standardize_py,
    "category_normalize_key": lambda v: None if v is None else _category_normalize_key_py(v),
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
    "name_script": "name_script_arrow",
    "strip_titles": "strip_titles_arrow",
    "strip_suffixes": "strip_suffixes_arrow",
    "name_proper": "name_proper_arrow",
    "nickname_standardize": "nickname_standardize_arrow",
    "has_initial": "has_initial_arrow",
    "email_lowercase": "email_lowercase_arrow",
    "email_normalize": "email_normalize_arrow",
    "email_extract_domain": "email_extract_domain_arrow",
    "email_validate": "email_validate_arrow",
    "url_normalize": "url_normalize_arrow",
    "url_extract_domain": "url_extract_domain_arrow",
    "address_standardize": "address_standardize_arrow",
    "address_expand": "address_expand_arrow",
    "state_abbreviate": "state_abbreviate_arrow",
    "state_expand": "state_expand_arrow",
    "zip_normalize": "zip_normalize_arrow",
    "country_standardize": "country_standardize_arrow",
    "unit_normalize": "unit_normalize_arrow",
    "currency_strip": "currency_strip_arrow",
    "percentage_normalize": "percentage_normalize_arrow",
    "to_integer": "to_integer_arrow",
    "comma_decimal": "comma_decimal_arrow",
    "scientific_to_decimal": "scientific_to_decimal_arrow",
    "boolean_normalize": "boolean_normalize_arrow",
    "gender_standardize": "gender_standardize_arrow",
    "null_standardize": "null_standardize_arrow",
    "category_normalize_key": "category_normalize_key_arrow",
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
