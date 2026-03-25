from __future__ import annotations

from collections import Counter

import polars as pl
from rapidfuzz import fuzz

from goldenflow.transforms import register_transform


def _build_canonical_map(
    values: list[str | None],
    frequency_threshold: float = 0.05,
    match_threshold: float = 85.0,
) -> dict[str, str]:
    """Build a mapping from variant values to canonical values.

    1. Count case-insensitive frequencies
    2. Values above frequency_threshold are canonical candidates
    3. Low-frequency values fuzzy-matched against candidates
    """
    # Count case-insensitive frequencies
    lower_counts: Counter[str] = Counter()
    case_map: dict[str, Counter[str]] = {}  # lowercase -> Counter of original casings

    for v in values:
        if v is None:
            continue
        v_stripped = v.strip()
        if not v_stripped:
            continue
        low = v_stripped.lower()
        lower_counts[low] += 1
        if low not in case_map:
            case_map[low] = Counter()
        case_map[low][v_stripped] += 1

    total = sum(lower_counts.values())
    if total == 0:
        return {}

    # Determine canonical values: high-frequency case-insensitive groups
    # A value is canonical if its relative frequency exceeds the threshold
    canonical_values: dict[str, str] = {}  # lowercase -> most common casing
    low_freq_values: list[str] = []

    for low, count in lower_counts.items():
        if (count / total) >= frequency_threshold:
            # Most common casing is the canonical form
            canonical_values[low] = case_map[low].most_common(1)[0][0]
        else:
            low_freq_values.append(low)

    # Fuzzy match low-frequency values against canonical ones
    corrections: dict[str, str] = {}

    # First: exact case-insensitive matches (e.g., "ACTIVE" -> "active")
    for low in list(canonical_values.keys()):
        for original_casing in case_map[low]:
            if original_casing != canonical_values[low]:
                corrections[original_casing] = canonical_values[low]

    # Second: fuzzy matches for truly different strings
    canonical_list = list(canonical_values.keys())
    for low in low_freq_values:
        best_score = 0.0
        best_match = ""
        for canon_low in canonical_list:
            score = fuzz.ratio(low, canon_low)
            if score > best_score:
                best_score = score
                best_match = canon_low
        if best_score >= match_threshold:
            # Map all original casings of this low-freq value to the canonical
            for original_casing in case_map[low]:
                corrections[original_casing] = canonical_values[best_match]

    return corrections


@register_transform(
    name="category_auto_correct",
    input_types=["string"],
    auto_apply=True,
    priority=35,
    mode="series",
)
def category_auto_correct(
    series: pl.Series,
    frequency_threshold: float = 0.05,
    match_threshold: float = 85.0,
) -> pl.Series:
    """Auto-correct categorical misspellings and case variants.

    Identifies low-frequency values that fuzzy-match high-frequency canonical
    values and corrects them. No LLM required.
    """
    values = series.to_list()
    corrections = _build_canonical_map(values, frequency_threshold, match_threshold)

    if not corrections:
        return series

    def _correct(val: str | None) -> str | None:
        if val is None:
            return None
        v_stripped = val.strip()
        return corrections.get(v_stripped, v_stripped)

    return series.map_elements(_correct, return_dtype=pl.Utf8)
