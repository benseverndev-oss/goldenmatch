"""Pattern-type scorer — detects semantic types from sample values via regex."""
from __future__ import annotations

import re

from infermap._native_loader import native_enabled, native_module
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


def _match_types_pure(s: str) -> int:
    """Bitmask oracle for ``infermap-core::pattern_match_types``.

    Bit ``i`` (LSB=0) is set iff ``s`` matches the i-th ``SEMANTIC_TYPES``
    pattern, in insertion order. ``s`` is expected pre-stripped by the caller.
    """
    mask = 0
    for i, pattern in enumerate(_COMPILED.values()):
        if pattern.match(s):
            mask |= 1 << i
    return mask


def _match_types_batch(stripped: list[str]) -> list[int]:
    """Per-sample bitmasks; native kernel when available, pure oracle otherwise."""
    if native_enabled("pattern_match_types"):
        return list(native_module().pattern_match_types(stripped))
    return [_match_types_pure(s) for s in stripped]


def _classify_with_pct(
    field: FieldInfo,
    threshold: float = 0.6,
) -> tuple[str | None, float]:
    """Return (best_type, match_pct) or (None, 0.0) if below threshold or no samples."""
    samples = [
        str(s).strip()
        for s in field.sample_values
        if s is not None and str(s).strip() != ""
    ]
    if not samples:
        return None, 0.0

    masks = _match_types_batch(samples)

    best_type: str | None = None
    best_pct: float = 0.0
    for i, type_name in enumerate(SEMANTIC_TYPES):  # insertion order == bit order
        matches = sum(1 for m in masks if m & (1 << i))
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
