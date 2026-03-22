import polars as pl
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.models.finding import Severity

def test_no_nulls_suggests_required():
    df = pl.DataFrame({"email": ["a@b.com", "c@d.com", "e@f.com"] * 100})
    findings = NullabilityProfiler().profile(df, "email")
    assert any(f.check == "nullability" and "required" in f.message.lower() for f in findings)

def test_all_nulls_flags_error():
    df = pl.DataFrame({"broken": [None, None, None]})
    findings = NullabilityProfiler().profile(df, "broken")
    assert any(f.severity == Severity.ERROR for f in findings)

def test_some_nulls_reports_info():
    df = pl.DataFrame({"notes": ["hello", None, "world", None]})
    findings = NullabilityProfiler().profile(df, "notes")
    assert any(f.check == "nullability" for f in findings)
