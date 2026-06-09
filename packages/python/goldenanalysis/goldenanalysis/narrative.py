"""Narrative generation — a one-paragraph NL summary templated from the flagged
regressions + the largest co-moving metrics across analyzers.

Deterministic; ASCII only (Windows-terminal safe, no em-dash). The root cause in
the spec's worked scenario was only visible by crossing analyzers, so this operates
over the full metric set, not one analyzer's output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenanalysis.models import AnalysisReport, Regression


def _pretty(key: str) -> str:
    return key.split(".")[-1].replace("_", " ")


def _top_finding_class(report: AnalysisReport) -> tuple[str, int] | None:
    for table in report.tables:
        if table.name == "findings_by_class" and table.rows:
            top = max(table.rows, key=lambda r: r[1] if len(r) > 1 and isinstance(r[1], int) else 0)
            return str(top[0]), int(top[1])
    return None


def build_narrative(report: AnalysisReport, regressions: list[Regression] | None = None) -> str:
    regressions = regressions or []
    flagged = [r for r in regressions if r.flagged]

    if not flagged:
        # Neutral summary: the most notable metrics by magnitude.
        notable = sorted(
            (m for m in report.metrics if isinstance(m.value, (int, float))),
            key=lambda m: abs(float(m.value)),
            reverse=True,
        )[:3]
        if not notable:
            return "No metrics to summarize."
        bits = ", ".join(f"{_pretty(m.key)} = {m.value}" for m in notable)
        return f"No regressions flagged. Notable metrics: {bits}."

    worst = max(flagged, key=lambda r: abs(r.delta_pct))
    lead = (
        f"{_pretty(worst.metric).capitalize()} {'fell' if worst.delta_pct < 0 else 'rose'} to "
        f"{worst.current:g} (baseline {worst.baseline:g}; {worst.delta_pct:+.1f}%)."
    )

    comovers = [r for r in flagged if r.metric != worst.metric]
    parts = [lead]
    if comovers:
        moves = "; ".join(
            f"{_pretty(r.metric)} {r.baseline:g} -> {r.current:g} ({r.delta_pct:+.1f}%)" for r in comovers
        )
        parts.append(f"Co-moving signals: {moves}.")
    fc = _top_finding_class(report)
    if fc is not None:
        parts.append(f"Most common quality finding: {fc[0]} ({fc[1]}).")
    return " ".join(parts)
