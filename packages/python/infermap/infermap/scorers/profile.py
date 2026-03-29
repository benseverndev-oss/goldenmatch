"""Profile scorer stub — returns None (abstain)."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class ProfileScorer:
    """Stub: always abstains."""

    name: str = "ProfileScorer"
    weight: float = 0.5

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None
