"""Collective entity resolution: neighborhood-similarity blend-and-iterate.

Pure / in-memory. graph_er.py does the I/O + per-entity ER and calls in here.
Spec: docs/superpowers/specs/2026-06-16-collective-er-deepening-design.md
"""
from __future__ import annotations

import math
from collections import defaultdict


def relational_similarity(
    n1: set,
    n2: set,
    *,
    mode: str = "jaccard",
    cluster_sizes: dict | None = None,
) -> float:
    """Overlap of two neighbor-cluster sets (each elem = (related_entity, cluster_id)).

    jaccard: |∩|/|∪|.
    adamic_adar: Σ_{c∈∩} 1/log(1+size(c)), needs cluster_sizes.
    """
    if not n1 or not n2:
        return 0.0
    inter = n1 & n2
    if not inter:
        return 0.0
    if mode == "adamic_adar":
        sizes = cluster_sizes or {}
        return sum(1.0 / math.log(1 + sizes.get(c, 2)) for c in inter)
    return len(inter) / len(n1 | n2)


def build_neighbor_index(groups) -> dict:
    """Build a co-occurrence neighbor graph from groups of co-members.

    Args:
        groups: Iterable of lists of ``(entity, record_id)`` tuples.  Each
            group represents a set of co-occurring members (e.g. the author
            members on a single paper).

    Returns:
        ``dict[(entity, record_id) -> set[(entity, record_id)]]`` where each
        member maps to the set of all co-members it appears with across every
        group.  Self-loops are excluded.
    """
    index: dict = defaultdict(set)
    for group in groups:
        members = list(group)
        for node in members:
            for other in members:
                if other != node:
                    index[node].add(other)
    return dict(index)


def neighbor_cluster_set(node, index: dict, clusters_by_entity: dict) -> set:
    """Map a node's neighbors to their current cluster ids.

    Args:
        node: ``(entity, record_id)`` tuple.
        index: Neighbor index from :func:`build_neighbor_index`.
        clusters_by_entity: ``dict[entity -> dict[record_id -> cluster_id]]``.

    Returns:
        ``set[(related_entity, cluster_id)]``.  Neighbors whose entity or
        record_id are absent from *clusters_by_entity* are silently skipped.
    """
    result: set = set()
    for neighbor_entity, neighbor_rid in index.get(node, set()):
        entity_map = clusters_by_entity.get(neighbor_entity)
        if entity_map is None:
            continue
        cid = entity_map.get(neighbor_rid)
        if cid is None:
            continue
        result.add((neighbor_entity, cid))
    return result
