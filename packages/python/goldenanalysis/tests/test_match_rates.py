"""match.rates analyzer (pure — hand-built artifacts)."""

from __future__ import annotations

from goldenanalysis.analyzers.match_rates import MatchRatesAnalyzer
from goldenanalysis.models import AnalyzerInput


def _input(**artifacts) -> AnalyzerInput:
    return AnalyzerInput(dataset="customers", artifacts=artifacts)


def test_core_metrics() -> None:
    inp = _input(
        scored_pairs=[(0, 1, 0.9), (1, 2, 0.8), (3, 4, 0.95)],
        match_stats={"total_records": 10, "match_rate": 0.3, "total_clusters": 2, "matched_records": 3},
        match_threshold=0.82,
    )
    m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
    assert m["match.pair_count"].value == 3
    assert m["match.match_rate"].value == 0.3
    assert m["match.threshold"].value == 0.82
    assert abs(m["match.mean_pair_score"].value - (0.9 + 0.8 + 0.95) / 3) < 1e-9
    assert "match.recall_estimate" not in m  # no cert supplied -> omitted
    assert "match.recall_safe_bound" not in m


def test_recall_from_certificate() -> None:
    inp = _input(
        scored_pairs=[(0, 1, 0.9)],
        match_stats={"total_records": 4, "match_rate": 0.5},
        recall_certificate={"estimate": 0.94, "safe_bound": 0.89},
    )
    m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
    assert m["match.recall_estimate"].value == 0.94
    assert m["match.recall_estimate"].direction == "higher_better"
    assert m["match.recall_safe_bound"].value == 0.89
    assert m["match.recall_safe_bound"].direction == "higher_better"


def test_recall_estimate_only() -> None:
    # A RecallEstimate (no safe bound) -> estimate emitted, safe_bound omitted.
    inp = _input(
        scored_pairs=[(0, 1, 0.9)],
        match_stats={"match_rate": 0.5},
        recall_certificate={"estimate": 0.94, "safe_bound": None},
    )
    m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
    assert m["match.recall_estimate"].value == 0.94
    assert "match.recall_safe_bound" not in m


def test_score_histogram_table() -> None:
    inp = _input(scored_pairs=[(0, 1, 0.1), (2, 3, 0.9)], match_stats={"match_rate": 0.5})
    tables = {t.name: t for t in MatchRatesAnalyzer().run(inp).tables}
    assert "score_histogram" in tables


def test_empty_pairs_degrades() -> None:
    inp = _input(scored_pairs=[], match_stats={"total_records": 5, "match_rate": 0.0})
    m = {x.key: x for x in MatchRatesAnalyzer().run(inp).metrics}
    assert m["match.pair_count"].value == 0
    assert "match.mean_pair_score" not in m
