"""Markdown rendering for ``AnalysisReport``.

Renders the title, optional narrative, the metric table, and any embedded
``AnalysisTable``s. When ``regressions`` are supplied (Phase 2b), a flagged-
regression callout and a "Δ vs baseline" column are added; without them the output
is byte-identical to the Phase 1 form.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenanalysis.models import AnalysisReport, Regression


def _fmt_value(value: float | int | str, unit: str | None) -> str:
    if isinstance(value, float):
        text = f"{value:,.4g}" if value else "0"
    elif isinstance(value, int):
        text = f"{value:,}"
    else:
        text = str(value)
    return f"{text} {unit}" if unit else text


def format_markdown(report: AnalysisReport, regressions: list[Regression] | None = None) -> str:
    dataset = report.source.get("dataset", "frame")
    lines: list[str] = [f"# Analysis — {dataset} (run {report.run_id})", ""]

    by_metric: dict[str, Regression] = {r.metric: r for r in (regressions or [])}
    flagged = [r for r in (regressions or []) if r.flagged]
    if flagged:
        lead = "; ".join(
            f"{r.metric} {r.baseline:g} -> {r.current:g} ({r.delta_pct:+.1f}%)" for r in flagged
        )
        lines += [f"> WARNING: {len(flagged)} regression(s) flagged. {lead}", ""]

    if report.narrative:
        lines += [report.narrative, ""]

    if by_metric:
        lines += ["| Metric | Value | Δ vs baseline |", "|---|---|---|"]
        for m in report.metrics:
            reg = by_metric.get(m.key)
            if reg is None:
                delta = ""
            else:
                mark = "🔴 " if reg.flagged else ""
                delta = f"{mark}{reg.delta_pct:+.1f}%"
            lines.append(f"| {m.key} | {_fmt_value(m.value, m.unit)} | {delta} |")
    else:
        lines += ["| Metric | Value |", "|---|---|"]
        for m in report.metrics:
            lines.append(f"| {m.key} | {_fmt_value(m.value, m.unit)} |")
    lines.append("")

    for table in report.tables:
        lines.append(f"**{table.name}**")
        lines.append("")
        lines.append("| " + " | ".join(table.columns) + " |")
        lines.append("|" + "|".join("---" for _ in table.columns) + "|")
        for row in table.rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
