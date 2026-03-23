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

def test_some_nulls_notable_rate_reports_info():
    # >50% null rate — notable enough to report
    nulls = [None] * 60
    vals = ["hello"] * 40
    df = pl.DataFrame({"notes": vals + nulls})
    findings = NullabilityProfiler().profile(df, "notes")
    assert any(f.check == "nullability" for f in findings)


def test_some_nulls_normal_rate_no_info():
    # 10% null rate in a small dataset (<100 rows) — "normal optional", should not report
    vals = ["hello"] * 9 + [None] * 1
    df = pl.DataFrame({"notes": vals})
    findings = NullabilityProfiler().profile(df, "notes")
    assert not any(f.check == "nullability" for f in findings)
