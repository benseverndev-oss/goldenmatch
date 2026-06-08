"""ReportHistory — SQLite backend (optional, durable, same surface)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from goldenanalysis.history import ReportHistory
from goldenanalysis.models import AnalysisReport, Metric, RegressionPolicy


def _report(run_id: str, metrics: list[tuple[str, float, str]]) -> AnalysisReport:
    return AnalysisReport(
        run_id=run_id,
        generated_at=datetime(2026, 6, 8, tzinfo=UTC),
        source={"dataset": "customers"},
        metrics=[Metric(key=k, value=v, unit="ratio", direction=d) for k, v, d in metrics],
    )


def test_sqlite_append_order_and_upsert(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    hist = ReportHistory(backend="sqlite", path=db)
    hist.append(_report("r0", [("m", 1.0, "neutral")]))
    hist.append(_report("r1", [("m", 2.0, "neutral")]))
    hist.append(_report("r0", [("m", 9.0, "neutral")]))  # upsert
    reps = hist.reports("customers")
    assert [r.run_id for r in reps] == ["r0", "r1"]
    assert reps[0].metrics[0].value == 9.0


def test_sqlite_durable_across_handles(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    ReportHistory(backend="sqlite", path=db).append(_report("r0", [("m", 1.0, "neutral")]))
    # A fresh handle on the same file sees the persisted report.
    assert len(ReportHistory(backend="sqlite", path=db).reports("customers")) == 1


def test_sqlite_detect_regressions(tmp_path: Path) -> None:
    hist = ReportHistory(backend="sqlite", path=tmp_path / "a.db")
    for i in range(7):
        hist.append(_report(f"r{i}", [("match.recall_safe_bound", 0.97, "higher_better")]))
    hist.append(_report("r7", [("match.recall_safe_bound", 0.89, "higher_better")]))
    policy = RegressionPolicy(per_metric={"match.recall_safe_bound": 2.0})
    flagged = hist.detect_regressions("customers", baseline="rolling_median", policy=policy)
    assert any(r.metric == "match.recall_safe_bound" for r in flagged)
