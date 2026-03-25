import polars as pl

from goldenflow.transforms.names import (
    initial_expand,
    name_proper,
    split_name,
    split_name_reverse,
    strip_suffixes,
    strip_titles,
)


def test_split_name():
    df = pl.DataFrame({"name": ["John Smith", "Jane Marie Doe", "Madonna"]})
    result = split_name(df, "name")
    assert result["first_name"].to_list() == ["John", "Jane Marie", "Madonna"]
    assert result["last_name"].to_list() == ["Smith", "Doe", ""]


def test_split_name_reverse():
    df = pl.DataFrame({"name": ["Smith, John", "Doe, Jane Marie"]})
    result = split_name_reverse(df, "name")
    assert result["first_name"].to_list() == ["John", "Jane Marie"]
    assert result["last_name"].to_list() == ["Smith", "Doe"]


def test_strip_titles():
    s = pl.Series("n", ["Dr. James Wilson", "Mrs. Jane Smith", "Mr. Bob Jones Jr."])
    result = strip_titles(s)
    assert result[0] == "James Wilson"
    assert result[1] == "Jane Smith"
    assert result[2] == "Bob Jones Jr."


def test_strip_suffixes():
    s = pl.Series("n", ["James Wilson MD", "Jane Smith PhD", "Bob Jones Esq"])
    result = strip_suffixes(s)
    assert result[0] == "James Wilson"
    assert result[1] == "Jane Smith"
    assert result[2] == "Bob Jones"


def test_name_proper():
    s = pl.Series("n", ["mcdonald", "o'brien", "van der berg", "SMITH"])
    result = name_proper(s)
    assert result[0] == "McDonald"
    assert result[1] == "O'Brien"


def test_initial_expand():
    s = pl.Series("n", ["J. Smith", "John Smith", "R. J. Wilson"])
    result, flagged = initial_expand(s)
    assert result[0] == "J. Smith"  # unchanged
    assert result[1] == "John Smith"
    assert 0 in flagged  # row 0 flagged for review
    assert 2 in flagged
