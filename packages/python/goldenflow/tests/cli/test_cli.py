from pathlib import Path

from goldenflow.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_transform_zero_config(sample_csv: Path, tmp_path: Path):
    result = runner.invoke(app, ["transform", str(sample_csv), "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "sample_transformed.csv").exists()
    assert (tmp_path / "sample_manifest.json").exists()


def test_validate_dry_run(sample_csv: Path):
    result = runner.invoke(app, ["validate", str(sample_csv)])
    assert result.exit_code == 0
    assert "would change" in result.stdout.lower() or "transform" in result.stdout.lower()


def test_profile(sample_csv: Path):
    result = runner.invoke(app, ["profile", str(sample_csv)])
    assert result.exit_code == 0
    assert "name" in result.stdout.lower()


def test_learn(sample_csv: Path, tmp_path: Path):
    out = tmp_path / "learned.yaml"
    result = runner.invoke(app, ["learn", str(sample_csv), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_diff(sample_csv: Path, tmp_path: Path):
    # Transform first, then diff
    runner.invoke(app, ["transform", str(sample_csv), "--output-dir", str(tmp_path)])
    transformed = tmp_path / "sample_transformed.csv"
    result = runner.invoke(app, ["diff", str(sample_csv), str(transformed)])
    assert result.exit_code == 0


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "goldenflow" in result.stdout.lower() or "transform" in result.stdout.lower()
