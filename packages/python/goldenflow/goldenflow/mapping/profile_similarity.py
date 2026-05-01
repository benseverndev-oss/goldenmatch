from __future__ import annotations

from goldenflow.engine.profiler_bridge import ColumnProfile


def profile_similarity(source: ColumnProfile, target: ColumnProfile) -> float:
    """Score how similar two column profiles are (0.0-1.0)."""
    score = 0.0
    weights = 0.0

    # Type match
    if source.inferred_type == target.inferred_type:
        score += 0.4
    weights += 0.4

    # Null percentage similarity
    null_diff = abs(source.null_pct - target.null_pct)
    score += 0.2 * max(0.0, 1.0 - null_diff)
    weights += 0.2

    # Uniqueness similarity
    unique_diff = abs(source.unique_pct - target.unique_pct)
    score += 0.2 * max(0.0, 1.0 - unique_diff)
    weights += 0.2

    # Cardinality ratio
    if source.unique_count > 0 and target.unique_count > 0:
        ratio = min(source.unique_count, target.unique_count) / max(
            source.unique_count, target.unique_count
        )
        score += 0.2 * ratio
    weights += 0.2

    return score / weights if weights > 0 else 0.0
