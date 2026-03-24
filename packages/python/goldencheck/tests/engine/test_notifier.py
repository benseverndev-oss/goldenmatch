from goldencheck.engine.notifier import should_notify
from goldencheck.engine.history import ScanRecord
from goldencheck.models.finding import Finding, Severity


def _make_finding(severity: Severity) -> Finding:
    return Finding(
        severity=severity,
        column="col",
        check="chk",
        message="msg",
    )


def _make_scan_record(grade: str = "A") -> ScanRecord:
    return ScanRecord(
        timestamp="2024-01-01T00:00:00",
        file="/test.csv",
        rows=10,
        columns=2,
        grade=grade,
        score=90,
        errors=0,
        warnings=0,
        findings_count=0,
    )


# --- any-error trigger ---

def test_any_error_with_errors():
    findings = [_make_finding(Severity.ERROR)]
    assert should_notify("A", findings, None, "any-error") is True


def test_any_error_without_errors():
    findings = [_make_finding(Severity.WARNING)]
    assert should_notify("A", findings, None, "any-error") is False


def test_any_error_no_findings():
    assert should_notify("A", [], None, "any-error") is False


# --- any-warning trigger ---

def test_any_warning_with_warnings():
    findings = [_make_finding(Severity.WARNING)]
    assert should_notify("A", findings, None, "any-warning") is True


def test_any_warning_with_errors():
    """Errors also trigger any-warning."""
    findings = [_make_finding(Severity.ERROR)]
    assert should_notify("A", findings, None, "any-warning") is True


def test_any_warning_info_only():
    findings = [_make_finding(Severity.INFO)]
    assert should_notify("A", findings, None, "any-warning") is False


def test_any_warning_no_findings():
    assert should_notify("A", [], None, "any-warning") is False


# --- grade-drop trigger ---

def test_grade_drop_detected():
    prev = _make_scan_record(grade="A")
    assert should_notify("B", [], prev, "grade-drop") is True


def test_grade_drop_severe():
    prev = _make_scan_record(grade="A")
    assert should_notify("F", [], prev, "grade-drop") is True


def test_grade_no_drop():
    prev = _make_scan_record(grade="B")
    assert should_notify("A", [], prev, "grade-drop") is False


def test_grade_same():
    prev = _make_scan_record(grade="B")
    assert should_notify("B", [], prev, "grade-drop") is False


def test_grade_drop_no_previous_scan():
    """No previous scan means we can't detect a drop."""
    assert should_notify("F", [], None, "grade-drop") is False


# --- unknown trigger ---

def test_unknown_trigger():
    assert should_notify("F", [_make_finding(Severity.ERROR)], None, "unknown") is False
