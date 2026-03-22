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
    assert "0.1.0" in result.stdout
