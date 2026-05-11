import polars as pl
from goldenflow.transforms.url import url_extract_domain, url_normalize


def test_url_normalize_adds_scheme():
    s = pl.Series("u", ["example.com", "www.test.org"])
    result = url_normalize(s)
    assert result[0] == "https://example.com"
    assert result[1] == "https://www.test.org"


def test_url_normalize_lowercases_and_strips_trailing_slash():
    s = pl.Series("u", ["HTTPS://Example.COM/Path/", "http://TEST.org/"])
    result = url_normalize(s)
    assert result[0] == "https://example.com/Path"
    assert result[1] == "http://test.org"


def test_url_normalize_preserves_path():
    s = pl.Series("u", ["https://example.com/page?q=1"])
    result = url_normalize(s)
    assert result[0] == "https://example.com/page?q=1"


def test_url_normalize_none():
    s = pl.Series("u", [None, ""])
    result = url_normalize(s)
    assert result[0] is None
    assert result[1] is None


def test_url_extract_domain():
    s = pl.Series("u", [
        "https://www.example.com/page",
        "http://sub.domain.org/path?q=1",
        "example.com/about",
        None,
        "",
    ])
    result = url_extract_domain(s)
    assert result[0] == "www.example.com"
    assert result[1] == "sub.domain.org"
    assert result[2] == "example.com"
    assert result[3] is None
    assert result[4] is None
