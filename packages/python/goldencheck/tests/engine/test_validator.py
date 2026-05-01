from pathlib import Path
from goldencheck.engine.validator import validate_file
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, IgnoreEntry
from goldencheck.models.finding import Severity

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_validate_required_column():
    config = GoldenCheckConfig(columns={"email": ColumnRule(type="string", required=True)})
    findings = validate_file(FIXTURES / "simple.csv", config)
    assert any(f.check == "required" and f.severity == Severity.ERROR for f in findings)


def test_validate_enum():
    config = GoldenCheckConfig(columns={"status": ColumnRule(type="string", enum=["active", "inactive"])})
    findings = validate_file(FIXTURES / "simple.csv", config)
    assert any(f.check == "enum" for f in findings)  # "pending" is not in enum


def test_validate_missing_column():
    config = GoldenCheckConfig(columns={"nonexistent": ColumnRule(type="string")})
    findings = validate_file(FIXTURES / "simple.csv", config)
    assert any(f.check == "existence" for f in findings)


def test_validate_ignore():
    config = GoldenCheckConfig(
        columns={"email": ColumnRule(type="string", required=True)},
        ignore=[IgnoreEntry(column="email", check="required")],
    )
    findings = validate_file(FIXTURES / "simple.csv", config)
    assert not any(f.column == "email" and f.check == "required" for f in findings)
