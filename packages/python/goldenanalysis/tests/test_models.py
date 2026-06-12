"""Model-layer contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

from goldenanalysis.models import (
    AnalysisReport,
    AnalysisTable,
    AnalyzerInfo,
    AnalyzerResult,
    Metric,
)


def test_metric_defaults_direction_neutral() -> None:
    m = Metric(key="frame.row_count", value=10, unit="rows")
    assert m.direction == "neutral"
    assert m.value == 10
    assert m.unit == "rows"


def test_metric_direction_literal() -> None:
    m = Metric(key="frame.null_ratio_mean", value=0.4, unit="ratio", direction="lower_better")
    assert m.direction == "lower_better"


def test_report_defaults() -> None:
    r = AnalysisReport(
        run_id="r1",
        generated_at=datetime(2026, 6, 8, tzinfo=UTC),
        source={},
        metrics=[],
        tables=[],
    )
    assert r.schema_version == 1
    assert r.analyzers_run == []
    assert r.narrative is None


def test_report_json_roundtrip() -> None:
    r = AnalysisReport(
        run_id="r1",
        generated_at=datetime(2026, 6, 8, tzinfo=UTC),
        source={"dataset": "customers"},
        metrics=[Metric(key="frame.row_count", value=3, unit="rows")],
        tables=[AnalysisTable(name="per_column", columns=["c"], rows=[["a"]])],
        analyzers_run=["frame.summary"],
    )
    again = AnalysisReport.model_validate_json(r.model_dump_json())
    assert again == r


def test_analyzer_io_types() -> None:
    info = AnalyzerInfo(name="frame.summary", consumes=["frame"], produces=["frame.row_count"])
    assert info.name == "frame.summary"
    assert info.consumes == ["frame"]
    res = AnalyzerResult(metrics=[Metric(key="frame.row_count", value=1)], tables=[])
    assert res.metrics[0].key == "frame.row_count"
    assert res.tables == []
