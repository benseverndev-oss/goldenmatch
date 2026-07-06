"""Exact name scorer — case-insensitive exact field name match."""
from __future__ import annotations

from infermap._native_loader import native_enabled, native_module
from infermap.types import FieldInfo, ScorerResult


def _exact_score(a: str, b: str) -> float:
    if native_enabled("exact_score"):
        return native_module().exact_score(a, b)
    return _exact_score_pure(a, b)


def _exact_score_pure(a: str, b: str) -> float:
    """Byte-identical reference for ``infermap-core::exact_score``."""
    return 1.0 if a.strip().lower() == b.strip().lower() else 0.0


class ExactScorer:
    """Returns 1.0 when source and target field names match exactly (case-insensitive)."""

    name: str = "ExactScorer"
    weight: float = 1.0

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult:
        # Exact scores RAW names (not canonical) -- matches the original behavior.
        if _exact_score(source.name, target.name) == 1.0:
            return ScorerResult(score=1.0, reasoning=f"Exact name match: '{source.name}'")
        return ScorerResult(score=0.0, reasoning=f"No exact match: '{source.name}' vs '{target.name}'")
