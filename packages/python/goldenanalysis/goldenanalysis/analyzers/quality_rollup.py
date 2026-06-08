"""``quality.rollup`` — a single "is the data healthy" view rolling up both
GoldenCheck (scan findings + profile) and GoldenFlow (transform manifest).

Reads ``findings`` / ``profile`` / ``manifest`` from ``AnalyzerInput.artifacts``,
degrading per-producer: the ``quality.*`` keys need ``findings``; the ``flow.*``
keys need ``manifest``; either can be absent.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from goldenanalysis.models import (
    AnalysisTable,
    AnalyzerInfo,
    AnalyzerInput,
    AnalyzerResult,
    Metric,
)

_PRODUCES = [
    "quality.findings_total",
    "quality.columns_with_findings",
    "quality.score",
    "flow.rows_changed",
    "flow.rules_fired",
]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or an object (Finding/TransformRecord either way)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _severity_name(value: Any) -> str:
    """Normalize a severity (enum / int / str) to an upper-case name."""
    name = getattr(value, "name", None)
    if name:
        return str(name).upper()
    if isinstance(value, int):
        return {1: "INFO", 2: "WARNING", 3: "ERROR"}.get(value, str(value))
    return str(value).upper()


class QualityRollupAnalyzer:
    """Findings totals + GoldenCheck score + GoldenFlow transform stats."""

    info = AnalyzerInfo(name="quality.rollup", consumes=["findings", "manifest"], produces=_PRODUCES)

    def run(self, inp: AnalyzerInput) -> AnalyzerResult:
        art = inp.artifacts
        metrics: list[Metric] = []
        tables: list[AnalysisTable] = []

        findings = art.get("findings")
        if findings is not None:
            by_class = Counter(str(_get(f, "check", "unknown")) for f in findings)
            columns = {_get(f, "column") for f in findings}
            metrics.append(
                Metric(key="quality.findings_total", value=len(findings), unit="findings", direction="lower_better")
            )
            metrics.append(
                Metric(
                    key="quality.columns_with_findings",
                    value=len({c for c in columns if c is not None}),
                    unit="columns",
                    direction="lower_better",
                )
            )
            profile = art.get("profile")
            if profile is not None:
                score = _health_score(profile, findings)
                if score is not None:
                    metrics.append(
                        Metric(key="quality.score", value=score, unit="ratio", direction="higher_better")
                    )
            tables.append(
                AnalysisTable(
                    name="findings_by_class",
                    columns=["class", "count"],
                    rows=[[cls, n] for cls, n in by_class.most_common()],
                )
            )

        manifest = art.get("manifest")
        if manifest is not None:
            records = _get(manifest, "records", []) or []
            metrics.append(
                Metric(
                    key="flow.rows_changed",
                    value=sum(int(_get(r, "affected_rows", 0)) for r in records),
                    unit="rows",
                    direction="neutral",
                )
            )
            metrics.append(
                Metric(key="flow.rules_fired", value=len(records), unit="count", direction="neutral")
            )

        return AnalyzerResult(metrics=metrics, tables=tables)


def _health_score(profile: Any, findings: Any) -> float | None:
    """GoldenCheck ``DatasetProfile.health_score`` normalized to a 0-1 ratio.

    Builds the per-column severity counts the method expects from the findings.
    Returns None if the profile doesn't expose ``health_score``.
    """
    health = getattr(profile, "health_score", None)
    if health is None:
        return None
    by_col: dict[str, dict[str, int]] = {}
    for f in findings:
        col = _get(f, "column")
        if col is None:
            continue
        sev = _severity_name(_get(f, "severity"))
        bucket = by_col.setdefault(col, {"errors": 0, "warnings": 0})
        if sev == "ERROR":
            bucket["errors"] += 1
        elif sev == "WARNING":
            bucket["warnings"] += 1
    try:
        _grade, score = health(findings_by_column=by_col)
    except Exception:
        return None
    return score / 100.0
