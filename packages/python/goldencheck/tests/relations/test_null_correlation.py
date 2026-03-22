import polars as pl
from goldencheck.relations.null_correlation import NullCorrelationProfiler

def test_correlated_nulls_detected():
    df = pl.DataFrame({
        "addr": ["123 St", None, "456 Ave", None],
        "city": ["NYC", None, "LA", None],
        "zip": ["10001", None, "90001", None],
    })
    findings = NullCorrelationProfiler().profile(df)
    assert any("null" in f.message.lower() and "correlat" in f.message.lower() for f in findings)

def test_uncorrelated_nulls():
    df = pl.DataFrame({
        "a": [1, None, 3, 4],
        "b": [None, 2, None, 4],
    })
    findings = NullCorrelationProfiler().profile(df)
    # These nulls don't correlate (different positions)
    corr_findings = [f for f in findings if "correlat" in f.message.lower()]
    assert len(corr_findings) == 0
