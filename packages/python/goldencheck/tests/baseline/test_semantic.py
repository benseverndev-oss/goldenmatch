"""Tests for goldencheck.baseline.semantic — keyword-based semantic type inferrer."""
from __future__ import annotations

import polars as pl

from goldencheck.baseline.semantic import infer_semantic_types


# ---------------------------------------------------------------------------
# Email detection
# ---------------------------------------------------------------------------


def test_email_column_detected():
    df = pl.DataFrame({"email": ["alice@example.com", "bob@example.com"]})
    result = infer_semantic_types(df, use_embeddings=False)
    assert "email" in result
    assert "email" in result["email"]


def test_email_substring_match():
    """Column name 'customer_email' contains 'email' — substring match."""
    df = pl.DataFrame({"customer_email": ["x@y.com"]})
    result = infer_semantic_types(df, use_embeddings=False)
    assert "email" in result
    assert "customer_email" in result["email"]


# ---------------------------------------------------------------------------
# Multiple columns of the same type
# ---------------------------------------------------------------------------


def test_multiple_phone_columns():
    """Both 'phone' and 'mobile_phone' should map to 'phone'."""
    df = pl.DataFrame(
        {
            "phone": ["555-1234"],
            "mobile_phone": ["555-5678"],
        }
    )
    result = infer_semantic_types(df, use_embeddings=False)
    assert "phone" in result
    phones = result["phone"]
    assert "phone" in phones
    assert "mobile_phone" in phones


# ---------------------------------------------------------------------------
# Date columns
# ---------------------------------------------------------------------------


def test_date_columns_detected():
    df = pl.DataFrame(
        {
            "start_date": ["2024-01-01"],
            "end_date": ["2024-12-31"],
        }
    )
    result = infer_semantic_types(df, use_embeddings=False)
    assert "date" in result
    dates = result["date"]
    assert "start_date" in dates
    assert "end_date" in dates


# ---------------------------------------------------------------------------
# Unclassifiable column not in results
# ---------------------------------------------------------------------------


def test_unclassifiable_column_absent():
    """A column with no keyword match should not appear in results."""
    df = pl.DataFrame({"xyzzy_quux": ["foo", "bar"]})
    result = infer_semantic_types(df, use_embeddings=False)
    # No type should list this column
    for cols in result.values():
        assert "xyzzy_quux" not in cols


# ---------------------------------------------------------------------------
# One column maps to at most one type (first match wins)
# ---------------------------------------------------------------------------


def test_first_match_wins():
    """'user_email_id' contains both 'email' and 'id' — only one type assigned."""
    df = pl.DataFrame({"user_email_id": ["a@b.com"]})
    result = infer_semantic_types(df, use_embeddings=False)
    found_types = [t for t, cols in result.items() if "user_email_id" in cols]
    assert len(found_types) <= 1


# ---------------------------------------------------------------------------
# Prefix / suffix match rules
# ---------------------------------------------------------------------------


def test_prefix_match():
    """Keyword 'is_' (prefix marker) should match 'is_active' but not 'diagnosis_desc'."""
    df = pl.DataFrame({"is_active": [True, False], "diagnosis_desc": ["a", "b"]})
    result = infer_semantic_types(df, use_embeddings=False)
    boolean_cols = result.get("boolean", [])
    assert "is_active" in boolean_cols
    assert "diagnosis_desc" not in boolean_cols


def test_suffix_match():
    """Keyword '_id' (suffix marker) should match 'user_id' and 'order_id'."""
    df = pl.DataFrame({"user_id": [1, 2], "order_id": [10, 20]})
    result = infer_semantic_types(df, use_embeddings=False)
    id_cols = result.get("identifier", [])
    assert "user_id" in id_cols
    assert "order_id" in id_cols


# ---------------------------------------------------------------------------
# Return type sanity
# ---------------------------------------------------------------------------


def test_returns_dict_of_lists():
    df = pl.DataFrame({"email": ["a@b.com"], "noise": ["abc"]})
    result = infer_semantic_types(df, use_embeddings=False)
    assert isinstance(result, dict)
    for key, val in result.items():
        assert isinstance(key, str)
        assert isinstance(val, list)


def test_empty_dataframe_returns_empty():
    df = pl.DataFrame({"email": pl.Series([], dtype=pl.Utf8)})
    result = infer_semantic_types(df, use_embeddings=False)
    # Should still detect by column name even with no rows
    assert isinstance(result, dict)
