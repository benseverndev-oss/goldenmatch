"""Markdown rendering for ``AnalysisReport``.

Phase 1 renders the title, optional narrative, the metric table, and any embedded
``AnalysisTable``s. The "Δ vs baseline" regression column from Appendix B is
deferred to Phase 2 (it needs ``ReportHistory``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenanalysis.models import AnalysisReport


def _fmt_value(value: float | int | str, unit: str | None) -> str:
    if isinstance(value, float):
        text = f"{value:,.4g}" if value else "0"
    elif isinstance(value, int):
        text = f"{value:,}"
    else:
        text = str(value)
    return f"{text} {unit}" if unit else text


def format_markdown(report: "AnalysisReport") -> str:
    dataset = report.source.get("dataset", "frame")
    lines: list[str] = [f"# Analysis — {dataset} (run {report.run_id})", ""]

    if report.narrative:
        lines += [report.narrative, ""]

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
