"""Base profiler interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from goldencheck._polars_lazy import pl
from goldencheck.models.finding import Finding


class BaseProfiler(ABC):
    @abstractmethod
    def profile(self, frame: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        ...
