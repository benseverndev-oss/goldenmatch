"""Base profiler interface."""
from __future__ import annotations
from abc import ABC, abstractmethod
import polars as pl
from goldencheck.models.finding import Finding

class BaseProfiler(ABC):
    @abstractmethod
    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        ...
