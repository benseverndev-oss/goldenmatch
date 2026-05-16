import polars as pl
from goldenflow.transforms.numeric import (
    abs_value,
    clamp,
    comma_decimal,
    currency_strip,
    fill_zero,
    percentage_normalize,
    round_values,
    scientific_to_decimal,
    to_integer,
)


def _apply_expr(func, column: str, data: list) -> list:
    """Helper to apply an expr-mode transform to test data."""
    df = pl.DataFrame({column: data})
    expr = func(column)
    return df.select(expr.alias(column))[column].to_list()


def test_currency_strip():
    result = _apply_expr(
        currency_strip, "p", ["$1,234.56", "$99.99", "$0.50", "free"],
    )
    assert result[0] == 1234.56
    assert result[1] == 99.99
    assert result[2] == 0.50
    assert result[3] is None


def test_percentage_normalize():
    result = _apply_expr(
        percentage_normalize, "p", ["85%", "100%", "0.5%", "50"],
    )
    assert result[0] == 0.85
    assert result[1] == 1.0
    assert result[2] == 0.005


def test_round_values():
    s = pl.Series("v", [1.23456, 2.789, 3.1])
    result = round_values(s, n=2)
    assert result.to_list() == [1.23, 2.79, 3.1]


def test_clamp():
    s = pl.Series("v", [-5.0, 0.0, 50.0, 150.0])
    result = clamp(s, min_val=0.0, max_val=100.0)
    assert result.to_list() == [0.0, 0.0, 50.0, 100.0]


def test_to_integer():
    result = _apply_expr(
        to_integer, "v", ["42", "3.7", "100", "abc", None],
    )
    assert result[0] == 42
    assert result[1] == 3  # truncates decimal
    assert result[2] == 100
    assert result[3] is None
    assert result[4] is None


def test_abs_value():
    s = pl.Series("v", [-5.0, 3.0, -0.5, 0.0])
    result = abs_value(s)
    assert result.to_list() == [5.0, 3.0, 0.5, 0.0]


def test_fill_zero():
    s = pl.Series("v", [1.0, None, 3.0, None])
    result = fill_zero(s)
    assert result.to_list() == [1.0, 0.0, 3.0, 0.0]


def test_comma_decimal():
    s = pl.Series("v", ["1.234,56", "99,99", "1.000,00", "abc", None])
    result = comma_decimal(s)
    assert result[0] == 1234.56
    assert result[1] == 99.99
    assert result[2] == 1000.0  # comma present → European format
    assert result[3] is None
    assert result[4] is None


def test_comma_decimal_does_not_corrupt_us_format():
    """US-format decimals without commas should be parsed correctly, not corrupted."""
    s = pl.Series("v", ["1.5", "3.14", "0.99", "100"])
    result = comma_decimal(s)
    assert result[0] == 1.5
    assert result[1] == 3.14
    assert result[2] == 0.99
    assert result[3] == 100.0


def test_scientific_to_decimal():
    s = pl.Series("v", ["1.5e3", "2.0E-4", "3.14e0", "100", "abc", None])
    result = scientific_to_decimal(s)
    assert result[0] == 1500.0
    assert result[1] == 0.0002
    assert result[2] == 3.14
    assert result[3] == 100.0
    assert result[4] is None
    assert result[5] is None
