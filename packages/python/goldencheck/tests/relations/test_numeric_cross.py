import polars as pl

from goldencheck.relations.numeric_cross import NumericCrossColumnProfiler, _find_max_pairs
from goldencheck.models.finding import Severity


def test_find_max_pairs_amount_limit():
    pairs = _find_max_pairs(["amount", "limit"])
    assert ("amount", "limit") in pairs


def test_find_max_pairs_no_match():
    pairs = _find_max_pairs(["name", "email"])
    assert pairs == []


def test_find_max_pairs_score_max_score():
    pairs = _find_max_pairs(["score", "max_score"])
    assert ("score", "max_score") in pairs


def test_no_violations():
    df = pl.DataFrame({
        "amount": [10, 20, 30],
        "limit": [100, 100, 100],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert findings == []


def test_value_exceeds_max():
    df = pl.DataFrame({
        "amount": [10, 200, 30],
        "limit": [100, 100, 100],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].check == "cross_column_validation"
    assert findings[0].affected_rows == 1
    assert "exceeds" in findings[0].sample_values[0]


def test_multiple_violations():
    df = pl.DataFrame({
        "amount": [200, 300, 30],
        "limit": [100, 100, 100],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].affected_rows == 2


def test_no_matching_columns():
    df = pl.DataFrame({
        "name": ["Alice", "Bob"],
        "email": ["a@b.com", "c@d.com"],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert findings == []


def test_all_equal_no_violation():
    df = pl.DataFrame({
        "amount": [100, 100],
        "limit": [100, 100],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert findings == []


def test_float_columns():
    df = pl.DataFrame({
        "charge": [10.5, 200.7],
        "max_charge": [100.0, 100.0],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].affected_rows == 1


def test_string_numeric_columns():
    """String columns that can be cast to float should work."""
    df = pl.DataFrame({
        "amount": ["10", "200", "30"],
        "limit": ["100", "100", "100"],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].affected_rows == 1


def test_balance_limit_pair():
    df = pl.DataFrame({
        "balance": [500, 100],
        "credit_limit": [200, 200],
    })
    findings = NumericCrossColumnProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].affected_rows == 1
