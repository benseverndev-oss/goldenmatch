import polars as pl
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.models.finding import Severity

def test_email_format_detected():
    df = pl.DataFrame({"contact": ["a@b.com", "c@d.com", "not-email", "e@f.com"]})
    findings = FormatDetectionProfiler().profile(df, "contact")
    assert any("email" in f.message.lower() for f in findings)

def test_clean_emails_no_error():
    df = pl.DataFrame({"email": [f"user{i}@test.com" for i in range(100)]})
    findings = FormatDetectionProfiler().profile(df, "email")
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0

def test_non_string_column_skipped():
    df = pl.DataFrame({"count": [1, 2, 3, 4, 5]})
    findings = FormatDetectionProfiler().profile(df, "count")
    assert len(findings) == 0

def test_emails_in_url_column_flagged():
    urls = ["https://example.com"] * 90 + ["user@email.com"] * 10
    df = pl.DataFrame({"website_url": urls})
    findings = FormatDetectionProfiler().profile(df, "website_url")
    assert any("wrong" in f.message.lower() or "email" in f.message.lower() for f in findings)
