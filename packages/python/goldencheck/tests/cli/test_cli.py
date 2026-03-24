from pathlib import Path
from typer.testing import CliRunner
from goldencheck.cli.main import app

runner = CliRunner()
FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_scan_with_no_tui():
    result = runner.invoke(app, [str(FIXTURES / "simple.csv"), "--no-tui"])
    assert result.exit_code == 0


def test_validate_without_config():
    result = runner.invoke(app, ["validate", str(FIXTURES / "simple.csv")])
    assert result.exit_code != 0


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.3.0" in result.stdout


def test_llm_boost_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["scan", str(FIXTURES / "simple.csv"), "--llm-boost", "--no-tui"])
    assert result.exit_code != 0


# --- Error handling tests ---

def test_error_file_not_found():
    result = runner.invoke(app, ["scan", "nonexistent_file.csv", "--no-tui"])
    assert result.exit_code == 1
    assert "Error:" in result.stdout or "Error:" in (result.stderr or "")
    assert "Traceback" not in result.stdout


def test_error_unsupported_format(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("hello")
    result = runner.invoke(app, ["scan", str(f), "--no-tui"])
    assert result.exit_code == 1
    assert "Unsupported" in result.stdout or "Unsupported" in (result.stderr or "")
    assert "Traceback" not in result.stdout


# --- Fix command tests ---

def test_fix_safe_mode(tmp_path):
    csv = tmp_path / "dirty.csv"
    csv.write_text("name,age\n  Alice ,25\nBob  ,30\n")
    result = runner.invoke(app, ["fix", str(csv), "--dry-run"])
    assert result.exit_code == 0
    assert "trim_whitespace" in result.stdout or "clean" in result.stdout


def test_fix_aggressive_requires_force(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\n")
    result = runner.invoke(app, ["fix", str(csv), "--mode", "aggressive"])
    assert result.exit_code == 1


def test_fix_output_path(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\n  Alice ,25\n")
    out = tmp_path / "cleaned.csv"
    result = runner.invoke(app, ["fix", str(csv), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_fix_no_changes(tmp_path):
    csv = tmp_path / "clean.csv"
    csv.write_text("name,age\nAlice,25\nBob,30\n")
    result = runner.invoke(app, ["fix", str(csv)])
    assert result.exit_code == 0
    assert "clean" in result.stdout.lower()
