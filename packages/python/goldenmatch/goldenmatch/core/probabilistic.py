"""Fellegi-Sunter probabilistic matching with EM-trained parameters.

Implements the classic Fellegi-Sunter model for record linkage:
- Comparison vectors classify field agreements into levels (agree/partial/disagree)
- Expectation-Maximization estimates m-probabilities (P(level|match)) and
  u-probabilities (P(level|non-match)) from unlabeled data
- Match weights are log-likelihood ratios: log2(m/u)
- Thresholds computed from the weight distribution

References:
    Fellegi & Sunter (1969). "A Theory for Record Linkage"
    Winkler (2006). "Overview of Record Linkage and Current Research Directions"
"""
from __future__ import annotations

import logging
import math
import os
import random
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import polars as pl

from goldenmatch.config.schemas import MatchkeyConfig
from goldenmatch.core.scorer import score_field

logger = logging.getLogger(__name__)


def _fs_sigmoid_enabled() -> bool:
    """Splink-style sigmoid match-probability normalization (default ON).

    The probabilistic scorers convert the summed log2-Bayes match weight W to
    a match probability via P = 1 / (1 + 2^(-W)) -- the Splink convention --
    instead of the legacy min-max normalization. min-max is pathological: one
    high-variance EM weight inflates the running max, so a pair agreeing on all
    the name fields can normalize below a 0.5 threshold and be rejected.

    GOLDENMATCH_FS_SIGMOID=0/false/disabled/no restores the legacy min-max
    normalization byte-identically.
    """
    return os.environ.get("GOLDENMATCH_FS_SIGMOID", "1").strip().lower() not in (
        "0",
        "false",
        "disabled",
        "no",
    )


def _fs_per_rule_em_enabled() -> bool:
    """Per-rule EM (estimate each field's m from runs where it is free) -- default ON.
    GOLDENMATCH_FS_PER_RULE_EM=0/false/disabled/no restores the single-run EM."""
    return os.environ.get("GOLDENMATCH_FS_PER_RULE_EM", "1").strip().lower() not in (
        "0", "false", "disabled", "no")


# Term-frequency adjustment bounds. The adjusted u for a shared value is clamped
# into [TF_MIN_U, TF_MAX_U] so a hapax (freq -> 0) can't blow the weight up
# unboundedly and a ubiquitous value can't drive u above 0.5 (which would make
# agreement net-negative evidence beyond what the model can justify).
TF_MIN_U = float(os.environ.get("GOLDENMATCH_TF_MIN_U", "1e-6"))
TF_MAX_U = float(os.environ.get("GOLDENMATCH_TF_MAX_U", "0.5"))


def _tf_adjusted_weight(
    m_exact: float, u_exact: float, freq_v: float, n_distinct: int
) -> float:
    """Splink scale-the-base TF adjustment for the exact-agree (top) level.

      freq_avg = 1/n_distinct ;  u_v = clamp(u_exact * freq_v / freq_avg, TF_MIN_U, TF_MAX_U)
      weight   = log2(m_exact / u_v)

    Rarer shared value -> freq_v<freq_avg -> smaller u_v -> LARGER weight. A value of
    average frequency returns the base weight log2(m_exact/u_exact). Common value ->
    larger u_v -> weight collapses toward/below 0 (agreeing on a ubiquitous value is weak
    evidence). Bounds keep hapax/huge extremes sane.
    """
    freq_avg = 1.0 / max(n_distinct, 1)
    u_v = u_exact * (freq_v / freq_avg) if freq_avg > 0 else u_exact
    u_v = min(max(u_v, TF_MIN_U), TF_MAX_U)
    return math.log2(max(m_exact, 1e-10) / u_v)


@dataclass
class EMResult:
    """Result of EM training for Fellegi-Sunter model."""

    m_probs: dict[str, list[float]]  # field -> P(level_i | match)
    u_probs: dict[str, list[float]]  # field -> P(level_i | non-match)
    match_weights: dict[str, list[float]]  # field -> log2(m/u) per level
    converged: bool
    iterations: int
    proportion_matched: float  # estimated match rate in the data
    # Per-field relative-frequency table for tf_adjust fields, keyed on the SAME
    # transformed value the scorer/xform-column sees. None when no field opts in.
    # Trailing default keeps every existing EMResult(...) call site valid.
    tf_tables: dict[str, dict[str, float]] | None = None


def comparison_vector(
    row_a: dict,
    row_b: dict,
    mk: MatchkeyConfig,
) -> list[int]:
    """Compute comparison vector for a pair of records.

    Returns a list of level indices, one per field.
    For 2-level fields: 0=disagree, 1=agree
    For 3-level fields: 0=disagree, 1=partial, 2=agree
    """
    from goldenmatch.utils.transforms import apply_transforms

    levels = []
    for f in mk.fields:
        val_a = str(row_a.get(f.field, "")) if row_a.get(f.field) is not None else None
        val_b = str(row_b.get(f.field, "")) if row_b.get(f.field) is not None else None
        # Apply field transforms before scoring (e.g. lowercase, strip)
        if f.transforms:
            val_a = apply_transforms(val_a, f.transforms)
            val_b = apply_transforms(val_b, f.transforms)
        s = score_field(val_a, val_b, f.scorer)

        if s is None:
            levels.append(0)  # treat nulls as disagree
        elif f.levels == 2:
            levels.append(1 if s >= f.partial_threshold else 0)
        elif f.levels == 3:
            if s >= 0.95:
                levels.append(2)
            elif s >= f.partial_threshold:
                levels.append(1)
            else:
                levels.append(0)
        else:
            # N levels: evenly spaced thresholds from 0 to 1
            # Level 0 = lowest (disagree), Level N-1 = highest (exact agree)
            n = f.levels
            level = 0
            for k in range(1, n):
                threshold = k / n
                if s >= threshold:
                    level = k
            levels.append(level)
    return levels


def continuous_scores(
    row_a: dict,
    row_b: dict,
    mk: MatchkeyConfig,
) -> list[float]:
    """Compute continuous field scores for a pair (Winkler extension).

    Returns raw scorer output per field (0.0-1.0), preserving the
    full continuous signal instead of discretizing into levels.
    """
    from goldenmatch.utils.transforms import apply_transforms

    scores = []
    for f in mk.fields:
        val_a = str(row_a.get(f.field, "")) if row_a.get(f.field) is not None else None
        val_b = str(row_b.get(f.field, "")) if row_b.get(f.field) is not None else None
        if f.transforms:
            val_a = apply_transforms(val_a, f.transforms)
            val_b = apply_transforms(val_b, f.transforms)
        s = score_field(val_a, val_b, f.scorer)
        scores.append(s if s is not None else 0.0)
    return scores


def _build_continuous_matrix(
    pairs: list[tuple[int, int]],
    row_lookup: dict[int, dict],
    mk: MatchkeyConfig,
) -> np.ndarray:
    """Build NxF continuous score matrix."""
    n_pairs = len(pairs)
    n_fields = len(mk.fields)
    matrix = np.zeros((n_pairs, n_fields), dtype=np.float64)

    for i, (a, b) in enumerate(pairs):
        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})
        matrix[i] = continuous_scores(row_a, row_b, mk)

    return matrix


def _sample_pairs(
    df: pl.DataFrame,
    n_pairs: int = 10000,
    seed: int = 42,
) -> list[tuple[int, int]]:
    """Sample random pairs for EM training."""
    row_ids = df["__row_id__"].to_list()
    rng = random.Random(seed)

    if len(row_ids) < 2:
        return []

    # For small datasets, use all pairs
    max_possible = len(row_ids) * (len(row_ids) - 1) // 2
    if max_possible <= n_pairs:
        return list(combinations(row_ids, 2))

    # Reservoir sampling of random pairs
    pairs = set()
    attempts = 0
    max_attempts = n_pairs * 10
    while len(pairs) < n_pairs and attempts < max_attempts:
        i, j = rng.sample(row_ids, 2)
        pair = (min(i, j), max(i, j))
        pairs.add(pair)
        attempts += 1

    return list(pairs)


def _build_comparison_matrix(
    pairs: list[tuple[int, int]],
    row_lookup: dict[int, dict],
    mk: MatchkeyConfig,
) -> np.ndarray:
    """Build NxF comparison matrix where N=pairs, F=fields."""
    n_pairs = len(pairs)
    n_fields = len(mk.fields)
    matrix = np.zeros((n_pairs, n_fields), dtype=np.int8)

    for i, (a, b) in enumerate(pairs):
        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})
        vec = comparison_vector(row_a, row_b, mk)
        matrix[i] = vec

    return matrix


def _sample_blocked_pairs(
    blocks: list,
    n_pairs: int = 10000,
    seed: int = 42,
) -> list[tuple[int, int]]:
    """Sample within-block pairs for EM training.

    This produces a much higher match rate than random sampling because
    records in the same block are more likely to be true matches.
    """
    rng = random.Random(seed)
    all_block_pairs: list[tuple[int, int]] = []

    for block in blocks:
        block_df = block.df.collect() if hasattr(block.df, 'collect') else block.df
        row_ids = block_df["__row_id__"].to_list()
        if len(row_ids) < 2:
            continue
        # Limit per-block pairs for large blocks
        if len(row_ids) > 100:
            sampled_ids = rng.sample(row_ids, 100)
        else:
            sampled_ids = row_ids
        for i in range(len(sampled_ids)):
            for j in range(i + 1, len(sampled_ids)):
                all_block_pairs.append((min(sampled_ids[i], sampled_ids[j]),
                                        max(sampled_ids[i], sampled_ids[j])))

    # Deduplicate and sample down if too many
    all_block_pairs = list(set(all_block_pairs))
    if len(all_block_pairs) > n_pairs:
        all_block_pairs = rng.sample(all_block_pairs, n_pairs)

    return all_block_pairs


def _estimate_m_one_pass(
    comp_matrix: np.ndarray,
    mk: MatchkeyConfig,
    u_probs: dict[str, list[float]],
    excluded: set[str],
    max_iterations: int,
    convergence: float,
) -> tuple[dict[str, list[float]], float, bool, int]:
    """One EM run: estimate m for fields NOT in `excluded` (the run's blocking fields,
    held constant in this block). Returns (m_probs, p_match, converged, iterations).
    m_probs covers ALL fields (excluded ones keep the exponential prior; callers ignore
    those). Vectorized E-step identical to the inline loop it replaces. The extra return
    values (p_match/converged/iterations) preserve what train_em's EMResult needs so the
    single-run refactor is behavior-preserving (existing tests assert converged/iterations)."""
    n_pairs = comp_matrix.shape[0]
    p_match = 0.02
    m_probs = {}
    for f in mk.fields:
        raw = [2 ** k for k in range(f.levels)]
        m_probs[f.field] = [r / sum(raw) for r in raw]
    converged = False
    iteration = 0
    for iteration in range(max_iterations):
        old_m = {k: list(v) for k, v in m_probs.items()}
        log_m = np.zeros(n_pairs); log_u = np.zeros(n_pairs)
        for j, f in enumerate(mk.fields):
            levels_j = comp_matrix[:, j]
            m_table = np.log(np.maximum(np.asarray(m_probs[f.field], dtype=np.float64), 1e-10))
            u_table = np.log(np.maximum(np.asarray(u_probs[f.field], dtype=np.float64), 1e-10))
            log_m += m_table[levels_j]; log_u += u_table[levels_j]
        log_match = math.log(max(p_match, 1e-10)) + log_m
        log_nonmatch = math.log(max(1 - p_match, 1e-10)) + log_u
        max_log = np.maximum(log_match, log_nonmatch)
        e_match = np.exp(log_match - max_log); e_nonmatch = np.exp(log_nonmatch - max_log)
        posteriors = e_match / (e_match + e_nonmatch)
        total_match = posteriors.sum()
        p_match = max(total_match / n_pairs, 1e-6)
        for j, f in enumerate(mk.fields):
            if f.field in excluded:
                continue
            new_m = [0.0] * f.levels
            for level in range(f.levels):
                mask = comp_matrix[:, j] == level
                new_m[level] = (posteriors[mask].sum() + 1e-6) / (total_match + f.levels * 1e-6)
            m_probs[f.field] = new_m
        max_delta = max((abs(m_probs[f.field][k] - old_m[f.field][k])
                         for f in mk.fields if f.field not in excluded
                         for k in range(f.levels)), default=0.0)
        if max_delta < convergence:
            converged = True
            break
    iterations = iteration + 1
    return m_probs, p_match, converged, iterations


def train_em(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    n_sample_pairs: int = 10000,
    max_iterations: int = 20,
    convergence: float = 0.001,
    seed: int = 42,
    blocks: list | None = None,
    blocking_fields: list[str] | None = None,
) -> EMResult:
    """Train Fellegi-Sunter model using Expectation-Maximization.

    When blocks are provided, samples within-block pairs for training.
    This produces much better m/u estimates because blocked pairs have
    a higher true match rate than random pairs from the full dataset.

    IMPORTANT: Fields used for blocking are always "agree" within blocks,
    so they provide no discrimination for EM. If blocking_fields is provided,
    those fields get fixed high-confidence priors instead of EM-estimated values.

    Args:
        df: DataFrame with __row_id__ and field columns.
        mk: Probabilistic matchkey config.
        n_sample_pairs: Number of pairs to sample for training.
        max_iterations: Maximum EM iterations.
        convergence: Stop when max change in any probability < this.
        seed: Random seed for pair sampling.
        blocks: Optional list of BlockResult for within-block sampling.
        blocking_fields: Fields used for blocking (excluded from EM training).

    Returns:
        EMResult with trained m/u probabilities and match weights.
    """
    if blocking_fields is None:
        blocking_fields = []

    cols = [f.field for f in mk.fields if f.field != "__record__"]
    row_lookup: dict[int, dict] = {}
    for row in df.select(["__row_id__"] + cols).to_dicts():
        row_lookup[row["__row_id__"]] = row

    # ── Step 1: Estimate u from RANDOM pairs (Splink approach) ──
    # Random pairs are overwhelmingly non-matches, so the observed
    # level distribution approximates u directly. No EM needed for u.
    random_pairs = _sample_pairs(df, min(n_sample_pairs, 5000), seed)
    if len(random_pairs) < 10:
        logger.warning("Too few pairs (%d) for EM training", len(random_pairs))
        return _fallback_result(mk)

    random_matrix = _build_comparison_matrix(random_pairs, row_lookup, mk)
    u_probs = {}
    for j, f in enumerate(mk.fields):
        n_levels = f.levels
        counts = [0.0] * n_levels
        for level in range(n_levels):
            counts[level] = float((random_matrix[:, j] == level).sum())
        total = sum(counts) + n_levels * 1e-6
        u_probs[f.field] = [(c + 1e-6) / total for c in counts]

    # Override blocking fields with neutral u (since random pairs give biased u for blocked fields)
    for f in mk.fields:
        if f.field in blocking_fields:
            if f.levels == 2:
                u_probs[f.field] = [0.50, 0.50]  # neutral
            else:
                u_probs[f.field] = [0.34, 0.33, 0.33]

    logger.info("u-probabilities estimated from %d random pairs", len(random_pairs))

    # ── Step 2: Get blocked pairs for m estimation ──
    if blocks:
        pairs = _sample_blocked_pairs(blocks, n_sample_pairs, seed)
        logger.info("EM training m on %d within-block pairs", len(pairs))
    else:
        pairs = random_pairs
        logger.info("No blocks provided; using random pairs for m estimation")

    if len(pairs) < 10:
        return _fallback_result(mk)

    comp_matrix = _build_comparison_matrix(pairs, row_lookup, mk)

    # ── Step 3: EM iterations — only update m, fix u ──
    m_probs, p_match, converged, iterations = _estimate_m_one_pass(
        comp_matrix, mk, u_probs, set(blocking_fields), max_iterations, convergence)
    if not converged:
        logger.warning("EM did not converge after %d iterations", max_iterations)

    # Compute match weights: log2(m/u)
    # For blocking fields, use fixed priors since EM can't learn from
    # fields that are always "agree" within blocks
    match_weights = {}
    for f in mk.fields:
        if f.field in blocking_fields:
            # Fixed weights: linearly increasing from -3 to +3
            n = f.levels
            match_weights[f.field] = [
                -3.0 + 6.0 * k / (n - 1) if n > 1 else 3.0
                for k in range(n)
            ]
            logger.debug("Using fixed weights for blocking field '%s'", f.field)
            continue

        weights = []
        for k in range(f.levels):
            m_val = max(m_probs[f.field][k], 1e-10)
            u_val = max(u_probs[f.field][k], 1e-10)
            weights.append(math.log2(m_val / u_val))
        match_weights[f.field] = weights

    # ── Step 4: build per-field relative-frequency tables for tf_adjust fields ──
    # Key on the SAME transformed value the scorer/xform-column sees:
    # apply_transforms(str(value), f.transforms). This matches both the slow
    # path's lookup (comparison_vector str()s then transforms) AND the fast
    # path's materialized __xform_<sig>__ column (cast(Utf8) then transforms).
    from goldenmatch.utils.transforms import apply_transforms

    tf_tables: dict[str, dict[str, float]] = {}
    for f in mk.fields:
        if not getattr(f, "tf_adjust", False):
            continue
        vals = df[f.field].to_list()
        counts: dict[str, int] = {}
        total = 0
        for v in vals:
            if v is None:
                continue
            key = str(v)
            if f.transforms:
                key = apply_transforms(key, f.transforms)
            counts[key] = counts.get(key, 0) + 1
            total += 1
        if total:
            tf_tables[f.field] = {k: c / total for k, c in counts.items()}

    return EMResult(
        m_probs=m_probs,
        u_probs=u_probs,
        match_weights=match_weights,
        converged=converged,
        iterations=iterations,
        proportion_matched=p_match,
        tf_tables=(tf_tables or None),
    )


@dataclass
class ContinuousEMResult:
    """Result of continuous-score EM training (Winkler extension)."""

    m_mean: dict[str, float]  # field -> mean score for matches
    m_var: dict[str, float]   # field -> variance for matches
    u_mean: dict[str, float]  # field -> mean score for non-matches
    u_var: dict[str, float]   # field -> variance for non-matches
    converged: bool
    iterations: int
    proportion_matched: float


def train_em_continuous(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    n_sample_pairs: int = 10000,
    max_iterations: int = 20,
    convergence: float = 0.001,
    seed: int = 42,
    blocks: list | None = None,
    blocking_fields: list[str] | None = None,
) -> ContinuousEMResult:
    """Train Fellegi-Sunter model using continuous scores (Winkler extension).

    Instead of discretizing scores into levels, models P(score|match) and
    P(score|non-match) as Gaussians per field. This preserves the full
    continuous signal and produces better likelihood ratios.
    """
    if blocking_fields is None:
        blocking_fields = []

    cols = [f.field for f in mk.fields if f.field != "__record__"]
    row_lookup: dict[int, dict] = {}
    for row in df.select(["__row_id__"] + cols).to_dicts():
        row_lookup[row["__row_id__"]] = row

    if blocks:
        pairs = _sample_blocked_pairs(blocks, n_sample_pairs, seed)
        logger.info("Continuous EM training on %d within-block pairs", len(pairs))
    else:
        pairs = _sample_pairs(df, n_sample_pairs, seed)

    if len(pairs) < 10:
        logger.warning("Too few pairs for continuous EM")
        return ContinuousEMResult(
            m_mean={f.field: 0.9 for f in mk.fields},
            m_var={f.field: 0.01 for f in mk.fields},
            u_mean={f.field: 0.2 for f in mk.fields},
            u_var={f.field: 0.04 for f in mk.fields},
            converged=False, iterations=0, proportion_matched=0.05,
        )

    # Build continuous score matrix
    score_matrix = _build_continuous_matrix(pairs, row_lookup, mk)
    n_pairs = len(pairs)
    _n_fields = len(mk.fields)

    # Initialize with strong priors — matches score high, non-matches score low.
    # Use the actual score distribution to set non-match priors at the median.
    p_match = 0.02  # conservative: expect few matches

    # Compute actual score statistics for better initialization
    field_medians = {}
    for j, f in enumerate(mk.fields):
        if f.field not in blocking_fields:
            col = score_matrix[:, j]
            field_medians[f.field] = float(np.median(col))

    m_mean = {f.field: 0.90 for f in mk.fields}  # matches should score very high
    m_var = {f.field: 0.01 for f in mk.fields}    # tight distribution
    u_mean = {f.field: field_medians.get(f.field, 0.30) for f in mk.fields}  # non-matches at median
    u_var = {f.field: 0.05 for f in mk.fields}    # broader distribution

    # Override blocking fields
    for f in mk.fields:
        if f.field in blocking_fields:
            m_mean[f.field] = 0.99
            m_var[f.field] = 0.001
            u_mean[f.field] = 0.99  # always agree in blocks
            u_var[f.field] = 0.001

    converged = False
    for iteration in range(max_iterations):
        old_m_mean = dict(m_mean)
        old_u_mean = dict(u_mean)

        # E-step: compute posteriors using Gaussian likelihood
        posteriors = np.zeros(n_pairs)
        for i in range(n_pairs):
            log_m = math.log(max(p_match, 1e-10))
            log_u = math.log(max(1 - p_match, 1e-10))

            for j, f in enumerate(mk.fields):
                if f.field in blocking_fields:
                    continue
                s = score_matrix[i, j]
                # Gaussian log-likelihood
                var_m = max(m_var[f.field], 1e-6)
                var_u = max(u_var[f.field], 1e-6)
                log_m += -0.5 * ((s - m_mean[f.field]) ** 2) / var_m - 0.5 * math.log(var_m)
                log_u += -0.5 * ((s - u_mean[f.field]) ** 2) / var_u - 0.5 * math.log(var_u)

            max_log = max(log_m, log_u)
            posteriors[i] = math.exp(log_m - max_log) / (
                math.exp(log_m - max_log) + math.exp(log_u - max_log)
            )

        # M-step
        total_match = posteriors.sum()
        total_nonmatch = n_pairs - total_match
        p_match = max(total_match / n_pairs, 1e-6)

        for j, f in enumerate(mk.fields):
            if f.field in blocking_fields:
                continue
            scores = score_matrix[:, j]
            # Weighted mean and variance for matches
            if total_match > 1e-6:
                m_mean[f.field] = float(np.average(scores, weights=posteriors))
                m_var[f.field] = float(np.average((scores - m_mean[f.field]) ** 2, weights=posteriors)) + 1e-6
            # Weighted mean and variance for non-matches
            w_nonmatch = 1 - posteriors
            if total_nonmatch > 1e-6:
                u_mean[f.field] = float(np.average(scores, weights=w_nonmatch))
                u_var[f.field] = float(np.average((scores - u_mean[f.field]) ** 2, weights=w_nonmatch)) + 1e-6

        # Convergence check
        max_delta = 0.0
        for f in mk.fields:
            if f.field in blocking_fields:
                continue
            max_delta = max(max_delta, abs(m_mean[f.field] - old_m_mean[f.field]))
            max_delta = max(max_delta, abs(u_mean[f.field] - old_u_mean[f.field]))

        if max_delta < convergence:
            converged = True
            logger.info("Continuous EM converged after %d iterations", iteration + 1)
            break

    if not converged:
        logger.warning("Continuous EM did not converge after %d iterations", max_iterations)

    return ContinuousEMResult(
        m_mean=m_mean, m_var=m_var,
        u_mean=u_mean, u_var=u_var,
        converged=converged,
        iterations=iteration + 1,
        proportion_matched=p_match,
    )


def score_probabilistic_continuous(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    em: ContinuousEMResult,
    threshold: float = 0.50,
    exclude_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Score pairs using continuous Fellegi-Sunter (Winkler extension).

    Computes log-likelihood ratios from Gaussian models of match/non-match
    score distributions. Returns pairs above threshold as normalized 0-1 scores.
    """
    if exclude_pairs is None:
        exclude_pairs = set()

    cols = [f.field for f in mk.fields if f.field != "__record__"]
    row_lookup: dict[int, dict] = {}
    for row in block_df.select(["__row_id__"] + cols).to_dicts():
        row_lookup[row["__row_id__"]] = row

    row_ids = block_df["__row_id__"].to_list()

    results = []
    for i in range(len(row_ids)):
        for j in range(i + 1, len(row_ids)):
            a, b = row_ids[i], row_ids[j]
            pair_key = (min(a, b), max(a, b))
            if pair_key in exclude_pairs:
                continue

            row_a = row_lookup.get(a, {})
            row_b = row_lookup.get(b, {})
            scores = continuous_scores(row_a, row_b, mk)

            # Compute log-likelihood ratio
            log_ratio = 0.0
            for k, f in enumerate(mk.fields):
                s = scores[k]
                var_m = max(em.m_var[f.field], 1e-6)
                var_u = max(em.u_var[f.field], 1e-6)
                # Log Gaussian likelihood ratio
                log_m = -0.5 * ((s - em.m_mean[f.field]) ** 2) / var_m - 0.5 * math.log(var_m)
                log_u = -0.5 * ((s - em.u_mean[f.field]) ** 2) / var_u - 0.5 * math.log(var_u)
                log_ratio += log_m - log_u

            # Convert to 0-1 via sigmoid
            normalized = 1.0 / (1.0 + math.exp(-log_ratio))

            if normalized >= threshold:
                results.append((a, b, round(normalized, 4)))

    return results


def _fallback_result(mk: MatchkeyConfig) -> EMResult:
    """Return a fallback EMResult when EM can't be trained."""
    m_probs = {}
    u_probs = {}
    match_weights = {}
    for f in mk.fields:
        if f.levels == 2:
            m_probs[f.field] = [0.1, 0.9]
            u_probs[f.field] = [0.9, 0.1]
            match_weights[f.field] = [math.log2(0.1 / 0.9), math.log2(0.9 / 0.1)]
        else:
            m_probs[f.field] = [0.05, 0.15, 0.80]
            u_probs[f.field] = [0.80, 0.15, 0.05]
            match_weights[f.field] = [
                math.log2(0.05 / 0.80),
                math.log2(0.15 / 0.15),
                math.log2(0.80 / 0.05),
            ]
    return EMResult(
        m_probs=m_probs, u_probs=u_probs, match_weights=match_weights,
        converged=False, iterations=0, proportion_matched=0.05,
    )


def compute_thresholds(
    em_result: EMResult,
    scored_weights: list[float] | None = None,
) -> tuple[float, float]:
    """Compute link and review thresholds from EM result.

    Returns (link_threshold, review_threshold) as normalized 0-1 scores.
    link_threshold: pairs above this are matches
    review_threshold: pairs between review and link are uncertain

    If scored_weights are provided (actual pair weight distribution),
    uses percentile-based thresholds. Otherwise uses a fixed default
    that works well across datasets.

    Under sigmoid normalization (the default, GOLDENMATCH_FS_SIGMOID on) the
    scores are match-probabilities, so the thresholds are interpreted on the
    (0,1) match-probability scale: the fixed default is (0.9, 0.5) and the
    data-driven link clamp ceiling is raised to 0.99. Under the kill-switch
    (min-max) the legacy (0.50, 0.35) default and [0.40, 0.95] link clamp
    are restored exactly.
    """
    sigmoid = _fs_sigmoid_enabled()

    if scored_weights and len(scored_weights) > 50:
        # Data-driven: use the distribution of actual pair scores
        sorted_w = sorted(scored_weights)
        n = len(sorted_w)
        # Link at the (1 - match_rate) percentile — top match_rate% of pairs
        # But clamp to reasonable range
        match_pct = max(em_result.proportion_matched, 0.001)
        link_idx = int(n * (1 - match_pct * 2))  # 2x match rate for headroom
        link_idx = max(0, min(link_idx, n - 1))
        link_norm = sorted_w[link_idx]

        review_idx = int(n * (1 - match_pct * 5))  # 5x for review band
        review_idx = max(0, min(review_idx, n - 1))
        review_norm = sorted_w[review_idx]

        if sigmoid:
            # Match-probability scale: don't cap a sane link below a
            # high match-prob; keep the review floor at the match-prob midpoint.
            link = round(max(0.5, min(0.99, link_norm)), 4)
            review = round(max(0.5, min(link - 0.05, review_norm)), 4)
            return link, review
        return round(max(0.40, min(0.95, link_norm)), 4), round(max(0.25, min(link_norm - 0.05, review_norm)), 4)

    # Fixed defaults that work well with pre-blocked pairs.
    if sigmoid:
        # Match-probability scale: 0.9 link / 0.5 review (a pair with
        # net-positive evidence -- W > 0 -- clears the review band).
        return 0.9, 0.5
    # Legacy min-max default: 0.50 is permissive enough to catch partial
    # matches while still filtering clear non-matches (which score near 0).
    return 0.50, 0.35


def score_probabilistic(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Score pairs in a block using Fellegi-Sunter match weights.

    Returns pairs above the link threshold as (row_id_a, row_id_b, normalized_score).
    Score is normalized to 0-1 range for compatibility with the rest of the pipeline.
    """
    if exclude_pairs is None:
        exclude_pairs = set()

    # Build row lookup
    cols = [f.field for f in mk.fields if f.field != "__record__"]
    row_lookup: dict[int, dict] = {}
    for row in block_df.select(["__row_id__"] + cols).to_dicts():
        row_lookup[row["__row_id__"]] = row

    row_ids = block_df["__row_id__"].to_list()

    # Compute weight range for legacy min-max normalization. Kept even under
    # sigmoid so the kill-switch path is byte-identical.
    max_weight = sum(max(em_result.match_weights[f.field]) for f in mk.fields)
    min_weight = sum(min(em_result.match_weights[f.field]) for f in mk.fields)
    weight_range = max_weight - min_weight

    # Resolve normalization mode once (no per-pair env reads).
    sigmoid = _fs_sigmoid_enabled()

    # Determine threshold
    if mk.link_threshold is not None:
        link_threshold = mk.link_threshold
    else:
        link_threshold, _ = compute_thresholds(em_result)

    results = []
    for i in range(len(row_ids)):
        for j in range(i + 1, len(row_ids)):
            a, b = row_ids[i], row_ids[j]
            pair_key = (min(a, b), max(a, b))
            if pair_key in exclude_pairs:
                continue

            row_a = row_lookup.get(a, {})
            row_b = row_lookup.get(b, {})
            vec = comparison_vector(row_a, row_b, mk)

            # Sum match weights
            total_weight = 0.0
            for k, f in enumerate(mk.fields):
                level = vec[k]
                top = f.levels - 1
                if (em_result.tf_tables and getattr(f, "tf_adjust", False)
                        and f.field in em_result.tf_tables and level == top):
                    raw = row_a.get(f.field)
                    val = str(raw) if raw is not None else None
                    if val is not None and f.transforms:
                        from goldenmatch.utils.transforms import apply_transforms
                        val = apply_transforms(val, f.transforms)
                    tft = em_result.tf_tables[f.field]
                    if val is not None and val in tft:
                        m_exact = max(em_result.m_probs[f.field][top], 1e-10)
                        u_exact = max(em_result.u_probs[f.field][top], 1e-10)
                        total_weight += _tf_adjusted_weight(
                            m_exact, u_exact, tft[val], len(tft)
                        )
                        continue
                total_weight += em_result.match_weights[f.field][level]

            # Normalize to 0-1
            if sigmoid:
                # Splink-style match probability; already in (0,1).
                normalized = 1.0 / (1.0 + 2.0 ** (-total_weight))
            elif weight_range > 0:
                normalized = (total_weight - min_weight) / weight_range
            else:
                normalized = 0.5

            if normalized >= link_threshold:
                results.append((a, b, round(normalized, 4)))

    return results


def score_pair_probabilistic(
    row_a: dict,
    row_b: dict,
    mk: MatchkeyConfig,
    em_result: EMResult,
) -> float:
    """Score a single pair using Fellegi-Sunter weights. For match_one."""
    vec = comparison_vector(row_a, row_b, mk)

    max_weight = sum(max(em_result.match_weights[f.field]) for f in mk.fields)
    min_weight = sum(min(em_result.match_weights[f.field]) for f in mk.fields)
    weight_range = max_weight - min_weight

    total_weight = 0.0
    for k, f in enumerate(mk.fields):
        level = vec[k]
        top = f.levels - 1
        if (em_result.tf_tables and getattr(f, "tf_adjust", False)
                and f.field in em_result.tf_tables and level == top):
            raw = row_a.get(f.field)
            val = str(raw) if raw is not None else None
            if val is not None and f.transforms:
                from goldenmatch.utils.transforms import apply_transforms
                val = apply_transforms(val, f.transforms)
            tft = em_result.tf_tables[f.field]
            if val is not None and val in tft:
                m_exact = max(em_result.m_probs[f.field][top], 1e-10)
                u_exact = max(em_result.u_probs[f.field][top], 1e-10)
                total_weight += _tf_adjusted_weight(m_exact, u_exact, tft[val], len(tft))
                continue
        total_weight += em_result.match_weights[f.field][level]

    if _fs_sigmoid_enabled():
        # Splink-style match probability; already in (0,1).
        return 1.0 / (1.0 + 2.0 ** (-total_weight))
    if weight_range > 0:
        return (total_weight - min_weight) / weight_range
    return 0.5
