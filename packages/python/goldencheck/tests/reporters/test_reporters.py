import io
import json
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile
from goldencheck.reporters.json_reporter import report_json
from goldencheck.reporters.ci_reporter import report_ci


def test_json_reporter_schema():
    findings = [Finding(severity=Severity.ERROR, column="email", check="format", message="bad")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert data["file"] == "test.csv"
    assert data["health_score"]["grade"] in ("A", "B", "C", "D", "F")
    assert len(data["findings"]) == 1


def test_ci_exit_code_error():
    findings = [Finding(severity=Severity.ERROR, column="x", check="y", message="bad")]
    assert report_ci(findings, "error") == 1


def test_ci_exit_code_clean():
    findings = [Finding(severity=Severity.INFO, column="x", check="y", message="ok")]
    assert report_ci(findings, "error") == 0


def test_ci_exit_code_warning():
    findings = [Finding(severity=Severity.WARNING, column="x", check="y", message="hmm")]
    assert report_ci(findings, "warning") == 1
    assert report_ci(findings, "error") == 0

def test_json_reporter_includes_source_when_llm():
    findings = [Finding(severity=Severity.ERROR, column="x", check="y", message="bad", source="llm")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert data["findings"][0]["source"] == "llm"

def test_json_reporter_omits_source_when_none():
    findings = [Finding(severity=Severity.INFO, column="x", check="y", message="ok")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert "source" not in data["findings"][0]
