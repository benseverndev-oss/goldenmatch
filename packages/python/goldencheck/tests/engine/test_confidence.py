from goldencheck.engine.confidence import apply_corroboration_boost, apply_confidence_downgrade
from goldencheck.models.finding import Finding, Severity


def test_corroboration_boost_two_profilers():
    findings = [
        Finding(severity=Severity.WARNING, column="email", check="format", message="a", confidence=0.6),
        Finding(severity=Severity.WARNING, column="email", check="pattern", message="b", confidence=0.5),
    ]
    result = apply_corroboration_boost(findings)
    assert all(f.confidence >= 0.6 for f in result)  # boosted by 0.1


def test_corroboration_boost_three_profilers():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="1", confidence=0.5),
        Finding(severity=Severity.WARNING, column="x", check="b", message="2", confidence=0.5),
        Finding(severity=Severity.WARNING, column="x", check="c", message="3", confidence=0.5),
    ]
    result = apply_corroboration_boost(findings)
    assert all(f.confidence == 0.7 for f in result)  # boosted by 0.2


def test_corroboration_boost_capped_at_1():
    findings = [
        Finding(severity=Severity.ERROR, column="x", check="a", message="1", confidence=0.95),
        Finding(severity=Severity.ERROR, column="x", check="b", message="2", confidence=0.95),
    ]
    result = apply_corroboration_boost(findings)
    assert all(f.confidence == 1.0 for f in result)


def test_corroboration_no_mutation():
    original = Finding(severity=Severity.WARNING, column="x", check="a", message="1", confidence=0.5)
    findings = [original, Finding(severity=Severity.WARNING, column="x", check="b", message="2", confidence=0.5)]
    apply_corroboration_boost(findings)
    assert original.confidence == 0.5  # original not mutated


def test_downgrade_low_confidence_without_llm():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="m", confidence=0.3),
        Finding(severity=Severity.ERROR, column="y", check="b", message="n", confidence=0.9),
    ]
    result = apply_confidence_downgrade(findings, llm_boost=False)
    assert result[0].severity == Severity.INFO  # low confidence downgraded
    assert result[1].severity == Severity.ERROR  # high confidence unchanged


def test_downgrade_skipped_with_llm():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="m", confidence=0.3),
    ]
    result = apply_confidence_downgrade(findings, llm_boost=True)
    assert result[0].severity == Severity.WARNING  # not downgraded, LLM will handle


def test_downgrade_appends_suffix():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="something wrong", confidence=0.3),
    ]
    result = apply_confidence_downgrade(findings, llm_boost=False)
    assert "low confidence" in result[0].message
    assert "--llm-boost" in result[0].message
