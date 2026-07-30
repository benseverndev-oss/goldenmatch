"""``key.integrity`` — metric-aware key-integrity metrics from a certificate.

Reads a ``key_certificate`` (a GoldenMatch ``KeyIntegrityCertificate`` or a dict
of the same shape) from ``AnalyzerInput.artifacts`` and projects it into the
suite's ``Metric`` / ``AnalysisTable`` reporting shape — the read-side sibling of
``match.rates`` (which consumes a recall certificate).

The certificate answers "is the entity key a semantic model declares actually
trustworthy?": unique-at-grain, per-measure fan-out, and (opt-in) entity
fragmentation / undercount. This analyzer only *reports* it — the computation and
the advisory contract live in goldenmatch.
"""

from __future__ import annotations

from typing import Any

from goldenanalysis.models import (
    AnalysisTable,
    AnalyzerInfo,
    AnalyzerInput,
    AnalyzerResult,
    Metric,
)

_PRODUCES = [
    "key.uniqueness",
    "key.duplicate_groups",
    "key.max_fan_out",
    "key.undercount_estimate",
    "key.fragmented_entities",
]


def _get(cert: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dataclass certificate or a plain dict."""
    if cert is None:
        return default
    if isinstance(cert, dict):
        return cert.get(name, default)
    return getattr(cert, name, default)


class KeyIntegrityAnalyzer:
    """Project a key-integrity certificate into suite metrics + a fan-out table."""

    info = AnalyzerInfo(
        name="key.integrity",
        consumes=["key_certificate"],
        produces=_PRODUCES,
    )

    def run(self, inp: AnalyzerInput) -> AnalyzerResult:
        cert = inp.artifacts.get("key_certificate")
        metrics: list[Metric] = []
        tables: list[AnalysisTable] = []
        if cert is None:
            return AnalyzerResult(metrics=metrics, tables=tables)

        # `estimate` is a property on the dataclass; recompute for the dict path.
        uniqueness = _get(cert, "estimate")
        if uniqueness is None:
            n_groups = _get(cert, "n_key_groups", 0) or 0
            dupes = _get(cert, "duplicate_key_groups", 0) or 0
            uniqueness = 1.0 - (dupes / n_groups) if n_groups else 1.0
        metrics.append(
            Metric(key="key.uniqueness", value=float(uniqueness), unit="ratio", direction="higher_better")
        )
        metrics.append(
            Metric(
                key="key.duplicate_groups",
                value=int(_get(cert, "duplicate_key_groups", 0) or 0),
                unit="groups",
                direction="lower_better",
            )
        )
        metrics.append(
            Metric(
                key="key.max_fan_out",
                value=float(_get(cert, "max_fan_out", 1.0) or 1.0),
                unit="ratio",
                direction="lower_better",
            )
        )

        undercount = _get(cert, "undercount_estimate")
        if undercount is not None:
            metrics.append(
                Metric(
                    key="key.undercount_estimate",
                    value=float(undercount),
                    unit="ratio",
                    direction="lower_better",
                )
            )
        fragmented = _get(cert, "fragmented_entities")
        if fragmented is not None:
            metrics.append(
                Metric(
                    key="key.fragmented_entities",
                    value=int(fragmented),
                    unit="entities",
                    direction="lower_better",
                )
            )

        fan_out = _get(cert, "measure_fan_out", {}) or {}
        if fan_out:
            tables.append(
                AnalysisTable(
                    name="measure_fan_out",
                    columns=["measure", "fan_out_ratio"],
                    rows=[[m, float(r)] for m, r in fan_out.items()],
                )
            )

        return AnalyzerResult(metrics=metrics, tables=tables)
