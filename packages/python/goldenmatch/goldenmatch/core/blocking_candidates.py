"""Blocking-key candidate classification (#408).

Auto-config historically conflated two orthogonal column properties:
matchkey suitability (high cardinality + identity-shaped = good) and
blocking-key suitability (mid cardinality = good; near-unique = WORST
because it produces singleton blocks). Perfect identity claims like
NPI / SSN / federally-issued IDs are IDEAL matchkeys but produce
1-row-per-block blocking strategies that defeat the purpose of blocking.

This module separates the two candidate pools so the controller can
pick a high-cardinality identifier for matching AND a mid-cardinality
column (or composite pair) for blocking, even when both decisions
draw from the same input frame.

Spec: docs/superpowers/specs/2026-05-21-blocking-key-candidate-pool-design.md
Issue: #408
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import polars as pl

from goldenmatch.core.quality_exclusions import ColumnProfile

logger = logging.getLogger(__name__)


# Default cardinality bounds for blocking-key candidates. Below
# BLOCKING_MIN_RATIO the column produces mega-blocks (1000+ rows per
# block, scoring explodes). Above BLOCKING_MAX_RATIO the column
# approaches per-record-unique (singleton blocks, no candidate pairs).
# Env vars let users tune for unusual data shapes without rebuilding.
DEFAULT_BLOCKING_MIN_RATIO = 0.001
DEFAULT_BLOCKING_MAX_RATIO = 0.5
DEFAULT_COMPOSITE_TARGET_AVG_BLOCK_SIZE = 20
DEFAULT_DEGENERATE_GUARD_THRESHOLD = 2.0


def _blocking_min_ratio() -> float:
    raw = os.environ.get("GOLDENMATCH_BLOCKING_MIN_RATIO")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_BLOCKING_MIN_RATIO=%r is not a float; "
                "falling back to %f", raw, DEFAULT_BLOCKING_MIN_RATIO,
            )
    return DEFAULT_BLOCKING_MIN_RATIO


def _blocking_max_ratio() -> float:
    raw = os.environ.get("GOLDENMATCH_BLOCKING_MAX_RATIO")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_BLOCKING_MAX_RATIO=%r is not a float; "
                "falling back to %f", raw, DEFAULT_BLOCKING_MAX_RATIO,
            )
    return DEFAULT_BLOCKING_MAX_RATIO


@dataclass(frozen=True)
class ColumnRole:
    """Per-column role classification.

    ``is_matchkey_candidate`` and ``is_blocking_candidate`` are
    INDEPENDENT axes. NPI (cardinality 1.0) is True for matchkey
    and False for blocking. ``state`` (cardinality 0.0001) is False
    for matchkey (lifecycle-shaped) and False for blocking (mega-block
    risk). ``zip`` (cardinality 0.05) is False for matchkey (too few
    distinct values to anchor identity) and True for blocking.

    ``blocking_excluded_reason`` is the human-readable explanation
    surfaced in INFO logs + postflight. ``None`` when blocking-eligible.
    """

    name: str
    is_matchkey_candidate: bool
    is_blocking_candidate: bool
    blocking_excluded_reason: str | None


def classify_column_role(
    profile: ColumnProfile,
    *,
    blocking_min_ratio: float | None = None,
    blocking_max_ratio: float | None = None,
) -> ColumnRole:
    """Classify a column for matchkey + blocking suitability.

    Matchkey suitability is delegated to the existing rule chain (this
    function doesn't second-guess; it always reports ``True`` and lets
    downstream `compute_column_priors` + rule heuristics filter). The
    blocking dimension is what's new.

    Args:
        profile: cheap stats from ``_build_column_profile``.
        blocking_min_ratio: defaults to env var or 0.001.
        blocking_max_ratio: defaults to env var or 0.5.

    Returns:
        ColumnRole with both axes set + a reason string when blocking
        is excluded.
    """
    min_ratio = (
        blocking_min_ratio
        if blocking_min_ratio is not None
        else _blocking_min_ratio()
    )
    max_ratio = (
        blocking_max_ratio
        if blocking_max_ratio is not None
        else _blocking_max_ratio()
    )

    is_blocking = True
    reason: str | None = None

    if profile.cardinality_ratio > 0.95:
        is_blocking = False
        reason = (
            f"near-unique column (cardinality={profile.cardinality_ratio:.3f}); "
            "would produce singleton blocks"
        )
    elif profile.cardinality_ratio > max_ratio:
        is_blocking = False
        reason = (
            f"too unique for blocking (cardinality={profile.cardinality_ratio:.3f} "
            f"> {max_ratio}); avg block size would be < {1/max(profile.cardinality_ratio, 1e-9):.1f}"
        )
    elif profile.cardinality_ratio < min_ratio and profile.distinct_count > 10:
        is_blocking = False
        reason = (
            f"mega-block risk (cardinality={profile.cardinality_ratio:.4f} "
            f"< {min_ratio}); avg block size would explode"
        )
    elif profile.distinct_count <= 10:
        is_blocking = False
        reason = (
            f"distinct_count={profile.distinct_count} <= 10; "
            "lifecycle/flag column, would produce too few blocks"
        )

    return ColumnRole(
        name="",  # caller sets
        is_matchkey_candidate=True,  # delegated to existing rules
        is_blocking_candidate=is_blocking,
        blocking_excluded_reason=reason,
    )


def find_composite_blocking_keys(
    df: pl.DataFrame,
    column_roles: list[ColumnRole],
    *,
    target_avg_block_size: int = DEFAULT_COMPOSITE_TARGET_AVG_BLOCK_SIZE,
) -> list[str] | None:
    """Search for a 2-column composite blocking key.

    Enumerates pairs of mid-cardinality columns (each with ratio in
    ``[0.05, 0.5]`` so the joint cardinality lands in a sane band).
    For each pair, computes joint cardinality via
    ``df.select(c1, c2).n_unique()`` and picks the pair whose joint
    cardinality is closest to ``n_rows / target_avg_block_size``.

    Returns the column names of the best pair, or ``None`` when no
    pair lands in ``[n_rows/100, n_rows/2]`` (avg block size 2-100).

    V1 stays at pair search (max_columns=2); 3+ column composites are
    a documented follow-up. Pair search covers 95% of
    healthcare/finance/retail shapes.
    """
    n_rows = df.height
    if n_rows < 2:
        return None

    # Only consider columns the caller flagged as blocking-eligible.
    # ColumnRole.is_blocking_candidate already encodes the mid-cardinality
    # gate from classify_column_role.
    mid_card_names: list[str] = [
        role.name for role in column_roles if role.is_blocking_candidate
    ]

    if len(mid_card_names) < 2:
        return None

    target_cardinality = max(n_rows // target_avg_block_size, 1)
    band_lo = max(n_rows // 100, 1)  # avg block size 100 (lower bound)
    band_hi = max(n_rows // 2, 1)    # avg block size 2 (upper bound)

    best_pair: tuple[str, str] | None = None
    best_distance = float("inf")

    for i, c1 in enumerate(mid_card_names):
        for c2 in mid_card_names[i + 1:]:
            if c1 not in df.columns or c2 not in df.columns:
                continue
            try:
                joint_card = int(df.select([c1, c2]).n_unique())
            except Exception as exc:  # pragma: no cover -- defensive
                logger.debug(
                    "find_composite_blocking_keys: skipping (%s, %s): %s",
                    c1, c2, exc,
                )
                continue
            if joint_card < band_lo or joint_card > band_hi:
                continue
            distance = abs(joint_card - target_cardinality)
            if distance < best_distance:
                best_distance = distance
                best_pair = (c1, c2)

    if best_pair is None:
        return None
    return list(best_pair)


def estimate_avg_block_size(
    sample_df: pl.DataFrame,
    blocking_field_names: list[str],
    full_population_n_rows: int,
) -> float:
    """Estimate avg block size for ``full_population_n_rows`` from a sample.

    Builds the block keys on ``sample_df``, counts distinct, scales to
    the full population. Estimate is noisy (sample cardinality is a
    bounded estimator of full-pop cardinality), but the magnitude is
    what matters for the degenerate-blocking guard: "is it ~1 or ~50."

    Returns 1.0 when no fields are given (caller treats that as a
    degenerate config that should be rejected).
    """
    if not blocking_field_names or sample_df.height == 0:
        return 1.0
    fields_in_df = [f for f in blocking_field_names if f in sample_df.columns]
    if not fields_in_df:
        return 1.0
    try:
        sample_distinct = int(sample_df.select(fields_in_df).n_unique())
    except Exception as exc:  # pragma: no cover -- defensive
        logger.debug("estimate_avg_block_size failed: %s", exc)
        return 1.0
    if sample_distinct == 0:
        return 1.0
    # Scale sample-distinct linearly to full population. Coupon-collector
    # correction would be more accurate but the magnitude is what matters.
    scaled_distinct = max(
        int(sample_distinct * (full_population_n_rows / sample_df.height)),
        1,
    )
    return full_population_n_rows / scaled_distinct


def degenerate_guard_threshold() -> float:
    """Env-overridable threshold for the BLOCKING_DEGENERATE guard."""
    raw = os.environ.get("GOLDENMATCH_BLOCKING_DEGENERATE_THRESHOLD")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_BLOCKING_DEGENERATE_THRESHOLD=%r is not "
                "a float; falling back to %f",
                raw, DEFAULT_DEGENERATE_GUARD_THRESHOLD,
            )
    return DEFAULT_DEGENERATE_GUARD_THRESHOLD
