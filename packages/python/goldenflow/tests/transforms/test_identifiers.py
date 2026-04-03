import polars as pl

from goldenflow.transforms.identifiers import ein_format, ssn_format, ssn_mask


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
