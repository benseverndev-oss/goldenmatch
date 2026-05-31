"""Union-Find clustering for GoldenMatch."""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from goldenmatch.core._native_loader import native_enabled, native_module
from goldenmatch.core._profile_helpers import transitivity_rate
from goldenmatch.core.bench import stage
from goldenmatch.core.complexity_profile import ClusterProfile
from goldenmatch.core.profile_emitter import _emitter_stack, current_emitter

if TYPE_CHECKING:
    import polars as pl

    from goldenmatch.core.memory.store import MemoryStore

_log = logging.getLogger("goldenmatch.memory")
_clog = logging.getLogger("goldenmatch.cluster")

# Auto-split work budget (cumulative edges processed across the split loop).
# split_oversized_cluster removes the single weakest MST edge per pass, which is
# O(edges); a large DENSE cluster with no clean weak bridge peels ~1 node per
# pass and degrades to O(nodes * edges) -- effectively non-terminating. Legitimate
# weak-bridge splits finish far under this budget; a dense peel trips it and the
# remaining oversized clusters are left intact (excluded from golden downstream,
# matching auto_split=False). Env-overridable for testing.
_DEFAULT_SPLIT_EDGE_WORK_BUDGET = 5_000_000


def _split_edge_work_budget() -> int:
    """Cumulative-edge-work cap for the auto-split loop (env-overridable)."""
    raw = os.environ.get("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET")
    if raw is None:
        return _DEFAULT_SPLIT_EDGE_WORK_BUDGET
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_SPLIT_EDGE_WORK_BUDGET


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

    def nodes(self) -> list[int]:
        """Return all added members. Order is insertion order via dict."""
        return list(self._parent.keys())

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


# Bridge detection is O(E*(V+E)) per cluster; only run on clusters at or below
# this size so the sample path stays cheap (clusters there are small).
_BRIDGE_MAX_CLUSTER_SIZE = 100


def _severe_bridge_count(members: list[int], pair_scores: dict) -> int:
    """Count edges whose removal splits this cluster into two components each
    with >= 2 nodes -- the 'merged by one weak link' pathology. A bridge between
    a node and a singleton tail is not severe (one side < 2)."""
    if native_enabled("clustering"):
        edges = [(k[0], k[1], v) for k, v in pair_scores.items()
                 if isinstance(k, tuple) and len(k) == 2]
        return native_module().severe_bridge_count(members, edges)
    adj: dict[int, set[int]] = {m: set() for m in members}
    for k in pair_scores:
        if isinstance(k, tuple) and len(k) == 2 and k[0] in adj and k[1] in adj:
            adj[k[0]].add(k[1])
            adj[k[1]].add(k[0])
    n = len(members)
    count = 0
    for k in pair_scores:
        if not (isinstance(k, tuple) and len(k) == 2):
            continue
        a, b = k
        if a not in adj or b not in adj:
            continue
        # BFS from a with the a-b edge removed; if b is unreachable it's a bridge.
        seen = {a}
        stack = [a]
        while stack:
            u = stack.pop()
            for w in adj[u]:
                if (u == a and w == b) or (u == b and w == a):
                    continue
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        if b not in seen:
            side_a = len(seen)
            if side_a >= 2 and (n - side_a) >= 2:
                count += 1
    return count


def _measure_bridges(clusters: dict[int, dict]) -> tuple[int, float | None]:
    """Returns ``(bridge_edge_count, measured_bridge_risk)``. ``measured_bridge_risk``
    is ``None`` when no multi-member cluster was small enough to measure cheaply
    (the zero_label scorer then falls back to its heuristic proxy)."""
    measurable = [
        c for c in clusters.values()
        if 2 <= c["size"] <= _BRIDGE_MAX_CLUSTER_SIZE
    ]
    if not measurable:
        return 0, None
    total_bridges = 0
    risky = 0
    for c in measurable:
        b = _severe_bridge_count(c["members"], c.get("pair_scores", {}))
        total_bridges += b
        if b > 0:
            risky += 1
    return total_bridges, risky / len(measurable)


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

    bridge_edge_count, measured_bridge_risk = _measure_bridges(clusters)

    profile = ClusterProfile(
        n_clusters=len(clusters),
        cluster_size_p50=percentile(sizes, 0.50),
        cluster_size_p99=percentile(sizes, 0.99),
        cluster_size_max=sizes[-1] if sizes else 0,
        transitivity_rate=transitivity_rate(members_by_cluster, aggregated_scores, threshold),
        edge_confidence_p50=confidences[len(confidences) // 2] if confidences else 0.0,
        edge_confidence_min=confidences[0] if confidences else 0.0,
        oversized_cluster_count=sum(1 for c in clusters.values() if c.get("oversized")),
        bridge_edge_count=bridge_edge_count,
        measured_bridge_risk=measured_bridge_risk,
    )
    current_emitter().set_cluster(profile)


def build_clusters(
    pairs: Any,  # list[tuple[int, int, float]] | ray.data.Dataset
    all_ids: list[int] | None = None,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    auto_split: bool = True,
) -> dict[int, dict]:
    """Build clusters from scored pairs using Union-Find.

    Auto-splits oversized clusters via MST (when auto_split=True). Assigns cluster_quality
    ("strong", "weak", "split") and downgrades confidence for weak clusters.

    Phase 3: also accepts a Ray Dataset of pairs (columns: id_a, id_b, score).
    Dispatches to build_clusters_distributed when the input is a Ray Dataset.
    """
    # Phase 3: distributed path when pairs is a Ray Dataset.
    from goldenmatch.distributed import is_ray_dataset
    if is_ray_dataset(pairs):
        from goldenmatch.distributed.clustering import (
            build_clusters_distributed,
            materialize_cluster_dict,
        )
        if all_ids is None:
            seen: set[int] = set()
            for row in pairs.take_all():
                seen.add(row["id_a"])
                seen.add(row["id_b"])
            all_ids = list(seen)
        clusters_ds = build_clusters_distributed(
            pairs, all_ids=all_ids,
            max_cluster_size=max_cluster_size,
            weak_cluster_threshold=weak_cluster_threshold,
        )
        return materialize_cluster_dict(clusters_ds, pairs)

    # Derive all_ids from pairs when not provided explicitly
    if all_ids is None:
        seen: set[int] = set()
        for id_a, id_b, _s in pairs:
            seen.add(id_a)
            seen.add(id_b)
        all_ids = list(seen)

    with stage("cluster_connected_components"):
        if native_enabled("clustering"):
            # Native Union-Find (component membership is identical to the Python
            # union-by-rank path). Returns list[list[int]].
            clusters = native_module().connected_components(list(pairs), all_ids)
        else:
            uf = UnionFind()
            uf.add_many(all_ids)
            for id_a, id_b, _score in pairs:
                uf.union(id_a, id_b)

            clusters = uf.get_clusters()
            # Release UnionFind internals (parent + rank dicts). At 25M these hold
            # ~2.5 GB combined; Python GC won't free them until `uf` falls out of
            # scope, which is later in this function. Force-release now.
            del uf

    with stage("cluster_sort_clusters"):
        sorted_clusters = sorted(clusters, key=lambda s: min(s))
        # `clusters` is the list-of-sets view returned by get_clusters(); after
        # sorting we don't need the original list, only the sorted view.
        del clusters

    with stage("cluster_member_to_cid"):
        member_to_cid: dict[int, int] = {}
        for cluster_id, members in enumerate(sorted_clusters, start=1):
            for m in members:
                member_to_cid[m] = cluster_id

    with stage("cluster_result_dict_init"):
        result: dict[int, dict] = {}
        for cluster_id, members in enumerate(sorted_clusters, start=1):
            size = len(members)
            # v34 attribution showed this loop at 21.7s / 29% of cluster
            # wall, dominated by the 2M `sorted(members)` calls (one per
            # cluster). The sort isn't load-bearing -- every reader
            # iterates or treats members as a set; even add_to_cluster
            # (the only incremental writer) re-sorts after appending,
            # proving the contract doesn't assume sorted input. Keep
            # whatever order the native UF kernel returned -- it's
            # deterministic across runs (id-anchored) which is the only
            # invariant any caller actually consumes.
            result[cluster_id] = {
                "members": list(members),
                "size": size,
                "oversized": size > max_cluster_size,
                "pair_scores": {},
            }

    with stage("cluster_pair_scores_fill"):
        for id_a, id_b, score in pairs:
            cid = member_to_cid[id_a]
            result[cid]["pair_scores"][(id_a, id_b)] = score
        # member_to_cid + sorted_clusters held ~1.25 + 1.0 GB at 25M scale; once
        # pair_scores is populated they aren't read again inside this function.
        del member_to_cid
        del sorted_clusters

    with stage("cluster_compute_confidence"):
        for cid, cinfo in result.items():
            conf = compute_cluster_confidence(cinfo["pair_scores"], cinfo["size"])
            cinfo["confidence"] = conf["confidence"]
            cinfo["bottleneck_pair"] = conf["bottleneck_pair"]

    # Auto-split oversized clusters (when enabled). See _split_edge_work_budget:
    # the per-pass single-weakest-edge split degrades to O(nodes * edges) on a
    # large dense cluster, so two guards keep the loop terminating without
    # changing behavior for normal weak-bridge clusters:
    #   1. no-progress: if a split can't break the cluster (returns it unchanged),
    #      leave it oversized instead of re-enqueueing the same cluster forever.
    #   2. work budget: cap cumulative edge-work; once tripped, leave the current
    #      and any still-queued clusters oversized.
    # Oversized clusters left un-split are excluded from golden downstream, the
    # same as auto_split=False -- a giant cohesive blob of near-identical records
    # is legitimately one quarantined cluster, not N arbitrary cuts.
    to_split = [cid for cid, c in result.items() if c["oversized"]] if auto_split else []
    edge_work = 0
    edge_budget = _split_edge_work_budget()
    budget_tripped = False
    while to_split:
        cid = to_split.pop()
        cinfo = result.pop(cid)
        edge_work += len(cinfo["pair_scores"])
        if edge_work > edge_budget:
            cinfo["oversized"] = True
            result[cid] = cinfo  # leave oversized; queued cids stay in result too
            budget_tripped = True
            break
        sub_clusters = split_oversized_cluster(cinfo["members"], cinfo["pair_scores"])
        if len(sub_clusters) <= 1:
            # Couldn't split (no edges / no MST): leave it as-is, don't re-enqueue.
            cinfo["oversized"] = cinfo["size"] > max_cluster_size
            result[cid] = cinfo
            continue
        next_cid = max(result.keys(), default=0) + 1
        for sc in sub_clusters:
            sc["oversized"] = sc["size"] > max_cluster_size
            sc["_was_split"] = True
            result[next_cid] = sc
            if sc["oversized"]:
                to_split.append(next_cid)
            next_cid += 1
    if budget_tripped:
        n_oversized = sum(1 for c in result.values() if c.get("oversized"))
        _clog.warning(
            "build_clusters: auto-split edge-work budget (%d) exhausted; %d "
            "cluster(s) left oversized (dense, no clean weak-bridge split). "
            "Oversized clusters are excluded from golden downstream.",
            edge_budget, n_oversized,
        )

    # Assign cluster_quality and apply confidence downgrade
    with stage("cluster_quality_assignment"):
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
    if native_enabled("clustering"):
        edges = [(k[0], k[1], v) for k, v in pair_scores.items()
                 if isinstance(k, tuple) and len(k) == 2]
        min_e, avg_e, conn, bn, conf = native_module().cluster_confidence(edges, size)
        return {
            "min_edge": min_e,
            "avg_edge": avg_e,
            "connectivity": conn,
            "bottleneck_pair": tuple(bn) if bn is not None else None,
            "confidence": conf,
        }

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


# ---------------------------------------------------------------------------
# Arrow-native roadmap Phase 1a (#623): columnar pair-stream entry point
# ---------------------------------------------------------------------------
#
# Sibling function to ``build_clusters`` that accepts a ``pl.DataFrame``
# pair stream (the Phase 1a output of ``score_blocks_columnar`` /
# ``find_fuzzy_matches_columnar``) instead of the legacy list of tuples.
#
# Today's implementation converts the DataFrame to the list shape at the
# boundary and delegates to ``build_clusters`` — same correctness contract
# as the Phase 1a scorer wrappers. Phase 1c will invert: the columnar
# function becomes canonical, the list version becomes a shim.
#
# Spec: docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md
# (gitignored).


def build_clusters_columnar(
    pairs_df: pl.DataFrame,
    all_ids: list[int] | None = None,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    auto_split: bool = True,
) -> dict[int, dict]:
    """Columnar wrapper around :func:`build_clusters`.

    Accepts a Polars DataFrame ``(id_a, id_b, score)`` (canonical
    ``PAIR_STREAM_SCHEMA`` from ``scorer.pairs_list_to_df``). Returns the
    same ``dict[int, dict]`` cluster shape as ``build_clusters``; Phase 2
    will change the return shape to the two-frame ``(assignments,
    metadata)`` layout. For Phase 1a the goal is JUST to accept the
    columnar input.

    Args:
        pairs_df: Polars DataFrame with int64 ``id_a``, int64 ``id_b``,
            float64 ``score`` columns. ``PAIR_STREAM_SCHEMA``-shaped.
        all_ids: Optional explicit ID list; if None, derived from the
            ``id_a`` + ``id_b`` columns via ``.unique()``.
        max_cluster_size, weak_cluster_threshold, auto_split:
            Forwarded to ``build_clusters`` unchanged.

    Returns:
        Same ``dict[int, dict]`` as ``build_clusters``. Phase 2 changes
        this to the columnar two-frame shape.
    """
    import polars as _pl

    from goldenmatch.core.scorer import pairs_df_to_list

    if all_ids is None and not pairs_df.is_empty():
        # Derive all_ids from the DataFrame via Polars expressions instead
        # of iterating tuples (the slow path in build_clusters' own derive
        # branch). Phase 2 lifts this to a fully vectorized columnar
        # derive in the canonical implementation.
        ids_series = _pl.concat(
            [pairs_df["id_a"], pairs_df["id_b"]],
        ).unique()
        all_ids = [int(i) for i in ids_series.to_list()]

    pairs = pairs_df_to_list(pairs_df)
    return build_clusters(
        pairs,
        all_ids=all_ids,
        max_cluster_size=max_cluster_size,
        weak_cluster_threshold=weak_cluster_threshold,
        auto_split=auto_split,
    )


# ---------------------------------------------------------------------------
# Arrow-native roadmap Phase 2a (#624): cluster representation columnar
# ---------------------------------------------------------------------------
#
# Sibling function that returns the Phase-2 two-frame cluster shape:
# - assignments: pl.DataFrame({"cluster_id": i64, "member_id": i64}) long form
# - metadata:    pl.DataFrame({"cluster_id": i64, "size": i64,
#                              "confidence": f64, "quality": Utf8,
#                              "oversized": bool,
#                              "bottleneck_pair_a": i64,
#                              "bottleneck_pair_b": i64})
#
# This is Phase 2a (additive sibling); Phase 2b migrates consumers
# (identity, web preview, MCP, REST) one at a time; Phase 2c removes
# the dict-returning build_clusters once all consumers are migrated.
#
# Today's implementation delegates to build_clusters and converts the
# dict to the two-frame shape at the boundary. Same correctness contract
# as Phase 1a wrappers (the inner clustering math is byte-identical;
# only the return shape changes).
#
# Why the two-frame shape: it eliminates the ~3 GB cluster dict that
# materialize_cluster_dict collects driver-side at 25M scale, AND it
# lets distributed identity resolve (Phase 5 / Splink-Spark roadmap
# Phase 6) run true map_batches over partitions of cluster_assignments
# instead of collecting everything to the driver.
#
# Spec: docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md
# (gitignored).


from dataclasses import dataclass


@dataclass(frozen=True)
class ClusterFrames:
    """Two-frame cluster representation (Phase 2a output).

    ``assignments`` is long form: one row per (cluster, member) pair.
    Iterate via ``df.group_by("cluster_id")`` for per-cluster work, or
    use as input to a join with the source frame for golden record
    build (Phase 4).

    ``metadata`` is one row per cluster: size, confidence, quality
    ("strong" / "weak" / "split"), oversized flag, and the bottleneck
    pair (the weakest edge inside the cluster — used by unmerge).

    The two frames share ``cluster_id`` as the key. Phase 2c will make
    this the canonical return shape; today it's an additive sibling.
    """
    # Both fields are pl.DataFrame at runtime. Annotated as string
    # forward refs against the TYPE_CHECKING-only ``pl`` import so the
    # module stays Polars-lazy at import time, while pyright narrows
    # ``frames.assignments.is_empty()`` etc. correctly.
    assignments: pl.DataFrame
    metadata: pl.DataFrame


def cluster_dict_to_frames(clusters: dict[int, dict]) -> ClusterFrames:
    """Adapter: legacy ``dict[int, dict]`` cluster shape -> two-frame
    Phase-2 representation. Lossless modulo ``pair_scores`` (which
    moves to a lazy view computed from the pair stream in Phase 2b).
    """
    import polars as _pl

    if not clusters:
        return ClusterFrames(
            assignments=_pl.DataFrame(schema={
                "cluster_id": _pl.Int64(), "member_id": _pl.Int64(),
            }),
            metadata=_pl.DataFrame(schema={
                "cluster_id": _pl.Int64(),
                "size": _pl.Int64(),
                "confidence": _pl.Float64(),
                "quality": _pl.Utf8(),
                "oversized": _pl.Boolean(),
                "bottleneck_pair_a": _pl.Int64(),
                "bottleneck_pair_b": _pl.Int64(),
            }),
        )

    assign_rows: list[tuple[int, int]] = []
    meta_rows: list[dict] = []
    for cid, cluster in clusters.items():
        members = cluster.get("members", [])
        for m in members:
            assign_rows.append((int(cid), int(m)))
        bottleneck = cluster.get("bottleneck_pair") or (0, 0)
        meta_rows.append({
            "cluster_id": int(cid),
            "size": int(cluster.get("size", len(members))),
            "confidence": float(cluster.get("confidence", 0.0)),
            "quality": str(cluster.get("cluster_quality", "strong")),
            "oversized": bool(cluster.get("oversized", False)),
            "bottleneck_pair_a": int(bottleneck[0]) if bottleneck else 0,
            "bottleneck_pair_b": int(bottleneck[1]) if bottleneck else 0,
        })

    assignments = _pl.DataFrame(
        assign_rows,
        schema={"cluster_id": _pl.Int64(), "member_id": _pl.Int64()},
        orient="row",
    )
    metadata = _pl.DataFrame(meta_rows)
    return ClusterFrames(assignments=assignments, metadata=metadata)


def cluster_frames_to_dict(frames: ClusterFrames) -> dict[int, dict]:
    """Adapter: two-frame Phase-2 representation -> legacy
    ``dict[int, dict]`` shape. For migrating consumers during Phase 2b.

    NOTE: ``pair_scores`` is NOT reconstructed (it doesn't live on the
    cluster frame). Consumers that need pair_scores must look it up via
    the lazy view against the Phase-1 pair stream — Phase 2b will wire
    that helper.
    """
    assignments = frames.assignments
    metadata = frames.metadata
    if assignments.is_empty():
        return {}

    # Build member lists by cluster_id from the assignments frame.
    by_cid: dict[int, list[int]] = {}
    for cid, mid in zip(
        assignments["cluster_id"].to_list(),
        assignments["member_id"].to_list(),
        strict=True,
    ):
        by_cid.setdefault(int(cid), []).append(int(mid))

    out: dict[int, dict] = {}
    for row in metadata.iter_rows(named=True):
        cid = int(row["cluster_id"])
        bot = (row["bottleneck_pair_a"], row["bottleneck_pair_b"])
        out[cid] = {
            "members": by_cid.get(cid, []),
            "size": int(row["size"]),
            "confidence": float(row["confidence"]),
            "cluster_quality": str(row["quality"]),
            "oversized": bool(row["oversized"]),
            "bottleneck_pair": bot if bot != (0, 0) else None,
            # pair_scores omitted -- consumers that need it use the lazy
            # view against the Phase-1 pair stream (Phase 2b deliverable).
            "pair_scores": {},
        }
    return out


def build_clusters_v2_columnar(
    pairs_df: pl.DataFrame,
    all_ids: list[int] | None = None,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    auto_split: bool = True,
) -> ClusterFrames:
    """Phase 2a entry point. Returns the canonical two-frame cluster
    shape (``ClusterFrames``) instead of the legacy ``dict[int, dict]``.

    Same clustering math as ``build_clusters`` / ``build_clusters_columnar``;
    only the return shape differs. Phase 2c will make this canonical.

    Args:
        pairs_df: ``PAIR_STREAM_SCHEMA`` DataFrame (from
            ``score_blocks_columnar`` etc.).
        all_ids, max_cluster_size, weak_cluster_threshold, auto_split:
            Forwarded to ``build_clusters`` unchanged.

    Returns:
        ``ClusterFrames(assignments, metadata)``.
    """
    legacy = build_clusters_columnar(
        pairs_df,
        all_ids=all_ids,
        max_cluster_size=max_cluster_size,
        weak_cluster_threshold=weak_cluster_threshold,
        auto_split=auto_split,
    )
    return cluster_dict_to_frames(legacy)
