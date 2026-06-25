"""Unsupervised config-health proxy for the self-verification gate.

Used by ``review_config(verify=True)`` to filter suggestions that would worsen
the score distribution's separation between matches and non-matches -- without
any ground-truth labels.

Two complementary health functions
-----------------------------------
``suggestion_health(scored_pairs, threshold) -> float``
    Computes health from the scored-pairs list.  Works best when pairs include
    scores across the full [0, 1] range.  NOTE: ``_run_pipeline`` returns pairs
    FILTERED by threshold (only pairs >= threshold are emitted), so this function
    sees ``mass_above = 1.0`` by construction and always fires the
    precision-collapse penalty.  Use ``suggestion_health_from_clusters`` for
    the self-verify gate instead.

``suggestion_health_from_clusters(clusters, n_records) -> float``
    Computes health from cluster statistics.  This is the PRIMARY function used
    by the self-verification pass in ``review_config``.  It is threshold-agnostic
    and correctly detects recall collapse (too few matches) and merge collapse
    (everything merged into giant clusters).

    Formula:
        matched_rate = fraction of records that ended up in a multi-member cluster
        avg_conf     = mean cluster confidence over multi-member clusters
        collapse     = largest cluster covers > 50% of records  (merge pathology)
        health       = matched_rate * avg_conf - 0.5 * collapse

    A config that raises the threshold too aggressively drops true matches,
    reducing matched_rate.  A config that lowers it too aggressively over-merges,
    increasing matched_rate but reducing avg_conf AND triggering the collapse flag.

Reuse philosophy:
    The formula is extracted directly from the signals the controller already
    emits (ScoringProfile.mass_above_threshold, ScoringProfile.mass_in_borderline,
    RunHistory.pick_committed precision_collapse_floor).  No new measurement;
    we just compute the same signals from a raw scored-pairs list.
"""
from __future__ import annotations

import os

# Fraction of scored pairs within this band just below threshold = "borderline"
_BORDER_WIDTH: float = 0.10

# Precision-collapse floor (matches controller's precision_collapse_floor=0.9)
_COLLAPSE_FLOOR: float = 0.90

# Penalty applied when mass_above exceeds the collapse floor
_COLLAPSE_PENALTY: float = 0.50

# Concentration floor for the cluster-based health proxy (Herfindahl index).
# Below this HHI the clustering is considered well-distributed (no penalty);
# above it the graded merge-collapse penalty ramps in.  0.25 means: a single
# cluster holding ~half the records (HHI 0.25) is the point where over-merge
# concern begins; the two-equal-50%-clusters case (HHI 0.50) lands solidly in
# the penalized band; many small clusters (HHI -> 0) pay nothing.
_COLLAPSE_FLOOR_CLUSTER: float = 0.25


def suggestion_health(
    scored_pairs: list[tuple[int, int, float]],
    threshold: float,
) -> float:
    """Unsupervised config-health proxy.

    Higher is healthier.  Returns a float in roughly [-1.5, +1.0].

    Args:
        scored_pairs: List of (id_a, id_b, score) tuples from the pipeline.
            Score is in [0, 1].
        threshold: The matchkey threshold used for this run (the dividing line
            between matches and non-matches).

    Returns:
        float: health score.  Typical healthy range is [0.2, 1.0]; typical
        degenerate range is [-1.5, 0.0].

    Notes:
        Runs in O(N) over scored_pairs with no extra allocation beyond a
        counter loop.  Cost is negligible vs the pipeline run itself.
    """
    n = len(scored_pairs)
    if n == 0:
        # No scored pairs -> worst possible (no discrimination possible)
        return -1.0

    border_lo = threshold - _BORDER_WIDTH

    above = 0
    border = 0
    for _, _, score in scored_pairs:
        if score >= threshold:
            above += 1
        elif score >= border_lo:
            border += 1

    mass_above = above / n
    mass_border = border / n
    mass_sep = mass_above - mass_border

    # Precision-collapse penalty: same guard as the controller's
    # RunHistory.pick_committed(precision_collapse_floor=0.9) demotion
    pathology = 1.0 if mass_above > _COLLAPSE_FLOOR else 0.0
    return mass_sep - _COLLAPSE_PENALTY * pathology


def _health_legacy(
    clusters: dict,
    n_records: int,
) -> float:
    """Unsupervised health proxy computed from cluster statistics.

    This is the PRIMARY function used by ``review_config``'s self-verification
    pass.  Unlike ``suggestion_health``, it does NOT rely on scored pairs and is
    therefore immune to the threshold-filtering issue (``_run_pipeline`` returns
    only pairs >= threshold, so ``mass_above`` is always 1.0 in the pairs list).

    Basis: ``matched_rate * avg_conf - concentration_penalty``.

        matched_rate = (records in multi-member clusters) / n_records
        avg_conf     = mean confidence over multi-member clusters
        hhi          = Herfindahl concentration = sum((size / n_records)**2)
                       over multi-member clusters  (a few giant clusters => high)
        penalty      = COLLAPSE_PENALTY * clamp01((hhi - FLOOR) / (1 - FLOOR))
        health       = matched_rate * avg_conf - penalty

    Why HHI (concentration) and not max-cluster-size: a single-max check
    (``max_size / n_records > 0.5``) misses over-merge SPREAD across a few big
    clusters -- e.g. two clusters each at 50% of records (a degenerate
    over-merge) keeps ``max_size`` at exactly 0.5 and would slip the gate.  HHI
    sums the squared mass of EVERY cluster, so the two-50% case scores
    ``0.5^2 + 0.5^2 = 0.5`` and is penalized just like one 71%-cluster
    (``0.71^2 ~= 0.5``).  The penalty is GRADED (continuous) above ``FLOOR``
    rather than a hard flag, so a borderline concentration degrades smoothly
    instead of snapping.  A healthy frame of many small clusters has
    ``hhi -> 0`` and pays no penalty.

    Interpretation:
        - A threshold raise that drops true matches lowers ``matched_rate``.
        - A threshold lower that over-merges raises ``hhi`` (and usually lowers
          ``avg_conf``), so the penalty grows.
        - Returns -1.0 when ``n_records == 0`` (degenerate).
        - Returns 0.0 when no multi-member clusters exist (no matches found).

    Known limitation: this is an UNSUPERVISED proxy, NOT F1.  It cannot see
    ground truth -- it rewards "confident, non-degenerate clustering" and
    penalizes recall collapse and merge collapse, which empirically tracks the
    direction of F1 on already-healthy configs (the self-verify use case) but
    is not a substitute for a labeled metric.

    Args:
        clusters: Dict mapping cluster_id -> cluster_info dict (as returned by
            ``EngineResult.clusters``).  Each info dict must have at least
            ``"size"`` and optionally ``"confidence"`` and ``"members"``.
        n_records: Total number of records in the frame (used to compute rates).

    Returns:
        float: health score in roughly [-0.5, 1.0].
    """
    if n_records == 0:
        return -1.0

    multi_member = [
        info
        for info in clusters.values()
        if info.get("size", 1) > 1 and not info.get("oversized", False)
    ]

    if not multi_member:
        # No multi-member clusters: nothing matched.  Score of 0 (not -1)
        # because a config that legitimately matches nothing in a dataset with
        # no true duplicates IS healthy.  The relative comparison in the
        # verify gate handles this: if baseline is also 0, tie => keep.
        return 0.0

    n_matched = sum(info.get("size", 2) for info in multi_member)
    matched_rate = n_matched / n_records

    # Average confidence (default 0.5 when not present)
    avg_conf = sum(
        float(info.get("confidence", 0.5)) for info in multi_member
    ) / len(multi_member)

    # Concentration pathology via Herfindahl index over multi-member clusters.
    # Catches over-merge spread across several big clusters, not just one.
    hhi = sum((info.get("size", 2) / n_records) ** 2 for info in multi_member)

    # Graded penalty above the floor: 0 at hhi <= FLOOR, ramps to the full
    # COLLAPSE_PENALTY at hhi == 1.0 (one cluster == all records).
    over = (hhi - _COLLAPSE_FLOOR_CLUSTER) / (1.0 - _COLLAPSE_FLOOR_CLUSTER)
    concentration = min(1.0, max(0.0, over))
    penalty = _COLLAPSE_PENALTY * concentration

    return matched_rate * avg_conf - penalty


def suggestion_health_from_clusters(clusters: dict, n_records: int) -> float:
    """Public health proxy used by review_config's verify gate. Env-selectable:
    GOLDENMATCH_SUGGEST_HEALTH = 'legacy' (default, the shipped formula) or
    'cohesion' (the precision-sensitive redesign). Default keeps PR #1267
    behavior byte-identical."""
    mode = os.environ.get("GOLDENMATCH_SUGGEST_HEALTH", "legacy").strip().lower()
    if mode == "cohesion":
        return suggestion_health_cohesion(clusters, n_records)
    return _health_legacy(clusters, n_records)


def _cluster_min_edges(clusters: dict) -> list[float]:
    """Per-cluster weakest intra-cluster edge, over all multi-member clusters
    (INCLUDING oversized -- an oversized cluster is over-merge we must penalise)."""
    out: list[float] = []
    for info in clusters.values():
        if info.get("size", 1) <= 1:
            continue
        ps = info.get("pair_scores") or {}
        if isinstance(ps, dict) and ps:
            out.append(min(ps.values()))
        else:
            out.append(float(info.get("confidence", 0.5)))
    return out


def _cohesion_min(clusters: dict) -> float:
    edges = _cluster_min_edges(clusters)
    return min(edges) if edges else 0.0


def _cohesion_mean_bottomk(clusters: dict, k: int = 5) -> float:
    edges = sorted(_cluster_min_edges(clusters))
    if not edges:
        return 0.0
    bottom = edges[: max(1, min(k, len(edges)))]
    return sum(bottom) / len(bottom)


def _cohesion_edge_below_cutoff(clusters: dict, cutoff: float = 0.75) -> float:
    edges = _cluster_min_edges(clusters)
    if not edges:
        return 1.0  # no clusters -> no weak clusters (coverage handles recall collapse;
                    # NOTE: divergent default from _cohesion_min/_mean_bottomk which return 0.0)
    below = sum(1 for e in edges if e < cutoff)
    return 1.0 - below / len(edges)


# ---------------------------------------------------------------------------
# Task 2: cohesion x saturating-coverage proxy
# ---------------------------------------------------------------------------

_COVERAGE_CAP: float = 0.30
_COHESION_BOTTOMK: int = 5
_COHESION_CUTOFF: float = 0.75


def _coverage(clusters: dict, n_records: int) -> float:
    if n_records <= 0:
        return 0.0
    n_matched = sum(i.get("size", 2) for i in clusters.values() if i.get("size", 1) > 1)
    return min((n_matched / n_records) / _COVERAGE_CAP, 1.0)


def _select_cohesion(clusters: dict) -> float:
    which = os.environ.get("GOLDENMATCH_SUGGEST_COHESION", "min_edge").strip().lower()
    if which == "mean_bottomk_edge":
        return _cohesion_mean_bottomk(clusters, _COHESION_BOTTOMK)
    if which == "edge_below_cutoff_fraction":
        return _cohesion_edge_below_cutoff(clusters, _COHESION_CUTOFF)
    return _cohesion_min(clusters)


def suggestion_health_cohesion(clusters: dict, n_records: int) -> float:
    """Precision-sensitive health proxy: cohesion (low-tail intra-cluster edge)
    x saturating coverage. Cohesion statistic env-selectable
    (GOLDENMATCH_SUGGEST_COHESION); default min_edge. NO ground truth."""
    if n_records <= 0:
        return -1.0
    return _select_cohesion(clusters) * _coverage(clusters, n_records)


def _extract_threshold(config: object) -> float:
    """Extract the first weighted/exact matchkey threshold from a config.

    Returns 0.5 as a safe default when no threshold is found -- a mid-range
    value that works for both above/below evaluation.
    """
    try:
        for mk in config.get_matchkeys():  # type: ignore[attr-defined]
            t = getattr(mk, "threshold", None)
            if t is not None:
                return float(t)
    except Exception:
        pass
    return 0.5
