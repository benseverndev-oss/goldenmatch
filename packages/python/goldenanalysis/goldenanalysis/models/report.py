"""Core report domain types: ``Metric``, ``AnalysisTable``, ``AnalysisReport``.

These are the cross-surface contract. ``schema_version`` anchors it; bumping it
requires a parity-test update on both Python and (later) TypeScript. Metric keys
are dotted and stable — renaming one breaks ``ReportHistory`` comparability, so a
rename is a ``schema_version`` bump.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Direction = Literal["higher_better", "lower_better", "neutral"]


class Metric(BaseModel):
    """A single named measurement over an artifact."""

    key: str  # dotted, stable: "frame.row_count", "match.recall_safe_bound"
    value: float | int | str
    unit: str | None = None  # "rows", "ratio", "ms", None
    direction: Direction = "neutral"


class AnalysisTable(BaseModel):
    """A small, report-embeddable table. Large tables go to a Parquet sidecar."""

    name: str  # e.g. "per_column"
    columns: list[str]
    rows: list[list[Any]]


class AnalysisReport(BaseModel):
    """The unified, exportable output of one analysis run."""

    schema_version: int = 1
    run_id: str
    generated_at: datetime
    source: dict[str, str] = Field(default_factory=dict)
    metrics: list[Metric] = Field(default_factory=list)
    tables: list[AnalysisTable] = Field(default_factory=list)
    narrative: str | None = None
    analyzers_run: list[str] = Field(default_factory=list)

    # --- exporters -------------------------------------------------------

    def to_json(self, path: str | Path | None = None, *, indent: int = 2) -> str:
        """Serialize to JSON. Writes to ``path`` if given; always returns the text."""
        text = self.model_dump_json(indent=indent)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    @classmethod
    def from_json(cls, data: str | bytes) -> AnalysisReport:
        """Parse a report back from its JSON form (lossless round-trip)."""
        return cls.model_validate_json(data)

    def to_markdown(self, regressions: list[Any] | None = None) -> str:
        """Render a human-readable Markdown report.

        When ``regressions`` (a list of ``Regression``) is supplied, a flagged-
        regression callout and a Δ-vs-baseline column are added; otherwise the
        output is the plain Phase-1 form.
        """
        from goldenanalysis.render import format_markdown

        return format_markdown(self, regressions)

    def to_parquet(self, path: str | Path) -> Path:
        """Write the long-form metric frame (key/value/unit/direction).

        Each embedded table is written as a ``<path>.<table_name>.parquet`` sidecar.
        ``value`` is stored as text so the single column stays well-typed across the
        mixed int/float/str metric values; the JSON form is the lossless one.
        """
        import polars as pl

        path = Path(path)
        pl.DataFrame(
            {
                "key": [m.key for m in self.metrics],
                "value": [str(m.value) for m in self.metrics],
                "unit": [m.unit for m in self.metrics],
                "direction": [m.direction for m in self.metrics],
            },
            schema={"key": pl.String, "value": pl.String, "unit": pl.String, "direction": pl.String},
        ).write_parquet(path)

        for table in self.tables:
            sidecar = path.with_name(f"{path.name}.{table.name}.parquet")
            pl.DataFrame(
                {col: [row[i] for row in table.rows] for i, col in enumerate(table.columns)}
                if table.rows
                else {col: [] for col in table.columns}
            ).write_parquet(sidecar)

        return path
