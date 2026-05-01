"""Scorer protocol definition."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from infermap.types import FieldInfo, ScorerResult


@runtime_checkable
class Scorer(Protocol):
    """Protocol that all scorers must implement."""

    name: str
    weight: float

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        """Score the similarity between source and target fields.

        Returns None to abstain (scorer has no opinion).
        Returns ScorerResult with score in [0.0, 1.0].
        """
        ...
