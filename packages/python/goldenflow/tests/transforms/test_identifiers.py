import polars as pl
from goldenflow.transforms.identifiers import (
    cc_format,
    cc_mask,
    cc_validate,
    ein_format,
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
