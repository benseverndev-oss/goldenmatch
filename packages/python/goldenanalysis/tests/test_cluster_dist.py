"""cluster.distribution analyzer (pure)."""

from __future__ import annotations

from goldenanalysis.analyzers.cluster_dist import ClusterDistributionAnalyzer
from goldenanalysis.models import AnalyzerInput

# 4 clusters, sizes [1, 1, 3, 2] -> 7 records.
_CLUSTERS = {
    0: {"members": [0], "size": 1},
    1: {"members": [1], "size": 1},
    2: {"members": [2, 3, 4], "size": 3},
    3: {"members": [5, 6], "size": 2},
}


def _run(**artifacts):
    inp = AnalyzerInput(dataset="customers", artifacts=artifacts)
    return ClusterDistributionAnalyzer().run(inp)


def test_core_metrics() -> None:
    r = _run(clusters=_CLUSTERS)
    m = {x.key: x for x in r.metrics}
    assert m["cluster.count"].value == 4
    assert m["cluster.record_count"].value == 7
    assert m["cluster.singleton_ratio"].value == 0.5
    assert m["cluster.size_max"].value == 3
    assert abs(m["cluster.reduction_ratio"].value - (1 - 4 / 7)) < 1e-9


def test_histogram_buckets() -> None:
    r = _run(clusters=_CLUSTERS)
    tbl = {t.name: t for t in r.tables}["cluster_size_histogram"]
    assert tbl.rows == [[1, 2], [2, 1], [3, 1], ["4+", 0]]


def test_record_count_prefers_stats() -> None:
    # When match_stats carries total_records, use it (the engine's own total).
    r = _run(clusters=_CLUSTERS, match_stats={"total_records": 20})
    m = {x.key: x for x in r.metrics}
    assert m["cluster.record_count"].value == 20
    assert abs(m["cluster.reduction_ratio"].value - (1 - 4 / 20)) < 1e-9


def test_no_clusters_emits_nothing() -> None:
    r = _run(clusters={})
    assert r.metrics == []
