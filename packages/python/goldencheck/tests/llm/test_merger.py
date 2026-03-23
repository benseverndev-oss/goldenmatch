from goldencheck.llm.merger import merge_llm_findings
from goldencheck.llm.prompts import LLMResponse, LLMColumnAssessment, LLMIssue, LLMUpgrade, LLMDowngrade, LLMRelation
from goldencheck.models.finding import Finding, Severity

def test_new_issue_added():
    findings = []
    response = LLMResponse(columns={"name": LLMColumnAssessment(
        semantic_type="person_name",
        issues=[LLMIssue(severity="error", check="type_inference", message="numbers in name", affected_values=["123"])],
    )})
    result = merge_llm_findings(findings, response)
    assert len(result) == 1
    assert result[0].source == "llm"
    assert result[0].severity == Severity.ERROR
    assert result[0].sample_values == ["123"]

def test_upgrade_changes_severity():
    findings = [Finding(severity=Severity.INFO, column="email", check="nullability", message="optional")]
    response = LLMResponse(columns={"email": LLMColumnAssessment(
        semantic_type="email",
        upgrades=[LLMUpgrade(original_check="nullability", original_severity="info", new_severity="warning", reason="emails should be required")],
    )})
    result = merge_llm_findings(findings, response)
    assert result[0].severity == Severity.WARNING
    assert result[0].source == "llm"

def test_downgrade_changes_severity():
    findings = [Finding(severity=Severity.WARNING, column="phone", check="pattern_consistency", message="mixed")]
    response = LLMResponse(columns={"phone": LLMColumnAssessment(
        semantic_type="phone",
        downgrades=[LLMDowngrade(original_check="pattern_consistency", original_severity="warning", new_severity="info", reason="mixed formats are normal")],
    )})
    result = merge_llm_findings(findings, response)
    assert result[0].severity == Severity.INFO
    assert result[0].source == "llm"

def test_upgrade_nonexistent_creates_new_issue():
    findings = []
    response = LLMResponse(columns={"x": LLMColumnAssessment(
        semantic_type="id",
        upgrades=[LLMUpgrade(original_check="uniqueness", original_severity="info", new_severity="error", reason="IDs must be unique")],
    )})
    result = merge_llm_findings(findings, response)
    assert len(result) == 1
    assert result[0].severity == Severity.ERROR
    assert result[0].source == "llm"

def test_downgrade_nonexistent_ignored():
    findings = [Finding(severity=Severity.ERROR, column="a", check="b", message="c")]
    response = LLMResponse(columns={"x": LLMColumnAssessment(
        semantic_type="id",
        downgrades=[LLMDowngrade(original_check="z", original_severity="warning", new_severity="info", reason="not real")],
    )})
    result = merge_llm_findings(findings, response)
    assert len(result) == 1  # original unchanged, downgrade ignored

def test_malformed_response_returns_original():
    findings = [Finding(severity=Severity.INFO, column="a", check="b", message="c")]
    result = merge_llm_findings(findings, None)
    assert len(result) == 1
    assert result[0].source is None  # untouched

def test_relation_creates_finding():
    findings = []
    response = LLMResponse(relations=[
        LLMRelation(type="temporal_order", columns=["signup_date", "last_login"], reasoning="signup before login"),
    ])
    result = merge_llm_findings(findings, response)
    assert len(result) == 1
    assert result[0].column == "last_login,signup_date"  # alphabetically sorted
    assert result[0].check == "temporal_order"
    assert result[0].source == "llm"
