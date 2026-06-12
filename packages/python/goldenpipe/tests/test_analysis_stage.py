"""GoldenAnalysis terminal reporting stage (goldenanalysis.report).

The stage is read-only: it attaches an `analysis_report` artifact built from the
run's accumulated artifacts and never mutates data. The integration cases skip
cleanly when goldenanalysis isn't installed; the info + guard cases always run.
"""
from __future__ import annotations

import pytest
from goldenpipe.adapters import analysis
from goldenpipe.adapters.analysis import AnalysisReportStage
from goldenpipe.models.context import PipeContext, StageStatus


def test_stage_info() -> None:
    info = AnalysisReportStage.info
    assert info.name == "goldenanalysis.report"
    assert info.produces == ["analysis_report"]
    # Only df is hard-required; the rest are read opportunistically (degrade).
    assert info.consumes == ["df"]


def test_validate_requires_goldenanalysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis, "HAS_ANALYSIS", False)
    with pytest.raises(RuntimeError, match="goldenanalysis"):
        AnalysisReportStage().validate(PipeContext())


def test_run_attaches_report_from_artifacts() -> None:
    pytest.importorskip("goldenanalysis")
    ctx = PipeContext(
        artifacts={
            "clusters": {0: {"members": [0], "size": 1}, 1: {"members": [1, 2], "size": 2}},
            "scored_pairs": [(1, 2, 0.9)],
            "match_stats": {"total_records": 3, "match_rate": 0.66},
        },
        metadata={"source": "customers.parquet"},
    )
    result = AnalysisReportStage().run(ctx)
    assert result.status == StageStatus.SUCCESS
    report = ctx.artifacts["analysis_report"]
    keys = {m["key"] for m in report["metrics"]}
    assert "cluster.count" in keys
    assert "match.pair_count" in keys
    assert report["source"]["producer"] == "goldenpipe"
    # Read-only: nothing else was added to the data artifacts.
    assert "clusters" in ctx.artifacts  # untouched


def test_run_degrades_with_no_artifacts() -> None:
    pytest.importorskip("goldenanalysis")
    ctx = PipeContext(metadata={"source": "empty.csv"})
    result = AnalysisReportStage().run(ctx)
    # A reporting stage must never break the run: no artifacts -> empty report.
    assert result.status == StageStatus.SUCCESS
    assert ctx.artifacts["analysis_report"]["analyzers_run"] == []


def test_registered_as_entry_point() -> None:
    pytest.importorskip("goldenanalysis")
    from goldenpipe.engine.registry import StageRegistry

    reg = StageRegistry()
    reg.discover()
    assert "goldenanalysis.report" in reg.list_all()
