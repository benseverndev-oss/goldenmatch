"""Generic comparison-feature extractor for the band-override student.

Domain-agnostic by design (the transfer thesis): the student learns *how to
combine similarity signals*, not entities. For each shared field we emit
[jaro_winkler, token_sort, exact, both_present]; plus the FS score. This fixed,
order-invariant vector is what lets a centrally-trained student transfer to
unseen user data (spec 2026-07-31).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rapidfuzz.distance import JaroWinkler
from rapidfuzz.fuzz import token_sort_ratio

# Per-field feature names (documented so the vector layout is stable + inspectable).
PER_FIELD = ("jw", "token_sort", "exact", "both_present")


def field_features(a: str, b: str) -> list[float]:
    """The 4 per-field comparison signals for two cell values."""
    a = a or ""
    b = b or ""
    return [
        JaroWinkler.normalized_similarity(a, b),
        token_sort_ratio(a, b) / 100.0,
        1.0 if a and b and a == b else 0.0,
        1.0 if a and b else 0.0,
    ]


def band_features(
    row_a: Mapping[str, Any],
    row_b: Mapping[str, Any],
    fields: Sequence[str],
    fs_score: float,
) -> list[float]:
    """Full feature vector for a candidate pair: per-field signals + FS score.

    ``fields`` fixes the layout; a missing key reads as empty (both_present=0),
    so schema normalization = agreeing on ``fields`` across datasets.
    """
    vec: list[float] = []
    for f in fields:
        vec.extend(field_features(_cell(row_a, f), _cell(row_b, f)))
    vec.append(float(fs_score))
    return vec


def feature_names(fields: Sequence[str]) -> list[str]:
    """Human-readable name per feature dimension (for explainability / attribution)."""
    names = [f"{f}.{p}" for f in fields for p in PER_FIELD]
    names.append("fs_score")
    return names


def _cell(row: Mapping[str, Any], field: str) -> str:
    v = row.get(field)
    return "" if v is None else str(v)
