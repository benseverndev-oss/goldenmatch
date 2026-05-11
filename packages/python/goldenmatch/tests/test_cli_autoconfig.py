"""Tests for the `goldenmatch autoconfig` CLI command + controller panel
rendering used by `dedupe`'s zero-config path.

The CLI's stderr is the controller panel + status messages; stdout is the
serialized config (intended for ``> goldenmatch.yml``). Both are asserted
separately.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def cli_csv(tmp_path: Path) -> Path:
    """A small but non-trivial CSV: 3 columns with clear identity / fuzz signals.

    The first_name column has within-record case noise (corruption signal),
    email is a clean identity anchor, last_name has typo-driven variants.
    """
    path = tmp_path / "data.csv"
    pl.DataFrame({
        "first_name": ["John", "john", "Jane", "JOHN", "Bob", "Robert"],
        "last_name": ["Smith", "Smith", "Doe", "Smyth", "Jones", "Brown"],
        "email": [
            "john@ex.com", "john@ex.com", "jane@t.com",
            "john@ex.com", "bob@t.com", "robert@t.com",
        ],
    }).write_csv(path)
    return path


def test_autoconfig_emits_yaml_on_stdout(cli_csv: Path):
    """Zero options → committed config goes to stdout, ready to pipe to a file."""
    result = runner.invoke(app, ["autoconfig", str(cli_csv), "--quiet"])
    assert result.exit_code == 0, result.stderr
    # Stdout is the YAML body — must look like a goldenmatch config.
    assert "matchkeys:" in result.stdout
    # Real config has at least one matchkey with a real name and type.
    assert "name:" in result.stdout
    # No Rich markup on stdout (we use bare print(), not console).
    assert "[bold" not in result.stdout


def test_autoconfig_panel_renders_on_stderr(cli_csv: Path):
    """Non-quiet mode prints the controller panel to stderr."""
    result = runner.invoke(app, ["autoconfig", str(cli_csv)])
    assert result.exit_code == 0, result.stderr
    # Panel title is "AutoConfig controller"; stop_reason line surfaces a
    # health verdict. Don't assert the specific verdict — auto-config on
    # 6 rows can legitimately end up YELLOW or RED.
    assert "AutoConfig controller" in result.stderr
    assert "stop ·" in result.stderr
    assert "health ·" in result.stderr


def test_autoconfig_out_flag_writes_file_and_suppresses_stdout(
    cli_csv: Path, tmp_path: Path,
):
    """``--out`` writes the YAML to a file and stdout stays empty."""
    out_path = tmp_path / "out.yml"
    result = runner.invoke(app, [
        "autoconfig", str(cli_csv), "--out", str(out_path), "--quiet",
    ])
    assert result.exit_code == 0, result.stderr
    assert out_path.exists()
    body = out_path.read_text(encoding="utf-8")
    assert "matchkeys:" in body
    # ``--out`` short-circuits the stdout YAML dump. (Some library deps
    # print incidental messages to stdout — assert the YAML body isn't
    # there rather than insisting on empty.)
    assert "matchkeys:" not in result.stdout


def test_autoconfig_hide_controller_silences_panel(cli_csv: Path):
    """``--hide-controller`` swaps the panel for a one-line status."""
    result = runner.invoke(app, [
        "autoconfig", str(cli_csv), "--hide-controller",
    ])
    assert result.exit_code == 0, result.stderr
    assert "AutoConfig controller" not in result.stderr
    # The short-status line uses ``health=...`` / ``stop=...`` format.
    assert "health=" in result.stderr
    assert "stop=" in result.stderr


def test_autoconfig_verbose_includes_decisions(cli_csv: Path):
    """``--verbose`` adds the refit decisions table to the panel."""
    result = runner.invoke(app, ["autoconfig", str(cli_csv), "--verbose"])
    assert result.exit_code == 0, result.stderr
    # On a 6-row dataset the controller may or may not refit, so don't
    # assert the table content — just confirm the verbose-only header
    # appears at all when there's any decision to render. (Smoke test;
    # if the harness ever stops emitting decisions on this fixture, this
    # test should be updated rather than masking the regression.)
    assert "complexity profile" in result.stderr or "indicator column priors" in result.stderr
