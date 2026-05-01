from pathlib import Path
from goldencheck.engine.scanner import scan_file
from goldencheck.models.finding import Finding

FIXTURES = Path(__file__).parent.parent / "fixtures"

def test_scan_returns_findings():
    findings, profile = scan_file(FIXTURES / "simple.csv")
    assert isinstance(findings, list)
    assert all(isinstance(f, Finding) for f in findings)
    assert profile.row_count == 5
    assert profile.column_count == 5

def test_scan_detects_issues_in_fixture():
    findings, profile = scan_file(FIXTURES / "simple.csv")
    assert len(findings) > 0

def test_findings_sorted_by_severity():
    findings, _ = scan_file(FIXTURES / "simple.csv")
    if len(findings) >= 2:
        for i in range(len(findings) - 1):
            assert findings[i].severity >= findings[i + 1].severity
