"""GoldenAnalysis domain models."""

from __future__ import annotations

from goldenanalysis.models.analyzer import AnalyzerInfo, AnalyzerInput, AnalyzerResult
from goldenanalysis.models.report import (
    AnalysisReport,
    AnalysisTable,
    Direction,
    Metric,
)

__all__ = [
    "Metric",
    "AnalysisTable",
    "AnalysisReport",
    "Direction",
    "AnalyzerInfo",
    "AnalyzerInput",
    "AnalyzerResult",
]
