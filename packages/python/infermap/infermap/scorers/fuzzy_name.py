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
        # Prefer canonical names (schema-wide common affixes stripped) so e.g.
        # `City` vs `City` wins over `City` vs `prospectcity`. Falls back to
        # raw name when MapEngine hasn't populated canonical_name.
        src_name = source.canonical_name or source.name
        tgt_name = target.canonical_name or target.name
        src_norm = _normalize(src_name)
        tgt_norm = _normalize(tgt_name)
        similarity = JaroWinkler.similarity(src_norm, tgt_norm)
        return ScorerResult(
            score=similarity,
            reasoning=(
                f"Jaro-Winkler similarity between '{src_norm}' and '{tgt_norm}': "
                f"{similarity:.3f}"
            ),
        )
