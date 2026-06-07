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

# ── Score calibration ──────────────────────────────────────────────────────
# Two ways to turn the summed Fellegi-Sunter match weight (Σ log2(m/u), in
# bits) into the 0-1 score the rest of the pipeline consumes:
#
#   "linear"    — (W - W_min) / (W_max - W_min). Monotonic in W but NOT a
#                 probability: a 0.50 cutoff lands at the midpoint of the
#                 achievable weight range, which has no probabilistic meaning
#                 and (on asymmetric-weight data) sits far above the Bayes
#                 boundary. This is the historical behavior — precision-heavy,
#                 recall-starved (98.8% / 57.6% on DBLP-ACM).
#
#   "posterior" — the true FS match probability:
#                     logodds = log2(λ/(1-λ)) + W
#                     p       = 1 / (1 + 2^(-logodds))
#                 where λ is the EM-estimated within-block match rate
#                 (em_result.proportion_matched). This restores the prior the
#                 EM step estimates and then the linear path discards, and the
#                 score is an actual calibrated probability the user can reason
#                 about. A 0.50 cutoff means "more likely a match than not".
#
# Default resolved by `_fs_calibration_mode()`. `GOLDENMATCH_FS_CALIBRATED`
# overrides: "1"/"posterior" -> posterior, "0"/"linear" -> linear.
# NOTE: default flipped to "posterior" below once measured on DBLP-ACM/Febrl.
_FS_CALIBRATION_DEFAULT = "linear"


def _fs_calibration_mode() -> str:
    """Return the active FS score-calibration mode: 'posterior' or 'linear'."""
    val = os.environ.get("GOLDENMATCH_FS_CALIBRATED")
    if val is None:
        return _FS_CALIBRATION_DEFAULT
    v = val.strip().lower()
    if v in ("0", "false", "no", "off", "disabled", "linear"):
        return "linear"
    if v in ("1", "true", "yes", "on", "enabled", "posterior"):
        return "posterior"
    return _FS_CALIBRATION_DEFAULT


def prior_weight(proportion_matched: float) -> float:
    """log2 prior odds of a match: log2(λ / (1-λ)).

    λ is clamped off {0, 1} so the log is finite. For a within-block match
    rate of 0.002 this is ≈ -8.96 bits — the evidence a pair must overcome
    before it is more likely a match than not.
    """
    lam = min(max(proportion_matched, 1e-9), 1.0 - 1e-9)
    return math.log2(lam / (1.0 - lam))


def posterior_from_weight(total_weight: float, prior_w: float) -> float:
    """Convert total match weight (bits) + prior weight (bits) to P(match).

    posterior = 1 / (1 + 2^(-(prior_w + total_weight))). Clamped to avoid
    overflow in the 2**(-logodds) term for extreme weights.
    """
    logodds = prior_w + total_weight
    if logodds > 60.0:
        return 1.0
    if logodds < -60.0:
        return 0.0
    return 1.0 / (1.0 + 2.0 ** (-logodds))


@dataclass
class EMResult:
    """Result of EM training for Fellegi-Sunter model."""

    m_probs: dict[str, list[float]]  # field -> P(level_i | match)
    u_probs: dict[str, list[float]]  # field -> P(level_i | non-match)
    match_weights: dict[str, list[float]]  # field -> log2(m/u) per level
    converged: bool
    iterations: int
    proportion_matched: float  # estimated match rate in the data
    # Term-frequency (Winkler) adjustment data, populated only for fields with
    # tf_adjustment=True. tf_freqs: field -> {transformed_value -> relative
    # frequency}. tf_collision: field -> Σ freq(v)^2 (the expected exact-match
    # collision rate; the baseline an agreement is adjusted against).
    tf_freqs: dict[str, dict[str, float]] | None = None
    tf_collision: dict[str, float] | None = None


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
    n_pairs = len(pairs)
    _n_fields = len(mk.fields)

    # Initialize m with strong priors (matches mostly agree at highest level)
    p_match = 0.02  # conservative prior
    m_probs = {}
    for j, f in enumerate(mk.fields):
        n_levels = f.levels
        # Exponential prior: highest level gets most mass
        raw = [2 ** k for k in range(n_levels)]
        total = sum(raw)
        m_probs[f.field] = [r / total for r in raw]

    # ── Step 3: EM iterations — only update m, fix u ──
    converged = False
    for iteration in range(max_iterations):
        old_m = {k: list(v) for k, v in m_probs.items()}

        # E-step: compute posterior P(match | comparison vector).
        # Vectorized over pairs (this was the FS-training bottleneck: a
        # per-pair Python loop with math.log/exp -- ~1.1s of a 1.46s
        # train_em at n_sample_pairs=10000). Per-field log-prob lookup tables
        # are gathered by level and summed left-to-right (j = 0..n_fields-1),
        # matching the scalar accumulation order so results stay
        # bit-identical to the loop it replaces.
        log_m = np.zeros(n_pairs)
        log_u = np.zeros(n_pairs)
        for j, f in enumerate(mk.fields):
            levels_j = comp_matrix[:, j]
            m_table = np.log(np.maximum(np.asarray(m_probs[f.field], dtype=np.float64), 1e-10))
            u_table = np.log(np.maximum(np.asarray(u_probs[f.field], dtype=np.float64), 1e-10))
            log_m += m_table[levels_j]
            log_u += u_table[levels_j]

        log_match = math.log(max(p_match, 1e-10)) + log_m
        log_nonmatch = math.log(max(1 - p_match, 1e-10)) + log_u

        max_log = np.maximum(log_match, log_nonmatch)
        e_match = np.exp(log_match - max_log)
        e_nonmatch = np.exp(log_nonmatch - max_log)
        posteriors = e_match / (e_match + e_nonmatch)

        # M-step: update ONLY m_probs and p_match (u is fixed)
        total_match = posteriors.sum()
        p_match = max(total_match / n_pairs, 1e-6)

        for j, f in enumerate(mk.fields):
            if f.field in blocking_fields:
                continue  # skip blocked fields
            n_levels = f.levels
            new_m = [0.0] * n_levels
            for level in range(n_levels):
                mask = comp_matrix[:, j] == level
                new_m[level] = (posteriors[mask].sum() + 1e-6) / (total_match + n_levels * 1e-6)
            m_probs[f.field] = new_m

        # Check convergence (only m changes)
        max_delta = 0.0
        for f in mk.fields:
            if f.field in blocking_fields:
                continue
            for k in range(f.levels):
                max_delta = max(max_delta, abs(m_probs[f.field][k] - old_m[f.field][k]))

        if max_delta < convergence:
            converged = True
            logger.info("EM converged after %d iterations (delta=%.6f)", iteration + 1, max_delta)
            break

    if not converged:
        logger.warning("EM did not converge after %d iterations (delta=%.6f)", max_iterations, max_delta)

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

    # Term-frequency tables for fields opting into Winkler TF adjustment.
    tf_freqs, tf_collision = _build_tf_tables(df, mk)

    return EMResult(
        m_probs=m_probs,
        u_probs=u_probs,
        match_weights=match_weights,
        converged=converged,
        iterations=min(iteration + 1, max_iterations) if not converged else iteration + 1,
        proportion_matched=p_match,
        tf_freqs=tf_freqs,
        tf_collision=tf_collision,
    )


def _build_tf_tables(
    df: pl.DataFrame, mk: MatchkeyConfig,
) -> tuple[dict[str, dict[str, float]] | None, dict[str, float] | None]:
    """Per-value relative frequencies for TF-adjustment fields.

    Returns (tf_freqs, tf_collision) or (None, None) when no field opts in.
    Frequencies are computed over the full column (transformed identically to
    ``comparison_vector``) so the adjustment reflects the population, not the
    block. ``tf_collision[field] = Σ freq(v)^2`` is the expected exact-match
    collision rate — the baseline an agreement weight is adjusted against.
    """
    from goldenmatch.utils.transforms import apply_transforms

    tf_fields = [f for f in mk.fields if getattr(f, "tf_adjustment", False)]
    if not tf_fields:
        return None, None

    tf_freqs: dict[str, dict[str, float]] = {}
    tf_collision: dict[str, float] = {}
    for f in tf_fields:
        if f.field not in df.columns:
            continue
        vals = df[f.field].to_list()
        counts: dict[str, int] = {}
        total = 0
        for v in vals:
            if v is None:
                continue
            s = str(v)
            if f.transforms:
                s = apply_transforms(s, f.transforms)
            if s is None or s == "":
                continue
            counts[s] = counts.get(s, 0) + 1
            total += 1
        if total == 0:
            continue
        freqs = {val: c / total for val, c in counts.items()}
        tf_freqs[f.field] = freqs
        tf_collision[f.field] = sum(p * p for p in freqs.values())
    if not tf_freqs:
        return None, None
    return tf_freqs, tf_collision


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
    # Active (non-blocking) field column indices, fixed across iterations.
    active_j = [j for j, f in enumerate(mk.fields) if f.field not in blocking_fields]
    for iteration in range(max_iterations):
        old_m_mean = dict(m_mean)
        old_u_mean = dict(u_mean)

        # E-step: Gaussian log-likelihood per pair, vectorized over pairs.
        # Each active field contributes -0.5*(s-mean)^2/var - 0.5*log(var),
        # summed across fields (left-to-right over active_j to match the
        # scalar accumulation order). Replaces the per-pair Python loop with
        # numpy column ops.
        log_m = np.full(n_pairs, math.log(max(p_match, 1e-10)))
        log_u = np.full(n_pairs, math.log(max(1 - p_match, 1e-10)))
        for j in active_j:
            f = mk.fields[j]
            s = score_matrix[:, j]
            var_m = max(m_var[f.field], 1e-6)
            var_u = max(u_var[f.field], 1e-6)
            log_m += -0.5 * ((s - m_mean[f.field]) ** 2) / var_m - 0.5 * math.log(var_m)
            log_u += -0.5 * ((s - u_mean[f.field]) ** 2) / var_u - 0.5 * math.log(var_u)

        max_log = np.maximum(log_m, log_u)
        e_m = np.exp(log_m - max_log)
        e_u = np.exp(log_u - max_log)
        posteriors = e_m / (e_m + e_u)

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

    row_ids = block_df["__row_id__"].to_list()
    n = len(row_ids)
    if n < 2:
        return []

    # Per-field Gaussian log-likelihood-ratio matrix, summed across fields.
    # Vectorized over pairs via the same NxN similarity matrices the discrete
    # path uses; replaces the per-pair Python double loop.
    log_ratio = np.zeros((n, n), dtype=np.float64)
    for f in mk.fields:
        vals = _field_values_for_block(block_df, f, n)
        sim = np.asarray(_field_score_matrix(vals, f.scorer), dtype=np.float64)
        # continuous_scores maps null -> 0.0 (score_field returns None -> 0.0).
        null_mask = np.array([v is None for v in vals], dtype=bool)
        if null_mask.any():
            either_null = null_mask[:, None] | null_mask[None, :]
            sim = np.where(either_null, 0.0, sim)
        var_m = max(em.m_var[f.field], 1e-6)
        var_u = max(em.u_var[f.field], 1e-6)
        log_m = -0.5 * ((sim - em.m_mean[f.field]) ** 2) / var_m - 0.5 * math.log(var_m)
        log_u = -0.5 * ((sim - em.u_mean[f.field]) ** 2) / var_u - 0.5 * math.log(var_u)
        log_ratio += log_m - log_u

    # Convert to 0-1 via sigmoid (clamped against overflow).
    with np.errstate(over="ignore"):
        normalized = 1.0 / (1.0 + np.exp(-np.clip(log_ratio, -700.0, 700.0)))

    iu, ju = np.triu_indices(n, k=1)
    keep = normalized[iu, ju] >= threshold
    if not keep.any():
        return []
    ids = np.asarray(row_ids)
    a_ids = ids[iu[keep]]
    b_ids = ids[ju[keep]]
    scores = normalized[iu, ju][keep]

    results: list[tuple[int, int, float]] = []
    for a, b, s in zip(a_ids.tolist(), b_ids.tolist(), scores.tolist()):
        pair_key = (a, b) if a < b else (b, a)
        if pair_key in exclude_pairs:
            continue
        results.append((a, b, round(float(s), 4)))
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
    calibrated: bool | None = None,
) -> tuple[float, float]:
    """Compute link and review thresholds from EM result.

    Returns (link_threshold, review_threshold) as normalized 0-1 scores.
    link_threshold: pairs above this are matches
    review_threshold: pairs between review and link are uncertain

    If scored_weights are provided (actual pair weight distribution),
    uses percentile-based thresholds. Otherwise uses a fixed default
    that works well across datasets.

    When ``calibrated`` is True (posterior scoring), the default thresholds
    are interpreted as probabilities: link at 0.5 ("more likely a match than
    not", given the within-block prior) and a low review floor. The score is
    already a probability so no percentile rescaling is applied.
    """
    if calibrated is None:
        calibrated = _fs_calibration_mode() == "posterior"
    if calibrated:
        # Posterior scores are calibrated probabilities; thresholds are
        # absolute, not distribution-relative. 0.5 is the Bayes boundary.
        return 0.50, 0.10
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

        return round(max(0.40, min(0.95, link_norm)), 4), round(max(0.25, min(link_norm - 0.05, review_norm)), 4)

    # Fixed defaults that work well with pre-blocked pairs
    # 0.50 is permissive enough to catch partial matches while
    # still filtering clear non-matches (which score near 0)
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

    # Compute weight range for normalization
    max_weight = sum(max(em_result.match_weights[f.field]) for f in mk.fields)
    min_weight = sum(min(em_result.match_weights[f.field]) for f in mk.fields)
    weight_range = max_weight - min_weight

    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0

    # Determine threshold
    if mk.link_threshold is not None:
        link_threshold = mk.link_threshold
    else:
        link_threshold, _ = compute_thresholds(em_result, calibrated=calibrated)

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
                total_weight += em_result.match_weights[f.field][vec[k]]

            if calibrated:
                normalized = posterior_from_weight(total_weight, prior_w)
            elif weight_range > 0:
                normalized = (total_weight - min_weight) / weight_range
            else:
                normalized = 0.5

            if normalized >= link_threshold:
                results.append((a, b, round(normalized, 4)))

    return results


def _field_values_for_block(block_df: pl.DataFrame, f, n: int) -> list[str | None]:
    """Transformed per-field values for a block, matching comparison_vector.

    str()-coerces non-null values then applies field transforms, exactly as
    ``comparison_vector`` does per pair — but once per column instead of once
    per (pair, field). Missing column -> all-null (slow path: level 0).
    """
    from goldenmatch.utils.transforms import apply_transforms

    if f.field not in block_df.columns:
        return [None] * n
    raw = block_df[f.field].to_list()
    out: list[str | None] = []
    for v in raw:
        if v is None:
            out.append(None)
            continue
        s = str(v)
        if f.transforms:
            s = apply_transforms(s, f.transforms)
        out.append(s)
    return out


def _levels_from_similarity(sim: np.ndarray, levels: int, partial_threshold: float) -> np.ndarray:
    """Vectorized level assignment matching ``comparison_vector`` semantics.

    - 2 levels: 1 if sim >= partial_threshold else 0
    - 3 levels: 2 if sim >= 0.95, elif sim >= partial_threshold -> 1, else 0
    - N>3 levels: largest k in 1..N-1 with sim >= k/N (even spacing), which
      equals the count of satisfied thresholds.
    """
    if levels == 2:
        return (sim >= partial_threshold).astype(np.intp)
    if levels == 3:
        lvl = np.zeros(sim.shape, dtype=np.intp)
        lvl[sim >= partial_threshold] = 1
        lvl[sim >= 0.95] = 2
        return lvl
    # N > 3: count thresholds k/N satisfied (k = 1..N-1), increasing cutoffs.
    lvl = np.zeros(sim.shape, dtype=np.intp)
    for k in range(1, levels):
        lvl += (sim >= (k / levels)).astype(np.intp)
    return lvl


def _field_score_matrix(vals: list[str | None], scorer: str) -> np.ndarray:
    """NxN similarity matrix for a field, routing by scorer the same way
    ``find_fuzzy_matches`` does: exact / soundex have dedicated matrices,
    everything else goes through ``_fuzzy_score_matrix`` (which itself handles
    jaro_winkler/token_sort/levenshtein/ensemble/dice/jaccard/qgram + plugins).
    """
    from goldenmatch.core.scorer import (
        _exact_score_matrix,
        _fuzzy_score_matrix,
        _soundex_score_matrix,
    )

    if scorer == "exact":
        return _exact_score_matrix(vals)
    if scorer == "soundex":
        return _soundex_score_matrix(vals)
    return _fuzzy_score_matrix(vals, scorer)


# Max magnitude (bits) of a single term-frequency adjustment, so a unique
# singleton value can't dominate the whole match weight.
_TF_CLAMP = 10.0


def _apply_tf_adjustment(total_weight, vals, lvl, f, em_result, n) -> None:
    """Add Winkler term-frequency adjustment to ``total_weight`` in place.

    For pairs that agree EXACTLY on a value at the top comparison level, adjust
    the match weight by ``log2(collision_rate / freq(value))`` — rare values get
    a positive bump, common ones a penalty. No-op unless the field opted in via
    ``tf_adjustment=True`` and EM produced a frequency table.
    """
    if not getattr(f, "tf_adjustment", False):
        return
    if not em_result.tf_freqs or f.field not in em_result.tf_freqs:
        return
    freqs = em_result.tf_freqs[f.field]
    collision = (em_result.tf_collision or {}).get(f.field)
    if not collision:
        return
    top = int(f.levels) - 1

    # Per-row adjustment weight (0 where the value is null/unknown).
    adj = np.zeros(n, dtype=np.float64)
    code_arr = np.full(n, -1, dtype=np.int64)
    codes: dict[str, int] = {}
    for i, v in enumerate(vals):
        if v is None:
            continue
        c = codes.get(v)
        if c is None:
            c = len(codes)
            codes[v] = c
        code_arr[i] = c
        fv = freqs.get(v)
        if fv:
            adj[i] = float(np.clip(math.log2(collision / fv), -_TF_CLAMP, _TF_CLAMP))

    # Apply only on exact-equal, top-level agreements. adj[i] == adj[j] there
    # (same value), so broadcasting the row vector is correct.
    equal = (code_arr[:, None] == code_arr[None, :]) & (code_arr[:, None] >= 0)
    apply = equal & (lvl == top)
    if apply.any():
        total_weight += np.where(apply, adj[:, None], 0.0)


def vectorized_scorer_supported(scorer: str) -> bool:
    """Whether a field scorer can be expressed as an NxN matrix here.

    Model-backed scorers (embedding / record_embedding) need per-block model
    bootstrap and are intentionally excluded — callers fall back to the scalar
    ``score_probabilistic`` path for matchkeys containing them.
    """
    return scorer not in ("embedding", "record_embedding")


def score_probabilistic_vectorized(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Vectorized Fellegi-Sunter block scoring.

    Output-equivalent (within rapidfuzz/native-kernel tolerance) to
    ``score_probabilistic`` but replaces the per-pair Python double loop with
    one ``rapidfuzz.cdist`` NxN similarity matrix per field plus numpy level/
    weight/normalize ops — the same transformation the fuzzy path already uses
    (``core.scorer._fuzzy_score_matrix``). This is what lets the pipeline score
    full blocks instead of skipping large ones for performance, which is the
    dominant recall lever for FS (DBLP-ACM: skipping blocks >500 caps recall at
    ~60%; full-block scoring reaches ~96%).

    Falls back to the scalar path semantics for any field whose scorer cannot
    be expressed as an NxN matrix (handled by ``_fuzzy_score_matrix``'s own
    fallbacks). Null values are forced to level 0 (disagree) to match
    ``comparison_vector``.
    """
    if exclude_pairs is None:
        exclude_pairs = set()

    row_ids = block_df["__row_id__"].to_list()
    n = len(row_ids)
    if n < 2:
        return []

    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0

    max_weight = sum(max(em_result.match_weights[f.field]) for f in mk.fields)
    min_weight = sum(min(em_result.match_weights[f.field]) for f in mk.fields)
    weight_range = max_weight - min_weight

    if mk.link_threshold is not None:
        link_threshold = mk.link_threshold
    else:
        link_threshold, _ = compute_thresholds(em_result, calibrated=calibrated)

    # Accumulate the total match-weight matrix field by field.
    total_weight = np.zeros((n, n), dtype=np.float64)
    for f in mk.fields:
        vals = _field_values_for_block(block_df, f, n)
        weights = np.asarray(em_result.match_weights[f.field], dtype=np.float64)
        sim = np.asarray(_field_score_matrix(vals, f.scorer), dtype=np.float64)
        lvl = _levels_from_similarity(sim, int(f.levels), float(f.partial_threshold))
        # Null on either side -> level 0 (disagree), matching comparison_vector.
        null_mask = np.array([v is None for v in vals], dtype=bool)
        if null_mask.any():
            either_null = null_mask[:, None] | null_mask[None, :]
            lvl = np.where(either_null, 0, lvl)
        total_weight += weights[lvl]
        _apply_tf_adjustment(total_weight, vals, lvl, f, em_result, n)

    if calibrated:
        logodds = prior_w + total_weight
        with np.errstate(over="ignore"):
            normalized = 1.0 / (1.0 + np.power(2.0, -np.clip(logodds, -60.0, 60.0)))
    elif weight_range > 0:
        # TF adjustment can push the summed weight past the per-level max, so
        # clip into [0, 1] to preserve the score contract.
        normalized = np.clip((total_weight - min_weight) / weight_range, 0.0, 1.0)
    else:
        normalized = np.full((n, n), 0.5, dtype=np.float64)

    # Emit upper-triangle pairs at/above threshold.
    iu, ju = np.triu_indices(n, k=1)
    keep = normalized[iu, ju] >= link_threshold
    if not keep.any():
        return []
    ids = np.asarray(row_ids)
    a_ids = ids[iu[keep]]
    b_ids = ids[ju[keep]]
    scores = normalized[iu, ju][keep]

    results: list[tuple[int, int, float]] = []
    for a, b, s in zip(a_ids.tolist(), b_ids.tolist(), scores.tolist()):
        pair_key = (a, b) if a < b else (b, a)
        if pair_key in exclude_pairs:
            continue
        results.append((a, b, round(float(s), 4)))
    return results


def _fs_vectorized_enabled() -> bool:
    """Whether the vectorized block scorer is enabled (default ON).

    `GOLDENMATCH_FS_VECTORIZED=0` forces the scalar `score_probabilistic`
    path (per-pair Python loop) — an escape hatch for exact scalar parity or
    debugging.
    """
    val = os.environ.get("GOLDENMATCH_FS_VECTORIZED")
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "no", "off", "disabled")


def probabilistic_block_scorer(mk: MatchkeyConfig, em_result: EMResult):
    """Pick the best block-scoring callable for (mk, em_result).

    Returns ``fn(block_df, exclude_pairs=None) -> list[(a, b, score)]``.

    Prefers the vectorized NxN-matrix path when every field scorer can be
    expressed as a matrix (the common case); falls back to the scalar
    ``score_probabilistic`` for matchkeys with model-backed scorers
    (embedding / record_embedding) or when explicitly disabled via
    ``GOLDENMATCH_FS_VECTORIZED=0``.
    """
    use_vec = _fs_vectorized_enabled() and all(
        vectorized_scorer_supported(f.scorer) for f in mk.fields
    )
    if use_vec:
        def _scorer(block_df, exclude_pairs=None):
            return score_probabilistic_vectorized(block_df, mk, em_result, exclude_pairs)
        return _scorer

    def _scalar(block_df, exclude_pairs=None):
        return score_probabilistic(block_df, mk, em_result, exclude_pairs)
    return _scalar


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
        total_weight += em_result.match_weights[f.field][vec[k]]

    if _fs_calibration_mode() == "posterior":
        return posterior_from_weight(total_weight, prior_weight(em_result.proportion_matched))
    if weight_range > 0:
        return (total_weight - min_weight) / weight_range
    return 0.5
