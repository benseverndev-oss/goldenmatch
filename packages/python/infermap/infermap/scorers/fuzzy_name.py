"""Fuzzy name scorer — Jaro-Winkler similarity on normalized field names."""
from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from infermap._native_loader import native_enabled, native_module
from infermap.types import FieldInfo, ScorerResult


def _normalize(name: str) -> str:
    """Strip, lowercase, remove underscores, hyphens, and spaces."""
    return name.strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _fuzzy_name_score(a: str, b: str) -> float:
    if native_enabled("fuzzy_name_score"):
        return native_module().fuzzy_name_score(a, b)
    return _fuzzy_name_score_pure(a, b)


def _fuzzy_name_score_pure(a: str, b: str) -> float:
    """Byte-identical reference for ``infermap-core::fuzzy_name_score`` (normalize + JW)."""
    return JaroWinkler.similarity(_normalize(a), _normalize(b))


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
        similarity = _fuzzy_name_score(src_name, tgt_name)
        # Reasoning stays host: normalize again (idempotent, no muscle) for the message.
        src_norm = _normalize(src_name)
        tgt_norm = _normalize(tgt_name)
        return ScorerResult(
            score=similarity,
            reasoning=(
                f"Jaro-Winkler similarity between '{src_norm}' and '{tgt_norm}': "
                f"{similarity:.3f}"
            ),
        )
