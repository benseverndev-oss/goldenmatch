"""ReportHistory — JSONL backend (default)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from goldenanalysis.history import ReportHistory
from goldenanalysis.models import AnalysisReport, Metric, RegressionPolicy


def _report(run_id: str, metrics: list[tuple[str, float, str]], dataset: str = "customers") -> AnalysisReport:
    return AnalysisReport(
        run_id=run_id,
        generated_at=datetime(2026, 6, 8, tzinfo=UTC),
        source={"dataset": dataset},
        metrics=[Metric(key=k, value=v, unit="ratio", direction=d) for k, v, d in metrics],
    )


def _seed(hist: ReportHistory) -> None:
    # 7 healthy nights then a regressed 8th (the spec's worked scenario).
    for i in range(7):
        hist.append(
            _report(
                f"r{i}",
                [("match.recall_safe_bound", 0.97, "higher_better"), ("cluster.singleton_ratio", 0.58, "neutral")],
            )
        )
    hist.append(
        _report(
            "r7",
            [("match.recall_safe_bound", 0.89, "higher_better"), ("cluster.singleton_ratio", 0.71, "neutral")],
        )
    )


def test_append_and_reports_order(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "a.jsonl")
    hist.append(_report("r0", [("m", 1.0, "neutral")]))
    hist.append(_report("r1", [("m", 2.0, "neutral")]))
    reps = hist.reports("customers")
    assert [r.run_id for r in reps] == ["r0", "r1"]


def test_idempotent_upsert(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "a.jsonl")
    hist.append(_report("r0", [("m", 1.0, "neutral")]))
    hist.append(_report("r0", [("m", 9.0, "neutral")]))  # same (analysis, dataset, run_id)
    reps = hist.reports("customers")
    assert len(reps) == 1
    assert reps[0].metrics[0].value == 9.0


def test_trend(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "a.jsonl")
    _seed(hist)
    series = hist.trend("cluster.singleton_ratio", "customers", last_n=14)
    assert series.points[-1] == ("r7", 0.71)
    assert len(series.points) == 8


def test_detect_regressions_scenario(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "a.jsonl")
    _seed(hist)
    policy = RegressionPolicy(default_pct=10.0, per_metric={"match.recall_safe_bound": 2.0})
    flagged = {r.metric: r for r in hist.detect_regressions("customers", baseline="rolling_median", policy=policy)}
    # recall_safe_bound -8.2% flags under its 2% gate (a 10% gate would miss it).
    assert "match.recall_safe_bound" in flagged
    # singleton_ratio +22.4% flags under the default 10% gate.
    assert "cluster.singleton_ratio" in flagged


def test_previous_baseline_over_post_step_pair_flags_nothing(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "a.jsonl")
    # Two post-step nights (both already at the new level) -> "previous" sees no move.
    hist.append(_report("a", [("match.recall_safe_bound", 0.89, "higher_better")]))
    hist.append(_report("b", [("match.recall_safe_bound", 0.89, "higher_better")]))
    policy = RegressionPolicy(per_metric={"match.recall_safe_bound": 2.0})
    assert hist.detect_regressions("customers", baseline="previous", policy=policy) == []
