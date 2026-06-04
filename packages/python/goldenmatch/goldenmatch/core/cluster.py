"""Union-Find clustering for GoldenMatch."""

from __future__ import annotations

import logging
import operator
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


_SPLIT_EDGE_BUDGET_PER_ROW = 5  # C: linear edge-work allowance per input row


def _split_edge_work_budget(n_rows: int, override: int | None = None) -> int:
    """Cumulative-edge-work cap for the auto-split loop.

    Precedence: explicit ``override`` (GoldenRulesConfig.split_edge_budget) >
    GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET env > max(5M, n_rows * C). With the
    single-MST batch split (#661) exhaustion is rare; this makes the rare case
    scale-appropriate and tunable."""
    if override is not None:
        return max(1, int(override))
    raw = os.environ.get("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET")
    if raw is not None:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(_DEFAULT_SPLIT_EDGE_WORK_BUDGET, int(n_rows) * _SPLIT_EDGE_BUDGET_PER_ROW)


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
    # operator.itemgetter(2) is a C-level key extractor; the equivalent
    # `lambda e: e[2]` invokes a Python frame per comparison-key fetch and
    # showed up as a hotspot in the profile-hotspots cProfile run.
    edges.sort(key=operator.itemgetter(2), reverse=True)
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

    if native_enabled("clustering"):  # pragma: no cover - exercised by the native CI lane (test_native_parity), not the no-ext python lane
        # Native kernel does MST (Kruskal) + weakest-edge removal + re-union
        # and returns the post-split subcluster member lists. Edges are passed
        # in pair_scores iteration order so the native stable score-desc sort
        # and first-minimum tie-break reproduce the Python path exactly. An
        # empty return means the MST was empty -> unsplittable (handled by the
        # shared guard below, mirroring the pure-Python `if not mst`).
        edges = [(a, b, s) for (a, b), s in pair_scores.items()]
        subclusters: list = native_module().mst_split_components(members, edges)
    else:
        mst = _build_mst(members, pair_scores)
        if not mst:
            subclusters = []
        else:
            weakest = min(mst, key=lambda e: e[2])
            remaining = [(a, b, s) for a, b, s in mst if (a, b, s) != weakest]

            uf = UnionFind()
            uf.add_many(members)
            for a, b, _s in remaining:
                uf.union(a, b)

            subclusters = uf.get_clusters()

    if not subclusters:
        # Native MST empty or Python cluster unsplittable -> leave intact.
        return [{"members": sorted(members), "size": len(members),
                 "oversized": False, "pair_scores": pair_scores}]
    # Partition pair_scores across the post-split subclusters in a SINGLE
    # pass. The previous shape rebuilt each subcluster's pair dict with a
    # comprehension that re-scanned the FULL pair_scores per subcluster
    # (O(pairs * subclusters)); on dense oversized clusters that was a
    # measured hotspot. A member -> subcluster-index map lets us bucket
    # each pair exactly once (O(pairs)). A pair whose endpoints landed in
    # different subclusters (the removed weakest edge, plus any cross-cut
    # edge) belongs to neither bucket -- identical to the old `a in
    # sc_members and b in sc_members` filter.
    member_to_sub: dict[int, int] = {}
    for idx, sc_members in enumerate(subclusters):
        for m in sc_members:
            member_to_sub[m] = idx
    sub_pairs: list[dict[tuple[int, int], float]] = [{} for _ in subclusters]
    for (a, b), s in pair_scores.items():
        ia = member_to_sub.get(a)
        if ia is not None and ia == member_to_sub.get(b):
            sub_pairs[ia][(a, b)] = s

    result = []
    for idx, sc_members in enumerate(subclusters):
        sc_list = sorted(sc_members)
        sc_pairs = sub_pairs[idx]
        size = len(sc_list)
        conf = compute_cluster_confidence(sc_pairs, size)
        result.append({
            "members": sc_list, "size": size, "oversized": False,
            "pair_scores": sc_pairs, "confidence": conf["confidence"],
            "bottleneck_pair": conf["bottleneck_pair"],
        })
    return result


def split_oversized_cluster_to_size(
    members: list[int],
    pair_scores: dict[tuple[int, int], float],
    max_size: int,
) -> list[dict]:
    """Split a cluster down to ``max_size`` from a SINGLE MST build (#661).

    Repeatedly cuts the weakest tree edge of any component still larger than
    ``max_size``. A sub-tree of a maximum spanning tree IS the maximum spanning
    tree of its induced sub-graph (cycle property), so cutting original tree
    edges reproduces the old per-component re-MST cut decisions (same membership
    partition, same first-minimum tie-break). Returns final sub-clusters in a
    DETERMINISTIC order (sort-by-min-member at each cut, oversized components
    re-enqueued LIFO).

    Components that cannot be cut further (no remaining cuttable tree edge) are
    returned still oversized (``oversized=True``)."""
    if len(members) <= max_size or len(members) <= 1 or not pair_scores:
        size = len(members)
        return [{"members": sorted(members), "size": size,
                 "oversized": size > max_size, "pair_scores": pair_scores,
                 **_confidence_fields(pair_scores, size)}]

    tree_edges = _build_mst(members, pair_scores)
    if not tree_edges:
        size = len(members)
        return [{"members": sorted(members), "size": size,
                 "oversized": size > max_size, "pair_scores": pair_scores,
                 **_confidence_fields(pair_scores, size)}]

    out_order: list[frozenset[int]] = []
    work: list[tuple[set[int], list]] = [(set(members), list(tree_edges))]
    while work:
        node_set, edges = work.pop()
        if len(node_set) <= max_size or not edges:
            out_order.append(frozenset(node_set))
            continue
        weakest = min(edges, key=lambda e: e[2])   # first-minimum, same as today
        remaining = [e for e in edges if e is not weakest]
        uf = UnionFind()
        uf.add_many(list(node_set))
        for a, b, _s in remaining:
            uf.union(a, b)
        comps = uf.get_clusters()                   # 2 components
        node_to_rep = {n: uf.find(n) for n in node_set}
        rep_to_edges: dict[int, list] = {}
        for e in remaining:
            rep_to_edges.setdefault(node_to_rep[e[0]], []).append(e)
        sub_items = [(c, rep_to_edges.get(uf.find(next(iter(c))), [])) for c in comps]
        sub_items.sort(key=lambda ci: min(ci[0]))
        for c, ce in sub_items:
            if len(c) > max_size:
                work.append((set(c), ce))
            else:
                out_order.append(frozenset(c))

    member_to_idx: dict[int, int] = {}
    for idx, s in enumerate(out_order):
        for m in s:
            member_to_idx[m] = idx
    sub_pairs: list[dict] = [{} for _ in out_order]
    for (a, b), sc in pair_scores.items():
        ia = member_to_idx.get(a)
        if ia is not None and ia == member_to_idx.get(b):
            sub_pairs[ia][(a, b)] = sc

    result = []
    for idx, s in enumerate(out_order):
        sc_list = sorted(s)
        size = len(sc_list)
        result.append({
            "members": sc_list, "size": size, "oversized": size > max_size,
            "pair_scores": sub_pairs[idx],
            **_confidence_fields(sub_pairs[idx], size),
        })
    return result


def _confidence_fields(pair_scores: dict, size: int) -> dict:
    conf = compute_cluster_confidence(pair_scores, size)
    return {"confidence": conf["confidence"], "bottleneck_pair": conf["bottleneck_pair"]}


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


def _is_pairs_dataframe(pairs: Any) -> bool:
    """True when ``pairs`` is a Polars DataFrame (the columnar pair stream).

    polars is a hard dependency of goldenmatch but is imported lazily here so
    cluster.py stays import-light for the list path. The cheap ``is_empty``
    duck-type check short-circuits the common list input before importing
    polars; the isinstance check is the actual gate.
    """
    if not hasattr(pairs, "is_empty"):
        return False
    import polars as pl
    return isinstance(pairs, pl.DataFrame)


def build_clusters(
    pairs: Any,  # list[tuple[int, int, float]] | pl.DataFrame | ray.data.Dataset
    all_ids: list[int] | None = None,
    max_cluster_size: int = 100,
    weak_cluster_threshold: float = 0.3,
    auto_split: bool = True,
    split_edge_budget: int | None = None,
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

    # Arrow Phase 1 (Wave 2): accept a columnar pair stream as a first-class
    # input. A Polars DataFrame (PAIR_STREAM_SCHEMA: id_a, id_b, score) is
    # converted via the numpy path -- NOT a Python list comprehension -- to the
    # list[(int, int, float)] the Union-Find / pair_scores path below consumes.
    # The return shape is unchanged (dict[int, dict]); Phase 2 changes it to
    # the two-frame ClusterFrames layout. The list[tuple] branch below stays
    # for the deprecation window.
    if _is_pairs_dataframe(pairs):
        if all_ids is None and not pairs.is_empty():
            import numpy as _np
            a_np = pairs["id_a"].to_numpy()
            b_np = pairs["id_b"].to_numpy()
            all_ids = _np.unique(_np.concatenate([a_np, b_np])).tolist()
        pairs = _pairs_df_to_list_numpy(pairs)

    # Derive all_ids from pairs when not provided explicitly
    if all_ids is None:
        seen: set[int] = set()
        for id_a, id_b, _s in pairs:
            seen.add(id_a)
            seen.add(id_b)
        all_ids = list(seen)

    # SP1 (columnar cluster-build core): when enabled, build the same
    # ``dict[int, dict]`` via the columnar Arrow path. Default OFF; the output
    # is byte-identical to the dict path (parity gate
    # tests/test_columnar_cluster_build_parity.py), differing only in member
    # LIST ORDER (a separate Union-Find -> compared as a set). Gate goes AFTER
    # the Ray short-circuit, the DataFrame branch, and the all_ids derivation so
    # it never intercepts Ray and always has a concrete pairs list + all_ids.
    if _columnar_cluster_build_enabled():
        return _build_clusters_via_frames(
            pairs, all_ids, max_cluster_size, weak_cluster_threshold, auto_split,
            split_edge_budget,
        )

    return _build_clusters_dict_path(
        pairs, all_ids, max_cluster_size, weak_cluster_threshold, auto_split,
        split_edge_budget=split_edge_budget,
    )


def _columnar_cluster_build_enabled() -> bool:
    """Columnar cluster-build core for ``build_clusters`` (SP1): build the
    ``dict[int, dict]`` via the two-frame columnar path
    (``_build_clusters_via_frames``) instead of the list/dict Union-Find path.
    Default OFF -- byte-identical to the dict path on OUTPUT (member list order
    aside; a separate UF runs so order legitimately differs but membership is
    identical). Kill-switch ``GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=0`` (the
    default) restores the dict path. Mirrors the identity
    ``_batch_fingerprint_enabled`` gate."""
    return os.environ.get(
        "GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "0"
    ).strip() != "0"


def _cluster_frames_out_enabled() -> bool:  # pyright: ignore[reportUnusedFunction]
    """SP-A frames-out path gate. When ``GOLDENMATCH_CLUSTER_FRAMES_OUT`` is set
    (non-``0``), ``build_cluster_frames`` returns the two-frame ``ClusterFrames``
    columnar representation directly, WITHOUT materializing the per-cluster
    ``dict[int, dict]`` for non-oversized clusters. Default OFF. Independent of
    ``build_clusters`` and all its consumers, which are untouched.

    Not consumed in SP-A; the pipeline wires this gate in SP-B to choose
    build_cluster_frames vs build_clusters. The pyright-ignore drops the
    reportUnusedFunction false-positive until then."""
    return os.environ.get("GOLDENMATCH_CLUSTER_FRAMES_OUT", "0").strip() != "0"


def build_cluster_frames(
    pairs: Any,
    all_ids: list[int] | None = None,
    *,
    max_cluster_size: int,
    weak_cluster_threshold: float,
    auto_split: bool,
    split_edge_budget: int | None = None,
) -> ClusterFrames:
    """SP-A frames-out entry point (gated ``GOLDENMATCH_CLUSTER_FRAMES_OUT``).

    Returns the two-frame ``ClusterFrames`` columnar representation
    (``assignments`` + 9-col ``metadata``) WITHOUT building the per-cluster
    ``dict[int, dict]`` for non-oversized clusters. ``build_clusters`` and all
    its consumers stay UNTOUCHED.

    The BULK path (non-oversized clusters) is the shared pre-split Union-Find
    (via ``_columnar_presplit``) + vectorized weak/quality + emit. Oversized
    clusters are auto-split frames-natively (the dict is materialized ONLY for
    that rare minority), reusing ``split_oversized_cluster`` and mirroring
    ``_finalize_clusters``.

    ``cluster_frames_to_dict(build_cluster_frames(...))`` round-trips to
    ``build_clusters(...)`` gate-ON (the score-free dict): members-as-set,
    ``pair_scores`` stripped, everything else byte-identical.
    """
    import numpy as _np
    import polars as _pl

    from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA

    pairs_list = list(pairs)
    if all_ids is None:                       # mirror build_clusters (:411-417)
        seen: set[int] = set()
        for a, b, _s in pairs_list:
            seen.add(a)
            seen.add(b)
        all_ids = list(seen)

    pairs_df = _pl.DataFrame(
        {
            "id_a": [p[0] for p in pairs_list],
            "id_b": [p[1] for p in pairs_list],
            "score": [p[2] for p in pairs_list],
        },
        schema=PAIR_STREAM_SCHEMA,
    )
    # BULK pre-split frames. Two paths, IDENTICAL output schema/shape (the SP-A
    # parity test runs both native=1 and native=0):
    #   - Native: the Arrow kernel ALREADY returns canonical (assignments,
    #     metadata) -- cids sorted by min-member, enumerate start=1 (== the
    #     Python re-sort SP-A asserts). Use them DIRECTLY; skip the Python
    #     member-set round-trip + numpy re-fill that _columnar_presplit's native
    #     branch did (it rebuilt member-sets off frames.assignments only to
    #     re-emit the same frames -- ~5s of pure waste at 5M). The kernel's
    #     metadata is the SAME 9-col schema in the SAME order the rest of this
    #     function + cluster_frames_to_dict expect; only min/avg need a null
    #     coalesce (kernel emits null for edgeless/singleton clusters, where the
    #     numpy path coalesced via metadata_by_cid -> 0.0).
    #   - Off-native (no kernel): _columnar_presplit + numpy-fill, VERBATIM (the
    #     only correct path without the kernel). quality="strong" placeholder;
    #     Step-3 below overwrites it on BOTH paths.
    native = native_module() if native_enabled("clustering") else None
    arrow_fn = getattr(native, "build_clusters_arrow", None) if native else None
    if native is not None and arrow_fn is not None:
        frames0 = build_clusters_arrow_native(
            pairs_df, all_ids=all_ids, max_cluster_size=max_cluster_size,
        )
        assignments = frames0.assignments
        metadata = frames0.metadata.with_columns(
            _pl.col("min_edge").fill_null(0.0),
            _pl.col("avg_edge").fill_null(0.0),
        )
    else:
        sorted_clusters, metadata_by_cid, _weak = _columnar_presplit(
            pairs_list, pairs_df, all_ids, max_cluster_size,
        )
        n_clusters = len(sorted_clusters)
        total_members = sum(len(s) for s in sorted_clusters)
        a_cid = _np.empty(total_members, dtype=_np.int64)
        a_mid = _np.empty(total_members, dtype=_np.int64)
        m_cid = _np.empty(n_clusters, dtype=_np.int64)
        m_size = _np.empty(n_clusters, dtype=_np.int64)
        m_conf = _np.empty(n_clusters, dtype=_np.float64)
        m_over = _np.empty(n_clusters, dtype=_np.bool_)
        m_bot_a = _np.empty(n_clusters, dtype=_np.int64)
        m_bot_b = _np.empty(n_clusters, dtype=_np.int64)
        m_min = _np.empty(n_clusters, dtype=_np.float64)
        m_avg = _np.empty(n_clusters, dtype=_np.float64)
        a_idx = 0
        for i, members in enumerate(sorted_clusters):
            cid = i + 1
            n = len(members)
            a_cid[a_idx:a_idx + n] = cid
            a_mid[a_idx:a_idx + n] = list(members)
            a_idx += n
            md = metadata_by_cid[cid]
            bot = md["bottleneck_pair"] or (0, 0)
            m_cid[i] = cid
            m_size[i] = n
            m_conf[i] = md["confidence"]
            m_over[i] = n > max_cluster_size
            m_bot_a[i] = bot[0]
            m_bot_b[i] = bot[1]
            m_min[i] = md["min_edge"]   # coalesced 0.0, never None
            m_avg[i] = md["avg_edge"]
        assignments = _pl.DataFrame({"cluster_id": a_cid, "member_id": a_mid})
        metadata = _pl.DataFrame({
            "cluster_id": m_cid,
            "size": m_size,
            "confidence": m_conf,
            "quality": _pl.Series("quality", ["strong"] * n_clusters, dtype=_pl.Utf8),
            "oversized": m_over,
            "bottleneck_pair_a": m_bot_a,
            "bottleneck_pair_b": m_bot_b,
            "min_edge": m_min,
            "avg_edge": m_avg,
        })

    if auto_split:
        # Frames-native auto-split: the per-cluster dict (the SP1 bench loss) is
        # confined to the rare oversized MINORITY. Mirrors _finalize_clusters's
        # split loop EXACTLY: iterate sorted(oversized), call
        # split_oversized_cluster_to_size ONCE per top-level cluster (the batch fn
        # owns the full recursive split + sub ordering -- no re-enqueue here), and
        # label subs contiguously from max(live_cids)+1. split rows carry
        # quality="split" so the Step-3 vectorized weak/quality block's
        # when(quality=="split") short-circuit preserves them.
        oversized = sorted(metadata.filter(_pl.col("oversized"))["cluster_id"].to_list())
        if oversized:
            members_by_cid = {
                int(cid): assignments.filter(_pl.col("cluster_id") == cid)["member_id"].to_list()
                for cid in oversized
            }
            live_cids = set(range(1, metadata.height + 1))
            split_result: dict[int, dict] = {}
            drop_cids = set()                      # ORIGINAL cids that split
            edge_work, edge_budget, budget_tripped = 0, _split_edge_work_budget(len(all_ids), split_edge_budget), False
            for cid in oversized:
                members = members_by_cid[int(cid)]
                ms = set(members)
                ps = {(a, b): s for a, b, s in pairs_list if a in ms and b in ms}
                edge_work += len(ps)
                if edge_work > edge_budget:
                    budget_tripped = True
                    break  # leave cid (+ remaining) oversized: original rows stay
                subs = split_oversized_cluster_to_size(members, ps, max_cluster_size)
                if len(subs) <= 1:
                    continue  # unsplittable: original row stays
                drop_cids.add(int(cid))
                live_cids.discard(int(cid))
                next_cid = max(live_cids, default=0) + 1
                for sc in subs:
                    split_result[next_cid] = sc
                    live_cids.add(next_cid)
                    next_cid += 1
            if budget_tripped:
                _clog.warning("build_cluster_frames: auto-split edge-work budget (%d) "
                              "exhausted; clusters left oversized.", edge_budget)
            # Materialize the FINAL split sub-clusters into frame rows. split rows
            # carry quality="split" so the Step-3 when(quality=="split") short-circuit
            # preserves them. min/avg are schema-fill (unused: split skips the weak test).
            split_assign_rows = []
            split_meta_rows = []
            for ncid, sc in split_result.items():
                for m in sc["members"]:
                    split_assign_rows.append((ncid, m))
                _sv = list(sc["pair_scores"].values())
                _bot = sc["bottleneck_pair"] or (0, 0)
                split_meta_rows.append({
                    "cluster_id": ncid, "size": sc["size"],
                    "confidence": sc["confidence"], "quality": "split",
                    "oversized": sc["size"] > max_cluster_size,
                    "bottleneck_pair_a": int(_bot[0]),
                    "bottleneck_pair_b": int(_bot[1]),
                    "min_edge": min(_sv) if _sv else 0.0,
                    "avg_edge": (sum(_sv) / len(_sv)) if _sv else 0.0,
                })
            if drop_cids:
                assignments = assignments.filter(~_pl.col("cluster_id").is_in(drop_cids))
                metadata = metadata.filter(~_pl.col("cluster_id").is_in(drop_cids))
            if split_assign_rows:
                assignments = _pl.concat([assignments, _pl.DataFrame(
                    split_assign_rows, schema=["cluster_id", "member_id"], orient="row")])
                metadata = _pl.concat(
                    [metadata, _pl.DataFrame(split_meta_rows, schema=metadata.schema)],
                    how="vertical",
                )

    # Step 3: vectorized weak/quality (excludes "split" rows -- none in Task 1).
    # Reproduces the dict path's _finalize_clusters weak/quality logic.
    metadata = metadata.with_columns(
        _pl.when(_pl.col("quality") == "split").then(_pl.col("quality"))
        .when(
            (_pl.col("size") > 1)
            & ((_pl.col("avg_edge") - _pl.col("min_edge")) > weak_cluster_threshold)
        )
        .then(_pl.lit("weak")).otherwise(_pl.lit("strong")).alias("quality"),
    ).with_columns(
        _pl.when(_pl.col("quality") == "weak")
        .then(_pl.col("confidence") * 0.7).otherwise(_pl.col("confidence"))
        .alias("confidence"),
    )

    _emit_cluster_profile_frames(metadata, assignments)
    return ClusterFrames(assignments=assignments, metadata=metadata)


def _emit_cluster_profile_frames(metadata: Any, assignments: Any) -> None:
    """Frames-path twin of ``_emit_cluster_profile``. Telemetry only -- no-op
    when no capture is active. Builds the ``ClusterProfile`` from the metadata +
    assignments frames as closely as the columnar shape allows (no per-cluster
    pair_scores on this path, so the transitivity threshold falls back to 0.5,
    same as ``_emit_cluster_profile`` when ``aggregated_scores`` is empty)."""
    import math

    if not _emitter_stack.get():
        return  # fast path: no capture active

    import polars as _pl

    if metadata.is_empty():
        current_emitter().set_cluster(ClusterProfile())
        return

    sizes = sorted(metadata["size"].to_list())

    def percentile(xs: list, q: float) -> int:
        if not xs:
            return 0
        idx = max(0, min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1))
        return xs[idx]

    confidences = sorted(
        v for v in metadata["confidence"].to_list() if v is not None
    )

    members_by_cluster: dict[int, list[int]] = {}
    if not assignments.is_empty():
        for cid, mid in zip(
            assignments["cluster_id"].to_list(),
            assignments["member_id"].to_list(),
            strict=True,
        ):
            members_by_cluster.setdefault(int(cid), []).append(int(mid))

    # No per-cluster pair_scores on the frames path -> empty aggregate ->
    # transitivity threshold falls back to 0.5 (same as _emit_cluster_profile).
    aggregated_scores: dict[tuple[int, int], float] = {}
    threshold = 0.5

    oversized_count = int(
        metadata.filter(_pl.col("oversized")).height
    )

    profile = ClusterProfile(
        n_clusters=metadata.height,
        cluster_size_p50=percentile(sizes, 0.50),
        cluster_size_p99=percentile(sizes, 0.99),
        cluster_size_max=sizes[-1] if sizes else 0,
        transitivity_rate=transitivity_rate(
            members_by_cluster, aggregated_scores, threshold,
        ),
        edge_confidence_p50=(
            confidences[len(confidences) // 2] if confidences else 0.0
        ),
        edge_confidence_min=confidences[0] if confidences else 0.0,
        oversized_cluster_count=oversized_count,
        bridge_edge_count=0,
        measured_bridge_risk=0.0,
    )
    current_emitter().set_cluster(profile)


def _build_clusters_dict_path(
    pairs: Any,
    all_ids: list[int],
    max_cluster_size: int,
    weak_cluster_threshold: float,
    auto_split: bool,
    split_edge_budget: int | None = None,
) -> dict[int, dict]:
    """Legacy list/dict Union-Find cluster build. Verbatim extraction of the
    original ``build_clusters`` body (UF stage -> emit); behavior is unchanged.
    The columnar path (``_build_clusters_via_frames``) shares the tail via
    ``_finalize_clusters``."""
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

    return _finalize_clusters(
        result, max_cluster_size, weak_cluster_threshold, auto_split,
        n_rows=len(all_ids), split_edge_budget=split_edge_budget,
    )


def _finalize_clusters(
    result: dict[int, dict],
    max_cluster_size: int,
    weak_cluster_threshold: float,
    auto_split: bool,
    *,
    raw_pairs: list[tuple[int, int, float]] | None = None,
    weak_stats: dict[int, tuple[float, float]] | None = None,
    n_rows: int = 0,
    split_edge_budget: int | None = None,
) -> dict[int, dict]:
    """Shared cluster-build tail: auto-split oversized clusters, assign
    ``cluster_quality`` (+ weak confidence downgrade), then emit the profile.

    Reads ``result`` (the pre-split per-cluster dict) plus the params. Both the
    dict path and the columnar path call this so split/quality/emit are
    byte-identical. OWNS the ``_emit_cluster_profile`` call -- callers must NOT
    emit again.

    Columnar path (SP4): the dict path passes its full per-cluster ``pair_scores``;
    the columnar path passes ``pair_scores={}`` plus ``raw_pairs`` (the flat input
    pairs) + ``weak_stats`` ({cid: (min_edge, avg_edge)} for multi-member
    clusters). When ``raw_pairs`` is given: the MST split materializes an oversized
    cluster's ``pair_scores`` on demand (input order, last-wins == the dict path's
    fill) BEFORE the edge-budget meter, and ALL ``pair_scores`` are reset to ``{}``
    after the split loop. When ``weak_stats`` is given, the weak test reads min/avg
    from it instead of ``pair_scores``. With both ``None`` (dict path) behavior is
    UNCHANGED."""
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
    oversized_cids = sorted(cid for cid, c in result.items() if c["oversized"]) if auto_split else []
    edge_work = 0
    edge_budget = _split_edge_work_budget(n_rows, split_edge_budget)
    budget_tripped = False
    for cid in oversized_cids:
        cinfo = result[cid]
        # Columnar path: materialize this oversized cluster's pair_scores on demand
        # from the raw input pairs (input order, last-wins == the dict path's fill).
        if raw_pairs is not None and not cinfo["pair_scores"]:
            _ms = set(cinfo["members"])
            cinfo["pair_scores"] = {
                (a, b): s for a, b, s in raw_pairs if a in _ms and b in _ms
            }
        edge_work += len(cinfo["pair_scores"])
        if edge_work > edge_budget:
            cinfo["oversized"] = True
            budget_tripped = True
            break  # leave this + remaining oversized cids in result, flagged
        subs = split_oversized_cluster_to_size(
            cinfo["members"], cinfo["pair_scores"], max_cluster_size
        )
        if len(subs) <= 1:
            # Unsplittable (no edges / single blob): leave as-is, flagged by size.
            cinfo["oversized"] = cinfo["size"] > max_cluster_size
            continue
        del result[cid]
        next_cid = max(result.keys(), default=0) + 1
        for sc in subs:
            sc["_was_split"] = True            # batch fn already set sc["oversized"]
            result[next_cid] = sc
            next_cid += 1
    if budget_tripped:
        n_oversized = sum(1 for c in result.values() if c.get("oversized"))
        _clog.warning(
            "build_clusters: auto-split edge-work budget (%d) exhausted; %d "
            "cluster(s) left oversized (dense, no clean weak-bridge split). "
            "Oversized clusters are excluded from golden downstream.",
            edge_budget, n_oversized,
        )

    # Columnar path: the per-oversized materialization above transiently put real
    # pair_scores on clusters processed in the split loop; reset ALL pair_scores to
    # {} so the returned dict is uniformly score-free (scores are served by the
    # pipeline view). The weak step below reads weak_stats, not pair_scores.
    if raw_pairs is not None:
        for cinfo in result.values():
            cinfo["pair_scores"] = {}

    # Assign cluster_quality and apply confidence downgrade
    with stage("cluster_quality_assignment"):
        for cid, cinfo in result.items():
            if cinfo.get("_was_split"):
                cinfo["cluster_quality"] = "split"
            elif weak_stats is not None:
                # Columnar path: weak test reads min/avg from weak_stats (split
                # sub-clusters hit "_was_split" above; absent cid = no edges).
                if cinfo["size"] > 1 and cid in weak_stats:
                    min_edge, avg_edge = weak_stats[cid]
                    if avg_edge - min_edge > weak_cluster_threshold:
                        cinfo["cluster_quality"] = "weak"
                        cinfo["confidence"] *= 0.7
                    else:
                        cinfo["cluster_quality"] = "strong"
                else:
                    cinfo["cluster_quality"] = "strong"
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


def _build_clusters_via_frames(
    pairs: Any,
    all_ids: list[int],
    max_cluster_size: int,
    weak_cluster_threshold: float,
    auto_split: bool,
    split_edge_budget: int | None = None,
) -> dict[int, dict]:
    """Columnar cluster-build core. Returns the dict path's shape EXCEPT
    ``pair_scores`` is ``{}`` on every cluster (SP4: the eager per-cluster dict --
    the SP1 bench loss -- is dropped; scores are served by a ``ClusterPairScores``
    view built at the pipeline level from the scored-pair stream). Everything else
    (members/size/oversized/confidence/bottleneck/cluster_quality/ids) is
    byte-identical; the shared ``_finalize_clusters`` tail does auto-split/quality/
    emit.

    Union-Find source differs native vs off-native:
      * Native + the Arrow kernel present: ``build_clusters_arrow_native`` runs
        UF in Rust (UF-ONLY, pre-split); member sets come from
        ``frames.assignments``. Confidence/bottleneck/min_edge/avg_edge are read
        DIRECTLY from ``frames.metadata`` keyed by ``cluster_id`` (the kernel sorts
        components by min-member + enumerates start=1, identical to our re-sort, so
        the cid mapping is direct). NO per-cluster ``pair_scores`` fill.
      * Off-native (or the Arrow kernel absent): UF membership comes DIRECTLY
        from ``connected_components`` (when exposed) or the pure-Python
        ``UnionFind``. Confidence + min/avg come from a TRANSIENT per-cluster fill
        in PAIRS-INPUT ORDER (matching the dict path's float-sum order exactly)
        which is then DISCARDED -- the returned dict's ``pair_scores`` stays ``{}``.
        We do NOT call ``build_clusters_arrow_native`` here (its
        ``build_clusters_v2_columnar`` fallback re-runs the FULL ``build_clusters``
        incl. auto-split -> POST-split frames).

    Both states are STRICT byte-identical to the dict path on everything but
    ``pair_scores`` (the kernel's metadata and the off-native transient fill are
    both pairs-input order). ``raw_pairs`` + ``weak_stats`` are threaded into
    ``_finalize_clusters`` for the per-oversized split materialization + the weak
    test.
    """
    import polars as _pl

    from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA

    pairs_list = list(pairs)
    pairs_df = _pl.DataFrame(
        {
            "id_a": [p[0] for p in pairs_list],
            "id_b": [p[1] for p in pairs_list],
            "score": [p[2] for p in pairs_list],
        },
        schema=PAIR_STREAM_SCHEMA,
    )

    sorted_clusters, metadata_by_cid, weak_stats = _columnar_presplit(
        pairs_list, pairs_df, all_ids, max_cluster_size,
    )

    # --- Build the SAME pre-split result dict as the dict path. ------------
    # confidence/bottleneck come from metadata_by_cid (native: kernel metadata;
    # off-native: the discarded transient fill, bit-identical to
    # compute_cluster_confidence). pair_scores stays {} (SP4: served by the view).
    with stage("cluster_result_dict_init"):
        result: dict[int, dict] = {}
        for cluster_id, members in enumerate(sorted_clusters, start=1):
            size = len(members)
            md_c = metadata_by_cid[cluster_id]
            result[cluster_id] = {
                "members": list(members),
                "size": size,
                "oversized": size > max_cluster_size,
                "pair_scores": {},
                "confidence": md_c["confidence"],
                "bottleneck_pair": md_c["bottleneck_pair"],
            }

    # --- Steps 5-7: auto-split + cluster_quality + emit (shared tail). -----
    # raw_pairs lets _finalize materialize per-OVERSIZED pair_scores (input order,
    # last-wins) for the MST split; weak_stats feeds the weak test. The returned
    # dict's pair_scores is {} on every cluster.
    return _finalize_clusters(
        result, max_cluster_size, weak_cluster_threshold, auto_split,
        raw_pairs=pairs_list, weak_stats=weak_stats,
        n_rows=len(all_ids), split_edge_budget=split_edge_budget,
    )


def _columnar_presplit(
    pairs_list: list[tuple[int, int, float]],
    pairs_df: Any,
    all_ids: list[int],
    max_cluster_size: int,
) -> tuple[list[set[int]], dict[int, dict], dict[int, tuple[float, float]]]:
    """Shared pre-split Union-Find + per-cluster confidence source.

    Returns ``(sorted_clusters, metadata_by_cid, weak_stats)``.

    - ``sorted_clusters``: member sets sorted by min member (cid = index+1,
      start=1).
    - ``metadata_by_cid``: ``{cid: {confidence, bottleneck_pair, min_edge,
      avg_edge}}`` for EVERY cid. min/avg are COALESCED to ``0.0`` (never None)
      so the frames-out path can write them directly into the metadata frame.
      Native path: from the Arrow kernel metadata (pairs-input order ->
      bit-identical to ``compute_cluster_confidence``). Off-native: from the
      transient pairs-input-order fill (also bit-identical), discarded after.
    - ``weak_stats``: ``{cid: (min_edge, avg_edge)}`` ONLY for multi-member cids
      that HAVE in-cluster edges. Off-native guard ``size>1 and ps``; native
      ``size>1``. Edgeless multi-member cids ABSENT. Carries the RAW (non-
      coalesced) min/avg the dict path uses for the weak test; membership must
      EXACTLY match the current ``_build_clusters_via_frames`` so SP4 parity
      (tests/test_columnar_drop_pairscores_parity.py) doesn't regress.
    """
    import polars as _pl

    # --- Step 1: Union-Find member sets (PRE-split). -----------------------
    native = native_module() if native_enabled("clustering") else None
    arrow_fn = getattr(native, "build_clusters_arrow", None) if native else None

    member_sets: list[set[int]]
    # Native path: per-cluster confidence/bottleneck/min/avg from frames.metadata
    # (keyed by kernel cluster_id == our canonical cid). None off-native.
    kernel_md_by_cid: dict[int, dict] | None = None
    if native is not None and arrow_fn is not None:
        # Native Arrow kernel: UF-ONLY assignments (pre-split). Derive raw
        # member sets per UF component from the assignments frame.
        with stage("cluster_connected_components"):
            frames = build_clusters_arrow_native(
                pairs_df, all_ids=all_ids, max_cluster_size=max_cluster_size,
            )
            by_kernel_cid: dict[int, set[int]] = {}
            for kcid, mid in zip(
                frames.assignments["cluster_id"].to_list(),
                frames.assignments["member_id"].to_list(),
                strict=True,
            ):
                by_kernel_cid.setdefault(int(kcid), set()).add(int(mid))
            member_sets = list(by_kernel_cid.values())
            # Retain the kernel's per-cluster metadata (pairs-input order ->
            # bit-identical to compute_cluster_confidence). (0,0) bottleneck -> None.
            md = frames.metadata
            kernel_md_by_cid = {}
            for cid_v, conf_v, ba_v, bb_v, mn_v, av_v in zip(
                md["cluster_id"].to_list(),
                md["confidence"].to_list(),
                md["bottleneck_pair_a"].to_list(),
                md["bottleneck_pair_b"].to_list(),
                md["min_edge"].to_list(),
                md["avg_edge"].to_list(),
                strict=True,
            ):
                kernel_md_by_cid[int(cid_v)] = {
                    "confidence": conf_v,
                    "bottleneck_pair": (
                        None if (int(ba_v), int(bb_v)) == (0, 0)
                        else (int(ba_v), int(bb_v))
                    ),
                    "min_edge": mn_v,
                    "avg_edge": av_v,
                }
    else:
        # Off-native: source UF DIRECTLY (same pre-split UF as the dict path).
        with stage("cluster_connected_components"):
            if native is not None:
                # connected_components is exposed even when the Arrow kernel
                # isn't (returns list[list[int]]).
                member_sets = [
                    set(c)
                    for c in native.connected_components(pairs_list, all_ids)
                ]
            else:
                uf = UnionFind()
                uf.add_many(all_ids)
                for id_a, id_b, _score in pairs_list:
                    uf.union(id_a, id_b)
                member_sets = uf.get_clusters()
                del uf

    # --- Step 2: sort + member->cid map. ----------------------------------
    with stage("cluster_sort_clusters"):
        sorted_clusters = sorted(member_sets, key=lambda s: min(s))
        del member_sets

    with stage("cluster_member_to_cid"):
        member_to_cid: dict[int, int] = {}
        for cluster_id, members in enumerate(sorted_clusters, start=1):
            for m in members:
                member_to_cid[m] = cluster_id

    sizes_by_cid: dict[int, int] = {
        cluster_id: len(members)
        for cluster_id, members in enumerate(sorted_clusters, start=1)
    }

    # --- Steps 3-4: confidence/bottleneck/min/avg for EVERY cid. ----------
    # Native reads off frames.metadata (deduped, pairs-input order). Off-native
    # uses a TRANSIENT pairs-input-order fill (replace_strict in row order, dict
    # last-wins -- matching the dict path) that feeds confidence + min/avg, then
    # is DISCARDED. weak_stats[cid] = (min_edge, avg_edge) per multi-member
    # cluster WITH edges (raw, non-coalesced) feeds the weak test downstream.
    metadata_by_cid: dict[int, dict] = {}
    weak_stats: dict[int, tuple[float, float]] = {}
    if kernel_md_by_cid is not None:
        with stage("cluster_compute_confidence"):
            for cid in sizes_by_cid:
                md_c = kernel_md_by_cid[cid]
                metadata_by_cid[cid] = {
                    "confidence": md_c["confidence"],
                    "bottleneck_pair": md_c["bottleneck_pair"],
                    "min_edge": (md_c["min_edge"] or 0.0),
                    "avg_edge": (md_c["avg_edge"] or 0.0),
                }
                if sizes_by_cid[cid] > 1:
                    weak_stats[cid] = (md_c["min_edge"], md_c["avg_edge"])
    else:
        transient: dict[int, dict[tuple[int, int], float]] = {}
        with stage("cluster_pair_scores_fill"):
            if not pairs_df.is_empty():
                tagged = pairs_df.with_columns(
                    _pl.col("id_a").replace_strict(member_to_cid).alias("__cid__"),
                )
                for id_a, id_b, score, cid in zip(
                    tagged["id_a"].to_list(),
                    tagged["id_b"].to_list(),
                    tagged["score"].to_list(),
                    tagged["__cid__"].to_list(),
                    strict=True,
                ):
                    transient.setdefault(int(cid), {})[(id_a, id_b)] = score
            del member_to_cid
        with stage("cluster_compute_confidence"):
            for cid, size in sizes_by_cid.items():
                ps = transient.get(cid, {})
                conf = compute_cluster_confidence(ps, size)
                metadata_by_cid[cid] = {
                    "confidence": conf["confidence"],
                    "bottleneck_pair": conf["bottleneck_pair"],
                    # compute_cluster_confidence returns None for size<=1 / no
                    # edges -> coalesce to 0.0 for the frames metadata.
                    "min_edge": (conf["min_edge"] or 0.0),
                    "avg_edge": (conf["avg_edge"] or 0.0),
                }
                if size > 1 and ps:
                    _vals = list(ps.values())
                    weak_stats[cid] = (min(_vals), sum(_vals) / len(_vals))

    return sorted_clusters, metadata_by_cid, weak_stats


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
    # `min(pair_scores, key=lambda p: pair_scores[p])` invoked a Python lambda
    # AND a dict lookup per pair (a profiled hotspot at ~2.9M lambda calls).
    # Scanning items() with a C-level itemgetter key avoids both; ties resolve
    # to the same first-minimum key because items() preserves dict order.
    bottleneck_pair = min(pair_scores.items(), key=operator.itemgetter(1))[0]

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
    scored_pairs: list[tuple[int, int, float]] | None = None,
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

    # Source the affected cluster's pair scores from the explicit scored_pairs
    # stream (filtered to this cluster's members -> exactly its within-member edges,
    # equal to cinfo["pair_scores"]; cross-cut edges are excluded by the member
    # filter, so the single-cluster filter is byte-identical) when provided, else
    # the stored cluster dict. Lets unmerge survive a build that drops pair_scores.
    # Both consumers below (the memory-correction loop and the re-cluster
    # extraction) read this one local map.
    if scored_pairs is not None:
        member_set = set(cinfo["members"])
        pair_scores: dict[tuple[int, int], float] = {
            (min(a, b), max(a, b)): s
            for a, b, s in scored_pairs
            if a in member_set and b in member_set
        }
    else:
        pair_scores = cinfo.get("pair_scores") or {}

    # Memory: reject correction for every pair (record_id, other) in this cluster.
    if memory_store is not None:
        unmerge_pairs: list[tuple[int, int]] = []
        for (a, b) in pair_scores.keys():
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
        for (a, b), s in pair_scores.items()
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
    split_edge_budget: int | None = None,
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
    if all_ids is None and not pairs_df.is_empty():
        # Derive all_ids via numpy (zero-copy concat then tolist's tight
        # C loop). The previous Polars-based path materialized id_a/id_b
        # as Python lists separately, then int()-coerced each one. At
        # 131M pairs that's a measurable cost; numpy.tolist on int64
        # already returns native Python ints.
        import numpy as _np
        a_np = pairs_df["id_a"].to_numpy()
        b_np = pairs_df["id_b"].to_numpy()
        all_ids = _np.unique(_np.concatenate([a_np, b_np])).tolist()

    # Convert pair stream to list[(int, int, float)] via numpy.tolist()
    # instead of pairs_df_to_list's Polars-based path. The cprofile
    # hotspot run (run 26725154453) showed build_clusters_columnar at
    # cluster.py:821 with 212s cumtime at 1M, larger than score_blocks
    # at 188s. The list construction dominated because Polars'
    # Series.to_list() walks Python objects per-element AND the
    # comprehension paid (int(), int(), float()) coercion per row.
    # numpy.tolist() on int64/float64 already returns native Python
    # int/float, so zip() can build tuples without further coercion.
    pairs = _pairs_df_to_list_numpy(pairs_df)
    return build_clusters(
        pairs,
        all_ids=all_ids,
        max_cluster_size=max_cluster_size,
        weak_cluster_threshold=weak_cluster_threshold,
        auto_split=auto_split,
        split_edge_budget=split_edge_budget,
    )


def _pairs_df_to_list_numpy(df: pl.DataFrame) -> list[tuple[int, int, float]]:
    """Faster DataFrame -> list[Pair] via numpy.tolist().

    The legacy ``scorer.pairs_df_to_list`` uses Polars' Series.to_list()
    which walks Python objects per element AND then does ``(int(a),
    int(b), float(s))`` coercion in the comprehension. numpy.tolist on
    a Polars-backed int64/float64 Series uses zero-copy + a tight C
    loop, returning native Python int/float directly. Net win at 131M
    pairs: ~2-3x on this conversion step (cumtime reduction visible in
    the profile_hotspots harness post-merge).

    Kept private (leading underscore) so consumers other than
    ``build_clusters_columnar`` don't accidentally rely on this path
    while ``scorer.pairs_df_to_list`` is still the public contract.
    """
    if df.is_empty():
        return []
    return list(zip(
        df["id_a"].to_numpy().tolist(),
        df["id_b"].to_numpy().tolist(),
        df["score"].to_numpy().tolist(),
        strict=True,
    ))


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

    Phase 2c hot-path (#624): builds the columnar arrays directly via
    numpy with pre-sized buffers and slice-assignment, instead of
    list-of-tuples append-per-row. At the 17M-cluster scale that
    ``materialize_cluster_dict`` hits at 25M (per CLAUDE.md), the
    Python append overhead dominated; this brings it down to a
    single-pass numpy fill where the only Python loop is per-cluster
    (not per-member). ``pl.DataFrame`` accepts numpy arrays zero-copy
    via the columnar constructor.
    """
    import numpy as _np
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
                "min_edge": _pl.Float64(),
                "avg_edge": _pl.Float64(),
            }),
        )

    n_clusters = len(clusters)
    total_members = sum(
        len(c.get("members", [])) for c in clusters.values()
    )

    # Pre-size numpy buffers for both frames. Avoids the O(N) Python
    # list growth + tuple construction the previous implementation paid
    # per (cluster, member) pair.
    a_cid = _np.empty(total_members, dtype=_np.int64)
    a_mid = _np.empty(total_members, dtype=_np.int64)

    m_cid = _np.empty(n_clusters, dtype=_np.int64)
    m_size = _np.empty(n_clusters, dtype=_np.int64)
    m_conf = _np.empty(n_clusters, dtype=_np.float64)
    m_quality: list[str] = [""] * n_clusters
    m_oversized = _np.empty(n_clusters, dtype=_np.bool_)
    m_bot_a = _np.empty(n_clusters, dtype=_np.int64)
    m_bot_b = _np.empty(n_clusters, dtype=_np.int64)
    m_min = _np.empty(n_clusters, dtype=_np.float64)
    m_avg = _np.empty(n_clusters, dtype=_np.float64)

    a_idx = 0
    for m_idx, (cid, cluster) in enumerate(clusters.items()):
        members = cluster.get("members", [])
        n = len(members)
        if n:
            # Broadcast cluster_id across the slice; copy members via
            # numpy's list-fast-path. One copy per cluster, not one
            # tuple per member.
            a_cid[a_idx:a_idx + n] = cid
            a_mid[a_idx:a_idx + n] = members
            a_idx += n

        bottleneck = cluster.get("bottleneck_pair") or (0, 0)
        m_cid[m_idx] = cid
        m_size[m_idx] = cluster.get("size", n)
        m_conf[m_idx] = cluster.get("confidence", 0.0)
        m_quality[m_idx] = str(cluster.get("cluster_quality", "strong"))
        m_oversized[m_idx] = bool(cluster.get("oversized", False))
        m_bot_a[m_idx] = bottleneck[0] if bottleneck else 0
        m_bot_b[m_idx] = bottleneck[1] if bottleneck else 0
        _ps = cluster.get("pair_scores") or {}
        _scores = list(_ps.values())
        m_min[m_idx] = min(_scores) if _scores else 0.0
        m_avg[m_idx] = (sum(_scores) / len(_scores)) if _scores else 0.0

    assignments = _pl.DataFrame({
        "cluster_id": a_cid,
        "member_id": a_mid,
    })
    metadata = _pl.DataFrame({
        "cluster_id": m_cid,
        "size": m_size,
        "confidence": m_conf,
        "quality": m_quality,
        "oversized": m_oversized,
        "bottleneck_pair_a": m_bot_a,
        "bottleneck_pair_b": m_bot_b,
        "min_edge": m_min,
        "avg_edge": m_avg,
    })
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


# ---------------------------------------------------------------------------
# Arrow-native roadmap Phase 3 (#625): build_clusters via Rust Arrow kernel
# ---------------------------------------------------------------------------
#
# Reads pair stream + all_ids as Arrow buffers (zero-copy from Polars),
# runs Union-Find in Rust, returns ClusterFrames-shaped output without
# the dict/list intermediate that capped build_clusters_native at 1.09x.
#
# Third Rust Arrow kernel in the Phase 3 series after dedup_pairs_arrow
# (#643) and record_fingerprints_batch_arrow (#644). Same strategic
# pattern: kernels with Arrow IO unblock DataFusion B2 via PyCapsule
# ScalarUDFs.
#
# v1 scope: Union-Find + cluster_id assignment + confidence/bottleneck/
# oversized metadata. Auto-split (oversized -> MST split) and weak-
# cluster downgrades (cluster_quality=weak) are NOT in this kernel --
# callers wrap and post-process for those. The output's ``quality``
# column is always ``"strong"`` and ``auto_split`` is ignored;
# build_clusters_v2_columnar can re-apply downstream filters when
# needed.


def build_clusters_arrow_native(
    pairs_df: pl.DataFrame,  # PAIR_STREAM_SCHEMA columns
    all_ids: list[int] | None = None,
    max_cluster_size: int = 100,
) -> ClusterFrames:
    """Rust Arrow native cluster builder. Reads the pair stream's Arrow
    buffers directly via the C Data Interface, runs Union-Find in Rust,
    emits two ClusterFrames-shaped Arrow buffer sets.

    Phase 3 deliverable per the Arrow-native roadmap (#625). The
    dict-shaped ``build_clusters_native`` Rust kernel benched at 1.09x
    (capped by per-cluster PyDict construction); this Arrow path
    bypasses the dict construction entirely.

    Falls back to ``build_clusters_v2_columnar`` (Polars columnar +
    legacy build_clusters) when the native ``clustering`` component
    is disabled or the Arrow kernel isn't built -- graceful degrade
    with identical output shape.

    Args:
        pairs_df: ``PAIR_STREAM_SCHEMA`` DataFrame.
        all_ids: Optional explicit ID list (defaults to derived from
            pair endpoints).
        max_cluster_size: Threshold for the ``oversized`` flag on
            metadata.

    Returns:
        ``ClusterFrames`` with the canonical (assignments, metadata)
        shape. ``quality`` column is always ``"strong"`` in v1 --
        weak-cluster downgrades and auto-split happen via the legacy
        post-processor path; callers can chain that as needed.
    """
    from goldenmatch.core._native_loader import native_enabled, native_module

    # Fall back when native isn't enabled or the kernel isn't exposed.
    if not native_enabled("clustering"):
        return build_clusters_v2_columnar(
            pairs_df, all_ids=all_ids, max_cluster_size=max_cluster_size,
        )
    native = native_module()
    arrow_fn = getattr(native, "build_clusters_arrow", None)
    if arrow_fn is None:
        return build_clusters_v2_columnar(
            pairs_df, all_ids=all_ids, max_cluster_size=max_cluster_size,
        )

    import polars as _pl

    if all_ids is None:
        # Vectorized derive: concat id_a/id_b and uniq. Polars handles
        # this without materializing a Python list.
        if pairs_df.is_empty():
            all_ids = []
        else:
            ids_series = _pl.concat(
                [pairs_df["id_a"], pairs_df["id_b"]],
            ).unique()
            all_ids = [int(i) for i in ids_series.to_list()]

    a_arrow = pairs_df["id_a"].to_arrow() if not pairs_df.is_empty() \
        else _pl.Series("id_a", [], dtype=_pl.Int64).to_arrow()
    b_arrow = pairs_df["id_b"].to_arrow() if not pairs_df.is_empty() \
        else _pl.Series("id_b", [], dtype=_pl.Int64).to_arrow()
    s_arrow = pairs_df["score"].to_arrow() if not pairs_df.is_empty() \
        else _pl.Series("score", [], dtype=_pl.Float64).to_arrow()
    all_ids_arrow = _pl.Series("__ids__", all_ids, dtype=_pl.Int64).to_arrow()

    (
        a_cid, a_mid,
        m_cid, m_size, m_conf, m_over, m_bot_a, m_bot_b, m_min, m_avg,
    ) = arrow_fn(a_arrow, b_arrow, s_arrow, all_ids_arrow, max_cluster_size)

    assignments = _pl.DataFrame({
        "cluster_id": _pl.from_arrow(a_cid),
        "member_id":  _pl.from_arrow(a_mid),
    })
    metadata = _pl.DataFrame({
        "cluster_id":       _pl.from_arrow(m_cid),
        "size":             _pl.from_arrow(m_size),
        "confidence":       _pl.from_arrow(m_conf),
        "quality":          _pl.Series(
            "quality", ["strong"] * metadata_height(m_cid), dtype=_pl.Utf8,
        ),
        "oversized":        _pl.from_arrow(m_over),
        "bottleneck_pair_a": _pl.from_arrow(m_bot_a),
        "bottleneck_pair_b": _pl.from_arrow(m_bot_b),
        "min_edge":         _pl.from_arrow(m_min),
        "avg_edge":         _pl.from_arrow(m_avg),
    })
    return ClusterFrames(assignments=assignments, metadata=metadata)


def metadata_height(arrow_array: Any) -> int:
    """Tiny helper -- get row count from a PyArrow ArrayData/Array
    without materializing the values. Used for the ``quality`` column
    construction in ``build_clusters_arrow_native``."""
    # PyArrow arrays expose ``__len__``.
    return len(arrow_array)
