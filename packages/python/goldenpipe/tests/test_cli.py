"""Tests for CLI commands."""
from typer.testing import CliRunner
from goldenpipe.cli.main import app

runner = CliRunner()


class TestRunCommand:
    def test_run_csv(self, sample_csv):
        result = runner.invoke(app, ["run", str(sample_csv)])
        assert result.exit_code == 0

    def test_run_nonexistent(self):
        result = runner.invoke(app, ["run", "/nonexistent.csv"])
        assert result.exit_code == 0
        assert "FAILED" in result.stdout or "failed" in result.stdout.lower()

    def test_run_verbose(self, sample_csv):
        result = runner.invoke(app, ["run", str(sample_csv), "--verbose"])
        assert result.exit_code == 0


class TestStagesCommand:
    def test_list_stages(self):
        result = runner.invoke(app, ["stages"])
        assert result.exit_code == 0


class TestValidateCommand:
    def test_validate(self, tmp_path):
        config = tmp_path / "pipe.yml"
        config.write_text("pipeline: test\nstages: []\n")
        result = runner.invoke(app, ["validate", "--config", str(config)])
        assert result.exit_code == 0


class TestInitCommand:
    def test_init(self, tmp_path):
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "goldenpipe.yml").exists()
