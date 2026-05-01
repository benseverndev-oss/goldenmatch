"""Core data models: PipeContext, StageResult, Decision, PipeResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import polars as pl


class StageStatus(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipeStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class Decision:
    """Routing instruction from a stage to the framework."""
    skip: list[str] = field(default_factory=list)
    abort: bool = False
    insert: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class StageResult:
    """Result returned by every stage's run() method."""
    status: StageStatus
    decision: Decision | None = None
    error: str | None = None


@dataclass
class PipeContext:
    """The object flowing through the pipeline. Stages mutate it in place."""
    df: pl.DataFrame | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)
    reasoning: dict[str, str] = field(default_factory=dict)
    stage_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipeResult:
    """Final output returned to the caller."""
    status: PipeStatus
    source: str
    input_rows: int
    stages: dict[str, StageResult] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reasoning: dict[str, str] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        stage_summary = ", ".join(
            f"{name}: {r.status.value}" for name, r in self.stages.items()
        )
        return (
            f"PipeResult(status={self.status.value!r}, source={self.source!r}, "
            f"rows={self.input_rows}, stages=[{stage_summary}])"
        )

    def _repr_html_(self) -> str:
        rows = ""
        for name, r in self.stages.items():
            color = {"success": "green", "skipped": "orange", "failed": "red"}.get(
                r.status.value, "gray"
            )
            rows += (
                f"<tr><td>{name}</td>"
                f"<td style='color:{color}'>{r.status.value}</td>"
                f"<td>{r.error or ''}</td></tr>"
            )
        return (
            f"<table><caption>GoldenPipe: {self.source} "
            f"({self.input_rows} rows) - {self.status.value}</caption>"
            f"<tr><th>Stage</th><th>Status</th><th>Error</th></tr>"
            f"{rows}</table>"
        )
