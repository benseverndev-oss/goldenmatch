import polars as pl
from goldencheck.profilers.uniqueness import UniquenessProfiler
from goldencheck.models.finding import Severity

def test_fully_unique_column():
    df = pl.DataFrame({"id": list(range(100))})
    findings = UniquenessProfiler().profile(df, "id")
    assert any("unique" in f.message.lower() and "primary key" in f.message.lower() for f in findings)

def test_duplicates_detected():
    df = pl.DataFrame({"code": ["A", "B", "A", "C", "B", "A"]})
    findings = UniquenessProfiler().profile(df, "code")
    # Low unique percentage, no near-unique warning expected
    assert not any(f.severity == Severity.WARNING for f in findings)

def test_all_same_value():
    df = pl.DataFrame({"flag": ["yes"] * 100})
    findings = UniquenessProfiler().profile(df, "flag")
    assert not any(f.severity == Severity.ERROR for f in findings)
