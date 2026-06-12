"""Analyzer I/O types: ``AnalyzerInfo``, ``AnalyzerInput``, ``AnalyzerResult``.

``AnalyzerInput`` is what an adapter produces and an analyzer consumes. In Phase 1
the only adapter is the generic ``frame`` adapter, so ``frame`` carries a polars
DataFrame; ``artifacts`` is the forward seam for typed suite artifacts (Phase 2).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from goldenanalysis.models.report import AnalysisTable, Metric


class AnalyzerInfo(BaseModel):
    """Static descriptor of an analyzer: its name and what it consumes/produces."""

    name: str
    consumes: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)


class AnalyzerInput(BaseModel):
    """Normalized input handed to an analyzer's ``run``.

    ``frame`` is the generic polars path (zero suite deps). ``artifacts`` holds
    typed producer outputs once the suite adapters land (Phase 2).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dataset: str = "frame"
    frame: Any = None  # polars.DataFrame for the generic path
    artifacts: dict[str, Any] = Field(default_factory=dict)


class AnalyzerResult(BaseModel):
    """What an analyzer's ``run`` returns: metrics plus optional embedded tables."""

    metrics: list[Metric] = Field(default_factory=list)
    tables: list[AnalysisTable] = Field(default_factory=list)
