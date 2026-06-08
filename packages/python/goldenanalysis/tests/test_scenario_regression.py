"""Worked-scenario acceptance (spec acceptance §6) — the Maya regression story.

Seed 7 healthy nights + 1 regressed night; the per-metric 2% gate on
match.recall_safe_bound catches a drop a global 10% gate would miss; the narrative
names the root-cause chain; baseline='previous' over a post-step pair flags nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from goldenanalysis.history import ReportHistory
from goldenanalysis.models import AnalysisReport, AnalysisTable, Metric, RegressionPolicy
from goldenanalysis.narrative import build_narrative


def _night(run_id: str, recall: float, singleton: float, findings: int) -> AnalysisReport:
    return AnalysisReport(
        run_id=run_id,
        generated_at=datetime(2026, 6, 8, tzinfo=UTC),
        source={"dataset": "customers"},
        metrics=[
            Metric(key="match.recall_safe_bound", value=recall, unit="ratio", direction="higher_better"),
            Metric(key="cluster.singleton_ratio", value=singleton, unit="ratio", direction="neutral"),
            Metric(key="quality.findings_total", value=findings, unit="findings", direction="lower_better"),
        ],
        tables=[
            AnalysisTable(name="findings_by_class", columns=["class", "count"], rows=[["email_blanked", 1188]])
        ],
    )


def test_maya_scenario(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "analysis.jsonl")
    for i in range(7):
        hist.append(_night(f"n{i}", recall=0.97, singleton=0.58, findings=410))
    hist.append(_night("n7", recall=0.89, singleton=0.71, findings=1205))

    policy = RegressionPolicy(default_pct=10.0, per_metric={"match.recall_safe_bound": 2.0})
    flagged = hist.detect_regressions("customers", baseline="rolling_median", policy=policy)
    keys = {r.metric for r in flagged}

    # The 2% gate catches recall_safe_bound (-8.2%); a global 10% gate would NOT.
    assert "match.recall_safe_bound" in keys
    assert not any(
        r.flagged for r in hist.detect_regressions("customers", policy=RegressionPolicy(default_pct=10.0))
        if r.metric == "match.recall_safe_bound"
    )
    # singleton_ratio (+22.4%) and findings_total (+194%) also flag.
    assert "cluster.singleton_ratio" in keys
    assert "quality.findings_total" in keys

    narrative = build_narrative(hist.reports("customers")[-1], flagged)
    assert "recall safe bound" in narrative
    assert "email_blanked" in narrative


def test_previous_over_post_step_pair_flags_nothing(tmp_path: Path) -> None:
    hist = ReportHistory(backend="jsonl", path=tmp_path / "a.jsonl")
    hist.append(_night("a", recall=0.89, singleton=0.71, findings=1205))
    hist.append(_night("b", recall=0.89, singleton=0.71, findings=1205))
    policy = RegressionPolicy(per_metric={"match.recall_safe_bound": 2.0})
    assert hist.detect_regressions("customers", baseline="previous", policy=policy) == []
