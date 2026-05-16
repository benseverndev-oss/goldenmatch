import polars as pl
from goldenflow.transforms.email import (
    email_extract_domain,
    email_lowercase,
    email_normalize,
    email_validate,
)


def _apply_expr(func, column: str, data: list) -> list:
    """Helper to apply an expr-mode transform to test data."""
    df = pl.DataFrame({column: data})
    expr = func(column)
    return df.select(expr.alias(column))[column].to_list()


def test_email_lowercase():
    result = _apply_expr(
        email_lowercase, "e",
        ["John.DOE@Gmail.Com", "ADMIN@EXAMPLE.COM", None],
    )
    assert result[0] == "john.doe@gmail.com"
    assert result[1] == "admin@example.com"
    assert result[2] is None


def test_email_normalize_strips_gmail_dots():
    s = pl.Series("e", ["john.doe@gmail.com", "j.o.h.n@gmail.com"])
    result = email_normalize(s)
    assert result[0] == "johndoe@gmail.com"
    assert result[1] == "john@gmail.com"


def test_email_normalize_strips_plus_tags():
    s = pl.Series("e", ["user+spam@example.com", "test+tag@gmail.com"])
    result = email_normalize(s)
    assert result[0] == "user@example.com"
    assert result[1] == "test@gmail.com"


def test_email_normalize_lowercases():
    s = pl.Series("e", ["USER@Example.COM"])
    result = email_normalize(s)
    assert result[0] == "user@example.com"


def test_email_normalize_preserves_none_and_invalid():
    s = pl.Series("e", [None, "", "not-an-email"])
    result = email_normalize(s)
    assert result[0] is None
    assert result[1] == ""  # preserved, not dropped
    assert result[2] == "not-an-email"  # preserved, not dropped


def test_email_extract_domain():
    result = _apply_expr(
        email_extract_domain, "e",
        ["user@example.com", "admin@sub.domain.org", None, "invalid"],
    )
    assert result[0] == "example.com"
    assert result[1] == "sub.domain.org"
    assert result[2] is None
    assert result[3] is None


def test_email_validate():
    s = pl.Series("e", [
        "valid@example.com",
        "also.valid+tag@sub.example.co.uk",
        "missing-at-sign",
        "@no-local.com",
        "no-domain@",
        None,
        "",
    ])
    result = email_validate(s)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is False
    assert result[3] is False
    assert result[4] is False
    assert result[5] is None
    assert result[6] is False
