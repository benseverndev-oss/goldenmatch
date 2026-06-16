"""Collective entity resolution: neighborhood-similarity blend-and-iterate.

Pure / in-memory. graph_er.py does the I/O + per-entity ER and calls in here.
Spec: docs/superpowers/specs/2026-06-16-collective-er-deepening-design.md
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict

_log = logging.getLogger("goldenmatch.collective")


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


def _partition_signature(rid_to_cid: dict) -> frozenset:
    """Relabeling-invariant signature of a ``{rid -> cid}`` clustering.

    Two clusterings are the same partition iff they group the same records
    together, regardless of the concrete cluster-id labels. Represent the
    partition as the set-of-frozensets of co-clustered record ids.
    """
    members_by_cid: dict = defaultdict(set)
    for rid, cid in rid_to_cid.items():
        members_by_cid[cid].add(rid)
    return frozenset(frozenset(members) for members in members_by_cid.values())


def _invert_clusters(clusters: dict) -> dict:
    """``{cid -> {"members": [...], ...}}`` -> ``{rid -> cid}``."""
    rid_to_cid: dict = {}
    for cid, cinfo in clusters.items():
        for rid in cinfo["members"]:
            rid_to_cid[rid] = cid
    return rid_to_cid


def _coneighbor_pairs(
    entity: str,
    ids: list,
    neighbor_index: dict,
    snapshot: dict,
    *,
    rel_mode: str,
    fanout_cap: int,
) -> set:
    """Co-neighbor candidate pairs: pairs of *entity* records that share >=1
    neighbor-cluster value under *snapshot*.

    For each neighbor-cluster value, collect the records whose
    ``neighbor_cluster_set`` (under the snapshot) contains it, then emit all
    within-bucket pairs. If a bucket would emit more than ``fanout_cap`` pairs,
    cap it: sort the records and take a bounded prefix so the emitted pair count
    stays under the cap, then log a one-line truncation warning.
    """
    # rid -> its neighbor-cluster set under the snapshot (computed once each).
    ncs_by_rid: dict = {
        rid: neighbor_cluster_set((entity, rid), neighbor_index, snapshot)
        for rid in ids
    }
    # Invert: neighbor-cluster value -> list of records carrying it.
    bucket: dict = defaultdict(list)
    for rid in ids:
        for value in ncs_by_rid[rid]:
            bucket[value].append(rid)

    pairs: set = set()
    for value, records in bucket.items():
        if len(records) < 2:
            continue
        recs = sorted(records)
        # Pairs from k records = k*(k-1)/2. Bound k so that stays <= fanout_cap.
        n_pairs = len(recs) * (len(recs) - 1) // 2
        if n_pairs > fanout_cap:
            # Largest k with k*(k-1)/2 <= fanout_cap.
            k = 1
            while (k + 1) * k // 2 <= fanout_cap:
                k += 1
            recs = recs[:k]
            _log.warning(
                "collective_resolve: fan-out cap %d hit for cluster %r "
                "(%d pairs > cap, using %d records)",
                fanout_cap, value, n_pairs, len(recs),
            )
        for i in range(len(recs)):
            for j in range(i + 1, len(recs)):
                a, b = recs[i], recs[j]
                pairs.add((a, b) if a <= b else (b, a))
    return pairs


def collective_resolve(
    entity_state: dict,
    neighbor_index: dict,
    *,
    alpha: float = 0.5,
    rel_mode: str = "jaccard",
    threshold: float = 0.5,
    max_iterations: int = 10,
    max_cluster_size: int = 100,
    fanout_cap: int = 200,
    stats: dict | None = None,
) -> dict:
    """Synchronous (Jacobi) blend-and-iterate collective ER fixpoint.

    Blends attribute similarity with relational (neighbor-cluster overlap)
    similarity and re-clusters each resolvable entity until the per-entity
    partitions stop changing.

    Args:
        entity_state: ``entity -> {"attr_pairs": [(a, b, score)], "ids": [int],
            "clusters": {rid -> cid}}``. An entity with non-empty ``attr_pairs``
            is *resolvable* (re-clustered each iteration); an entity with empty
            ``attr_pairs`` is a *fixed* reference neighbor (kept unchanged).

            WARNING: the ``"clusters"`` seed MUST be the attribute-ER output
            (a non-trivial partitioning), NOT all-singletons. With high ``alpha``,
            singleton seeds give ``rel_sim=0`` for mutual-only neighbors and the
            fixpoint silently under-merges — all relational signal is lost until
            at least some records share a non-singleton cluster in the snapshot.

        neighbor_index: ``(entity, rid) -> iterable[(related_entity, related_rid)]``
            from :func:`build_neighbor_index`.
        alpha: Weight on relational vs attribute similarity. ``blended =
            (1 - alpha) * attr + alpha * rel``.
        rel_mode: Mode passed to :func:`relational_similarity` (``"jaccard"`` /
            ``"adamic_adar"``).
        threshold: Keep a blended pair as an edge iff ``blended >= threshold``.
        max_iterations: Maximum Jacobi sweeps.
        max_cluster_size: Forwarded to :func:`build_clusters`.
        fanout_cap: Max co-neighbor candidate pairs generated per shared
            neighbor-cluster bucket.
        stats: Optional dict. When provided, populated with ``{"iterations": int,
            "converged": bool}`` reflecting the actual fixpoint outcome. Callers
            that do not need this metadata can omit the argument entirely.

    Returns:
        ``entity -> {rid -> cluster_id}`` (the final clustering per entity).
    """
    from goldenmatch.core.cluster import build_clusters

    clusters_by_entity: dict = {
        entity: dict(state["clusters"]) for entity, state in entity_state.items()
    }
    resolvable = [
        entity for entity, state in entity_state.items() if state["attr_pairs"]
    ]

    _actual_iterations = 0
    _converged = False
    for _iteration in range(max_iterations):
        _actual_iterations = _iteration + 1

        # (a) Snapshot: all scoring this sweep reads ONLY this frozen copy.
        snapshot: dict = {
            entity: dict(rid_to_cid)
            for entity, rid_to_cid in clusters_by_entity.items()
        }

        new_clusterings: dict = {}
        for entity in resolvable:
            state = entity_state[entity]
            ids = state["ids"]

            # attr_sim lookup keyed by canonical (min, max) pair.
            attr_sim: dict = {}
            for a, b, score in state["attr_pairs"]:
                attr_sim[(a, b) if a <= b else (b, a)] = score

            # (b) Candidate pairs = attr_pairs UNION co-neighbor pairs.
            candidates: set = set(attr_sim.keys())
            candidates |= _coneighbor_pairs(
                entity, ids, neighbor_index, snapshot,
                rel_mode=rel_mode, fanout_cap=fanout_cap,
            )

            # Score + keep edges that clear the blended threshold.
            kept_pairs: list = []
            for b1, b2 in candidates:
                attr = attr_sim.get((b1, b2), 0.0)
                rel = relational_similarity(
                    neighbor_cluster_set((entity, b1), neighbor_index, snapshot),
                    neighbor_cluster_set((entity, b2), neighbor_index, snapshot),
                    mode=rel_mode,
                )
                blended = (1 - alpha) * attr + alpha * rel
                if blended >= threshold:
                    kept_pairs.append((b1, b2, blended))

            # Re-cluster B; every id in state["ids"] appears (singletons too).
            clusters = build_clusters(
                kept_pairs, all_ids=list(ids), max_cluster_size=max_cluster_size,
            )
            new_clusterings[entity] = _invert_clusters(clusters)

        # (d) Jacobi swap: install all resolvable clusterings at once. Fixed
        #     entities keep their snapshot clusters (already in clusters_by_entity).
        # (e) Convergence: stop if no resolvable partition changed vs snapshot.
        changed = False
        for entity in resolvable:
            if _partition_signature(new_clusterings[entity]) != _partition_signature(
                snapshot[entity]
            ):
                changed = True
            clusters_by_entity[entity] = new_clusterings[entity]
        if not changed:
            _converged = True
            break

    if stats is not None:
        stats["iterations"] = _actual_iterations
        stats["converged"] = _converged

    return clusters_by_entity
