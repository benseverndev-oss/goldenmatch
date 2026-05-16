import polars as pl
from goldenflow.transforms.text import (
    collapse_whitespace,
    extract_numbers,
    fix_mojibake,
    lowercase,
    normalize_line_endings,
    normalize_quotes,
    normalize_unicode,
    pad_left,
    pad_right,
    remove_digits,
    remove_emojis,
    remove_html_tags,
    remove_punctuation,
    remove_urls,
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


def test_remove_html_tags():
    s = pl.Series("a", ["<p>Hello</p>", "<b>bold</b> text", "no tags", None])
    result = remove_html_tags(s)
    assert result[0] == "Hello"
    assert result[1] == "bold text"
    assert result[2] == "no tags"
    assert result[3] is None


def test_remove_html_tags_nested():
    s = pl.Series("a", ["<div><span>nested</span></div>", "<a href='url'>link</a>"])
    result = remove_html_tags(s)
    assert result[0] == "nested"
    assert result[1] == "link"


def test_remove_urls():
    s = pl.Series("a", [
        "visit https://example.com for info",
        "go to http://test.org/path?q=1 now",
        "no url here",
        None,
    ])
    result = remove_urls(s)
    assert result[0] == "visit  for info"
    assert result[1] == "go to  now"
    assert result[2] == "no url here"
    assert result[3] is None


def test_remove_digits():
    result = _apply_expr(remove_digits, "a", ["abc123", "hello", "99 bottles"])
    assert result == ["abc", "hello", " bottles"]


def test_pad_left():
    s = pl.Series("a", ["42", "7", "123", None])
    result = pad_left(s, width=5, char="0")
    assert result[0] == "00042"
    assert result[1] == "00007"
    assert result[2] == "00123"
    assert result[3] is None


def test_pad_right():
    s = pl.Series("a", ["AB", "X", "ABCDE", None])
    result = pad_right(s, width=5, char=" ")
    assert result[0] == "AB   "
    assert result[1] == "X    "
    assert result[2] == "ABCDE"
    assert result[3] is None


def test_remove_emojis():
    result = _apply_expr(remove_emojis, "a", [
        "Hello \U0001f600 World", "No emojis", "\U0001f44d Great \U0001f44d", None,
    ])
    assert result[0] == "Hello  World"
    assert result[1] == "No emojis"
    assert result[2] == " Great "
    assert result[3] is None


def test_fix_mojibake():
    s = pl.Series("a", [
        "caf\u00c3\u00a9",           # cafÃ© (UTF-8 bytes of é decoded as Latin-1)
        "it\u00e2\u0080\u0099s",     # itâ€™s (right single quote mojibake)
        "normal text",
        None,
    ])
    result = fix_mojibake(s)
    assert result[0] == "caf\u00e9"
    assert result[1] == "it\u2019s"
    assert result[2] == "normal text"
    assert result[3] is None


def test_normalize_line_endings():
    result = _apply_expr(normalize_line_endings, "a", [
        "hello\r\nworld", "foo\rbar", "no change\n", None,
    ])
    assert result[0] == "hello\nworld"
    assert result[1] == "foo\nbar"
    assert result[2] == "no change\n"
    assert result[3] is None


def test_extract_numbers():
    s = pl.Series("a", ["Weight: 150 lbs", "Age 25, Height 5.11", "none", None])
    result = extract_numbers(s)
    assert result[0] == "150"
    assert result[1] == "25 5.11"
    assert result[2] == ""
    assert result[3] is None
