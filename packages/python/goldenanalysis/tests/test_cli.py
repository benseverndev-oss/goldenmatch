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


def _seed_history(path: Path) -> None:
    from datetime import UTC, datetime

    from goldenanalysis.history import ReportHistory
    from goldenanalysis.models import AnalysisReport, Metric

    hist = ReportHistory(backend="jsonl", path=path)

    def night(run_id: str, recall: float, singleton: float) -> AnalysisReport:
        return AnalysisReport(
            run_id=run_id,
            generated_at=datetime(2026, 6, 8, tzinfo=UTC),
            source={"dataset": "customers"},
            metrics=[
                Metric(key="match.recall_safe_bound", value=recall, unit="ratio", direction="higher_better"),
                Metric(key="cluster.singleton_ratio", value=singleton, unit="ratio", direction="neutral"),
            ],
        )

    for i in range(7):
        hist.append(night(f"n{i}", 0.97, 0.58))
    hist.append(night("n7", 0.89, 0.71))


def test_trend_command(tmp_path: Path) -> None:
    hpath = tmp_path / "h.jsonl"
    _seed_history(hpath)
    result = runner.invoke(
        app, ["trend", "--metric", "cluster.singleton_ratio", "--dataset", "customers", "--history", str(hpath)]
    )
    assert result.exit_code == 0, result.output
    assert "0.71" in result.output and "n7" in result.output


def test_regressions_command(tmp_path: Path) -> None:
    hpath = tmp_path / "h.jsonl"
    _seed_history(hpath)
    result = runner.invoke(
        app,
        ["regressions", "--dataset", "customers", "--history", str(hpath), "--policy", "match.recall_safe_bound=2"],
    )
    assert result.exit_code == 0, result.output
    assert "match.recall_safe_bound" in result.output


def test_regressions_fail_on_regression_exits_1(tmp_path: Path) -> None:
    hpath = tmp_path / "h.jsonl"
    _seed_history(hpath)
    result = runner.invoke(
        app,
        [
            "regressions", "--dataset", "customers", "--history", str(hpath),
            "--policy", "match.recall_safe_bound=2", "--fail-on-regression",
        ],
    )
    assert result.exit_code == 1
    assert "match.recall_safe_bound" in result.output
