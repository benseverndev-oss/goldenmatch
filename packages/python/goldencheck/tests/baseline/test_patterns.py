"""Tests for goldencheck.baseline.patterns — TDD baseline."""
from __future__ import annotations

import polars as pl

from goldencheck.baseline.patterns import _induce_column_grammars, induce_patterns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _product_code_df(n: int = 100) -> pl.DataFrame:
    """Return a DataFrame with a uniform product-code column like 'AB0001'."""
    # All values match pattern: [A-Z]{2}[0-9]{4}
    codes = [f"{chr(65 + (i % 26))}{chr(65 + ((i + 1) % 26))}{i % 10000:04d}" for i in range(n)]
    return pl.DataFrame({"product_code": codes})


def _phone_df(n: int = 120) -> pl.DataFrame:
    """Return a DataFrame with two phone formats mixed."""
    # Half: (555) 123-4567 → ([0-9]{3}) [0-9]{3}-[0-9]{4}
    # Half: 555-123-4567   → [0-9]{3}-[0-9]{3}-[0-9]{4}
    phones = []
    for i in range(n):
        if i % 2 == 0:
            phones.append(f"(555) {i % 900 + 100:03d}-{i % 9000 + 1000:04d}")
        else:
            phones.append(f"555-{i % 900 + 100:03d}-{i % 9000 + 1000:04d}")
    return pl.DataFrame({"phone": phones})


def _mixed_df() -> pl.DataFrame:
    """Return a DataFrame with both string and numeric columns."""
    codes = [f"AB{i:04d}" for i in range(50)]
    return pl.DataFrame({"code": codes, "amount": list(range(50))})


def _high_cardinality_df(n: int = 200) -> pl.DataFrame:
    """Return a DataFrame where every string value is unique (random sentences)."""
    import string

    values = [
        "".join(string.ascii_letters[j % 52] for j in range(i % 5 + 1, i % 25 + 5)) for i in range(n)
    ]
    return pl.DataFrame({"notes": values})


def _low_row_df() -> pl.DataFrame:
    """Return a DataFrame with fewer than 30 rows."""
    return pl.DataFrame({"code": [f"AB{i:04d}" for i in range(10)]})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_product_code_pattern_detected():
    """Uniform product-code column should yield a single high-coverage grammar."""
    df = _product_code_df(n=100)
    result = induce_patterns(df)

    assert "product_code" in result
    grammars = result["product_code"]
    assert len(grammars) >= 1

    # Top grammar should cover almost everything
    top = max(grammars, key=lambda g: g.coverage)
    assert top.coverage > 0.9, f"Expected coverage > 0.9, got {top.coverage}"
    # Pattern should mention character classes
    assert "[A-Z]" in top.pattern or "[0-9]" in top.pattern


def test_multiple_phone_formats_detected():
    """Mixed phone formats should produce at least 2 distinct grammars."""
    df = _phone_df(n=120)
    result = induce_patterns(df)

    assert "phone" in result
    grammars = result["phone"]
    assert len(grammars) >= 2, f"Expected >= 2 grammars, got {len(grammars)}: {grammars}"


def test_skips_numeric_columns():
    """Numeric columns must not appear in the pattern result."""
    df = _mixed_df()
    result = induce_patterns(df)

    assert "amount" not in result, "Numeric column 'amount' should be skipped"
    assert "code" in result


def test_high_cardinality_no_crash():
    """High-cardinality string columns should not crash (may return few/no grammars)."""
    df = _high_cardinality_df(n=200)
    # Should not raise
    result = induce_patterns(df)
    # Result is a dict; column may be absent (nothing >= 3%) or present with grammars
    assert isinstance(result, dict)


def test_skips_low_row_count():
    """DataFrames with fewer than 30 rows should return an empty dict."""
    df = _low_row_df()
    result = induce_patterns(df)
    assert result == {}, f"Expected empty dict for low row count, got {result}"


def test_coverage_reasonable_for_uniform_format():
    """A perfectly uniform format column should produce coverage > 0.9."""
    # All values: exactly "XX-0000" pattern (two uppercase, dash, four digits)
    values = [f"{chr(65 + i % 26)}{chr(90 - i % 26)}-{i % 10000:04d}" for i in range(80)]
    df = pl.DataFrame({"sku": values})
    result = induce_patterns(df)

    assert "sku" in result
    grammars = result["sku"]
    total_coverage = sum(g.coverage for g in grammars)
    assert total_coverage > 0.9, f"Total coverage {total_coverage} too low for uniform format"


def test_induce_column_grammars_direct():
    """_induce_column_grammars should work as a standalone function."""
    values = [f"INV-{i:05d}" for i in range(60)]
    grammars = _induce_column_grammars(values)
    assert len(grammars) >= 1
    top = max(grammars, key=lambda g: g.coverage)
    assert top.coverage > 0.9
    assert "[A-Z]" in top.pattern or "[0-9]" in top.pattern


def test_pattern_coverage_sums_near_one_for_uniform():
    """Coverage values from _induce_column_grammars sum close to 1 for a uniform column."""
    values = [f"ORD{i:06d}" for i in range(100)]
    grammars = _induce_column_grammars(values)
    total = sum(g.coverage for g in grammars)
    assert 0.9 <= total <= 1.0, f"Expected total coverage near 1.0, got {total}"
