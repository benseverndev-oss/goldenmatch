from typer.testing import CliRunner
from goldenflow.cli.main import app

runner = CliRunner()


def test_demo_command(tmp_path):
    result = runner.invoke(app, ["demo", "-o", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "demo_data.csv").exists()
    assert (tmp_path / "demo_config.yaml").exists()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "1.1.0" in result.stdout


def test_strict_mode_passes(sample_csv, tmp_path):
    result = runner.invoke(app, ["transform", str(sample_csv), "--output-dir", str(tmp_path), "--strict"])
    assert result.exit_code == 0
