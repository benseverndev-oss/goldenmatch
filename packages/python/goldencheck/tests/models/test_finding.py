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

def test_finding_default_source_is_none():
    f = Finding(severity=Severity.INFO, column="x", check="y", message="z")
    assert f.source is None

def test_finding_with_llm_source():
    f = Finding(severity=Severity.ERROR, column="x", check="y", message="z", source="llm")
    assert f.source == "llm"

def test_finding_default_confidence():
    f = Finding(severity=Severity.INFO, column="x", check="y", message="z")
    assert f.confidence == 1.0

def test_finding_custom_confidence():
    f = Finding(severity=Severity.WARNING, column="x", check="y", message="z", confidence=0.3)
    assert f.confidence == 0.3
