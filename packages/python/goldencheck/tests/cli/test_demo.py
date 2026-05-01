"""Tests for the demo command."""
from __future__ import annotations

from typer.testing import CliRunner

from goldencheck.cli.main import app

runner = CliRunner()


def test_demo_runs_without_error():
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0


def test_demo_shows_findings():
    result = runner.invoke(app, ["demo"])
    assert (
        "Finding" in result.stdout
        or "ERROR" in result.stdout
        or "WARNING" in result.stdout
    )


def test_demo_no_tui_flag():
    result = runner.invoke(app, ["demo", "--no-tui"])
    assert result.exit_code == 0
