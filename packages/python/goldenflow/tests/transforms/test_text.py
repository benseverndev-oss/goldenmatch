import polars as pl

from goldenflow.transforms.text import (
    collapse_whitespace,
    lowercase,
    normalize_quotes,
    normalize_unicode,
    remove_punctuation,
    strip,
    title_case,
    truncate,
    uppercase,
)


def _apply_expr(func, column: str, data: list[str]) -> list[str]:
    """Helper to apply an expr-mode transform to test data."""
    df = pl.DataFrame({column: data})
    expr = func(column)
    return df.select(expr.alias(column))[column].to_list()


def test_strip():
    result = _apply_expr(strip, "a", ["  hello  ", "world ", " hi"])
    assert result == ["hello", "world", "hi"]


def test_lowercase():
    result = _apply_expr(lowercase, "a", ["HELLO", "World"])
    assert result == ["hello", "world"]


def test_uppercase():
    result = _apply_expr(uppercase, "a", ["hello", "World"])
    assert result == ["HELLO", "WORLD"]


def test_title_case():
    result = _apply_expr(title_case, "a", ["john smith", "JANE DOE"])
    assert result == ["John Smith", "Jane Doe"]


def test_normalize_unicode():
    s = pl.Series("a", ["\u00e9", "caf\u00e9", "na\u00efve"])
    result = normalize_unicode(s)
    assert result.to_list() == ["e", "cafe", "naive"]


def test_remove_punctuation():
    result = _apply_expr(remove_punctuation, "a", ["hello!", "test@123", "a-b_c"])
    assert all(c.isalnum() or c.isspace() for val in result for c in val)


def test_collapse_whitespace():
    result = _apply_expr(collapse_whitespace, "a", ["hello   world", "  a  b  "])
    assert result == ["hello world", " a b "]


def test_truncate():
    s = pl.Series("a", ["hello world", "hi", "a very long string"])
    result = truncate(s, n=5)
    assert result.to_list() == ["hello", "hi", "a ver"]


def test_normalize_quotes():
    result = _apply_expr(normalize_quotes, "a", [
        '\u201cHello\u201d',      # "Hello"
        '\u2018world\u2019',      # 'world'
        'no quotes here',
    ])
    assert result[0] == '"Hello"'
    assert result[1] == "'world'"
    assert result[2] == 'no quotes here'
