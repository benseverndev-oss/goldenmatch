"""Lazy per-cluster pair-score view (Phase 2 SP2). Decouples the identity
evidence-edge consumer from the legacy per-cluster ``pair_scores`` dict. Sourced
from the FINAL (post-split) cluster partition so it is byte-identical to the dict
path. dict-of-dicts backing; the ``iter_clusters`` interface is frame-ready for a
future SP that makes the build produce a columnar pair frame natively."""
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from typing import Any

import numpy as np

from goldenmatch._polars_lazy import pl


def _bucket_pairs(
    pairs: Iterable[tuple[int, int, float]],
    member_to_cid: dict[int, int],
) -> dict[int, dict[tuple[int, int], float]]:
    """Bucket RAW pairs into per-cluster score dicts: INPUT order, LAST-WINS
    overwrite, keyed by ``(a, b)`` as given. A pair is kept only when BOTH
    endpoints map to the same cid (cross-cut edges of a split cluster are
    excluded). Shared by ``from_pairs`` and ``from_frames`` so the two paths are
    byte-identical."""
    by_cid: dict[int, dict[tuple[int, int], float]] = {}
    for a, b, s in pairs:
        ca = member_to_cid.get(a)
        if ca is not None and ca == member_to_cid.get(b):
            by_cid.setdefault(ca, {})[(a, b)] = s
    return by_cid


class ClusterPairScores:
    __slots__ = ("_by_cid", "_agg_frame", "_cid_to_row", "_edges")

    def __init__(
        self,
        by_cid: dict[int, dict[tuple[int, int], float]] | None = None,
        agg_frame: Any | None = None,
        cid_to_row: dict[int, int] | None = None,
        edges: tuple | None = None,
    ):
        # Two backings, mutually exclusive in practice:
        #  - _by_cid: legacy resident dict-of-dicts (from_pairs/from_cluster_dict)
        #  - _agg_frame + _cid_to_row: engine-plannable frame-backed view. ONE
        #    group_by("cid").agg(...) frame (one row per cid, edges as list-columns
        #    in first-occurrence order) PLUS a cheap cid->row-index dict
        #    (num_clusters entries). Accessors slice the row and build the per-cid
        #    dict on demand, so the global 100M-entry dict-of-dicts is NEVER
        #    resident and no per-cid frame partition is materialized at build time.
        #  - _edges: Arrow-native view (`from_sorted_edges`, built by DataFusion in
        #    `cluster_edges_df.cluster_pair_scores_datafusion`). Flat cid-sorted
        #    edge arrays plus per-cid run offsets; a cid is found by binary search,
        #    so not even a cid->row dict is built. Python sees one cluster's edges
        #    only when a consumer asks for that cluster.
        # __slots__ raises on unset access, so set ALL of them every time.
        self._by_cid = by_cid
        self._agg_frame = agg_frame
        self._cid_to_row = cid_to_row
        self._edges = edges

    @classmethod
    def from_sorted_edges(cls, cid: Any, a: Any, b: Any, score: Any) -> ClusterPairScores:
        """Arrow-backed view over edges ALREADY in final order: sorted by cid, and
        within a cid in first-occurrence order, one row per kept ``(a, b)``
        carrying its last-wins score -- the order and values ``_bucket_pairs``
        produces. Accepts Arrow (Chunked)Arrays or numpy. Raises if the cid
        column is not sorted, since every lookup depends on it."""
        c = np.asarray(cid, dtype=np.int64)
        av = np.asarray(a, dtype=np.int64)
        bv = np.asarray(b, dtype=np.int64)
        sv = np.asarray(score, dtype=np.float64)
        if c.size:
            brk = np.flatnonzero(c[1:] != c[:-1]) + 1
            starts = np.concatenate((np.zeros(1, np.int64), brk))
            ends = np.concatenate((brk, np.array([c.size], np.int64)))
            uniq = c[starts]
            if uniq.size > 1 and not bool(np.all(uniq[1:] > uniq[:-1])):
                raise ValueError("from_sorted_edges: cid column is not sorted")
        else:
            starts = ends = uniq = np.empty(0, np.int64)
        return cls(edges=(uniq, starts, ends, av, bv, sv))

    def _edge_slice(self, cid: int) -> tuple[int, int] | None:
        uniq, starts, ends = self._edges[0], self._edges[1], self._edges[2]
        i = int(np.searchsorted(uniq, cid))
        if i < uniq.size and uniq[i] == cid:
            return int(starts[i]), int(ends[i])
        return None

    @classmethod
    def from_cluster_dict(cls, clusters: dict[int, dict]) -> ClusterPairScores:
        by_cid: dict[int, dict[tuple[int, int], float]] = {}
        for cid, info in clusters.items():
            ps = info.get("pair_scores") or {}
            if ps:
                by_cid[cid] = dict(ps)
        return cls(by_cid)

    @classmethod
    def from_pairs(
        cls,
        pairs: Iterable[tuple[int, int, float]],
        clusters: dict[int, dict],
    ) -> ClusterPairScores:
        """Build the view from the RAW input pairs + final cluster membership --
        used when the columnar build returns ``pair_scores={}`` (SP4). Per cluster
        collects the pairs whose BOTH endpoints are in that cluster, in INPUT order
        with LAST-WINS overwrite, keyed by ``(id_a, id_b)`` as given. This
        reproduces the dict path's per-cluster ``pair_scores`` BYTE-IDENTICALLY (the
        eager fill is the same input-order last-wins; cross-cut edges of a split
        cluster have an endpoint outside its members and are excluded -- the #681
        single-cluster argument).

        MUST be the RAW pairs (the same list fed to ``build_clusters``), NOT the
        ``dedup_pairs_max_score`` (max-score, sorted) ``scored_pairs`` field --
        those differ from the dict path's last-wins on different-score duplicate
        canonical pairs.
        """
        member_to_cid: dict[int, int] = {}
        for cid, info in clusters.items():
            for m in info.get("members", []):
                member_to_cid[m] = cid
        return cls(_bucket_pairs(pairs, member_to_cid))

    @classmethod
    def from_frames(
        cls,
        assignments: Any,
        all_pairs: Iterable[tuple[int, int, float]],
    ) -> ClusterPairScores:
        """Build the view from the FINAL ``assignments`` frame + the RAW input
        pairs -- the SP-C identity-from-frames path that drops the dict rebuild.
        Mirrors ``from_pairs`` exactly: ``member_to_cid`` comes from the
        ``assignments`` frame (one row per ``(cluster_id, member_id)``, singletons
        included) instead of iterating ``clusters.items()``, then the SAME
        bucketing runs -- INPUT order, LAST-WINS, keyed by ``(a, b)`` as given,
        both endpoints in the same cluster. Byte-identical to
        ``from_pairs(all_pairs, clusters)`` for every cid.

        MUST be the RAW pairs (the same list fed to ``build_clusters``), NOT the
        ``dedup_pairs_max_score`` (max-score, sorted) ``scored_pairs`` field --
        those differ from the dict path's last-wins on different-score duplicate
        canonical pairs.

        Stage-1: vectorized via a Polars join instead of the ``_bucket_pairs``
        Python loop (10x faster at 100M pairs). Selects the SAME kept edges:
        a pair is kept iff BOTH endpoints map to the same cid; key is ``(a, b)``
        EXACTLY as given (NEVER canonicalized); key insertion order = first
        occurrence; value = score at the LAST occurrence (LAST-WINS, NOT max);
        self-pairs ``(a, a)`` are kept.

        Stage-2: the VIEW BUILD is a single engine-plannable
        ``group_by("cid").agg(...)`` producing ONE frame -- one row per cid, edges
        as list-columns in first-occurrence order -- plus a cheap
        ``cid -> row-index`` dict. Per-cluster dict materialization is DEFERRED to
        the accessors (built on demand, O(cluster size)). The global dict-of-dicts
        is NEVER resident.
        """
        # D6: arrow-lane / polars-free path -- the assignments frame may be a
        # pa.Table; delegate to the BYTE-IDENTICAL Python reference
        # (from_pairs semantics: input order, LAST-WINS, both-endpoints-in-
        # same-cid) via a seam-read member->cid map. The polars join below is
        # the polars-present WALL optimization (10x at 100M pairs).
        from goldenmatch.core.frame import is_polars_dataframe as _is_pl_df

        if not _is_pl_df(assignments):
            from goldenmatch.core.frame import to_frame as _tf

            _af = _tf(assignments)
            member_to_cid = dict(
                zip(
                    _af.column("member_id").to_list(),
                    _af.column("cluster_id").to_list(),
                )
            )
            fake_clusters = {}
            for mid, cid in member_to_cid.items():
                fake_clusters.setdefault(int(cid), {"members": []})["members"].append(mid)
            return cls.from_pairs(all_pairs, fake_clusters)

        # all_pairs is the raw list[(a,b,s)] AS-GIVEN. NEVER canonicalize.
        a_col, b_col, s_col = [], [], []
        for a, b, s in all_pairs:
            a_col.append(a)
            b_col.append(b)
            s_col.append(s)
        # Explicit schema so an EMPTY pair list yields Int64 ``a``/``b`` columns
        # (not null-typed) -- otherwise the join below blows up with a
        # SchemaError against the i64 ``member_id`` key when a dedupe run
        # produces no scored pairs (small/degenerate inputs).
        pairs_df = pl.DataFrame(
            {"a": a_col, "b": b_col, "s": s_col},
            schema={"a": pl.Int64, "b": pl.Int64, "s": pl.Float64},
        ).with_row_index("__i__")
        amap_a = assignments.select(
            "member_id", pl.col("cluster_id").alias("cid_a")
        )
        amap_b = assignments.select(
            "member_id", pl.col("cluster_id").alias("cid_b")
        )
        j = (
            pairs_df.join(amap_a, left_on="a", right_on="member_id", how="left")
            .join(amap_b, left_on="b", right_on="member_id", how="left")
            .filter(
                pl.col("cid_a").is_not_null()
                & pl.col("cid_b").is_not_null()
                & (pl.col("cid_a") == pl.col("cid_b"))
            )
            .with_columns(pl.col("cid_a").alias("cid"))
        )
        g = (
            j.group_by("cid", "a", "b")
            .agg(
                pl.col("__i__").min().alias("first_i"),
                # LAST-WINS: score at the last occurrence by input order.
                # NEVER pl.col("s").max().
                pl.col("s").sort_by("__i__").last().alias("last_s"),
            )
            # REQUIRED: group_by output order is undefined; first-occurrence
            # insertion order is part of the byte-identical contract.
            .sort("cid", "first_i")
        )
        # Stage-2 (engine-plannable): collapse the per-pair rows into ONE frame,
        # one row per cid, edges as list-columns. ``maintain_order=True`` preserves
        # the prior ``sort("cid", "first_i")`` row order, so within each cid the
        # a/b/last_s lists are in first-occurrence order -- byte-identical to
        # Stage-1 / from_pairs. The BUILD cost is just this group_by + a small
        # cid->row index; the per-cid dict is built lazily in the accessors. The
        # global dict-of-dicts is NEVER resident and NO per-cid frame partition is
        # materialized.
        agg = g.group_by("cid", maintain_order=True).agg(
            pl.col("a"), pl.col("b"), pl.col("last_s")
        )
        # num_clusters ints (cheap). Do NOT .to_list() the edge list-columns here;
        # the accessors slice them on demand.
        cid_to_row = dict(zip(agg["cid"].to_list(), range(agg.height)))
        return cls(agg_frame=agg, cid_to_row=cid_to_row)

    def _row_edges(self, row: int) -> tuple[list, list, list]:
        # Slice ONE row of the agg frame and pull its three edge list-columns.
        # The lists are in first-occurrence order (maintain_order=True at build).
        r = self._agg_frame.row(row, named=True)
        return r["a"], r["b"], r["last_s"]

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        if self._edges is not None:
            sl = self._edge_slice(cid)
            if sl is None:
                return {}
            lo, hi = sl
            av, bv, sv = self._edges[3], self._edges[4], self._edges[5]
            # rows are in first-occurrence order -> dict insertion order matches
            return dict(zip(zip(av[lo:hi].tolist(), bv[lo:hi].tolist()), sv[lo:hi].tolist()))
        if self._agg_frame is not None:
            row = self._cid_to_row.get(int(cid))
            if row is None:
                return {}
            al, bl, sl = self._row_edges(row)
            # list order == first-occurrence order; last value within a key
            # already collapsed by the build (last-wins).
            return {(int(a), int(b)): float(s) for a, b, s in zip(al, bl, sl)}
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        if self._edges is not None:
            uniq, starts, ends, av, bv, sv = self._edges
            for k in range(uniq.size):
                lo, hi = int(starts[k]), int(ends[k])
                yield int(uniq[k]), list(zip(
                    av[lo:hi].tolist(), bv[lo:hi].tolist(), sv[lo:hi].tolist()
                ))
            return
        if self._agg_frame is not None:
            for cid, al, bl, sl in self._agg_frame.select(
                "cid", "a", "b", "last_s"
            ).iter_rows():
                yield int(cid), [
                    (int(a), int(b), float(s)) for a, b, s in zip(al, bl, sl)
                ]
        else:
            for cid, ps in self._by_cid.items():
                yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        key = (min(a, b), max(a, b))
        if self._edges is not None or self._agg_frame is not None:
            # Canonical QUERY key vs AS-GIVEN stored keys: a pair stored (7,3)
            # is intentionally MISSED by score_for(cid, 3, 7) -> None. Preserved.
            return self.for_cluster(cid).get(key)
        return self._by_cid.get(cid, {}).get(key)


# ---------------------------------------------------------------------------
# The one seam that builds identity's pair-score view
# ---------------------------------------------------------------------------
def _datafusion_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("datafusion") is not None


def _pair_score_view_backend() -> str:
    """``GOLDENMATCH_PAIR_SCORE_VIEW``: ``auto`` (default) = the Arrow-native
    DataFusion view when ``datafusion`` is installed, else the Python view;
    ``datafusion`` = require it (raise if not installed rather than quietly
    taking the path that OOMs at scale); ``python`` = the legacy view
    (kill-switch). Anything else reads as ``auto``."""
    raw = os.environ.get("GOLDENMATCH_PAIR_SCORE_VIEW", "auto").strip().lower()
    return raw if raw in ("auto", "datafusion", "python") else "auto"


def _as_pair_tuples(pairs: Any) -> Any:
    """The legacy builders take an iterable of ``(a, b, score)``. An Arrow (or
    Polars) pair table goes through the same ``to_pydict`` + ``zip`` the pipeline
    always used, so the Python fallback is unchanged."""
    if hasattr(pairs, "to_arrow") and not hasattr(pairs, "column_names"):
        pairs = pairs.to_arrow()
    if hasattr(pairs, "column_names"):
        names = pairs.column_names
        a, b = ("id_a", "id_b") if "id_a" in names else ("a", "b")
        d = pairs.select([a, b, "score"]).to_pydict()
        return list(zip(d[a], d[b], d["score"]))
    return pairs


def build_pair_score_view(assignments: Any, pairs: Any) -> ClusterPairScores:
    """Identity's per-cluster pair-score view from the FINAL ``assignments``
    frame and the RAW scored pairs (a ``(a, b, score)`` iterable, or an Arrow /
    Polars pair table with ``id_a``/``id_b``/``score`` or ``a``/``b``/``score``).

    The DataFusion builder (scale-substrate SP1, #695, wired here) takes an
    Arrow pair table as-is -- no Python object per pair -- and is byte-identical
    to ``from_frames``: same kept edges, last-wins scores, first-occurrence
    order. The Python builder round-trips every pair through Python, which at
    ~30M pairs is a multi-GB transient (the 14M identity-resolve OOM)."""
    backend = _pair_score_view_backend()
    if backend == "datafusion" and not _datafusion_available():
        raise ImportError(
            "GOLDENMATCH_PAIR_SCORE_VIEW=datafusion but datafusion is not "
            "installed: pip install 'goldenmatch[datafusion]'"
        )
    if backend == "datafusion" or (backend == "auto" and _datafusion_available()):
        from goldenmatch.core import cluster_edges_df as _ce

        return _ce.cluster_pair_scores_datafusion(pairs, assignments)
    return ClusterPairScores.from_frames(assignments, _as_pair_tuples(pairs))
