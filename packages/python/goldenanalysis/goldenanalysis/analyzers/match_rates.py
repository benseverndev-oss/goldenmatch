"""``match.rates`` — match metrics from a GoldenMatch result's artifacts.

Reads ``scored_pairs`` / ``match_stats`` (+ optional ``recall_certificate`` and
``match_threshold``) from ``AnalyzerInput.artifacts``. Degrades: emits the metrics
its present artifacts support and omits the rest.
"""

from __future__ import annotations

from typing import Any

from goldenanalysis.core import aggregate as agg
from goldenanalysis.models import (
    AnalysisTable,
    AnalyzerInfo,
    AnalyzerInput,
    AnalyzerResult,
    Metric,
)

_PRODUCES = [
    "match.pair_count",
    "match.match_rate",
    "match.threshold",
    "match.recall_estimate",
    "match.recall_safe_bound",
    "match.mean_pair_score",
]


def _cert_values(cert: Any) -> tuple[float | None, float | None]:
    """Normalize a certificate (dict or RecallEstimate/RecallCertificate) to
    ``(estimate, safe_bound)``. Either may be None."""
    if cert is None:
        return None, None
    if isinstance(cert, dict):
        return cert.get("estimate", cert.get("recall")), cert.get("safe_bound", cert.get("recall_lower"))
    # dataclass: RecallEstimate has .recall; RecallCertificate adds .recall_lower
    return getattr(cert, "recall", None), getattr(cert, "recall_lower", None)


class MatchRatesAnalyzer:
    """Pair counts, match rate, recall (from a certificate), score distribution."""

    info = AnalyzerInfo(name="match.rates", consumes=["scored_pairs", "match_stats"], produces=_PRODUCES)

    def run(self, inp: AnalyzerInput) -> AnalyzerResult:
        art = inp.artifacts
        scored_pairs = art.get("scored_pairs", [])
        stats = art.get("match_stats", {}) or {}

        metrics: list[Metric] = [
            Metric(key="match.pair_count", value=len(scored_pairs), unit="pairs", direction="neutral"),
        ]
        if "match_rate" in stats:
            metrics.append(
                Metric(key="match.match_rate", value=stats["match_rate"], unit="ratio", direction="neutral")
            )
        if art.get("match_threshold") is not None:
            metrics.append(
                Metric(key="match.threshold", value=art["match_threshold"], unit="score", direction="neutral")
            )

        estimate, safe_bound = _cert_values(art.get("recall_certificate"))
        if estimate is not None:
            metrics.append(
                Metric(key="match.recall_estimate", value=estimate, unit="ratio", direction="higher_better")
            )
        if safe_bound is not None:
            metrics.append(
                Metric(
                    key="match.recall_safe_bound", value=safe_bound, unit="ratio", direction="higher_better"
                )
            )

        tables: list[AnalysisTable] = []
        if scored_pairs:
            scores = [float(s) for *_, s in scored_pairs]
            mean_score = sum(scores) / len(scores)
            metrics.append(
                Metric(key="match.mean_pair_score", value=mean_score, unit="score", direction="neutral")
            )
            hist = agg.histogram(scores, bins=10)
            tables.append(
                AnalysisTable(
                    name="score_histogram",
                    columns=["bin_left", "count"],
                    rows=[[edge, count] for edge, count in hist],
                )
            )

        return AnalyzerResult(metrics=metrics, tables=tables)
