from goldencheck.models.finding import Finding, Severity

def test_finding_creation():
    f = Finding(severity=Severity.ERROR, column="email", check="format",
                message="6% not valid email", affected_rows=3000,
                sample_values=["not-an-email", "also bad"])
    assert f.severity == Severity.ERROR
    assert f.column == "email"
    assert f.affected_rows == 3000

def test_finding_without_optional_fields():
    f = Finding(severity=Severity.INFO, column="status", check="cardinality",
                message="4 unique values detected")
    assert f.affected_rows == 0
    assert f.sample_values == []
    assert f.suggestion is None

def test_finding_with_suggestion():
    f = Finding(severity=Severity.WARNING, column="date", check="format",
                message="2 date formats detected",
                suggestion="Standardize to MM/DD/YYYY (majority format)")
    assert f.suggestion == "Standardize to MM/DD/YYYY (majority format)"

def test_severity_ordering():
    assert Severity.ERROR.value > Severity.WARNING.value
    assert Severity.WARNING.value > Severity.INFO.value
