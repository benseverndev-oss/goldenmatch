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

import contextvars
import logging
import math
import os
import random
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import MatchkeyConfig, NegativeEvidenceField
from goldenmatch.core.scorer import score_field

logger = logging.getLogger(__name__)

# Training-time semi-supervised label anchors, keyed by matchkey name (or the
# sentinel "*" to apply to every probabilistic matchkey). A two-pass driver
# (run FS -> label the uncertain band -> rerun) sets this via
# ``set_fs_label_anchors(...)`` so ``load_or_train_em`` feeds the labels into
# label-constrained EM without threading them through the user-facing config.
# Default None -> unsupervised EM, byte-identical to the prior behavior.
_FS_LABEL_ANCHORS: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "fs_label_anchors", default=None
)


def set_fs_label_anchors(anchors: dict | None):
    """Set training-time label anchors; returns the ContextVar token to reset.

    ``anchors`` maps matchkey name -> {canonical (a, b) pair: 0/1 label}. Use
    the key ``"*"`` to apply the same anchor set to every probabilistic
    matchkey. Reset with ``reset_fs_label_anchors(token)``."""
    return _FS_LABEL_ANCHORS.set(anchors)


def reset_fs_label_anchors(token) -> None:
    """Restore the label-anchor ContextVar to its prior value."""
    _FS_LABEL_ANCHORS.reset(token)


def _label_anchors_for(mk: MatchkeyConfig) -> dict | None:
    """Resolve the label anchors that apply to ``mk`` (name match, then '*')."""
    anchors = _FS_LABEL_ANCHORS.get()
    if not anchors:
        return None
    name = getattr(mk, "name", None)
    if name is not None and name in anchors:
        return anchors[name]
    return anchors.get("*")

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


# Review band sits this many bits of evidence below the link cut when the
# prior-aware evidence cut is active (W >= c links; W in [c-gap, c) reviews).
_FS_EVIDENCE_REVIEW_GAP_BITS = 3.0

def _fs_evidence_cut() -> float | None:
    """Prior-aware link cut in BITS of net match evidence. **Default None (off).**

    The posterior score is ``σ(prior_w + W)`` where ``W = Σ log2(m/u)`` is the
    endpoint-invariant match evidence and ``prior_w = log2(λ/(1-λ))`` is the
    within-block prior. A FIXED posterior cut (0.99) is equivalent to
    ``W ≥ 6.63 - prior_w``, so blocking's λ inflation LOWERS the evidence bar and
    weak pairs clear it (measured: historical_50k has λ≈0.92, which drops the bar to
    W ≥ 3.12 bits and collapses posterior precision to 0.48).

    Setting ``GOLDENMATCH_FS_EVIDENCE_CUT=c`` makes the link threshold
    ``σ(c + prior_w)``, which is exactly ``W ≥ c`` — a prior- AND endpoint-invariant
    evidence bar. That matters because the DEFAULT linear min-max score renormalizes
    against the summed per-field weight extremes, so any weight change (TF,
    honorific stripping, u-corrections) silently relocates the operating point of
    every pair, including pairs whose evidence on the changed field never moved.
    On this axis the bar does not move. Measured: the post-blocking-u lever swings
    ncvr F1 by -0.162 under the linear default but only +0.025 at c=6.

    Implies posterior scoring (set ``GOLDENMATCH_FS_CALIBRATED=linear`` to override).
    ``c`` is a deliberate user knob, NOT auto-selected: no single constant matches
    every dataset (historical f1 peaks near c=6, its f1_probabilistic near c=9, ncvr
    plateaus at c>=9), and unsupervised selection off EM's own parameters is refuted
    (both a score-histogram anti-mode and a λ-driven quantile measured far worse).
    c=9 (512:1 odds) is the best single value found on the local quality gate.
    Default off is byte-identical.
    """
    val = os.environ.get("GOLDENMATCH_FS_EVIDENCE_CUT")
    if val is None:
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def _fs_calibration_mode() -> str:
    """Return the active FS score-calibration mode: 'posterior' or 'linear'."""
    val = os.environ.get("GOLDENMATCH_FS_CALIBRATED")
    if val is None:
        # An evidence cut is defined on the posterior (log-odds) axis, so it
        # forces posterior scoring unless the caller explicitly picked linear.
        if _fs_evidence_cut() is not None:
            return "posterior"
        return _FS_CALIBRATION_DEFAULT
    v = val.strip().lower()
    if v in ("0", "false", "no", "off", "disabled", "linear"):
        return "linear"
    if v in ("1", "true", "yes", "on", "enabled", "posterior"):
        return "posterior"
    return _FS_CALIBRATION_DEFAULT


def _fs_require_positive_evidence() -> bool:
    """Whether the linear FS scorer refuses to emit a NET-ZERO-EVIDENCE pair — one
    whose summed match weight (log-likelihood ratio) is <= 0, i.e. the evidence
    does NOT favor a match.

    Such a pair agrees only on the (score-excluded) blocking field and disagrees /
    is null on everything else, yet the linear min-max normalization can still map
    its non-positive weight onto a score >= the 0.50 neutral point and AUTO-LINK
    it — chaining true clusters into mega-clusters via union-find. Measured on the
    synthetic person shape: pair-precision collapses 0.85 (5K) -> 0.25 (30K) and
    worsens with N. Requiring strictly positive evidence (LR > 1) to link is the
    Fellegi-Sunter-principled cut and — unlike raising the global link threshold
    to 0.55 — does NOT cut real-but-weak partial matches (which carry positive
    evidence in the 0.50-0.55 band, e.g. historical_50k's corrupted PII).

    ``GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE`` (default ON; ``0``/``off``
    restores the legacy emit-at-neutral behavior). Applies to the LINEAR
    calibration only — the posterior path already folds the prior into the
    log-odds and uses a 0.99 Bayes cut.

    Both the numpy scorer AND the Rust ``fs-core`` kernel (native / the default
    FS route) carry the filter, so native == numpy under the flag. The
    wasm/DuckDB/Postgres surfaces pass ``false`` (legacy) until each opts in +
    regenerates its parity fixture. Measured neutral-or-better on every gated
    dataset (see ``docs/superpowers/specs/2026-07-18-fs-net-zero-evidence-filter.md``).
    """
    v = os.environ.get("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "disabled")


_FS_MISSING_DEFAULT = "unobserved"


def fs_missing_mode(mk: MatchkeyConfig | None = None) -> str:
    """Resolve missing-value semantics: 'unobserved' or 'disagree' (#1846).

    Precedence: GOLDENMATCH_FS_MISSING env > mk.missing > default.

    'unobserved' (#1819/#1834) is textbook Fellegi-Sunter -- a missing value is
    absence of evidence. 'disagree' (pre-#1834) treats it as evidence against a
    match. Which is correct depends on whether missingness is INFORMATIVE in the
    data, so auto-config picks per-dataset from the profiled null rates rather
    than the library imposing one globally. See MatchkeyConfig.missing.
    """
    val = os.environ.get("GOLDENMATCH_FS_MISSING")
    if val is not None:
        v = val.strip().lower()
        if v in ("disagree", "level0", "0"):
            return "disagree"
        if v in ("unobserved", "skip", "1"):
            return "unobserved"
    cfg = getattr(mk, "missing", None) if mk is not None else None
    if cfg in ("unobserved", "disagree"):
        return cfg
    return _FS_MISSING_DEFAULT


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


def _fs_post_blocking_u_enabled() -> bool:
    """Post-blocking bias correction for FS u-probabilities. **Default OFF.**

    u is estimated from RANDOM pairs (global non-matches), but FS only ever scores
    BLOCKED pairs, where blocking-correlated fields agree far more often than at
    random. So a field common among candidates (e.g. authors on a per-author
    blocked corpus) gets a wildly inflated ``log2(m/u)`` weight and drives false
    merges. When ``GOLDENMATCH_FS_POST_BLOCKING_U`` is truthy, the final weights use
    u DECONVOLVED from the blocked population: the observed blocked-pair level rate
    is ``p_match*m + (1-p_match)*u_blocked``, and EM already gives m and p_match, so
    ``u_blocked = (blocked_rate - p_match*m) / (1 - p_match)`` — the non-match
    agreement rate CONDITIONED on blocking. Deflates candidate-common fields toward
    weight ~0, letting the genuinely discriminative fields decide. m-estimation
    (the E-step, on random-pair u) is unchanged; only the final weights shift.
    Default off is byte-identical.
    """
    return os.environ.get("GOLDENMATCH_FS_POST_BLOCKING_U", "0").lower() in (
        "1", "true", "on", "yes", "enabled",
    )


def _deconvolve_post_blocking_u(
    comp_matrix, mk, m_probs, p_match, always_conditioned, u_floor: float = 1e-4,
) -> dict:
    """Per-field u deconvolved from the blocked-pair level distribution (see
    ``_fs_post_blocking_u_enabled``). Returns ``{field: [u per level]}`` for the
    regular (non-blocking) fields; blocking fields keep their fixed prior."""
    pm = min(max(float(p_match), 0.0), 0.99)
    denom = 1.0 - pm
    out: dict = {}
    for j, f in enumerate(mk.fields):
        if f.field in always_conditioned:
            continue
        lv = comp_matrix[:, j]
        obs = float((lv >= 0).sum())
        if obs <= 0 or denom <= 0:
            continue
        m_f = m_probs.get(f.field)
        if m_f is None:
            continue
        u_new = []
        for k in range(int(f.levels)):
            blocked = float((lv == k).sum()) / obs
            u_k = (blocked - pm * m_f[k]) / denom
            u_new.append(max(u_floor, u_k))
        s = sum(u_new)
        if s > 0:
            out[f.field] = [x / s for x in u_new]
    return out


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


def _training_config_manifest(mk: MatchkeyConfig) -> dict:
    """Canonical comparison semantics that a persisted model was trained for."""
    return {
        "fields": [
            {
                "field": f.resolved_field,
                "scorer": f.scorer,
                "transforms": list(f.transforms),
                "model": f.model,
                "columns": list(f.columns) if f.columns is not None else None,
                "column_weights": f.column_weights,
                "levels": f.levels,
                "partial_threshold": f.partial_threshold,
                "level_thresholds": (
                    list(f.level_thresholds)
                    if f.level_thresholds is not None
                    else None
                ),
                "tf_adjustment": f.tf_adjustment,
                "tf_freqs": f.tf_freqs,
            }
            for f in mk.fields
        ],
        "negative_evidence": [
            {
                "field": ne.field,
                "scorer": ne.scorer,
                "transforms": list(ne.transforms),
                "threshold": ne.threshold,
                "penalty_bits": ne.penalty_bits,
                "derive_from": (
                    list(ne.derive_from) if ne.derive_from is not None else None
                ),
            }
            for ne in (mk.negative_evidence or [])
        ],
    }


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
    # Unsupervised link cutoff picked from the training-pair score distribution
    # (GOLDENMATCH_FS_CALIBRATE_THRESHOLD). The fixed 0.50 default over-merges when
    # namesakes pile up just below it (historical_50k: 0.50 F1 0.75 vs 0.55 F1
    # 0.80); this adapts the cutoff per dataset. None = off = fixed default.
    calibrated_link_threshold: float | None = None
    training_config: dict | None = None
    # Non-serialized marker distinguishing a loaded schema-v1 model from an
    # in-memory result constructed before manifests existed.
    _source_schema_version: int | None = None

    # Bumped when the serialized shape changes incompatibly.
    # v2 changes missing regular-field comparisons from disagreement/level 0
    # to unobserved evidence (#1819) AND carries per-pair multi-pass
    # conditioning (#1835). v1 probabilities were trained with nulls folded
    # into level 0 and are therefore not calibration-compatible with v2.
    SCHEMA_VERSION = 2

    def to_dict(self) -> dict:
        """JSON-serializable snapshot of the trained model.

        Every field is already JSON-native (dicts of float lists, scalars),
        so this is a plain projection plus a version/type marker for
        forward-compatible loading.
        """
        version = 2 if self.training_config is not None else 1
        data = {
            "__type__": "goldenmatch.EMResult",
            "__version__": version,
            "m_probs": self.m_probs,
            "u_probs": self.u_probs,
            "match_weights": self.match_weights,
            "converged": self.converged,
            "iterations": self.iterations,
            "proportion_matched": self.proportion_matched,
            "tf_freqs": self.tf_freqs,
            "tf_collision": self.tf_collision,
        }
        if self.calibrated_link_threshold is not None:
            data["calibrated_link_threshold"] = self.calibrated_link_threshold
        if self.training_config is not None:
            data["training_config"] = self.training_config
        return data

    @classmethod
    def from_dict(cls, data: dict) -> EMResult:
        """Reconstruct an EMResult from :meth:`to_dict` output."""
        version = data.get("__version__", 1)
        if version > cls.SCHEMA_VERSION:
            raise ValueError(
                f"FS model schema version {version} is newer than this "
                f"goldenmatch supports ({cls.SCHEMA_VERSION}); upgrade goldenmatch."
            )
        # v1 models LOAD (inspection stays possible) but carry the source
        # version marker; validate_for rejects reuse -- v1 was trained with
        # legacy missing-value semantics (#1819) and has no training-config
        # manifest (#1835).
        try:
            if version >= 2 and "training_config" not in data:
                raise KeyError("training_config")
            return cls(
                m_probs=data["m_probs"],
                u_probs=data["u_probs"],
                match_weights=data["match_weights"],
                converged=data["converged"],
                iterations=data["iterations"],
                proportion_matched=data["proportion_matched"],
                tf_freqs=data.get("tf_freqs"),
                tf_collision=data.get("tf_collision"),
                calibrated_link_threshold=data.get("calibrated_link_threshold"),
                training_config=data.get("training_config"),
                _source_schema_version=version,
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
        """Raise if this model can't score ``mk`` safely.

        Schema-v2 models bind weights to a canonical comparison-semantics
        manifest in addition to field/level shape. Schema-v1 files can still
        be deserialized, but cannot be reused because they carry no manifest.
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
        for ne in (mk.negative_evidence or []):
            if ne.penalty_bits is not None:
                continue  # fixed override -- no EM entry needed
            key = f"__ne__{ne.field}"
            weights = self.match_weights.get(key)
            if weights is None:
                raise FSModelMismatchError(
                    f"Persisted FS model has no weights for negative_evidence "
                    f"field '{ne.field}' (expected key '{key}'). The matchkey "
                    f"added this NE field since training — retrain the model, "
                    f"or set `penalty_bits` on the negative_evidence entry to "
                    f"skip EM for this field."
                )
            if len(weights) != 2:
                raise FSModelMismatchError(
                    f"Persisted FS model for negative_evidence field "
                    f"'{ne.field}' (key '{key}') has {len(weights)} entries but "
                    f"NE weights must be a 2-element [fired, not_fired] list. "
                    f"Retrain or clear the model_path."
                )
        if self.training_config is None:
            if self._source_schema_version == 1:
                raise FSModelMismatchError(
                    "Persisted FS model uses schema v1, which was trained with "
                    "legacy missing-value semantics (nulls folded into level 0) "
                    "and has no training configuration manifest; it cannot be "
                    "safely reused. Retrain the model to write schema v2, or "
                    "clear the model_path."
                )
            return
        if self.training_config != _training_config_manifest(mk):
            raise FSModelMismatchError(
                "Persisted FS model training configuration does not match the "
                "current probabilistic matchkey. A comparison field's scorer, "
                "transforms, thresholds, TF settings, or order changed; retrain "
                "or clear the model_path."
            )


class FSModelMismatchError(ValueError):
    """A persisted FS model is incompatible with the matchkey being scored."""


def comparison_vector(
    row_a: dict,
    row_b: dict,
    mk: MatchkeyConfig,
    field_sims: dict[int, float] | None = None,
) -> list[int]:
    """Compute comparison vector for a pair of records.

    Returns a list of level indices, one per field. ``-1`` means the
    comparison is unobserved because either operand is missing.
    For 2-level fields: 0=disagree, 1=agree
    For 3-level fields: 0=disagree, 1=partial, 2=agree

    ``field_sims`` supplies precomputed similarities for model-backed
    (``embedding`` / ``record_embedding``) fields keyed by field index — those
    scorers can't run through ``score_field``, so the EM E-step
    (:func:`_build_comparison_matrix`) computes their cosine similarity in bulk
    and passes it here. The supplied similarity flows through the SAME level
    thresholds as every other scorer, so training and scoring assign levels
    identically.
    """
    from goldenmatch.utils.transforms import apply_transforms

    levels = []
    for idx, f in enumerate(mk.fields):
        if field_sims is not None and idx in field_sims:
            # Model-backed field: similarity precomputed upstream.
            # A per-field ``embedding`` still treats a null on either side as
            # disagree (level 0), matching the vectorized scorer's null-mask;
            # ``record_embedding`` is record-level and has no single-field null.
            if f.scorer != "record_embedding" and (
                row_a.get(f.field) is None or row_b.get(f.field) is None
            ):
                levels.append(0)
                continue
            s = field_sims[idx]
        else:
            val_a = str(row_a.get(f.field, "")) if row_a.get(f.field) is not None else None
            val_b = str(row_b.get(f.field, "")) if row_b.get(f.field) is not None else None
            # Apply field transforms before scoring (e.g. lowercase, strip)
            if f.transforms:
                val_a = apply_transforms(val_a, f.transforms)
                val_b = apply_transforms(val_b, f.transforms)
            s = score_field(val_a, val_b, f.scorer)

        if s is None:
            # #1846: -1 (unobserved, no evidence) or 0 (disagree) per mk.missing.
            levels.append(-1 if fs_missing_mode(mk) == "unobserved" else 0)
        elif f.level_thresholds is not None:
            level = 0
            for t in f.level_thresholds:
                if s >= t:
                    level += 1
            levels.append(level)
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


def fs_regular_weight_sum(
    match_weights: dict[str, list[float]],
    vec: list[int],
    indexed_fields: list[tuple[int, str]],
) -> float:
    """Sum the FS match-weight bits for the OBSERVED regular fields of one pair.

    ``comparison_vector`` returns ``-1`` for a field that is unobserved on either
    side (the ``missing="unobserved"`` sentinel). Indexing ``weights[-1]`` picks
    the LAST element -- the highest-agreement weight -- so a MISSING field would
    contribute maximal positive evidence FOR a match. Guard it: an unobserved
    field carries no evidence and is skipped, matching every runtime FS scorer
    (``score_pair_probabilistic`` etc., which all ``continue`` on ``vec[k] < 0``).

    ``indexed_fields`` is ``[(k, field_name), ...]`` for the regular comparison
    fields (NE / record fields excluded by the caller).
    """
    return sum(
        match_weights[name][vec[k]]
        for k, name in indexed_fields
        if vec[k] >= 0
    )


def continuous_scores(
    row_a: dict,
    row_b: dict,
    mk: MatchkeyConfig,
) -> list[float]:
    """Compute continuous field scores for a pair (Winkler extension).

    Returns raw scorer output per field (0.0-1.0), preserving the
    full continuous signal instead of discretizing into levels. Missing
    comparisons are represented by ``NaN`` and contribute no evidence.
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
        scores.append(s if s is not None else math.nan)
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


def _row_lookup_for_pairs(
    df: pl.DataFrame,
    cols: list[str],
    pairs_lists: Sequence[Sequence[tuple[int, int]]],
) -> dict[int, dict]:
    """Row dicts for ONLY the ids appearing in the given pair lists.

    The EM trainers used to materialize the ENTIRE dataframe as Python dicts
    before sampling, while the samplers only ever touch a few thousand rows —
    the dominant training-memory cost at 10M+ rows (epic #1803). Sampling
    depends only on ``__row_id__`` + blocks, so restricting the lookup to the
    sampled ids leaves the trained model byte-identical.
    """
    from goldenmatch.core.frame import to_frame as _tf_w6

    ids: set[int] = set()
    for pairs in pairs_lists:
        for a, b in pairs:
            ids.add(a)
            ids.add(b)
    lookup: dict[int, dict] = {}
    if not ids:
        return lookup
    frame = _tf_w6(df).filter_in("__row_id__", list(ids))
    for row in frame.select_dicts(["__row_id__"] + cols):
        lookup[row["__row_id__"]] = row
    return lookup


def _sample_pairs(
    df: pl.DataFrame,
    n_pairs: int = 10000,
    seed: int = 42,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int]]:
    """Sample random pairs for EM training.

    ``target_ids`` selects two-table linkage mode: the pair universe is the
    target x reference Cartesian product, never either table's self-pairs.
    """
    from goldenmatch.core.frame import to_frame as _tf_w6

    row_ids = _tf_w6(df).column("__row_id__").to_list()
    rng = random.Random(seed)

    if len(row_ids) < 2:
        return []

    if target_ids is not None:
        targets = sorted(row_id for row_id in row_ids if row_id in target_ids)
        references = sorted(row_id for row_id in row_ids if row_id not in target_ids)
        max_possible = len(targets) * len(references)
        if max_possible == 0:
            return []
        if max_possible <= n_pairs:
            return [
                (min(target, reference), max(target, reference))
                for target in targets
                for reference in references
            ]
        sampled_offsets = rng.sample(range(max_possible), n_pairs)
        n_references = len(references)
        return [
            (
                min(targets[offset // n_references], references[offset % n_references]),
                max(targets[offset // n_references], references[offset % n_references]),
            )
            for offset in sampled_offsets
        ]

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


def _record_concat_value(row: dict, columns, column_weights) -> str:
    """Concatenate a row's ``record_embedding`` columns into one string.

    Byte-identical to the concat in ``_record_embedding_score_matrix`` so the
    EM E-step and the scoring path embed the SAME text (and therefore agree on
    similarity + level)."""
    parts: list[str] = []
    for col in (columns or []):
        if column_weights is not None:
            w = column_weights.get(col, 1.0)
            if w <= 0:
                continue
            val = row.get(col)
            if val is not None:
                part = f"{col}: {val}"
                repeats = round(w) if w > 1.0 else 1
                parts.extend([part] * repeats)
        else:
            val = row.get(col)
            if val is not None:
                parts.append(f"{col}: {val}")
    return " | ".join(parts) if parts else ""


def _embedding_pair_sims(
    pairs: list[tuple[int, int]],
    row_lookup: dict[int, dict],
    emb_cols: dict,
    mk: MatchkeyConfig,
) -> dict[int, np.ndarray]:
    """Cosine similarity per pair for each model-backed field column.

    Embeds each referenced row ONCE per field (O(rows) model calls, not
    O(pairs)), then reads pair cosines by index. Returns ``{field_index:
    np.ndarray(len(pairs))}``. The embedder produces L2-normalized vectors, so
    cosine is a dot product — the same value the scoring path derives from
    ``_fuzzy_score_matrix`` / ``_record_embedding_score_matrix``.
    """
    from goldenmatch.core.embedder import get_embedder
    from goldenmatch.utils.transforms import apply_transforms

    rids = sorted({rid for pair in pairs for rid in pair})
    pos = {rid: i for i, rid in enumerate(rids)}
    out: dict[int, np.ndarray] = {}

    for idx, f in emb_cols.items():
        if f.scorer == "record_embedding":
            values = [
                _record_concat_value(row_lookup.get(rid, {}), f.columns, f.column_weights)
                for rid in rids
            ]
        else:
            values = []
            for rid in rids:
                raw = row_lookup.get(rid, {}).get(f.field)
                if raw is None:
                    values.append(None)
                    continue
                s = str(raw)
                if f.transforms:
                    s = apply_transforms(s, f.transforms)
                values.append(s)
        embedder = get_embedder(f.model or "all-MiniLM-L6-v2")
        vecs = np.asarray(
            embedder.embed_column(values, cache_key=f"_em_estep_{idx}_{id(pairs)}"),
            dtype=np.float64,
        )
        out[idx] = np.array(
            [float(vecs[pos[a]] @ vecs[pos[b]]) for a, b in pairs],
            dtype=np.float64,
        )
    return out


def _build_comparison_matrix(
    pairs: list[tuple[int, int]],
    row_lookup: dict[int, dict],
    mk: MatchkeyConfig,
) -> np.ndarray:
    """Build NxF comparison matrix where N=pairs, F=fields.

    Model-backed (embedding / record_embedding) fields can't run through the
    per-pair ``score_field``; their cosine similarity is precomputed in bulk and
    fed to ``comparison_vector`` via ``field_sims`` so the level logic stays
    single-sourced.
    """
    n_pairs = len(pairs)
    n_fields = len(mk.fields)
    matrix = np.zeros((n_pairs, n_fields), dtype=np.int8)

    emb_cols = {
        idx: f for idx, f in enumerate(mk.fields)
        if f.scorer in _MODEL_BACKED_SCORERS
    }
    sims_by_col = (
        _embedding_pair_sims(pairs, row_lookup, emb_cols, mk) if emb_cols else {}
    )

    for i, (a, b) in enumerate(pairs):
        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})
        field_sims = (
            {idx: sims_by_col[idx][i] for idx in emb_cols} if emb_cols else None
        )
        matrix[i] = comparison_vector(row_a, row_b, mk, field_sims=field_sims)

    return matrix


def _ne_fired(row_a: dict, row_b: dict, ne_field: NegativeEvidenceField) -> bool:
    """Return True iff a Fellegi-Sunter negative-evidence field FIRES.

    Fires when BOTH values are present (post-transform, non-empty) AND the
    scorer similarity is STRICTLY below ``ne_field.threshold`` — matching the
    weighted-NE firing rule (``core/scorer.py:292``,
    ``backends/score_buckets.py:942``). Any missing/empty value on either
    side (including nulls) means the
    dimension does NOT fire — negative evidence never boosts a pair, so an
    inconclusive comparison must not count against a match either.
    """
    from goldenmatch.utils.transforms import apply_transforms

    val_a = row_a.get(ne_field.field)
    val_b = row_b.get(ne_field.field)
    if val_a is None or val_b is None:
        return False
    val_a = str(val_a)
    val_b = str(val_b)
    if ne_field.transforms:
        val_a = apply_transforms(val_a, ne_field.transforms)
        val_b = apply_transforms(val_b, ne_field.transforms)
    if not val_a or not val_b:
        return False
    sim = score_field(val_a, val_b, ne_field.scorer)
    if sim is None:
        return False
    return sim < ne_field.threshold


def _em_ne_fields(mk: MatchkeyConfig) -> list:
    """NE fields that participate in EM (excludes ``penalty_bits`` overrides,
    which skip EM entirely and contribute a fixed weight at scoring time)."""
    return [ne for ne in (mk.negative_evidence or []) if ne.penalty_bits is None]


def _fs_ne_extend_cols(cols: list, mk: MatchkeyConfig) -> None:
    """Append each negative-evidence field name to ``cols`` in place when not
    already present, so NE-only fields (e.g. the canonical phone example, absent
    from ``mk.fields``) are projected into row_lookup for ``_ne_fired`` to read.
    The single NE-projection source shared by the EM trainers and the scalar
    scorer (#1804 item 4)."""
    for ne in (mk.negative_evidence or []):
        if ne.field not in cols:
            cols.append(ne.field)


def _fs_projection_cols(mk: MatchkeyConfig) -> list:
    """The scoring/label projection columns: every non-record matchkey field
    plus the negative-evidence fields. Used by ``estimate_m_from_labels`` and the
    scalar scorer; ``train_em`` additionally projects record_embedding columns,
    so it builds the base list itself and calls ``_fs_ne_extend_cols`` (#1804
    item 4)."""
    cols = [f.field for f in mk.fields if f.field != "__record__"]
    _fs_ne_extend_cols(cols, mk)
    return cols


def _build_ne_matrix(
    pairs: list[tuple[int, int]],
    row_lookup: dict[int, dict],
    mk: MatchkeyConfig,
) -> np.ndarray:
    """Build an n_pairs x n_ne int8 matrix of NE firing state for EM.

    0 = fired, 1 = not-fired (including nulls on either side). A SEPARATE
    matrix from :func:`_build_comparison_matrix` — NE dimensions are not
    regular FS fields, and comparison-matrix consumers assume
    ``len(row) == len(mk.fields)``. Only ``penalty_bits``-free NE fields are
    included (penalty_bits fields skip EM entirely); the column order
    matches :func:`_em_ne_fields`.
    """
    ne_fields = _em_ne_fields(mk)
    n_pairs = len(pairs)
    n_ne = len(ne_fields)
    matrix = np.ones((n_pairs, n_ne), dtype=np.int8)  # default: not-fired
    for i, (a, b) in enumerate(pairs):
        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})
        for j, ne in enumerate(ne_fields):
            if _ne_fired(row_a, row_b, ne):
                matrix[i, j] = 0
    return matrix


def _ne_u_probs_from_matrix(ne_matrix: np.ndarray, ne_fields: list) -> dict[str, list[float]]:
    """Observed [fired, not_fired] rates from a NE matrix — the u estimate.

    Same smoothing style as the regular-field u estimate in :func:`train_em`
    (additive 1e-6 per state).
    """
    out: dict[str, list[float]] = {}
    for j, ne in enumerate(ne_fields):
        fired = float((ne_matrix[:, j] == 0).sum())
        not_fired = float((ne_matrix[:, j] == 1).sum())
        total = fired + not_fired + 2 * 1e-6
        out[ne.field] = [(fired + 1e-6) / total, (not_fired + 1e-6) / total]
    return out


def _warn_ne_blocking_overlap(ne_fields: list, blocking_fields: list[str]) -> None:
    """Log a warning per NE field that also names a blocking field.

    Within-block pairs are guaranteed to agree on blocking fields, so an NE
    field that is also a blocking key never fires in the EM training sample
    — a degenerate m/u estimate. Not a hard error: multi-pass blocking may
    only partially overlap.
    """
    for ne in ne_fields:
        if ne.field in blocking_fields:
            logger.warning(
                "FS negative-evidence field '%s' is also a blocking field; "
                "within-block pairs are guaranteed to agree on it, so it will "
                "never fire during EM training (degenerate m/u estimate).",
                ne.field,
            )


def _sample_blocked_pairs_with_fields(
    blocks: list,
    n_pairs: int = 10000,
    seed: int = 42,
    target_ids: set[int] | None = None,
) -> tuple[list[tuple[int, int]], list[frozenset[str]]]:
    """Sample within-block pairs plus the fields conditioning each pair.

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
    order = sorted(
        range(len(blocks)),
        key=lambda i: (
            str(getattr(blocks[i], "block_key", "")),
            tuple(getattr(blocks[i], "blocking_fields", ()) or ()),
            i,
        ),
    )
    rng.shuffle(order)
    # Headroom over n_pairs so the post-dedup downsample still has enough to
    # draw from even when blocks overlap or are tiny.
    target = n_pairs * 3
    all_block_pairs: list[tuple[int, int]] = []
    all_block_fields: list[frozenset[str]] = []
    required_conditioning = {
        frozenset(getattr(block, "blocking_fields", ()) or ())
        for block in blocks
    }
    seen_conditioning: set[frozenset[str]] = set()

    for bi in order:
        block = blocks[bi]
        conditioned = frozenset(getattr(block, "blocking_fields", ()) or ())
        before_block = len(all_block_pairs)
        row_ids = sorted(
            block.materialize().column("__row_id__").to_list()
        )  # canonical order before the seeded sample
        if len(row_ids) < 2:
            continue
        # Limit per-block pairs for large blocks. In linkage mode preserve both
        # sides even on highly imbalanced blocks instead of sampling 100 ids
        # from the combined population and potentially missing the small side.
        sampled_ids: list[int] = []
        if target_ids is not None:
            target_rows = [row_id for row_id in row_ids if row_id in target_ids]
            reference_rows = [row_id for row_id in row_ids if row_id not in target_ids]
            if not target_rows or not reference_rows:
                continue
            if len(row_ids) > 100:
                n_target = min(len(target_rows), 50)
                n_reference = min(len(reference_rows), 50)
                remaining = 100 - n_target - n_reference
                add_target = min(remaining, len(target_rows) - n_target)
                n_target += add_target
                remaining -= add_target
                n_reference += min(remaining, len(reference_rows) - n_reference)
                target_rows = rng.sample(target_rows, n_target)
                reference_rows = rng.sample(reference_rows, n_reference)
            for target in target_rows:
                for reference in reference_rows:
                    all_block_pairs.append((min(target, reference), max(target, reference)))
                    all_block_fields.append(conditioned)
        elif len(row_ids) > 100:
            sampled_ids = rng.sample(row_ids, 100)
        else:
            sampled_ids = row_ids
        if target_ids is None:
            for i in range(len(sampled_ids)):
                for j in range(i + 1, len(sampled_ids)):
                    all_block_pairs.append((min(sampled_ids[i], sampled_ids[j]),
                                            max(sampled_ids[i], sampled_ids[j])))
                    all_block_fields.append(conditioned)
        if len(all_block_pairs) > before_block:
            seen_conditioning.add(conditioned)
        if (
            len(all_block_pairs) >= target
            and required_conditioning.issubset(seen_conditioning)
        ):
            break

    # Deduplicate and sample down if too many
    # A pair reached through multiple passes is conditioned only on fields
    # common to EVERY route that emitted it. A field used by just one route is
    # still discriminative under the union-of-passes candidate mechanism.
    pair_fields: dict[tuple[int, int], set[str]] = {}
    for pair, fields in zip(all_block_pairs, all_block_fields):
        if pair in pair_fields:
            pair_fields[pair].intersection_update(fields)
        else:
            pair_fields[pair] = set(fields)
    unique_pairs = set(pair_fields)
    all_block_pairs = (
        sorted(unique_pairs) if target_ids is not None else list(unique_pairs)
    )
    if len(all_block_pairs) > n_pairs:
        all_block_pairs = rng.sample(all_block_pairs, n_pairs)

    return all_block_pairs, [frozenset(pair_fields[p]) for p in all_block_pairs]


def _sample_blocked_pairs(
    blocks: list,
    n_pairs: int = 10000,
    seed: int = 42,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int]]:
    """Backward-compatible pair-only view of the provenance-aware sampler."""
    pairs, _ = _sample_blocked_pairs_with_fields(
        blocks, n_pairs, seed, target_ids=target_ids
    )
    return pairs


def _training_pair_conditioning(
    blocks: list | None,
    fallback_pairs: list[tuple[int, int]],
    blocking_fields: list[str],
    n_pairs: int,
    seed: int,
    target_ids: set[int] | None = None,
) -> tuple[list[tuple[int, int]], list[frozenset[str]]]:
    """Return EM pairs and the blocking fields conditioning each pair.

    Old/custom ``BlockResult`` producers do not carry pass provenance. For
    those callers, retain the legacy interpretation that every configured
    blocking field conditioned every training pair.
    """
    if blocks:
        pairs, conditioning = _sample_blocked_pairs_with_fields(
            blocks, n_pairs, seed, target_ids=target_ids
        )
        if not any(conditioning):
            conditioning = [frozenset(blocking_fields)] * len(pairs)
        return pairs, conditioning

    return fallback_pairs, [frozenset(blocking_fields)] * len(fallback_pairs)


def estimate_u_from_counts(
    mk: MatchkeyConfig,
    pattern_counts: list[tuple[tuple[int, ...], int]],
) -> dict[str, list[float]]:
    """``u`` from COUNTED comparison vectors over random pairs.

    ``u`` is the level distribution among NON-matches. Random pairs are
    overwhelmingly non-matches, so their observed level distribution estimates
    it directly -- no EM, which is why ``u`` is a separate computation from
    ``m`` here and in Splink (``estimate_u.py``).

    The estimator is ``train_em``'s, reached from counts instead of a matrix::

        u[level] = (count(level) + 1e-6) / (observed + n_levels * 1e-6)

    Both parts are sums over pairs, so collapsing identical vectors regroups the
    terms without changing the totals -- the same aggregation property that lets
    ``m`` be trained from counts.

    ``observed`` excludes unobserved (``-1``) entries. A field null on one side
    is missing data, not a disagreement, and putting those pairs in the
    denominator would shrink every level of a sparse field toward zero and
    inflate its ``log2(m/u)`` in proportion to how little data supports it.

    ``pattern_counts`` must come from RANDOM (unblocked) pairs. Counts from
    blocked candidates estimate the level distribution among pairs that already
    agree on a blocking key, which is not ``u``, and the resulting weights would
    be wrong with nothing about them looking wrong.
    """
    if not pattern_counts:
        raise ValueError("pattern_counts is empty; nothing to estimate u from")

    n_fields = len(mk.fields)
    for vec, count in pattern_counts:
        if len(vec) != n_fields:
            raise ValueError(
                f"comparison vector {vec} has {len(vec)} entries but the "
                f"matchkey has {n_fields} fields -- the vectors must be ordered "
                f"by mk.fields"
            )
        if count <= 0:
            raise ValueError(f"pattern {vec} has non-positive count {count}")

    u_probs: dict[str, list[float]] = {}
    for j, f in enumerate(mk.fields):
        counts = [0.0] * f.levels
        observed = 0.0
        for vec, c in pattern_counts:
            level = vec[j]
            if level < 0:
                continue
            observed += c
            if level < f.levels:
                counts[level] += c
        total = observed + f.levels * 1e-6
        u_probs[f.field] = [(c + 1e-6) / total for c in counts]
    return u_probs


def _fixed_blocking_weights(field) -> list[float]:
    """The bounded ``-3..+3`` ramp a conditioned field's weights take.

    The other half of the #1835 prior (see :func:`_neutral_u_for`, which supplies
    the ``u`` side). A conditioned field's ``m`` is never updated -- it keeps the
    exponential initialisation -- so ``log2(m/u)`` for one of these is the ratio
    of an arbitrary init to a random-pair estimate. It is not a quantity, and it
    lands in the model looking exactly like every other weight.

    The ramp supplies both properties the estimate would destroy: a real
    DISAGREEMENT PENALTY at the bottom (drives precision) and an agreement
    weight BOUNDED at +3 (preserves recall, by denying a near-unique key the
    20-plus bits that let it dominate every other field).

    One definition, because :func:`train_em`, :func:`train_em_from_counts`,
    :func:`_combine_em_sessions` and :func:`estimate_m_from_labels` all need it,
    and it is already implemented a fifth time in Rust
    (``score-core/src/em_core.rs``). Copies of a calibration rule diverge
    silently: every variant still produces a plausible weight vector.
    """
    n = field.levels
    if n <= 1:
        return [3.0]
    return [-3.0 + 6.0 * k / (n - 1) for k in range(n)]


def _neutral_u_for(field) -> list[float]:
    """The fixed ``u`` prior for a field EM must not learn from random pairs.

    A configured blocking field carries a deliberate prior (#1835): a
    disagreement penalty that drives precision, and a BOUNDED agreement weight
    that preserves recall. Estimating it from random pairs instead collapses a
    near-unique key's ``u`` toward the smoothing floor, which explodes
    ``log2(m/u)`` -- 28 bits on historical_50k -- and lets one field dominate
    the score (measured F1 0.83 -> 0.57).

    One definition, because both the pooled run and the per-pass combination
    apply it, and two copies that drifted would differ only in the numbers a
    blocking field's weights are built from.

    The 3-level case is ``[0.34, 0.33, 0.33]`` rather than exact thirds: it is
    the legacy prior, preserved so adopting this helper moves no model.
    """
    if field.levels == 2:
        return [0.50, 0.50]  # neutral
    if field.levels == 3:
        return [0.34, 0.33, 0.33]
    return [1.0 / field.levels] * field.levels


def _train_em_from_counts_native(
    mk: MatchkeyConfig,
    pattern_counts: list[tuple[tuple[int, ...], int]],
    u_probs: dict[str, list[float]],
    *,
    conditioned_fields: Sequence[str],
    tf_freqs: dict[str, dict[str, float]] | None,
    tf_collision: dict[str, float] | None,
    max_iterations: int,
    convergence: float,
) -> EMResult | None:
    """Run the counted trainer in the Rust kernel, or ``None`` to fall back.

    Phase 1 of the FS-EM single-source spec. The same loop was maintained in
    Python, TypeScript and Rust; this is what makes the Rust one load-bearing
    for Python rather than a fourth copy nothing runs.

    Returns ``None`` -- never a partial result -- when the kernel is absent, the
    component is gated off, or the symbol is missing from an older published
    wheel. Symbol presence is checked at the call site and not only through the
    component gate, because a wheel predating this symbol is the normal state of
    every environment until the wheel is republished (#688's secondary bug).

    Parity is DECISION-LEVEL, not bitwise: libm's ``ln``/``log2``/``exp`` differ
    from CPython's in the low mantissa bits, so which path ran moves the trained
    numbers in the last ~1e-9. That is the same posture every other float kernel
    here takes; it is stated because "the model differs by environment" is
    otherwise a surprising thing to discover.
    """
    from goldenmatch.core._native_loader import native_enabled, native_module

    if not native_enabled("fs_em", "train_em_from_counts_native"):
        return None
    mod = native_module()
    fn = getattr(mod, "train_em_from_counts_native", None)
    if fn is None:
        return None

    conditioned = set(conditioned_fields)
    n_levels = [f.levels for f in mk.fields]
    try:
        u_in = [list(u_probs[f.field]) for f in mk.fields]
    except KeyError as exc:
        raise ValueError(
            f"u_probs is missing field {exc.args[0]!r}; the counted trainer "
            f"needs one u vector per matchkey field"
        ) from None

    m_tab, u_tab, w_tab, converged, iterations, p_match = fn(
        n_levels,
        [list(vec) for vec, _ in pattern_counts],
        [float(c) for _, c in pattern_counts],
        u_in,
        [f.field in conditioned for f in mk.fields],
        int(max_iterations),
        float(convergence),
    )

    names = [f.field for f in mk.fields]
    logger.info(
        "EM from counts (native): %d patterns, converged=%s in %d iterations",
        len(pattern_counts), converged, iterations,
    )
    return EMResult(
        m_probs={n: list(v) for n, v in zip(names, m_tab)},
        u_probs={n: list(v) for n, v in zip(names, u_tab)},
        match_weights={n: list(v) for n, v in zip(names, w_tab)},
        converged=bool(converged),
        iterations=int(iterations),
        proportion_matched=float(p_match),
        # Carried through the kernel path too. The kernel computes only the
        # numeric half; forgetting these here would drop the TF tables from
        # every model trained on a box that has the wheel installed, and the
        # model would look complete.
        tf_freqs=tf_freqs,
        tf_collision=tf_collision,
    )


def _combine_em_sessions(
    mk: MatchkeyConfig,
    sessions: list[tuple[tuple[str, ...], EMResult, float]],
) -> EMResult:
    """Combine independent per-pass EM sessions into one model.

    ``sessions`` is ``(conditioned_fields, result, weight)``. Shared by
    :func:`train_em_per_pass` and the distributed caller, which differ only in
    HOW a session is trained -- from a sampled pair list or from counted
    comparison vectors computed by a cluster. The combination itself is the same
    question either way, and a second copy would drift silently: every variant
    of it still returns valid probability vectors.

    **m** is the pair-weighted mean over the sessions that could estimate the
    field (those whose pass does not block on it), weighted because a pass
    contributing 10 pairs and one contributing 10,000 are not equal evidence.
    A field no session can estimate keeps the fixed prior every session gave it.

    **u** is neutralised for the UNION of every pass's blocking fields, matching
    ``train_em``'s ``always_conditioned |= set(blocking_fields)``. Taking one
    session's ``u`` wholesale is wrong even though the sessions were all trained
    against the same random-pair sample: each one neutralises only its OWN
    blocking fields, so lifting the vector from session 0 leaves every other
    pass's blocking field carrying the random-pair estimate and re-opens #1835
    for it.
    """
    base = sessions[0][1]
    blocking_union = {name for fields, _, _ in sessions for name in fields}

    m_probs: dict[str, list[float]] = {}
    for f in mk.fields:
        name = f.field
        estimable = [
            (em, w) for fields, em, w in sessions
            if name not in fields and name in em.m_probs
        ]
        if not estimable:
            # No pass leaves this field free. Every session fell back to the
            # fixed prior for it, so they agree and the first is as good as any
            # -- and this is exactly the pooled run's `always_conditioned` case.
            m_probs[name] = list(base.m_probs[name])
            continue
        total = sum(w for _, w in estimable)
        n_levels = len(estimable[0][0].m_probs[name])
        m_probs[name] = [
            sum(em.m_probs[name][k] * w for em, w in estimable) / total
            for k in range(n_levels)
        ]

    u_probs = {k: list(v) for k, v in base.u_probs.items()}
    for f in mk.fields:
        if f.field in blocking_union:
            u_probs[f.field] = _neutral_u_for(f)

    total_w = sum(w for _, _, w in sessions)
    proportion = sum(em.proportion_matched * w for _, em, w in sessions) / total_w

    # Recomputed from the COMBINED m/u rather than averaged from the sessions:
    # log2 of a mean is not the mean of the log2s, and averaging the weights
    # would give a model whose weights do not match its own probabilities.
    match_weights = {}
    for f in mk.fields:
        name = f.field
        if name not in m_probs:
            continue
        if name in blocking_union:
            match_weights[name] = _fixed_blocking_weights(f)
        else:
            match_weights[name] = [
                math.log2(max(m, 1e-10) / max(u, 1e-10))
                for m, u in zip(m_probs[name], u_probs.get(name, m_probs[name]))
            ]
    return EMResult(
        m_probs=m_probs,
        u_probs=u_probs,
        match_weights=match_weights,
        converged=all(em.converged for _, em, _ in sessions),
        iterations=max(em.iterations for _, em, _ in sessions),
        proportion_matched=proportion,
        tf_freqs=base.tf_freqs,
        tf_collision=base.tf_collision,
    )


def train_em_from_counts(
    mk: MatchkeyConfig,
    pattern_counts: list[tuple[tuple[int, ...], int]],
    u_probs: dict[str, list[float]],
    *,
    conditioned_fields: Sequence[str] = (),
    tf_freqs: dict[str, dict[str, float]] | None = None,
    tf_collision: dict[str, float] | None = None,
    max_iterations: int = 20,
    convergence: float = 0.001,
) -> EMResult:
    """Train FS from COUNTED comparison vectors instead of a pair sample.

    The last link of ``compute gammas distributed -> GROUP BY them ->
    train_em(pair_weights=counts)``. ``pattern_counts`` is what
    :func:`goldenmatch.spark.em.agreement_pattern_counts` returns: one row per
    distinct comparison vector, with how many pairs had it.

    Calls the SAME :func:`_em_iterate` as :func:`train_em`, so this is not a
    second EM -- it is the same loop reached without a sampler. The vectors are
    ordered by ``mk.fields``, matching ``_build_comparison_matrix``.

    Args:
        u_probs: estimated from RANDOM (unblocked) pairs, which this function
            does not see. Required rather than defaulted: u is half the
            likelihood ratio, and inventing it here would produce a model whose
            weights are wrong in a direction nobody could spot from the m
            estimates. Splink keeps the same separation (``estimate_u.py``).
        conditioned_fields: fields the blocking pass makes uninformative -- the
            pass conditioning, constant within a session, which is exactly why
            the counts needed no pass column.

        tf_freqs / tf_collision: per-value frequency tables for fields with
            ``tf_adjustment``. **Supplied, never derived** -- collapsing
            identical comparison vectors is exactly what discards the values a
            TF table is built from. On the one box ``_build_tf_tables`` computes
            them from the source frame; on a cluster
            ``spark.em.tf_value_frequencies`` computes the same thing as a
            distributed ``GROUP BY``. Either way they only have to reach the
            EMResult, because **TF is a SCORING-time adjustment here**:
            ``_em_iterate`` contains no reference to tf, so the table never
            enters the E-step.

            Splink is the other way round by default -- its
            ``estimate_without_term_frequencies`` is ``False``, so TF joins its
            E-step via the per-pair path, and its agreement-pattern-counts path
            is the ``True`` branch. Counted training and TF-in-training are
            mutually exclusive there too; the difference is that we never put TF
            in training at all, which is what makes the table separable here.

    Negative evidence remains unsupported and refused rather than ignored: it
    needs a per-pair NE matrix this function is not given, and silently dropping
    it would train a different model from the one the config asks for.
    """
    if not pattern_counts:
        raise ValueError("pattern_counts is empty; nothing to train on")
    if _em_ne_fields(mk):
        raise NotImplementedError(
            f"matchkey {mk.name!r} has negative-evidence fields, which need a "
            f"per-pair NE matrix that counted comparison vectors do not carry. "
            f"Train it with train_em() on sampled pairs."
        )
    tf_fields = {f.field for f in mk.fields if getattr(f, "tf_adjustment", False)}
    if tf_fields and not tf_freqs:
        raise NotImplementedError(
            f"matchkey {mk.name!r} uses term-frequency adjustment on "
            f"{sorted(tf_fields)}, and counted comparison vectors do not carry "
            f"the per-value frequencies to build the table from -- collapsing "
            f"identical vectors is what discards those values. Pass tf_freqs "
            f"(spark.em.tf_value_frequencies computes it distributed), or train "
            f"with train_em() on sampled pairs."
        )
    # A table for a field that did not opt in means the caller and the config
    # disagree about which fields are adjusted. The surplus entry would sit in
    # the persisted model doing nothing until some later scorer honoured it.
    surplus = set(tf_freqs or {}) - tf_fields
    if surplus:
        raise ValueError(
            f"tf_freqs carries {sorted(surplus)}, which did not opt in to "
            f"tf_adjustment on matchkey {mk.name!r}"
        )

    n_fields = len(mk.fields)
    for vec, count in pattern_counts:
        if len(vec) != n_fields:
            raise ValueError(
                f"comparison vector {vec} has {len(vec)} entries but the "
                f"matchkey has {n_fields} fields -- the vectors must be ordered "
                f"by mk.fields"
            )
        if count <= 0:
            raise ValueError(f"pattern {vec} has non-positive count {count}")

    native = _train_em_from_counts_native(
        mk, pattern_counts, u_probs,
        conditioned_fields=conditioned_fields,
        tf_freqs=tf_freqs, tf_collision=tf_collision,
        max_iterations=max_iterations, convergence=convergence,
    )
    if native is not None:
        return native

    comp_matrix = np.asarray([v for v, _ in pattern_counts], dtype=np.int64)
    w = np.asarray([float(c) for _, c in pattern_counts], dtype=np.float64)
    total_weight = float(w.sum())

    conditioned = set(conditioned_fields)
    conditioned_mask = np.asarray(
        [[f.field in conditioned for f in mk.fields]] * len(pattern_counts),
        dtype=bool,
    )
    # Constant across rows within a session, so a field conditioned by this
    # pass is conditioned for every row -- the `always_conditioned` case, and
    # the reason those fields keep the fixed prior rather than being learned
    # from evidence the blocking removed.
    always_conditioned = {f.field for f in mk.fields if f.field in conditioned}

    # The #1835 prior, same as `train_em` applies. This does NOT move the
    # trained m -- a conditioned field is skipped in the E-step either way --
    # but `u_probs` is half the persisted model, and a caller reading it off a
    # model trained here would otherwise see a near-unique random-pair estimate
    # where `train_em` reports the fixed prior. Found by the Rust parity gate.
    u_probs = dict(u_probs)
    for f in mk.fields:
        if f.field in always_conditioned:
            u_probs[f.field] = _neutral_u_for(f)

    empty_ne = np.zeros((len(pattern_counts), 0), dtype=np.int64)
    m_probs, _m_ne, p_match, converged, iterations = _em_iterate(
        mk, comp_matrix, empty_ne, conditioned_mask,
        np.zeros((len(pattern_counts), 0), dtype=bool),
        w, total_weight, [], u_probs, {}, always_conditioned, set(),
        None, None, max_iterations, convergence,
    )

    match_weights = {}
    for f in mk.fields:
        if f.field in always_conditioned:
            match_weights[f.field] = _fixed_blocking_weights(f)
        elif f.field in m_probs and f.field in u_probs:
            match_weights[f.field] = [
                math.log2(max(m, 1e-10) / max(u, 1e-10))
                for m, u in zip(m_probs[f.field], u_probs[f.field])
            ]
    logger.info(
        "EM from counts: %d patterns, %.0f pairs, converged=%s in %d iterations",
        len(pattern_counts), total_weight, converged, iterations,
    )
    return EMResult(
        m_probs=m_probs,
        u_probs={k: list(v) for k, v in u_probs.items()},
        match_weights=match_weights,
        converged=converged,
        iterations=iterations,
        proportion_matched=p_match,
        tf_freqs=tf_freqs,
        tf_collision=tf_collision,
    )


def train_em_per_pass(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    blocks: list,
    *,
    blocking_fields: list[str] | None = None,
    **kw: object,
) -> EMResult:
    """One EM session per BLOCKING PASS, combined -- Splink's decomposition.

    ``train_em`` pools every pass into a single run and masks per (pair, field):
    a pair emitted by a pass that blocks on ``zip`` carries no ``zip``
    likelihood, but ``zip`` is still learnable from pairs emitted by other
    passes. That is correct and it is one pass over the data.

    Splink decomposes the same problem differently: it runs a separate EM
    session per ``blocking_rule_for_training``, records which comparisons
    ``cannot_be_estimated`` under that rule (the ones the rule blocks on), and
    combines the sessions. Two properties follow, and they are the reason to
    have this alongside the pooled run:

    1. **Each session is independent**, so they can be trained anywhere --
       including one per executor, or one per SQL engine round trip. The pooled
       run couples every pass into one convergence loop.
    2. **The aggregation key loses the pass.** Within a session the conditioning
       is constant, so counting comparison vectors needs no pass column: it is
       ``GROUP BY <gammas>``, exactly the shape ``count_agreement_patterns_sql``
       produces. That is what makes the counts computable by a cluster and the
       iteration cheap, -- see ``pair_weights`` on :func:`train_em`.

    Combination is :func:`_combine_em_sessions`, shared with the distributed
    caller so the two cannot drift on what combining means.

    Args:
        blocks: ``BlockResult``s carrying ``blocking_fields`` provenance. Blocks
            are grouped by that tuple; each distinct value is one pass.
        blocking_fields: legacy union, forwarded to :func:`train_em` for block
            producers that carry no provenance.
        **kw: forwarded verbatim to :func:`train_em`.
    """
    by_pass: dict[tuple[str, ...], list] = {}
    for b in blocks or []:
        by_pass.setdefault(tuple(getattr(b, "blocking_fields", ()) or ()), []).append(b)

    # One pass (or none, or no provenance at all) -- there is nothing to
    # decompose, and delegating keeps a single-pass config bit-identical to the
    # pooled run rather than routing it through a combination step that would
    # have to be proven a no-op.
    if len(by_pass) <= 1:
        return train_em(df, mk, blocks=blocks, blocking_fields=blocking_fields, **kw)

    sessions: list[tuple[tuple[str, ...], EMResult, float]] = []
    for fields, pass_blocks in sorted(by_pass.items()):
        # `n_rows()` rather than a pair count: the sampler decides how many
        # pairs it draws, and weighting by block size is the honest proxy for
        # "how much evidence this pass brought" without training twice to
        # find out.
        weight = float(sum(max(int(b.n_rows()), 0) for b in pass_blocks))
        if weight <= 0:
            continue
        em = train_em(
            df, mk, blocks=pass_blocks, blocking_fields=list(fields), **kw
        )
        sessions.append((fields, em, weight))
        logger.info(
            "EM session: pass=%s blocks=%d rows=%.0f converged=%s",
            fields or "(none)", len(pass_blocks), weight, em.converged,
        )

    if not sessions:
        return train_em(df, mk, blocks=blocks, blocking_fields=blocking_fields, **kw)

    return _combine_em_sessions(mk, sessions)


def _em_iterate(
    mk: MatchkeyConfig,
    comp_matrix,
    ne_matrix,
    conditioned_mask,
    ne_conditioned_mask,
    w,
    total_weight: float,
    ne_fields_em: list,
    u_probs: dict[str, list[float]],
    u_probs_ne: dict[str, list[float]],
    always_conditioned: set,
    always_conditioned_ne: set,
    label_clamp_idx,
    label_clamp_val,
    max_iterations: int,
    convergence: float,
):
    """The EM loop itself: seed m, iterate to convergence, return the estimates.

    Extracted so there is ONE implementation of the iteration and two ways to
    reach it -- from sampled pairs (:func:`train_em`) and from precomputed
    comparison rows carrying counts (:func:`train_em_from_counts`). A second
    copy would drift, and the drift would be invisible: both copies would go on
    producing valid probability vectors.

    It reads nothing it is not handed. Every input is a matrix over rows, a mask
    over rows, or a fixed parameter -- which is exactly why the rows can be
    counted somewhere else (a cluster) and passed in as ``w``.

    Returns ``(m_probs, m_probs_ne, p_match, converged, iterations)``.
    """
    n_pairs = comp_matrix.shape[0]
    # Initialize m with strong priors (matches mostly agree at highest level)
    p_match = 0.02  # conservative prior
    m_probs = {}
    for j, f in enumerate(mk.fields):
        n_levels = f.levels
        # Exponential prior: highest level gets most mass
        raw = [2 ** k for k in range(n_levels)]
        total = sum(raw)
        m_probs[f.field] = [r / total for r in raw]

    # NE dims: fired is rare in matches (a true match usually agrees on the
    # NE field), so seed m with a low fired-probability prior.
    m_probs_ne: dict[str, list[float]] = {ne.field: [0.05, 0.95] for ne in ne_fields_em}

    # Pair weights. `None` -> all ones, which is the pair-wise caller and is
    # bit-identical to the unweighted arithmetic it replaces (x*1.0 == x).
    # An aggregated caller passes one row per DISTINCT (comparison vector,
    # conditioning, NE vector) with its count, which is exact rather than a
    # sample: the E-step reads only those three things, so identical rows have
    # identical posteriors and every M-step sum is linear in the count.
    #
    # Bounded, too: the number of distinct rows is at most the product over
    # fields of (levels + 1), times the number of blocking passes -- thousands,
    # not millions -- which is what lets the comparison vectors be counted
    # anywhere (a cluster, say) and the iteration run on the result.
    #
    # WEIGHTS ARE COUNTS, and the scale is load-bearing. The M-step smoothing
    #
    #     new_m[level] = (sum + 1e-6) / (eligible_match + n_levels * 1e-6)
    #
    # is additive and unscaled, so its influence shrinks as the weighted totals
    # grow -- correct for a prior, and it means doubling every weight is NOT a
    # no-op. Aggregation is exact only because collapsing identical rows
    # regroups the terms of each sum WITHOUT changing the total: the represented
    # population is the same, so the smoothing constant weighs the same. Pass
    # normalised or rescaled weights and the low-probability cells shift, which
    # is where log2(m/u) is largest and the shift is least visible.
    # Pinned by tests/test_fs_em_weighted_pairs.py.
    # ── Step 3: EM iterations — only update m, fix u ──
    converged = False
    for iteration in range(max_iterations):
        old_m = {k: list(v) for k, v in m_probs.items()}
        old_m_ne = {k: list(v) for k, v in m_probs_ne.items()}

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
            observed = levels_j >= 0
            m_table = np.log(np.maximum(np.asarray(m_probs[f.field], dtype=np.float64), 1e-10))
            u_table = np.log(np.maximum(np.asarray(u_probs[f.field], dtype=np.float64), 1e-10))
            # Compose #1819 (unobserved: level -1 carries no evidence) with
            # #1835 (pass-conditioned pairs carry no evidence for this field).
            eligible = observed & ~conditioned_mask[:, j]
            log_m[eligible] += m_table[levels_j[eligible]]
            log_u[eligible] += u_table[levels_j[eligible]]

        # NE dims: same E-step accumulation, 2-entry [fired, not_fired]
        # lookup tables indexed by the NE matrix's {0, 1} columns.
        for j, ne in enumerate(ne_fields_em):
            levels_j = ne_matrix[:, j]
            m_table = np.log(np.maximum(np.asarray(m_probs_ne[ne.field], dtype=np.float64), 1e-10))
            u_table = np.log(np.maximum(np.asarray(u_probs_ne[ne.field], dtype=np.float64), 1e-10))
            eligible = ~ne_conditioned_mask[:, j]
            log_m[eligible] += m_table[levels_j[eligible]]
            log_u[eligible] += u_table[levels_j[eligible]]

        log_match = math.log(max(p_match, 1e-10)) + log_m
        log_nonmatch = math.log(max(1 - p_match, 1e-10)) + log_u

        max_log = np.maximum(log_match, log_nonmatch)
        e_match = np.exp(log_match - max_log)
        e_nonmatch = np.exp(log_nonmatch - max_log)
        posteriors = e_match / (e_match + e_nonmatch)

        # Semi-supervised clamp: pin labeled anchors' responsibility to the
        # label so the M-step re-estimates m (and p_match) with those pairs as
        # ground truth. u stays fixed from the random-pair estimate, exactly as
        # in unsupervised EM. No-op when no anchors were supplied.
        if label_clamp_idx is not None:
            posteriors[label_clamp_idx] = label_clamp_val

        # M-step: update ONLY m_probs and p_match (u is fixed)
        #
        # Every quantity below is a SUM OVER PAIRS, which is the whole reason
        # `pair_weights` can exist: two pairs with the same comparison vector,
        # the same pass conditioning and the same NE vector contribute
        # identically to every one of them, so they can be collapsed into one
        # row carrying a count. `w` is all-ones for the pair-wise caller, and
        # the aggregated caller passes the counts. Same arithmetic either way.
        wp = posteriors * w
        total_match = wp.sum()
        p_match = max(total_match / total_weight, 1e-6)

        for j, f in enumerate(mk.fields):
            if f.field in always_conditioned:
                continue  # skip blocked fields
            n_levels = f.levels
            # Compose #1819 + #1835: a pair contributes to this field's m
            # only when the comparison was OBSERVED (level >= 0) and the pair
            # is not conditioned out of this field for its pass.
            observed = comp_matrix[:, j] >= 0
            eligible = observed & ~conditioned_mask[:, j]
            eligible_match = wp[eligible].sum()
            new_m = [0.0] * n_levels
            for level in range(n_levels):
                mask = eligible & (comp_matrix[:, j] == level)
                new_m[level] = (wp[mask].sum() + 1e-6) / (
                    eligible_match + n_levels * 1e-6
                )
            m_probs[f.field] = new_m

        # NE dimensions use the same pair-level conditioning. A field that
        # blocks one pass can still learn its veto from the other passes.
        for j, ne in enumerate(ne_fields_em):
            if ne.field in always_conditioned_ne:
                continue
            new_m_ne = [0.0, 0.0]
            eligible = ~ne_conditioned_mask[:, j]
            eligible_match = wp[eligible].sum()
            for level in range(2):
                mask = eligible & (ne_matrix[:, j] == level)
                new_m_ne[level] = (wp[mask].sum() + 1e-6) / (
                    eligible_match + 2 * 1e-6
                )
            m_probs_ne[ne.field] = new_m_ne

        # Check convergence (only m changes)
        max_delta = 0.0
        for f in mk.fields:
            if f.field in always_conditioned:
                continue
            for k in range(f.levels):
                max_delta = max(max_delta, abs(m_probs[f.field][k] - old_m[f.field][k]))
        for ne in ne_fields_em:
            if ne.field in always_conditioned_ne:
                continue
            for k in range(2):
                max_delta = max(max_delta, abs(m_probs_ne[ne.field][k] - old_m_ne[ne.field][k]))

        if max_delta < convergence:
            converged = True
            logger.info("EM converged after %d iterations (delta=%.6f)", iteration + 1, max_delta)
            break

    if not converged:
        logger.warning("EM did not converge after %d iterations (delta=%.6f)", max_iterations, max_delta)

    return m_probs, m_probs_ne, p_match, converged, iteration + 1



def _fs_em_counted_budget() -> int | None:
    """Pair budget for the counted one-box trainer, or ``None`` when off.

    ``GOLDENMATCH_FS_EM_COUNTED`` unset/``0`` -> off (the sampler runs).
    ``1`` -> on at the default budget. Any integer -> on at that budget.
    """
    raw = os.environ.get("GOLDENMATCH_FS_EM_COUNTED", "").strip().lower()
    if raw in ("", "0", "false", "no"):
        return None
    if raw in ("1", "true", "yes"):
        return 2_000_000
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "GOLDENMATCH_FS_EM_COUNTED=%r is not an integer; counted EM stays off",
            raw,
        )
        return None
    return n if n > 0 else None


def train_em_counted(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    blocks: list,
    *,
    n_pairs: int = 2_000_000,
    seed: int = 42,
    max_iterations: int = 20,
    convergence: float = 0.001,
) -> EMResult:
    """Train on COLLAPSED comparison vectors over a large blocked-pair budget.

    The one-box analogue of ``spark.em.train_em_distributed``, and the thing
    that makes the arc's claim measurable without a cluster.

    ``train_em`` samples ``n_sample_pairs`` (10,000 by default) blocked pairs
    and iterates over that matrix, so its EM cost is proportional to the SAMPLE
    and its estimates carry that sample's noise. Here the budget is ~200x
    larger and the matrix is collapsed to distinct vectors first, so the
    iteration cost stops tracking the pair count and starts tracking
    ``prod(levels + 1)``. Collapsing is EXACT, not an approximation -- every
    M-step quantity is a sum linear in how many pairs share a vector.

    **Per pass, because conditioning is per pass.** Two pairs with the same
    comparison vector are only interchangeable if the same fields were
    conditioned out for both, and that is constant WITHIN a blocking pass and
    not across passes. Collapsing the pooled matrix would merge rows whose
    conditioning differs and quietly train on a population that never existed.
    So this groups blocks by their pass, collapses inside each, and combines
    with the same ``_combine_em_sessions`` the distributed caller uses.
    """
    from collections import Counter

    cols = _fs_projection_cols(mk)
    _fs_ne_extend_cols(cols, mk)

    if _em_ne_fields(mk):
        raise NotImplementedError(
            "counted EM does not support negative-evidence fields; they need a "
            "per-pair NE matrix collapsing discards. Unset "
            "GOLDENMATCH_FS_EM_COUNTED for this config."
        )

    by_pass: dict[tuple[str, ...], list] = {}
    for b in blocks or []:
        by_pass.setdefault(tuple(getattr(b, "blocking_fields", ()) or ()), []).append(b)
    if not by_pass:
        raise ValueError("counted EM needs blocks; none were supplied")

    # u from RANDOM pairs, exactly as train_em estimates it -- the counted path
    # changes how m is estimated, not what u means.
    random_pairs = _sample_pairs(df, min(10_000, 5000 * len(mk.fields)), seed)
    if len(random_pairs) < 10:
        return _fallback_result(mk)
    lookup = _row_lookup_for_pairs(df, cols, [random_pairs])
    u_probs = estimate_u_from_counts(
        mk,
        sorted(
            Counter(
                tuple(int(v) for v in row)
                for row in _build_comparison_matrix(random_pairs, lookup, mk)
            ).items()
        ),
    )

    tf_freqs, tf_collision = _build_tf_tables(df, mk)
    sessions: list[tuple[tuple[str, ...], EMResult, float]] = []
    per_pass_budget = max(1000, n_pairs // max(len(by_pass), 1))

    for fields, pass_blocks in sorted(by_pass.items()):
        pairs, _cond = _sample_blocked_pairs_with_fields(
            pass_blocks, per_pass_budget, seed
        )
        if len(pairs) < 10:
            continue
        pair_lookup = _row_lookup_for_pairs(df, cols, [pairs])
        matrix = _build_comparison_matrix(pairs, pair_lookup, mk)
        counts = sorted(
            Counter(tuple(int(v) for v in row) for row in matrix).items()
        )
        em = train_em_from_counts(
            mk, counts, u_probs, conditioned_fields=fields,
            tf_freqs=tf_freqs, tf_collision=tf_collision,
            max_iterations=max_iterations, convergence=convergence,
        )
        sessions.append((fields, em, float(len(pairs))))
        logger.info(
            "counted EM: pass=%s pairs=%d -> %d distinct vectors (%.3g%%)",
            fields or "(none)", len(pairs), len(counts),
            100.0 * len(counts) / max(len(pairs), 1),
        )

    if not sessions:
        return _fallback_result(mk)
    return _combine_em_sessions(mk, sessions)

def train_em(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    n_sample_pairs: int = 10000,
    max_iterations: int = 20,
    convergence: float = 0.001,
    seed: int = 42,
    blocks: list | None = None,
    blocking_fields: list[str] | None = None,
    target_ids: set[int] | None = None,
    label_pairs: dict[tuple[int, int], int] | None = None,
    pair_weights: Sequence[float] | None = None,
) -> EMResult:
    """Train Fellegi-Sunter model using Expectation-Maximization.

    When blocks are provided, samples within-block pairs for training.
    This produces much better m/u estimates because blocked pairs have
    a higher true match rate than random pairs from the full dataset.

    Blocking is conditioned at the sampled-pair/pass level. A field contributes
    no likelihood for pairs emitted by a pass that blocks on it, but remains
    learnable from pairs emitted by other passes. Fields that condition every
    sampled pair retain the legacy fixed-prior treatment.

    Args:
        df: DataFrame with __row_id__ and field columns.
        mk: Probabilistic matchkey config.
        n_sample_pairs: Number of pairs to sample for training.
        max_iterations: Maximum EM iterations.
        convergence: Stop when max change in any probability < this.
        seed: Random seed for pair sampling.
        blocks: Optional list of BlockResult for within-block sampling.
        blocking_fields: Union of configured blocking fields. Used as a legacy
            fallback when blocks do not carry per-pass provenance.
        target_ids: Target-side row ids for two-table linkage training. When
            provided, random and blocked samples contain only target-reference
            pairs.
        label_pairs: Optional semi-supervised anchors — a mapping of canonical
            ``(min_row_id, max_row_id)`` pairs to a 0/1 label. Labeled pairs are
            injected into the EM training sample and their E-step responsibility
            is clamped to the label every iteration (match -> 1.0,
            non-match -> 0.0), so the trained m stays representative of the full
            blocked population (the anchors are a small fraction) while the
            labels steer the decision boundary. u is unchanged — it is estimated
            from random pairs and held fixed during EM, exactly as in the
            unsupervised path. Labels must be 0/1 (or bool); self-pairs
            ``(a, a)`` are ignored. This is label-constrained (semi-supervised)
            EM, NOT a per-pair score override: the labels improve the trained
            model, which is then re-scored across the whole population. Default
            None is byte-identical to unsupervised EM.

    Returns:
        EMResult with trained m/u probabilities and match weights.
    """
    if blocking_fields is None:
        blocking_fields = []


    ne_fields_em = _em_ne_fields(mk)
    _warn_ne_blocking_overlap(ne_fields_em, blocking_fields)

    # Counted mode (GOLDENMATCH_FS_EM_COUNTED) trades the 10k sampler for a
    # ~200x larger blocked-pair budget collapsed to distinct vectors. Opt-in and
    # env-gated so it can be A/B'd as a bench LANE against the default sampler
    # without touching any call site -- the same lever the FS_NATIVE lanes use.
    # Only when blocks exist and no per-pair overrides are in play: label
    # anchors and caller-supplied weights are per-PAIR, and collapsing is what
    # discards the identity of the pair they attach to.
    _counted_budget = _fs_em_counted_budget()
    if (
        _counted_budget
        and blocks
        and label_pairs is None
        and pair_weights is None
        and not _em_ne_fields(mk)
    ):
        return train_em_counted(
            df, mk, blocks, n_pairs=_counted_budget, seed=seed,
            max_iterations=max_iterations, convergence=convergence,
        )

    cols = [f.field for f in mk.fields
            if f.field != "__record__" and f.scorer != "record_embedding"]
    # record_embedding fields carry their real columns in ``columns`` (their
    # ``field`` is a placeholder like "__record__"); project those so the
    # E-step can rebuild the concatenated record from row_lookup.
    for f in mk.fields:
        if f.scorer == "record_embedding":
            for c in (f.columns or []):
                if c not in cols:
                    cols.append(c)
    # Extend with NE field names (incl. derive_from-synthesized columns,
    # which are materialized under the NE field's own name by
    # precompute_matchkey_transforms before train_em ever sees the frame) —
    # else NE-only fields (the canonical phone example) are absent from
    # row_lookup, read as missing/null, and NE never fires during EM
    # (degenerate m/u estimate).
    _fs_ne_extend_cols(cols, mk)
    # ── Step 1: Estimate u from RANDOM pairs (Splink approach) ──
    # Random pairs are overwhelmingly non-matches, so the observed
    # level distribution approximates u directly. No EM needed for u.
    random_pairs = _sample_pairs(
        df,
        min(n_sample_pairs, 5000),
        seed,
        target_ids=target_ids,
    )
    if len(random_pairs) < 10:
        logger.warning("Too few pairs (%d) for EM training", len(random_pairs))
        return _fallback_result(mk)

    # Sample the m-estimation pairs up front too, so the row-dict lookup below
    # can be restricted to the sampled ids (#1803 item 4 — the full-df dict was
    # the dominant training-memory cost at 10M+ rows). Sampling reads only
    # ``__row_id__`` / the blocks, never the lookup, so the sampled pairs — and
    # the trained model — are unchanged by this reordering.
    blocked_pairs, pair_conditioning = _training_pair_conditioning(
        blocks,
        random_pairs,
        blocking_fields,
        n_sample_pairs,
        seed,
        target_ids=target_ids,
    )
    # Semi-supervised anchors: append caller-provided labeled pairs to the EM
    # training sample so their comparison vectors are present in comp_matrix and
    # their E-step responsibility can be clamped to the label each iteration.
    # Anchors carry no pass-conditioning (the label is authority, so every
    # observed field contributes to the m re-estimate). Default None -> no-op.
    label_anchors: dict[tuple[int, int], float] = {}
    if label_pairs:
        blocked_pairs = list(blocked_pairs)
        pair_conditioning = list(pair_conditioning)
        existing = {(min(a, b), max(a, b)) for a, b in blocked_pairs}
        for (a, b), lab in label_pairs.items():
            ai, bi = int(a), int(b)
            if ai == bi:
                continue  # a self-pair carries no comparison signal
            # Caller-facing input: reject anything that isn't a clean 0/1 label
            # rather than silently coercing (e.g. 2 -> match) an unintended anchor.
            if lab is True or lab == 1:
                val = 1.0
            elif lab is False or lab == 0:
                val = 0.0
            else:
                raise ValueError(
                    f"label_pairs label for ({ai}, {bi}) must be 0/1 or bool, got {lab!r}"
                )
            key = (min(ai, bi), max(ai, bi))
            # A caller passing both (a, b) and (b, a) with DIFFERENT labels is an
            # input error -- surface it rather than silently letting the later
            # dict entry win and produce a surprising anchored model.
            if key in label_anchors and label_anchors[key] != val:
                raise ValueError(
                    f"label_pairs has conflicting labels for pair {key}: "
                    f"{int(label_anchors[key])} vs {int(val)}"
                )
            label_anchors[key] = val
            if key not in existing:
                blocked_pairs.append(key)
                pair_conditioning.append(frozenset())
                existing.add(key)
    row_lookup = _row_lookup_for_pairs(df, cols, [random_pairs, blocked_pairs])

    random_matrix = _build_comparison_matrix(random_pairs, row_lookup, mk)
    u_probs = {}
    for j, f in enumerate(mk.fields):
        n_levels = f.levels
        counts = [0.0] * n_levels
        for level in range(n_levels):
            counts[level] = float((random_matrix[:, j] == level).sum())
        observed = float((random_matrix[:, j] >= 0).sum())
        total = observed + n_levels * 1e-6
        u_probs[f.field] = [(c + 1e-6) / total for c in counts]

    conditioned_mask = np.asarray(
        [
            [field.field in conditioned for field in mk.fields]
            for conditioned in pair_conditioning
        ],
        dtype=bool,
    )
    always_conditioned = {
        field.field
        for j, field in enumerate(mk.fields)
        if len(conditioned_mask) and bool(conditioned_mask[:, j].all())
    }
    # A configured blocking field carries a deliberate fixed prior that EM
    # cannot recover from random pairs: a disagreement PENALTY (drives
    # precision) and a BOUNDED agreement weight (preserves recall). Learning it
    # per-pass (#1835) loses both -- a near-unique key's `u` collapses to the
    # smoothing floor, exploding the agreement weight (e.g. 28 bits on
    # historical_50k) and dominating the score, which collapsed recall
    # (F1 0.83 -> 0.57). So blocking fields always take the fixed-weight prior.
    # The per-pass conditioning of the E/M sample still applies to non-blocking
    # fields; only the final weight/skip treatment is forced here.
    always_conditioned |= set(blocking_fields or [])
    ne_conditioned_mask = np.zeros(
        (len(pair_conditioning), len(ne_fields_em)), dtype=bool
    )
    for i, conditioned in enumerate(pair_conditioning):
        for j, ne in enumerate(ne_fields_em):
            ne_conditioned_mask[i, j] = ne.field in conditioned
    always_conditioned_ne = {
        ne.field
        for j, ne in enumerate(ne_fields_em)
        if len(ne_conditioned_mask) and bool(ne_conditioned_mask[:, j].all())
    }

    # A field that conditions every training pair has no unbiased sample from
    # which EM can learn it, so retain the legacy neutral-u/fixed-weight prior.
    for f in mk.fields:
        if f.field in always_conditioned:
            u_probs[f.field] = _neutral_u_for(f)

    logger.info("u-probabilities estimated from %d random pairs", len(random_pairs))

    # NE dims: u from the SAME random-pair sample as regular fields (an NE
    # dimension is never a blocking key by construction, so no neutral-u
    # override is needed here — see _warn_ne_blocking_overlap above for the
    # degenerate case where it accidentally is one).
    random_ne_matrix = _build_ne_matrix(random_pairs, row_lookup, mk)
    u_probs_ne = _ne_u_probs_from_matrix(random_ne_matrix, ne_fields_em)

    # ── Step 2: Blocked pairs for m estimation (sampled in Step 1's prelude) ──
    pairs = blocked_pairs
    if blocks:
        logger.info("EM training m on %d within-block pairs", len(pairs))
    else:
        logger.info("No blocks provided; using random pairs for m estimation")

    if len(pairs) < 10:
        return _fallback_result(mk)

    comp_matrix = _build_comparison_matrix(pairs, row_lookup, mk)
    ne_matrix = _build_ne_matrix(pairs, row_lookup, mk)
    n_pairs = len(pairs)
    _n_fields = len(mk.fields)

    # Resolve semi-supervised anchors to comp_matrix row indices (the E-step
    # clamps posteriors[label_clamp_idx] = label_clamp_val each iteration).
    label_clamp_idx = None
    label_clamp_val = None
    if label_anchors:
        pair_to_i = {(min(a, b), max(a, b)): i for i, (a, b) in enumerate(pairs)}
        idxs: list[int] = []
        vals: list[float] = []
        for key, val in label_anchors.items():
            i = pair_to_i.get(key)
            if i is not None:
                idxs.append(i)
                vals.append(val)
        if idxs:
            label_clamp_idx = np.asarray(idxs, dtype=np.intp)
            label_clamp_val = np.asarray(vals, dtype=np.float64)
            logger.info("EM using %d semi-supervised label anchors", len(idxs))

    if pair_weights is None:
        w = np.ones(n_pairs, dtype=np.float64)
    else:
        w = np.asarray(pair_weights, dtype=np.float64)
        if w.shape != (n_pairs,):
            raise ValueError(
                f"pair_weights has shape {w.shape}, expected ({n_pairs},) -- "
                f"one weight per comparison row"
            )
        if not np.all(w > 0):
            raise ValueError("pair_weights must all be positive")
    total_weight = float(w.sum())

    (
        m_probs, m_probs_ne, p_match, converged, _em_iterations
    ) = _em_iterate(
        mk, comp_matrix, ne_matrix, conditioned_mask, ne_conditioned_mask,
        w, total_weight, ne_fields_em, u_probs, u_probs_ne,
        always_conditioned, always_conditioned_ne,
        label_clamp_idx, label_clamp_val, max_iterations, convergence,
    )

    # Compute match weights: log2(m/u)
    # For blocking fields, use fixed priors since EM can't learn from
    # fields that are always "agree" within blocks
    # Post-blocking bias correction (GOLDENMATCH_FS_POST_BLOCKING_U, default off):
    # deconvolve u from the blocked population so candidate-common fields don't get
    # a random-pair-inflated weight. Off -> empty -> the random-pair u is used.
    u_post = (
        _deconvolve_post_blocking_u(comp_matrix, mk, m_probs, p_match, always_conditioned)
        if _fs_post_blocking_u_enabled() else {}
    )
    if u_post:
        logger.info("FS post-blocking u correction on %d field(s)", len(u_post))
    match_weights = {}
    for f in mk.fields:
        if f.field in always_conditioned:
            match_weights[f.field] = _fixed_blocking_weights(f)
            logger.debug("Using fixed weights for blocking field '%s'", f.field)
            continue

        u_src = u_post.get(f.field) or u_probs[f.field]
        weights = []
        for k in range(f.levels):
            m_val = max(m_probs[f.field][k], 1e-10)
            u_val = max(u_src[k], 1e-10)
            weights.append(math.log2(m_val / u_val))
        match_weights[f.field] = weights

    # NE dims: store under __ne__<field> — [w_fired, 0.0]. The 0.0 for
    # not-fired is the NEGATIVE-EVIDENCE CLAMP (not log2(m1/u1)): agreement
    # or an inconclusive comparison never boosts the score, only a
    # confident disagreement subtracts from it.
    for ne in ne_fields_em:
        key = f"__ne__{ne.field}"
        m0 = max(m_probs_ne[ne.field][0], 1e-10)
        u0 = max(u_probs_ne[ne.field][0], 1e-10)
        m_probs[key] = list(m_probs_ne[ne.field])
        u_probs[key] = list(u_probs_ne[ne.field])
        match_weights[key] = [math.log2(m0 / u0), 0.0]

    # Guard: FS match weights are expected non-decreasing in agreement level.
    # EM can invert a rare-but-discriminative middle level above exact
    # agreement. Default 'warn' surfaces it (Splink posture) without changing
    # the weights; 'enforce' isotonically repairs them (measured to trade F1
    # on some data — opt in deliberately). __ne__ entries are [fired,
    # not_fired]-ordered, not level-ordered, so they must never be repaired —
    # skip_fields excludes them (mirrors blocking_fields' exclusion).
    _mono_mode = _fs_monotonic_mode()
    if _mono_mode != "off":
        _ne_skip = {k for k in match_weights if k.startswith("__ne__")}
        repaired, adjusted = enforce_weight_monotonicity(
            match_weights, skip_fields=list(always_conditioned) + list(_ne_skip),
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

    # Unsupervised link-threshold calibration from the training-pair score
    # distribution (GOLDENMATCH_FS_CALIBRATE_THRESHOLD, default off -> None).
    calibrated_link_threshold = None
    if _fs_calibrate_threshold_enabled():
        calibrated_link_threshold = _calibrate_link_threshold(
            comp_matrix, mk, match_weights, p_match,
        )
        if calibrated_link_threshold is not None:
            logger.info(
                "FS calibrated link threshold: %.4f (vs fixed 0.50 default)",
                calibrated_link_threshold,
            )

    return EMResult(
        m_probs=m_probs,
        u_probs=u_probs,
        match_weights=match_weights,
        converged=converged,
        iterations=_em_iterations,
        proportion_matched=p_match,
        tf_freqs=tf_freqs,
        tf_collision=tf_collision,
        calibrated_link_threshold=calibrated_link_threshold,
        training_config=_training_config_manifest(mk),
    )


def fs_model_preloaded(mk: MatchkeyConfig) -> bool:
    """True when :func:`load_or_train_em` will load ``mk.model_path`` from
    disk and skip EM training entirely (same check it performs internally).

    Callers use this to avoid building the within-block training pairs that
    only EM consumes: at scale ``build_blocks``'s partition materializes
    millions of tiny eager frames (the dataset duplicated per blocking pass),
    which SIGKILLed a 14M-row FS dedupe before scoring started even though
    the loaded-model path never reads them (issue #1798).
    """
    path = getattr(mk, "model_path", None)
    return bool(path and os.path.exists(path))


def load_or_train_em(
    df: pl.DataFrame,
    mk: MatchkeyConfig,
    *,
    blocks: list | None = None,
    blocking_fields: list[str] | None = None,
    max_iterations: int | None = None,
    convergence: float | None = None,
    target_ids: set[int] | None = None,
    label_pairs: dict[tuple[int, int], int] | None = None,
) -> EMResult:
    """Return a trained EMResult, reusing ``mk.model_path`` when present.

    Splink-style train-once -> reuse: when ``mk.model_path`` is set and the
    file exists, the persisted model is loaded, validated against ``mk``
    (field/level shape and comparison semantics), and EM is skipped. When the
    path is set but absent, EM runs
    and the trained model is saved there for next time. With no ``model_path``
    this is exactly ``train_em``. The single seam all three pipeline call sites
    (core pipeline x2, TUI engine) share.

    Semi-supervised anchors (``label_pairs`` kwarg or the ``set_fs_label_anchors``
    ContextVar) are resolved BEFORE the persisted-model fast path: when anchors
    are present they force a fresh anchored train (the cached un-anchored model
    is bypassed, never returned), and the anchored result is NOT written back to
    ``model_path`` -- it is a per-call override, so the shared canonical model is
    left intact. Only an un-anchored train persists to ``model_path``.
    """
    # Semi-supervised anchors: explicit kwarg wins; else the training-time
    # ContextVar seam (a two-pass label-then-rerun driver). None -> unsupervised.
    # Resolved BEFORE the persisted-model fast path so anchors are never silently
    # ignored when the shared model file already exists.
    if label_pairs is None:
        label_pairs = _label_anchors_for(mk)

    path = getattr(mk, "model_path", None)
    if path and os.path.exists(path) and not label_pairs:
        em = EMResult.load_json(path)
        em.validate_for(mk)  # raises FSModelMismatchError on shape mismatch
        logger.info("Loaded FS model from %s (skipped EM training)", path)
        return em
    if path and os.path.exists(path) and label_pairs:
        # The persisted model is un-anchored; the caller explicitly wants
        # anchors, so retrain WITH them instead of returning the cached model.
        logger.info(
            "FS model at %s bypassed: %d semi-supervised label anchors force a "
            "retrain", path, len(label_pairs),
        )

    em = train_em(
        df, mk,
        max_iterations=max_iterations if max_iterations is not None else mk.em_iterations,
        convergence=convergence if convergence is not None else mk.convergence_threshold,
        blocks=blocks,
        blocking_fields=blocking_fields,
        target_ids=target_ids,
        label_pairs=label_pairs,
    )
    # Persist ONLY the canonical un-anchored model. An anchored model is a
    # per-call override; overwriting the shared model_path file with it would
    # surprise the next reuse.
    if path and not label_pairs:
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

    from goldenmatch.core.frame import to_frame as _tf_w6

    ne_fields_em = _em_ne_fields(mk)
    _warn_ne_blocking_overlap(ne_fields_em, blocking_fields)

    cols = _fs_projection_cols(mk)
    # Keep only labels whose ids are present and distinct; canonicalize + dedup.
    # Membership rides the (cheap, ints-only) id column — the row dicts are
    # materialized below for ONLY the sampled/labeled ids (#1803 item 4).
    valid_ids = set(_tf_w6(df).column("__row_id__").to_list())
    valid = {
        (min(a, b), max(a, b))
        for a, b in labels
        if a != b and a in valid_ids and b in valid_ids
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
    # Row dicts for ONLY the labeled + sampled ids (#1803 item 4).
    row_lookup = _row_lookup_for_pairs(df, cols, [label_pairs, random_pairs])
    u_probs: dict[str, list[float]] = {}
    u_probs_ne: dict[str, list[float]] = {}
    if len(random_pairs) >= 10:
        random_matrix = _build_comparison_matrix(random_pairs, row_lookup, mk)
        for j, f in enumerate(mk.fields):
            counts = [float((random_matrix[:, j] == lvl).sum()) for lvl in range(f.levels)]
            total = sum(counts) + f.levels * 1e-6
            u_probs[f.field] = [(c + 1e-6) / total for c in counts]
        random_ne_matrix = _build_ne_matrix(random_pairs, row_lookup, mk)
        u_probs_ne = _ne_u_probs_from_matrix(random_ne_matrix, ne_fields_em)
    else:
        fallback = _fallback_result(mk)
        u_probs = fallback.u_probs
        u_probs_ne = {
            ne.field: fallback.u_probs[f"__ne__{ne.field}"] for ne in ne_fields_em
        }
    # Blocking fields: neutral u (random pairs give a biased u for them).
    for f in mk.fields:
        if f.field in blocking_fields:
            if f.levels == 2:
                u_probs[f.field] = [0.50, 0.50]
            elif f.levels == 3:
                u_probs[f.field] = [0.34, 0.33, 0.33]
            else:
                u_probs[f.field] = [1.0 / f.levels] * f.levels

    # ── m from LABELED matches: observed level frequency (Laplace smoothed) ──
    label_matrix = _build_comparison_matrix(label_pairs, row_lookup, mk)
    m_probs: dict[str, list[float]] = {}
    for j, f in enumerate(mk.fields):
        counts = [float((label_matrix[:, j] == lvl).sum()) for lvl in range(f.levels)]
        total = sum(counts) + f.levels * smoothing
        m_probs[f.field] = [(c + smoothing) / total for c in counts]

    # NE dims: m from the observed fired-rate among LABELED matches (the
    # supervised twin of train_em's EM-estimated m); u from the same
    # random-pair sample as regular fields, computed above.
    label_ne_matrix = _build_ne_matrix(label_pairs, row_lookup, mk)
    for j, ne in enumerate(ne_fields_em):
        fired = float((label_ne_matrix[:, j] == 0).sum())
        not_fired = float((label_ne_matrix[:, j] == 1).sum())
        total = fired + not_fired + 2 * smoothing
        m_probs[f"__ne__{ne.field}"] = [(fired + smoothing) / total, (not_fired + smoothing) / total]
        u_probs[f"__ne__{ne.field}"] = list(u_probs_ne.get(ne.field, [0.5, 0.5]))

    # ── match weights (blocking fixed) — mirrors train_em ──
    match_weights: dict[str, list[float]] = {}
    for f in mk.fields:
        if f.field in blocking_fields:
            match_weights[f.field] = _fixed_blocking_weights(f)
            continue
        match_weights[f.field] = [
            math.log2(max(m_probs[f.field][k], 1e-10) / max(u_probs[f.field][k], 1e-10))
            for k in range(f.levels)
        ]

    # NE dims: [w_fired, 0.0] — the not-fired weight is the negative-evidence
    # clamp, not log2(m1/u1). Mirrors train_em's storage exactly.
    for ne in ne_fields_em:
        key = f"__ne__{ne.field}"
        m0 = max(m_probs[key][0], 1e-10)
        u0 = max(u_probs[key][0], 1e-10)
        match_weights[key] = [math.log2(m0 / u0), 0.0]

    _mono_mode = _fs_monotonic_mode()
    if _mono_mode != "off":
        _ne_skip = {k for k in match_weights if k.startswith("__ne__")}
        repaired, adjusted = enforce_weight_monotonicity(
            match_weights, skip_fields=list(blocking_fields) + list(_ne_skip),
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
        training_config=_training_config_manifest(mk),
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
    from goldenmatch.core.tf_tables import value_frequencies

    tf_fields = [f for f in mk.fields if getattr(f, "tf_adjustment", False)]
    if not tf_fields:
        return None, None

    tf_freqs: dict[str, dict[str, float]] = {}
    tf_collision: dict[str, float] = {}
    for f in tf_fields:
        if f.field not in df.columns:
            continue
        freqs = value_frequencies(df, f.field, f.transforms)
        if not freqs:
            continue
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
    if mk.negative_evidence:
        raise ValueError(
            "negative_evidence is not supported on the continuous/Winkler FS "
            "path (train_em_continuous / score_probabilistic_continuous). Use "
            "the discrete probabilistic path (train_em / load_or_train_em) "
            "for a matchkey with negative_evidence."
        )
    if blocking_fields is None:
        blocking_fields = []


    cols = [f.field for f in mk.fields if f.field != "__record__"]
    fallback_pairs = [] if blocks else _sample_pairs(df, n_sample_pairs, seed)
    pairs, pair_conditioning = _training_pair_conditioning(
        blocks, fallback_pairs, blocking_fields, n_sample_pairs, seed
    )
    if blocks:
        logger.info("Continuous EM training on %d within-block pairs", len(pairs))
    # Row dicts for ONLY the sampled ids (#1803 item 4).
    row_lookup = _row_lookup_for_pairs(df, cols, [pairs])

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
    conditioned_mask = np.asarray(
        [
            [field.field in conditioned for field in mk.fields]
            for conditioned in pair_conditioning
        ],
        dtype=bool,
    )
    always_conditioned = {
        field.field
        for j, field in enumerate(mk.fields)
        if bool(conditioned_mask[:, j].all())
    }
    # Blocking fields take the fixed prior, same as the discrete path -- learning
    # them per-pass regresses recall on near-unique keys (see train_em).
    always_conditioned |= set(blocking_fields or [])

    # Initialize with strong priors — matches score high, non-matches score low.
    # Use the actual score distribution to set non-match priors at the median.
    p_match = 0.02  # conservative: expect few matches

    # Compute actual score statistics for better initialization
    field_medians = {}
    for j, f in enumerate(mk.fields):
        if f.field not in always_conditioned:
            # #1835 pass-conditioning composed with #1819 NaN-as-unobserved.
            col = score_matrix[~conditioned_mask[:, j], j]
            observed = col[~np.isnan(col)]
            if observed.size:
                field_medians[f.field] = float(np.median(observed))

    m_mean = {f.field: 0.90 for f in mk.fields}  # matches should score very high
    m_var = {f.field: 0.01 for f in mk.fields}    # tight distribution
    u_mean = {f.field: field_medians.get(f.field, 0.30) for f in mk.fields}  # non-matches at median
    u_var = {f.field: 0.05 for f in mk.fields}    # broader distribution

    # Override blocking fields
    for f in mk.fields:
        if f.field in always_conditioned:
            m_mean[f.field] = 0.99
            m_var[f.field] = 0.001
            u_mean[f.field] = 0.99  # always agree in blocks
            u_var[f.field] = 0.001

    converged = False
    # Active (non-blocking) field column indices, fixed across iterations.
    active_j = [j for j, f in enumerate(mk.fields) if f.field not in always_conditioned]
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
            observed = ~np.isnan(s)
            if not observed.any():
                continue
            var_m = max(m_var[f.field], 1e-6)
            var_u = max(u_var[f.field], 1e-6)
            eligible = observed & ~conditioned_mask[:, j]
            log_m[eligible] += (
                -0.5 * ((s[eligible] - m_mean[f.field]) ** 2) / var_m
                - 0.5 * math.log(var_m)
            )
            log_u[eligible] += (
                -0.5 * ((s[eligible] - u_mean[f.field]) ** 2) / var_u
                - 0.5 * math.log(var_u)
            )

        max_log = np.maximum(log_m, log_u)
        e_m = np.exp(log_m - max_log)
        e_u = np.exp(log_u - max_log)
        posteriors = e_m / (e_m + e_u)

        # M-step
        total_match = posteriors.sum()
        p_match = max(total_match / n_pairs, 1e-6)

        for j, f in enumerate(mk.fields):
            if f.field in always_conditioned:
                continue
            # Compose #1819 (NaN score = unobserved) with #1835 (pass-
            # conditioned pairs carry no evidence for this field).
            eligible = ~conditioned_mask[:, j] & ~np.isnan(score_matrix[:, j])
            if not eligible.any():
                continue
            scores = score_matrix[eligible, j]
            field_posteriors = posteriors[eligible]
            field_match = field_posteriors.sum()
            # Weighted mean and variance for matches
            if field_match > 1e-6:
                m_mean[f.field] = float(np.average(scores, weights=field_posteriors))
                m_var[f.field] = float(
                    np.average(
                        (scores - m_mean[f.field]) ** 2,
                        weights=field_posteriors,
                    )
                ) + 1e-6
            # Weighted mean and variance for non-matches
            w_nonmatch = 1 - field_posteriors
            field_nonmatch = w_nonmatch.sum()
            if field_nonmatch > 1e-6:
                u_mean[f.field] = float(np.average(scores, weights=w_nonmatch))
                u_var[f.field] = float(np.average((scores - u_mean[f.field]) ** 2, weights=w_nonmatch)) + 1e-6

        # Convergence check
        max_delta = 0.0
        for f in mk.fields:
            if f.field in always_conditioned:
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
    score distributions, adds the learned match-prior log-odds, and returns
    pairs above the posterior-probability threshold.
    """
    if mk.negative_evidence:
        raise ValueError(
            "negative_evidence is not supported on the continuous/Winkler FS "
            "path (train_em_continuous / score_probabilistic_continuous). Use "
            "the discrete probabilistic path (train_em / load_or_train_em) "
            "for a matchkey with negative_evidence."
        )
    if exclude_pairs is None:
        exclude_pairs = set()

    from goldenmatch.core.frame import to_frame as _tf_w6

    row_ids = _tf_w6(block_df).column("__row_id__").to_list()
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
        # A missing operand is unobserved evidence, not similarity 0.0.
        null_mask = np.array([v is None for v in vals], dtype=bool)
        either_null = null_mask[:, None] | null_mask[None, :]
        var_m = max(em.m_var[f.field], 1e-6)
        var_u = max(em.u_var[f.field], 1e-6)
        log_m = -0.5 * ((sim - em.m_mean[f.field]) ** 2) / var_m - 0.5 * math.log(var_m)
        log_u = -0.5 * ((sim - em.u_mean[f.field]) ** 2) / var_u - 0.5 * math.log(var_u)
        log_ratio += np.where(either_null, 0.0, log_m - log_u)

    # Convert the likelihood ratio to a posterior by adding the learned prior
    # log-odds before the sigmoid. Clamp the prior away from {0, 1} so malformed
    # or degenerate training results remain numerically finite.
    p_match = min(max(em.proportion_matched, 1e-10), 1.0 - 1e-10)
    log_ratio += math.log(p_match / (1.0 - p_match))
    with np.errstate(over="ignore"):
        normalized = 1.0 / (1.0 + np.exp(-np.clip(log_ratio, -700.0, 700.0)))

    return _emit_triu_pairs(normalized, row_ids, threshold, exclude_pairs)


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
        elif f.levels == 3:
            m_probs[f.field] = [0.05, 0.15, 0.80]
            u_probs[f.field] = [0.80, 0.15, 0.05]
            match_weights[f.field] = [
                math.log2(0.05 / 0.80),
                math.log2(0.15 / 0.15),
                math.log2(0.80 / 0.05),
            ]
        else:
            raw = [2.0 ** k for k in range(f.levels)]
            total = sum(raw)
            m = [r / total for r in raw]
            u = list(reversed(m))
            m_probs[f.field] = m
            u_probs[f.field] = u
            match_weights[f.field] = [math.log2(m_i / u_i) for m_i, u_i in zip(m, u)]

    ne_fields_em = _em_ne_fields(mk)
    if ne_fields_em:
        logger.warning(
            "FS EM training fell back to conservative defaults (too few pairs); "
            "negative-evidence field(s) %s get a fixed -3.0 bit weight instead of "
            "an EM-learned one.",
            ", ".join(ne.field for ne in ne_fields_em),
        )
    for ne in ne_fields_em:
        key = f"__ne__{ne.field}"
        # m=0.0625, u=0.5 -> log2(0.0625/0.5) == -3.0 exactly (a conservative
        # fixed weight consistent with a symmetric u=[0.5, 0.5] non-match
        # prior, mirroring the pipeline-wide fallback posture).
        m_probs[key] = [0.0625, 0.9375]
        u_probs[key] = [0.5, 0.5]
        match_weights[key] = [-3.0, 0.0]

    return EMResult(
        m_probs=m_probs, u_probs=u_probs, match_weights=match_weights,
        converged=False, iterations=0, proportion_matched=0.05,
        training_config=_training_config_manifest(mk),
    )


def fs_weight_range(em: EMResult, mk: MatchkeyConfig) -> tuple[float, float]:
    """Achievable Fellegi-Sunter total-weight range ``(min_weight, max_weight)``.

    Centralizes the min/max weight-sum computation used for linear-score
    normalization and threshold calibration -- previously hand-rolled at every
    scoring/prep site, which meant a missed site silently produced
    out-of-[0,1] normalized scores as soon as an NE field fired.

    Regular fields: ``sum(min(weights))`` / ``sum(max(weights))`` over
    ``em.match_weights[f.field]`` for each ``f in mk.fields`` -- unchanged
    from the pre-NE behavior.

    NE fields (``mk.negative_evidence``):
      - ``penalty_bits`` set: contributes ``(-abs(penalty_bits), 0.0)`` to
        (min, max) directly -- no EM entry needed (the fixed-override case).
      - else: uses the EM-learned ``em.match_weights["__ne__<field>"]``
        entry, which is stored as ``[w_fired, 0.0]``. ``min``/``max`` over
        that 2-entry list reproduces ``(min(w_fired, 0), max(w_fired, 0))``
        for free regardless of ``w_fired``'s sign (it's normally negative).
      - An NE field with NEITHER (shouldn't happen once ``validate_for``
        enforces model/matchkey shape parity) is defensively skipped --
        contributes ``(0.0, 0.0)`` rather than raising.
    """
    min_weight, max_weight = _fs_ne_weight_range(em, mk)
    for f in mk.fields:
        weights = em.match_weights[f.field]
        min_weight += min(weights)
        max_weight += max(weights)
    return min_weight, max_weight


def _fs_ne_weight_range(em: EMResult, mk: MatchkeyConfig) -> tuple[float, float]:
    """Weight envelope contributed independently of regular-field presence."""
    min_weight = 0.0
    max_weight = 0.0
    for ne in (mk.negative_evidence or []):
        if ne.penalty_bits is not None:
            min_weight += -abs(ne.penalty_bits)
            continue
        entry = em.match_weights.get(f"__ne__{ne.field}")
        if entry is None:
            continue
        min_weight += min(entry)
        max_weight += max(entry)
    return min_weight, max_weight


def _ne_scalar_contribution(
    row_a: dict, row_b: dict, ne: NegativeEvidenceField, em_result: EMResult,
) -> float:
    """One NE field's scalar weight contribution for a single pair.

    0.0 unless the field FIRES (:func:`_ne_fired`); when fired, ``-abs(bits)``
    for the ``penalty_bits`` fixed override, else the EM-learned
    ``__ne__<field>`` fired-weight (``match_weights[...][0]``).
    """
    if not _ne_fired(row_a, row_b, ne):
        return 0.0
    if ne.penalty_bits is not None:
        return -abs(ne.penalty_bits)
    return em_result.match_weights[f"__ne__{ne.field}"][0]


def _add_ne_matrix_contribution(
    total_weight: np.ndarray,
    vals: list[str | None],
    ne: NegativeEvidenceField,
    em_result: EMResult,
) -> None:
    """Add one NE field's fired-weight contribution to an NxN/SxS weight matrix.

    ``vals`` is the NE field's per-row values, already transformed the same
    way :func:`_ne_fired` transforms them (callers pass the output of
    :func:`_field_values_for_block` / the batched equivalent, which apply
    ``ne.transforms`` -- ``NegativeEvidenceField`` has the same ``.field`` /
    ``.transforms`` shape as ``MatchkeyField``). Fired iff BOTH sides are
    present + non-empty AND similarity is STRICTLY below ``ne.threshold`` --
    the same rule as :func:`_ne_fired`, vectorized.
    """
    sim = _field_score_matrix_dedup(vals, ne.scorer)
    inconclusive = np.array([v is None or v == "" for v in vals], dtype=bool)
    fired = sim < ne.threshold
    if inconclusive.any():
        either = inconclusive[:, None] | inconclusive[None, :]
        fired = fired & ~either
    if ne.penalty_bits is not None:
        w_fired = -abs(ne.penalty_bits)
    else:
        w_fired = em_result.match_weights[f"__ne__{ne.field}"][0]
    total_weight += np.where(fired, w_fired, 0.0)


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
        # Prior-aware evidence cut: threshold the posterior at σ(c + prior_w),
        # which is exactly W >= c — a prior- AND endpoint-invariant evidence bar.
        # Falls through to the legacy fixed 0.99 cut when unset.
        c = _fs_evidence_cut()
        if c is not None:
            prior_w = prior_weight(em_result.proportion_matched)
            link = posterior_from_weight(float(c), prior_w)
            review = posterior_from_weight(
                float(c) - _FS_EVIDENCE_REVIEW_GAP_BITS, prior_w
            )
            return link, min(review, link)
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

        # `match_pct * 2` is a WINDOW over the top of the distribution, and the
        # percentile below is only meaningful while that window is a minority of
        # it. At proportion_matched >= 0.5 the window covers everything:
        # `1 - match_pct*2` goes <= 0, link_idx clamps to 0, and the
        # "data-driven" cut becomes `sorted_w[0]` -- the MINIMUM observed score.
        # The cut is then not chosen from the data, it is the floor of the data,
        # and every scored pair is admitted.
        #
        # MEASURED, person shape: EM reported proportion_matched 0.638477, which
        # is percentile 0.0. The bench measured the shipped lane's minimum
        # retained score at exactly 0.60 -- the two corroborate. Against
        # comparison lanes cutting at 0.85 that admitted 276,836 pairs vs
        # 184,285, and connected components chained the extra links until
        # non-singleton clusters averaged 7.98 members against a truth of 2.40.
        # Pairwise precision 0.2627.
        #
        # A high match rate on blocked pairs is not absurd -- good blocking
        # concentrates duplicates -- so this does NOT try to correct EM. It only
        # refuses to read a percentile that carries no information, and falls
        # back to the same fixed default the no-weights path uses.
        if match_pct * 2 >= 1.0:
            return 0.50, 0.35
        # Admit about as many pairs as EM says are MATCHES -- not a multiple.
        #
        # This was `1 - match_pct * 2`, "2x match rate for headroom". That
        # deliberately admitted twice the estimated match rate, so by the
        # model's own estimate half of what it admitted was a non-match: a
        # precision ceiling of ~0.5 BY CONSTRUCTION, before connected components
        # chained anything.
        #
        # Measured, person @ 1M (run 32063484894, `gm_probabilistic_shipped`,
        # which sets no link_threshold and so lands here):
        #
        #     pairwise  P 0.263  R 1.000  F1 0.416   TP 239,829 FP 673,277 FN 89
        #     splink, same fixture, cutting at 0.85:  F1 0.995, EIGHT false pos
        #
        # Recall 1.000 at precision 0.263 is the signature of a cut chosen for
        # headroom: it finds everything and pays in false positives.
        #
        # The model is the only thing here that knows how many matches to
        # expect, so its own estimate is the defensible bound. The change is
        # self-limiting: at low match rates both rules sit above the 0.95 clamp
        # ceiling and nothing moves, while at high match rates 2x drove the
        # percentile BELOW the 0.40 floor (match_pct 0.30 -> percentile 0.40,
        # 0.45 -> 0.10), which is how a "data-driven" cut ended up admitting 60%
        # of pairs regardless of the data.
        link_idx = int(n * (1 - match_pct))
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


def resolve_thresholds(
    mk: MatchkeyConfig, em_result: EMResult
) -> tuple[float, float]:
    """Resolve configured or calibrated ``(link, review)`` score cutoffs.

    The review cutoff is clamped to the link cutoff so an explicit low link
    threshold cannot accidentally turn linked pairs into review candidates.
    """
    computed_link, computed_review = compute_thresholds(em_result)
    # Precedence: explicit user config > EM-calibrated cutoff > fixed default.
    calibrated = getattr(em_result, "calibrated_link_threshold", None)
    if mk.link_threshold is not None:
        link = float(mk.link_threshold)
    elif calibrated is not None:
        link = float(calibrated)
    else:
        link = computed_link
    review = (
        float(mk.review_threshold)
        if mk.review_threshold is not None
        else computed_review
    )
    return link, min(review, link)


#: How the FS link cutoff was arrived at, for reporting (#2483).
LINK_THRESHOLD_CONFIGURED = "configured"
LINK_THRESHOLD_CALIBRATED = "calibrated"
LINK_THRESHOLD_FALLBACK = "fallback"


def link_threshold_source(mk: MatchkeyConfig, em_result: EMResult) -> str:
    """Which of the three precedence steps supplied the link cutoff (#2483).

    Mirrors -- and must keep mirroring -- the precedence in `resolve_thresholds`
    and `_fs_link_threshold`: explicit `mk.link_threshold`, then the
    EM-calibrated per-dataset cutoff, then the fixed default. Derived here
    rather than re-inferred at the reporting site so the two cannot drift; a
    reporting layer that disagrees with the resolver about where the number
    came from is worse than no report at all.

    ``"fallback"`` means nothing about THIS dataset chose the cut. That is the
    case worth surfacing: the reporter in #2483 had a 94.4% match rate from the
    fixed 0.50 default, with nothing in the config or the result saying a
    decision had never been made.
    """
    if mk.link_threshold is not None:
        return LINK_THRESHOLD_CONFIGURED
    if getattr(em_result, "calibrated_link_threshold", None) is not None:
        return LINK_THRESHOLD_CALIBRATED
    return LINK_THRESHOLD_FALLBACK


def _fs_calibrate_threshold_enabled() -> bool:
    """Unsupervised per-dataset link-threshold calibration. **Default OFF.**

    When ``GOLDENMATCH_FS_CALIBRATE_THRESHOLD`` is truthy, EM picks the link cutoff
    per-dataset from the training-pair normalized-score distribution (Otsu's
    between-class-variance split) instead of the fixed 0.50. The fixed cutoff
    over-merges when non-match pairs pile up just below it and under-merges
    elsewhere; the adaptive cutoff fixes both.

    MEASURED (scripts/bench_er_headtohead panel, simple auto-config, GM
    probabilistic vs Splink, reproduced across two runs): historical_50k F1
    0.7520 -> 0.7935 (+0.0415), dblp_acm F1 0.3758 -> 0.8611 (+0.4853); febrl3/
    synthetic unchanged. NOT default-on: the auto-config CONTROLLER's refined
    config yields a different score distribution where Otsu regresses some quality
    gate datasets (anchor_person_match 1.0 -> 0.94, historical 0.81 -> 0.76). The
    Otsu selector's config-dependence must be hardened before a default flip. Ships
    opt-in; default off is byte-identical.
    """
    return os.environ.get("GOLDENMATCH_FS_CALIBRATE_THRESHOLD", "0").lower() in (
        "1", "true", "on", "yes", "enabled",
    )


# Clamp the calibrated cutoff to a sane band (mirrors compute_thresholds' own
# [0.40, 0.95] clamp; 0.90 upper keeps recall from collapsing on odd shapes).
_CALIBRATE_MIN, _CALIBRATE_MAX = 0.40, 0.90
#: Posterior-scale bounds. Wider than the linear pair because a posterior is a
#: probability: well-separated pairs sit at ~0 and ~1, so a legitimate split can
#: land far from 0.5 (the fixed posterior default is 0.99). The bounds still
#: refuse the degenerate ends -- a cut at 0.0 links everything and one at 1.0
#: links nothing, and either would be a silent disaster rather than a bad model.
_CALIBRATE_POSTERIOR_MIN, _CALIBRATE_POSTERIOR_MAX = 0.05, 0.995


def _otsu_threshold(scores) -> float | None:
    """Otsu split: the cutoff maximizing between-class variance of a [0,1] score
    histogram. For a bimodal non-match/match distribution this is the class
    boundary; for well-separated (unimodal-ish) data the exact split barely
    matters (flat F1 curve), so it stays safe."""
    nbins = 100
    hist, _ = np.histogram(scores, bins=nbins, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return None
    prob = hist / total
    centers = (np.arange(nbins) + 0.5) / nbins
    omega = np.cumsum(prob)                    # class-0 (below) weight
    mu = np.cumsum(prob * centers)             # class-0 cumulative mean
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = np.where(denom > 1e-12, (mu_t * omega - mu) ** 2 / denom, 0.0)
    k = int(np.argmax(sigma_b))
    return float((k + 1) / nbins)              # upper edge of the split bin


# ── FS threshold-refit loop (Phase 3a) ───────────────────────────────────────
# Design: docs/superpowers/specs/2026-08-02-fs-refit-loop-design.md.
# The non-iterated FS path commits a FIXED link cutoff (0.50). On over-merge-prone
# shapes (household hard-negatives: distinct people sharing a surname) the F1-
# optimal cutoff sits ABOVE 0.50, and the fixed default over-merges. This picks
# the cutoff from the ACTUAL scored-pair distribution via Otsu, GATED on a
# bimodality test so it ONLY moves when there is a genuine class-separating valley
# -- the guard that keeps it a no-op on 0.50-optimal data (historical_50k, whose
# scored distribution is one non-match mass + a declining tail, no valley).
#
# MEASURED (score-once, re-cluster sweep):
#   household_hardneg  Otsu-on-scored 0.750 == F1-optimal; valley_ratio 0.00 (gap)
#   historical_50k     Otsu-on-scored 0.540 but valley_ratio 0.55 (NO valley) -> keep 0.50
# A naive "maximize multi-member cluster count" objective was REJECTED: it peaks
# at 0.60 on historical_50k and would regress F1 0.841 -> 0.756. The valley gate,
# not the split, is what makes this safe.
_REFIT_BINS = 20
# A genuine class-separating valley is NEARLY EMPTY (household: the gap bin holds
# 0% of the smaller flank mode). A shallow dip -- e.g. ncvr's 22%-of-mode trough
# INSIDE its corruption-spread match distribution -- is a shoulder, not a class
# boundary, and cutting there removes true matches. Require a deep gap so the
# refit fires only on a real false-band / true-mass separation (measured:
# household 0.00 < 0.10 fires; ncvr 0.22 > 0.10 does not).
_REFIT_VALLEY_MAX = 0.10
_REFIT_MIN_MODE_MASS = 0.02  # each side of the valley must hold >= 2% of scored pairs
_REFIT_MIN_PAIRS = 200     # too few pairs -> histogram is noise, don't refit
# Ceiling on the share of already-matched records a raised cutoff may strand as
# SINGLETONS (see `_expelled_share`). Over-merge repair regroups records without
# stranding them, so the two regimes separate by ~160x on the panel: accept-correct
# household_hardneg 0.0000 / cotenant_hardneg 0.0006 vs reject-correct person
# 0.1020. 0.01 sits an order of magnitude clear of BOTH sides of that gap.
_REFIT_MAX_EXPELLED_SHARE = 0.01


def _fs_refit_threshold_enabled() -> bool:
    """Health-gated FS link-threshold refit. **Default ON (2026-08-13, #2518).**

    When ``GOLDENMATCH_FS_REFIT_THRESHOLD`` is truthy, the FS path picks the link
    cutoff from the actual scored-pair distribution (bimodality-gated valley +
    cluster-shape guard + expelled-share guard) instead of the fixed default,
    correcting the over-merge the non-iterated path can't see.
    ``GOLDENMATCH_FS_REFIT_THRESHOLD=0`` is the kill-switch and is byte-identical
    to the fixed cutoff.

    **Why it was flipped back (#2387).** #2377 turned this ON after validating on
    the `ab_lever` panel + QIS. Neither covers `ncvr_synthetic`, and the
    nightly-only `bench-suggest-quality` gate went red the very next morning and
    stayed red for five nights: `convergence_final_f1` 0.9828 -> 0.8881
    (**-0.0966**). Confirmed by single-variable A/B on one sha -- `=0` scores
    0.9847, default-on scores 0.8881.

    The cause is a DEFECT IN THE CLUSTER-SHAPE GUARD, not a baseline that needed
    re-blessing, which is why this is a revert rather than a re-bless. On
    ncvr_synthetic the guard commits ``0.5000 -> 0.7000 (max cluster 5 -> 2,
    over-merge reduced)`` -- and every gold cluster in that dataset is EXACTLY
    size 2 (2500 disjoint pairs, max gold size 2), so the guard's own objective is
    satisfied while F1 still falls. `max_candidate < max_default` is a
    single-outlier statistic: it fixes the one or two genuinely over-merged
    clusters and is structurally blind to the true pairs the raised cutoff drops
    across the other ~2500 correct size-2 clusters. It works on household_hardneg
    (0.947 -> 1.000) only because there over-merge IS the dominant error mode.

    **The guard defect was fixed in #2518**: the single-outlier max test is paired
    with a GLOBAL one -- `_expelled_share`, the share of matched records a
    candidate would strand as singletons -- which separates over-merge repair from
    cluster-shattering by ~160x on the panel. (The linked-pair bound #2387
    suggested does NOT work; see `fs_refit_link_threshold` for the numbers.)

    **Why the re-flip is safe this time -- validated on the panel that GATES it.**
    #2377's mistake was evidence from `ab_lever` + QIS, the panels that MOTIVATED
    the change, while the panel that gated it went unrun. So this flip was measured
    with the gate itself, `scripts.suggest_quality`, single-variable `=0` vs `=1` on
    one sha, native kernel present:

    * fast `gate` (FULL_DIST=0): **byte-identical on every metric**, both arms
      PASS. The number that reddened the nightly -- ncvr_synthetic
      `convergence_final_f1` -- is **0.9847 in BOTH arms**, exactly its `=0` value
      from the #2387 A/B (where default-on scored 0.8881).
    * heavy `gym-gate` (FULL_DIST=1, catalog recovery): run with `=1` against the
      `=0`-blessed `gym_scorecard.json` baseline.

    Mechanically the flip is a no-op on ncvr_synthetic because the valley detector
    now finds NO candidate above 0.50 there at all, so the accept criterion is
    never reached -- the guard fix and a shift in the scorer set the controller
    picks both point the same way.

    **The two gates that failed to fire in #2387 are widened in the same change**,
    since "a check exists and does not fire" is what made this a five-night red
    rather than a caught regression: `bench-suggest-quality`'s push filter now
    includes the FS refit paths (it listed only `suggest/**` +
    `scripts/suggest_quality/**`, so a `probabilistic.py` edit ran NO suggester
    gate), and `fs-lever-gate`'s PR tier now includes ncvr_synthetic -- the dataset
    that caught the regression belongs on the per-PR tier, not only the nightly."""
    return os.environ.get("GOLDENMATCH_FS_REFIT_THRESHOLD", "1").lower() in (
        "1", "true", "on", "yes", "enabled",
    )


#: Minimum-mode mass for the CALIBRATOR's population, which is not the refit
#: loop's. `_REFIT_MIN_MODE_MASS` (2%) is tuned for SCORED pairs -- already past
#: a threshold, so match-rich. The calibrator reads TRAINING pairs: blocked
#: candidates, overwhelmingly non-matches, where the true-match mode is a thin
#: sliver by construction. Measured on the 20K person fixture, its posterior
#: histogram is cleanly bimodal (9,905 pairs in bin 0, 82 in the top two, every
#: bin between empty) and the match side is 0.82% -- so the 2% floor rejects a
#: textbook separation and the calibrator falls back to a cut that scores F1
#: 0.0000.
#:
#: 0.2% admits that shape while still refusing a handful of stray pairs as a
#: "mode". It is a floor on EVIDENCE, not a claim about match rates: below it
#: the smaller flank is too small for its maximum to mean anything, and the
#: trough ratio it anchors becomes noise.
_CALIBRATE_MIN_MODE_MASS = 0.002


def _score_distribution_valley(hist, min_mode_mass: float | None = None) -> float | None:
    """Locate the class-separating VALLEY of a [0,1] score histogram: a deep
    density trough with substantial mass on BOTH sides. Returns the valley's cut
    point (the low edge of the deepest qualifying bin, in [0,1]) when the
    distribution is genuinely bimodal, else None.

    The valley -- NOT Otsu's variance split -- is the right cut: with the mode
    IMBALANCE typical of FS scores (a small false-pair band vs a huge true-match
    mass), Otsu's between-class-variance maximum lands INSIDE the dominant mode
    (measured: 0.88, deep in the true mass, cutting recall to 0.62), while the
    valley sits exactly on the false/true boundary. We do NOT anchor on the global
    peak (the true mass often dwarfs the false band, so the valley is BELOW the
    peak): instead scan every candidate cut bin, keep those with >= _REFIT_MIN_MODE
    _MASS on each side, and take the one whose trough is deepest relative to its
    flanking maxima. The gate (trough < _REFIT_VALLEY_MAX of the smaller flank)
    keeps this a no-op on overlapping unimodal-with-tail shapes (historical_50k:
    no bin has a deep trough with mass on both sides -> None)."""
    total = hist.sum()
    if total <= 0:
        return None
    # Default preserves the refit loop's behaviour exactly; the calibrator
    # passes its own floor because it reads a different population (see
    # `_CALIBRATE_MIN_MODE_MASS`).
    floor = _REFIT_MIN_MODE_MASS if min_mode_mass is None else min_mode_mass
    nb = len(hist)
    best_idx: int | None = None
    best_ratio = _REFIT_VALLEY_MAX
    for v in range(1, nb - 1):
        left, right = hist[:v], hist[v + 1:]
        if left.sum() / total < floor:
            continue
        if right.sum() / total < floor:
            continue
        smaller = min(left.max(), right.max())
        if smaller <= 0:
            continue
        ratio = hist[v] / smaller       # trough depth vs the shallower flank mode
        if ratio < best_ratio:
            best_ratio, best_idx = ratio, v
    if best_idx is None:
        return None
    return float(best_idx) / nb         # low edge of the deepest valley bin


def fs_refit_threshold(scores, default_link: float) -> float:
    """Pick the FS link cutoff from the scored-pair distribution, GATED on
    bimodality. Returns the class-separating VALLEY cut (clamped to the sane band)
    only when the distribution has a genuine trough between a false-pair band and
    the true-match mass; otherwise returns ``default_link`` unchanged -- so on a
    0.50-optimal (non-bimodal) dataset the refit is a no-op and cannot regress.
    ``scores`` is a 1-D array of the already-computed pair scores (no re-scoring)."""
    arr = np.asarray(scores, dtype=np.float64)
    if arr.shape[0] < _REFIT_MIN_PAIRS:
        return default_link
    hist, _ = np.histogram(arr, bins=_REFIT_BINS, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    valley = _score_distribution_valley(hist)
    if valley is None:
        return default_link
    return round(float(np.clip(valley, _CALIBRATE_MIN, _CALIBRATE_MAX)), 4)


def _max_cluster_size(id_a, id_b, score, threshold: float) -> int:
    """Largest cluster size when linking pairs with score >= threshold. Singletons
    can't be the max cluster, so ``build_clusters`` infers ids from the pairs (no
    need for the full row-id set)."""
    from goldenmatch.core.cluster import build_clusters  # noqa: PLC0415
    linked = [
        (int(a), int(b), float(s))
        for a, b, s in zip(id_a, id_b, score) if s >= threshold
    ]
    if not linked:
        return 0
    clusters = build_clusters(linked)
    return max((c["size"] for c in clusters.values()), default=0)


def _matched_records(id_a, id_b, score, threshold: float) -> set[int]:
    """Records that land in a MULTI-MEMBER cluster when linking at ``threshold``.

    The complement (within the linked-pair id space) is the set of records the
    cutoff leaves unmatched, which is what ``_expelled_share`` measures."""
    from goldenmatch.core.cluster import build_clusters  # noqa: PLC0415
    linked = [
        (int(a), int(b), float(s))
        for a, b, s in zip(id_a, id_b, score) if s >= threshold
    ]
    if not linked:
        return set()
    out: set[int] = set()
    for c in build_clusters(linked).values():
        if c.get("size", 0) > 1:
            out.update(int(m) for m in c["members"])
    return out


def _expelled_share(id_a, id_b, score, default_link: float, candidate: float) -> float:
    """Share of records matched at ``default_link`` that become SINGLETONS at
    ``candidate`` -- the GLOBAL statistic the max-cluster-size guard lacks.

    This is the asymmetry that separates the two ways a raised cutoff changes
    clustering, which a maximum cannot tell apart:

    * **Over-merge repair** REGROUPS records. Splitting a surname-collapsed
      cluster of 8 into three clusters of ~3 moves every record into a smaller
      cluster, but leaves them all still matched -- so the expelled share stays
      at ~0 (measured: household_hardneg 0.0000, cotenant_hardneg 0.0006).
    * **Shattering correct clusters** EXPELS records. Cutting a true size-2
      cluster leaves BOTH of its records as singletons, so the damage shows up
      directly (measured: person 0.1020, where the refit costs -0.0616 F1).

    Returns 0.0 when nothing was matched at the default (no baseline to lose)."""
    matched_default = _matched_records(id_a, id_b, score, default_link)
    if not matched_default:
        return 0.0
    matched_candidate = _matched_records(id_a, id_b, score, candidate)
    return len(matched_default - matched_candidate) / len(matched_default)


def fs_refit_link_threshold(id_a, id_b, score, default_link: float) -> float:
    """Guarded FS threshold refit: the distributional valley candidate, ACCEPTED
    only when re-clustering at it actually REDUCES over-merge (max cluster size)
    vs the default. This is the guard that a pure distributional valley needs: a
    density gap can't tell an over-merge false-pair band (household: cutting it
    shrinks giant surname-collapsed clusters, max 8 -> 3) from a gap between
    low-scoring TRUE matches and the rest (person: cutting removes real matches,
    max stays 3). Only a candidate that both (a) raises the cutoff and (b) shrinks
    the largest cluster is committed; otherwise the default stands, so clean,
    already-separated datasets (person/ncvr/historical_50k) are a no-op. Re-cluster
    only -- scores are not recomputed.

    **TWO guards, both required (#2518).** The max-cluster-size test alone is a
    SINGLE-OUTLIER statistic -- one scalar over the whole dataset -- so a candidate
    that fixes the one genuinely over-merged cluster is accepted no matter how many
    CORRECT clusters the raised cutoff shatters, because that damage sits BELOW the
    max where a max cannot see it. That is the #2387 defect. It is now paired with
    ``_expelled_share``, a GLOBAL criterion: the share of already-matched records the
    candidate would strand as singletons, capped at ``_REFIT_MAX_EXPELLED_SHARE``.
    The two are complementary and neither is sufficient alone -- the max supplies the
    positive evidence (over-merge is really being repaired), the expelled share the
    negative (correct clusters are not being shattered).

    Measured on the FS lever panel (flag ON, valley candidate vs default):

    ==================  ========  ========  =======  =======
    dataset             expelled  linked_d      dF1  correct
    ==================  ========  ========  =======  =======
    person                0.1020    0.1161  -0.0616   reject
    household_hardneg     0.0000    0.0608  +0.0313   accept
    cotenant_hardneg      0.0006    0.7341  +0.5770   accept
    ncvr_synthetic          n/a       n/a      n/a    no valley
    ==================  ========  ========  =======  =======

    ``expelled`` separates the regimes by ~160x. Note what this does and does NOT
    change: the max test alone already calls all three of these correctly, so no
    LIVE panel decision moves -- person is the shattering shape the cap is
    calibrated against, not a regression being fixed here. The case the two
    criteria actually disagree on is ncvr-shaped, and ncvr_synthetic no longer
    produces a valley above 0.50 at all (F1 0.9913 either way), so that shape is
    covered by a unit test rather than a panel dataset:
    ``test_rejects_when_correct_clusters_would_be_shattered``.

    **The linked-pair-count bound that #2387 proposed as the alternative remedy is
    FALSIFIED** -- person's 0.1161 drop sits BETWEEN the two correct accepts
    (0.0608 / 0.7341), so no bound on it can classify all three.

    **Still default-OFF.** This fixes the accept criterion; flipping the default is a
    separate decision that needs a green nightly `bench-suggest-quality` panel, which
    `ci-required` does not run. Residual known blind spot: a candidate that splits a
    correct cluster into two multi-member clusters expels nobody, so neither guard
    sees it; no panel dataset exhibits that shape."""
    candidate = fs_refit_threshold(np.asarray(score, dtype=np.float64), default_link)
    if candidate <= default_link:
        # No valley above the default -> the loop is a no-op (the common case on
        # 0.50-optimal data). DEBUG so an opt-in run can confirm it engaged.
        logger.debug(
            "FS link-threshold refit: no distributional valley above %.4f -> keeping %.4f",
            default_link, default_link,
        )
        return default_link
    max_default = _max_cluster_size(id_a, id_b, score, default_link)
    max_candidate = _max_cluster_size(id_a, id_b, score, candidate)
    if max_candidate >= max_default:
        logger.debug(
            "FS link-threshold refit: declined candidate %.4f (max cluster %d -> %d, no "
            "over-merge reduction) -> keeping %.4f",
            candidate, max_default, max_candidate, default_link,
        )
        return default_link
    expelled = _expelled_share(id_a, id_b, score, default_link, candidate)
    if expelled > _REFIT_MAX_EXPELLED_SHARE:
        logger.debug(
            "FS link-threshold refit: declined candidate %.4f despite max cluster "
            "%d -> %d (%.2f%% of matched records would be stranded as singletons, "
            "cap %.2f%%) -> keeping %.4f",
            candidate, max_default, max_candidate, expelled * 100.0,
            _REFIT_MAX_EXPELLED_SHARE * 100.0, default_link,
        )
        return default_link
    # Committed: the same observability discipline the controller uses for its
    # weighted-path commit decision, so the FS refit is auditable on one surface
    # (this is the non-iterated FS path's analogue of a RunHistory decision).
    logger.info(
        "FS link-threshold refit: %.4f -> %.4f (valley candidate; max cluster "
        "%d -> %d, over-merge reduced; %.2f%% of matched records stranded)",
        default_link, candidate, max_default, max_candidate, expelled * 100.0,
    )
    return candidate


def _calibrate_link_threshold(comp_matrix, mk, match_weights, p_match) -> float | None:
    """Pick a link cutoff from the training-pair normalized-score distribution via
    Otsu's method, instead of the fixed 0.50.

    Scores each training pair (regular fields; matches the default-config scoring
    path) via ``(total - min)/(max - min)``. Returns None if there is too little
    to calibrate.
    """
    fields = [f for f in mk.fields if f.field in match_weights]
    if not fields:
        return None
    weights = {f.field: np.asarray(match_weights[f.field], dtype=np.float64) for f in fields}
    pair_min = float(sum(w.min() for w in weights.values()))
    pair_max = float(sum(w.max() for w in weights.values()))
    if pair_max <= pair_min:
        return None
    total = np.zeros(comp_matrix.shape[0], dtype=np.float64)
    for j, f in enumerate(fields):
        lv = comp_matrix[:, j]
        obs = lv >= 0
        total[obs] += weights[f.field][lv[obs]]
    if total.shape[0] <= 50:
        return None

    # Calibrate on the scale the cut will be COMPARED against, or the number is
    # a different quantity that merely shares the [0, 1] range.
    #
    # `resolve_thresholds` hands this value straight to the scorer, and under
    # posterior calibration the scorer is comparing PROBABILITIES. Splitting the
    # linear weight envelope and applying the result as a posterior was the
    # original bug: measured on a 20K person fixture, Otsu picked 0.11 on the
    # linear scale while the posterior default for the same data is 0.99, and
    # nothing failed -- the linear value is inside [0, 1], the run reports
    # `source: "calibrated"`, and the model quietly cuts in the wrong place.
    #
    # The tell is prior-invariance: the linear envelope does not depend on the
    # match rate, so the same comparison vectors gave the same cut at a 0.1% and
    # a 40% prior. A posterior cut must move with the prior.
    posterior = _fs_calibration_mode() == "posterior"
    if posterior:
        prior_w = prior_weight(p_match)
        norm = np.asarray(
            [posterior_from_weight(float(w), prior_w) for w in total],
            dtype=np.float64,
        )
    else:
        norm = np.clip((total - pair_min) / (pair_max - pair_min), 0.0, 1.0)

    # Otsu histograms over a FIXED [0, 1] range, which is right for the linear
    # scale (min-max normalised scores spread across it) and wrong for
    # posteriors: a well-separated pair sits at ~0 or ~1, so both masses land in
    # end bins and the between-class variance peaks at the FIRST occupied bin --
    # BELOW the low cluster rather than between the two. Measured on the two
    # posterior clusters of the fixture below (0.0103 and 0.9942), Otsu returns
    # 0.02, which links everything.
    #
    # So on the posterior scale the split is taken between the two masses
    # directly. `_otsu_threshold` is left untouched: it is also used by the
    # refit loop, whose scores ARE linear, and changing it there would move a
    # separately-validated behaviour.
    t = _posterior_split(norm) if posterior else _otsu_threshold(norm)
    if t is None:
        return None
    # The [0.40, 0.90] clamp is a LINEAR-scale guard: it bounds how far a
    # min-max normalised cut may stray from the 0.50 midpoint. Posterior scores
    # pile up against both ends (a well-separated pair is ~0 or ~1), so the same
    # clamp there would force every dataset to 0.40 or 0.90 -- which is exactly
    # what it did: the 0.11 split above was clamped to 0.40 and scored well by
    # accident, because 0.40 happened to sit between this fixture's two
    # posterior clusters.
    if posterior:
        lo, hi = _CALIBRATE_POSTERIOR_MIN, _CALIBRATE_POSTERIOR_MAX
    else:
        lo, hi = _CALIBRATE_MIN, _CALIBRATE_MAX
    return round(float(np.clip(t, lo, hi)), 4)


def _posterior_split(scores) -> float | None:
    """A cut between the two posterior masses, rather than an Otsu bin edge.

    Posterior scores are not spread over [0, 1] -- they pile up near 0 and 1 --
    so a fixed-range histogram puts both classes in end bins and Otsu's split
    lands below the low mass instead of between them (measured: 0.02 for
    clusters at 0.0103 and 0.9942).

    This finds the widest GAP between consecutive sorted scores and cuts at its
    midpoint. On a genuinely bimodal set that gap is the class boundary. It
    returns None when the widest gap is not a real separation -- a set with no
    meaningful split should keep the fixed default rather than invent a cut
    from noise, which is the failure mode the refit loop's valley gate exists
    to prevent.
    """
    arr = np.asarray(scores, dtype=np.float64)
    if arr.shape[0] < 2:
        return None

    # BIMODALITY GATE, not a gap rule. The first version of this took the
    # midpoint of the widest gap between sorted scores, which finds *a*
    # separation in any distribution -- including one that has none. The
    # cross-dataset sweep (run 31844730947) measured the cost: on
    # `historical_50k` it cut at 0.348 where that dataset's optimum is 0.99,
    # taking F1 0.7462 -> 0.0005 while reporting `source: "calibrated"` and
    # returning clusters the whole way. Its shape is one non-match mass
    # decaying into the matches -- no valley, so the widest gap sits inside the
    # tail.
    #
    # `_score_distribution_valley` is the refit loop's answer to exactly this
    # problem, and its own comment names the same dataset as the reason it
    # exists ("historical_50k ... valley_ratio 0.55 (NO valley) -> keep 0.50").
    # Reusing it means one bimodality rule rather than two that can disagree
    # about what a class boundary is.
    #
    # It is deliberately reused rather than reimplemented on the posterior
    # scale: the detector reads a [0, 1] histogram and both scales are [0, 1],
    # so the only thing that changes is which quantity fills the bins.
    hist, _ = np.histogram(arr, bins=_REFIT_BINS, range=(0.0, 1.0))
    valley = _score_distribution_valley(
        hist.astype(np.float64), min_mode_mass=_CALIBRATE_MIN_MODE_MASS
    )
    if valley is None:
        return None

    # The valley is the LOW EDGE of the trough bin. Return the bin's midpoint so
    # the cut sits inside the empty region rather than on the shoulder of the
    # mass below it.
    return float(valley + 0.5 / _REFIT_BINS)


def _fs_link_threshold(
    mk: MatchkeyConfig, em_result: EMResult, calibrated: bool
) -> float:
    """The FS link cutoff: configured ``mk.link_threshold`` when set, else the
    calibrated per-dataset cutoff (when EM produced one), else the
    calibration-aware value from ``compute_thresholds``. Extracted verbatim from
    the scalar / vectorized / batched scorers (#1804 item 4) so they cannot
    drift on how the cutoff is resolved."""
    if mk.link_threshold is not None:
        return mk.link_threshold
    cal = getattr(em_result, "calibrated_link_threshold", None)
    if cal is not None:
        return cal
    link_threshold, _ = compute_thresholds(em_result, calibrated=calibrated)
    return link_threshold


def _emit_triu_pairs(
    normalized: np.ndarray,
    row_ids: list,
    threshold: float,
    exclude_pairs: set,
) -> list[tuple[int, int, float]]:
    """Emit upper-triangle (i<j) pairs whose ``normalized`` score is at/above
    ``threshold``, dropping excluded pairs and rounding to 4 dp. Extracted
    verbatim from the continuous / vectorized FS scorers (#1804 item 4)."""
    n = len(row_ids)
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

    # Build row lookup. Extend with NE field names (incl. derive_from
    # synthesized columns, materialized under the NE field's own name by
    # precompute_matchkey_transforms) so an NE-only field (the canonical
    # phone example -- not in mk.fields) is present for _ne_fired to read,
    # mirroring train_em's row_lookup projection.
    cols = _fs_projection_cols(mk)
    row_lookup: dict[int, dict] = {}
    from goldenmatch.core.frame import to_frame as _tf_d5c

    for row in _tf_d5c(block_df).select_dicts(["__row_id__"] + cols):
        row_lookup[row["__row_id__"]] = row

    from goldenmatch.core.frame import to_frame as _to_frame_d5

    row_ids = _to_frame_d5(block_df).column("__row_id__").to_list()

    ne_min_weight, ne_max_weight = _fs_ne_weight_range(em_result, mk)

    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0

    link_threshold = _fs_link_threshold(mk, em_result, calibrated)

    # Precompute per-row transformed values for TF-adjustment fields so the
    # per-pair loop can apply the same Winkler adjustment the vectorized path
    # does (#1801). Keyed identically to the freq table (str() + transforms).
    tf_row_vals: dict[str, dict[int, str | None]] = {}
    if em_result.tf_freqs:
        for f in mk.fields:
            if not getattr(f, "tf_adjustment", False):
                continue
            if f.field not in em_result.tf_freqs:
                continue
            tf_row_vals[f.field] = {
                rid: _transform_field_value(row_lookup.get(rid, {}).get(f.field), f)
                for rid in row_ids
            }

    ne_fields = mk.negative_evidence or []
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

            # Sum match weights (+ Winkler TF adjustment where opted in).
            total_weight = 0.0
            pair_min_weight = ne_min_weight
            pair_max_weight = ne_max_weight
            has_regular_evidence = False
            for k, f in enumerate(mk.fields):
                weights = em_result.match_weights[f.field]
                # #1854 full-range normalization: every field widens the min-max
                # range (before the observed guard), so a pair agreeing on its
                # one observed field can't saturate the shrunk range to 1.0.
                pair_min_weight += min(weights)
                pair_max_weight += max(weights)
                if vec[k] < 0:
                    continue
                has_regular_evidence = True
                total_weight += weights[vec[k]]
                per_row = tf_row_vals.get(f.field)
                if per_row is not None:
                    total_weight += _scalar_tf_contribution(
                        per_row.get(a), per_row.get(b), vec[k], f, em_result,
                    )
            for ne in ne_fields:
                total_weight += _ne_scalar_contribution(row_a, row_b, ne, em_result)

            if calibrated:
                normalized = posterior_from_weight(total_weight, prior_w)
            elif not has_regular_evidence and total_weight == 0.0:
                normalized = 0.5
            elif pair_max_weight > pair_min_weight:
                # Clip into [0, 1]: TF (and NE) can push the summed weight
                # past the per-level max/min, matching the vectorized path's
                # np.clip so the two routes score identically (#1801).
                normalized = min(1.0, max(0.0, (
                    total_weight - pair_min_weight
                ) / (pair_max_weight - pair_min_weight)))
            else:
                normalized = 0.5

            if not calibrated and total_weight <= 0.0 and _fs_require_positive_evidence():
                # Net-zero / net-negative evidence is a non-match (mirrors the
                # vectorized path). See _fs_require_positive_evidence.
                continue
            if normalized >= link_threshold:
                results.append((a, b, round(normalized, 4)))

    return results


def _field_values_from_list(raw: list | None, f, n: int) -> list[str | None]:
    """Transformed per-field values from an already-extracted value list,
    matching comparison_vector: str()-coerce non-null values then apply the
    field transforms, once per column instead of once per (pair, field).
    ``raw is None`` = missing column -> all-null (unobserved evidence).
    Backend-neutral (W2a): the fused FS prep feeds it from either Frame
    backend's ``utf8_values``; ``_field_values_for_block`` feeds it from the
    classic Polars block frame."""
    from goldenmatch.utils.transforms import apply_transforms

    if raw is None:
        return [None] * n
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


def _field_values_for_block(block_df: pl.DataFrame, f, n: int) -> list[str | None]:
    """Transformed per-field values for a block, matching comparison_vector.
    Missing column -> all-null (unobserved evidence).

    Prefers the precomputed ``__xform_<sig>__`` column when present (already the
    transformed values), skipping the per-row transform re-derivation -- the Vec
    (older-wheel) analog of the ``_fs_arrow_column`` fast path. Byte-identical by
    the precompute contract; falls back to the raw field otherwise."""
    from goldenmatch.core.frame import to_frame as _to_frame_d5
    from goldenmatch.core.matchkey import _xform_sig

    _bf = _to_frame_d5(block_df)
    sig = _xform_sig(f)
    if sig in _bf.columns:
        return _bf.column(sig).to_list()
    raw = _bf.column(f.field).to_list() if f.field in _bf.columns else None
    return _field_values_from_list(raw, f, n)


def _levels_from_similarity(
    sim: np.ndarray,
    levels: int,
    partial_threshold: float,
    level_thresholds: list[float] | None = None,
) -> np.ndarray:
    """Vectorized level assignment matching ``comparison_vector`` semantics.

    - custom (``level_thresholds`` given): level = count of thresholds t in
      ``level_thresholds`` with sim >= t (order-independent sum of satisfied
      descending thresholds).
    - 2 levels: 1 if sim >= partial_threshold else 0
    - 3 levels: 2 if sim >= 0.95, elif sim >= partial_threshold -> 1, else 0
    - N>3 levels: largest k in 1..N-1 with sim >= k/N (even spacing), which
      equals the count of satisfied thresholds.
    """
    if level_thresholds is not None:
        lvl = np.zeros(sim.shape, dtype=np.intp)
        for t in level_thresholds:
            lvl += (sim >= t).astype(np.intp)
        return lvl
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


def _transform_field_value(raw, f) -> str | None:
    """One value transformed the way ``comparison_vector`` and the TF freq
    table do: ``str()``-coerce a non-null value then apply the field
    transforms. ``None`` stays ``None`` (treated as disagree)."""
    if raw is None:
        return None
    from goldenmatch.utils.transforms import apply_transforms
    s = str(raw)
    if f.transforms:
        s = apply_transforms(s, f.transforms)
    return s


def _scalar_tf_contribution(va, vb, level: int, f, em_result) -> float:
    """Winkler term-frequency adjustment for a single scalar pair on ``f``.

    Scalar mirror of the vectorized ``_apply_tf_adjustment`` (#1801): a rare
    exact agreement gets a positive bump, a common one a penalty, applied
    ONLY on an exact-equal, top-level agreement (``level == f.levels - 1``
    and ``va == vb`` non-null -- matching the vectorized ``equal & (lvl ==
    top)`` mask). ``va``/``vb`` are the field values transformed identically
    to the freq table (via ``_transform_field_value``). Returns 0.0 unless
    the field opted into ``tf_adjustment`` and EM produced a table.
    """
    if not getattr(f, "tf_adjustment", False):
        return 0.0
    if not em_result.tf_freqs or f.field not in em_result.tf_freqs:
        return 0.0
    collision = (em_result.tf_collision or {}).get(f.field)
    if not collision:
        return 0.0
    if level != int(f.levels) - 1:
        return 0.0
    if va is None or va != vb:
        return 0.0
    fv = em_result.tf_freqs[f.field].get(va)
    if not fv:
        return 0.0
    return float(np.clip(math.log2(collision / fv), -_TF_CLAMP, _TF_CLAMP))


#: Scorers that CANNOT run through the scalar per-pair ``score_field`` (they are
#: matrix-only by nature) — model-backed embedding scorers. The FS path handles
#: them exclusively on the vectorized matrix (EM E-step + block scoring); they
#: never reach ``score_field``, which would raise ``Unknown scorer``.
_MODEL_BACKED_SCORERS = ("embedding", "record_embedding")


def vectorized_scorer_supported(scorer: str) -> bool:
    """Whether a REGULAR field scorer can be expressed on the NxN matrix.

    Every regular-field scorer qualifies: string scorers via
    ``_fuzzy_score_matrix`` (rapidfuzz cdist / native kernel), per-field
    ``embedding`` via the same, and record-level ``record_embedding`` via the
    dedicated branch in ``score_probabilistic_vectorized``. The model-backed
    scorers are matrix-ONLY (scalar ``score_field`` can't run them), so
    ``probabilistic_block_scorer`` forces the vectorized path for any matchkey
    carrying them and trains them via the vectorized EM E-step (#1806).
    """
    return True


def _ne_scorer_vectorizable(scorer: str) -> bool:
    """Whether a NEGATIVE-EVIDENCE field scorer runs on the NE matrix path.

    NE routes through ``_add_ne_matrix_contribution`` ->
    ``_field_score_matrix_dedup``, which does NOT special-case the model-backed
    scorers (record_embedding has no NE branch; the record-level un-gate in
    #1806 covers regular fields only). So a model-backed NE scorer still forces
    the scalar path — unchanged from before #1806. Expanding NE to model-backed
    scorers is a separate follow-up.
    """
    return scorer not in _MODEL_BACKED_SCORERS


def _fs_vectorized_supported(mk: MatchkeyConfig) -> bool:
    """Whether the matchkey can be scored on the vectorized NxN matrix path.

    Regular fields: every scorer is matrix-expressible (see
    ``vectorized_scorer_supported``). NE fields: every scorer except the
    model-backed ones (see ``_ne_scorer_vectorizable``) — an NE field carrying a
    model-backed scorer forces the scalar path, exactly as before #1806.
    """
    return all(
        vectorized_scorer_supported(f.scorer) for f in mk.fields
    ) and all(
        _ne_scorer_vectorizable(ne.scorer) for ne in (mk.negative_evidence or [])
    )


def _fs_vec_max_elems() -> int:
    """Dense-matrix guard for the vectorized FS scorers (#1826, retightened #1857).

    Maximum elements a single NxN (or coalesced SxS) float64 matrix may hold
    before the scorer REFUSES with an actionable error instead of OOMing.

    Default 5e7 elements (n~7,071). The original 2e9 (~16 GB for ONE matrix,
    n~44.7K) only bounded a single allocation, but ``score_probabilistic_vectorized``
    holds ~6 dense float64 matrices per block simultaneously (total_weight,
    pair_min/max_weight, normalized, the per-field sim + level arrays), and
    ``score_probabilistic_blocks_batched`` scores up to ``_fs_scoring_workers()``
    (<=16) blocks IN PARALLEL. So a block well under the old cap still composes
    tens of GB across the thread pool and takes the host down -- exactly the
    #1857 failure: a ~15K-row birth-year block at 1M rows (auto-config
    diversification) OOM'd a 64 GB runner mid-sweep. At 5e7 a single block's peak
    (~6 x n^2 x 8 bytes ~2.4 GB) x 16 workers stays ~40 GB, and any larger block
    refuses cleanly (a recorded error) instead of crashing the process.
    ``GOLDENMATCH_FS_VEC_MAX_ELEMS`` overrides (raise it on a big box, ``0``
    disables the guard); FS-quality-bench blocks (<=~1,550 rows) are far below it.
    """
    try:
        return int(os.environ.get("GOLDENMATCH_FS_VEC_MAX_ELEMS", "50000000"))
    except ValueError:
        return 50_000_000


def _fs_vec_guard(n: int, fn_name: str) -> None:
    cap = _fs_vec_max_elems()
    if cap and n * n > cap:
        raise ValueError(
            f"{fn_name}: block of {n:,} rows needs a dense {n:,}x{n:,} float64 "
            f"matrix (~{n * n * 8 / 1e9:.1f} GB per field) -- refusing instead "
            "of an allocator OOM (#1826). Remedies: let blocking auto-split the "
            "oversized block (default on the bucket route), refine the blocking "
            "key, set blocking.skip_oversized=true, or raise/disable this guard "
            "via GOLDENMATCH_FS_VEC_MAX_ELEMS (0 disables)."
        )


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
    fallbacks). Null values contribute no evidence, matching
    ``comparison_vector``'s ``-1`` sentinel.
    """
    if exclude_pairs is None:
        exclude_pairs = set()

    from goldenmatch.core.frame import to_frame as _tf_w6

    row_ids = _tf_w6(block_df).column("__row_id__").to_list()
    n = len(row_ids)
    if n < 2:
        return []
    _fs_vec_guard(n, "score_probabilistic_vectorized")

    calibrated = _fs_calibration_mode() == "posterior"
    _missing_mode = fs_missing_mode(mk)  # #1846
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0

    ne_min_weight, ne_max_weight = _fs_ne_weight_range(em_result, mk)

    link_threshold = _fs_link_threshold(mk, em_result, calibrated)

    # Accumulate the total match-weight matrix field by field.
    total_weight = np.zeros((n, n), dtype=np.float64)
    has_regular_evidence = np.zeros((n, n), dtype=bool)
    pair_min_weight = np.full((n, n), ne_min_weight, dtype=np.float64)
    pair_max_weight = np.full((n, n), ne_max_weight, dtype=np.float64)
    for f in mk.fields:
        if f.scorer == "record_embedding":
            # Record-level embedding: one cosine matrix over the concatenated
            # record columns. No single-field value, so no per-field null-mask
            # and no TF adjustment (mirrors the weighted find_fuzzy_matches
            # branch). The EM E-step embeds the SAME concatenation, so levels
            # agree between train and score.
            from goldenmatch.core.scorer import _record_embedding_score_matrix
            weights = np.asarray(em_result.match_weights[f.field], dtype=np.float64)
            sim = _record_embedding_score_matrix(
                block_df, f.columns or [],
                model_name=f.model or "all-MiniLM-L6-v2",
                column_weights=f.column_weights,
            )
            lvl = _levels_from_similarity(
                sim, int(f.levels), float(f.partial_threshold),
                level_thresholds=f.level_thresholds,
            )
            total_weight += weights[lvl]
            has_regular_evidence[:] = True
            pair_min_weight += float(weights.min())
            pair_max_weight += float(weights.max())
            continue
        vals = _field_values_for_block(block_df, f, n)
        weights = np.asarray(em_result.match_weights[f.field], dtype=np.float64)
        sim = _field_score_matrix_dedup(vals, f.scorer)
        lvl = _levels_from_similarity(
            sim, int(f.levels), float(f.partial_threshold), level_thresholds=f.level_thresholds
        )
        null_mask = np.array([v is None for v in vals], dtype=bool)
        # #1846: under "disagree", a missing value is evidence AGAINST a match
        # (level 0) rather than absence of evidence, so it stays "observed" and
        # `lvl` is forced to 0 -- the pre-#1834 semantics, selected per-dataset.
        observed = ~(null_mask[:, None] | null_mask[None, :])
        if _missing_mode == "disagree":
            lvl = np.where(observed, lvl, 0)
            observed = np.ones_like(observed, dtype=bool)
        has_regular_evidence |= observed
        total_weight += np.where(observed, weights[lvl], 0.0)
        # #1854 full-range normalization: the min-max range spans EVERY field,
        # not only the observed ones -- otherwise a pair agreeing on its single
        # observed field has total == pair_max and saturates to 1.0 (maximal
        # confidence from minimal evidence). `total_weight` stays observed-gated.
        # Under missing="disagree" `observed` is all-True (set above), so this is
        # identical to the previous np.where; it changes only the unobserved path.
        pair_min_weight += float(weights.min())
        pair_max_weight += float(weights.max())
        _apply_tf_adjustment(total_weight, vals, lvl, f, em_result, n)

    for ne in (mk.negative_evidence or []):
        ne_vals = _field_values_for_block(block_df, ne, n)
        _add_ne_matrix_contribution(total_weight, ne_vals, ne, em_result)

    if calibrated:
        logodds = prior_w + total_weight
        with np.errstate(over="ignore"):
            normalized = 1.0 / (1.0 + np.power(2.0, -np.clip(logodds, -60.0, 60.0)))
    else:
        # TF adjustment can push the summed weight past the per-level max, so
        # clip into [0, 1] to preserve the score contract.
        pair_range = pair_max_weight - pair_min_weight
        normalized = np.full((n, n), 0.5, dtype=np.float64)
        np.divide(
            total_weight - pair_min_weight,
            pair_range,
            out=normalized,
            where=pair_range > 0,
        )
        normalized = np.clip(normalized, 0.0, 1.0)
        normalized = np.where(
            ~has_regular_evidence & (total_weight == 0.0), 0.5, normalized
        )
        if _fs_require_positive_evidence():
            # Net-zero / net-negative evidence (LR <= 1) is a Fellegi-Sunter
            # non-match; force it below any cut so the asymmetric min-max can't
            # auto-link it into a mega-cluster. See _fs_require_positive_evidence.
            normalized = np.where(total_weight <= 0.0, -1.0, normalized)

    # Emit upper-triangle pairs at/above threshold.
    return _emit_triu_pairs(normalized, row_ids, link_threshold, exclude_pairs)


def _fs_batch_rows() -> int:
    """Row cap for batched FS block scoring (``GOLDENMATCH_FS_BATCH_ROWS``).

    Small blocks are coalesced up to this many rows so one set of per-field
    matrices covers many blocks, amortizing the per-call FFI/marshal overhead
    that dominates on the tiny blocks multi-pass blocking produces. Larger caps
    cut call count further but grow the discarded cross-block compute (the
    diagonal sub-blocks are kept; off-diagonal cells are computed and ignored),
    so the dense SxS numpy level/weight ops eventually outweigh the saved calls.
    256 is the measured sweet spot on historical_50k (a cap sweep put 256 at
    ~25.3s vs ~27.7s at 512); the cap only changes block grouping, so the
    emitted pair set is identical at any value.
    """
    try:
        return max(2, int(os.environ.get("GOLDENMATCH_FS_BATCH_ROWS", "256")))
    except ValueError:
        return 256


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
    from goldenmatch.core.frame import to_frame as _tf_w6

    for bdf in block_dfs:
        rid = _tf_w6(bdf).column("__row_id__").to_list()
        spans.append((start, start + len(rid)))
        row_ids.extend(rid)
        start += len(rid)
    S = len(row_ids)
    if S < 2:
        return []
    _fs_vec_guard(S, "score_probabilistic_vectorized_batch")

    calibrated = _fs_calibration_mode() == "posterior"
    _missing_mode = fs_missing_mode(mk)  # #1846
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0
    ne_min_weight, ne_max_weight = _fs_ne_weight_range(em_result, mk)
    link_threshold = _fs_link_threshold(mk, em_result, calibrated)

    total_weight = np.zeros((S, S), dtype=np.float64)
    has_regular_evidence = np.zeros((S, S), dtype=bool)
    pair_min_weight = np.full((S, S), ne_min_weight, dtype=np.float64)
    pair_max_weight = np.full((S, S), ne_max_weight, dtype=np.float64)
    for f in mk.fields:
        vals: list[str | None] = []
        for bdf, (s, e) in zip(block_dfs, spans):
            vals.extend(_field_values_for_block(bdf, f, e - s))
        weights = np.asarray(em_result.match_weights[f.field], dtype=np.float64)
        sim = _field_score_matrix_dedup(vals, f.scorer)
        lvl = _levels_from_similarity(
            sim, int(f.levels), float(f.partial_threshold), level_thresholds=f.level_thresholds
        )
        null_mask = np.array([v is None for v in vals], dtype=bool)
        # #1846: under "disagree", a missing value is evidence AGAINST a match
        # (level 0) rather than absence of evidence, so it stays "observed" and
        # `lvl` is forced to 0 -- the pre-#1834 semantics, selected per-dataset.
        observed = ~(null_mask[:, None] | null_mask[None, :])
        if _missing_mode == "disagree":
            lvl = np.where(observed, lvl, 0)
            observed = np.ones_like(observed, dtype=bool)
        has_regular_evidence |= observed
        total_weight += np.where(observed, weights[lvl], 0.0)
        # #1854 full-range normalization: the min-max range spans EVERY field,
        # not only the observed ones -- otherwise a pair agreeing on its single
        # observed field has total == pair_max and saturates to 1.0 (maximal
        # confidence from minimal evidence). `total_weight` stays observed-gated.
        # Under missing="disagree" `observed` is all-True (set above), so this is
        # identical to the previous np.where; it changes only the unobserved path.
        pair_min_weight += float(weights.min())
        pair_max_weight += float(weights.max())
        _apply_tf_adjustment(total_weight, vals, lvl, f, em_result, S)

    for ne in (mk.negative_evidence or []):
        ne_vals: list[str | None] = []
        for bdf, (s, e) in zip(block_dfs, spans):
            ne_vals.extend(_field_values_for_block(bdf, ne, e - s))
        _add_ne_matrix_contribution(total_weight, ne_vals, ne, em_result)

    if calibrated:
        logodds = prior_w + total_weight
        with np.errstate(over="ignore"):
            normalized = 1.0 / (1.0 + np.power(2.0, -np.clip(logodds, -60.0, 60.0)))
    else:
        normalized = np.full((S, S), 0.5, dtype=np.float64)
        pair_range = pair_max_weight - pair_min_weight
        np.divide(
            total_weight - pair_min_weight,
            pair_range,
            out=normalized,
            where=pair_range > 0,
        )
        normalized = np.clip(normalized, 0.0, 1.0)
        normalized = np.where(
            ~has_regular_evidence & (total_weight == 0.0), 0.5, normalized
        )
        if _fs_require_positive_evidence():
            # Net-zero / net-negative evidence is a non-match; keep it below any
            # cut (mirrors score_probabilistic_vectorized). See the helper.
            normalized = np.where(total_weight <= 0.0, -1.0, normalized)

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
    target_ids: set[int] | None = None,
):
    """Collect + score ``blocks`` in row-capped batches via the SxS batch scorer.

    Threads the running exclude set across batches so a pair emitted in an
    earlier batch is suppressed later — matching the per-block loop's
    block-by-block ``matched_pairs`` dedup. Falls back to the per-block scorer
    when the vectorized numpy path isn't active (native FS kernel / scalar /
    model-backed scorers), since the batching is a numpy-path optimization.
    When ``target_ids`` is provided (two-table linkage), only pairs with exactly
    one target-side id are returned. Does NOT mutate the caller's
    ``exclude_pairs``; the caller folds the returned pairs into ``matched_pairs``
    as before.
    """
    from concurrent.futures import ThreadPoolExecutor


    if exclude_pairs is None:
        exclude_pairs = set()
    if cap is None:
        cap = _fs_batch_rows()

    use_vec = (
        not _fs_native_eligible(mk)
        and _fs_vectorized_enabled()
        and _fs_vectorized_supported(mk)
    )
    base_excl = set(exclude_pairs)

    def _bdf(block):
        return block.materialize().native

    def _eligible(pairs):
        if target_ids is None:
            return pairs
        return [
            (a, b, score)
            for a, b, score in pairs
            if (a in target_ids) != (b in target_ids)
        ]

    # Split the work into independent scoring units + a per-unit scorer. Native/
    # scalar path: one unit per block. Vectorized path: row-capped batches of
    # blocks (the SxS batch scorer amortizes rapidfuzz.cdist across a batch).
    if not use_vec:
        scorer = probabilistic_block_scorer(mk, em_result)
        units: list[list] = [[_bdf(block)] for block in blocks]

        def _score_unit(unit, excl):
            return scorer(unit[0], excl)
    else:
        units = []
        batch: list = []
        rows = 0
        for block in blocks:
            bdf = _bdf(block)
            h = block.n_rows()
            if batch and rows + h > cap:
                units.append(batch)
                batch, rows = [], 0
            batch.append(bdf)
            rows += h
            if rows >= cap:
                units.append(batch)
                batch, rows = [], 0
        if batch:
            units.append(batch)

        def _score_unit(unit, excl):
            return score_probabilistic_vectorized_batch(unit, mk, em_result, excl)

    workers = _fs_scoring_workers()
    results: list[tuple[int, int, float]] = []

    # Sequential path (also the deterministic reference): thread the running
    # exclude set across units, byte-identical to the historical loop.
    if workers <= 1 or len(units) <= 1:
        excl = set(base_excl)
        for unit in units:
            pairs = _eligible(_score_unit(unit, excl))
            for a, b, _s in pairs:
                excl.add((min(a, b), max(a, b)))
            results.extend(pairs)
        return results

    # Parallel path: units are independent and the FS kernels (native Rust /
    # rapidfuzz.cdist) release the GIL, so a thread pool gives real parallelism —
    # the property the weighted scorer already exploits in score_blocks_parallel.
    # Each unit is scored against a frozen snapshot of the exclude set; a pair
    # surfaced by more than one unit scores identically in each, so a
    # canonical-key dedup in unit order reproduces the sequential running-exclude
    # output exactly (verified by tests/test_probabilistic_parallel.py).
    frozen = frozenset(base_excl)
    seen: set[tuple[int, int]] = set(frozen)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for pairs in executor.map(lambda u: _score_unit(u, frozen), units):
            for a, b, s in _eligible(pairs):
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                results.append((a, b, s))
    return results


def _fs_scoring_workers() -> int:
    """Thread-pool size for parallel probabilistic block scoring.

    ``GOLDENMATCH_FS_WORKERS`` overrides; default is ``min(cpu_count(), 16)``
    (mirrors the weighted path's ``_DEFAULT_MAX_WORKERS``). ``1`` forces the
    sequential unit loop — the byte-identical, deterministic reference path.
    Both FS kernels (native Rust, vectorized ``rapidfuzz.cdist``) release the
    GIL, so threads give real parallelism.
    """
    val = os.environ.get("GOLDENMATCH_FS_WORKERS")
    if val is not None:
        try:
            return max(1, int(val))
        except ValueError:
            return 1
    return min(16, (os.cpu_count() or 1))


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


# Scorer-name -> native kernel id for the FS kernel. 0..=3 are score-core's
# score_one ids; 4/5 are the FS-kernel reserved reference-data name scorers
# (goldenmatch-fs-core FS_SCORER_NAME_FREQ_WEIGHTED / FS_SCORER_GIVEN_NAME_ALIASED),
# dispatched to the process-registered census / alias tables inside score_fs_pair.
# soundex/embedding/record_embedding are absent on purpose — those fields force
# the numpy fallback (the kernel's score_one doesn't implement them).
_NATIVE_FS_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3,
    "name_freq_weighted_jw": 4, "given_name_aliased_jw": 5, "ensemble": 6,
    # Model-backed embedding scorers (kernel id 7). The field's value is a dense
    # vector, not a string: the host embeds the column and marshals the
    # already-L2-normalized vectors; the kernel scores the pair as their cosine
    # (dot). Native only when the wheel advertises FS_SUPPORTS_EMBEDDING. NOT a
    # valid NE scorer (the kernel's NE path is score_one 0..=3 + ensemble only).
    "embedding": 7, "record_embedding": 7,
}
# The model-backed embedding scorers (kernel id 7). A field carrying one is native
# only when the wheel advertises FS_SUPPORTS_EMBEDDING; the host marshals the
# precomputed vectors. Same members as `_MODEL_BACKED_SCORERS` (the numpy-path
# name), kept here as a frozenset for the eligibility gates.
_EMBEDDING_SCORER_IDS: frozenset[str] = frozenset({"embedding", "record_embedding"})
# The reference-data name scorers (kernel ids 4/5). A field carrying one is
# native only when the wheel advertises FS_SUPPORTS_NAME_SCORERS AND the relevant
# refdata pack is loaded (so the injected table matches the numpy path). NE fields
# may NOT use these (the kernel's NE path is score_one 0..=3 only).
_NAME_SCORER_IDS: frozenset[str] = frozenset(
    {"name_freq_weighted_jw", "given_name_aliased_jw"}
)
# The FUSED match kernel (`match_fused_fs`) now scores each field through
# fs-core's `field_similarity` (the SAME dispatch `score_fs_pair` uses), so it
# reaches the reference-data name scorers (`name_freq_weighted_jw` id 4 /
# `given_name_aliased_jw` id 5) and `ensemble` (id 6) — matching the classic
# `_NATIVE_FS_SCORER_IDS` regular-field set (embedding id 7 stays out: the fused
# kernel takes a raw string columns mapping, not precomputed vectors). Coverage of
# ids 4/5/6 on the fused path is wheel-gated on `FUSED_FS_SUPPORTS_NAME_SCORERS`
# (see `_fused_fs_matchkey_covered`); an old wheel's fused kernel scored those ids
# via the stateless `score_one` catch-all, so it declines to the classic path.
# NOTE: this is the REGULAR-field set. Fused NE stays ids 0..=3 (the kernel
# validates ne_scorer_ids in 0..=3 and has no reference-data / vector access on
# its NE path) — `_fused_fs_matchkey_covered` restricts NE separately, so do NOT
# read NE coverage off this map.
_FUSED_FS_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3,
    "name_freq_weighted_jw": 4, "given_name_aliased_jw": 5, "ensemble": 6,
}
# Fused NE scorers stay the score_one base (0..=3) — the fused kernel's NE path
# has no reference-data/vector access and validates ids in 0..=3.
_FUSED_FS_NE_SCORER_IDS: frozenset[str] = frozenset(
    {"jaro_winkler", "levenshtein", "token_sort", "exact"}
)


def _fs_name_refdata_available(scorers: set[str]) -> bool:
    """Whether the refdata pack backing each needed name scorer is loaded:
    ``name_freq_weighted_jw`` -> surnames census, ``given_name_aliased_jw`` ->
    given-name aliases. Fail-closed (any import/error -> not available)."""
    try:
        if "name_freq_weighted_jw" in scorers:
            from goldenmatch.refdata.surnames import is_available as _surn_avail
            if not _surn_avail():
                return False
        if "given_name_aliased_jw" in scorers:
            from goldenmatch.refdata.given_names import is_available as _given_avail
            if not _given_avail():
                return False
        return True
    except Exception:
        return False


def _ensure_fs_name_refdata(mod) -> None:
    """Register the census / alias tables into the native kernel ONCE per process
    (the design's "build the index once, inject a handle" contract). Idempotent:
    skips when the kernel already carries the tables. Fail-open — a registration
    error leaves the kernel with no tables, so name-scorer fields degrade to plain
    JW (and `_fs_native_eligible` already declined the matchkey if the pack that
    feeds the table was absent)."""
    try:
        if getattr(mod, "has_name_reference_data", None) is None:
            return
        if mod.has_name_reference_data():
            return
        from goldenmatch.refdata.given_names import export_alias_forms
        from goldenmatch.refdata.surnames import export_counts
        surname_counts = [(n, float(c)) for n, c in export_counts()]
        alias_forms = export_alias_forms()
        mod.set_name_reference_data(surname_counts, alias_forms)
    except Exception:
        logger.debug("FS name refdata registration skipped", exc_info=True)


def _fs_native_enabled() -> bool:
    """Whether the native FS block kernel is active. **Default ON (reference mode).**

    Under Rust-is-the-reference (2026-07-01,
    `docs/design/2026-07-01-rust-is-the-reference-roadmap.md`) the native FS kernel
    is the authoritative FS scorer -- rapidfuzz-rs decides the comparison levels;
    the numpy vectorized path is the reproducible FALLBACK, selected explicitly
    with `GOLDENMATCH_FS_NATIVE=0` (or when the native ext isn't built / a field is
    ineligible -- see `_fs_native_eligible`).

    The DISCRETE-level sensitivity that kept this opt-in still exists (a similarity
    on a `partial_threshold` -- token_sort ratios are rationals like 0.7 / 0.857 --
    can flip a level between rapidfuzz-rs and Python-rapidfuzz, swinging the
    normalized score up to ~0.45), but under reference mode the native result IS
    the answer, so it is a defined, reproducible output rather than a divergence.
    The default flip was gated on the probabilistic bench panel (gm_prob_native vs
    gm_probabilistic F1 non-regression) -- see the PR. `=0` restores the prior
    numpy operating point.
    """
    val = os.environ.get("GOLDENMATCH_FS_NATIVE")
    if val is not None and val.strip().lower() in ("0", "false", "no", "off", "disabled"):
        return False  # explicit opt-out to the reproducible numpy fallback
    from goldenmatch.core._native_loader import native_enabled
    return native_enabled("block_scoring")


def _fs_native_eligible(mk: MatchkeyConfig) -> bool:
    """Whether (mk) can use the native FS kernel.

    Every field scorer must be one the kernel's score_one implements, and no
    field may opt into TF adjustment (the kernel doesn't carry the per-value
    frequency tables — those fields stay on the numpy path). Custom
    ``level_thresholds`` banding is native from goldenmatch-native >= 0.1.14:
    the kernel's ``score_block_pairs_fs`` takes an optional per-field
    ``level_thresholds`` kwarg and ``score.rs fs_level_from_sim`` bands with
    the exact ``_levels_from_similarity`` custom semantics (level = count of
    satisfied thresholds, ``>=`` inclusive). Support is detected via the
    kernel's ``FS_SUPPORTS_LEVEL_THRESHOLDS`` module const — an older wheel
    without it declines here, so those environments keep the pure-Python
    (numpy/scalar) fallback where `_levels_from_similarity` does the banding.

    Negative evidence (``mk.negative_evidence``) is native from
    goldenmatch-native >= 0.1.15: eligible when EVERY NE field's scorer is in
    ``_NATIVE_FS_SCORER_IDS`` (an ``ensemble``-scorer NE field — autoconfig's
    default pick for unknown columns — declines the whole matchkey to the
    numpy path) AND the loaded module exposes ``FS_SUPPORTS_NE``. Old wheels
    lack the const and decline here exactly as before the port.
    """
    if not _fs_native_enabled():
        return False
    if not mk.fields:
        return False
    # Missing-value mode. "unobserved"/neutral (a null field is skipped) is the
    # native default. "disagree" (#1834/#1851: auto-config picks it per-dataset for
    # null-heavy data like historical_50k) scores a null field as level 0 (evidence
    # AGAINST); the kernel handles it via the `missing_disagree=` kwarg on wheels
    # that expose FS_SUPPORTS_MISSING_DISAGREE. Older wheels lack the kwarg, so
    # `needs_disagree` declines them to the numpy path below (which honors both).
    needs_disagree = fs_missing_mode(mk) == "disagree"
    needs_ensemble = False
    ne_fields = mk.negative_evidence or []
    for ne in ne_fields:
        # NE goes through field_similarity (score_one 0..=3 + ensemble id 6); the
        # reference-data name scorers (4/5) and embedding (7) are NOT valid NE
        # scorers (the kernel's NE path has no vector access).
        if (
            ne.scorer not in _NATIVE_FS_SCORER_IDS
            or ne.scorer in _NAME_SCORER_IDS
            or ne.scorer in _EMBEDDING_SCORER_IDS
        ):
            return False
        if ne.scorer == "ensemble":
            needs_ensemble = True
    needs_level_thresholds = False
    needs_tf = False
    needs_embedding = False
    name_scorers_needed: set[str] = set()
    for f in mk.fields:
        if f.scorer not in _NATIVE_FS_SCORER_IDS:
            return False
        if getattr(f, "tf_adjustment", False):
            needs_tf = True
        if getattr(f, "level_thresholds", None) is not None:
            needs_level_thresholds = True
        if f.scorer in _NAME_SCORER_IDS:
            name_scorers_needed.add(f.scorer)
        if f.scorer == "ensemble":
            needs_ensemble = True
        if f.scorer in _EMBEDDING_SCORER_IDS:
            needs_embedding = True
    if name_scorers_needed and not _fs_name_refdata_available(name_scorers_needed):
        # The pack that would supply the injected table isn't loaded, so the
        # kernel would degrade to plain JW — diverging from the numpy path (which
        # ALSO degrades, but only via its own is_available gate). Decline to the
        # numpy path so the two never disagree on an unloaded pack.
        return False
    needs_name_scorers = bool(name_scorers_needed)
    try:
        from goldenmatch.core._native_loader import native_module
        mod = native_module()
        if not hasattr(mod, "score_block_pairs_fs"):
            return False
        if not getattr(mod, "FS_SUPPORTS_MISSING_NEUTRAL", False):
            return False  # old wheel: nulls are incorrectly scored as level 0
        if needs_disagree and not getattr(
            mod, "FS_SUPPORTS_MISSING_DISAGREE", False
        ):
            return False  # old wheel: no missing_disagree kwarg -> numpy path
        if needs_level_thresholds and not getattr(
            mod, "FS_SUPPORTS_LEVEL_THRESHOLDS", False
        ):
            return False  # old wheel: level_thresholds never crosses its FFI
        if ne_fields and not getattr(mod, "FS_SUPPORTS_NE", False):
            return False  # old wheel: NE never crosses its FFI
        if needs_name_scorers and not getattr(mod, "FS_SUPPORTS_NAME_SCORERS", False):
            return False  # old wheel: name scorers never cross their FFI
        if needs_tf and not getattr(mod, "FS_SUPPORTS_TF_ADJUSTMENT", False):
            return False  # old wheel: tf_freqs/tf_collision never cross their FFI
        if needs_ensemble and not getattr(mod, "FS_SUPPORTS_ENSEMBLE", False):
            return False  # old wheel: scores ensemble (id 6) as 0.0 (catch-all)
        if needs_embedding and not getattr(mod, "FS_SUPPORTS_EMBEDDING", False):
            return False  # old wheel: no emb_vectors kwarg / scores id 7 as 0.0
        return True
    except Exception:
        return False


def _fs_arrow_column(native_df, f, n: int):
    """Arrow column of transformed values for one field (#1803 item 2).

    Zero-copy when the column is already utf8/large_utf8 AND the field has no
    transforms (``str(v)`` on a string is the identity, so the raw arrow
    buffer IS the Vec entry's value list). Otherwise materialize exactly the
    values the Vec entry would receive (``_field_values_from_list``) into one
    arrow array — still one C-level build instead of the per-element PyO3
    ``Vec<Vec<Option<String>>>`` clone. Byte-identical either way.
    """
    import pyarrow as _pa

    from goldenmatch.core.frame import is_polars_dataframe as _ipd
    from goldenmatch.core.frame import to_frame as _tf
    from goldenmatch.core.matchkey import _xform_sig

    _bf = _tf(native_df)
    # Prefer the precomputed ``__xform_<sig>__`` column (materialized ONCE on the
    # full frame by ``precompute_matchkey_transforms``). It already holds exactly
    # the transformed values ``_field_values_from_list`` would rebuild, so reading
    # it returns the arrow buffer ZERO-COPY and skips the per-bucket, per-row
    # transform re-derivation that otherwise dominated FS scoring input-prep.
    # Mirrors the weighted fast path (``score_buckets`` reads ``_xform_sig(f)``);
    # byte-identical by the precompute contract (``__xform_<sig>__`` == the
    # apply_transforms fallback). Falls back to the raw field where the column is
    # absent (paths that skip precompute).
    sig = _xform_sig(f)
    if sig in _bf.columns:
        native = _bf.native
        arr = (
            native[sig].to_arrow() if _ipd(native)
            else native.column(sig).combine_chunks()
        )
        if _pa.types.is_string(arr.type) or _pa.types.is_large_string(arr.type):
            return arr
    if f.field not in _bf.columns:
        return _pa.nulls(n, type=_pa.large_string())
    native = _bf.native
    if _ipd(native):
        arr = native[f.field].to_arrow()
    else:  # pa.Table lane
        arr = native.column(f.field).combine_chunks()
    if not f.transforms and (
        _pa.types.is_string(arr.type) or _pa.types.is_large_string(arr.type)
    ):
        return arr
    vals = _field_values_from_list(_bf.column(f.field).to_list(), f, n)
    return _pa.array(vals, type=_pa.large_string())


def _record_concat_values(frame, f, n: int) -> list[str]:
    """Per-row concatenated text for a ``record_embedding`` field — a byte-for-byte
    copy of the concat in ``core/scorer._record_embedding_score_matrix`` (``col:
    val`` parts joined by `` | ``, ``column_weights`` repeats, ``""`` when empty),
    so native embeds the SAME strings the numpy path does. Built via column lists
    (works for both polars + arrow frames), never ``iter_rows``."""
    from goldenmatch.core.frame import to_frame as _tf_rc

    bf = _tf_rc(frame)
    cols = list(f.columns or [])
    weights = f.column_weights
    data: dict[str, list] = {
        c: bf.column(c).to_list() for c in cols if c in bf.columns
    }
    out: list[str] = []
    for r in range(n):
        parts: list[str] = []
        for c in cols:
            val = data.get(c, [None] * n)[r]
            if weights is not None:
                w = weights.get(c, 1.0)
                if w <= 0:
                    continue
                if val is not None:
                    part = f"{c}: {val}"
                    repeats = round(w) if w > 1.0 else 1
                    parts.extend([part] * repeats)
            elif val is not None:
                parts.append(f"{c}: {val}")
        out.append(" | ".join(parts) if parts else "")
    return out


def _fs_embedding_vectors(frame, mk: MatchkeyConfig, n: int):
    """Per-field embedding vectors for the native FS kernel (scorer id 7).

    Returns ``(emb_vectors, emb_dims)`` — lists indexed like ``mk.fields``:
    ``None``/``0`` for a non-embedding field, else the ROW-MAJOR ``n*dim`` f64
    buffer + its ``dim``. Each field is embedded with the SAME embedder + SAME
    values the numpy path feeds: an ``embedding`` field uses the transformed
    column (``_field_values_for_block`` -> ``_fuzzy_score_matrix(vals,
    "embedding")``); a ``record_embedding`` field uses the concatenated record
    text (``_record_concat_values`` -> ``_record_embedding_score_matrix``). So
    native's per-pair ``dot(row_i, row_j)`` equals the numpy ``embeddings @
    embeddings.T`` cosine within f64 tolerance (embeddings are L2-normalized).
    """
    import numpy as _np

    from goldenmatch.core.embedder import get_embedder

    emb_vectors: list[list[float] | None] = []
    emb_dims: list[int] = []
    for f in mk.fields:
        if f.scorer not in _EMBEDDING_SCORER_IDS:
            emb_vectors.append(None)
            emb_dims.append(0)
            continue
        if f.scorer == "record_embedding":
            vals: list = _record_concat_values(frame, f, n)
        else:
            vals = _field_values_for_block(frame, f, n)
        embedder = get_embedder(f.model or "all-MiniLM-L6-v2")
        vecs = _np.asarray(
            embedder.embed_column(vals, cache_key=f"_fsnat_{id(vals)}"),
            dtype=_np.float64,
        )
        dim = int(vecs.shape[1]) if vecs.ndim == 2 and vecs.shape[0] == n else 0
        emb_vectors.append([float(x) for x in vecs.reshape(-1)] if dim else None)
        emb_dims.append(dim)
    return emb_vectors, emb_dims


# BUCKET_DEBUG: split the native FS scoring kernel into the Rust call (compute +
# pyo3 Vec<tuple> conversion) vs the Python pair-marshal loop
# (`[(a,b,round(float(s),4)) ...]` over every emitted pair). Thread-safe because
# the bucket workers run in a ThreadPoolExecutor. score_buckets prints this in
# its BUCKET_DEBUG summary. Zero cost when off (one env read per bucket call).
_FS_NATIVE_DBG = {
    "prep_s": 0.0, "native_s": 0.0, "marshal_s": 0.0,
    "n_pairs": 0, "n_calls": 0, "n_single_block": 0, "single_block_prep_s": 0.0,
}
_FS_NATIVE_DBG_LOCK = threading.Lock()


def _fs_native_dbg_on() -> bool:
    return os.environ.get("GOLDENMATCH_BUCKET_DEBUG", "0") not in (
        "0", "", "false", "False", "no", "off",
    )


def _fs_native_dbg_record(
    prep_s: float, native_s: float, marshal_s: float, n_pairs: int, single_block: bool
) -> None:
    with _FS_NATIVE_DBG_LOCK:
        _FS_NATIVE_DBG["prep_s"] += prep_s
        _FS_NATIVE_DBG["native_s"] += native_s
        _FS_NATIVE_DBG["marshal_s"] += marshal_s
        _FS_NATIVE_DBG["n_pairs"] += n_pairs
        _FS_NATIVE_DBG["n_calls"] += 1
        if single_block:
            _FS_NATIVE_DBG["n_single_block"] += 1
            _FS_NATIVE_DBG["single_block_prep_s"] += prep_s


def _score_fs_native_frame(
    frame,
    size_list,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
    exclude_handle=None,
) -> list[tuple[int, int, float]]:
    """Shared native FS kernel call over ``frame`` with per-block ``size_list``.

    ``frame`` must be sorted so blocks are contiguous in the order ``size_list``
    delimits (``size_list`` is the run-length list of block sizes). All kernel
    args (row_ids, per-field ``field_values``, negative-evidence arrays) are
    built over the WHOLE frame in row order — the kernel slices per block using
    ``size_list`` (``score.rs`` walks contiguous spans and only compares WITHIN
    each block), so:

      - single block:  ``size_list = [n]`` (``score_probabilistic_native``)
      - whole bucket:  ``size_list`` = run-lengths over the block-sorted bucket
        (``score_probabilistic_bucket_native``)

    are byte-identical: the batched output equals concatenating per-block scores.
    Caller gates on ``_fs_native_eligible``.
    """
    from goldenmatch.core._native_loader import native_module
    from goldenmatch.core.frame import to_frame as _tf_w6

    _dbg_entry = _fs_native_dbg_on()
    _t_entry = time.perf_counter() if _dbg_entry else 0.0

    if exclude_pairs is None:
        exclude_pairs = set()

    row_ids = _tf_w6(frame).column("__row_id__").to_list()
    n = len(row_ids)
    if n < 2:
        return []

    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0
    # NE-aware weight envelope (FS_SUPPORTS_NE): NE fields cross the FFI on
    # this path now, and `fs_weight_range` genuinely covers their (min, max)
    # contribution here -- `-abs(penalty_bits)` or the EM-learned
    # `__ne__<field>` fired weight -- the same centralized envelope the numpy
    # and scalar paths normalize against.
    min_weight, max_weight = fs_weight_range(em_result, mk)
    weight_range = max_weight - min_weight
    # _fs_link_threshold honors mk.link_threshold, then the EM-calibrated cutoff,
    # then compute_thresholds — so the native path calibrates like numpy does.
    link_threshold = _fs_link_threshold(mk, em_result, calibrated)

    mod = native_module()
    # Register the census / alias tables once per process when a name scorer is in
    # play (kernel ids 4/5). Cheap no-op once registered; `_fs_native_eligible`
    # already gated on the pack being loaded + FS_SUPPORTS_NAME_SCORERS.
    if any(f.scorer in _NAME_SCORER_IDS for f in mk.fields):
        _ensure_fs_name_refdata(mod)
    # Zero-copy arrow entry (#1803 item 2): route to score_block_pairs_fs_arrow
    # when the wheel carries it; old wheels keep the Vec entry byte-identically.
    use_arrow = bool(getattr(mod, "FS_SUPPORTS_ARROW", False))
    scorer_ids = [_NATIVE_FS_SCORER_IDS[f.scorer] for f in mk.fields]
    levels = [int(f.levels) for f in mk.fields]
    partials = [float(f.partial_threshold) for f in mk.fields]
    weights = [[float(w) for w in em_result.match_weights[f.field]] for f in mk.fields]
    # Shared exclude handle (#1803 item 1, the #552/#688 fix on the FS side):
    # when the caller prebuilt an ExcludeSet (score_buckets, once per call),
    # skip the per-call canonicalize + marshal entirely. Handle only usable
    # when the wheel supports it (arrow entry always does; the Vec entry needs
    # FS_SUPPORTS_EXCLUDE_SET); otherwise fall back to the Vec contract.
    use_handle = exclude_handle is not None and (
        use_arrow or bool(getattr(mod, "FS_SUPPORTS_EXCLUDE_SET", False))
    )
    if use_handle:
        excl: list[tuple[int, int]] = []
    else:
        # Kernel canonicalizes pair_key to (min,max); pass pre-canonicalized.
        excl = [(a, b) if a < b else (b, a) for a, b in exclude_pairs]

    # Optional capability kwargs. Each group is sent ONLY when the matchkey
    # actually uses the feature — an old wheel must NEVER see the kwarg, even
    # if the eligibility gate ever drifted.
    opt_kwargs: dict = {}
    # Net-zero-evidence filter (mirrors the numpy path). Pass the kwarg only when
    # the wheel advertises support (FS_SUPPORTS_REQUIRE_POSITIVE_EVIDENCE); an
    # OLDER wheel without the param then degrades gracefully to the legacy native
    # behavior instead of raising on the unknown kwarg. See
    # _fs_require_positive_evidence.
    if _fs_require_positive_evidence() and getattr(
        mod, "FS_SUPPORTS_REQUIRE_POSITIVE_EVIDENCE", False
    ):
        opt_kwargs["require_positive_evidence"] = True

    # Missing="disagree": a null field scores as comparison level 0 (evidence
    # AGAINST a match) instead of unobserved. Gated on the capability flag so an
    # older wheel -- which `_fs_native_eligible` already declined for disagree --
    # never receives the unknown kwarg. When absent, the kernel keeps the neutral
    # "unobserved" default (byte-identical to before this kwarg).
    if fs_missing_mode(mk) == "disagree" and getattr(
        mod, "FS_SUPPORTS_MISSING_DISAGREE", False
    ):
        opt_kwargs["missing_disagree"] = True

    # Custom banding (goldenmatch-native >= 0.1.14).
    level_thresholds = [
        list(f.level_thresholds) if f.level_thresholds is not None else None
        for f in mk.fields
    ]
    if any(t is not None for t in level_thresholds):
        opt_kwargs["level_thresholds"] = level_thresholds

    # Negative evidence (goldenmatch-native >= 0.1.15, FS_SUPPORTS_NE). Values
    # go through the same _field_values_for_block transform path as regular
    # fields (NegativeEvidenceField shares the .field/.transforms duck-type;
    # derive_from-synthesized NE columns already exist on the block frame via
    # precompute_matchkey_transforms upstream). w_fired resolution mirrors
    # _ne_scalar_contribution: -abs(penalty_bits) when set, else the
    # EM-learned __ne__<field> fired weight (a missing entry raising KeyError
    # matches the scalar path's contract; validate_for guarantees it exists).
    ne_fields = mk.negative_evidence or []
    if ne_fields:
        if use_arrow:
            opt_kwargs["ne_arrays"] = [
                _fs_arrow_column(frame, ne, n) for ne in ne_fields
            ]
        else:
            opt_kwargs["ne_values"] = [
                _field_values_for_block(frame, ne, n) for ne in ne_fields
            ]
        opt_kwargs["ne_scorer_ids"] = [
            _NATIVE_FS_SCORER_IDS[ne.scorer] for ne in ne_fields
        ]
        opt_kwargs["ne_thresholds"] = [float(ne.threshold) for ne in ne_fields]
        opt_kwargs["ne_weights"] = [
            -abs(float(ne.penalty_bits)) if ne.penalty_bits is not None
            else float(em_result.match_weights[f"__ne__{ne.field}"][0])
            for ne in ne_fields
        ]

    # Winkler TF adjustment: per-field frequency tables (kernel applies the
    # log2(collision/freq) bump on exact-equal top-level agreements). Passed only
    # when a field opted in AND EM produced the table (mirrors the numpy path's
    # own `em_result.tf_freqs` gate). Field values reach the kernel already
    # transformed, matching the transformed-value keys of tf_freqs.
    if em_result.tf_freqs and any(
        getattr(f, "tf_adjustment", False) for f in mk.fields
    ):
        tf_freqs_list: list[dict[str, float] | None] = []
        tf_collision_list: list[float | None] = []
        for f in mk.fields:
            freqs = em_result.tf_freqs.get(f.field) if getattr(
                f, "tf_adjustment", False
            ) else None
            if freqs:
                tf_freqs_list.append(dict(freqs))
                tf_collision_list.append(
                    float((em_result.tf_collision or {}).get(f.field, 0.0))
                )
            else:
                tf_freqs_list.append(None)
                tf_collision_list.append(None)
        opt_kwargs["tf_freqs"] = tf_freqs_list
        opt_kwargs["tf_collision"] = tf_collision_list

    # Embedding scorers (goldenmatch-native FS_SUPPORTS_EMBEDDING). The host
    # embeds each `embedding` / `record_embedding` field and marshals the
    # L2-normalized vectors; the kernel scores the pair as their cosine (dot).
    # Sent only when the matchkey carries an embedding field (an old wheel must
    # never see the kwarg — `_fs_native_eligible` already gated on the capability).
    has_embedding = any(f.scorer in _EMBEDDING_SCORER_IDS for f in mk.fields)
    if has_embedding:
        emb_vectors, emb_dims = _fs_embedding_vectors(frame, mk, n)
        opt_kwargs["emb_vectors"] = emb_vectors
        opt_kwargs["emb_dims"] = emb_dims

    if use_arrow:
        import pyarrow as _pa

        from goldenmatch.core.frame import is_polars_dataframe as _ipd

        native_df = _tf_w6(frame).native
        if _ipd(native_df):
            row_ids_arrow = native_df["__row_id__"].cast(pl.Int64).to_arrow()
        else:  # pa.Table lane: combine chunks for the FFI
            import pyarrow.compute as _pc

            row_ids_arrow = _pc.cast(
                native_df.column("__row_id__").combine_chunks(), _pa.int64()
            )
        field_arrays = [_fs_arrow_column(frame, f, n) for f in mk.fields]
        # record_embedding has no single value column (`f.field` isn't in the
        # frame), so _fs_arrow_column returns all-null -> the kernel would treat it
        # as unobserved. A record embedding is ALWAYS observed (the concat is ""
        # for empty, never null), so pin its value column to a never-null sentinel;
        # the actual similarity rides in emb_vectors.
        for _i, _f in enumerate(mk.fields):
            if _f.scorer == "record_embedding":
                field_arrays[_i] = _pa.array([""] * n, type=_pa.large_string())
        if use_handle:
            opt_kwargs["exclude_set"] = exclude_handle
        _t0 = time.perf_counter() if _dbg_entry else 0.0
        pairs = mod.score_block_pairs_fs_arrow(
            row_ids_arrow, field_arrays, [int(s) for s in size_list],
            scorer_ids, levels, partials, weights, calibrated, prior_w,
            min_weight, weight_range, link_threshold,
            exclude=excl if excl else None,
            **opt_kwargs,
        )
        _t1 = time.perf_counter() if _dbg_entry else 0.0
        out = [(a, b, round(float(s), 4)) for a, b, s in pairs]
        if _dbg_entry:
            _fs_native_dbg_record(
                _t0 - _t_entry, _t1 - _t0, time.perf_counter() - _t1,
                len(out), len(size_list) == 1,
            )
        return out

    field_values = [_field_values_for_block(frame, f, n) for f in mk.fields]
    # record_embedding is always observed (see the arrow branch above) — pin its
    # value column to a never-null sentinel so the kernel scores it, not skips it.
    for _i, _f in enumerate(mk.fields):
        if _f.scorer == "record_embedding":
            field_values[_i] = [""] * n
    if use_handle:
        opt_kwargs["exclude_set"] = exclude_handle
    _t0 = time.perf_counter() if _dbg_entry else 0.0
    pairs = mod.score_block_pairs_fs(
        row_ids, [int(s) for s in size_list], field_values, scorer_ids, levels,
        partials, weights, calibrated, prior_w, min_weight, weight_range,
        link_threshold, excl,
        **opt_kwargs,
    )
    _t1 = time.perf_counter() if _dbg_entry else 0.0
    out = [(a, b, round(float(s), 4)) for a, b, s in pairs]
    if _dbg_entry:
        _fs_native_dbg_record(
            _t0 - _t_entry, _t1 - _t0, time.perf_counter() - _t1,
            len(out), len(size_list) == 1,
        )
    return out


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
    same negative-evidence firing rule + fired weights (EM-learned
    ``__ne__<field>`` entries or the ``penalty_bits`` override), same
    calibration + threshold. The kernel replaces the per-field numpy NxN
    matrices with a single GIL-released per-pair Rust loop. Caller gates on
    ``_fs_native_eligible``. Thin wrapper over ``_score_fs_native_frame`` with a
    single-block ``size_list = [n]`` (the batched bucket path shares the prep).
    """
    from goldenmatch.core.frame import to_frame as _tf_w6

    n = _tf_w6(block_df).height
    return _score_fs_native_frame(block_df, [n], mk, em_result, exclude_pairs)


def score_probabilistic_bucket_native(
    sorted_bucket_df,
    size_list,
    mk: MatchkeyConfig,
    em_result: EMResult,
    exclude_pairs: set[tuple[int, int]] | None = None,
    exclude_handle=None,
) -> list[tuple[int, int, float]]:
    """Score a WHOLE block-sorted bucket in ONE native FS kernel call.

    The FS analog of the weighted bucket fast path (``score_block_pairs_arrow``
    over a whole bucket): instead of one ``score_probabilistic_native`` call per
    block, hand the kernel the block-sorted bucket plus its per-block run-length
    ``size_list`` and let it isolate blocks by the sizes list (the same block
    isolation the single-block path gets from ``[n]``).

    Precondition: ``sorted_bucket_df`` is already sorted by ``__block_key__`` and
    ``size_list`` is the run-length list of block sizes over it (blocks are
    contiguous, in order). Caller guarantees ``_fs_native_eligible(mk)`` and
    ``_fs_native_enabled()`` and has already applied the oversized / size<2 keep
    mask (so every block in ``size_list`` is scoreable).

    **Byte-parity:** for the same ``sorted_bucket_df`` + ``size_list``, the output
    equals concatenating ``score_probabilistic_native(block_slice, ...)`` over
    each block — identical values, identical 4dp rounding — because the kernel
    only compares within-block pairs (``score.rs`` spans) and both paths feed the
    same transformed per-field values. Asserted in
    ``tests/test_fs_bucket_native.py``.
    """
    return _score_fs_native_frame(
        sorted_bucket_df, size_list, mk, em_result, exclude_pairs,
        exclude_handle=exclude_handle,
    )


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

    # Model-backed scorers can ONLY run on the vectorized matrix (scalar
    # ``score_field`` raises ``Unknown scorer``), so they force the vectorized
    # path regardless of the ``GOLDENMATCH_FS_VECTORIZED=0`` debug knob — the
    # knob only downgrades scalar-capable configs.
    requires_vec = any(f.scorer in _MODEL_BACKED_SCORERS for f in mk.fields)
    use_vec = _fs_vectorized_supported(mk) and (
        _fs_vectorized_enabled() or requires_vec
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

    pair_min_weight, pair_max_weight = _fs_ne_weight_range(em_result, mk)

    total_weight = 0.0
    has_regular_evidence = False
    for k, f in enumerate(mk.fields):
        weights = em_result.match_weights[f.field]
        # #1854 full-range normalization: every field widens the min-max range
        # (before the observed guard) so minimal evidence can't saturate to 1.0.
        pair_min_weight += min(weights)
        pair_max_weight += max(weights)
        if vec[k] < 0:
            continue
        has_regular_evidence = True
        total_weight += weights[vec[k]]
        if getattr(f, "tf_adjustment", False):
            total_weight += _scalar_tf_contribution(
                _transform_field_value(row_a.get(f.field), f),
                _transform_field_value(row_b.get(f.field), f),
                vec[k], f, em_result,
            )
    for ne in (mk.negative_evidence or []):
        total_weight += _ne_scalar_contribution(row_a, row_b, ne, em_result)

    if _fs_calibration_mode() == "posterior":
        return posterior_from_weight(total_weight, prior_weight(em_result.proportion_matched))
    if not has_regular_evidence and total_weight == 0.0:
        return 0.5
    if pair_max_weight > pair_min_weight:
        # Clip into [0, 1] so a TF/NE overshoot can't leave the score contract.
        return min(1.0, max(0.0, (
            total_weight - pair_min_weight
        ) / (pair_max_weight - pair_min_weight)))
    return 0.5


# ── FS explainability: match-weight waterfall (Phase 2) ─────────────────────


@dataclass
class FSFieldContribution:
    """One field's contribution to a Fellegi-Sunter pair score."""

    field: str
    scorer: str
    value_a: str | None
    value_b: str | None
    level: int            # -1=unobserved; else 0=disagree .. n_levels-1=exact
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
        observed = level >= 0
        wbits = float(weights[level]) if observed and level < len(weights) else 0.0
        m_list = em_result.m_probs.get(f.field, [])
        u_list = em_result.u_probs.get(f.field, [])
        m = float(m_list[level]) if observed and level < len(m_list) else float("nan")
        u = float(u_list[level]) if observed and level < len(u_list) else float("nan")
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
