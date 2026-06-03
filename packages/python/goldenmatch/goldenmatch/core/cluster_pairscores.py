"""Lazy per-cluster pair-score view (Phase 2 SP2). Decouples the identity
evidence-edge consumer from the legacy per-cluster ``pair_scores`` dict. Sourced
from the FINAL (post-split) cluster partition so it is byte-identical to the dict
path. dict-of-dicts backing; the ``iter_clusters`` interface is frame-ready for a
future SP that makes the build produce a columnar pair frame natively."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any


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
    __slots__ = ("_by_cid",)

    def __init__(self, by_cid: dict[int, dict[tuple[int, int], float]]):
        self._by_cid = by_cid

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
        """
        member_to_cid: dict[int, int] = {}
        for cid, mid in zip(
            assignments["cluster_id"].to_list(),
            assignments["member_id"].to_list(),
        ):
            member_to_cid[int(mid)] = int(cid)
        return cls(_bucket_pairs(all_pairs, member_to_cid))

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        for cid, ps in self._by_cid.items():
            yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        return self._by_cid.get(cid, {}).get((min(a, b), max(a, b)))
