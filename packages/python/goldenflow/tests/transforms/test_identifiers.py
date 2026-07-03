import polars as pl
from goldenflow.transforms.identifiers import (
    cc_format,
    cc_mask,
    cc_validate,
    ean_validate,
    ein_format,
    iban_format,
    iban_validate,
    isbn_normalize,
    isbn_validate,
    ssn_format,
    ssn_mask,
)


def test_ssn_format_normalizes_various_formats():
    s = pl.Series("ssn", ["123456789", "123-45-6789", "123 45 6789", None])
    result = ssn_format(s)
    assert result[0] == "123-45-6789"
    assert result[1] == "123-45-6789"
    assert result[2] == "123-45-6789"
    assert result[3] is None


def test_ssn_format_preserves_invalid():
    s = pl.Series("ssn", ["12345", "abcdefghi", ""])
    result = ssn_format(s)
    assert result[0] == "12345"  # not 9 digits, preserved
    assert result[1] == "abcdefghi"
    assert result[2] == ""


def test_ssn_mask():
    s = pl.Series("ssn", ["123-45-6789", "123456789", "987-65-4321", None])
    result = ssn_mask(s)
    assert result[0] == "***-**-6789"
    assert result[1] == "***-**-6789"
    assert result[2] == "***-**-4321"
    assert result[3] is None


def test_ssn_mask_preserves_invalid():
    s = pl.Series("ssn", ["12345", "invalid"])
    result = ssn_mask(s)
    assert result[0] == "12345"
    assert result[1] == "invalid"


def test_ein_format():
    s = pl.Series("ein", ["123456789", "12-3456789", "12 3456789", None])
    result = ein_format(s)
    assert result[0] == "12-3456789"
    assert result[1] == "12-3456789"
    assert result[2] == "12-3456789"
    assert result[3] is None


def test_ein_format_preserves_invalid():
    s = pl.Series("ein", ["12345", "abcdefghi"])
    result = ein_format(s)
    assert result[0] == "12345"
    assert result[1] == "abcdefghi"


# --- Payment-card (Luhn) identifiers ----------------------------------------


def test_cc_validate_valid_and_invalid():
    s = pl.Series(
        "cc",
        [
            "4242 4242 4242 4242",  # Visa test
            "378282246310005",  # Amex
            "4242424242424241",  # bad checksum
            "1234",  # too short
            None,
        ],
    )
    result = cc_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is False
    assert result[3] is False
    assert result[4] is None


def test_cc_format_groups_by_brand():
    s = pl.Series(
        "cc",
        [
            "4242424242424242",  # 16-digit -> 4-4-4-4
            "378282246310005",  # Amex -> 4-6-5
            "4242424242424241",  # invalid -> null
            None,
        ],
    )
    result = cc_format(s)
    assert result[0] == "4242 4242 4242 4242"
    assert result[1] == "3782 822463 10005"
    assert result[2] is None
    assert result[3] is None


def test_cc_mask_stars_plus_last4():
    s = pl.Series(
        "cc",
        [
            "4242424242424242",
            "bogus",
            "1234",
            None,
        ],
    )
    result = cc_mask(s)
    assert result[0] == "************4242"
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None


# --- IBAN (ISO 7064 mod-97) identifiers -------------------------------------


def test_iban_validate_valid_and_invalid():
    s = pl.Series(
        "iban",
        [
            "GB82 WEST 1234 5698 7654 32",  # UK, spaced
            "DE89370400440532013000",  # Germany
            "FR1420041010050500013M02606",  # France, alnum BBAN
            "GB82WEST12345698765433",  # bad check digits
            "XX00",  # too short
            "",  # empty
            None,
        ],
    )
    result = iban_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is True
    assert result[3] is False
    assert result[4] is False
    assert result[5] is False
    assert result[6] is None


def test_iban_format_groups_in_4s():
    s = pl.Series(
        "iban",
        [
            "DE89370400440532013000",
            "GB82WEST12345698765433",  # invalid -> null
            None,
        ],
    )
    result = iban_format(s)
    assert result[0] == "DE89 3704 0044 0532 0130 00"
    assert result[1] is None
    assert result[2] is None


# --- ISBN (10/13 checksum) identifiers ---------------------------------------


def test_isbn_validate_valid_and_invalid():
    s = pl.Series(
        "isbn",
        [
            "0-306-40615-2",  # ISBN-10, dashed
            "0306406152",  # ISBN-10, bare
            "0-8044-2957-X",  # ISBN-10, X check digit
            "978-0-306-40615-7",  # ISBN-13, dashed
            "9780306406157",  # ISBN-13, bare
            "0306406153",  # ISBN-10, bad check digit
            "12345",  # wrong length
            "",  # empty
            None,
        ],
    )
    result = isbn_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is True
    assert result[3] is True
    assert result[4] is True
    assert result[5] is False
    assert result[6] is False
    assert result[7] is False
    assert result[8] is None


def test_isbn_normalize_to_isbn13():
    s = pl.Series(
        "isbn",
        [
            "0306406152",  # ISBN-10 -> ISBN-13
            "0-306-40615-2",  # ISBN-10, dashed -> ISBN-13
            "0-8044-2957-X",  # ISBN-10, X check -> ISBN-13
            "978-0-306-40615-7",  # ISBN-13 -> canonical digits
            "0306406153",  # invalid -> null
            None,
        ],
    )
    result = isbn_normalize(s)
    assert result[0] == "9780306406157"
    assert result[1] == "9780306406157"
    assert result[2] == "9780804429573"
    assert result[3] == "9780306406157"
    assert result[4] is None
    assert result[5] is None


# --- EAN/UPC (GTIN mod-10) identifiers --------------------------------------


def test_ean_validate_valid_and_invalid():
    s = pl.Series(
        "ean",
        [
            "4006381333931",  # EAN-13
            "73513537",  # EAN-8
            "036000291452",  # UPC-A
            "4006381333930",  # bad check digit
            "12345",  # wrong length
            "40063813339a1",  # non-digit
            "",  # empty
            None,  # null
        ],
    )
    result = ean_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is True
    assert result[3] is False
    assert result[4] is False
    assert result[5] is False
    assert result[6] is False
    assert result[7] is None
