"""Edge-case tests for the semantic type classifier."""
from __future__ import annotations

import polars as pl

from goldencheck.semantic.classifier import (
    ColumnClassification,
    classify_columns,
    list_available_domains,
    load_type_defs,
)


# ---------------------------------------------------------------------------
# test_classify_empty_dataframe
# ---------------------------------------------------------------------------


class TestClassifyEmptyDataframe:
    def test_returns_empty_dict(self):
        df = pl.DataFrame()
        result = classify_columns(df)
        assert result == {}

    def test_headers_only(self):
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.String)})
        result = classify_columns(df)
        assert "a" in result
        assert isinstance(result["a"], ColumnClassification)

    def test_multiple_empty_columns(self):
        df = pl.DataFrame({
            "x": pl.Series([], dtype=pl.Int64),
            "y": pl.Series([], dtype=pl.String),
            "z": pl.Series([], dtype=pl.Float64),
        })
        result = classify_columns(df)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# test_classify_all_domains — all 3 domain packs load
# ---------------------------------------------------------------------------


class TestClassifyAllDomains:
    def test_three_domains_available(self):
        domains = list_available_domains()
        assert len(domains) >= 3
        assert "healthcare" in domains
        assert "finance" in domains
        assert "ecommerce" in domains

    def test_healthcare_type_defs_load(self):
        defs = load_type_defs(domain="healthcare")
        assert len(defs) > 0

    def test_finance_type_defs_load(self):
        defs = load_type_defs(domain="finance")
        assert len(defs) > 0

    def test_ecommerce_type_defs_load(self):
        defs = load_type_defs(domain="ecommerce")
        assert len(defs) > 0

    def test_domain_types_extend_base(self):
        base = load_type_defs()
        health = load_type_defs(domain="healthcare")
        # Domain defs should include base types too
        assert len(health) >= len(base)

    def test_classify_with_each_domain(self):
        df = pl.DataFrame({
            "email": ["alice@example.com", "bob@example.com"],
            "name": ["Alice", "Bob"],
        })
        for domain in list_available_domains():
            defs = load_type_defs(domain=domain)
            result = classify_columns(df, type_defs=defs)
            assert "email" in result
            assert "name" in result


# ---------------------------------------------------------------------------
# test_classify_single_column
# ---------------------------------------------------------------------------


class TestClassifySingleColumn:
    def test_email_column(self):
        df = pl.DataFrame({"email": ["a@b.com", "c@d.org", "e@f.io"]})
        result = classify_columns(df)
        assert result["email"].type_name == "email"
        assert result["email"].source == "name"

    def test_unknown_column(self):
        df = pl.DataFrame({"xyzzy": ["aaa", "bbb", "ccc"]})
        result = classify_columns(df)
        # Generic short strings with no name hint should not match a type
        assert result["xyzzy"].type_name is None or result["xyzzy"].source in (
            "none", "value"
        )

    def test_phone_column_by_name(self):
        df = pl.DataFrame({"phone_number": ["555-1234", "555-5678"]})
        result = classify_columns(df)
        assert result["phone_number"].type_name is not None


# ---------------------------------------------------------------------------
# test_classify_numeric_only — DataFrame with only numeric columns
# ---------------------------------------------------------------------------


class TestClassifyNumericOnly:
    def test_all_int_columns(self):
        df = pl.DataFrame({
            "a": [1, 2, 3],
            "b": [10, 20, 30],
            "c": [100, 200, 300],
        })
        result = classify_columns(df)
        assert len(result) == 3
        # All should classify (even if to "none")
        for col_name, classification in result.items():
            assert isinstance(classification, ColumnClassification)

    def test_float_columns(self):
        df = pl.DataFrame({
            "temperature": [98.6, 99.1, 97.8],
            "weight": [150.0, 180.5, 200.3],
        })
        result = classify_columns(df)
        assert len(result) == 2

    def test_mixed_numeric_types(self):
        df = pl.DataFrame({
            "int_col": pl.Series([1, 2, 3], dtype=pl.Int32),
            "float_col": pl.Series([1.1, 2.2, 3.3], dtype=pl.Float64),
            "uint_col": pl.Series([10, 20, 30], dtype=pl.UInt16),
        })
        result = classify_columns(df)
        assert len(result) == 3
        for c in result.values():
            assert isinstance(c, ColumnClassification)

    def test_numeric_with_nulls(self):
        df = pl.DataFrame({
            "val": pl.Series([1, None, 3, None, 5], dtype=pl.Int64),
        })
        result = classify_columns(df)
        assert "val" in result
