"""Core data models: PipeContext, StageResult, Decision, PipeResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import polars as pl

from goldenpipe.models.frame import Frame, LocalFrame


class StageStatus(str, Enum):  # noqa: UP042  # explicit str+Enum preserves __str__ semantics; StrEnum behaves differently
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipeStatus(str, Enum):  # noqa: UP042  # explicit str+Enum preserves __str__ semantics; StrEnum behaves differently
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

    @property
    def frame(self) -> Frame | None:
        """The relocatable-stage seam. Returns the engine-resident frame if one is
        set (Phase C -- a ``DuckDBFrame`` left by a remote stage, kept lazy/not
        materialized), else a **zero-copy** ``LocalFrame`` view over ``df``
        (Phase A). ``.polars()`` on either yields the data; on a ``LocalFrame`` it
        is the backing ``df`` by reference (Stage 0 preserved), on an engine frame
        it triggers the boundary materialization. See the relocatable-stage-contract."""
        engine = getattr(self, "_frame", None)
        if engine is not None:
            return engine
        return LocalFrame(self.df) if self.df is not None else None

    @frame.setter
    def frame(self, value: Frame | None) -> None:
        # A ``LocalFrame`` (or None) is canonical in-Python data -> store in ``df``,
        # clear any engine frame. An **engine-resident** frame (``DuckDBFrame``) is
        # kept AS-IS -- NOT materialized -- so a chain of remote stages stays in the
        # engine (Phase C); it materializes to ``df`` only when a local stage needs
        # it (the Runner does that transition) or at egress.
        if value is None:
            self._frame = None
            self.df = None
        elif isinstance(value, LocalFrame):
            self._frame = None
            self.df = value.polars()
        else:
            self._frame = value  # engine-resident: do not materialize here


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
