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

        if points >= 90: grade = "A"
        elif points >= 80: grade = "B"
        elif points >= 70: grade = "C"
        elif points >= 60: grade = "D"
        else: grade = "F"
        return grade, points
