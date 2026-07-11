"""Numeric helpers for profile emission. Used by scorer + cluster instrumentation."""
from __future__ import annotations

from typing import Any


def histogram_20(scores: list[float]) -> list[int]:
    """20 fixed bins over [0, 1]. Score >= 1.0 lands in bin 19."""
    bins = [0] * 20
    for s in scores:
        idx = min(19, max(0, int(s * 20)))
        bins[idx] += 1
    return bins


def hartigan_dip(scores: list[float]) -> float:
    """Hartigan's dip statistic. Returns value in [0, 0.25]; small=unimodal.

    Hard-requires the ``diptest`` package (added as dep in Task 2.2).
    """
    if not scores:
        return 0.0
    import diptest
    import numpy as np
    # diptest.dipstat overload returns float | tuple[float, dict]; the no-extra
    # path returns float at runtime, but the stub union confuses pyright.
    return float(diptest.dipstat(np.asarray(scores)))  # pyright: ignore[reportArgumentType]  # diptest stub returns union; runtime is always float in the no-pval branch


def mass_above(scores: list[float], threshold: float) -> float:
    if not scores:
        return 0.0
    return sum(1 for s in scores if s >= threshold) / len(scores)


def mass_borderline(scores: list[float], threshold: float, band: float = 0.1) -> float:
    if not scores:
        return 0.0
    lo, hi = threshold - band, threshold + band
    return sum(1 for s in scores if lo <= s <= hi) / len(scores)


# Minimum in-cluster triple count for a transitivity estimate to count as
# evidence. Below this, the rate is returned as vacuously 1.0 (not
# evaluable) -- systemic non-transitivity at real scale always has support.
_MIN_TRIPLE_SUPPORT = 30


def transitivity_rate(
    members_by_cluster: dict[int, list[int]],
    pair_scores: dict[tuple[int, int], float],
    threshold: float,
    *,
    max_samples: int = 1000,
    seed: int = 0,
) -> float:
    """Fraction of in-cluster (a,b,c) triples where all three of (a,b), (b,c),
    (a,c) score >= threshold.

    Pair lookup canonicalizes (a,b) as (min,max) per project convention.
    Returns 1.0 when no clusters have >= 3 members (vacuously transitive),
    AND when fewer than ``_MIN_TRIPLE_SUPPORT`` triples exist -- a rate
    estimated from a handful of triples is sampling noise, not a signal.
    Without the support floor, small controller samples on typo-heavy person
    data hard-RED the cluster verdict on 2-of-13-triangle noise, the
    controller reacts by lowering thresholds, borderline pairs multiply,
    transitivity gets WORSE, and zero-config death-spirals to
    BUDGET_ITERATIONS (the 2026-07-10 bench regression's terminal layer).
    Samples up to ``max_samples`` triples for cost control.
    """
    import random
    from itertools import combinations as _combinations

    rng = random.Random(seed)
    triples: list[tuple[int, int, int]] = []
    for members in members_by_cluster.values():
        if len(members) < 3:
            continue
        n = len(members)
        if n <= 20:
            triples.extend(_combinations(sorted(members), 3))
        else:
            for _ in range(min(max_samples, 100)):
                a, b, c = sorted(rng.sample(members, 3))
                triples.append((a, b, c))
    if len(triples) < _MIN_TRIPLE_SUPPORT:
        # Too few triples for the rate to mean anything (includes the
        # no-triples vacuous case). See docstring.
        return 1.0
    if len(triples) > max_samples:
        triples = rng.sample(triples, max_samples)

    def edge(x: int, y: int) -> float:
        return pair_scores.get((min(x, y), max(x, y)), 0.0)

    agree = sum(
        1 for a, b, c in triples
        if edge(a, b) >= threshold
        and edge(b, c) >= threshold
        and edge(a, c) >= threshold
    )
    return agree / len(triples)


def data_profile_column_stats(
    df: Any,  # pl.DataFrame (today) or any Frame-coercible
    user_cols: list[str],
) -> tuple[
    dict[str, str], dict[str, float], dict[str, float], dict[str, int], dict[str, int]
]:
    """Shared body of autoconfig._emit_data_profile and
    autoconfig_controller._compute_data_profile_from_df (W3c: the twins were
    byte-identical -- one seam-routed implementation retires the mirror,
    the W2c _cross_source_filter_df pattern).

    Returns (column_types, cardinality_ratio, null_rate, p50, p99).
    column_types keeps the LEGACY tag set {text, numeric, date, unknown}:
    the old dtype substring chain had no bool branch, so Boolean maps
    "unknown" here even though semantic_dtype knows better -- byte-identical
    verdicts beat tidiness until a deliberate re-bless.
    """
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)
    n_rows = frame.height
    column_types: dict[str, str] = {}
    cardinality_ratio: dict[str, float] = {}
    null_rate: dict[str, float] = {}
    value_length_p50: dict[str, int] = {}
    value_length_p99: dict[str, int] = {}
    for col in user_cols:
        ser = frame.column(col)
        non_null = ser.drop_nulls()
        n_non_null = len(non_null)
        cardinality_ratio[col] = (non_null.n_unique() / n_non_null) if n_non_null else 0.0
        null_rate[col] = 1 - (n_non_null / n_rows) if n_rows else 0.0
        tag = ser.semantic_dtype()
        column_types[col] = tag if tag in ("text", "numeric", "date") else "unknown"
        if column_types[col] == "text" and n_non_null:
            try:
                lens = sorted(non_null.cast_str().str_len_chars().to_list())
                if lens:
                    value_length_p50[col] = int(lens[len(lens) // 2])
                    value_length_p99[col] = int(lens[max(0, int(0.99 * len(lens)) - 1)])
            except Exception:
                pass
    return column_types, cardinality_ratio, null_rate, value_length_p50, value_length_p99
