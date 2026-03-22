from pathlib import Path
import yaml
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.validator import validate_file
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, Settings
from goldencheck.config.loader import load_config
from goldencheck.config.writer import save_config
from goldencheck.models.finding import Finding, Severity
from goldencheck.reporters.ci_reporter import report_ci

FIXTURES = Path(__file__).parent / "fixtures"

def test_scan_messy_file():
    """Scan should find multiple issues in messy.csv."""
    findings, profile = scan_file(FIXTURES / "messy.csv")
    assert profile.row_count == 10
    assert profile.column_count == 10
    assert len(findings) > 5  # should find many issues

def test_scan_finds_all_profiler_types():
    """Verify findings come from multiple profiler types."""
    findings, _ = scan_file(FIXTURES / "messy.csv")
    checks = {f.check for f in findings}
    # Should have findings from at least 3 different profiler types
    assert len(checks) >= 3

def test_pin_and_export_roundtrip(tmp_path):
    """Scan, pin rules, export, reload, validate."""
    findings, profile = scan_file(FIXTURES / "messy.csv")

    # Build a config from findings
    config = GoldenCheckConfig(
        columns={
            "email": ColumnRule(type="string", required=True, format="email"),
            "status": ColumnRule(type="string", enum=["active", "inactive", "pending", "closed"]),
            "age": ColumnRule(type="integer", range=[0, 120]),
        }
    )

    # Save and reload
    config_path = tmp_path / "goldencheck.yml"
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded is not None
    assert "email" in loaded.columns

    # Validate against rules
    violations = validate_file(FIXTURES / "messy.csv", loaded)
    assert len(violations) > 0

    # Should find: null emails (required), bad emails (format), unknown status (enum), age 250 (range)
    checks = {f.check for f in violations}
    assert "required" in checks or "enum" in checks

def test_ci_exit_code_on_messy():
    """Validate messy file, verify exit code is 1."""
    config = GoldenCheckConfig(
        columns={"email": ColumnRule(type="string", required=True)}
    )
    findings = validate_file(FIXTURES / "messy.csv", config)
    exit_code = report_ci(findings, "error")
    assert exit_code == 1  # messy.csv has null emails

def test_ci_exit_code_clean():
    """Validate simple file with lenient rules, exit code 0."""
    config = GoldenCheckConfig(
        columns={"id": ColumnRule(type="integer")}
    )
    findings = validate_file(FIXTURES / "simple.csv", config)
    exit_code = report_ci(findings, "error")
    assert exit_code == 0  # no errors expected
