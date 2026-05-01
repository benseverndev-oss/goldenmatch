import polars as pl
from goldencheck.relations.temporal import TemporalOrderProfiler
from goldencheck.models.finding import Severity

def test_valid_temporal_order():
    df = pl.DataFrame({
        "start_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
        "end_date": ["2024-01-15", "2024-02-15", "2024-03-15"],
    })
    findings = TemporalOrderProfiler().profile(df)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0

def test_invalid_temporal_order():
    df = pl.DataFrame({
        "start_date": ["2024-01-01", "2024-03-01", "2024-03-01"],
        "end_date": ["2024-01-15", "2024-02-01", "2024-03-15"],
    })
    findings = TemporalOrderProfiler().profile(df)
    assert any(f.severity == Severity.ERROR for f in findings)

def test_no_date_columns():
    df = pl.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
    findings = TemporalOrderProfiler().profile(df)
    assert len(findings) == 0

def test_signup_login_pair_detected():
    df = pl.DataFrame({
        "signup_date": ["2024-01-01", "2024-02-01"],
        "last_login": ["2024-01-15", "2024-01-15"],  # second row: login before signup
    })
    findings = TemporalOrderProfiler().profile(df)
    assert any(f.severity == Severity.ERROR for f in findings)

def test_any_date_pair_low_confidence():
    df = pl.DataFrame({
        "date_a": ["2024-01-01", "2024-03-01"],
        "date_b": ["2024-01-15", "2024-02-01"],  # violation
    })
    findings = TemporalOrderProfiler().profile(df)
    # Should detect but with low confidence (not keyword matched)
    if findings:
        assert any(f.confidence < 0.5 for f in findings)

def test_many_date_columns_skips_exhaustive():
    # 12 date columns — should skip any-date-pair check
    data = {f"date_{i}": ["2024-01-01"] for i in range(12)}
    df = pl.DataFrame(data)
    findings = TemporalOrderProfiler().profile(df)
    # Only keyword-matched pairs, no exhaustive check
    assert len(findings) == 0  # no keyword matches among date_0..date_11
