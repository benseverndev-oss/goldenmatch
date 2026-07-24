"""Transitive-consistency cluster postflight (inspired by TransClean, 2506.04006).

GM forms clusters by connected components over the matched pairs, so a single
false-positive pair can transitively CHAIN two distinct entities into one cluster
(A-B real, B-C false, C-D real => {A,B,C,D}). Threshold calibration fixes pairs,
not transitive collapses. This postflight finds clusters held together by a WEAK
transitive bridge — a low-support cut edge whose removal splits the cluster into
two ≥2-node groups while the groups are internally more cohesive — and splits
them at that edge.

Reuses the in-house cluster primitives (no new deps): ``_severe_bridge_count``
(the bridge pathology), ``compute_cluster_confidence`` (weakest vs average edge),
``split_oversized_cluster`` (max-weight spanning tree, cut the weakest edge).

Gated by ``GOLDENMATCH_TRANSITIVE_POSTFLIGHT`` (default OFF -> no-op ->
byte-identical). Only touches clusters that show the pathology; everything else
passes through unchanged.
"""
from __future__ import annotations

import os

from goldenmatch.core.cluster import (
    _severe_bridge_count,
    compute_cluster_confidence,
    split_oversized_cluster,
)

# Bridge detection is O(E·(V+E)); skip clusters above this (mirrors
# _BRIDGE_MAX_CLUSTER_SIZE in cluster.py). A severe 2+2 split needs >= 4 nodes.
_TC_MAX_CLUSTER_SIZE = 100
_TC_MIN_CLUSTER_SIZE = 4
_TC_DEFAULT_MARGIN = 0.15


def _transitive_postflight_enabled() -> bool:
    """Whether the transitive-consistency cluster postflight runs. Default OFF."""
    return os.environ.get("GOLDENMATCH_TRANSITIVE_POSTFLIGHT", "0").lower() in (
        "1", "true", "on", "yes", "enabled",
    )


def _weak_bridge_margin() -> float:
    """How far below the average edge the weakest edge must sit to count as a weak
    bridge (``GOLDENMATCH_TRANSITIVE_WEAK_MARGIN``; default 0.15). Larger = more
    conservative (splits fewer clusters)."""
    v = os.environ.get("GOLDENMATCH_TRANSITIVE_WEAK_MARGIN")
    if v:
        try:
            return max(0.0, float(v))
        except ValueError:
            pass
    return _TC_DEFAULT_MARGIN


def _is_weak_transitive_bridge(members, pair_scores, margin) -> bool:
    """A cluster is a weak transitive bridge if it has a severe bridge (an edge
    whose removal leaves two ≥2-node groups) AND its weakest edge is materially
    below its average edge (cohesive groups joined by one weak link)."""
    conf = compute_cluster_confidence(pair_scores, len(members))
    min_e, avg_e = conf.get("min_edge"), conf.get("avg_edge")
    if min_e is None or avg_e is None:
        return False
    if (avg_e - min_e) < margin:
        return False
    return _severe_bridge_count(members, pair_scores) > 0


def split_weak_transitive_bridges(clusters: dict, margin: float | None = None):
    """Split clusters held together by a single weak transitive bridge.

    ``clusters`` is ``{cid: {"members", "pair_scores", ...}}`` (the dict-path
    shape; pair_scores is required — columnar/frames clusters without it pass
    through untouched). Returns ``(refined_clusters, report)``.
    """
    if margin is None:
        margin = _weak_bridge_margin()
    refined: dict = {}
    next_cid = (max(clusters) + 1) if clusters else 0
    n_examined = n_split = n_new = 0
    for cid, cinfo in clusters.items():
        members = list(cinfo.get("members") or [])
        pair_scores = cinfo.get("pair_scores") or {}
        size = len(members)
        if size < _TC_MIN_CLUSTER_SIZE or size > _TC_MAX_CLUSTER_SIZE or not pair_scores:
            refined[cid] = cinfo
            continue
        n_examined += 1
        if not _is_weak_transitive_bridge(members, pair_scores, margin):
            refined[cid] = cinfo
            continue
        subs = split_oversized_cluster(members, pair_scores)
        if len(subs) <= 1:
            refined[cid] = cinfo
            continue
        n_split += 1
        # First subcluster keeps the original cid (stable ids for the largest);
        # the rest get fresh cids. Recompute confidence/size on each.
        subs.sort(key=lambda s: len(s["members"]), reverse=True)
        for i, sub in enumerate(subs):
            m, ps = sub["members"], sub.get("pair_scores", {})
            conf = compute_cluster_confidence(ps, len(m))
            entry = {
                "members": sorted(m),
                "size": len(m),
                "oversized": False,
                "pair_scores": ps,
                "confidence": conf.get("confidence"),
                "bottleneck_pair": conf.get("bottleneck_pair"),
                "cluster_quality": "split_transitive",
            }
            if i == 0:
                refined[cid] = entry
            else:
                refined[next_cid] = entry
                next_cid += 1
                n_new += 1
    report = {
        "enabled": True,
        "clusters_examined": n_examined,
        "clusters_split": n_split,
        "new_clusters_created": n_new,
        "weak_bridge_margin": margin,
    }
    return refined, report


def materialize_and_split(clusters: dict, all_pairs, margin: float | None = None):
    """Path-independent entry: ensure each cluster has ``pair_scores`` (restored
    from the global scored-pair list when the cluster dict left them empty, e.g.
    the columnar path), then split weak transitive bridges. ``all_pairs`` is the
    global ``list[(a, b, score)]``. Returns ``(refined_clusters, report)``."""
    needs_fill = any(not (c.get("pair_scores")) and len(c.get("members") or []) >= _TC_MIN_CLUSTER_SIZE
                     for c in clusters.values())
    if needs_fill and all_pairs:
        member_of: dict[int, int] = {}
        for cid, c in clusters.items():
            for m in (c.get("members") or []):
                member_of[m] = cid
        filled: dict[int, dict] = {}
        for a, b, s in all_pairs:
            ca, cb = member_of.get(a), member_of.get(b)
            if ca is not None and ca == cb:
                key = (a, b) if a < b else (b, a)
                filled.setdefault(ca, {})[key] = s
        clusters = {
            cid: ({**c, "pair_scores": filled.get(cid, c.get("pair_scores") or {})}
                  if not c.get("pair_scores") else c)
            for cid, c in clusters.items()
        }
    return split_weak_transitive_bridges(clusters, margin=margin)
