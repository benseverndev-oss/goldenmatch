import polars as pl
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler
from goldencheck.models.finding import Severity

def test_mixed_patterns_flagged():
    df = pl.DataFrame({"phone": ["(555) 123-4567"] * 90 + ["555.123.4567"] * 10})
    findings = PatternConsistencyProfiler().profile(df, "phone")
    assert any(f.severity == Severity.WARNING for f in findings)

def test_consistent_pattern_no_warning():
    df = pl.DataFrame({"code": ["ABC-123"] * 100})
    findings = PatternConsistencyProfiler().profile(df, "code")
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    assert len(warnings) == 0
