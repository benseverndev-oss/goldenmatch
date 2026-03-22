import polars as pl
from goldencheck.profilers.range_distribution import RangeDistributionProfiler
from goldencheck.models.finding import Severity

def test_outlier_detected():
    values = list(range(100)) + [99999]
    df = pl.DataFrame({"price": values})
    findings = RangeDistributionProfiler().profile(df, "price")
    assert any(f.severity == Severity.WARNING and "outlier" in f.message.lower() for f in findings)

def test_clean_range_no_warnings():
    df = pl.DataFrame({"age": list(range(20, 60))})
    findings = RangeDistributionProfiler().profile(df, "age")
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    assert len(warnings) == 0

def test_string_column_skipped():
    df = pl.DataFrame({"name": ["Alice", "Bob"]})
    findings = RangeDistributionProfiler().profile(df, "name")
    assert len(findings) == 0
