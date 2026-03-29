"""Fuzzy name scorer stub — returns None (abstain)."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class FuzzyNameScorer:
    """Stub: always abstains."""

    name: str = "FuzzyNameScorer"
    weight: float = 0.4

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None
