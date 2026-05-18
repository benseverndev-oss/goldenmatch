"""Union-Find clustering for GoldenMatch."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from goldenmatch.core._profile_helpers import transitivity_rate
from goldenmatch.core.complexity_profile import ClusterProfile
from goldenmatch.core.profile_emitter import _emitter_stack, current_emitter

if TYPE_CHECKING:
    from goldenmatch.core.memory.store import MemoryStore

_log = logging.getLogger("goldenmatch.memory")


def _record_unmerge_corrections(
    pairs: list[tuple[int, int]],
    memory_store: MemoryStore | None,
    dataset: str | None,
) -> None:
    """Write reject corrections with empty hashes for each unmerged pair."""
    if memory_store is None or not pairs:
        return
    try:
        from goldenmatch.core.memory.store import Correction

        for a, b in pairs:
            memory_store.add_correction(Correction(
                id=str(uuid.uuid4()),
                id_a=a,
                id_b=b,
                decision="reject",
                source="unmerge",
                trust=1.0,
                field_hash="",
                record_hash="",
                original_score=0.0,
                matchkey_name=None,
                reason=None,
                dataset=dataset,
                created_at=datetime.now(),
            ))
    except Exception as e:
        _log.warning("Unmerge memory write failed: %s", e)


class UnionFind:
    """Union-Find (disjoint set) with path compression and union by rank."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}
        self._rank: dict[int, int] = {}

    def add(self, x: int) -> None:
        """Add an element as its own root."""
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0

    def add_many(self, ids: list[int]) -> None:
        """Add multiple elements at once, more efficient than individual add() calls."""
        parent = self._parent
        rank = self._rank
        for x in ids:
            if x not in parent:
                parent[x] = x
                rank[x] = 0

    def find(self, x: int) -> int:
        """Find the root of x with iterative path compression."""
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression: point all nodes on the path directly to root
        while self._parent[x] != root:
            next_x = self._parent[x]
            self._parent[x] = root
            x = next_x
        return root

    def union(self, a: int, b: int) -> None:
        """Union the sets containing a and b using union by rank."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def get_clusters(self) -> list[set[int]]:
        """Return all clusters as a list of sets."""
        groups: dict[int, set[int]] = defaultdict(set)
        for x in self._parent:
            groups[self.find(x)].add(x)
        return list(groups.values())


def _build_mst(
    members: list[int], pair_scores: dict[tuple[int, int], float],
) -> list[tuple[int, int, float]]:
    """Build max-weight spanning tree using Kruskal's algorithm."""
    edges = [(a, b, s) for (a, b), s in pair_scores.items()]
    edges.sort(key=lambda e: e[2], reverse=True)
    uf = UnionFind()
    uf.add_many(members)
    mst: list[tuple[int, int, float]] = []
    for a, b, s in edges:
        if uf.find(a) != uf.find(b):
            uf.union(a, b)
            mst.append((a, b, s))
            if len(mst) == len(members) - 1:
                break
    return mst


def split_oversized_cluster(
    members: list[int], pair_scores: dict[tuple[int, int], float],
) -> list[dict]:
    """Split a cluster by removing the weakest MST edge."""
    if len(members) <= 1 or not pair_scores:
        return [{"members": sorted(members), "size": len(members),
                 "oversized": False, "pair_scores": pair_scores}]

    mst = _build_mst(members, pair_scores)
    if not mst:
        return [{"members": sorted(members), "size": len(members),
                 "oversized": False, "pair_scores": pair_scores}]

    weakest = min(mst, key=lambda e: e[2])
    remaining = [(a, b, s) for a, b, s in mst if (a, b, s) != weakest]

    uf = UnionFind()
    uf.add_many(members)
    for a, b, _s in remaining:
        uf.union(a, b)

    result = []
    for sc_members in uf.get_clusters():
        sc_list = sorted(sc_members)
        sc_pairs = {(a, b): s for (a, b), s in pair_scores.items()
                    if a in sc_members and b in sc_members}
        size = len(sc_list)
        conf = compute_cluster_confidence(sc_pairs, size)
        result.append({
            "members": sc_list, "size": size, "oversized": False,
            "pair_scores": sc_pairs, "confidence": conf["confidence"],
            "bottleneck_pair": conf["bottleneck_pair"],
        })
    return result


def _emit_cluster_profile(clusters: dict[int, dict]) -> None:
    """Emit ClusterProfile to current emitter. No-op when no capture is active."""
    import math
    if not _emitter_stack.get():
        return  # fast path: no capture active

    if not clusters:
        current_emitter().set_cluster(ClusterProfile())
        return

    sizes = sorted(c["size"] for c in clusters.values())

    def percentile(xs: list, q: float) -> int:
        if not xs:
            return 0
        idx = max(0, min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1))
        return xs[idx]

    confidences = sorted(
        c["confidence"] for c in clusters.values()
        if c.get("confidence") is not None
    )

    members_by_cluster = {cid: c["members"] for cid, c in clusters.items()}

    # Aggregate pair_scores across clusters
    aggregated_scores: dict[tuple[int, int], float] = {}
    for c in clusters.values():
        for k, v in c.get("pair_scores", {}).items():
            if isinstance(k, tuple) and len(k) == 2:
                a, b = k
                aggregated_scores[(min(a, b), max(a, b))] = v

    # Threshold proxy: minimum observed pair score (any pair already passed the
    # matchkey threshold, so the min is the effective formation floor).
    if aggregated_scores:
        threshold = min(aggregated_scores.values())
    else:
        threshold = 0.5  # fallback

    profile = ClusterProfile(
        n_clusters=len(clusters),
        cluster_size_p50=percentile(sizes, 0.50),
        cluster_size_p99=percentile(sizes, 0.99),
        cluster_size_max=sizes[-1] if sizes else 0,
        transitivity_rate=transitivity_rate(members_by_cluster, aggregated_scores, threshold),
        edge_confidence_p50=confidences[len(confidences) // 2] if confidences else 0.0,
        edge_confidence_min=confidences[0] if confidences else 0.0,
        oversized_cluster_count=sum(1 for c in clusters.values() if c.get("oversized")),
    )
    current_emitter().set_cluster(profile)


def build_clusters(
    pairs: list[tuple[int, int, float]],
    all_ids: list[int] | None = None,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    auto_split: bool = True,
) -> dict[int, dict]:
    """Build clusters from scored pairs.

    Auto-splits oversized clusters via MST (when auto_split=True). Assigns
    cluster_quality ("strong", "weak", "split") and downgrades confidence
    for weak clusters.

    Implementation: at 5M / 1.67M-pair scale the Python ``UnionFind`` union
    loop + per-cluster ``compute_cluster_confidence`` Python pass cost ~120s
    on a 16-core runner. This routes through scipy's
    ``connected_components`` (C-implemented) for labelling + numpy reduceat
    for vectorized confidence math. Smaller workloads fall through the same
    code path -- correctness is preserved; the Python work is just
    materializing the result dict structure.
    """
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    # Derive all_ids from pairs when not provided explicitly
    if all_ids is None:
        seen: set[int] = set()
        for id_a, id_b, _s in pairs:
            seen.add(id_a)
            seen.add(id_b)
        all_ids = list(seen)

    n = len(all_ids)
    if n == 0:
        _emit_cluster_profile({})
        return {}

    id_to_idx: dict[int, int] = {rid: i for i, rid in enumerate(all_ids)}

    if pairs:
        a_idx = np.fromiter(
            (id_to_idx[p[0]] for p in pairs), dtype=np.int64, count=len(pairs)
        )
        b_idx = np.fromiter(
            (id_to_idx[p[1]] for p in pairs), dtype=np.int64, count=len(pairs)
        )
        scores_arr = np.fromiter(
            (p[2] for p in pairs), dtype=np.float64, count=len(pairs)
        )
        data = np.ones(len(pairs), dtype=np.int8)
        adj = coo_matrix((data, (a_idx, b_idx)), shape=(n, n))
        _n_comp, labels = connected_components(adj, directed=False)
    else:
        labels = np.arange(n, dtype=np.int64)

    # Stable cluster_id ordering: sort components by min row_id-in-component.
    # Use a Polars groupby to gather members per component (fast vectorized
    # path) then re-key by 1-based cluster_id in min-row-id order.
    import polars as pl
    member_df = pl.DataFrame({
        "__row_id__": all_ids,
        "__component__": labels.tolist(),
    })
    comp_info = (
        member_df.lazy()
        .group_by("__component__")
        .agg([
            pl.col("__row_id__").alias("__members__"),
            pl.col("__row_id__").min().alias("__min_id__"),
        ])
        .sort("__min_id__")
        .collect()
    )
    comp_ids_in_order: list[int] = comp_info["__component__"].to_list()
    members_per_cluster: list[list[int]] = comp_info["__members__"].to_list()
    component_to_cid: dict[int, int] = {
        int(comp): cid for cid, comp in enumerate(comp_ids_in_order, start=1)
    }

    # Per-cluster confidence metrics, vectorized in numpy.
    cluster_metrics: dict[int, dict[str, float]] = {}
    if pairs:
        pair_cid_arr = np.fromiter(
            (component_to_cid[int(c)] for c in labels[a_idx]),
            dtype=np.int64, count=len(pairs),
        )
        sort_idx = np.argsort(pair_cid_arr, kind="stable")
        sorted_cids = pair_cid_arr[sort_idx]
        sorted_scores = scores_arr[sort_idx]
        unique_cids, start_idx, counts = np.unique(
            sorted_cids, return_index=True, return_counts=True,
        )
        # reduceat over the sorted score array gives per-cluster min / sum
        # in two C-level passes (replaces the Python compute_cluster_confidence
        # per-cluster loop, which was ~30s of the 120s wall at 5M).
        min_per_cluster = np.minimum.reduceat(sorted_scores, start_idx)
        sum_per_cluster = np.add.reduceat(sorted_scores, start_idx)
        avg_per_cluster = sum_per_cluster / counts
        cluster_metrics = {
            int(cid): {
                "min_edge": float(min_per_cluster[i]),
                "avg_edge": float(avg_per_cluster[i]),
                "n_edges": int(counts[i]),
            }
            for i, cid in enumerate(unique_cids)
        }

    # Build pair_scores dict per cluster (still per-pair Python — output
    # shape is dict[int, dict[tuple, float]] which only Python can populate).
    pair_scores_per_cluster: dict[int, dict[tuple[int, int], float]] = {}
    if pairs:
        for i, (id_a, id_b, score) in enumerate(pairs):
            cid = component_to_cid[int(labels[a_idx[i]])]
            d = pair_scores_per_cluster.get(cid)
            if d is None:
                d = {}
                pair_scores_per_cluster[cid] = d
            d[(id_a, id_b)] = score

    # Assemble result. One Python pass over 3.3M clusters with pre-computed
    # confidence; no Python computation inside the loop body.
    result: dict[int, dict] = {}
    for cluster_id, members in enumerate(members_per_cluster, start=1):
        members_sorted = sorted(members)
        size = len(members_sorted)
        ps = pair_scores_per_cluster.get(cluster_id, {})
        if size <= 1 or not ps:
            confidence = 1.0 if size <= 1 else 0.0
            bottleneck_pair = None
        else:
            metrics = cluster_metrics[cluster_id]
            min_edge = metrics["min_edge"]
            avg_edge = metrics["avg_edge"]
            n_edges = metrics["n_edges"]
            max_possible = size * (size - 1) / 2
            connectivity = n_edges / max_possible if max_possible > 0 else 0.0
            confidence = 0.4 * min_edge + 0.3 * avg_edge + 0.3 * connectivity
            bottleneck_pair = min(ps, key=ps.__getitem__)
        result[cluster_id] = {
            "members": members_sorted,
            "size": size,
            "oversized": size > max_cluster_size,
            "pair_scores": ps,
            "confidence": confidence,
            "bottleneck_pair": bottleneck_pair,
        }

    # Auto-split oversized clusters (when enabled)
    to_split = [cid for cid, c in result.items() if c["oversized"]] if auto_split else []
    while to_split:
        cid = to_split.pop()
        cinfo = result.pop(cid)
        sub_clusters = split_oversized_cluster(cinfo["members"], cinfo["pair_scores"])
        next_cid = max(result.keys(), default=0) + 1
        for sc in sub_clusters:
            sc["oversized"] = sc["size"] > max_cluster_size
            sc["_was_split"] = True
            result[next_cid] = sc
            if sc["oversized"]:
                to_split.append(next_cid)
            next_cid += 1

    # Assign cluster_quality and apply confidence downgrade
    for cid, cinfo in result.items():
        if cinfo.get("_was_split"):
            cinfo["cluster_quality"] = "split"
        elif cinfo["size"] > 1 and cinfo.get("pair_scores"):
            scores = list(cinfo["pair_scores"].values())
            min_edge = min(scores)
            avg_edge = sum(scores) / len(scores)
            if avg_edge - min_edge > weak_cluster_threshold:
                cinfo["cluster_quality"] = "weak"
                cinfo["confidence"] *= 0.7
            else:
                cinfo["cluster_quality"] = "strong"
        else:
            cinfo["cluster_quality"] = "strong"
        cinfo.pop("_was_split", None)

    _emit_cluster_profile(result)
    return result


def compute_cluster_confidence(
    pair_scores: dict[tuple[int, int], float],
    size: int,
) -> dict:
    """Compute confidence metrics for a cluster.

    Confidence captures how strongly a cluster is connected. A chain
    held together by a single weak link gets low confidence. A fully
    connected cluster with high scores gets high confidence.

    Args:
        pair_scores: Dict of (id_a, id_b) -> score for this cluster.
        size: Number of members in the cluster.

    Returns:
        Dict with: min_edge, avg_edge, connectivity, bottleneck_pair, confidence.
    """
    if size <= 1 or not pair_scores:
        return {
            "min_edge": None,
            "avg_edge": None,
            "connectivity": 1.0 if size <= 1 else 0.0,
            "bottleneck_pair": None,
            "confidence": 1.0 if size <= 1 else 0.0,
        }

    scores = list(pair_scores.values())
    min_edge = min(scores)
    avg_edge = sum(scores) / len(scores)
    max_possible_edges = size * (size - 1) / 2
    connectivity = len(pair_scores) / max_possible_edges if max_possible_edges > 0 else 0.0
    bottleneck_pair = min(pair_scores, key=lambda p: pair_scores[p])

    # Weighted confidence: weakest link matters most
    confidence = 0.4 * min_edge + 0.3 * avg_edge + 0.3 * connectivity

    return {
        "min_edge": min_edge,
        "avg_edge": avg_edge,
        "connectivity": connectivity,
        "bottleneck_pair": bottleneck_pair,
        "confidence": confidence,
    }


def add_to_cluster(
    record_id: int,
    matches: list[tuple[int, float]],
    clusters: dict[int, dict],
    max_cluster_size: int = 100,
) -> dict[int, dict]:
    """Add a new record to existing clusters based on matches.

    If the record matches members of a single cluster, it joins that cluster.
    If it matches members of multiple clusters, those clusters merge.
    If no matches, a new singleton cluster is created.

    Args:
        record_id: The new record's ID.
        matches: List of (matched_row_id, score) tuples.
        clusters: Current cluster dict (modified in-place).
        max_cluster_size: Threshold for oversized flagging.

    Returns:
        Updated clusters dict.

    Note:
        This function flags oversized clusters but does NOT auto-split them.
        Callers (e.g., StreamProcessor) should call split_oversized_cluster()
        after add_to_cluster() if auto-splitting is desired.
    """
    if not matches:
        next_cid = max(clusters.keys(), default=0) + 1
        clusters[next_cid] = {
            "members": [record_id],
            "size": 1,
            "oversized": False,
            "pair_scores": {},
            "confidence": 1.0,
            "bottleneck_pair": None,
            "cluster_quality": "strong",
        }
        return clusters

    # Find which cluster(s) the matched records belong to
    member_to_cid: dict[int, int] = {}
    for cid, cinfo in clusters.items():
        for m in cinfo["members"]:
            member_to_cid[m] = cid

    matched_cids = set()
    for matched_id, _score in matches:
        cid = member_to_cid.get(matched_id)
        if cid is not None:
            matched_cids.add(cid)

    if not matched_cids:
        next_cid = max(clusters.keys(), default=0) + 1
        clusters[next_cid] = {
            "members": [record_id],
            "size": 1,
            "oversized": False,
            "pair_scores": {},
            "confidence": 1.0,
            "bottleneck_pair": None,
            "cluster_quality": "strong",
        }
        return clusters

    if len(matched_cids) == 1:
        cid = matched_cids.pop()
        cinfo = clusters[cid]
        cinfo["members"] = sorted(cinfo["members"] + [record_id])
        cinfo["size"] += 1
        cinfo["oversized"] = cinfo["size"] > max_cluster_size
        for matched_id, score in matches:
            if member_to_cid.get(matched_id) == cid:
                cinfo["pair_scores"][(min(record_id, matched_id), max(record_id, matched_id))] = score
        conf = compute_cluster_confidence(cinfo["pair_scores"], cinfo["size"])
        cinfo["confidence"] = conf["confidence"]
        cinfo["bottleneck_pair"] = conf["bottleneck_pair"]
        cinfo["cluster_quality"] = cinfo.get("cluster_quality", "strong")
        return clusters

    # Multiple clusters — merge them all with the new record
    merged_members = [record_id]
    merged_pairs: dict[tuple[int, int], float] = {}

    for cid in matched_cids:
        cinfo = clusters[cid]
        merged_members.extend(cinfo["members"])
        merged_pairs.update(cinfo["pair_scores"])
        del clusters[cid]

    # Add new pair scores
    for matched_id, score in matches:
        merged_pairs[(min(record_id, matched_id), max(record_id, matched_id))] = score

    next_cid = max(clusters.keys(), default=0) + 1
    size = len(merged_members)
    conf = compute_cluster_confidence(merged_pairs, size)
    clusters[next_cid] = {
        "members": sorted(merged_members),
        "size": size,
        "oversized": size > max_cluster_size,
        "pair_scores": merged_pairs,
        "confidence": conf["confidence"],
        "bottleneck_pair": conf["bottleneck_pair"],
        "cluster_quality": "strong",
    }

    return clusters


def get_cluster_pair_scores(
    cluster_members: list[int],
    all_pairs: list[tuple[int, int, float]],
) -> dict[tuple[int, int], float]:
    """Get pair scores for a specific cluster. Call on-demand, not in hot path."""
    member_set = set(cluster_members)
    return {
        (a, b): s
        for a, b, s in all_pairs
        if a in member_set and b in member_set
    }


def unmerge_record(
    record_id: int,
    clusters: dict[int, dict],
    threshold: float = 0.0,
    *,
    memory_store: MemoryStore | None = None,
    dataset: str | None = None,
) -> dict[int, dict]:
    """Remove a record from its cluster and re-cluster remaining members.

    Uses stored pair_scores to re-build the cluster without the removed record.
    The removed record becomes a singleton. If the remaining members still form
    connected components above the threshold, they stay clustered.

    Args:
        record_id: The record ID to remove from its cluster.
        clusters: Full cluster dict (modified in-place and returned).
        threshold: Minimum score to keep a pair connection (default 0.0 = keep all).

    Returns:
        Updated clusters dict. The removed record is in its own singleton cluster.
    """
    # Find which cluster contains this record
    source_cid = None
    for cid, cinfo in clusters.items():
        if record_id in cinfo["members"]:
            source_cid = cid
            break

    if source_cid is None:
        return clusters  # Record not found in any cluster

    cinfo = clusters[source_cid]
    if cinfo["size"] <= 1:
        return clusters  # Already a singleton

    # Memory: reject correction for every pair (record_id, other) in this cluster.
    if memory_store is not None:
        unmerge_pairs: list[tuple[int, int]] = []
        for (a, b) in cinfo.get("pair_scores", {}).keys():
            if a == record_id and b != record_id:
                unmerge_pairs.append((a, b))
            elif b == record_id and a != record_id:
                unmerge_pairs.append((a, b))
        # Fall back to (record_id, other_member) if pair_scores missing this edge.
        if not unmerge_pairs:
            unmerge_pairs = [(record_id, m) for m in cinfo["members"] if m != record_id]
        _record_unmerge_corrections(unmerge_pairs, memory_store, dataset)

    # Extract pair_scores excluding the removed record
    remaining_members = [m for m in cinfo["members"] if m != record_id]
    remaining_pairs = [
        (a, b, s)
        for (a, b), s in cinfo["pair_scores"].items()
        if a != record_id and b != record_id and s >= threshold
    ]

    # Re-cluster the remaining members
    sub_clusters = build_clusters(remaining_pairs, remaining_members)

    # Remove the original cluster
    del clusters[source_cid]

    # Assign new cluster IDs (use max existing + 1)
    next_cid = max(clusters.keys(), default=0) + 1

    # Add the removed record as a singleton
    clusters[next_cid] = {
        "members": [record_id],
        "size": 1,
        "oversized": False,
        "pair_scores": {},
        "confidence": 1.0,
        "bottleneck_pair": None,
        "cluster_quality": "strong",
    }
    next_cid += 1

    # Add re-clustered groups
    for sub_cinfo in sub_clusters.values():
        clusters[next_cid] = sub_cinfo
        next_cid += 1

    return clusters


def unmerge_cluster(
    cluster_id: int,
    clusters: dict[int, dict],
    *,
    memory_store: MemoryStore | None = None,
    dataset: str | None = None,
) -> dict[int, dict]:
    """Shatter a cluster back into individual singletons.

    All members become their own cluster. Pair scores are discarded.

    Args:
        cluster_id: The cluster to shatter.
        clusters: Full cluster dict (modified in-place and returned).

    Returns:
        Updated clusters dict with the cluster replaced by singletons.
    """
    if cluster_id not in clusters:
        return clusters

    cinfo = clusters[cluster_id]
    members = cinfo["members"]

    # Memory: reject correction for every pair in this cluster.
    if memory_store is not None:
        unmerge_pairs = list(cinfo.get("pair_scores", {}).keys())
        if not unmerge_pairs:
            unmerge_pairs = [
                (members[i], members[j])
                for i in range(len(members))
                for j in range(i + 1, len(members))
            ]
        _record_unmerge_corrections(unmerge_pairs, memory_store, dataset)

    del clusters[cluster_id]

    next_cid = max(clusters.keys(), default=0) + 1
    for member_id in members:
        clusters[next_cid] = {
            "members": [member_id],
            "size": 1,
            "oversized": False,
            "pair_scores": {},
            "confidence": 1.0,
            "bottleneck_pair": None,
            "cluster_quality": "strong",
        }
        next_cid += 1

    return clusters
