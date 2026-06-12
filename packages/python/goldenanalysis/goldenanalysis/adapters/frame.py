"""The generic ``frame`` adapter — the always-available, zero-suite-dep path.

Imports nothing from other suite packages, so GoldenAnalysis is useful on any
polars DataFrame even with no other Golden package installed.
"""

from __future__ import annotations

import polars as pl

from goldenanalysis.models import AnalyzerInput


class FrameArtifactAdapter:
    """Normalizes a raw polars DataFrame into an ``AnalyzerInput``."""

    def load(self, df: pl.DataFrame, *, dataset: str | None = None) -> AnalyzerInput:
        return AnalyzerInput(frame=df, dataset=dataset or "frame")
