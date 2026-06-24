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

# Fraction of scored pairs within this band just below threshold = "borderline"
_BORDER_WIDTH: float = 0.10

# Precision-collapse floor (matches controller's precision_collapse_floor=0.9)
_COLLAPSE_FLOOR: float = 0.90

# Penalty applied when mass_above exceeds the collapse floor
_COLLAPSE_PENALTY: float = 0.50

# Collapse threshold for the cluster-based health proxy: a single cluster
# absorbing more than this fraction of all records signals a merge pathology.
_COLLAPSE_FLOOR_CLUSTER: float = 0.50


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


def suggestion_health_from_clusters(
    clusters: dict,
    n_records: int,
) -> float:
    """Unsupervised health proxy computed from cluster statistics.

    This is the PRIMARY function used by ``review_config``'s self-verification
    pass.  Unlike ``suggestion_health``, it does NOT rely on scored pairs and is
    therefore immune to the threshold-filtering issue (``_run_pipeline`` returns
    only pairs >= threshold, so ``mass_above`` is always 1.0 in the pairs list).

    Formula::

        matched_rate = (records in multi-member clusters) / n_records
        avg_conf     = mean confidence over multi-member clusters
        collapse     = (largest cluster size / n_records) > 0.50
        health       = matched_rate * avg_conf - 0.5 * collapse

    Interpretation:
        - A threshold raise that drops true matches lowers ``matched_rate``.
        - A threshold lower that causes over-merging lowers ``avg_conf`` and
          may trigger ``collapse``.
        - Returns -1.0 when ``n_records == 0`` (degenerate).
        - Returns 0.0 when no multi-member clusters exist (no matches found).

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

    # Collapse pathology: a single cluster absorbing > 50% of records
    max_size = max(info.get("size", 2) for info in multi_member)
    collapse = 1.0 if max_size / n_records > _COLLAPSE_FLOOR_CLUSTER else 0.0

    return matched_rate * avg_conf - _COLLAPSE_PENALTY * collapse


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
