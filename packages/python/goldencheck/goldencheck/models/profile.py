"""Profile models — column and dataset profiles."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class ColumnProfile:
    name: str
    inferred_type: str
    null_count: int
    null_pct: float
    unique_count: int
    unique_pct: float
    row_count: int
    min_value: str | None = None
    max_value: str | None = None
    mean: float | None = None
    stddev: float | None = None
    top_values: list[tuple[str, int]] = field(default_factory=list)
    detected_format: str | None = None
    detected_patterns: list[tuple[str, float]] = field(default_factory=list)
    enum_values: list[str] | None = None

@dataclass
class DatasetProfile:
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]

    def health_score(
        self,
        findings_by_column: dict[str, dict[str, int]] | None = None,
        errors: int = 0,
        warnings: int = 0,
    ) -> tuple[str, int]:
        """Calculate health score with per-column cap of -20."""
        if findings_by_column:
            total_deduction = 0
            for col_data in findings_by_column.values():
                col_deduction = (col_data.get("errors", 0) * 10) + (col_data.get("warnings", 0) * 3)
                total_deduction += min(col_deduction, 20)
            points = max(100 - total_deduction, 0)
        else:
            points = 100 - (errors * 10) - (warnings * 3)
            points = max(points, 0)

        if points >= 90:
            grade = "A"
        elif points >= 80:
            grade = "B"
        elif points >= 70:
            grade = "C"
        elif points >= 60:
            grade = "D"
        else:
            grade = "F"
        return grade, points

    def _repr_html_(self) -> str:
        colors = {"A": "#00ff00", "B": "#7fff00", "C": "#ffff00", "D": "#ff7f00", "F": "#ff0000"}
        grade, score = self.health_score()
        color = colors.get(grade, "#888")
        badge = (
            f'<span style="background:{color};color:#000;padding:2px 8px;'
            f'border-radius:4px;font-weight:bold">{grade} ({score})</span>'
        )
        rows = ""
        for c in self.columns:
            top = ", ".join(f"{v}({n})" for v, n in c.top_values[:3]) if c.top_values else ""
            rows += (
                f'<tr><td style="font-weight:bold">{c.name}</td>'
                f'<td>{c.inferred_type}</td>'
                f'<td style="text-align:right">{c.null_pct:.1f}%</td>'
                f'<td style="text-align:right">{c.unique_pct:.1f}%</td>'
                f'<td style="color:#888;font-size:0.85em">{top}</td></tr>'
            )
        return (
            f'<div style="font-family:monospace;font-size:13px">'
            f'<div style="margin-bottom:8px"><strong>{self.file_path}</strong> &mdash; '
            f'{self.row_count:,} rows, {self.column_count} columns &mdash; {badge}</div>'
            f'<table style="border-collapse:collapse;width:100%">'
            f'<thead><tr style="border-bottom:2px solid #444;text-align:left">'
            f'<th>Column</th><th>Type</th><th>Null%</th><th>Unique%</th><th>Top Values</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>'
        )
