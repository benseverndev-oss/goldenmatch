"""Pattern-type scorer stub — returns None (abstain)."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class PatternTypeScorer:
    """Stub: always abstains."""

    name: str = "PatternTypeScorer"
    weight: float = 0.7

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None
