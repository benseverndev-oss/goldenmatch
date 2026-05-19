"""Auto-config complexity indicators (v1.10).

Pure functions: each takes a polars DataFrame (and optional config args)
and returns a typed result. No controller state, no I/O. Each function
has a wall-clock budget; on exhaustion, returns None or a sentinel.

Spec: docs/superpowers/specs/2026-05-08-autoconfig-indicators-design.md
"""
from __future__ import annotations

import logging
import re
import time

import polars as pl

from goldenmatch.core.complexity_profile import (
    CollisionSignal,
    ColumnPrior,
    SparsityVerdict,
)

logger = logging.getLogger(__name__)

# Wall-clock budgets (seconds). Indicators returning None on budget
# exhaustion are documented in the spec § Error handling.
BUDGET_COLUMN_PRIORS = 5.0
BUDGET_SPARSE_MATCH = 2.0
BUDGET_FULL_POP_HITS = 15.0
BUDGET_CROSS_BLOCKING = 20.0
BUDGET_CORRUPTION = 3.0

# Identity-column heuristics. Column-name regex -> identity_score floor.
# These are NOT authoritative -- they're priors. Real identity verification
# happens via full-pop matchkey hits.
_IDENTITY_NAME_PATTERNS = [
    (re.compile(r"^(email|e[-_]?mail|email_addr)$", re.I), 0.95),
    (re.compile(r"^(ssn|social|tax_id)$", re.I), 0.95),
    (re.compile(r"^(phone|mobile|tel|telephone)$", re.I), 0.85),
    (re.compile(r"^(id|uuid|guid|user_id|account_id)$", re.I), 0.90),
]

_BOOLEAN_DTYPES = {pl.Boolean}
_NON_IDENTITY_DTYPES = {pl.Boolean, pl.Date, pl.Datetime, pl.Time}


def compute_column_priors(df: pl.DataFrame) -> dict[str, ColumnPrior]:
    """Compute per-column identity + corruption priors.

    identity_score:
      - 0.95 for canonical identity column names (email/ssn/uuid)
      - 0.85 for phone-like
      - 0.7 for high-cardinality strings (cardinality_ratio > 0.5)
      - 0.0 for booleans/dates/categoricals/low-cardinality

    corruption_score:
      - Computed on a 1000-row sample
      - High when within-column edit-distance variance is high (Brian/BRIAN/B.)
      - Low when entries are deterministic (clean email patterns)
    """
    start = time.time()
    if df.is_empty():
        return {}
    priors: dict[str, ColumnPrior] = {}
    sample = df.head(1000) if df.height > 1000 else df

    for col in df.columns:
        if (time.time() - start) > BUDGET_COLUMN_PRIORS:
            logger.info(
                "compute_column_priors: budget %ss exceeded; "
                "remaining %d columns get default priors",
                BUDGET_COLUMN_PRIORS, len(df.columns) - len(priors),
            )
            for remaining in df.columns:
                if remaining not in priors:
                    priors[remaining] = ColumnPrior(0.0, 0.0)
            break

        identity_score = _compute_identity_score(df, col)
        corruption_score = _compute_corruption_score_inline(sample, col)
        priors[col] = ColumnPrior(
            identity_score=identity_score,
            corruption_score=corruption_score,
        )

    return priors


def _compute_identity_score(df: pl.DataFrame, col: str) -> float:
    """Identity-score heuristic. Name match > dtype match > cardinality."""
    if df.schema.get(col) in _NON_IDENTITY_DTYPES:
        return 0.0
    for pattern, score in _IDENTITY_NAME_PATTERNS:
        if pattern.match(col):
            return score
    # High-cardinality string column = id-like
    try:
        n_unique = df[col].n_unique()
        cardinality_ratio = n_unique / max(1, df.height)
        if cardinality_ratio > 0.5:
            return 0.7
        if cardinality_ratio > 0.1:
            return 0.3
    except Exception:
        pass
    return 0.0


def _compute_corruption_score_inline(sample: pl.DataFrame, col: str) -> float:
    """Approximation of within-column edit-distance variance.

    Cheap proxy: fraction of values that are case-or-whitespace-collapsed
    duplicates of another value in the sample. High value -> high
    corruption (Brian/BRIAN/brian/Brian ).
    """
    # Pre-flight budget check: BUDGET_CORRUPTION <= 0.0 means "disabled".
    if BUDGET_CORRUPTION <= 0.0:
        return 0.0
    start = time.time()
    try:
        vals = sample[col].cast(str).fill_null("").to_list()
    except Exception:
        return 0.0
    if (time.time() - start) > BUDGET_CORRUPTION:
        return 0.0
    if not vals:
        return 0.0
    normalized = {v.strip().lower() for v in vals if v}
    raw = {v for v in vals if v}
    if not raw:
        return 0.0
    # Corruption ratio: how many distinct raw forms collapse to fewer
    # normalized forms. 1.0 means perfectly clean (1:1); lower means noise.
    ratio_clean = len(normalized) / len(raw)
    # Invert and clamp: corruption_score = 1.0 - ratio_clean
    return max(0.0, min(1.0, 1.0 - ratio_clean))


def estimate_sparse_match_signal(
    df: pl.DataFrame,
    exact_columns: list[str] | None = None,
    sample_size: int = 1000,
    sparse_threshold: int = 50,
) -> SparsityVerdict:
    """Count exact-matchkey collisions in a sample.

    If `exact_columns` is empty (caller has no exact matchkeys), treat as
    sparse -- controller can't sanity-check otherwise.
    """
    if not exact_columns or df.is_empty():
        return SparsityVerdict(is_sparse=True, estimated_n_true_pairs=0)
    sample = df.head(sample_size) if df.height > sample_size else df
    n_pairs = 0
    for col in exact_columns:
        if col not in sample.columns:
            continue
        try:
            counts = (
                sample.group_by(col)
                .agg(pl.len().alias("n"))
                .filter(pl.col("n") > 1)
            )
            # Each group of size k contributes k*(k-1)/2 pairs
            n_pairs += int(
                counts.select(
                    (pl.col("n") * (pl.col("n") - 1) / 2).sum()
                ).item()
            )
        except Exception:
            continue
    is_sparse = n_pairs < sparse_threshold
    return SparsityVerdict(is_sparse=is_sparse, estimated_n_true_pairs=n_pairs)


def compute_corruption_score(df: pl.DataFrame, col: str) -> float:
    """Public API for per-column corruption score (case/whitespace noise).

    See _compute_corruption_score_inline for the heuristic.
    """
    if col not in df.columns or df.is_empty():
        return 0.0
    sample = df.head(1000) if df.height > 1000 else df
    return _compute_corruption_score_inline(sample, col)


def estimate_full_pop_hits(df: pl.DataFrame, blocking_col: str) -> int | None:
    """Count exact-match collisions on the full population.

    Returns None on budget exhaustion. Used by indicator-aware rules
    to validate that v0's blocking key has structural signal even when
    sample's mass_above_threshold == 0.
    """
    # Pre-flight budget check: BUDGET_FULL_POP_HITS=0.0 means "disabled".
    if BUDGET_FULL_POP_HITS <= 0.0:
        logger.info("estimate_full_pop_hits: budget is zero; returning None")
        return None
    start = time.time()
    if blocking_col not in df.columns or df.is_empty():
        return 0
    if (time.time() - start) > BUDGET_FULL_POP_HITS:
        return None
    try:
        counts = (
            df.group_by(blocking_col)
            .agg(pl.len().alias("n"))
            .filter(pl.col("n") > 1)
        )
        if (time.time() - start) > BUDGET_FULL_POP_HITS:
            return None
        n_pairs = int(
            counts.select(
                (pl.col("n") * (pl.col("n") - 1) / 2).sum()
            ).item()
        )
        return n_pairs
    except Exception as exc:
        logger.warning("estimate_full_pop_hits failed: %s", exc)
        return None


def compute_cross_blocking_overlap(
    df: pl.DataFrame, key_a: str, key_b: str,
) -> float | None:
    """Fraction of (record_i, record_j) pairs that are co-blocked under
    BOTH key_a AND key_b out of all pairs co-blocked under EITHER.

    overlap = |co_a INTERSECTION co_b| / |co_a UNION co_b|

    Returns 1.0 if key_a == key_b (degenerate). Returns None on budget.
    Used by rule_cross_blocking_disagreement: low overlap indicates
    blocking key is genuinely capturing wrong candidates (orthogonal
    keys agree on nothing) vs. just-too-few-matches.
    """
    if key_a == key_b:
        return 1.0
    # Pre-flight budget check: BUDGET_CROSS_BLOCKING=0.0 means "disabled".
    if BUDGET_CROSS_BLOCKING <= 0.0:
        logger.info("compute_cross_blocking_overlap: budget is zero; returning None")
        return None
    start = time.time()
    if key_a not in df.columns or key_b not in df.columns or df.is_empty():
        return None
    if (time.time() - start) > BUDGET_CROSS_BLOCKING:
        return None
    try:
        # Co-blocked under key_a: pairs sharing same key_a value
        df_indexed = df.with_row_index("__row__")
        a_pairs = (
            df_indexed.group_by(key_a)
            .agg(pl.col("__row__").alias("rows"))
            .filter(pl.col("rows").list.len() > 1)
        )
        b_pairs = (
            df_indexed.group_by(key_b)
            .agg(pl.col("__row__").alias("rows"))
            .filter(pl.col("rows").list.len() > 1)
        )

        if (time.time() - start) > BUDGET_CROSS_BLOCKING:
            return None

        # Build pair sets (small enough to materialize as Python sets
        # within budget; if too big, return None defensively)
        def _pairs_set(grouped: pl.DataFrame) -> set[tuple[int, int]] | None:
            pairs: set[tuple[int, int]] = set()
            for rows in grouped["rows"].to_list():
                rows = sorted(rows)
                for i in range(len(rows)):
                    for j in range(i + 1, len(rows)):
                        pairs.add((rows[i], rows[j]))
                        if (time.time() - start) > BUDGET_CROSS_BLOCKING:
                            return None
            return pairs

        set_a = _pairs_set(a_pairs)
        if set_a is None:
            return None
        set_b = _pairs_set(b_pairs)
        if set_b is None:
            return None

        union = set_a | set_b
        if not union:
            return 1.0  # no co-blocked pairs at all -> degenerate, treat as match
        intersection = set_a & set_b
        return len(intersection) / len(union)
    except Exception as exc:
        logger.warning("compute_cross_blocking_overlap failed: %s", exc)
        return None


def dispatch_compute_column_priors(df_or_ds):
    """Route to in-memory or distributed compute_column_priors by input type."""
    from goldenmatch.distributed import is_ray_dataset
    if is_ray_dataset(df_or_ds):
        from goldenmatch.distributed.indicators import compute_column_priors_distributed
        return compute_column_priors_distributed(df_or_ds)
    return compute_column_priors(df_or_ds)


def dispatch_estimate_sparse_match_signal(df_or_ds, *, exact_columns):
    from goldenmatch.distributed import is_ray_dataset
    if is_ray_dataset(df_or_ds):
        from goldenmatch.distributed.indicators import estimate_sparse_match_signal_distributed
        return estimate_sparse_match_signal_distributed(df_or_ds, exact_columns=exact_columns)
    return estimate_sparse_match_signal(df_or_ds, exact_columns=exact_columns)


BUDGET_COLLISION = 8.0


def compute_identity_collision_signal(
    df: pl.DataFrame,
    identity_col: str,
    witness_cols: list[str],
) -> CollisionSignal:
    """Detect whether an identity column is shared across distinct entities.

    For each multi-record group (rows sharing the same `identity_col` value),
    compute the max pairwise divergence (1 - similarity) on `witness_cols`.
    Returns the fraction of multi-record groups where max-divergence > 0.5.

    A high rate indicates the identity column is NOT a reliable identity
    anchor (T3's adversarial pattern: same email used for distinct people
    with different addresses, phones, cities).

    Budget: BUDGET_COLLISION seconds. On exhaustion, returns
    CollisionSignal(rate=0.0, witness_used="") sentinel.
    """
    start = time.time()
    if BUDGET_COLLISION <= 0.0:
        return CollisionSignal(rate=0.0, witness_used="")
    if not witness_cols or df.is_empty() or identity_col not in df.columns:
        return CollisionSignal(rate=0.0, witness_used="")
    valid_witnesses = [c for c in witness_cols if c in df.columns]
    if not valid_witnesses:
        return CollisionSignal(rate=0.0, witness_used="")

    try:
        # Group by identity_col; only multi-record groups matter
        groups = (
            df.group_by(identity_col)
            .agg(pl.len().alias("__n__"))
            .filter(pl.col("__n__") > 1)
        )
        if (time.time() - start) > BUDGET_COLLISION:
            return CollisionSignal(rate=0.0, witness_used="")
        if groups.is_empty():
            return CollisionSignal(rate=0.0, witness_used="")

        n_groups = groups.height
        n_high_divergence = 0
        winning_witness = ""
        max_observed_div = 0.0

        # Use rapidfuzz for similarity computation
        from rapidfuzz import fuzz

        for group_value in groups[identity_col].to_list():
            if (time.time() - start) > BUDGET_COLLISION:
                return CollisionSignal(rate=0.0, witness_used="")
            group_df = df.filter(pl.col(identity_col) == group_value)
            n = group_df.height
            if n < 2:
                continue
            max_div_in_group = 0.0
            for witness in valid_witnesses:
                vals = group_df[witness].cast(str).fill_null("").to_list()
                # max pairwise divergence
                for i in range(n):
                    for j in range(i + 1, n):
                        sim = fuzz.token_sort_ratio(vals[i], vals[j]) / 100.0
                        div = 1.0 - sim
                        if div > max_div_in_group:
                            max_div_in_group = div
                            if div > max_observed_div:
                                max_observed_div = div
                                winning_witness = witness
            if max_div_in_group > 0.5:
                n_high_divergence += 1

        rate = n_high_divergence / n_groups if n_groups > 0 else 0.0
        return CollisionSignal(rate=rate, witness_used=winning_witness)
    except Exception as exc:
        logger.warning("compute_identity_collision_signal failed: %s", exc)
        return CollisionSignal(rate=0.0, witness_used="")
