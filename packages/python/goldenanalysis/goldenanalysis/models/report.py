"""Core report domain types: ``Metric``, ``AnalysisTable``, ``AnalysisReport``.

These are the cross-surface contract. ``schema_version`` anchors it; bumping it
requires a parity-test update on both Python and (later) TypeScript. Metric keys
are dotted and stable — renaming one breaks ``ReportHistory`` comparability, so a
rename is a ``schema_version`` bump.
"""

from __future__ import annotations

from datetime import datetime
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
