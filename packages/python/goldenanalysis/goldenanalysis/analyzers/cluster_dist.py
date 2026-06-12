"""``cluster.distribution`` — cluster-size shape from a GoldenMatch result.

Reads ``clusters`` (and optionally ``match_stats`` for the record count) from
``AnalyzerInput.artifacts``.
"""

from __future__ import annotations

from goldenanalysis.core import aggregate as agg
from goldenanalysis.models import (
    AnalysisTable,
    AnalyzerInfo,
    AnalyzerInput,
    AnalyzerResult,
    Metric,
)

_PRODUCES = [
    "cluster.count",
    "cluster.record_count",
    "cluster.singleton_ratio",
    "cluster.size_p50",
    "cluster.size_p95",
    "cluster.size_max",
    "cluster.reduction_ratio",
]


class ClusterDistributionAnalyzer:
    """Cluster count, singleton ratio, size quantiles, reduction ratio, histogram."""

    info = AnalyzerInfo(name="cluster.distribution", consumes=["clusters"], produces=_PRODUCES)

    def run(self, inp: AnalyzerInput) -> AnalyzerResult:
        clusters = inp.artifacts.get("clusters")
        if not clusters:
            return AnalyzerResult(metrics=[], tables=[])

        sizes = [int(c.get("size", len(c.get("members", []))) if isinstance(c, dict) else c) for c in clusters.values()]
        count = len(clusters)
        # Prefer the engine's own record total; fall back to summed cluster sizes.
        stats = inp.artifacts.get("match_stats", {}) or {}
        record_count = int(stats.get("total_records", sum(sizes)))
        singletons = sum(1 for s in sizes if s == 1)

        metrics = [
            Metric(key="cluster.count", value=count, unit="clusters", direction="neutral"),
            Metric(key="cluster.record_count", value=record_count, unit="rows", direction="neutral"),
            Metric(
                key="cluster.singleton_ratio",
                value=(singletons / count) if count else 0.0,
                unit="ratio",
                direction="neutral",
            ),
            Metric(key="cluster.size_p50", value=agg.quantile(sizes, 0.5), unit="rows", direction="neutral"),
            Metric(key="cluster.size_p95", value=agg.quantile(sizes, 0.95), unit="rows", direction="neutral"),
            Metric(key="cluster.size_max", value=max(sizes) if sizes else 0, unit="rows", direction="neutral"),
            Metric(
                key="cluster.reduction_ratio",
                value=(1 - count / record_count) if record_count else 0.0,
                unit="ratio",
                direction="neutral",
            ),
        ]

        # Discrete size histogram, buckets 1 / 2 / 3 / "4+".
        n1 = sum(1 for s in sizes if s == 1)
        n2 = sum(1 for s in sizes if s == 2)
        n3 = sum(1 for s in sizes if s == 3)
        n4 = sum(1 for s in sizes if s >= 4)
        table = AnalysisTable(
            name="cluster_size_histogram",
            columns=["size", "count"],
            rows=[[1, n1], [2, n2], [3, n3], ["4+", n4]],
        )

        return AnalyzerResult(metrics=metrics, tables=[table])
