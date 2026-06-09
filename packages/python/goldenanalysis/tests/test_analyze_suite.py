"""analyze_match / analyze_pipeline entrypoints (pure — duck-typed results)."""

from __future__ import annotations

from types import SimpleNamespace

import goldenanalysis as ga


def test_analyze_match() -> None:
    result = SimpleNamespace(
        clusters={0: {"members": [0], "size": 1}, 1: {"members": [1, 2], "size": 2}},
        scored_pairs=[(1, 2, 0.9)],
        stats={"total_records": 3, "match_rate": 0.66},
        config=None,
    )
    report = ga.analyze_match(result, dataset="customers")
    assert set(report.analyzers_run) == {"match.rates", "cluster.distribution"}
    keys = {m.key for m in report.metrics}
    assert "match.pair_count" in keys and "cluster.count" in keys
    assert report.source["dataset"] == "customers"
    assert report.source["producer"] == "goldenmatch"


def test_analyze_pipeline_fans_out_to_present_artifacts() -> None:
    result = SimpleNamespace(
        artifacts={
            "findings": [{"check": "x", "column": "a", "severity": "WARNING"}],
            "manifest": SimpleNamespace(records=[]),
            "clusters": {0: {"members": [0], "size": 1}},
            "scored_pairs": [],
            "match_stats": {"match_rate": 0.5},
        },
        source="customers.parquet",
    )
    report = ga.analyze_pipeline(result)
    ran = set(report.analyzers_run)
    assert "quality.rollup" in ran
    assert "cluster.distribution" in ran
    assert "match.rates" in ran
    # frame.summary needs a `frame` artifact, which PipeResult doesn't expose.
    assert "frame.summary" not in ran


def test_analyze_pipeline_omits_absent() -> None:
    # Only a manifest present -> only quality.rollup runs.
    result = SimpleNamespace(artifacts={"manifest": SimpleNamespace(records=[])}, source="d.csv")
    report = ga.analyze_pipeline(result)
    assert report.analyzers_run == ["quality.rollup"]
