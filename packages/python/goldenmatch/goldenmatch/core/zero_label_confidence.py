"""Zero-label (ZeroER-inspired) confidence scoring for auto-config.

Computes a :class:`ZeroLabelConfidenceProfile` from an already-assembled
:class:`ComplexityProfile` — deterministically, from emitted aggregates only
(no labels, no extra data scans). Design doc:
``docs/design/2026-05-25-zero-label-confidence-autoconfig-design.md``.

Phase 1 (this module): the directly-derivable signals + a combined
``overall_confidence`` with anti-degeneracy guards. Phase-2 signals
(``cluster_bridge_risk``, ``perturbation_stability``, expected precision/recall
proxies) stay ``None`` until their instrumentation / extra-run machinery lands.

Honesty note: this is a lightweight, dependency-free *approximation* of ZeroER
(which is a GMM+EM over similarity vectors). For a truer latent-separation
estimate, the existing Fellegi-Sunter EM (``core/probabilistic.py``) is the
closer cousin — see the design doc's open questions.
"""
from __future__ import annotations

import math
from typing import Any

from goldenmatch.core.complexity_profile import (
    ClusterProfile,
    ComplexityProfile,
    ScoringProfile,
    ZeroLabelConfidenceProfile,
)

# Tunable combine constants (documented; the optimizer may override the blend).
_DIP_NORM = 0.1  # dip_statistic scale (matches ComplexityProfile.normalized_signal_vector)
_EVERYTHING_MATCHES_THRESHOLD = 0.9  # mirrors RunHistory.precision_collapse_floor


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_distribution_confidence(scoring: ScoringProfile) -> dict[str, float]:
    """Histogram-derived separation signals.

    Returns ``latent_separation``, ``distribution_overlap``, ``score_entropy``,
    ``bimodality_or_dip_score`` (all in [0, 1]).
    """
    hist = list(scoring.score_histogram)
    nbins = len(hist) or 1
    total = sum(hist)
    if total <= 0:
        return {
            "latent_separation": 0.0,
            "distribution_overlap": _clamp(scoring.mass_in_borderline),
            "score_entropy": 0.0,
            "bimodality_or_dip_score": _clamp(scoring.dip_statistic / _DIP_NORM),
        }

    pmf = [c / total for c in hist]
    # Normalized Shannon entropy (1 = uniform/uninformative, 0 = concentrated).
    ent = -sum(p * math.log(p) for p in pmf if p > 0)
    score_entropy = _clamp(ent / math.log(nbins)) if nbins > 1 else 0.0
    bimodality = _clamp(scoring.dip_statistic / _DIP_NORM)
    distribution_overlap = _clamp(scoring.mass_in_borderline)

    # latent_separation: two-mode centroid distance. Split at the interior
    # valley (min-count bin), take the weighted-mean bin of the lower vs upper
    # mass, normalize the gap, and down-weight when one mode is a sliver (a tiny
    # high-score mode on top of a big low-score mass is suspicious, not "clean").
    if nbins >= 3:
        interior = hist[1:-1]
        split = min(range(len(interior)), key=lambda i: interior[i]) + 1
    else:
        split = nbins // 2
    lower, upper = hist[:split], hist[split:]
    lo_mass, hi_mass = sum(lower), sum(upper)
    if lo_mass == 0 or hi_mass == 0:
        latent_separation = 0.0
    else:
        c_lo = sum(i * c for i, c in enumerate(lower)) / lo_mass
        c_hi = sum((split + i) * c for i, c in enumerate(upper)) / hi_mass
        gap = (c_hi - c_lo) / (nbins - 1)
        balance = min(lo_mass, hi_mass) / max(lo_mass, hi_mass)
        latent_separation = _clamp(gap * (0.5 + 0.5 * balance))

    return {
        "latent_separation": latent_separation,
        "distribution_overlap": distribution_overlap,
        "score_entropy": score_entropy,
        "bimodality_or_dip_score": bimodality,
    }


def score_random_pair_contamination(scoring: ScoringProfile) -> tuple[float, str | None]:
    """Fraction of random non-blocked pairs scoring as matches.

    When the probe wasn't run (``None``), returns ``0.0`` + a reason and is
    treated as neutral (never penalizes a config for a missing signal).
    """
    rate = scoring.random_pair_above_threshold_rate
    if rate is None:
        return 0.0, "random-pair contamination signal unavailable (probe not run)"
    return _clamp(rate), None


def score_transitivity(cluster: ClusterProfile) -> float:
    return _clamp(cluster.transitivity_rate)


def score_cluster_confidence(cluster: ClusterProfile, n_rows: int) -> float:
    """``cluster_size_risk`` — oversized + giant-cluster risk (higher = worse)."""
    n_rows = max(n_rows, 1)
    giant = _clamp(cluster.cluster_size_max / n_rows)
    oversized = _clamp(cluster.oversized_cluster_count / max(cluster.n_clusters, 1))
    return _clamp(0.5 * giant + 0.5 * oversized)


def combine_zero_label_scores(
    dist: dict[str, float],
    contamination: float,
    transitive_coherence: float,
    cluster_size_risk: float,
    scoring: ScoringProfile,
    cluster: ClusterProfile,
    n_rows: int,
    base_reasons: list[str],
) -> tuple[float, list[str]]:
    """Blend the signals into ``overall_confidence`` + collect drivers/guards."""
    reasons = list(base_reasons)
    conf = _clamp(
        0.30 * dist["latent_separation"]
        + 0.20 * dist["bimodality_or_dip_score"]
        + 0.15 * (1.0 - dist["distribution_overlap"])
        + 0.15 * (1.0 - contamination)
        + 0.10 * transitive_coherence
        + 0.10 * (1.0 - cluster_size_risk)
    )

    if dist["latent_separation"] >= 0.6:
        reasons.append(f"clean latent separation ({dist['latent_separation']:.2f})")
    if dist["distribution_overlap"] > 0.3:
        reasons.append(f"high borderline overlap ({dist['distribution_overlap']:.2f})")
    if contamination > 0.1:
        reasons.append(f"random-pair contamination ({contamination:.2f})")
    if cluster_size_risk > 0.3:
        reasons.append(f"oversized/giant clusters (risk {cluster_size_risk:.2f})")
    if transitive_coherence < 0.7:
        reasons.append(f"low transitivity ({transitive_coherence:.2f})")

    # Anti-degeneracy guards (hard caps) — never reward a degenerate config that
    # merely "looks separated".
    if scoring.mass_above_threshold >= _EVERYTHING_MATCHES_THRESHOLD:
        conf = min(conf, 0.2)
        reasons.append(
            f"everything-matches guard: mass_above_threshold={scoring.mass_above_threshold:.2f}"
        )
    if scoring.mass_above_threshold == 0.0 and scoring.n_pairs_scored > 0:
        conf = min(conf, 0.1)
        reasons.append("no-matches: nothing reached threshold")
    if n_rows > 2 and cluster.n_clusters <= 1:
        conf = min(conf, 0.2)
        reasons.append("cluster collapse: <=1 cluster")

    return _clamp(conf), reasons


def compute_zero_label_confidence(
    profile: ComplexityProfile,
    config: Any = None,
    history: Any = None,
) -> ZeroLabelConfidenceProfile:
    """Deterministic zero-label confidence from an assembled ComplexityProfile.

    ``config`` / ``history`` are accepted for forward compatibility (the
    optimizer + Phase-2 perturbation pass them); Phase 1 reads only ``profile``.
    """
    scoring = profile.scoring
    cluster = profile.cluster
    n_rows = profile.data.n_rows

    dist = score_distribution_confidence(scoring)
    reasons: list[str] = []
    if sum(scoring.score_histogram) == 0:
        reasons.append("no pairs scored")
    contamination, contam_reason = score_random_pair_contamination(scoring)
    if contam_reason:
        reasons.append(contam_reason)
    transitive = score_transitivity(cluster)
    size_risk = score_cluster_confidence(cluster, n_rows)

    overall, reasons = combine_zero_label_scores(
        dist, contamination, transitive, size_risk, scoring, cluster, n_rows, reasons,
    )
    return ZeroLabelConfidenceProfile(
        latent_separation=dist["latent_separation"],
        distribution_overlap=dist["distribution_overlap"],
        score_entropy=dist["score_entropy"],
        bimodality_or_dip_score=dist["bimodality_or_dip_score"],
        random_pair_contamination=contamination,
        transitive_coherence=transitive,
        cluster_size_risk=size_risk,
        overall_confidence=overall,
        confidence_reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# Phase 2: perturbation stability (pure helpers; the re-run orchestration lives
# in AutoConfigController, which owns the sample + pipeline). Env-gated via
# GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_STABILITY.
# ---------------------------------------------------------------------------

_PERTURB_DELTAS = (0.05, -0.05)


def threshold_perturbations(config: Any, deltas: tuple[float, ...] = _PERTURB_DELTAS) -> list[Any]:
    """Deep-copied config variants with each weighted/probabilistic matchkey
    threshold shifted by ``±delta`` (clamped to [0, 1]). Returns only variants
    that actually changed a threshold; empty when nothing is perturbable
    (e.g. an exact-only config)."""
    variants: list[Any] = []
    for delta in deltas:
        cfg = config.model_copy(deep=True)
        touched = False
        for mk in cfg.get_matchkeys():
            if getattr(mk, "type", None) in ("weighted", "probabilistic") and mk.threshold is not None:
                new_t = _clamp(mk.threshold + delta)
                if new_t != mk.threshold:
                    mk.threshold = new_t
                    touched = True
        if touched:
            variants.append(cfg)
    return variants


def profile_drift(base: ComplexityProfile, perturbed: ComplexityProfile) -> float:
    """Mean normalized drift in [0, 1] across cluster/score signals."""
    bc, pc = base.cluster, perturbed.cluster
    bs, ps = base.scoring, perturbed.scoring

    def _rel(a: float, b: float) -> float:
        return _clamp(abs(a - b) / max(abs(a), abs(b), 1.0))

    drifts = [
        _rel(bc.n_clusters, pc.n_clusters),
        _rel(bc.cluster_size_max, pc.cluster_size_max),
        _clamp(abs(bc.transitivity_rate - pc.transitivity_rate)),  # already 0..1
        _clamp(abs(bs.mass_above_threshold - ps.mass_above_threshold)),  # 0..1
    ]
    return _clamp(sum(drifts) / len(drifts))


def perturbation_stability(
    base: ComplexityProfile, perturbed_profiles: list[ComplexityProfile]
) -> float | None:
    """``1 - mean(drift)`` over the perturbed profiles, in [0, 1]. None when
    there were no usable perturbations (caller treats as 'not measured')."""
    if not perturbed_profiles:
        return None
    drifts = [profile_drift(base, p) for p in perturbed_profiles]
    return _clamp(1.0 - sum(drifts) / len(drifts))
