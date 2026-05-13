import polars as pl
from goldenflow.transforms.categorical import (
    boolean_normalize,
    category_standardize,
    gender_standardize,
    null_standardize,
)


def test_boolean_normalize():
    s = pl.Series("b", ["Yes", "Y", "1", "True", "true", "No", "N", "0", "false"])
    result = boolean_normalize(s)
    assert result[0] is True
    assert result[4] is True
    assert result[5] is False
    assert result[8] is False


def test_gender_standardize():
    s = pl.Series("g", ["Male", "male", "M", "m", "Female", "female", "F", "f"])
    result = gender_standardize(s)
    assert result[0] == "M"
    assert result[3] == "M"
    assert result[4] == "F"
    assert result[7] == "F"


def test_null_standardize():
    s = pl.Series("n", ["N/A", "NULL", "none", "", "  ", "actual value", "null", "NA"])
    result = null_standardize(s)
    assert result[0] is None
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None
    assert result[4] is None
    assert result[5] == "actual value"


def test_category_standardize():
    mapping = {"US": ["USA", "United States", "U.S.A.", "US"]}
    s = pl.Series("c", ["USA", "United States", "U.S.A.", "Canada"])
    result = category_standardize(s, mapping=mapping)
    assert result[0] == "US"
    assert result[1] == "US"
    assert result[2] == "US"
    assert result[3] == "Canada"  # no mapping, preserved


def test_category_from_file(tmp_path):
    from goldenflow.transforms.categorical import category_from_file

    lookup = tmp_path / "countries.csv"
    lookup.write_text("variant,canonical\nUSA,US\nUnited States,US\nU.S.A.,US\n")
    s = pl.Series("c", ["USA", "United States", "Canada"])
    result = category_from_file(s, lookup_path=str(lookup))
    assert result[0] == "US"
    assert result[1] == "US"
    assert result[2] == "Canada"
