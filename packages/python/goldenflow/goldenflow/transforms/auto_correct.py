from __future__ import annotations

from collections import Counter
from typing import Iterable

import polars as pl
from rapidfuzz import fuzz

from goldenflow.transforms import register_transform


def _build_canonical_map(
    value_counts: Iterable[tuple[str | None, int]],
    frequency_threshold: float = 0.05,
    match_threshold: float = 85.0,
) -> dict[str, str]:
    """Build a mapping from variant values to canonical values.

    1. Count case-insensitive frequencies
    2. Values above frequency_threshold are canonical candidates
    3. Low-frequency values fuzzy-matched against candidates

    Takes (value, count) pairs (typically from a Polars ``value_counts``)
    so the caller can avoid materializing N row-level Python strings on
    large inputs — at 1M+ rows the row-level ``Series.to_list()`` can
    trip a pyo3 panic when PyString allocation fails under memory
    pressure (see goldenflow issue #174 / goldenmatch PR #173).
    """
    # Count case-insensitive frequencies
    lower_counts: Counter[str] = Counter()
    case_map: dict[str, Counter[str]] = {}  # lowercase -> Counter of original casings

    for v, count in value_counts:
        if v is None:
            continue
        v_stripped = v.strip()
        if not v_stripped:
            continue
        low = v_stripped.lower()
        lower_counts[low] += count
        if low not in case_map:
            case_map[low] = Counter()
        case_map[low][v_stripped] += count

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

    Builds the canonical map from a Polars ``value_counts`` aggregation
    rather than ``series.to_list()`` so the work stays O(n_unique) instead
    of O(n_rows). On large inputs this also dodges a pyo3 PanicException
    that can fire when materializing millions of Python strings under
    memory pressure (goldenflow issue #174).
    """
    vc = series.value_counts(sort=True)
    # value_counts returns a 2-column DataFrame: [<series.name>, "count"].
    # iter_rows() yields plain Python tuples; only n_unique tuples are
    # ever produced (no per-row materialization of the source Series).
    pairs: list[tuple[str | None, int]] = list(vc.iter_rows())
    corrections = _build_canonical_map(pairs, frequency_threshold, match_threshold)

    if not corrections:
        return series

    def _correct(val: str | None) -> str | None:
        if val is None:
            return None
        v_stripped = val.strip()
        return corrections.get(v_stripped, v_stripped)

    return series.map_elements(_correct, return_dtype=pl.Utf8)
