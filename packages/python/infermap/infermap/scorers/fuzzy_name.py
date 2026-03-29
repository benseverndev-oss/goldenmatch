"""Fuzzy name scorer — Jaro-Winkler similarity on normalized field names."""
from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from infermap.types import FieldInfo, ScorerResult


def _normalize(name: str) -> str:
    """Strip, lowercase, remove underscores, hyphens, and spaces."""
    return name.strip().lower().replace("_", "").replace("-", "").replace(" ", "")


class FuzzyNameScorer:
    """Scores field name similarity using Jaro-Winkler on normalized names."""

    name: str = "FuzzyNameScorer"
    weight: float = 0.4

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult:
        src_norm = _normalize(source.name)
        tgt_norm = _normalize(target.name)
        similarity = JaroWinkler.similarity(src_norm, tgt_norm)
        return ScorerResult(
            score=similarity,
            reasoning=(
                f"Jaro-Winkler similarity between '{src_norm}' and '{tgt_norm}': "
                f"{similarity:.3f}"
            ),
        )
