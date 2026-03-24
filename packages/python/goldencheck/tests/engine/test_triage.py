from goldencheck.engine.triage import auto_triage, TriageResult
from goldencheck.models.finding import Finding, Severity


def _make(severity: Severity, confidence: float) -> Finding:
    return Finding(
        severity=severity,
        column="col",
        check="chk",
        message="msg",
        confidence=confidence,
    )


def test_empty_findings():
    result = auto_triage([])
    assert result == TriageResult()
    assert result.pin == []
    assert result.dismiss == []
    assert result.review == []


def test_high_severity_high_confidence_pinned():
    """WARNING/ERROR with confidence >= 0.8 go to pin."""
    f_warn = _make(Severity.WARNING, 0.8)
    f_err = _make(Severity.ERROR, 0.95)
    result = auto_triage([f_warn, f_err])
    assert f_warn in result.pin
    assert f_err in result.pin
    assert result.dismiss == []
    assert result.review == []


def test_info_dismissed():
    """INFO findings always dismissed regardless of confidence."""
    f = _make(Severity.INFO, 1.0)
    result = auto_triage([f])
    assert f in result.dismiss
    assert result.pin == []


def test_low_confidence_dismissed():
    """Any finding with confidence < 0.5 dismissed."""
    f = _make(Severity.ERROR, 0.4)
    result = auto_triage([f])
    assert f in result.dismiss


def test_medium_confidence_warning_review():
    """WARNING with 0.5 <= confidence < 0.8 goes to review."""
    f = _make(Severity.WARNING, 0.6)
    result = auto_triage([f])
    assert f in result.review


def test_medium_confidence_error_review():
    """ERROR with 0.5 <= confidence < 0.8 goes to review."""
    f = _make(Severity.ERROR, 0.7)
    result = auto_triage([f])
    assert f in result.review


def test_boundary_confidence_080_pinned():
    """Exactly 0.8 confidence with WARNING is pinned."""
    f = _make(Severity.WARNING, 0.8)
    result = auto_triage([f])
    assert f in result.pin


def test_boundary_confidence_050_not_dismissed():
    """Exactly 0.5 confidence with ERROR is reviewed (not dismissed)."""
    f = _make(Severity.ERROR, 0.5)
    result = auto_triage([f])
    assert f in result.review


def test_mixed_findings():
    """Multiple findings distributed across all three buckets."""
    pin_f = _make(Severity.ERROR, 0.9)
    dismiss_f = _make(Severity.INFO, 0.9)
    review_f = _make(Severity.WARNING, 0.6)
    low_conf = _make(Severity.ERROR, 0.3)

    result = auto_triage([pin_f, dismiss_f, review_f, low_conf])
    assert pin_f in result.pin
    assert dismiss_f in result.dismiss
    assert review_f in result.review
    assert low_conf in result.dismiss
