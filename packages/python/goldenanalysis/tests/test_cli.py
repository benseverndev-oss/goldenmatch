"""CLI: the ``report`` command + honest stubs for trend/regressions."""

from __future__ import annotations

from pathlib import Path

import goldenanalysis as ga
from fixtures import ensure_customers_small
from goldenanalysis.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_report_markdown() -> None:
    fixture = ensure_customers_small()
    result = runner.invoke(app, ["report", str(fixture), "--analyzers", "frame.summary", "--format", "markdown"])
    assert result.exit_code == 0, result.output
    assert "frame.row_count" in result.output


def test_report_json_parses() -> None:
    fixture = ensure_customers_small()
    result = runner.invoke(app, ["report", str(fixture), "--format", "json"])
    assert result.exit_code == 0, result.output
    report = ga.AnalysisReport.from_json(result.output)
    assert "frame.row_count" in {m.key for m in report.metrics}


def test_report_writes_out(tmp_path: Path) -> None:
    fixture = ensure_customers_small()
    out = tmp_path / "r.md"
    result = runner.invoke(app, ["report", str(fixture), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "frame.row_count" in out.read_text(encoding="utf-8")


def test_trend_stub_is_honest() -> None:
    result = runner.invoke(app, ["trend", "--metric", "x", "--dataset", "d", "--history", "h.db"])
    assert result.exit_code == 1
    assert "0.2.0" in result.output


def test_regressions_stub_is_honest() -> None:
    result = runner.invoke(app, ["regressions", "--dataset", "d", "--history", "h.db"])
    assert result.exit_code == 1
    assert "0.2.0" in result.output
