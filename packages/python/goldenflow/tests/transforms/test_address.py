import polars as pl
from goldenflow.transforms.address import (
    address_expand,
    address_standardize,
    country_standardize,
    split_address,
    state_abbreviate,
    state_expand,
    unit_normalize,
    zip_normalize,
)


def _apply_expr(func, column: str, data: list, *params) -> list:
    """Helper to apply an expr-mode transform to test data."""
    df = pl.DataFrame({column: data})
    expr = func(column, *params)
    return df.select(expr.alias(column))[column].to_list()


def test_address_standardize():
    result = _apply_expr(
        address_standardize, "a",
        ["123 Main Street", "456 Oak Avenue", "789 Elm Drive"],
    )
    assert result[0] == "123 Main St"
    assert result[1] == "456 Oak Ave"
    assert result[2] == "789 Elm Dr"


def test_address_expand():
    result = _apply_expr(
        address_expand, "a", ["123 Main St", "456 Oak Ave"],
    )
    assert result[0] == "123 Main Street"
    assert result[1] == "456 Oak Avenue"


def test_state_abbreviate():
    result = _apply_expr(
        state_abbreviate, "st", ["Pennsylvania", "California", "new york", "TX"],
    )
    assert result[0] == "PA"
    assert result[1] == "CA"
    assert result[2] == "NY"
    assert result[3] == "TX"


def test_state_expand():
    result = _apply_expr(
        state_expand, "st", ["PA", "CA", "NY"],
    )
    assert result[0] == "Pennsylvania"
    assert result[1] == "California"
    assert result[2] == "New York"


def test_zip_normalize():
    result = _apply_expr(
        zip_normalize, "z", ["19103", "9001", "10001-1234", "abcde"],
    )
    assert result[0] == "19103"
    assert result[1] == "09001"  # zero-padded
    assert result[2] == "10001"  # strip +4
    assert result[3] == "abcde"  # invalid preserved


def test_split_address():
    df = pl.DataFrame({"addr": ["123 Main St, Philadelphia, PA 19103"]})
    result = split_address(df, "addr")
    assert "street" in result.columns
    assert "city" in result.columns
    assert "state" in result.columns
    assert "zip" in result.columns


def test_country_standardize():
    s = pl.Series("c", [
        "United States",
        "united states of america",
        "USA",
        "UK",
        "United Kingdom",
        "Great Britain",
        "Canada",
        "Deutschland",
        None,
    ])
    result = country_standardize(s)
    assert result[0] == "US"
    assert result[1] == "US"
    assert result[2] == "US"
    assert result[3] == "GB"
    assert result[4] == "GB"
    assert result[5] == "GB"
    assert result[6] == "CA"
    assert result[7] == "DE"
    assert result[8] is None


def test_country_standardize_preserves_unknown():
    s = pl.Series("c", ["Narnia", "XY"])
    result = country_standardize(s)
    assert result[0] == "Narnia"
    assert result[1] == "XY"


def test_unit_normalize():
    s = pl.Series("a", [
        "Apt 5",
        "Apartment 5",
        "Suite 200",
        "Ste 200",
        "Unit 3B",
        "#12",
        None,
    ])
    result = unit_normalize(s)
    assert result[0] == "Unit 5"
    assert result[1] == "Unit 5"
    assert result[2] == "Ste 200"
    assert result[3] == "Ste 200"
    assert result[4] == "Unit 3B"
    assert result[5] == "Unit 12"
    assert result[6] is None
