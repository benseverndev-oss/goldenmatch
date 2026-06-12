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
#                 achievable weight range, which has no probabilistic meaning.
#                 This is the default. With full-block scoring it measures
#                 P=0.978 / R=0.958 / F1=0.968 on DBLP-ACM (the old "57.6%
#                 recall" was a block-skip artifact, not the calibration).
#
#   "posterior" — the true FS match probability:
#                     logodds = log2(λ/(1-λ)) + W
#                     p       = 1 / (1 + 2^(-logodds))
#                 where λ is the EM-estimated within-block match rate
#                 (em_result.proportion_matched). The score is an actual
#                 calibrated probability. Note the 0.50 Bayes cut is too
#                 permissive in practice: blocking inflates the within-block
#                 prior λ, so post-block pairs clear 0.50 easily. Measured on
#                 DBLP-ACM, posterior matches linear's F1 only at a cut >= ~0.9
#                 (0.50 -> F1 0.936; 0.99 -> F1 0.968, P 0.984). So the
#                 calibrated default cut is 0.99, not 0.50 (compute_thresholds).
#
# Default resolved by `_fs_calibration_mode()`. `GOLDENMATCH_FS_CALIBRATED`
# overrides: "1"/"posterior" -> posterior, "0"/"linear" -> linear.
# Default stays "linear": posterior ties (not beats) linear on F1, and flipping
# the headline score from a normalized weight to a probability shifts the value
# distribution the downstream clustering thresholds are tuned against — that
# belongs in the Phase 4 calibration work, not here. Sweep:
# scripts/bench_fs_calibration.py.
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


def _isotonic_nondecreasing(values: list[float]) -> list[float]:
    """Pool-adjacent-violators isotonic regression (non-decreasing).

    Returns the non-decreasing sequence closest to ``values`` in squared
    error — the minimal-change monotone projection. Used to enforce that
    Fellegi-Sunter match weights increase with agreement level.
    """
    # Each block: [mean, pooled weight (count), size].
    blocks: list[list[float]] = []
    for v in values:
        blocks.append([float(v), 1.0, 1])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, s2 = blocks.pop()
            v1, w1, s1 = blocks.pop()
            merged = (v1 * w1 + v2 * w2) / (w1 + w2)
            blocks.append([merged, w1 + w2, int(s1 + s2)])
    out: list[float] = []
    for mean, _w, size in blocks:
        out.extend([mean] * int(size))
    return out


def _fs_monotonic_mode() -> str:
    """Resolve the FS match-weight monotonicity mode: 'warn' | 'enforce' | 'off'.

    Default is 'warn': detect a non-monotonic weight vector (a rare middle
    level outweighing exact agreement) and log it, but leave the EM weights
    untouched. This mirrors Splink, which surfaces non-monotonicity rather
    than silently mangling it — and on measured data (DBLP-ACM) isotonically
    pooling the inverted levels *regresses* F1 (0.968 -> 0.941, precision
    0.978 -> 0.896), because near-exact title agreement is genuinely more
    discriminative there than perfect agreement.

    ``GOLDENMATCH_FS_MONOTONIC``:
      - unset / 'warn'                 -> detect + warn, do not modify (default)
      - 'enforce' / '1' / 'true' / 'on'-> apply isotonic (PAV) repair
      - '0' / 'off' / 'disabled'       -> no detection, no warning
    """
    val = os.environ.get("GOLDENMATCH_FS_MONOTONIC")
    if val is None:
        return "warn"
    v = val.strip().lower()
    if v in ("0", "false", "no", "off", "disabled"):
        return "off"
    if v in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "warn"


def enforce_weight_monotonicity(
    match_weights: dict[str, list[float]],
    skip_fields: list[str] | None = None,
) -> tuple[dict[str, list[float]], list[str]]:
    """Make each field's match weights non-decreasing across agreement levels.

    Fellegi-Sunter weights are log2(m/u) per comparison level; with levels
    ordered disagree -> ... -> exact-agree, a well-specified model has weights
    that increase with the level. EM estimates m and u per level independently,
    so a rare-but-discriminative middle level can outweigh exact agreement
    (observed on DBLP-ACM title: partial 28.6 bits > exact 11.9 bits). That
    inversion means "partial agreement is stronger evidence than exact
    agreement", which is almost always a level-discretization artifact.

    Applies pool-adjacent-violators isotonic regression per field. Returns the
    (possibly adjusted) weights and the list of fields that changed.
    ``skip_fields`` (e.g. blocking fields, whose weights are fixed and already
    monotone) are passed through untouched.
    """
    skip = set(skip_fields or [])
    adjusted: list[str] = []
    out: dict[str, list[float]] = {}
    for field, weights in match_weights.items():
        if field in skip or len(weights) < 2:
            out[field] = list(weights)
            continue
        fixed = _isotonic_nondecreasing(weights)
        if any(abs(a - b) > 1e-9 for a, b in zip(fixed, weights)):
            adjusted.append(field)
        out[field] = fixed
    return out, adjusted


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

    # Bumped when the serialized shape changes incompatibly.
    SCHEMA_VERSION = 1

    def to_dict(self) -> dict:
        """JSON-serializable snapshot of the trained model.

        Every field is already JSON-native (dicts of float lists, scalars),
        so this is a plain projection plus a version/type marker for
        forward-compatible loading.
        """
        return {
            "__type__": "goldenmatch.EMResult",
            "__version__": EMResult.SCHEMA_VERSION,
            "m_probs": self.m_probs,
            "u_probs": self.u_probs,
            "match_weights": self.match_weights,
            "converged": self.converged,
            "iterations": self.iterations,
            "proportion_matched": self.proportion_matched,
            "tf_freqs": self.tf_freqs,
            "tf_collision": self.tf_collision,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EMResult:
        """Reconstruct an EMResult from :meth:`to_dict` output."""
        version = data.get("__version__", 1)
        if version > cls.SCHEMA_VERSION:
            raise ValueError(
                f"FS model schema version {version} is newer than this "
                f"goldenmatch supports ({cls.SCHEMA_VERSION}); upgrade goldenmatch."
            )
        try:
            return cls(
                m_probs=data["m_probs"],
                u_probs=data["u_probs"],
                match_weights=data["match_weights"],
                converged=data["converged"],
                iterations=data["iterations"],
                proportion_matched=data["proportion_matched"],
                tf_freqs=data.get("tf_freqs"),
                tf_collision=data.get("tf_collision"),
            )
        except KeyError as exc:
            raise ValueError(f"FS model dict is missing required key: {exc}") from exc

    def save_json(self, path: str) -> None:
        """Persist the trained model to ``path`` as JSON (atomic write)."""
        import json
        import tempfile

        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @classmethod
    def load_json(cls, path: str) -> EMResult:
        """Load a trained model previously written by :meth:`save_json`."""
        import json

        with open(path) as fh:
            return cls.from_dict(json.load(fh))

    def validate_for(self, mk: MatchkeyConfig) -> None:
        """Raise if this model can't score ``mk`` (field / level mismatch).

        A persisted model is only reusable against the same matchkey shape:
        every field must have a match-weight vector whose length equals the
        field's level count. Mismatch means the config changed since training,
        so fail loudly rather than silently scoring with a stale model.
        """
        for f in mk.fields:
            weights = self.match_weights.get(f.field)
            if weights is None:
                raise FSModelMismatchError(
                    f"Persisted FS model has no weights for field '{f.field}'. "
                    f"The matchkey changed since training — retrain or clear the "
                    f"model_path."
                )
            if len(weights) != f.levels:
                raise FSModelMismatchError(
                    f"Persisted FS model for field '{f.field}' has {len(weights)} "
                    f"levels but the matchkey expects {f.levels}. Retrain or clear "
                    f"the model_path."
                )


class FSModelMismatchError(ValueError):
    """A persisted FS model is incompatible with the matchkey being scored."""


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

    Within-block pairs have a far higher true-match rate than random pairs, so
    they're what EM needs to estimate m. We only need ``n_pairs`` of them. The
    blocks are visited in RANDOM order and we STOP once enough pairs are
    gathered — the old loop enumerated EVERY within-block pair across EVERY
    block (O(Σ size_i^2): ~140M tuples on a 6M-row / 150K-block run) and then
    sampled down to ``n_pairs``, which dominated ``train_em`` wall at scale
    (~110s of a 117s EM step). A few dozen random blocks give a representative,
    block-stratified sample. Deterministic (fixed ``seed``).
    """
    rng = random.Random(seed)
    # Determinism: `blocks` can arrive in a non-deterministic order (parallel /
    # hash-bucketed construction; varies by machine/core-count), so a seeded
    # shuffle of bare indices still draws a different training sample run-to-run.
    # Sort by the stable, already-computed block_key FIRST so the seeded shuffle
    # permutes a canonical order -> reproducible sample (no extra collect; block_key
    # is a string attribute set at construction). Ties (rare cross-pass key
    # collisions) fall back to the original index, adjacent same-key blocks being
    # near-equivalent anyway.
    order = sorted(range(len(blocks)), key=lambda i: (str(getattr(blocks[i], "block_key", "")), i))
    rng.shuffle(order)
    # Headroom over n_pairs so the post-dedup downsample still has enough to
    # draw from even when blocks overlap or are tiny.
    target = n_pairs * 3
    all_block_pairs: list[tuple[int, int]] = []

    for bi in order:
        block = blocks[bi]
        block_df = block.df.collect() if hasattr(block.df, 'collect') else block.df
        row_ids = sorted(block_df["__row_id__"].to_list())  # canonical order before the seeded sample
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
        if len(all_block_pairs) >= target:
            break

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

    # Guard: FS match weights are expected non-decreasing in agreement level.
    # EM can invert a rare-but-discriminative middle level above exact
    # agreement. Default 'warn' surfaces it (Splink posture) without changing
    # the weights; 'enforce' isotonically repairs them (measured to trade F1
    # on some data — opt in deliberately).
    _mono_mode = _fs_monotonic_mode()
    if _mono_mode != "off":
        repaired, adjusted = enforce_weight_monotonicity(
            match_weights, skip_fields=blocking_fields,
        )
        if adjusted and _mono_mode == "enforce":
            match_weights = repaired
            logger.warning(
                "FS match weights were non-monotonic; isotonically repaired "
                "field(s): %s (GOLDENMATCH_FS_MONOTONIC=enforce)",
                ", ".join(adjusted),
            )
        elif adjusted:
            logger.warning(
                "FS match weights are non-monotonic for field(s): %s — a partial "
                "agreement level outweighs exact agreement. Left as-is (Splink "
                "posture); set GOLDENMATCH_FS_MONOTONIC=enforce to isotonically "
                "repair, or inspect the level thresholds.", ", ".join(adjusted),
            )

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


def load_or_train_em(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    *,
    blocks: list | None = None,
    blocking_fields: list[str] | None = None,
    max_iterations: int | None = None,
    convergence: float | None = None,
) -> EMResult:
    """Return a trained EMResult, reusing ``mk.model_path`` when present.

    Splink-style train-once -> reuse: when ``mk.model_path`` is set and the
    file exists, the persisted model is loaded, validated against ``mk`` (field
    / level shape), and EM is skipped. When the path is set but absent, EM runs
    and the trained model is saved there for next time. With no ``model_path``
    this is exactly ``train_em``. The single seam all three pipeline call sites
    (core pipeline x2, TUI engine) share.
    """
    path = getattr(mk, "model_path", None)
    if path and os.path.exists(path):
        em = EMResult.load_json(path)
        em.validate_for(mk)  # raises FSModelMismatchError on shape mismatch
        logger.info("Loaded FS model from %s (skipped EM training)", path)
        return em

    em = train_em(
        df, mk,
        max_iterations=max_iterations if max_iterations is not None else mk.em_iterations,
        convergence=convergence if convergence is not None else mk.convergence_threshold,
        blocks=blocks,
        blocking_fields=blocking_fields,
    )
    if path:
        em.save_json(path)
        logger.info("Saved FS model to %s", path)
    return em


def estimate_m_from_labels(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    labels: list[tuple[int, int]],
    *,
    n_sample_pairs: int = 10000,
    blocking_fields: list[str] | None = None,
    proportion_matched: float | None = None,
    smoothing: float = 1.0,
    seed: int = 42,
) -> EMResult:
    """Estimate Fellegi-Sunter m-probabilities directly from labeled matches.

    The supervised analog of :func:`train_em` (Splink's
    ``estimate_m_from_label_column``). Given known true-match pairs, m is the
    observed comparison-level frequency among those matches — no EM iteration,
    no convergence risk. u is still estimated from random pairs (overwhelmingly
    non-matches), exactly as the unsupervised path does. This is usually the
    single biggest accuracy lever in record linkage: even a few hundred labels
    pin m far better than EM can infer it unsupervised.

    Args:
        df: DataFrame with ``__row_id__`` and the matchkey field columns.
        mk: Probabilistic matchkey config.
        labels: known true-match pairs as ``(row_id_a, row_id_b)``. Ordering and
            duplicates don't matter; ids absent from ``df`` are dropped.
        n_sample_pairs: random pairs sampled for the u estimate.
        blocking_fields: fields used for blocking — given fixed weights (they
            always agree within a block, so carry no learnable signal), matching
            :func:`train_em`.
        proportion_matched: within-block match-rate prior for posterior
            calibration. Labels don't reveal the candidate universe, so this
            defaults to a conservative 0.02 (the linear default calibration
            ignores it; posterior users should pass the real rate).
        smoothing: Laplace pseudo-count per level for the m estimate — guards a
            level unseen among few labels from forcing m=0 (an infinite weight).
        seed: random seed for the u-estimate pair sampling.

    Returns:
        EMResult (``iterations=0``, ``converged=True`` — a direct estimate).
        Persist it with :meth:`EMResult.save_json` and reuse via
        ``MatchkeyConfig.model_path`` (see :func:`load_or_train_em`).
    """
    if blocking_fields is None:
        blocking_fields = []

    cols = [f.field for f in mk.fields if f.field != "__record__"]
    row_lookup: dict[int, dict] = {}
    for row in df.select(["__row_id__"] + cols).to_dicts():
        row_lookup[row["__row_id__"]] = row

    # Keep only labels whose ids are present and distinct; canonicalize + dedup.
    valid = {
        (min(a, b), max(a, b))
        for a, b in labels
        if a != b and a in row_lookup and b in row_lookup
    }
    if not valid:
        raise ValueError(
            "estimate_m_from_labels: no usable labeled pairs (label ids must "
            "exist in df['__row_id__'] and differ)."
        )
    label_pairs = list(valid)
    if len(label_pairs) < 20:
        logger.warning(
            "estimate_m_from_labels: only %d labeled pair(s) — m estimates may "
            "be noisy (smoothing=%.3g mitigates).", len(label_pairs), smoothing,
        )

    # ── u from RANDOM pairs (mirrors train_em Step 1) ──
    random_pairs = _sample_pairs(df, min(n_sample_pairs, 5000), seed)
    u_probs: dict[str, list[float]] = {}
    if len(random_pairs) >= 10:
        random_matrix = _build_comparison_matrix(random_pairs, row_lookup, mk)
        for j, f in enumerate(mk.fields):
            counts = [float((random_matrix[:, j] == lvl).sum()) for lvl in range(f.levels)]
            total = sum(counts) + f.levels * 1e-6
            u_probs[f.field] = [(c + 1e-6) / total for c in counts]
    else:
        u_probs = _fallback_result(mk).u_probs
    # Blocking fields: neutral u (random pairs give a biased u for them).
    for f in mk.fields:
        if f.field in blocking_fields:
            u_probs[f.field] = [0.50, 0.50] if f.levels == 2 else [0.34, 0.33, 0.33]

    # ── m from LABELED matches: observed level frequency (Laplace smoothed) ──
    label_matrix = _build_comparison_matrix(label_pairs, row_lookup, mk)
    m_probs: dict[str, list[float]] = {}
    for j, f in enumerate(mk.fields):
        counts = [float((label_matrix[:, j] == lvl).sum()) for lvl in range(f.levels)]
        total = sum(counts) + f.levels * smoothing
        m_probs[f.field] = [(c + smoothing) / total for c in counts]

    # ── match weights (blocking fixed) — mirrors train_em ──
    match_weights: dict[str, list[float]] = {}
    for f in mk.fields:
        if f.field in blocking_fields:
            n = f.levels
            match_weights[f.field] = [
                -3.0 + 6.0 * k / (n - 1) if n > 1 else 3.0 for k in range(n)
            ]
            continue
        match_weights[f.field] = [
            math.log2(max(m_probs[f.field][k], 1e-10) / max(u_probs[f.field][k], 1e-10))
            for k in range(f.levels)
        ]

    _mono_mode = _fs_monotonic_mode()
    if _mono_mode != "off":
        repaired, adjusted = enforce_weight_monotonicity(
            match_weights, skip_fields=blocking_fields,
        )
        if adjusted and _mono_mode == "enforce":
            match_weights = repaired
            logger.warning(
                "Supervised FS match weights non-monotonic; isotonically "
                "repaired field(s): %s (GOLDENMATCH_FS_MONOTONIC=enforce)",
                ", ".join(adjusted),
            )
        elif adjusted:
            logger.warning(
                "Supervised FS match weights non-monotonic for field(s): %s — a "
                "partial level outweighs exact agreement (left as-is).",
                ", ".join(adjusted),
            )

    tf_freqs, tf_collision = _build_tf_tables(df, mk)

    logger.info("Estimated m from %d labeled pairs (supervised; no EM)", len(label_pairs))
    return EMResult(
        m_probs=m_probs,
        u_probs=u_probs,
        match_weights=match_weights,
        converged=True,
        iterations=0,
        proportion_matched=proportion_matched if proportion_matched is not None else 0.02,
        tf_freqs=tf_freqs,
        tf_collision=tf_collision,
    )


def labels_from_corrections(corrections) -> list[tuple[int, int]]:
    """Positive-match ``(id_a, id_b)`` labels from memory-store corrections.

    Duck-typed over :class:`goldenmatch.core.memory.store.Correction` (any
    object with ``id_a``, ``id_b``, ``decision``). Keeps only ``approve``
    verdicts — confirmed true matches — which is what supervises m.
    """
    out: list[tuple[int, int]] = []
    for c in corrections:
        if str(getattr(c, "decision", "")).lower() == "approve":
            out.append((int(c.id_a), int(c.id_b)))
    return out


def labels_from_review_items(items) -> list[tuple[int, int]]:
    """Positive-match labels from review-queue items with ``status='approved'``.

    Duck-typed over :class:`goldenmatch.core.review_queue.ReviewItem`.
    """
    out: list[tuple[int, int]] = []
    for it in items:
        if str(getattr(it, "status", "")).lower() == "approved":
            out.append((int(it.id_a), int(it.id_b)))
    return out


def labels_from_memory_store(store, dataset: str | None = None) -> list[tuple[int, int]]:
    """Convenience: pull approved-match labels straight from a MemoryStore.

    Duck-typed over any store exposing ``get_corrections(dataset)``.
    """
    return labels_from_corrections(store.get_corrections(dataset))


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
    are interpreted as probabilities. The link cut is 0.99, NOT the 0.5 Bayes
    boundary: blocking inflates the within-block prior λ, so post-block pairs
    clear 0.5 trivially (DBLP-ACM at 0.5 -> F1 0.936 vs 0.968 at 0.99 — see
    scripts/bench_fs_calibration.py). The score is already a probability so no
    percentile rescaling is applied.
    """
    if calibrated is None:
        calibrated = _fs_calibration_mode() == "posterior"
    if calibrated:
        # Posterior scores are calibrated probabilities; thresholds are
        # absolute, not distribution-relative. 0.99 is the measured-best link
        # cut on post-block pairs (the 0.5 Bayes boundary is too permissive
        # once blocking inflates the prior).
        return 0.99, 0.50
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


def _field_score_matrix_dedup(vals: list[str | None], scorer: str) -> np.ndarray:
    """``_field_score_matrix`` over the DISTINCT values, expanded back to NxN.

    Field similarity depends only on the ``(value_a, value_b)`` pair, so scoring
    the unique values once and gathering through an index map is bit-identical
    to scoring the full list — but it collapses repeated values (and a constant
    blocking-key field to a 1x1 matrix), shrinking the per-field cdist / native
    kernel call that dominates FS block scoring. No-op when all values are
    distinct (returns the full-list matrix directly).
    """
    n = len(vals)
    index = np.empty(n, dtype=np.intp)
    seen: dict[object, int] = {}
    uniq: list[str | None] = []
    for i, v in enumerate(vals):
        j = seen.get(v)
        if j is None:
            j = len(uniq)
            seen[v] = j
            uniq.append(v)
        index[i] = j
    if len(uniq) == n:
        return np.asarray(_field_score_matrix(vals, scorer), dtype=np.float64)
    sub = np.asarray(_field_score_matrix(uniq, scorer), dtype=np.float64)
    # Two distinct rows sharing a value collapse to the SAME unique index, so
    # the expansion reads that value's diagonal cell. For multiplicity-based
    # matrices (exact / soundex) ``_field_score_matrix`` leaves a singleton's
    # diagonal at 0, which would zero-out equal-value pairs that the full matrix
    # scores 1.0. The true self-similarity of any non-embedding scorer is 1.0
    # (an identical string), so pin the diagonal before gathering. No-op for the
    # cdist scorers (their diagonal is already 1.0).
    np.fill_diagonal(sub, 1.0)
    return sub[np.ix_(index, index)]


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
        sim = _field_score_matrix_dedup(vals, f.scorer)
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


def _fs_batch_rows() -> int:
    """Row cap for batched FS block scoring (``GOLDENMATCH_FS_BATCH_ROWS``).

    Small blocks are coalesced up to this many rows so one set of per-field
    matrices covers many blocks, amortizing the per-call FFI/marshal overhead
    that dominates on the tiny blocks multi-pass blocking produces. Larger caps
    cut call count further but grow the discarded cross-block compute (the
    diagonal sub-blocks are kept; off-diagonal cells are computed and ignored).
    """
    try:
        return max(2, int(os.environ.get("GOLDENMATCH_FS_BATCH_ROWS", "512")))
    except ValueError:
        return 512


def score_probabilistic_vectorized_batch(
    block_dfs: list,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Score several blocks with one set of per-field matrices.

    Concatenates the batch's per-field values into length-S lists, computes one
    (value-deduped) SxS matrix per field, then slices each block's DIAGONAL
    sub-matrix to emit pairs. A within-block cell ``[i, j]`` is computed from the
    same value pair as scoring that block alone, so the emitted ``(a, b, score)``
    set is IDENTICAL to per-block scoring (``score_probabilistic_vectorized``);
    the off-diagonal cross-block cells are computed and discarded. Equal-valued
    rows across blocks collapse under the dedup, so the batch matrix is usually
    far smaller than SxS. Amortizes the per-call overhead that dominates the FS
    wall on tiny blocks.
    """
    if exclude_pairs is None:
        exclude_pairs = set()

    spans: list[tuple[int, int]] = []
    row_ids: list[int] = []
    start = 0
    for bdf in block_dfs:
        rid = bdf["__row_id__"].to_list()
        spans.append((start, start + len(rid)))
        row_ids.extend(rid)
        start += len(rid)
    S = len(row_ids)
    if S < 2:
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

    total_weight = np.zeros((S, S), dtype=np.float64)
    for f in mk.fields:
        vals: list[str | None] = []
        for bdf, (s, e) in zip(block_dfs, spans):
            vals.extend(_field_values_for_block(bdf, f, e - s))
        weights = np.asarray(em_result.match_weights[f.field], dtype=np.float64)
        sim = _field_score_matrix_dedup(vals, f.scorer)
        lvl = _levels_from_similarity(sim, int(f.levels), float(f.partial_threshold))
        null_mask = np.array([v is None for v in vals], dtype=bool)
        if null_mask.any():
            either_null = null_mask[:, None] | null_mask[None, :]
            lvl = np.where(either_null, 0, lvl)
        total_weight += weights[lvl]
        _apply_tf_adjustment(total_weight, vals, lvl, f, em_result, S)

    if calibrated:
        logodds = prior_w + total_weight
        with np.errstate(over="ignore"):
            normalized = 1.0 / (1.0 + np.power(2.0, -np.clip(logodds, -60.0, 60.0)))
    elif weight_range > 0:
        normalized = np.clip((total_weight - min_weight) / weight_range, 0.0, 1.0)
    else:
        normalized = np.full((S, S), 0.5, dtype=np.float64)

    ids = np.asarray(row_ids)
    results: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    for (s, e) in spans:
        nb = e - s
        if nb < 2:
            continue
        sub = normalized[s:e, s:e]
        iu, ju = np.triu_indices(nb, k=1)
        sel = sub[iu, ju] >= link_threshold
        if not sel.any():
            continue
        a_ids = ids[s + iu[sel]]
        b_ids = ids[s + ju[sel]]
        sc = sub[iu, ju][sel]
        for a, b, score in zip(a_ids.tolist(), b_ids.tolist(), sc.tolist()):
            pair_key = (a, b) if a < b else (b, a)
            if pair_key in exclude_pairs or pair_key in seen:
                continue
            seen.add(pair_key)
            results.append((a, b, round(float(score), 4)))
    return results


def score_probabilistic_blocks_batched(
    blocks,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
    cap: int | None = None,
):
    """Collect + score ``blocks`` in row-capped batches via the SxS batch scorer.

    Threads the running exclude set across batches so a pair emitted in an
    earlier batch is suppressed later — matching the per-block loop's
    block-by-block ``matched_pairs`` dedup. Falls back to the per-block scorer
    when the vectorized numpy path isn't active (native FS kernel / scalar /
    model-backed scorers), since the batching is a numpy-path optimization.
    Does NOT mutate the caller's ``exclude_pairs``; the caller folds the returned
    pairs into ``matched_pairs`` as before.
    """
    import polars as pl

    if exclude_pairs is None:
        exclude_pairs = set()
    if cap is None:
        cap = _fs_batch_rows()

    use_vec = (
        not _fs_native_eligible(mk)
        and _fs_vectorized_enabled()
        and all(vectorized_scorer_supported(f.scorer) for f in mk.fields)
    )
    excl = set(exclude_pairs)
    results: list[tuple[int, int, float]] = []

    if not use_vec:
        scorer = probabilistic_block_scorer(mk, em_result)
        for block in blocks:
            bdf = block.df.collect() if isinstance(block.df, pl.LazyFrame) else block.df
            pairs = scorer(bdf, excl)
            for a, b, _s in pairs:
                excl.add((min(a, b), max(a, b)))
            results.extend(pairs)
        return results

    batch: list = []
    rows = 0

    def _flush():
        nonlocal batch, rows
        if not batch:
            return
        pairs = score_probabilistic_vectorized_batch(batch, mk, em_result, excl)
        for a, b, _s in pairs:
            excl.add((min(a, b), max(a, b)))
        results.extend(pairs)
        batch = []
        rows = 0

    for block in blocks:
        bdf = block.df.collect() if isinstance(block.df, pl.LazyFrame) else block.df
        h = bdf.height
        if batch and rows + h > cap:
            _flush()
        batch.append(bdf)
        rows += h
        if rows >= cap:
            _flush()
    _flush()
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


# Scorer-name -> native kernel id for the FS kernel (score_one ids 0..=3).
# soundex/embedding/record_embedding are absent on purpose — those fields force
# the numpy fallback (the kernel's score_one doesn't implement them).
_NATIVE_FS_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3,
}


def _fs_native_enabled() -> bool:
    """Whether the native FS block kernel is active. **Opt-in, default OFF.**

    Enable with `GOLDENMATCH_FS_NATIVE=1` (also needs the native ext built).
    Unlike the weighted native kernel (default ON), FS stays opt-in because its
    DISCRETE comparison levels amplify the tiny rapidfuzz-rs-vs-Python-rapidfuzz
    float differences: a similarity sitting exactly on a `partial_threshold`
    (token_sort ratios are rationals like 0.7 / 0.857, so this is common) can
    flip a level between the two libraries, and with EM weights that can span
    ~40 bits a single level flip swings the normalized score ~0.45 and can move
    a pair across the link threshold. The numpy vectorized path is the default
    so FS output is reproducible; native is a measured ~2.9x opt-in speedup for
    callers who accept boundary-level differences (same CLASS as the documented
    vectorized-vs-scalar FS parity, larger in per-pair magnitude).
    """
    val = os.environ.get("GOLDENMATCH_FS_NATIVE")
    if val is None or val.strip().lower() not in ("1", "true", "yes", "on", "enabled"):
        return False
    from goldenmatch.core._native_loader import native_enabled
    return native_enabled("block_scoring")


def _fs_native_eligible(mk: MatchkeyConfig) -> bool:
    """Whether (mk) can use the native FS kernel.

    Every field scorer must be one the kernel's score_one implements, and no
    field may opt into TF adjustment (the kernel doesn't carry the per-value
    frequency tables — those fields stay on the numpy path).
    """
    if not _fs_native_enabled():
        return False
    if not mk.fields:
        return False
    for f in mk.fields:
        if f.scorer not in _NATIVE_FS_SCORER_IDS:
            return False
        if getattr(f, "tf_adjustment", False):
            return False
    try:
        from goldenmatch.core._native_loader import native_module
        return hasattr(native_module(), "score_block_pairs_fs")
    except Exception:
        return False


def score_probabilistic_native(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Score one block via the native Rust FS kernel (``score_block_pairs_fs``).

    Output-equivalent (within rapidfuzz tolerance) to
    ``score_probabilistic_vectorized``: same transformed values
    (``_field_values_for_block``), same level mapping, same EM match weights,
    same calibration + threshold. The kernel replaces the per-field numpy NxN
    matrices with a single GIL-released per-pair Rust loop. Caller gates on
    ``_fs_native_eligible``.
    """
    from goldenmatch.core._native_loader import native_module

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
        link_threshold = float(mk.link_threshold)
    else:
        link_threshold, _ = compute_thresholds(em_result, calibrated=calibrated)

    field_values = [_field_values_for_block(block_df, f, n) for f in mk.fields]
    scorer_ids = [_NATIVE_FS_SCORER_IDS[f.scorer] for f in mk.fields]
    levels = [int(f.levels) for f in mk.fields]
    partials = [float(f.partial_threshold) for f in mk.fields]
    weights = [[float(w) for w in em_result.match_weights[f.field]] for f in mk.fields]
    # Kernel canonicalizes pair_key to (min,max); pass exclude pre-canonicalized.
    excl = [(a, b) if a < b else (b, a) for a, b in exclude_pairs]

    pairs = native_module().score_block_pairs_fs(
        row_ids, [n], field_values, scorer_ids, levels, partials, weights,
        calibrated, prior_w, min_weight, weight_range, link_threshold, excl,
    )
    return [(a, b, round(float(s), 4)) for a, b, s in pairs]


def probabilistic_block_scorer(mk: MatchkeyConfig, em_result: EMResult):
    """Pick the best block-scoring callable for (mk, em_result).

    Returns ``fn(block_df, exclude_pairs=None) -> list[(a, b, score)]``.

    Preference order: native Rust FS kernel (when built + all scorers are
    jaro_winkler/levenshtein/token_sort/exact + no TF adjustment) -> vectorized
    NxN-matrix numpy path -> scalar ``score_probabilistic`` (model-backed
    scorers or ``GOLDENMATCH_FS_VECTORIZED=0``).
    """
    if _fs_native_eligible(mk):
        def _native(block_df, exclude_pairs=None):
            return score_probabilistic_native(block_df, mk, em_result, exclude_pairs)
        return _native

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


# ── FS explainability: match-weight waterfall (Phase 2) ─────────────────────


@dataclass
class FSFieldContribution:
    """One field's contribution to a Fellegi-Sunter pair score."""

    field: str
    scorer: str
    value_a: str | None
    value_b: str | None
    level: int            # comparison level (0=disagree .. n_levels-1=exact)
    n_levels: int
    m: float              # P(level | match)
    u: float              # P(level | non-match)
    weight_bits: float    # log2(m/u) — the bits this field adds to the score


@dataclass
class FSWaterfall:
    """Per-comparison Fellegi-Sunter decomposition of a pair score.

    The Splink-style match-weight waterfall: a starting prior (bits), one
    signed bit contribution per field, summing to the total match weight, then
    the posterior probability. ``prior_bits + total_weight_bits == final_bits``
    and ``posterior == 1 / (1 + 2**-final_bits)`` by construction.
    """

    fields: list[FSFieldContribution]
    prior_bits: float
    total_weight_bits: float
    final_bits: float
    posterior: float
    proportion_matched: float


def explain_pair_fs(
    row_a: dict,
    row_b: dict,
    mk: MatchkeyConfig,
    em_result: EMResult,
) -> FSWaterfall:
    """Decompose a pair's Fellegi-Sunter score into per-field bit contributions.

    Uses the SAME comparison vector + match weights the scorer uses
    (:func:`score_probabilistic`), so ``total_weight_bits`` equals the summed
    weight that produces the pair score. m/u are surfaced per field for
    transparency; ``weight_bits`` is authoritative (it is what scoring sums, and
    for blocking fields is a fixed prior, not literally log2(m/u)).
    """
    vec = comparison_vector(row_a, row_b, mk)
    contribs: list[FSFieldContribution] = []
    total = 0.0
    for k, f in enumerate(mk.fields):
        level = vec[k]
        weights = em_result.match_weights.get(f.field, [])
        wbits = float(weights[level]) if level < len(weights) else 0.0
        m_list = em_result.m_probs.get(f.field, [])
        u_list = em_result.u_probs.get(f.field, [])
        m = float(m_list[level]) if level < len(m_list) else float("nan")
        u = float(u_list[level]) if level < len(u_list) else float("nan")
        va = row_a.get(f.field)
        vb = row_b.get(f.field)
        contribs.append(FSFieldContribution(
            field=f.field,
            scorer=getattr(f, "scorer", "?") or "?",
            value_a=str(va) if va is not None else None,
            value_b=str(vb) if vb is not None else None,
            level=int(level),
            n_levels=int(f.levels),
            m=m,
            u=u,
            weight_bits=wbits,
        ))
        total += wbits
    prior = prior_weight(em_result.proportion_matched)
    return FSWaterfall(
        fields=contribs,
        prior_bits=prior,
        total_weight_bits=total,
        final_bits=prior + total,
        posterior=posterior_from_weight(total, prior),
        proportion_matched=em_result.proportion_matched,
    )
