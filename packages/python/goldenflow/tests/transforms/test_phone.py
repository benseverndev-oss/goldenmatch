import polars as pl

from goldenflow.transforms.phone import (
    phone_country_code,
    phone_digits,
    phone_e164,
    phone_national,
    phone_validate,
)


def test_phone_e164():
    s = pl.Series("ph", ["(555) 123-4567", "555.987.6543", "+1-555-456-7890", "invalid"])
    result = phone_e164(s)
    assert result[0] == "+15551234567"
    assert result[1] == "+15559876543"
    assert result[2] == "+15554567890"
    assert result[3] == "invalid"  # preserved on failure


def test_phone_national():
    s = pl.Series("ph", ["+15551234567", "(555) 987-6543"])
    result = phone_national(s)
    assert result[0] == "(555) 123-4567"
    assert result[1] == "(555) 987-6543"


def test_phone_digits():
    s = pl.Series("ph", ["(555) 123-4567", "+1-555-987-6543"])
    result = phone_digits(s)
    assert result[0] == "5551234567"
    assert result[1] == "15559876543"


def test_phone_validate():
    s = pl.Series("ph", ["+15551234567", "invalid", "123"])
    result = phone_validate(s)
    assert result[0] is True
    assert result[1] is False
    assert result[2] is False


def test_phone_country_code():
    s = pl.Series("ph", ["+15551234567", "+44201234567", "+61412345678", "invalid", None])
    result = phone_country_code(s)
    assert result[0] == 1       # US
    assert result[1] == 44      # UK
    assert result[2] == 61      # Australia
    assert result[3] is None
    assert result[4] is None
