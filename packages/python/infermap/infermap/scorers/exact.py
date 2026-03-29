"""Exact name scorer — case-insensitive exact field name match."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class ExactScorer:
    """Returns 1.0 when source and target field names match exactly (case-insensitive)."""

    name: str = "ExactScorer"
    weight: float = 1.0

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult:
        src = source.name.strip().lower()
        tgt = target.name.strip().lower()
        if src == tgt:
            return ScorerResult(score=1.0, reasoning=f"Exact name match: '{source.name}'")
        return ScorerResult(score=0.0, reasoning=f"No exact match: '{source.name}' vs '{target.name}'")
