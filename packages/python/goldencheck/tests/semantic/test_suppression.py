from goldencheck.semantic.suppression import apply_suppression
from goldencheck.semantic.classifier import ColumnClassification, TypeDef
from goldencheck.models.finding import Finding, Severity

def _type_defs():
    return {
        "free_text": TypeDef(name_hints=["notes"], value_signals={}, suppress=["pattern_consistency", "cardinality"]),
        "identifier": TypeDef(name_hints=["id"], value_signals={}, suppress=["cardinality", "pattern_consistency"]),
    }

def test_suppresses_irrelevant_finding():
    findings = [Finding(severity=Severity.WARNING, column="notes", check="pattern_consistency", message="mixed patterns", confidence=0.5)]
    col_types = {"notes": ColumnClassification(type_name="free_text", source="name")}
    result = apply_suppression(findings, col_types, _type_defs())
    assert result[0].severity == Severity.INFO
    assert "suppressed" in result[0].message

def test_keeps_relevant_finding():
    findings = [Finding(severity=Severity.ERROR, column="notes", check="nullability", message="all null", confidence=0.99)]
    col_types = {"notes": ColumnClassification(type_name="free_text", source="name")}
    result = apply_suppression(findings, col_types, _type_defs())
    assert result[0].severity == Severity.ERROR  # not suppressed (nullability not in suppress list)

def test_never_suppresses_llm():
    findings = [Finding(severity=Severity.WARNING, column="notes", check="pattern_consistency", message="x", source="llm", confidence=0.5)]
    col_types = {"notes": ColumnClassification(type_name="free_text", source="name")}
    result = apply_suppression(findings, col_types, _type_defs())
    assert result[0].severity == Severity.WARNING  # not suppressed

def test_never_suppresses_high_confidence():
    findings = [Finding(severity=Severity.WARNING, column="notes", check="pattern_consistency", message="x", confidence=0.95)]
    col_types = {"notes": ColumnClassification(type_name="free_text", source="name")}
    result = apply_suppression(findings, col_types, _type_defs())
    assert result[0].severity == Severity.WARNING  # not suppressed (confidence >= 0.9)

def test_no_mutation():
    original = Finding(severity=Severity.WARNING, column="notes", check="pattern_consistency", message="x", confidence=0.5)
    col_types = {"notes": ColumnClassification(type_name="free_text", source="name")}
    apply_suppression([original], col_types, _type_defs())
    assert original.severity == Severity.WARNING  # original unchanged

def test_info_not_suppressed():
    findings = [Finding(severity=Severity.INFO, column="notes", check="cardinality", message="x")]
    col_types = {"notes": ColumnClassification(type_name="free_text", source="name")}
    result = apply_suppression(findings, col_types, _type_defs())
    assert result[0].severity == Severity.INFO  # already INFO, no change

def test_unclassified_column_not_suppressed():
    findings = [Finding(severity=Severity.WARNING, column="mystery", check="pattern_consistency", message="x", confidence=0.5)]
    col_types = {"mystery": ColumnClassification(type_name=None, source="none")}
    result = apply_suppression(findings, col_types, _type_defs())
    assert result[0].severity == Severity.WARNING  # no type, no suppression
