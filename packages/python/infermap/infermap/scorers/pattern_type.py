"""Pattern-type scorer — detects semantic types from sample values via regex."""
from __future__ import annotations

import re

from infermap.types import FieldInfo, ScorerResult

# Ordered dict — earlier entries take precedence when multiple patterns match
SEMANTIC_TYPES: dict[str, str] = {
    "email": r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "date_iso": r"^\d{4}-\d{2}-\d{2}$",
    "ip_v4": r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",
    "url": r"^https?://[^\s]+$",
    "phone": r"^[\+\d]?(\d[\s\-\.]?){7,14}\d$",
    "zip_us": r"^\d{5}(-\d{4})?$",
    "currency": r"^[\$\£\€]\s?\d[\d,]*(\.\d{1,2})?$",
}

_COMPILED: dict[str, re.Pattern] = {
    name: re.compile(pattern) for name, pattern in SEMANTIC_TYPES.items()
}


def _classify_with_pct(
    field: FieldInfo,
    threshold: float = 0.6,
) -> tuple[str | None, float]:
    """Return (best_type, match_pct) or (None, 0.0) if below threshold or no samples."""
    samples = [s for s in field.sample_values if s is not None and str(s).strip() != ""]
    if not samples:
        return None, 0.0

    best_type: str | None = None
    best_pct: float = 0.0

    for type_name, pattern in _COMPILED.items():
        matches = sum(1 for s in samples if pattern.match(str(s).strip()))
        pct = matches / len(samples)
        if pct > best_pct:
            best_pct = pct
            best_type = type_name

    if best_type is not None and best_pct >= threshold:
        return best_type, best_pct
    return None, 0.0


def classify_field(field: FieldInfo, threshold: float = 0.6) -> str | None:
    """Return the best matching semantic type name or None."""
    sem_type, _ = _classify_with_pct(field, threshold)
    return sem_type


class PatternTypeScorer:
    """Scores fields by comparing their detected semantic types from sample values."""

    name: str = "PatternTypeScorer"
    weight: float = 0.7

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        src_samples = [s for s in source.sample_values if s is not None and str(s).strip() != ""]
        tgt_samples = [s for s in target.sample_values if s is not None and str(s).strip() != ""]

        # Abstain if no samples on either side
        if not src_samples or not tgt_samples:
            return None

        src_type, src_pct = _classify_with_pct(source)
        tgt_type, tgt_pct = _classify_with_pct(target)

        # Samples exist but no type classified for either field
        if src_type is None and tgt_type is None:
            return ScorerResult(
                score=0.0,
                reasoning="No semantic type detected in either field's samples",
            )

        # One side has a type, the other doesn't — treat as a mismatch
        if src_type != tgt_type:
            return ScorerResult(
                score=0.0,
                reasoning=(
                    f"Semantic type mismatch: source={src_type!r} vs target={tgt_type!r}"
                ),
            )

        # Same type — score = min of both match percentages
        combined = min(src_pct, tgt_pct)
        return ScorerResult(
            score=combined,
            reasoning=(
                f"Both fields classified as '{src_type}' "
                f"(src={src_pct:.0%}, tgt={tgt_pct:.0%})"
            ),
        )
