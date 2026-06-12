"""Narrative generation."""

from __future__ import annotations

from datetime import UTC, datetime

from goldenanalysis.models import AnalysisReport, AnalysisTable, Metric, Regression
from goldenanalysis.narrative import build_narrative


def _report() -> AnalysisReport:
    return AnalysisReport(
        run_id="r7",
        generated_at=datetime(2026, 6, 8, tzinfo=UTC),
        source={"dataset": "customers"},
        metrics=[
            Metric(key="match.recall_safe_bound", value=0.89, unit="ratio", direction="higher_better"),
            Metric(key="cluster.singleton_ratio", value=0.71, unit="ratio", direction="neutral"),
            Metric(key="quality.findings_total", value=1205, unit="findings", direction="lower_better"),
        ],
        tables=[
            AnalysisTable(
                name="findings_by_class",
                columns=["class", "count"],
                rows=[["email_blanked", 1188], ["phone_unparseable", 12]],
            )
        ],
    )


def test_narrative_with_regressions() -> None:
    regs = [
        Regression(metric="match.recall_safe_bound", baseline=0.97, current=0.89, delta_pct=-8.2, flagged=True, direction="higher_better"),
        Regression(metric="cluster.singleton_ratio", baseline=0.58, current=0.71, delta_pct=22.4, flagged=True, direction="neutral"),
    ]
    text = build_narrative(_report(), regs).lower()
    # Leads with the largest-magnitude regression (singleton +22.4% > recall -8.2%).
    assert "recall safe bound" in text and "0.89" in text
    assert "singleton" in text and "0.71" in text
    assert "email_blanked" in text


def test_narrative_no_regressions() -> None:
    text = build_narrative(_report(), [])
    assert "No regressions" in text
    assert "regression" not in text.lower().replace("no regressions", "")
