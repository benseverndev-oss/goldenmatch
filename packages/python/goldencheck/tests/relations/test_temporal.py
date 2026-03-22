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
