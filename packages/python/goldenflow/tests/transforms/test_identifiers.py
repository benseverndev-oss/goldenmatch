import goldenflow
import polars as pl
from goldenflow.transforms import registry
from goldenflow.transforms.identifiers import (
    aba_validate,
    cc_format,
    cc_mask,
    cc_validate,
    ean_validate,
    ein_format,
    iban_format,
    iban_validate,
    imei_validate,
    isbn_normalize,
    isbn_validate,
    ssn_format,
    ssn_mask,
    swift_format,
    swift_validate,
    vat_format,
    vat_validate,
)

IDENTIFIER_TRANSFORM_NAMES = [
    "cc_validate",
    "cc_format",
    "cc_mask",
    "iban_validate",
    "iban_format",
    "isbn_validate",
    "isbn_normalize",
    "ean_validate",
    "swift_validate",
    "swift_format",
    "vat_validate",
    "vat_format",
    "aba_validate",
    "imei_validate",
]


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


# --- SWIFT/BIC (ISO 9362, structural only) identifiers ----------------------


def test_swift_validate_valid_and_invalid():
    s = pl.Series(
        "swift",
        [
            "DEUTDEFF",  # 8-char, valid
            "DEUTDEFF500",  # 11-char, valid
            "deutdeff",  # lowercase -> valid
            "DEUTDEFF5",  # bad length
            "DEUT1EFF",  # digit in institution
            "",  # empty
            None,  # null
        ],
    )
    result = swift_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is True
    assert result[3] is False
    assert result[4] is False
    assert result[5] is False
    assert result[6] is None


def test_swift_format_normalizes_and_nulls_invalid():
    s = pl.Series(
        "swift",
        [
            "deutdeff",  # -> DEUTDEFF
            "DEUTDEFF500",
            "DEUTDEFF5",  # invalid -> null
            None,
        ],
    )
    result = swift_format(s)
    assert result[0] == "DEUTDEFF"
    assert result[1] == "DEUTDEFF500"
    assert result[2] is None
    assert result[3] is None


# --- EU VAT identifiers (bounded scope: structural for all, checksum DE/IT) --


def test_vat_validate_valid_and_invalid():
    s = pl.Series(
        "vat",
        [
            "DE136695976",  # DE, checksum ok
            "de 136 695 976",  # DE, lowercase + spaced, checksum ok
            "IT00743110157",  # IT, checksum ok
            "NL004495445B01",  # NL, structural-only prefix
            "ATU13585627",  # AT, structural-only prefix
            "DE136695970",  # bad DE checksum
            "IT00743110150",  # bad IT checksum
            "ZZ123",  # unknown prefix
            "DE12345",  # bad length
            "",  # empty
            None,  # null
        ],
    )
    result = vat_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is True
    assert result[3] is True
    assert result[4] is True
    assert result[5] is False
    assert result[6] is False
    assert result[7] is False
    assert result[8] is False
    assert result[9] is False
    assert result[10] is None


def test_vat_format_normalizes_and_nulls_invalid():
    s = pl.Series(
        "vat",
        [
            "de 136 695 976",  # -> DE136695976
            "NL004495445B01",
            "DE136695970",  # bad checksum -> null
            "ZZ123",  # unknown prefix -> null
            None,
        ],
    )
    result = vat_format(s)
    assert result[0] == "DE136695976"
    assert result[1] == "NL004495445B01"
    assert result[2] is None
    assert result[3] is None
    assert result[4] is None


# --- ABA routing number (US bank routing transit number) --------------------


def test_aba_validate_valid_and_invalid():
    s = pl.Series(
        "aba",
        [
            "011000015",  # valid checksum
            "021000021",  # valid checksum
            "122105155",  # valid checksum
            "011000016",  # bad checksum
            "12345",  # wrong length
            "01100001a",  # non-digit
            "",  # empty
            None,  # null
        ],
    )
    result = aba_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is True
    assert result[3] is False
    assert result[4] is False
    assert result[5] is False
    assert result[6] is False
    assert result[7] is None


# --- IMEI (International Mobile Equipment Identity) --------------------------


def test_imei_validate_valid_and_invalid():
    s = pl.Series(
        "imei",
        [
            "490154203237518",  # valid Luhn
            "356938035643809",  # valid Luhn
            "490154203237519",  # bad Luhn
            "12345",  # wrong length
            "49015420323751a",  # non-digit
            "",  # empty
            None,  # null
        ],
    )
    result = imei_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is False
    assert result[3] is False
    assert result[4] is False
    assert result[5] is False
    assert result[6] is None


# --- Registration + zero-config posture --------------------------------------


def test_identifier_transforms_all_registered():
    registered = registry()
    for name in IDENTIFIER_TRANSFORM_NAMES:
        assert name in registered, f"{name} did not self-register"


def test_identifier_transforms_are_not_auto_applied():
    df = pl.DataFrame(
        {
            "card": ["4242 4242 4242 4242", "378282246310005", "4242424242424242"],
            "iban": ["GB82 WEST 1234 5698 7654 32", "DE89370400440532013000", None],
            "isbn": ["0-306-40615-2", "9780306406157", "0306406152"],
            "ean": ["4006381333931", "73513537", "036000291452"],
            "vat": ["DE136695976", "IT00743110157", "NL004495445B01"],
        }
    )
    result = goldenflow.transform_df(df)
    applied = {record.transform for record in result.manifest.records}
    leaked = applied.intersection(IDENTIFIER_TRANSFORM_NAMES)
    assert not leaked, (
        f"zero-config applied auto_apply=False identifier transforms: {leaked}"
    )
