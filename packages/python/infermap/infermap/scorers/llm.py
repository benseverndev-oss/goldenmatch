"""LLM scorer stub — returns None (abstain)."""
from __future__ import annotations

from infermap.types import FieldInfo, ScorerResult


class LLMScorer:
    """Stub: always abstains."""

    name: str = "LLMScorer"
    weight: float = 0.8

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        return None
