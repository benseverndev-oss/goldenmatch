"""Lazy per-cluster pair-score view (Phase 2 SP2). Decouples the identity
evidence-edge consumer from the legacy per-cluster ``pair_scores`` dict. Sourced
from the FINAL (post-split) cluster partition so it is byte-identical to the dict
path. dict-of-dicts backing; the ``iter_clusters`` interface is frame-ready for a
future SP that makes the build produce a columnar pair frame natively."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import polars as pl


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
    __slots__ = ("_by_cid", "_partitions")

    def __init__(
        self,
        by_cid: dict[int, dict[tuple[int, int], float]] | None = None,
        partitions: dict[int, Any] | None = None,
    ):
        # Two backings, mutually exclusive in practice:
        #  - _by_cid: legacy resident dict-of-dicts (from_pairs/from_cluster_dict)
        #  - _partitions: Stage-2 frame-backed, one small Polars frame per cid;
        #    accessors build the per-cid dict on demand so the global
        #    100M-entry dict-of-dicts is NEVER resident.
        # __slots__ raises on unset access, so set BOTH every time.
        self._by_cid = by_cid
        self._partitions = partitions

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
        Python loop (10x faster at 100M pairs). Builds the SAME ``_by_cid`` dict:
        a pair is kept iff BOTH endpoints map to the same cid; key is ``(a, b)``
        EXACTLY as given (NEVER canonicalized); key insertion order = first
        occurrence; value = score at the LAST occurrence (LAST-WINS, NOT max);
        self-pairs ``(a, a)`` are kept.
        """
        # all_pairs is the raw list[(a,b,s)] AS-GIVEN. NEVER canonicalize.
        a_col, b_col, s_col = [], [], []
        for a, b, s in all_pairs:
            a_col.append(a)
            b_col.append(b)
            s_col.append(s)
        pairs_df = pl.DataFrame(
            {"a": a_col, "b": b_col, "s": s_col}
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
                pl.col("s").sort_by("__i__").last().alias("last_score"),
            )
            # REQUIRED: group_by output order is undefined; first-occurrence
            # insertion order is part of the byte-identical contract.
            .sort("cid", "first_i")
        )
        # Stage-2: STOP materializing the global dict-of-dicts. Keep one small
        # Polars frame per cid; the accessors build the per-cid dict on demand.
        # ``g`` is already deduped (group_by) + ordered (sort cid, first_i), so
        # dropping first_i and partitioning by cid preserves first-occurrence
        # row order and last-wins scores -- byte-identical to Stage-1 / from_pairs.
        view_df = g.select("cid", "a", "b", pl.col("last_score").alias("s"))
        raw = view_df.partition_by("cid", as_dict=True)
        # Normalize key shape across Polars versions (current=tuple (cid,),
        # older=scalar cid) so lookups always use a scalar int cid.
        partitions = {
            int(k[0] if isinstance(k, tuple) else k): v for k, v in raw.items()
        }
        return cls(partitions=partitions)

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        if self._partitions is not None:
            f = self._partitions.get(int(cid))
            if f is None:
                return {}
            d: dict[tuple[int, int], float] = {}
            for a, b, s in f.select("a", "b", "s").iter_rows():
                d[(int(a), int(b))] = float(s)
            return d
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        if self._partitions is not None:
            for cid, f in self._partitions.items():
                yield int(cid), [
                    (int(a), int(b), float(s))
                    for a, b, s in f.select("a", "b", "s").iter_rows()
                ]
        else:
            for cid, ps in self._by_cid.items():
                yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        key = (min(a, b), max(a, b))
        if self._partitions is not None:
            # Canonical QUERY key vs AS-GIVEN stored keys: a pair stored (7,3)
            # is intentionally MISSED by score_for(cid, 3, 7) -> None. Preserved.
            return self.for_cluster(cid).get(key)
        return self._by_cid.get(cid, {}).get(key)
